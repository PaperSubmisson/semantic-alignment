"""Identify explicit exceptional results without changing already-local catch models."""
from framework.j2silver.model import children,txt,walk

def exceptional_methods(program):
    def subtype(a,b):
        while a:
            if a==b:return True
            a=program.parents.get(a)
        return False
    escaped=set()
    for key,m in program.methods.items():
        for n in walk(m.node):
            if n.type!='throw_statement':continue
            e=children(n)[0];typ=None
            if e.type=='object_creation_expression':typ=txt(e.child_by_field_name('type'))
            if e.type=='identifier':
                types=set()
                for x in walk(m.node):
                    if x.type=='variable_declarator' and txt(x.child_by_field_name('name'))==txt(e):types.add(txt(x.parent.child_by_field_name('type')))
                    if x.type=='catch_formal_parameter' and txt(x.child_by_field_name('name'))==txt(e):
                        types.add(txt(next(c for c in children(x) if c.type=='catch_type')))
                if len(types)==1:typ=next(iter(types))
            covered=False;p=n.parent
            while p and p.id!=m.node.id:
                if p.type=='try_statement':
                    body=p.child_by_field_name('body')
                    if body.start_byte<=n.start_byte<body.end_byte:
                        for c in children(p):
                            if c.type=='catch_clause':
                                param=next(x for x in children(c) if x.type=='catch_formal_parameter')
                                ct=txt(next(x for x in children(param) if x.type=='catch_type')).split('.')[-1]
                                if typ and subtype(typ,ct):covered=True
                p=p.parent
            if not covered:escaped.add(key)
    changed=True
    while changed:
        changed=False;names={program.methods[k].name for k in escaped}
        for k,m in program.methods.items():
            if k not in escaped and any(n.type=='method_invocation' and txt(n.child_by_field_name('name')) in names for n in walk(m.node)):
                escaped.add(k);changed=True
    called={txt(n.child_by_field_name('name')) for m in program.methods.values() for n in walk(m.node) if n.type=='method_invocation'}
    return escaped,{k for k in escaped if program.methods[k].name not in called}
