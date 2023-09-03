# import sys
# !{sys.executable} -m pip install lark-parser

from lark import Lark, Transformer
from typing import TypedDict, Union, Generator


MAX_MESSAGE_LEN = 4096

telegram_html_grammar = r"""
content: (tag | plain_text)*
?plain_text: STRING
tag: "<" TAG_NAME attributes? ">" content "</" TAG_NAME ">"

attributes: (WS attribute)+ -> list
attribute: VARIABLE "=" quoted_string

TAG_NAME: "b" | "strong" | "i" | "em" | "u" | "ins" | "s" | "strike" | "del" | "span" | "tg-spoiler" | "a" | "code" | "pre"

?quoted_string : DOUBLE_QUOTED_STRING | SINGLE_QUOTED_STRING

STRING: /[^<>]+/
VARIABLE: /[a-zA-Z0-19_]+/
DOUBLE_QUOTED_STRING  : /"[^"]*"/
SINGLE_QUOTED_STRING  : /'[^']*'/

%import common.WS
"""


class Tag(TypedDict):
    tag: str
    content: list[Union[str, "Tag"]]
    attrs: list[str]


class TelegramHtmlParser(Transformer):
    def tag(self, args) -> Tag:
        # print(f"tag(): {args=}")
        if len(args) == 3:
            open_tag, content, close_tag = args
            attrs = []
        else:
            open_tag, attrs_raw, content, close_tag = args
            attrs = attrs_raw[1::2]
        assert open_tag == close_tag
        return Tag(
            tag=open_tag.value,
            content=content,
            attrs=attrs)

    def STRING(self, arg):
        return str(arg)

    def content(self, args):
        # print(f"{args=}")
        return list(args)

    def attribute(self, args):
        # print(f"attribute: {args=}")
        k, v = args
        return f"{k.value}={v.value}"

    list = list


html_parser = Lark(telegram_html_grammar, start='content', parser='lalr', transformer=TelegramHtmlParser())

_p = html_parser.parse
assert _p("") == []
assert _p("Welcome") == ["Welcome"]
assert _p(" spaces must live ") == [" spaces must live "]
assert _p("<b>bold</b>") == [{
    'tag': 'b',
    'content': ['bold'],
    'attrs': []
}]
assert _p('<span  class="tg-spoiler" id="123">spoiler</span>') == [{
    'tag': 'span',
    'content': ['spoiler'],
    'attrs': ['class="tg-spoiler"', 'id="123"']
}]
assert _p('<b>Say:</b> Hi <a href="https://asdasd" visible="true">there <i>!</i></a>') == \
    [{
        'tag': 'b', 'content': ['Say:'], 'attrs': []},
        ' Hi ',
        {'tag': 'a',
         'content':
                 [
                     'there ', {
                         'tag': 'i',
                         'content': ['!'],
                         'attrs': []
                     }
                 ],
         'attrs': ['href="https://asdasd"', 'visible="true"']
     }]


def token_open_len(t: Tag) -> int:
    attrs_len = sum(len(a) + 1 for a in t['attrs'])
    return 1 + len(t['tag']) + attrs_len + 1


def token_close_len(t: Tag) -> int:
    return 2 + len(t['tag']) + 1


_s = '<a href="tg://user?id=123456789">attribe test</a>'
_p = html_parser.parse(_s)[0]
assert token_open_len(_p) + token_close_len(_p) + len(''.join(_p['content'])) == len(_s)


def token_len(tokens: list) -> int:
    ln = 0
    for t in tokens:
        if isinstance(t, str):
            ln += len(t)
        elif isinstance(t, dict):
            ln += token_open_len(t) + token_len(t['content']) + token_close_len(t)
        else:
            raise RuntimeError(f'Unknown token {t}')
    return ln


def token_open_str(t: Tag) -> str:
    attrs = ''.join(f" {a}" for a in t['attrs'])
    return f"<{t['tag']}{attrs}>"


def token_close_str(t: Tag) -> str:
    return f"</{t['tag']}>"


def token_str(tokens: str | Tag | list[Union[Tag, str]]) -> str:
    if isinstance(tokens, str):
        return tokens
    elif not isinstance(tokens, dict):
        return ''.join(token_str(t) for t in tokens)
    t = tokens
    contents = ''.join(token_str(x) for x in t['content'])
    return token_open_str(t) + contents + token_close_str(t)

for case in ['123',
             '<b>bold</b> there',
             '<a href="tg://user?id=123456789">attribe test</a>',
             '<b>1 <i> nested </i> 3 </b>']:
    p = html_parser.parse(case)
    assert token_len(p) == len(case)
    assert token_str(p) == case


def chunk_html(max_length: int, tokens: list, inside_tags: list = None):
    if not tokens:
        return []
    if inside_tags is None:
        inside_tags = ()
    prefix = ''.join(token_open_str(t) for t in inside_tags)
    postfix = ''.join(token_close_str(t) for t in inside_tags)

    def _str(tokens):
        return prefix + token_str(tokens) + postfix

    outer_tags_len = len(prefix) + len(postfix)
    if token_len(tokens) + outer_tags_len <= max_length:
        yield _str(tokens)
        return
    best_newline_dbl_idx = None
    best_space_dbl_idx = None
    best_anywhere_dbl_idx = None
    ln = outer_tags_len
    for ti, t in enumerate(tokens):
        if ln > max_length:
            break
        if not isinstance(t, str):
            ln += token_len([t])
        else: 
            for ci, c in enumerate(t):
                if c == '\n' and ln + ci + 1 <= max_length:
                    best_newline_dbl_idx = ti, ci
                if c == ' ' and ln + ci + 1 <= max_length:
                    best_space_dbl_idx = ti, ci
                if ln + ci + 1 <= max_length:
                    best_anywhere_dbl_idx = ti, ci
            ln += len(t)
    ti, ci = best_newline_dbl_idx \
             or best_space_dbl_idx \
             or best_anywhere_dbl_idx \
             or (None, None)
    if ti is not None:
        left = [t for i, t in enumerate(tokens) if i < ti]
        left.append(tokens[ti][:ci+1])
        if r := tokens[ti][ci+1:]:
            right = [r]
        else:
            right = []
        right += [t for i, t in enumerate(tokens) if i > ti]
        yield _str(left)
        yield from chunk_html(max_length, right, inside_tags)
    else:
        if isinstance(tokens[0], str):
            raise RuntimeError(f'Chunking {tokens} with {max_length=} is not possible')
        inside = inside_tags + (tokens[0],)
        yield from chunk_html(max_length, tokens[0]['content'], inside)
        yield from chunk_html(max_length, tokens[1:], inside_tags)
   

def chunk_by_newlines(text, max_length: int) -> Generator[str, None, None]:
    paragraphs = text.split('\n')
    acc = ''
    for p in paragraphs:
        if len(p) > max_length:
            raise Exception(f'Invalid page: too long line in message {text}')

        new_acc = (acc + '\n' + p).lstrip()
        if len(new_acc) > max_length:
            yield acc
            acc = p
        else:
            acc = new_acc
    yield acc


def chunk(max_length=MAX_MESSAGE_LEN, **kwargs):
    """First try to chunk unformatted text in newlines
    then try to chunk unformatted text in whitespaces
    then try to chunk unformatted text anywhere
    then try to chunk first formatted text, repeating steps 1, 2, 3"""
    text = kwargs['text']
    if len(text) <= max_length:
        return [text]
    parse_mode = kwargs.get('parse_mode', '').lower()
    if not parse_mode:
        return chunk_by_newlines(text, max_length=max_length)
    if parse_mode == 'html':
        toks = html_parser.parse(text)
        return chunk_html(max_length=max_length, tokens=toks)
    elif parse_mode in {"MarkdownV2", "Markdown"}:
        raise NotImplementedError("Can't chunk anything but parse_mode=HTML")
    else:
        raise RuntimeError(f"Unknown `{parse_mode=}`")


assert list(chunk(**{'text': "line1\n\nline2"},
                  max_length=1000)) == ['line1\n\nline2']
assert list(chunk(**{'text': "line1\n\nline2"},
                  max_length=5)) == ['line1', 'line2']

assert list(chunk(**{'text': 'some\ntext\n!',
                     'parse_mode': 'HTML'},
                  max_length=5)) == ['some\n', 'text\n', '!']
assert list(chunk(**{'text': 'some\ntext !!!',
                     'parse_mode': 'HTML'},
                  max_length=5)) == ['some\n', 'text ', '!!!']
assert list(chunk(**{'text': 's o m\nt e x t !!!',
                     'parse_mode': 'HTML'},
                  max_length=6)) == ['s o m\n', 't e x ', 't !!!']
assert list(chunk(**{'text': 'Verylongtextwithoutnewlinesandspaces',
                     'parse_mode': 'HTML'},
                  max_length=5)) == ['Veryl', 'ongte', 'xtwit', 'houtn', 'ewlin', 'esand', 'space', 's']
assert list(chunk(**{'text': 'hi<b>there</b>world',
                     'parse_mode': 'HTML'},
                  max_length=10)) == ['hi', '<b>the</b>', '<b>re</b>', 'world']
assert list(chunk(**{'text': '<pre><code class="language-python">import sys\nprint(sys.executable)\nprint(sys.argv)</code></pre>',
                     'parse_mode': 'HTML'},
            max_length=80)) == ['<pre><code class="language-python">import sys\n</pre></code>',
                                '<pre><code class="language-python">print(sys.executable)\n</pre></code>',
                                '<pre><code class="language-python">print(sys.argv)</pre></code>']
