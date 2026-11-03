from dataclasses import dataclass, field
import re


class TranslationError(Exception):
    def __init__(self, message, node=None, path=None):
        self.message = message
        self.line = node.start_point.row + 1 if node else None
        self.column = node.start_point.column + 1 if node else None
        self.path = str(path) if path else None
        super().__init__(message)

    def as_dict(self):
        return dict(message=self.message, file=self.path, line=self.line, column=self.column)


def txt(node):
    return node.text.decode('utf-8') if node else ''


def children(node):
    return [n for n in node.named_children if n.type not in {'line_comment', 'block_comment'}]


def walk(node):
    yield node
    for c in children(node):
        yield from walk(c)


def safe(name):
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    if name in {'res', 'result', 'old', 'var', 'method', 'function', 'field', 'domain', 'Int', 'Bool',
                'Perm', 'Ref', 'Seq', 'Set', 'Map', 'write', 'none', 'null', 'true', 'false',
                'requires', 'ensures', 'invariant', 'while', 'if', 'else', 'assert', 'inhale',
                'exhale', 'label', 'goto', 'return', 'new', 'fold', 'unfold', 'self'} or name.startswith('_j_'):
        name = '_u_' + name
    return name


@dataclass(frozen=True)
class JType:
    base: str
    dims: int = 0

    @property
    def ref(self): return self.dims > 0 or self.base not in {'int', 'long', 'char', 'boolean', 'double', 'float', 'void'}
    @property
    def real(self): return self.dims == 0 and self.base in {'double', 'float'}
    @property
    def integer(self): return self.dims == 0 and self.base in {'int', 'long', 'char'}
    @property
    def silver(self):
        if self.ref: return 'Ref'
        if self.real: return 'Perm'
        return {'boolean': 'Bool', 'void': 'Void'}.get(self.base, 'Int')
    @property
    def element(self):
        if not self.dims: raise ValueError('not an array')
        return JType(self.base, self.dims - 1)
    @property
    def field(self):
        t = self.element
        if t.ref: return 'arr_ref'
        if t.real: return 'arr_real'
        return {'boolean': 'arr_bool', 'char': 'arr_char'}.get(t.base, 'arr_int')
    @property
    def default(self):
        if self.ref: return 'null'
        if self.real: return 'none'
        return 'false' if self.base == 'boolean' else '0'
    def __str__(self): return self.base + '[]' * self.dims


INT=JType('int'); BOOL=JType('boolean'); REAL=JType('double'); VOID=JType('void'); STRING=JType('String'); NULL=JType('null')

def parse_type(node, extra=0):
    s=re.sub(r'\s+', '', txt(node))
    dims=s.count('[]')+extra
    base=s.replace('[]','').split('.')[-1]
    if base in {'byte','short','var'} or '<' in s or '@' in s:
        raise TranslationError('Unsupported type: '+s,node)
    return JType(base,dims)


@dataclass
class Field:
    name: str
    typ: JType
    owner: str
    static: bool
    final: bool
    value: object = None
    @property
    def silver(self): return safe(self.owner)+'_'+safe(self.name)


@dataclass
class Method:
    owner: str
    name: str
    typ: JType
    parameters: list
    node: object
    static: bool = False
    constructor: bool = False
    overloaded: bool = False
    @property
    def key(self): return self.owner+'.'+self.name+(f'/{len(self.parameters)}' if self.overloaded else '')
    @property
    def silver(self):
        suffix=f'__arity{len(self.parameters)}' if self.overloaded else ''
        return safe(self.owner)+'_'+('init' if self.constructor else safe(self.name))+suffix


@dataclass
class Class:
    name: str
    node: object
    fields: dict = field(default_factory=dict)
    methods: dict = field(default_factory=dict)

    def resolve_method(self, name, arity):
        return next((m for m in self.methods.values()
                     if m.name == name and len(m.parameters) == arity), None)


@dataclass
class Expr:
    value: str
    typ: JType
    pre: list = field(default_factory=list)


@dataclass
class LValue:
    value: str
    typ: JType
    pre: list = field(default_factory=list)
    array: str | None = None
    index: str | None = None
    content: str | None = None
    def store(self, value):
        if self.array:
            return f'{self.array}.{self.content} := {self.array}.{self.content}[{self.index} := {value}]'
        return f'{self.value} := {value}'
