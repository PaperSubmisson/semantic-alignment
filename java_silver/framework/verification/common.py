from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import time

from ..settings import settings


def digest(value):
    return hashlib.sha256(value if isinstance(value,bytes) else value.encode()).hexdigest()

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write_json(path, obj):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_name(p.name+'.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(p)

def run_process(command, directory, prefix, timeout=90):
    prefix=Path(prefix); prefix.parent.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy()
    env['PATH']=str(Path(settings.java).parent)+os.pathsep+env.get('PATH','')
    start=time.monotonic()
    proc=subprocess.Popen([str(v) for v in command],cwd=directory,env=env,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',start_new_session=True)
    timed_out=False
    try: stdout,stderr=proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out=True; os.killpg(proc.pid,signal.SIGKILL); stdout,stderr=proc.communicate()
    prefix.with_suffix('.stdout.log').write_text(stdout,encoding='utf-8')
    prefix.with_suffix('.stderr.log').write_text(stderr,encoding='utf-8')
    result={'command':[str(v) for v in command],'cwd':str(directory),'returncode':proc.returncode,
            'timed_out':timed_out,'seconds':round(time.monotonic()-start,3),
            'stdout_file':str(prefix.with_suffix('.stdout.log')),'stderr_file':str(prefix.with_suffix('.stderr.log'))}
    write_json(prefix.with_suffix('.process.json'),result)
    return result,stdout,stderr
