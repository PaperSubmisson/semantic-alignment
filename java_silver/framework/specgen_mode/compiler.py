"""Small, opt-in extensions; the original Compiler is never patched globally."""
import re
from framework.j2silver.compiler import Compiler
from framework.j2silver.model import Expr, JType, VOID, STRING, BOOL, children, txt, parse_type
from .frontend import SpecProgram
from .syntax import SyntaxMixin
from .strings import StringsMixin
from .state import StateMixin
from .calls import CallsMixin
from .numeric import NumericMixin
from .exceptions import ExceptionsMixin
from .references import ReferencesMixin
from .types import parse_type
from .boxed import BoxedMixin
from .characters import CharactersMixin
from .library import LibraryMixin
from .enum_model import EnumMixin
from framework.j2silver.model import walk

CONSOLE_MODEL = ('Standard out/err exist. Output text, stream state and printing failures are '
                 'outside this task model. Argument evaluation and its effects are retained. '
                 'Only scalar/String print/println are supported; no user toString.')


class SpecCompiler(ExceptionsMixin, StateMixin, EnumMixin, SyntaxMixin, LibraryMixin, CharactersMixin, BoxedMixin, ReferencesMixin, CallsMixin, StringsMixin, NumericMixin, Compiler):
    def __init__(self, sources, spec=None):
        self.program = SpecProgram(sources)
        self.spec = spec or {}
        self.validate_spec()
        self.fields = {}; self.runtime = set(); self.maps = {'methods': {}, 'loops': {}}
        self.notes = set(self.program.specializations); self.used_specs = set()
        self.state_fields = {}
        self.opaque_functions = {}
        from .exception_flow import exceptional_methods
        self.exceptional_methods,self.exception_roots=exceptional_methods(self.program)
        self.open_object_types=any(any(t.base=='Object' and not t.dims for _,t in m.parameters) and any(x.type=='instanceof_expression' for x in walk(m.node)) for m in self.program.methods.values())
        tag_names=set(self.program.classes) | set(self.program.parents) | {'Throwable','Object'}
        extra_tags={'IOException','UnsupportedEncodingException','NoSuchElementException','InputMismatchException'}-set(self.program.classes)
        self.type_tags = {name: i + 1 for i, name in enumerate(sorted(tag_names-extra_tags)+sorted(tag_names&extra_tags))}
        self.use_type_tags = self.open_object_types or any(c.node.child_by_field_name('superclass') for c in self.program.classes.values())
        # Empty objects need a concrete heap resource when crossing a call:
        # new() alone carries no ownership with which to establish freshness.
        empty_returns=any(m.typ.base in self.program.classes and not m.typ.dims and not any(not f.static for f in self.program.classes[m.typ.base].fields.values()) and any(n.type=='object_creation_expression' and txt(n.child_by_field_name('type'))==m.typ.base for n in walk(m.node)) for m in self.program.methods.values() if not m.constructor)
        if empty_returns:self.use_type_tags=True
        if self.use_type_tags:
            self.fields['c0_type'] = 'Int'
        for c in self.program.classes.values():
            for f in c.fields.values():
                if not f.static:
                    self.fields[f.silver] = f.typ.silver
                elif not f.final:
                    self.fields[f.silver] = f.typ.silver
                    self.state_fields[f.silver] = f
                elif f.typ.dims==1 and f.value is not None and f.value.type=='array_initializer' and not children(f.value):
                    # A shared, immutable reference to one initially empty
                    # array; identity is retained across all user calls.
                    self.fields[f.silver]=f.typ.silver
                    self.fields[f.typ.field]='Seq['+f.typ.element.silver+']'
                    self.state_fields[f.silver]=f
        if self.state_fields and self.exceptional_methods:
            self.fail('Shared static state combined with cross-method exceptional results needs a dedicated entry protocol',next(iter(self.program.methods.values())).node)
    def expr(self, n, expected=None):
        if n.type == 'unary_expression' and txt(n.child_by_field_name('operator')) in ('+', '-'):
            value = self.expr(n.child_by_field_name('operand'))
            if value.typ.real:
                return Expr('(' + txt(n.child_by_field_name('operator')) + value.value + ')', value.typ, value.pre)
            if value.typ.integer:
                return Expr('(' + txt(n.child_by_field_name('operator')) + value.value + ')', JType('long') if value.typ.base == 'long' else JType('int'), value.pre)
        if n.type == 'cast_expression':
            target = parse_type(n.child_by_field_name('type'))
            if target.real:
                value = self.expr(n.child_by_field_name('value'))
                if value.typ.real:
                    return Expr(value.value, target, value.pre)
                return Expr(self.convert(value, target, n), target, value.pre)
        value = super().expr(n, expected)
        if n.type == 'decimal_floating_point_literal' and txt(n).lower().endswith('f'):
            return Expr(value.value, JType('float'), value.pre)
        return value

    def operation(self, a, b, op, n):
        value = super().operation(a, b, op, n)
        if value.typ.real and a.typ.base != 'double' and b.typ.base != 'double':
            return Expr(value.value, JType('float'), value.pre)
        return value

    def compatible(self, a, b):
        # Both already use exact mathematical reals in this framework.
        return (a.real and b.real and a.base == 'double' and b.base == 'float') or super().compatible(a, b)

    def stmt(self, n):
        if n.type == 'assert_statement':
            parts = children(n)
            if len(parts) == 2 and parts[1].type == 'string_literal':
                value = self.expr(parts[0])
                if value.typ != BOOL:
                    self.fail('assert needs boolean', n)
                self.notes.add('Constant assert detail is diagnostic only; assertion condition is retained.')
                return value.pre + ['assert ' + value.value]
        return super().stmt(n)

    @staticmethod
    def literal_concat(n):
        if n.type == 'string_literal':
            return True
        return (n.type == 'binary_expression' and txt(n.child_by_field_name('operator')) == '+'
                and (SpecCompiler.literal_concat(n.child_by_field_name('left'))
                     or SpecCompiler.literal_concat(n.child_by_field_name('right'))))

    def print_value(self, n, pre):
        # Only flatten a string-concatenation subtree; numeric subtrees keep their
        # arithmetic, checks, evaluation order and side effects.
        if n.type == 'binary_expression' and self.literal_concat(n):
            self.print_value(n.child_by_field_name('left'), pre)
            self.print_value(n.child_by_field_name('right'), pre)
        else:
            value = self.expr(n)
            if not (value.typ.integer or value.typ.real or value.typ in (BOOL, STRING)
                    or value.typ.base == 'null'):
                self.fail('Console model excludes arrays and user object toString', n)
            self.freeze(value, pre)

    def call(self, n):
        owner = txt(n.child_by_field_name('object'))
        name = txt(n.child_by_field_name('name'))
        if owner in ('System.out','System.err') and name == 'printf':
            args=children(n.child_by_field_name('arguments'))
            if not args or args[0].type != 'string_literal':
                self.fail('printf requires a constant supported format',n)
            fmt=self.quoted(txt(args[0]),args[0]);codes=re.findall(r'%(.)',fmt)
            if any(c not in ('s','%','n') for c in codes) or len(re.findall('%',re.sub(r'%(s|%|n)','',fmt))) or codes.count('s') != len(args)-1:
                self.fail('printf projection supports matched %s, %n and %% only',n)
            pre=[]
            for arg in args:self.print_value(arg,pre)
            self.notes.add('Constant printf with matching %s/%n/%% follows the console projection; argument effects retained, output text ignored.')
            return Expr('',VOID,pre)
        if owner in ('System.out', 'System.err') and name in ('print', 'println'):
            if self.lookup('System') is not None or 'System' in self.owner.fields:
                self.fail('A shadowed System identifier is outside the console model', n)
            args = children(n.child_by_field_name('arguments'))
            if len(args) > 1 or (name == 'print' and len(args) != 1):
                self.fail('Unsupported print/println arity', n)
            pre = []
            for arg in args:
                self.print_value(arg, pre)
            self.notes.add(CONSOLE_MODEL)
            return Expr('', VOID, pre)
        return super().call(n)


def translate(sources, spec=None):
    from .routing import classify
    if classify(sources)['loop_count']:
        raise ValueError('Use the existing fixed-goal translator for tasks containing loops')
    spec = spec or {}
    if set(spec) - {'methods'} or any(set(v) - {'requires'} for v in spec.get('methods', {}).values()):
        raise ValueError('Specification generation accepts input requires, not prewritten functional goals')
    return SpecCompiler(sources, spec).compile()
