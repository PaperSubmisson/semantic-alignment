from framework.j2silver.model import *
from framework.j2silver.model import parse_type as base_parse


class NarrowType(JType):
    @property
    def ref(self):
        return self.dims > 0
    @property
    def integer(self):
        return self.dims == 0
    @property
    def element(self):
        return NarrowType(self.base, self.dims - 1)


class EnumType(JType):
    @property
    def ref(self):return self.dims > 0
    @property
    def integer(self):return self.dims == 0
    @property
    def default(self):return '-1'


def parse_type(node, extra=0):
    text = ''.join(txt(node).split())
    base = text.replace('[]', '')
    root=node
    while root and root.parent:root=root.parent
    enum_names={txt(x.child_by_field_name('name')) for x in walk(root) if x.type=='enum_declaration'} if root else set()
    if base.split('.')[-1] in enum_names:return EnumType(base.split('.')[-1],text.count('[]')+extra)
    aliases={'Map':'C0Map','Map<String,Object>':'C0Map','Map<String,String>':'C0Map',
             'HashMap<>':'C0Map','HashMap<String,Object>':'C0Map','HashMap<String,String>':'C0Map',
             'Enumeration':'C0Enumeration','Enumeration<String>':'C0Enumeration'}
    for prefix in ('java.util.',):
        if text.startswith(prefix) and text[len(prefix):] in aliases:
            return JType(aliases[text[len(prefix):]],extra)
    if text in aliases:return JType(aliases[text],extra)
    if base in ('byte', 'short'):
        return NarrowType(base, text.count('[]') + extra)
    return base_parse(node, extra)
