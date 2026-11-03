"""Limited lexical normalization of standalone non-delimiter Unicode char literals."""
import re
from framework.j2silver.model import TranslationError
def normalize(data,path=None):
    def char(m):
        value=int(m.group(1),16)
        if value in (10,13,39,92) or 0xd800<=value<=0xdfff:
            raise TranslationError('Unicode literal requires full preprocessing',path=path)
        return ("'"+chr(value)+"'").encode('utf8')
    # Do not treat quote-like text inside a string/comment as a char literal.
    tokens=rb'//[^\r\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|'+rb"'(?:\\.|[^'\\])*'"
    result=re.sub(tokens,lambda m:re.sub(rb"'\\u+([0-9a-fA-F]{4})'",char,m.group(0)) if m.group(0).startswith(b"'") else m.group(0),data)
    if re.search(rb'\\u+[0-9a-fA-F]{4}',result):
        raise TranslationError('Only standalone Unicode char literal escapes are normalized',path=path)
    return result
