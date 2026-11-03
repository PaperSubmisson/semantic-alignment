"""Tree-sitter syntax plus declaration collection for our explicitly checked subset."""
from pathlib import Path
import re
import sys
from framework.j2silver.model import *
from .types import parse_type


class TypedMethod(Method):
    signature_overload = False
    @property
    def key(self):
        suffix = '/' + ','.join(str(t) for _, t in self.parameters) if self.signature_overload else (f'/{len(self.parameters)}' if self.overloaded else '')
        return self.owner + '.' + self.name + suffix
    @property
    def silver(self):
        suffix = ('__' + '_'.join(safe(str(t)) for _, t in self.parameters)) if self.signature_overload else (f'__arity{len(self.parameters)}' if self.overloaded else '')
        return safe(self.owner) + '_' + ('init' if self.constructor else safe(self.name)) + suffix


def parser():
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_java
    except ImportError as e:
        raise RuntimeError('Install dependencies: python -m pip install -r requirements.txt') from e
    return Parser(Language(tree_sitter_java.language()))


class SpecProgram:
    def __init__(self, sources):
        self.classes={};self.sources=[];self.node_paths={};self.methods={};self.parents={}
        self.interfaces={};self.enums={};self.specializations=[]
        p=parser()
        for path in sources:
            from .preprocess import normalize
            path=Path(path);data=normalize(path.read_bytes(),path);tree=p.parse(data)
            self.sources.append((path,data,tree))
            if tree.root_node.has_error:
                bad=next((n for n in walk(tree.root_node) if n.type=='ERROR' or n.is_missing),tree.root_node)
                raise TranslationError('Java syntax error',bad,path)
            for n in walk(tree.root_node): self.node_paths[n.id]=path
            for n in walk(tree.root_node):
                if n.type=='enum_declaration':
                    self.enums[txt(n.child_by_field_name('name'))]=[txt(x.child_by_field_name('name')) for x in children(n.child_by_field_name('body')) if x.type=='enum_constant']
                if n.type=='interface_declaration':
                    self.interfaces[txt(n.child_by_field_name('name'))]=n
            for n in children(tree.root_node):
                if n.type=='class_declaration': self.collect_class(n)
                elif n.type not in {'package_declaration','import_declaration','empty_statement','interface_declaration','enum_declaration'}:
                    self.fail('Unsupported top-level declaration: '+n.type,n)
        from .exceptions import PARENTS
        self.parents.update(PARENTS)
        # First expansion supports passive hierarchies (data/exception classes).
        # Overrides, implicit superclass constructors and dispatch are not erased.
        merged=set()
        def merge_fields(current, active):
            if current in merged:return
            if current in active:self.fail('Cyclic inheritance', self.classes[current].node)
            active=active | {current}
            if current in self.parents:
                parent=self.parents[current]
                if parent in self.classes:
                    merge_fields(parent,active)
                    pclass=self.classes[parent]
                    if any(not m.constructor for m in pclass.methods.values()) or any(not m.constructor for m in self.classes[current].methods.values()):
                        self.fail('Active inheritance needs superclass construction and virtual dispatch', self.classes[current].node)
                    if any(f.value is not None for c in (pclass,self.classes[current]) for f in c.fields.values()) and any(m.constructor for c in (pclass,self.classes[current]) for m in c.methods.values()):
                        self.fail('Mixed inherited field initializers and explicit constructors need ordered initialization',self.classes[current].node)
                    inherited = dict(pclass.fields)
                    if set(inherited)&set(self.classes[current].fields):
                        self.fail('Hidden inherited fields require qualified resolution', self.classes[current].node)
                    inherited.update(self.classes[current].fields);self.classes[current].fields=inherited
                elif parent not in set(PARENTS)|{'Throwable','Object'}:
                    self.fail('Unresolved superclass '+parent,self.classes[current].node)
            merged.add(current)
        for name in list(self.classes):merge_fields(name,set())
        self.specialize_interfaces()
        known=set(self.classes)|set(PARENTS)|set(self.interfaces)|set(self.enums)|{'C0Map','C0Enumeration','PrintWriter','Scanner','Object','Class','Integer','Long','Short','Byte','Character','Throwable','String','StringBuilder','StringBuffer','Random','null','byte','short','int','long','char','boolean','double','float','void'}
        silver_names=set()
        for c in self.classes.values():
            for f in c.fields.values():
                if f.typ.base not in known:self.fail('Unresolved field type '+str(f.typ),f.value or c.node)
            for m in c.methods.values():
                for _,t in m.parameters:
                    if t.base not in known:self.fail('Unresolved parameter type '+str(t),m.node)
                if m.typ.base not in known:self.fail('Unresolved return type '+str(m.typ),m.node)
                if m.silver in silver_names:self.fail('Generated method name collision: '+m.silver,m.node)
                silver_names.add(m.silver)
                self.methods[m.key]=m

    def fail(self,message,node): raise TranslationError(message,node,self.node_paths.get(node.id))

    def collect_class(self,node):
        name=txt(node.child_by_field_name('name'))
        if name in {'String','Random','Math','System','Arrays','Integer','Long'}:
            self.fail('Shadowing a modeled platform class is unsupported: '+name,node)
        if name in self.classes:self.fail('Duplicate/simple class name ambiguity: '+name,node)
        if node.child_by_field_name('type_parameters'):
            self.fail('Generic source classes are outside the subset',node)
        superclass=node.child_by_field_name('superclass')
        if superclass:
            self.parents[name]=txt(children(superclass)[0]).split('.')[-1]
        c=Class(name,node);self.classes[name]=c
        for n in children(node.child_by_field_name('body')):
            if n.type=='class_declaration':self.collect_class(n)
            elif n.type=='field_declaration':
                mods=txt(next((x for x in children(n) if x.type=='modifiers'),None))
                for d in children(n):
                    if d.type!='variable_declarator':continue
                    extra=txt(d.child_by_field_name('dimensions')).count('[]')
                    t=parse_type(n.child_by_field_name('type'),extra)
                    fname=txt(d.child_by_field_name('name'))
                    c.fields[fname]=Field(fname,t,name,'static' in mods.split(),'final' in mods.split(),d.child_by_field_name('value'))
            elif n.type in {'method_declaration','constructor_declaration'}:
                ctor=n.type=='constructor_declaration';mname=txt(n.child_by_field_name('name'))
                mods=txt(next((x for x in children(n) if x.type=='modifiers'),None))
                if 'synchronized' in mods.split() or 'native' in mods.split():self.fail('Native/synchronized method unsupported',n)
                params=[]
                for a in children(n.child_by_field_name('parameters')):
                    if a.type=='spread_parameter':
                        # Accept unused varargs declarations. Calls requiring
                        # vararg packing remain outside the ordinary call rule.
                        ds=[d for d in children(a) if d.type=='variable_declarator']
                        parameter_name=txt(ds[0].child_by_field_name('name')) if ds else ''
                        ty=next((x for x in children(a) if x.type in ('type_identifier','integral_type','generic_type')),None)
                        if not parameter_name or ty is None or any(x.type=='identifier' and txt(x)==parameter_name for x in walk(n.child_by_field_name('body'))):
                            self.fail('Only unused varargs parameters are supported',a)
                        params.append((parameter_name,parse_type(ty,1)));continue
                    if a.type!='formal_parameter':self.fail('Receiver parameter unsupported',a)
                    params.append((txt(a.child_by_field_name('name')),parse_type(a.child_by_field_name('type'),txt(a.child_by_field_name('dimensions')).count('[]'))))
                if not n.child_by_field_name('body'):self.fail('User methods must have bodies',n)
                same_name=[m for m in c.methods.values() if m.name==mname]
                duplicate_arity = any(len(m.parameters)==len(params) for m in same_name)
                if ctor and duplicate_arity:
                    self.fail('Same-arity constructor overload requires typed allocation', n)
                method=TypedMethod(name,mname,VOID if ctor else parse_type(n.child_by_field_name('type')),params,n,'static' in mods.split(),ctor,bool(same_name))
                for m in same_name:m.overloaded=True
                if duplicate_arity or any(m.signature_overload for m in same_name):
                    method.signature_overload = True
                    for m in same_name:m.signature_overload=True
                c.methods[(mname,tuple(t for _,t in params))]=method
            elif n.type not in {'empty_statement','enum_declaration'}:self.fail('Unsupported class member: '+n.type,n)

    def specialize_interfaces(self):
        # Closed-world specialization only when every allocation supplies the
        # same concrete new-expression, and a field is written only by that ctor.
        for cls in self.classes.values():
            for field in cls.fields.values():
                if field.typ.base not in self.interfaces:continue
                writes=[(m,x) for m in cls.methods.values() for x in walk(m.node) if x.type=='assignment_expression' and txt(x.child_by_field_name('left')) in (field.name,'this.'+field.name)]
                if len(writes)!=1 or not writes[0][0].constructor:self.fail('Interface field requires finite dispatch or a unique constructor assignment',cls.node)
                ctor,node=writes[0];param=txt(node.child_by_field_name('right'))
                indexes=[i for i,(p,t) in enumerate(ctor.parameters) if p==param]
                if len(indexes)!=1:self.fail('Interface field assignment is not a constructor parameter',node)
                i=indexes[0];allocations=[x for _,_,tree in self.sources for x in walk(tree.root_node) if x.type=='object_creation_expression' and txt(x.child_by_field_name('type'))==cls.name]
                concrete=set()
                for alloc in allocations:
                    args=children(alloc.child_by_field_name('arguments'))
                    if len(args)!=len(ctor.parameters) or args[i].type!='object_creation_expression':self.fail('Interface specialization needs closed concrete allocations',alloc)
                    concrete.add(txt(args[i].child_by_field_name('type')))
                if len(concrete)!=1:self.fail('Interface specialization has no unique concrete target',cls.node)
                target=next(iter(concrete));field.typ=JType(target);ctor.parameters[i]=(param,JType(target))
                self.specializations.append(cls.name+'.'+field.name+' resolved to '+target+' from all closed allocation sites')
