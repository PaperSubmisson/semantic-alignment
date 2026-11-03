"""Box contents and unboxing; cache/identity observations are rejected."""
from framework.j2silver.model import *
from .types import NarrowType

BOXES = {'Integer': INT, 'Long': JType('long'), 'Short': NarrowType('short'), 'Byte': NarrowType('byte'), 'Character': JType('char')}


class BoxedMixin:
    def unbox(self, value, n):
        if value.typ.base not in BOXES or value.typ.dims:
            return value
        self.fields['c0_box'] = 'Int'
        pre = list(value.pre) + self.guard(value.value + ' != null', 'NullPointerException')
        return Expr(value.value + '.c0_box', BOXES[value.typ.base], pre)

    def box(self, name, value, pre, n):
        self.fields['c0_box'] = 'Int'
        if not value.typ.integer:
            self.fail('Box constructor requires an integral value', n)
        v = self.fresh(JType(name), pre)
        pre += [f'{v} := new(c0_box)', f'{v}.c0_box := {value.value}']
        self.notes.add('Numeric box contents/unboxing supported; wrapper identity/cache/hashCode observations rejected.')
        return Expr(v, JType(name), pre)

    def object_new(self, n):
        name = txt(n.child_by_field_name('type'))
        if name in BOXES:
            nodes = children(n.child_by_field_name('arguments'))
            if len(nodes) != 1:
                self.fail('Unsupported numeric box constructor', n)
            value = self.expr(nodes[0]); pre = []; v = self.freeze(value, pre)
            return self.box(name, Expr(v, value.typ), pre, n)
        return super().object_new(n)

    def convert(self, e, t, n):
        if t.integer or t.real:
            if e.typ.base in BOXES and not e.typ.dims:
                self.fields['c0_box'] = 'Int'
                # Field read enforces non-null/access; a caught unboxing needs
                # explicit sequencing, so reject that form rather than erase it.
                if self.catches:
                    self.fail('Caught assignment unboxing needs explicit conversion edge', n)
                return super().convert(Expr(e.value + '.c0_box', BOXES[e.typ.base]), t, n)
        return super().convert(e, t, n)

    def operation(self, a, b, op, n):
        boxed_a = a.typ.base in BOXES and not a.typ.dims
        boxed_b = b.typ.base in BOXES and not b.typ.dims
        if op in ('==', '!=') and a.typ.ref and b.typ.ref and (boxed_a or boxed_b):
            if a.typ == NULL or b.typ == NULL:
                return Expr(f'({a.value} {op} {b.value})', BOOL, a.pre + b.pre)
            self.fail('Box reference identity/cache semantics unsupported', n)
        if boxed_a or boxed_b:
            a = self.unbox(a, n); b = self.unbox(b, n)
        return super().operation(a, b, op, n)

    def call(self, n):
        owner = txt(n.child_by_field_name('object')); name = txt(n.child_by_field_name('name'))
        nodes = children(n.child_by_field_name('arguments'))
        if owner in BOXES and name == 'valueOf' and len(nodes) == 1:
            value = self.expr(nodes[0]); pre = []; v = self.freeze(value, pre)
            return self.box(owner, Expr(v, value.typ), pre, n)
        local = self.lookup(owner)
        if local and local[1].base in BOXES and name=='equals' and len(nodes)==1:
            recv=self.expr(n.child_by_field_name('object')); arg=self.expr(nodes[0]);pre=[]
            rv=self.freeze(recv,pre);av=self.freeze(arg,pre)
            pre+=self.guard(rv+' != null','NullPointerException')
            if arg.typ==recv.typ:
                return Expr(f'({av} != null && {rv}.c0_box == {av}.c0_box)',BOOL,pre)
            return Expr('false',BOOL,pre)
        if local and local[1].base in BOXES and not nodes and name in ('intValue', 'longValue', 'floatValue', 'doubleValue', 'shortValue', 'byteValue', 'charValue'):
            e = self.unbox(self.expr(n.child_by_field_name('object')), n)
            if name in ('shortValue','byteValue','charValue') or name=='intValue' and e.typ.base=='long':
                self.runtime.add('bitops')
                bits={'shortValue':16,'byteValue':8,'charValue':16,'intValue':32}[name]
                target={'shortValue':NarrowType('short'),'byteValue':NarrowType('byte'),'charValue':JType('char'),'intValue':INT}[name]
                fn='c0_unsigned' if name=='charValue' else 'c0_signed'
                return Expr(f'{fn}({e.value},{bits})',target,e.pre)
            target = {'floatValue': JType('float'), 'doubleValue': REAL, 'longValue': JType('long')}.get(name, INT)
            return Expr(self.convert(e, target, n), target, e.pre)
        return super().call(n)
