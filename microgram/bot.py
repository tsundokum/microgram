#!/usr/bin/env python
# coding: utf-8

import asyncio
import bisect
import sys
from typing import AsyncGenerator, Generator, Optional, List, Set, Tuple
from contextlib import asynccontextmanager
from time import monotonic, time
from pathlib import Path
from datetime import datetime
import logging

import httpx
from pythonjsonlogger import jsonlogger

from .chunking import chunk


POLL_TIMEOUT = 5
POLL_WAIT_SEC = 1.5
POLL_MAX_ERRORS = 10

ENTITY_BOT_COMMAND = 'bot_command'
ENTITY_URL = 'url'
ENTITY_TEXT_LINK = 'text_link'
MESSAGE_LIMIT = 4096

DEFAULT_WORKERS_COUNT = 10

class JsonFormatterWithTime(jsonlogger.JsonFormatter):
    def __init__(self) -> None:
        super().__init__('%(timestamp)s %(level)s %(name)s %(message)s', json_ensure_ascii=False)

    def add_fields(self, log_record, record, message_dict):
        super(JsonFormatterWithTime, self).add_fields(log_record, record, message_dict)
        if log_record.get('timestamp') == None:
            now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            log_record['timestamp'] = now
        if log_record.get('level'):
            log_record['level'] = log_record['level'].upper()
        else:
            log_record['level'] = record.levelname


def get_json_logger(log_path, name='json_loader'):
    """Returns a logger that logs JSON objects to a file."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(JsonFormatterWithTime())
    logger.addHandler(file_handler)
    return logger


def parse_entities(message: dict, type: str) -> Set[str]:
    text = message.get('text', '')
    return {text[e['offset']:(e['offset']+e['length'])] for e in message.get('entities', []) if e.get('type') == type}


_m = {'message_id': 370, 'from': {'id': 87799679, 'is_bot': False, 'first_name': 'Marat', 'username': 'tsundokum', 'language_code': 'en'}, 'chat': {'id': 87799679, 'first_name': 'Marat', 'username': 'tsundokum', 'type': 'private'}, 'date': 1678556500, 'text': '/balance www.leningrad.ru', 'entities': [{'offset': 0, 'length': 8, 'type': 'bot_command'}, {'offset': 9, 'length': 16, 'type': 'url'}]}
assert parse_entities(_m, ENTITY_BOT_COMMAND) == {'/balance'}
assert parse_entities(_m, ENTITY_URL) == {'www.leningrad.ru'}


def func_reply_chaining(main_reply_id: Optional[int], prev_reply_id: Optional[int]) -> Optional[int]:
    """First message is reply to given message,
    then second message is reply to the first and so on."""
    if main_reply_id and not prev_reply_id:
        return main_reply_id
    if prev_reply_id:
        return prev_reply_id


def func_reply_none(main_reply_id: Optional[int], prev_reply_id: Optional[int]) -> Optional[int]:
    return None


def func_reply_to_main(main_reply_id: Optional[int], prev_reply_id: Optional[int]) -> Optional[int]:
    return main_reply_id


def func_reply_only_first(main_reply_id: Optional[int], prev_reply_id: Optional[int]) -> Optional[int]:
    if main_reply_id and not prev_reply_id:
        return main_reply_id
    

async def ON_ERROR_PRINT_STDERR(update, error):
    print(f'{error} while processing {update}', file=sys.stderr)


class Bot:
    def __init__(self, token, logging_directory):
        self.polling_offset = 0
        self.token = token
        self.updates_logger = get_json_logger(Path(logging_directory) / 'updates.jl', 'updates')
        self.posts_logger = get_json_logger(Path(logging_directory) / 'posts.jl', 'posts')
        self._handlers = []
        self._scheduled_tasks: List[Tuple[int|datetime, callable]] = []
        self._timer = None
        self._status_active_workers_count = -1
        self._status_supervisor_last_check = -1

    async def post(self, method: str, fail_on_error=False, **kwargs):
        url = f'https://api.telegram.org/bot{self.token}/{method}'
        async with httpx.AsyncClient() as client:
            started = monotonic()
            if files := kwargs.get('files'):
                data = {k: v for k, v in kwargs.items() if k != 'files'}
                response = await client.post(url, data=data, files=files)
            else:
                response = await client.post(url, json=kwargs)
            try:
                j = response.json()
            except:
                j = {'text': response.text}
            rt = monotonic() - started
            self.posts_logger.info({'method': method, 'status': response.status_code,
                                    'request': kwargs, 'response': j,
                                    'response_time': rt})
            if fail_on_error and response.status_code != 200:
                raise Exception(f'Failed to call `{method}`: {response.status_code}, {response.text}')
            return j


    async def delete_message(self, fail_on_error=False, **kwargs) -> dict:
        return await self.post('deleteMessage', fail_on_error=fail_on_error, **kwargs)

    async def edit_message_text(self, fail_on_error=False, **kwargs) -> dict:
        return await self.post('editMessageText', fail_on_error=fail_on_error, **kwargs)

    async def send_message_typing(self, typing_delay_sec=2, **kwargs):
        async with self.chat_action(chat_id=kwargs['chat_id']):
            await asyncio.sleep(typing_delay_sec)
            await self.send_message(**kwargs)

    async def send_message(self, fail_on_error=True, message_limit=MESSAGE_LIMIT,
                           func_reply_to_message_id=func_reply_chaining, 
                           **kwargs) -> Optional[dict]:
        text = kwargs.get('text')
        if not text:
            return

        async def send_func(**override_kwargs) -> dict:
            params = dict(kwargs)
            params.update(override_kwargs)
            return await self.post('sendMessage', fail_on_error=fail_on_error, **params)

        prev_reply_id = None
        main_reply_id = kwargs.get('reply_to_message_id')
        for c in chunk(max_length=message_limit, **kwargs):
            rid = func_reply_to_message_id(main_reply_id, prev_reply_id)
            r = await send_func(text=c, reply_to_message_id=rid) 
            prev_reply_id = r.get('result', {}).get('message_id')

        return r

    async def poll(self, poll_timeout=POLL_TIMEOUT, poll_wait_sec=POLL_WAIT_SEC, max_errors=POLL_MAX_ERRORS) -> AsyncGenerator:
        async with httpx.AsyncClient() as client:
            errors_count = 0
            while True:
                if errors_count > max_errors:
                    raise RuntimeError(f'Reached {errors_count} errors')
                url = f'https://api.telegram.org/bot{self.token}/getUpdates?limit=1&offset={self.polling_offset}'
                try:
                    response = await client.get(url, timeout=poll_timeout)
                except (httpx.ConnectError, httpx.TimeoutException) as ex:
                    errors_count += 1
                    await asyncio.sleep(poll_wait_sec)
                    continue

                resp = response.json()
                if not resp.get('ok') and resp.get('error_code'):
                    if ra := resp.get('parameters', {}).get('retry_after'):
                        await asyncio.sleep(ra)
                # Compare with None because updates can be emptry list
                elif (updates := resp.get('result')) is not None:
                    errors_count = 0
                    for u in updates:
                        self.polling_offset = u['update_id'] + 1
                        self.updates_logger.info(u)
                        yield u
                    
                else:
                    print('NO RESULT', resp)
                    self.updates_logger.error('getUpdates', extra=resp)

                await asyncio.sleep(poll_wait_sec)

    @asynccontextmanager
    async def chat_action(self, chat_id, action='typing', text_while_waiting=None):
        message_id_while_waiting = None
        lock = asyncio.Event()

        if text_while_waiting:
            r = await self.send_message(chat_id=chat_id, text=text_while_waiting,
                                        disable_notification=True)
            message_id_while_waiting = r['result']['message_id']
        async def act(lock):
            while not lock.is_set():
                await self.post('sendChatAction', action=action, chat_id=chat_id)
                await asyncio.sleep(5)
        asyncio.create_task(act(lock))
        try:
            yield
        finally:
            lock.set()
            if message_id_while_waiting:
                await self.post("deleteMessage", chat_id=chat_id, message_id=message_id_while_waiting)
            
    def handler(self, func):
        self._handlers.append(func)
        return func

    async def _process_updates(self, queue, on_error):
        while True:
            update = await queue.get()
            try:
                for handle in self._handlers:
                    r = await handle(update)
                    if r:
                        break
            except KeyboardInterrupt:
                break
            except Exception as ex:
                await on_error(update, ex)
            finally:
                queue.task_done()

    async def _supervisor(self, tasks, timer):
        while True:
            count = 0
            for t in tasks:
                count += not t.cancelled()
            self._status_active_workers_count = count
            self._status_supervisor_last_check = monotonic()
            if timer.done():
                self._status_timer_running = timer
            else:
                self._status_timer_running = True
            await asyncio.sleep(1)

    async def _check_scheduled(self):
        while True:
            now = time()
            if self._scheduled_tasks:
                when, target_func = self._scheduled_tasks[0]
                if now > when:
                    self._scheduled_tasks.pop(0)
                    try:
                        await target_func()
                    except Exception as ex:
                        print(ex)
            await asyncio.sleep(1)
            
    def schedule(self, when_ts: float | datetime, target_func: callable):
        when_ts = when_ts if isinstance(when_ts, float) else when_ts.timestamp()
        bisect.insort(self._scheduled_tasks, (when_ts, target_func))

    async def arun(self, workers_count=DEFAULT_WORKERS_COUNT, on_error=ON_ERROR_PRINT_STDERR):
        q = asyncio.Queue()
        tasks = []
        for i in range(workers_count):
            tasks.append(asyncio.create_task(self._process_updates(q, on_error)))

        timer = asyncio.create_task(self._check_scheduled())
        sv = asyncio.create_task(self._supervisor(tasks, timer))
        async for update in self.poll():
            await q.put(update)

    def run(self, workers_count=DEFAULT_WORKERS_COUNT, on_error=ON_ERROR_PRINT_STDERR):
        asyncio.run(self.arun(workers_count, on_error))

