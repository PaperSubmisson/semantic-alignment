"""Tree-sitter syntax plus declaration collection for our explicitly checked subset."""
from pathlib import Path
import re
import sys
from .model import *


def parser():
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_java
    except ImportError as e:
        raise RuntimeError('Install dependencies: python -m pip install -r requirements.txt') from e
    return Parser(Language(tree_sitter_java.language()))


class Program:
    def __init__(self, sources):
        self.classes={};self.sources=[];self.node_paths={};self.methods={}
        p=parser()
        for path in sources:
            path=Path(path);data=path.read_bytes();tree=p.parse(data)
            if re.search(rb'\\u+[0-9a-fA-F]{4}',data):
                raise TranslationError('Java Unicode preprocessing escapes are not implemented; use literal UTF-8 characters',path=path)
            self.sources.append((path,data,tree))
            if tree.root_node.has_error:
                bad=next((n for n in walk(tree.root_node) if n.type=='ERROR' or n.is_missing),tree.root_node)
                raise TranslationError('Java syntax error',bad,path)
            for n in walk(tree.root_node): self.node_paths[n.id]=path
            for n in children(tree.root_node):
                if n.type=='class_declaration': self.collect_class(n)
                elif n.type not in {'package_declaration','import_declaration'}:
                    self.fail('Unsupported top-level declaration: '+n.type,n)
        known=set(self.classes)|{'String','Random','null','int','long','char','boolean','double','float','void'}
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
        if name in {'String','Random','Verifier','Math','System','Arrays','Integer','Long'}:
            self.fail('Shadowing a modeled platform class is unsupported: '+name,node)
        if name in self.classes:self.fail('Duplicate/simple class name ambiguity: '+name,node)
        if node.child_by_field_name('superclass') or node.child_by_field_name('interfaces') or node.child_by_field_name('type_parameters'):
            self.fail('Inheritance/interfaces/generics are outside the first subset',node)
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
                    if a.type!='formal_parameter':self.fail('Varargs/receiver parameter unsupported',a)
                    params.append((txt(a.child_by_field_name('name')),parse_type(a.child_by_field_name('type'),txt(a.child_by_field_name('dimensions')).count('[]'))))
                if not n.child_by_field_name('body'):self.fail('User methods must have bodies',n)
                same_name=[m for m in c.methods.values() if m.name==mname]
                if same_name and (ctor or any(m.constructor for m in same_name)):
                    self.fail('Constructor overloading/delegation is not supported: '+name+'.'+mname,n)
                if any(len(m.parameters)==len(params) for m in same_name):
                    self.fail('Same-arity overloads need type-based resolution; unsupported: '+name+'.'+mname,n)
                method=Method(name,mname,VOID if ctor else parse_type(n.child_by_field_name('type')),params,n,'static' in mods.split(),ctor,bool(same_name))
                for m in same_name:m.overloaded=True
                c.methods[(mname,len(params))]=method
            elif n.type not in {'empty_statement'}:self.fail('Unsupported class member: '+n.type,n)
