"""Explicit shared static state; root conditions encode initial class state.

Every user-method call forwards the same state object. Initializers are limited
to side-effect-free scalar literals in this phase.
"""
import re
from framework.j2silver.model import *


class StateMixin:
    def static_field(self, node):
        if node.type == 'identifier' and self.lookup(txt(node)) is None:
            f = self.owner.fields.get(txt(node))
            return f if f and f.static and f.silver in self.state_fields else None
        if node.type == 'field_access':
            owner = txt(node.child_by_field_name('object'))
            name = txt(node.child_by_field_name('field'))
            if owner in self.program.classes:
                f = self.program.classes[owner].fields.get(name)
                return f if f and f.static and f.silver in self.state_fields else None
        return None

    def expr(self, n, expected=None):
        f = self.static_field(n)
        if f:
            return Expr('_c0_state.' + f.silver, f.typ)
        if n.type == 'field_access':
            obj = n.child_by_field_name('object')
            local = self.lookup(txt(obj)) if obj.type == 'identifier' else None
            if local and local[1].base in self.program.classes:
                field = self.program.classes[local[1].base].fields.get(txt(n.child_by_field_name('field')))
                if field and field.silver in self.state_fields:
                    value = self.expr(obj)
                    return Expr('_c0_state.' + field.silver, field.typ, value.pre)
        return super().expr(n, expected)

    def lvalue(self, node, capture=True):
        f = self.static_field(node)
        if f:
            return LValue('_c0_state.' + f.silver, f.typ)
        return super().lvalue(node, capture)

    def method(self, m):
        lines = super().method(m)
        if not self.state_fields:
            return lines
        # Only calls to resolved user methods receive state; helpers do not.
        for i, line in enumerate(lines):
            for called in self.program.methods.values():
                line = re.sub(r'\b' + re.escape(called.silver) + r'\(', called.silver + '(_c0_state, ', line)
            lines[i] = line.replace('(_c0_state, )', '(_c0_state)')
        lines[0] = lines[0].replace('(_c0_state, ', '(_c0_state: Ref, ', 1).replace('(_c0_state)', '(_c0_state: Ref)', 1)
        resources = [f'acc(_c0_state.{f})' for f in self.state_fields]
        lines[1:1] = ['  requires _c0_state != null'] + ['  requires ' + x for x in resources]
        at = next(i for i, line in enumerate(lines) if line == '{' or line.startswith('  ensures '))
        lines[at:at] = ['  ensures ' + x for x in resources]
        self.maps['methods'][m.key]['shared_static_state'] = '_c0_state'
        return lines

    def compile(self):
        code, mapping = super().compile()
        if self.state_fields:
            from .call_graph import roots
            root_keys, _ = roots(self.program, code)
            wrappers = []
            for key in root_keys:
                m = self.program.methods[key]
                self.current = m; self.owner = self.program.classes[m.owner]
                signature = next(line for line in code.splitlines() if line.startswith('method ' + m.silver + '('))
                signature = signature.replace('method ' + m.silver + '(', 'method c0_entry_' + m.silver + '(')
                signature = signature.replace('_c0_state: Ref, ', '').replace('_c0_state: Ref', '')
                req = (['self != null'] if not m.static else []) + self.spec.get('methods', {}).get(key, {}).get('requires', [])
                if m.constructor:
                    req += [f'acc(self.{f.silver})' for f in self.owner.fields.values() if not f.static]
                initial = self.initial_state()
                body = ['var _c0_state: Ref', '_c0_state := new(' + ', '.join(self.state_fields) + ')']
                body += [f'_c0_state.{f.silver} := {f.typ.default}' for f in self.state_fields.values()]
                for f in self.state_fields.values():
                    if self.empty_static_array(f):
                        v='_c0_initial_'+f.silver
                        body += [f'var {v}: Ref',f'{v} := new({f.typ.field})',f'{v}.{f.typ.field} := Seq[{f.typ.element.silver}]()']
                body += [x.replace(' == ', ' := ', 1) for x in initial]
                args = ['_c0_state'] + (['self'] if not m.static else [])
                args += [p['input_name'] for p in mapping['methods'][key]['parameters']]
                body += [('res := ' if m.typ != VOID else '') + m.silver + '(' + ', '.join(args) + ')']
                wrappers += [signature] + ['  requires ' + x for x in req] + ['{'] + ['  ' + x for x in body] + ['}', '']
            code += '\n' + '\n'.join(wrappers)
            mapping['entry_wrappers'] = ['c0_entry_' + self.program.methods[k].silver for k in root_keys]
            mapping['static_state'] = {'parameter': '_c0_state', 'fields': list(self.state_fields),
                                       'initialization': 'Fresh execution entry wrapper initializes once; internal calls never reset it'}
            self.notes.add('Mutable static fields use one shared Ref across calls; execution-entry wrappers initialize once, internal/recursive calls never reset it.')
            mapping['notes'] = sorted(self.notes)
        return code, mapping

    def initial_state(self):
        clauses = []
        for f in self.state_fields.values():
            value = f.typ.default
            if self.empty_static_array(f):
                clauses.append(f'_c0_state.{f.silver} == _c0_initial_{f.silver}')
                continue
            if f.value is not None:
                if any(x.type in {'identifier', 'method_invocation', 'object_creation_expression', 'array_creation_expression', 'array_initializer', 'assignment_expression'} for x in walk(f.value)):
                    self.fail('Static initializer needs ordered allocation/call model', f.value)
                e = self.expr(f.value, f.typ)
                if e.pre:
                    self.fail('Static initializer has effects', f.value)
                value = self.convert(e, f.typ, f.value)
            clauses.append(f'_c0_state.{f.silver} == {value}')
        return clauses

    @staticmethod
    def empty_static_array(f):
        return f.static and f.final and f.typ.dims==1 and f.value is not None and f.value.type=='array_initializer' and not children(f.value)
