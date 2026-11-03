from framework.j2silver.model import *
from .types import parse_type


class ReferencesMixin:
    def method(self,m):
        lines=super().method(m)
        parent=self.program.parents.get(m.owner)
        if m.constructor and parent in self.program.classes:
            explicit=any(n.type=='explicit_constructor_invocation' for n in children(m.node.child_by_field_name('body')))
            if not explicit:
                ctor=self.program.classes[parent].resolve_method(parent,0)
                if ctor:
                    at=lines.index('{')+1
                    lines.insert(at,'  '+ctor.silver+'(self)')
        return lines

    def subtype(self, child, parent):
        seen = set()
        while child and child not in seen:
            if child == parent or parent == 'Object':
                return True
            seen.add(child); child = self.program.parents.get(child)
        return False

    def type_test(self, value, target):
        if not self.use_type_tags:
            return value + ' != null'
        tags = [str(tag) for name, tag in self.type_tags.items() if self.subtype(name, target)]
        return '(' + value + ' != null && (' + (' || '.join(value + '.c0_type == ' + tag for tag in tags) or 'false') + '))'

    def compatible(self, a, b):
        return (a==JType('Object') and b.ref) or (a.ref and b.ref and a.dims == b.dims and self.subtype(b.base, a.base)) or super().compatible(a, b)

    def expr(self, n, expected=None):
        if n.type=='field_access':
            obj=n.child_by_field_name('object');name=txt(n.child_by_field_name('field'))
            local=self.lookup(txt(obj)) if obj.type=='identifier' else None
            cls=self.owner if obj.type=='this' else self.program.classes.get(local[1].base) if local else None
            f=cls.fields.get(name) if cls else None
            if f and f.final and not f.static:
                value=self.expr(obj)
                return Expr(value.value+'.'+f.silver,f.typ,value.pre)
        if n.type == 'class_literal':
            allowed=getattr(self,'_class_existence_only',False)
            parent=n.parent
            if parent and parent.type=='argument_list' and parent.parent.type=='method_invocation':
                call=parent.parent;owner=txt(call.child_by_field_name('object')) or self.current.owner
                if owner in self.program.classes:
                    method=self.program.classes[owner].resolve_method(txt(call.child_by_field_name('name')),len(children(parent)))
                    if method:
                        i=next(i for i,a in enumerate(children(parent)) if a.id==n.id)
                        param=method.parameters[i][0]
                        allowed=not any(x.type=='identifier' and txt(x)==param for x in walk(method.node.child_by_field_name('body')))
            if not allowed:self.fail('Class literals supported only for existence or unused formal arguments',n)
            self.notes.add('Class literal existence modeled as non-null opaque Class reference; reflection/identity require separate models.')
            pre = []; v = self.fresh(JType('Class'), pre)
            return Expr(v, JType('Class'), pre + [f'{v} := new()'])
        if n.type == 'instanceof_expression':
            left = n.child_by_field_name('left') or children(n)[0]
            target = parse_type(n.child_by_field_name('right') or children(n)[1])
            self._class_existence_only = target == JType('Object')
            value = self.expr(left)
            self._class_existence_only = False
            if target == JType('Object') or target == value.typ:
                return Expr(value.value + ' != null', BOOL, value.pre)
            if value.typ.base in ('Integer','Long','Short','Byte','Character') and target == STRING:
                return Expr('false', BOOL, value.pre)
            if self.use_type_tags and target.base in self.type_tags and value.typ.base in self.type_tags and not target.dims and not value.typ.dims:
                return Expr(self.type_test(value.value, target.base), BOOL, value.pre)
            if value.typ.dims > 0 and target.dims == value.typ.dims and target.base == 'Object' and value.typ.element.ref:
                return Expr(value.value + ' != null', BOOL, value.pre)
            if value.typ.base == 'Object':
                # A generic Object input may have an arbitrary runtime subtype.
                # Only an explicitly constructed Object has a known exact type.
                init=left if left.type=='object_creation_expression' else None
                if left.type=='identifier':
                    declarations=[d for d in walk(self.current.node) if d.type=='variable_declarator' and txt(d.child_by_field_name('name'))==txt(left)]
                    if len(declarations)==1:init=declarations[0].child_by_field_name('value')
                if init is not None and init.type=='object_creation_expression' and txt(init.child_by_field_name('type'))=='Object':
                    return Expr('false', BOOL, value.pre)
                self.fail('Generic Object subtype tests need a full runtime type domain',n)
            self.fail('instanceof requires dynamic subtype information', n)
        if n.type == 'cast_expression':
            target = parse_type(n.child_by_field_name('type'))
            if not target.ref:
                return super().expr(n, expected)
            value = self.expr(n.child_by_field_name('value'))
            if target.ref and value.typ.ref:
                if target == value.typ or target == JType('Object') or (target == JType('CharSequence') and value.typ == STRING):
                    return value
                if value.typ == NULL:
                    return Expr('null', target, value.pre)
                if value.typ.base in ('Integer','Long','Short','Byte','Character') and target == STRING:
                    return Expr(value.value, target, list(value.pre) + self.guard(value.value + ' == null', 'ClassCastException'))
                if target.base in self.program.classes and (value.typ.base in self.program.classes or value.typ.base=='Object' and self.open_object_types):
                    cond = self.type_test(value.value, target.base) if self.use_type_tags else 'false'
                    pre = list(value.pre) + self.guard(f'({value.value} == null || {cond})', 'ClassCastException')
                    return Expr(value.value, target, pre)
                self.fail('Reference cast needs a runtime type model', n)
        return super().expr(n, expected)

    def object_new(self, n):
        name = txt(n.child_by_field_name('type'))
        if name == 'Object' and not children(n.child_by_field_name('arguments')):
            pre = []; v = self.fresh(JType('Object'), pre)
            return Expr(v, JType('Object'), pre + [f'{v} := new()'])
        result = super().object_new(n)
        if name in self.program.classes and not any(m.constructor for m in self.program.classes[name].methods.values()):
            parent=self.program.parents.get(name)
            while parent in self.program.classes:
                cls=self.program.classes[parent]
                ctor=cls.resolve_method(parent,0)
                if ctor:
                    result.pre.append(ctor.silver+'('+result.value+')')
                    break
                if any(m.constructor for m in cls.methods.values()):self.fail('Implicit constructor needs a no-arg superclass constructor',n)
                parent=self.program.parents.get(parent)
        if self.use_type_tags and result.typ.base in self.program.classes:
            pos = next(i for i, line in enumerate(result.pre) if line.startswith(result.value + ' := new('))
            line = result.pre[pos]
            result.pre[pos] = line[:-1] + (', ' if not line.endswith('new()') else '') + 'c0_type)'
            result.pre.insert(pos + 1, result.value + '.c0_type := ' + str(self.type_tags[result.typ.base]))
        return result

    def operation(self, a, b, op, n):
        if op in ('==','!=') and (a.typ.base == 'Class' or b.typ.base == 'Class') and a.typ != NULL and b.typ != NULL:
            self.fail('Class token identity/interning is not modeled',n)
        if op in ('==', '!=') and a.typ.ref and b.typ.ref and a.typ != STRING and b.typ != STRING:
            return Expr(f'({a.value} {op} {b.value})', BOOL, a.pre + b.pre)
        return super().operation(a, b, op, n)

    def call(self, n):
        owner = txt(n.child_by_field_name('object')); name = txt(n.child_by_field_name('name'))
        nodes = children(n.child_by_field_name('arguments'))
        local = self.lookup(owner)
        if local and local[1] == NULL and name == 'hashCode' and not nodes:
            pre = self.guard('false', 'NullPointerException')
            return Expr('0', INT, pre)
        if owner == 'Arrays' and name == 'copyOfRange' and len(nodes) == 3:
            args = [self.expr(a) for a in nodes]; pre = []
            vals = [self.freeze(a, pre) for a in args]
            if args[0].typ.dims != 1 or not all(a.typ.integer for a in args[1:]):
                self.fail('copyOfRange supports one-dimensional arrays and integer bounds', n)
            src, start, end = vals; typ = args[0].typ; field = self.field(typ)
            pre += [f'assert {src} != null', f'assert 0 <= {start} && {start} <= |{src}.{field}| && {start} <= {end}']
            result = self.allocate(typ, [f'({end}-{start})'], pre, n)
            count = f'({end} <= |{src}.{field}| ? {end} : |{src}.{field}|)'
            pre += [f'{result}.{field} := {src}.{field}[{start}..{count}] ++ {result}.{field}[{count}-{start}..]']
            return Expr(result, typ, pre)
        return super().call(n)
