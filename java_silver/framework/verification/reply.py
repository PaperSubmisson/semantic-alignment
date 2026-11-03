import json
import re

def parse_reply(content):
    s=content.strip()
    if s.startswith('```'):
        s=re.sub(r'^```(?:json)?\s*','',s);s=re.sub(r'\s*```$','',s)
    value=json.loads(s)
    if not isinstance(value,dict):raise ValueError('Response must be a JSON object')
    return value
