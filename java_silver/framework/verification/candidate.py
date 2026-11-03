"""Structured proof construction plus checks against the real Silver AST."""
import re
from .bridge import walk,index

class Rejected(ValueError): pass

def check(condition,message):
    if not condition: raise Rejected(message)

def exact(obj,allowed,required=()):
    check(isinstance(obj,dict),'Expected an object')
    check(set(obj)<=set(allowed),f'Unknown fields: {set(obj)-set(allowed)}')
    check(set(required)<=set(obj),f'Missing fields: {set(required)-set(obj)}')

def ident(name):
    check(isinstance(name,str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',name),'Invalid identifier')
    return name

FORBIDDEN=set('axiom domain field method function predicate requires ensures invariant decreases var returns inhale exhale assume assert fold unfold goto label new import define package apply wand while if else return'.split())

def expr(value):
    check(isinstance(value,str) and 0<len(value)<=12000,'Invalid expression size/type')
    # := is also Silver's immutable sequence-update expression, s[i := v].
    # Actual program assignments remain excluded by parsing and AST auditing.
    check(not any(s in value for s in ('//','/*','*/',';','"',"'")),'Expression contains comments or statement delimiters')
    words=set(re.findall(r'[A-Za-z_][A-Za-z_0-9]*',value))
    check(not words & FORBIDDEN,'Statement/declaration keyword in expression: '+str(words & FORBIDDEN))
    check(not re.search(r'\b(?:acc|perm)\s*\([^)]*\b(?:wildcard|epsilon)\b',value),'Unsupported permission form')
    return value.strip()

def expressions(values):
    check(isinstance(values,list) and len(values)<=100,'Expected bounded list of expressions')
    return [expr(v) for v in values]

def measures(values):
    result=expressions(values)
    check(all(v not in ('*','_') for v in result),'Termination exemptions are forbidden')
    return result

def typ(value):
    check(isinstance(value,str) and re.fullmatch(r'[A-Za-z_0-9\[\], ]+',value),'Invalid type')
    check(not set(re.findall(r'\w+',value)) & FORBIDDEN,'Declaration injection in type')
    return value

def params(values):
    check(isinstance(values,list) and len(values)<=50,'Invalid parameter list')
    result=[]; names=set()
    for p in values:
        exact(p,('name','type'),('name','type'))
        name=ident(p['name']);check(name not in names,'Duplicate parameter');names.add(name)
        result.append(name+': '+typ(p['type']))
    return ', '.join(result)

def apply_edits(base,edits):
    edits=sorted(enumerate(edits),key=lambda p:(p[1]['offset'],p[0]))
    result=''; cursor=0; spans=[]; mapping={}
    for _,e in edits:
        at=e['offset'];check(cursor<=at<=len(base),'Invalid/overlapping insertion')
        result+=base[cursor:at];cursor=at
        start=result.count('\n')+2
        chunk='\n'+e['text'].strip('\n')+'\n'
        result+=chunk
        end=result.count('\n')
        spans.append({'start_line':start,'end_line':end,'context':e['context']})
        for line in range(start,end+1):mapping[str(line)]=e['context']
    result+=base[cursor:]
    return result,spans,mapping

def build(base,ast,task,candidate,*,method_policy=None,ordered_contracts=False):
    # The runner already binds this reply to a prepared task. Identity/hash metadata
    # belongs to the local run manifest, not to text the model has to reproduce.
    fields=('loops','methods','functions','predicates','ghosts','lemmas','insertions')
    exact(candidate,fields+('schema_version','task_id','base_sha256'),('loops',))
    candidate=dict(candidate)
    for key in fields:
        candidate.setdefault(key,{} if key in ('loops','methods','ghosts') else [])
    slots=index(base,ast);edits=[];helper_names=set();ghost_names=set();lemma_names=set()
    reserved=set(re.findall(r'\b[A-Za-z_][A-Za-z_0-9]*\b',base))
    for collection in ('functions','predicates','lemmas'):
        check(isinstance(candidate[collection],list) and len(candidate[collection])<=30,'Invalid helper collection')
        for h in candidate[collection]:
            name=ident(h.get('name'))
            check(name not in reserved|helper_names,'Helper name collision')
            if collection=='lemmas':check(name.startswith('Lemma_'),'Lemma name must start with Lemma_')
            helper_names.add(name)
            if collection=='lemmas':lemma_names.add(name)
    def add(offset,text,**context):edits.append({'offset':offset,'text':text,'context':context})
    check(isinstance(candidate['loops'],dict),'loops must be an object')
    check(set(candidate['loops'])==set(slots['loops']),'Return every editable loop exactly once')
    for lid,v in candidate['loops'].items():
        exact(v,('invariants',),('invariants',)); clauses=expressions(v['invariants'])
        s=slots['loops'][lid]
        if clauses:add(s['body_start'],'\n'.join('invariant '+p for p in clauses),loop_id=lid,method_id=s['method'],kind='invariant')
    check(isinstance(candidate['methods'],dict),'methods must be an object')
    for mid,v in candidate['methods'].items():
        check(mid in task.get('editable_methods',[]) and mid in slots['methods'],'Method contracts are frozen: '+mid)
        if method_policy is None:
            check(mid not in task.get('entrypoints',[]),'Root contracts are immutable even if accidentally listed as editable')
        else:
            method_policy(mid,v,task)
        check(slots['methods'][mid]['has_body'],'Cannot extend a trusted abstract method')
        exact(v,('requires_add','ensures_add'))
        if ordered_contracts:
            from .bridge import offset
            method = next(m for m in ast['methods'] if m['name'] == mid)
            requires = ['requires '+p for p in expressions(v.get('requires_add',[]))]
            ensures = ['ensures '+p for p in expressions(v.get('ensures_add',[]))]
            at = slots['methods'][mid]['body_start']
            if method.get('posts'):
                at = base.rfind('ensures', 0, offset(base,method['posts'][0]['position']))
                check(at >= 0, 'Cannot locate existing postcondition')
            if requires:add(at,'\n'.join(requires),method_id=mid,kind='method_contract')
            if ensures:add(slots['methods'][mid]['body_start'],'\n'.join(ensures),method_id=mid,kind='method_contract')
        else:
            text=[]
            for key,keyword in [('requires_add','requires'),('ensures_add','ensures')]:
                text += [keyword+' '+p for p in expressions(v.get(key,[]))]
            if text:add(slots['methods'][mid]['body_start'],'\n'.join(text),method_id=mid,kind='method_contract')
    check(isinstance(candidate['ghosts'],dict),'ghosts must be an object')
    method_ghosts={}
    for mid,decls in candidate['ghosts'].items():
        check(mid in slots['methods'] and slots['methods'][mid]['has_body'],'Unknown ghost method')
        params(decls);method_ghosts[mid]=set()
        for p in decls:
            name=p['name'];check(name not in reserved|helper_names|ghost_names,'Ghost name collision')
            ghost_names.add(name);method_ghosts[mid].add(name)
        if decls:add(slots['methods'][mid]['body_start']+1,'\n'.join('var '+p['name']+': '+p['type'] for p in decls),method_id=mid,kind='ghost_declarations')
    def statements(stmts,locals_,depth=0):
        check(isinstance(stmts,list) and len(stmts)<=100 and depth<15,'Invalid proof body')
        out=[]
        for st in stmts:
            k=st.get('kind')
            if k=='ghost_assign':
                exact(st,('kind','target','expr'),('target','expr'));check(st['target'] in locals_,'Can only assign declared ghost/local')
                out.append(st['target']+' := '+expr(st['expr']))
            elif k=='assert':
                exact(st,('kind','expr'),('expr',));out.append('assert '+expr(st['expr']))
            elif k in ('fold','unfold'):
                exact(st,('kind','predicate'),('predicate',));out.append(k+' '+expr(st['predicate']))
            elif k=='lemma_call':
                exact(st,('kind','name','args'),('name','args'));check(st['name'] in lemma_names,'Call must target a provided lemma')
                out.append(st['name']+'('+', '.join(expressions(st['args']))+')')
            elif k=='if':
                exact(st,('kind','condition','then','else'),('condition','then','else'))
                out.append('if ('+expr(st['condition'])+') {\n'+statements(st['then'],locals_,depth+1)+'\n} else {\n'+statements(st['else'],locals_,depth+1)+'\n}')
            else:raise Rejected('Unsupported proof statement: '+str(k))
        return '\n'.join(out)
    helper_text=[]
    for h in candidate['functions']:
        exact(h,('name','params','return_type','requires','ensures','body_expr','decreases'),('name','params','return_type','body_expr','decreases'))
        text='function '+h['name']+'('+params(h['params'])+'): '+typ(h['return_type'])+'\n'
        text+='\n'.join('requires '+v for v in expressions(h.get('requires',[])))+'\n'
        text+='\n'.join('ensures '+v for v in expressions(h.get('ensures',[])))+'\n'
        text+='decreases '+', '.join(measures(h['decreases']))+'\n{\n'+expr(h['body_expr'])+'\n}'
        helper_text.append(text)
    for h in candidate['predicates']:
        exact(h,('name','params','body_assertion'),('name','params','body_assertion'))
        helper_text.append('predicate '+h['name']+'('+params(h['params'])+') {\n'+expr(h['body_assertion'])+'\n}')
    for h in candidate['lemmas']:
        exact(h,('name','params','requires','ensures','locals','body','decreases'),('name','params','body','decreases'))
        params(h.get('locals',[])); local_names={v['name'] for v in h.get('locals',[])}
        check(not local_names & {v['name'] for v in h['params']},'Lemma local shadows parameter')
        text='method '+h['name']+'('+params(h['params'])+')\n'
        text+='\n'.join('requires '+v for v in expressions(h.get('requires',[])))+'\n'
        text+='\n'.join('ensures '+v for v in expressions(h.get('ensures',[])))+'\n'
        text+='decreases '+', '.join(measures(h['decreases']))+'\n{\n'
        text+='\n'.join('var '+v['name']+': '+v['type'] for v in h.get('locals',[]))+'\n'
        text+=statements(h['body'],local_names)+'\n}'
        helper_text.append(text)
    if helper_text:add(len(base),'\n\n'.join(helper_text),kind='helpers')
    check(isinstance(candidate['insertions'],list) and len(candidate['insertions'])<=100,'Invalid insertions')
    for ins in candidate['insertions']:
        exact(ins,('method_id','anchor_id','position','statements'),('method_id','anchor_id','position','statements'))
        a=slots['anchors'].get(ins['anchor_id']);check(a is not None,'Unknown anchor')
        check(a['method']==ins['method_id'] and ins['position'] in a.get('positions',('before','after')),'Invalid insertion location')
        text=statements(ins['statements'],method_ghosts.get(ins['method_id'],set()))
        if text:add(a['start' if ins['position']=='before' else 'end'],text,method_id=a['method'],anchor_id=ins['anchor_id'],kind='proof_statements')
    result,spans,mapping=apply_edits(base,edits)
    return result,{'spans':spans,'source_map':mapping,'helpers':sorted(helper_names),'ghosts':sorted(ghost_names),'slots':slots}

def audit_ast(base_ast,new_ast,plan,candidate,task,generated_domains=None):
    check(new_ast.get('ok'),'Silver parsing/type checking failed')
    def generated(n):
        p=n.get('position',{});line=p.get('line');end=p.get('end_line',line)
        return line is not None and any(s['start_line']<=line<=end<=s['end_line'] for s in plan['spans'])
    def core(n,strip=False):
        if strip and generated(n): return None
        # Silver emits an empty, positionless Seqn for a var declaration.
        # Declarations themselves remain explicit nodes and are checked below.
        if n['kind']=='Seqn' and not n.get('children'): return None
        children=[x for c in n.get('children',[]) if (x:=core(c,strip)) is not None]
        info={k:n[k] for k in ('kind','name','typ') if k in n}
        if children:info['children']=children
        elif not n.get('children'):info['leaf']=n['text']
        return info
    for kind in ('domains','fields','functions','predicates','methods'):
        old={v['name']:v for v in base_ast.get(kind,[])}
        new={v['name']:v for v in new_ast.get(kind,[])}
        expected=set(old)|{h['name'] for h in candidate.get({'functions':'functions','predicates':'predicates','methods':'lemmas'}.get(kind,''),[])}
        if kind=='domains' and generated_domains:
            # The termination plugin adds these exact built-in declarations
            # when an added helper has an integer termination measure.
            # No candidate-provided domains/axioms are accepted here.
            for name in set(new)-set(old):
                check(new[name]['text']==generated_domains.get(name),'Unexpected generated domain: '+name)
            expected |= set(new)-set(old)
        check(set(new)==expected,'Unexpected/missing Silver member: '+kind)
        for name,n in old.items():
            if kind=='methods':
                for f in ('formalArgs','formalReturns'):check([core(x) for x in n[f]]==[core(x) for x in new[name][f]],'Changed signature')
                check(n.get('has_body')==new[name].get('has_body'),'Changed abstract/body status')
                for f in ('pres','posts'):
                    kept=[core(x,True) for x in new[name][f] if not generated(x)]
                    check([core(x) for x in n[f]]==kept,'Changed trusted method contract')
                if n.get('has_body'):check(core(n['body'])==core(new[name]['body'],True),'Changed program AST: '+name)
            else: check(n['text']==new[name]['text'],'Changed trusted '+kind+': '+name)
        for name in set(new)-set(old):
            if kind=='domains' and generated_domains:continue
            n=new[name];check(n.get('has_body', 'body' in n),'Helper has no implementation')
            for c in walk(n):
                check(c['kind'] not in ('Inhale','Exhale','InhaleExhaleExp','MagicWand','Package','Apply','NewStmt','FieldAssign','Goto','While'),'Forbidden helper AST: '+c['kind'])
                if c['kind']=='MethodCall':check(c.get('name') in plan['helpers'] or c['text'].split('(')[0].strip() in plan['helpers'],'Helper calls program method')
    for kind in ('methods','functions','predicates'):
        for m in new_ast.get(kind,[]):
            for n in walk(m):
                if generated(n):check(n['kind'] not in ('Inhale','Exhale','InhaleExhaleExp','MagicWand','Quasihavoc','NewStmt','FieldAssign'),'Forbidden generated AST: '+n['kind'])
    return {'status':'accepted','original_program_ast_unchanged':True,'original_targets_unchanged':True,
            'verified_members':{k:[n['name'] for n in new_ast.get(k,[])] for k in ('methods','functions','predicates')},
            'trusted_bodyless_members':[n['name'] for k in ('methods','functions') for n in base_ast.get(k,[]) if not n.get('has_body',False)]}
