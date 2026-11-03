def walk(node):
    yield node
    for c in node.get('children',[]): yield from walk(c)

def offset(text,position,end=False):
    line=position['end_line' if end else 'line']; col=position['end_column' if end else 'column']
    lines=text.splitlines(keepends=True)
    return sum(len(v) for v in lines[:line-1])+col-1

def index(text,ast):
    if not ast['ok']: raise ValueError('Cannot index an invalid Silver program')
    slots={'methods':{},'loops':{},'anchors':{},'symbols':{}}
    for m in ast.get('methods',[]):
        name=m['name']; slots['methods'][name]={'name':name,'has_body':m.get('has_body',False),
            'parameters':m.get('formalArgs',[]),'returns':m.get('formalReturns',[])}
        if not m.get('has_body'): continue
        body=m['body']; pos=body['position']
        slots['methods'][name].update(body_start=offset(text,pos),body_end=offset(text,pos,True),
                                      line=m['position']['line'],requires=[n['text'] for n in m['pres']],ensures=[n['text'] for n in m['posts']])
        slots['symbols'][name]=[{'name':n['name'],'type':n.get('typ'),'kind':n['kind'],'position':n.get('position')} for n in walk(m) if n['kind'] in ('LocalVarDecl','Field')]
        loops=0; anchors=0
        for n in walk(body):
            if n['kind']=='While':
                lid=f'{name}/L{loops}';loops+=1
                slots['loops'][lid]={'method':name,'line':n['position']['line'],'body_start':offset(text,n['body']['position']),
                                     'existing_invariants':[i['text'] for i in n['invs']]}
            if n['kind'] in ('LocalVarAssign','FieldAssign','Assert','MethodCall','While','If','Fold','Unfold'):
                p=n.get('position',{})
                if not all(k in p for k in ('line','column','end_line','end_column')):continue
                aid=f'{name}/S{anchors}';anchors+=1
                slots['anchors'][aid]={'method':name,'kind':n['kind'],'line':p['line'],'text':n['text'],
                    'start':offset(text,p),'end':offset(text,p,True)}
            elif n['kind']=='Label':
                p=n.get('position',{})
                if not all(k in p for k in ('line','column','end_line','end_column')):continue
                # Separate IDs preserve every historical S/L identifier.
                aid=f'{name}/label/{n["name"]}'
                slots['anchors'][aid]={'method':name,'kind':'Label','line':p['line'],'text':n['text'],
                    'label':n['name'],'positions':['after'],
                    'start':offset(text,p),'end':offset(text,p,True)}
    return slots
