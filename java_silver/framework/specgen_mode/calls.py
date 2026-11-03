from framework.j2silver.model import *


class CallsMixin:
    def call(self, n):
        obj = n.child_by_field_name('object')
        owner = txt(obj)
        name = txt(n.child_by_field_name('name'))
        nodes = children(n.child_by_field_name('arguments'))
        cname = self.current.owner if obj is None else owner if owner in self.program.classes else None
        if obj is not None and obj.type == 'identifier' and self.lookup(owner):
            cname = self.lookup(owner)[1].base
        methods = self.program.classes[cname].methods.values() if cname in self.program.classes else []
        overloaded = any(getattr(m, 'signature_overload', False) and m.name == name for m in methods)
        complex_recv = obj is not None and obj.type in ('method_invocation', 'object_creation_expression', 'parenthesized_expression')
        if not overloaded and not complex_recv and not (owner=='Verifier' and owner in self.program.classes):
            return super().call(n)
        pre = []; recv = None
        if obj is not None and owner not in self.program.classes:
            e = self.expr(obj)
            recv = Expr(self.freeze(e, pre), e.typ)
            cname = e.typ.base
        args = [self.expr(a) for a in nodes]
        vals = [self.freeze(a, pre) for a in args]
        args = [Expr(v, a.typ) for v, a in zip(vals, args)]
        if recv and recv.typ == JType('StringBuilder'):
            return self.builder_call(recv.value, name, args, pre, n)
        if recv and recv.typ == STRING:
            return self.string_call(recv, name, args, pre, n)
        if recv and recv.typ.base == 'Random':
            pre += [f'assert {recv.value} != null']
            if not args and name in ('nextInt', 'nextBoolean'):
                return self.nondet(INT if name == 'nextInt' else BOOL, pre)
            self.fail('Unsupported Random call', n)
        if cname not in self.program.classes:
            self.fail('Unsupported receiver type ' + str(cname), n)
        methods = [m for m in self.program.classes[cname].methods.values() if m.name == name and len(m.parameters) == len(args) and not m.constructor]
        exact = [m for m in methods if all(a.typ == t for a, (_, t) in zip(args, m.parameters))]
        compatible = exact or [m for m in methods if all(self.compatible(t, a.typ) for a, (_, t) in zip(args, m.parameters))]
        if len(compatible) != 1:
            self.fail('Unresolved/ambiguous typed call ' + cname + '.' + name, n)
        m = compatible[0]
        actual = [self.convert(a, t, n) for a, (_, t) in zip(args, m.parameters)]
        if not m.static:
            if recv:
                actual.insert(0, recv.value); pre += [f'assert {recv.value} != null']
            elif obj is None and not self.current.static:
                actual.insert(0, 'self')
            else:
                self.fail('Instance method requires a receiver', n)
        call = m.silver + '(' + ', '.join(actual) + ')'
        if m.typ == VOID:
            return Expr('', VOID, pre + [call])
        v = self.fresh(m.typ, pre)
        return Expr(v, m.typ, pre + [f'{v} := {call}'])
