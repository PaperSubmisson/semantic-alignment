from framework.j2silver.model import *
from .types import EnumType

class EnumMixin:
    def expr(self,n,expected=None):
        if n.type=='field_access':
            owner=txt(n.child_by_field_name('object')).split('.')[-1];name=txt(n.child_by_field_name('field'))
            if owner in self.program.enums and name in self.program.enums[owner]:
                return Expr(str(self.program.enums[owner].index(name)),EnumType(owner))
        if n.type=='identifier' and n.parent and n.parent.type=='switch_label':
            groups=n.parent.parent.parent;switch=groups.parent
            e=self.expr(switch.child_by_field_name('condition'))
            if isinstance(e.typ,EnumType) and txt(n) in self.program.enums[e.typ.base]:
                return Expr(str(self.program.enums[e.typ.base].index(txt(n))),e.typ)
        return super().expr(n,expected)

    def stmt(self,n):
        if n.type=='switch_expression':
            e=self.expr(n.child_by_field_name('condition'))
            if isinstance(e.typ,EnumType):
                if e.pre:self.fail('Effectful enum switch needs selector capture',n)
                return self.guard(e.value+' != -1','NullPointerException')+super().stmt(n)
        return super().stmt(n)

    def content(self,a,n):
        if isinstance(a.typ,EnumType):
            result='Seq(110,117,108,108)'
            for i,name in reversed(list(enumerate(self.program.enums[a.typ.base]))):
                seq='Seq('+','.join(str(ord(c)) for c in name)+')'
                result=f'({a.value} == {i} ? {seq} : {result})'
            return result
        return super().content(a,n)

    def operation(self,a,b,op,n):
        if op in ('==','!='):
            if isinstance(a.typ,EnumType) and b.typ==NULL:return Expr(f'({a.value} {op} -1)',BOOL,a.pre+b.pre)
            if isinstance(b.typ,EnumType) and a.typ==NULL:return Expr(f'(-1 {op} {b.value})',BOOL,a.pre+b.pre)
        return super().operation(a,b,op,n)
