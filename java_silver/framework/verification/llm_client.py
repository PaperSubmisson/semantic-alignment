"""No implicit credentials: live runs require a user-supplied config/key file."""
from pathlib import Path
import json
import os
import urllib.request
import urllib.error

class ReplayClient:
    def __init__(self,responses):self.responses=list(responses);self.calls=0
    def query(self,messages):
        if self.calls>=len(self.responses):raise RuntimeError('Replay fixture exhausted')
        response=self.responses[self.calls];self.calls+=1
        if isinstance(response,Exception):raise response
        return {'content':response if isinstance(response,str) else json.dumps(response),
                'usage':None,'provider':'scripted_test_fixture','model':'none'}

class LiveClient:
    def __init__(self,config):
        self.config=config
        # Read the explicitly configured key file or environment variable.
        if config.get('api_key_file'):self.key=Path(config['api_key_file']).read_text().strip()
        elif config.get('api_key_env'):self.key=os.environ.get(config['api_key_env'],'')
        else:raise ValueError('Provide api_key_file or api_key_env; no implicit credentials are used')
        if not self.key:raise ValueError('Configured key is empty')
        if not config.get('model') or not config.get('base_url','').startswith('https://'):raise ValueError('Explicit HTTPS base_url and model are required')
    def query(self,messages):
        if sum(len(m['content']) for m in messages)>self.config.get('max_prompt_chars',300000):
            raise RuntimeError('Prompt exceeds configured size limit; no source/target was truncated')
        body={'model':self.config['model'],'messages':messages,'temperature':self.config.get('temperature',0.4),
              'max_tokens':self.config.get('max_tokens',8192)}
        req=urllib.request.Request(self.config['base_url'].rstrip('/')+'/chat/completions',
            data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+self.key})
        try:
            with urllib.request.urlopen(req,timeout=self.config.get('request_timeout',120)) as r:response=json.load(r)
        except urllib.error.HTTPError as e:raise RuntimeError('API HTTP status '+str(e.code)) from None
        except (TimeoutError,urllib.error.URLError):raise RuntimeError('API network/timeout failure') from None
        try:content=response['choices'][0]['message']['content']
        except (KeyError,IndexError,TypeError):raise RuntimeError('API returned no completion content') from None
        if not isinstance(content,str):raise RuntimeError('API returned non-text completion')
        return {'content':content,'usage':response.get('usage'),'provider':'live','model':self.config['model'],'response_id':response.get('id')}
