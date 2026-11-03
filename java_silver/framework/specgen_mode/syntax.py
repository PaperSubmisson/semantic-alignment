"""C0-only control-flow extensions. Original loop lowering stays unchanged."""
from framework.j2silver.model import *
from framework.j2silver.compiler import indent
from .types import parse_type


class SyntaxMixin:
    def local(self, name, typ, node):
        value = super().local(name, typ, node)
        if value in ('lhs', 'rhs', 'in'):
            renamed = '_c0_' + value
            self.names.add(renamed)
            self.scopes[-1][name] = (renamed, typ)
            return renamed
        return value

    def stmt(self, n):
        if n.type == 'explicit_constructor_invocation':
            target = txt(n.child_by_field_name('constructor'))
            args = [self.expr(a) for a in children(n.child_by_field_name('arguments'))]
            if target == 'super' and not args and self.owner.name not in self.program.parents:
                return []
            if target != 'this':
                self.fail('Superclass constructor invocation requires an active hierarchy model', n)
            ctor = self.owner.resolve_method(self.owner.name, len(args))
            if ctor is None or ctor is self.current:
                self.fail('Unresolved/cyclic constructor delegation', n)
            pre = []; vals = [self.freeze(a, pre) for a in args]
            actual = [self.convert(Expr(v, a.typ), t, n) for v, a, (_, t) in zip(vals, args, ctor.parameters)]
            return pre + [ctor.silver + '(' + ', '.join(['self'] + actual) + ')']
        if n.type == 'local_variable_declaration':
            out = []
            for d in children(n):
                if d.type != 'variable_declarator':
                    continue
                typ = parse_type(n.child_by_field_name('type'), txt(d.child_by_field_name('dimensions')).count('[]'))
                allowed = set(self.program.classes) | set(self.program.interfaces) | set(self.program.enums) | {'Scanner','C0Map','C0Enumeration','Integer', 'Long', 'Short', 'Byte', 'Character', 'Object', 'CharSequence', 'String', 'StringBuilder', 'StringBuffer', 'Random', 'byte', 'short', 'int', 'long', 'char', 'boolean', 'float', 'double'}
                if typ.base not in allowed:
                    self.fail('Unknown local type ' + str(typ), n)
                value = d.child_by_field_name('value')
                e = self.expr(value, typ) if value else None
                if typ.base in ('Character','Integer','Long','Short','Byte') and e and e.typ.integer:
                    pre=list(e.pre)
                    e=self.box(typ.base,Expr(e.value,e.typ),pre,d)
                if (typ.base in ('Object', 'CharSequence') or typ.base in self.program.interfaces) and not typ.dims:
                    name = txt(d.child_by_field_name('name'))
                    reassigned = any(x.type == 'assignment_expression' and txt(x.child_by_field_name('left')) == name for x in walk(self.current.node))
                    if not e or not e.typ.ref or reassigned or (typ.base == 'CharSequence' and e.typ not in (STRING, JType('StringBuilder'))):
                        self.fail('Object/CharSequence local requires stable concrete reference type', d)
                    typ = e.typ
                    self.notes.add('Stable Object/CharSequence locals retain their concrete initializer type; reassignment and unresolved dynamic types are rejected.')
                v = self.local(txt(d.child_by_field_name('name')), typ, d)
                out.append(f'var {v}: {typ.silver}')
                if e:
                    out += e.pre + [f'{v} := {self.convert(e, typ, d)}']
                    self.assigned.add(v)
            return out
        if n.type == 'switch_expression':
            pre = []
            e = self.expr(n.child_by_field_name('condition'))
            if not e.typ.integer:
                self.fail('Only integer/char switch selectors are supported', n)
            selector = self.freeze(e, pre)
            end = f'_c0_switch_end{self.counter}'
            self.counter += 1
            groups = children(n.child_by_field_name('body'))
            targets, bodies, default = [], [], end
            before = set(self.assigned)
            exits = []
            self.scopes.append({})
            for group in groups:
                if group.type != 'switch_block_statement_group':
                    self.fail('Modern switch rule requires a separate model', group)
                label = f'_c0_case{self.counter}'
                self.counter += 1
                statements = []
                for child in children(group):
                    if child.type == 'switch_label':
                        values = children(child)
                        if not values:
                            default = label
                        for item in values:
                            v = self.expr(item)
                            if v.pre or not v.typ.integer:
                                self.fail('Case label must be a scalar constant', item)
                            targets.append((v.value, label))
                    else:
                        statements.append(child)
                self.assigned = set(before)
                self.loop_stack.append((end, None))
                lines = []
                for child in statements:
                    lines += self.stmt(child)
                self.loop_stack.pop()
                exits.append(set(self.assigned))
                bodies += ['label ' + label] + lines
            self.scopes.pop()
            # Silver type checking validates flow; conservative assignment set for
            # ordinary switch branches, with Java compilation as independent gate.
            self.assigned = set.union(before, *exits) if exits else before
            for value, label in targets:
                pre += [f'if ({selector} == {value}) {{ goto {label} }}']
            return pre + ['goto ' + default] + bodies + ['label ' + end]
        return super().stmt(n)
