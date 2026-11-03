from pathlib import Path
import re

def normalize(process,stdout,stderr,candidate,source_map=None):
    output=stdout+'\n'+stderr
    status='tool_error'
    if process['timed_out']: status='verification_timeout'
    elif re.search(r'timeout|timed out',output,re.I): status='verification_timeout'
    elif process['returncode']==0 and 'silicon finished verification successfully' in output: status='verified'
    elif re.search(r'silicon found [1-9][0-9]* errors? in',output): status='verification_failed'
    elif re.search(r'parse error|type error|typechecking|type checking|Parsing failed|Expected ',output,re.I): status='parse_type_error'
    diagnostics=[]
    for block in re.split(r'(?m)^\s*\[\d+\]\s+',stdout)[1:]:
        lines=block.splitlines(); message=lines[0]
        lower=message.lower()
        kind='other'
        for needle,label in [('might not hold on entry','loop_entry'),('might not be preserved','loop_preservation'),
                              ('insufficient permission','permission'),('termination','termination'),('decreases','termination'),
                              ('precondition','call_precondition'),('postcondition','postcondition'),('assert','assertion')]:
            if needle in lower:kind=label;break
        loc=re.search(r'\(([^()]+\.vpr)@(\d+)\.(\d+)(?:--(\d+)\.(\d+))?\)',message)
        variables={}
        for line in lines[1:]:
            if ' -> ' in line:
                key,value=line.strip().split(' -> ',1);variables[key]=value
        d={'kind':kind,'native_id':None,'message':message,'java_location':None,
           'model':{'kind':'proof_obligation','availability':'available' if variables else 'unavailable','variables':variables}}
        if loc:
            n=int(loc[2]);d['location']={'file':loc[1],'line':n,'column':int(loc[3]),'end_line':int(loc[4] or loc[2]),'end_column':int(loc[5] or loc[3])}
            code=Path(candidate).read_text().splitlines()
            d['source_context']=[{'line':i+1,'text':code[i]} for i in range(max(0,n-3),min(len(code),n+2))]
            if source_map:
                d['editing_context']=source_map.get(str(n))
        diagnostics.append(d)
    return {'source':'silicon','status':status,'returncode':process['returncode'],'diagnostics':diagnostics,
            'model_caveat':'Proof-obligation models are not necessarily reachable Java executions.',
            'stdout_file':process['stdout_file'],'stderr_file':process['stderr_file'],
            'raw_excerpt':output[-16000:] if not diagnostics else None}
