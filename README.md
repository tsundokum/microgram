# microgram
Asynchronious Python wrapper for Telegram Bot API. To use the library you will need to read https://core.telegram.org/bots/api
## Features
1. `send_message` splits plain or html formatted text into smaller messages if content is longer than allowed
2. Context manager for actions (like `typing...`)
## Principles
1. Don't create classes there dict can be used
2. Stick to the API names
3. Write custom function only when necessary. For example, `send_message` will break `text` into smaller chunks if it is longer than max messages length.
# Installation
```sh
pip install microgram
```
# Examples
## Echo bot
```python
from microgram import Bot

bot = Bot('Put your bot api key here', 'logging-dir')

@bot.handler
async def echo(update):                                              # https://core.telegram.org/bots/api#update
    if message := update.get('message'):
        await bot.send_message(chat_id=message['chat']['id'],
                               text=message['text'],
                               reply_to_message=message['id'])


if __name__ == "__main__":
    bot.run()
```

## Send Voice Message
```python
await bot.post('sendVoice', 
               chat_id=CHAT_ID, 
               caption='hello world', 
               files={'voice': open(ogg_file_path, 'rb')})
```
same with [sendDocument](https://core.telegram.org/bots/api#senddocument), [sendPhoto](https://core.telegram.org/bots/api#sendphoto), etc
