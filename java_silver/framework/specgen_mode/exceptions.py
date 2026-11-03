"""C0 exception edges for local caught runtime failures.

Uncaught failures remain safety obligations. Cross-method exceptional exits and
finally are rejected until their complete protocol is available.
"""
from framework.j2silver.model import *
from framework.j2silver.compiler import indent

PARENTS = {'ArithmeticException': 'RuntimeException', 'NullPointerException': 'RuntimeException',
           'IndexOutOfBoundsException': 'RuntimeException', 'ArrayIndexOutOfBoundsException': 'IndexOutOfBoundsException',
           'StringIndexOutOfBoundsException': 'IndexOutOfBoundsException', 'NegativeArraySizeException': 'RuntimeException',
           'ClassCastException': 'RuntimeException', 'NumberFormatException': 'IllegalArgumentException',
           'IllegalArgumentException': 'RuntimeException', 'RuntimeException': 'Exception', 'Exception': 'Throwable',
           'IOException':'Exception','UnsupportedEncodingException':'IOException',
           'NoSuchElementException':'RuntimeException','InputMismatchException':'NoSuchElementException'}


class ExceptionsMixin:
    def falls_through(self, n):
        if n.type == 'try_statement' and not any(c.type in ('finally_clause','resource_specification') for c in children(n)):
            # Completing the try/catch normally requires either its body or
            # one of its handlers to complete normally. Abrupt exits do not
            # require an otherwise unreachable synthetic return statement.
            return self.falls_through(n.child_by_field_name('body')) or any(
                self.falls_through(c.child_by_field_name('body')) for c in children(n) if c.type=='catch_clause')
        return super().falls_through(n)

    def method(self, m):
        self.catches = []
        lines=super().method(m)
        if m.key in self.exceptional_methods:
            if m.typ==VOID:lines[0]+=' returns (_c0_thrown: Ref)'
            else:lines[0]=lines[0][:-1]+', _c0_thrown: Ref)'
            at=lines.index('{')
            clauses=['  ensures _c0_thrown != null ==> acc(_c0_thrown.c0_type)']
            for f in {f.silver:f for c in self.program.classes.values() if self.subtype(c.name,'Throwable') for f in c.fields.values() if not f.static}.values():
                clauses+=['  ensures _c0_thrown != null ==> acc(_c0_thrown.'+f.silver+')']
            if m.key in self.exception_roots:clauses+=['  ensures _c0_thrown == null']
            lines[at:at]=clauses;at=lines.index('{')
            lines.insert(at+1,'  _c0_thrown := null')
            lines.insert(len(lines)-1,'  label _c0_exception_return')
            self.notes.add('Explicit cross-method exceptions use _c0_thrown result and catch dispatch; normal output is relevant only when this result is null. Entry methods require no uncaught exception.')
        return lines

    def propagate(self,ref):
        if self.current.key not in self.exceptional_methods:return ['assert false']
        out=['_c0_thrown := '+ref]
        if self.current.typ!=VOID:out+=['res := '+self.current.typ.default]
        return out+['goto _c0_exception_return']

    def dispatch_result(self,ref):
        out=[]
        for handlers in reversed(self.catches):
            for caught,label,var in handlers:
                out+=[f'if ({self.type_test(ref,caught)}) {{ {var} := {ref}; goto {label} }}']
        return out+self.propagate(ref)

    def call(self,n):
        e=super().call(n)
        targets={m.silver for k,m in self.program.methods.items() if k in self.exceptional_methods}
        import re
        pre=[]
        for line in e.pre:
            matched=next((name for name in targets if re.search(r'\b'+re.escape(name)+r'\(',line)),None)
            if not matched:pre.append(line);continue
            exc=self.fresh(JType('Object'),pre)
            if ':=' in line:
                lhs,rhs=line.split(':=',1);pre.append(lhs.rstrip()+', '+exc+' := '+rhs.strip())
            else:pre.append(exc+' := '+line)
            pre+=['if ('+exc+' != null) {']+indent(self.dispatch_result(exc))+['}']
        return Expr(e.value,e.typ,pre)

    def guard(self, condition, kind):
        for handlers in reversed(self.catches):
            current = kind
            for caught, label, variable in handlers:
                current = kind
                while current:
                    if current == caught:
                        alloc = [f'  {variable} := new()'] if not self.use_type_tags else [f'  {variable} := new(c0_type)', f'  {variable}.c0_type := {self.type_tags[kind]}']
                        return [f'if (!({condition})) {{'] + alloc + [f'  goto {label}', '}']
                    current = self.program.parents.get(current)
        return ['assert ' + condition]

    def stmt(self, n):
        if n.type == 'throw_statement':
            e = self.expr(children(n)[0]); pre = []; ref = self.freeze(e, pre)
            if not self.subtype(e.typ.base, 'Throwable'):
                self.fail('throw requires a modeled exception reference', n)
            pre += self.guard(ref + ' != null', 'NullPointerException')
            matched = False
            for handlers in reversed(self.catches):
                for caught, label, variable in handlers:
                    cond = self.type_test(ref, caught)
                    pre += [f'if ({cond}) {{', f'  {variable} := {ref}', f'  goto {label}', '}']
                    if self.subtype(e.typ.base, caught):
                        matched = True
            if not matched:
                return pre+self.propagate(ref)
            # The final edge is unreachable only when a compatible catch exists.
            return pre + ['assert false']
        if n.type == 'try_statement':
            if any(c.type in ('finally_clause', 'resource_specification') for c in children(n)):
                self.fail('finally/resources require abrupt-completion propagation', n)
            end = f'_c0_try_end{self.counter}'; self.counter += 1
            handlers = []; pending = []; out = []
            for c in children(n):
                if c.type != 'catch_clause':
                    continue
                parameter = next(x for x in children(c) if x.type == 'catch_formal_parameter')
                ptype = next(x for x in children(parameter) if x.type == 'catch_type')
                if len(children(ptype)) != 1:
                    self.fail('Multi-catch requires separate type alternatives', ptype)
                typ = txt(ptype).split('.')[-1]
                if typ not in set(self.type_tags):
                    self.fail('Custom exception hierarchy requires runtime type tags', ptype)
                label = f'_c0_catch{self.counter}'; variable = f'_c0_exc{self.counter}'; self.counter += 1
                out += [f'var {variable}: Ref']
                handlers.append((typ, label, variable))
                pending.append((c, parameter, label, variable, typ))
            self.catches.append(handlers)
            before = set(self.assigned)
            out += self.stmt(n.child_by_field_name('body'))
            normal = set(self.assigned)
            self.catches.pop()
            out += ['goto ' + end]
            for c, parameter, label, variable, typ in pending:
                self.scopes.append({txt(parameter.child_by_field_name('name')): (variable, JType(typ))})
                self.assigned = before | {variable}
                out += ['label ' + label] + self.stmt(c.child_by_field_name('body')) + ['goto ' + end]
                self.scopes.pop()
            self.assigned = normal
            self.notes.add('Local catch edges preserve runtime divide-by-zero, null, index and negative-size exceptions; unsupported exceptional transfers are rejected.')
            return out + ['label ' + end]
        return super().stmt(n)

    def operation(self, a, b, op, n):
        if self.catches and a.typ.integer and b.typ.integer and op in ('/', '%'):
            pre = a.pre + b.pre
            av = self.fresh(a.typ, pre, a.value)
            bv = self.fresh(b.typ, pre, b.value)
            pre += self.guard(bv + ' != 0', 'ArithmeticException')
            result = super().operation(Expr(av, a.typ), Expr(bv, b.typ), op, n)
            return Expr(result.value, result.typ, pre + result.pre)
        return super().operation(a, b, op, n)

    def lvalue(self, n, capture=True):
        lv = super().lvalue(n, capture)
        if self.catches:
            if lv.array:
                lv.pre += self.guard(lv.array + ' != null', 'NullPointerException')
                lv.pre += self.guard(f'0 <= {lv.index} && {lv.index} < |{lv.array}.{lv.content}|', 'ArrayIndexOutOfBoundsException')
            elif n.type == 'field_access' and not lv.value.startswith('_c0_state.'):
                receiver = lv.value.rsplit('.', 1)[0]
                lv.pre += self.guard(receiver + ' != null', 'NullPointerException')
        return lv

    def expr(self, n, expected=None):
        if self.catches and n.type == 'assignment_expression' and txt(n.child_by_field_name('operator')) == '=':
            target = n.child_by_field_name('left')
            if target.type in ('array_access', 'field_access'):
                # Simple assignment evaluates RHS before the destination's
                # null/bounds exception. Compound assignment reads first.
                active = self.catches; self.catches = []
                try:
                    lv = self.lvalue(target)
                finally:
                    self.catches = active
                pre = list(lv.pre)
                e = self.expr(n.child_by_field_name('right'), lv.typ)
                value = self.freeze(e, pre)
                if lv.array:
                    pre += self.guard(lv.array + ' != null', 'NullPointerException')
                    pre += self.guard(f'0 <= {lv.index} && {lv.index} < |{lv.array}.{lv.content}|', 'ArrayIndexOutOfBoundsException')
                elif not lv.value.startswith('_c0_state.'):
                    pre += self.guard(lv.value.rsplit('.', 1)[0] + ' != null', 'NullPointerException')
                converted = self.convert(Expr(value, e.typ), lv.typ, n)
                pre += [lv.store(converted)]
                return Expr(converted, lv.typ, pre)
        if self.catches and n.type == 'array_access':
            lv = self.lvalue(n)
            return Expr(lv.value, lv.typ, lv.pre)
        if self.catches and n.type == 'field_access' and txt(n.child_by_field_name('field')) == 'length':
            obj = self.expr(n.child_by_field_name('object'))
            if obj.typ.dims:
                pre = []; av = self.freeze(obj, pre)
                pre += self.guard(av + ' != null', 'NullPointerException')
                return Expr(f'|{av}.{self.field(obj.typ)}|', INT, pre)
        return super().expr(n, expected)

    def array_new(self, n):
        if not self.catches:
            return super().array_new(n)
        base = parse_type(n.child_by_field_name('type'))
        sizes = []; pre = []; missing = 0
        for child in children(n):
            if child.type == 'array_initializer':
                return super().array_new(n)
            if child.type == 'dimensions_expr':
                value = self.expr(children(child)[0])
                if not value.typ.integer:
                    self.fail('Array dimension must be integer', child)
                sizes.append(self.freeze(value, pre))
            elif child.type == 'dimensions':
                missing += txt(child).count('[')
        for size in sizes:
            pre += self.guard(size + ' >= 0', 'NegativeArraySizeException')
        if not sizes:
            self.fail('Missing array dimension', n)
        typ = JType(base.base, len(sizes) + missing)
        return Expr(self.allocate(typ, sizes, pre, n), typ, pre)
