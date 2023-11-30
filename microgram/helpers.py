def kk(dictionary: dict, space_sep_keywords, default=None):
    x = dict(dictionary)
    for kwd in space_sep_keywords.split():
        if not isinstance(x, dict):
            return default
        x = x.get(kwd)
        if x is None:
            return default
    return x


assert kk({'update': {'message': {'text': '123'}}}, 'update message text') == '123'
assert kk({'update': {'message': {'text': '123'}}}, 'update message from id') is None
assert kk({'update': {'message': {'text': '123'}}}, 'update message from id', 'UNKNOWN') == 'UNKNOWN'
assert kk({'update': ''}, 'update message', 'UNKNOWN') == 'UNKNOWN'
