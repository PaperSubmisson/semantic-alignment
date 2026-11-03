"""Compact prompts for the lightweight Ref + sequence encoding.

The existing Silver candidate JSON implementation is encoding-independent.
This module supplies its actual AST IDs, never source-level insertion guesses.
"""
import json
from pathlib import Path

PROMPTS=Path(__file__).resolve().parents[1]/'prompts'

def load(name):return (PROMPTS/name).read_text(encoding='utf-8').strip()

def context(folder):
    folder=Path(folder)
    task=json.loads((folder/'task.json').read_text(encoding='utf-8'))
    # Re-index cached tasks so new anchors are visible without rewriting past runs.
    from framework.verification.bridge import index
    slots=index((folder/'baseline.vpr').read_text(encoding='utf-8'),
                json.loads((folder/'baseline.ast.json').read_text(encoding='utf-8')))
    return {
        'numeric_semantics':{'Int':'mathematical_integer','Perm':'exact_real'},
        'termination_required':False,
        'fixed_roots':task['entrypoints'],
        'editable_methods':task['editable_methods'],
        'loops':{k:{'method':v['method'],'line':v['line']} for k,v in slots['loops'].items()},
        'anchors':{k:dict({'method':v['method'],'line':v['line'],'kind':v['kind']},
                        **({key:v[key] for key in ('label','positions')} if v['kind']=='Label' else {}))
                   for k,v in slots['anchors'].items()},
    }

def initial(folder):
    folder=Path(folder)
    ctx=context(folder)
    return [{'role':'system','content':load('system.md')},
            {'role':'user','content':load('encoding.md')+'\n\n'+load('output.md')+
             '\n\nTASK AND EDITABLE IDS\n'+json.dumps(ctx,ensure_ascii=False,separators=(',',':'))+
             '\n\nFIXED SILVER\n'+(folder/'baseline.vpr').read_text(encoding='utf-8')}]

def repair(initial_messages,candidate,feedback,history):
    messages=[dict(m) for m in initial_messages]
    messages[-1]['content']+='\n\n'+load('repair.md')+'\nCURRENT COMPLETE PROOF\n'+json.dumps(candidate,ensure_ascii=False)
    messages[-1]['content']+='\nSILICON LOGICAL FEEDBACK\n'+json.dumps(feedback,ensure_ascii=False)
    messages[-1]['content']+='\nRECENT LOGICAL FAILURES\n'+json.dumps(history[-3:],ensure_ascii=False)
    return messages

def retry_messages(current,candidate,feedback,history,initial_messages):
    """Only logical proof failures alter the model context, by user choice."""
    if feedback.get('status')=='verification_failed' and feedback.get('diagnostics'):
        return repair(initial_messages,candidate,feedback,history)
    return current
