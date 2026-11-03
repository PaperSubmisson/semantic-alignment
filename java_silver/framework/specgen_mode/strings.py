"""Finite String/StringBuilder API models over UTF-16 sequences.

Helpers have bodies; no opaque postcondition axioms replace library behavior.
Locale/case mapping and IEEE string formatting are deliberately not guessed.
"""
from framework.j2silver.model import *
from framework.j2silver.compiler import indent

BUILDER = JType('StringBuilder')


class StringsMixin:
    def constant_value(self, node, seen=None):
        if node is None:return None
        if node.type in ('true','false'):return ('boolean',node.type=='true')
        if node.type=='object_creation_expression' and txt(node.child_by_field_name('type'))=='String':
            args=children(node.child_by_field_name('arguments'))
            if len(args)==1:
                value=self.constant_value(args[0],seen)
                return value if value and value[0]=='string' else None
        if node.type == 'string_literal':return ('string', self.quoted(txt(node),node))
        if node.type == 'character_literal':return ('integer', ord(self.quoted(txt(node),node)))
        if node.type == 'decimal_integer_literal':return ('integer', int(txt(node).rstrip('lL').replace('_','')))
        if node.type == 'unary_expression' and txt(node.child_by_field_name('operator')) == '-':
            value=self.constant_value(node.child_by_field_name('operand'),seen)
            return ('integer',-value[1]) if value and value[0]=='integer' else None
        if node.type == 'identifier':
            name=txt(node);seen=set(seen or ())
            if name in seen:return None
            seen.add(name)
            if any(x.type=='assignment_expression' and txt(x.child_by_field_name('left'))==name or x.type=='update_expression' and txt(children(x)[0])==name for x in walk(self.current.node)):return None
            declarations=[x for x in walk(self.current.node) if x.type=='variable_declarator' and txt(x.child_by_field_name('name'))==name]
            if len(declarations)==1 and declarations[0].end_byte < node.start_byte:
                scope=declarations[0].parent
                while scope and scope.type not in ('block','switch_block'):scope=scope.parent
                if scope and scope.start_byte <= node.start_byte < scope.end_byte:
                    return self.constant_value(declarations[0].child_by_field_name('value'),seen)
        return None

    def constant_search(self,n,name):
        obj=self.constant_value(n.child_by_field_name('object'))
        args=[self.constant_value(a) for a in children(n.child_by_field_name('arguments'))]
        if not obj or obj[0]!='string' or not args or any(a is None for a in args):return None
        def units(s):
            raw=s.encode('utf-16-le','surrogatepass')
            return ''.join(chr(int.from_bytes(raw[i:i+2],'little')) for i in range(0,len(raw),2))
        s=units(obj[1]);needle=args[0]
        if needle[0]=='integer':
            if not 0<=needle[1]<=0x10ffff:return -1
            p=units(chr(needle[1]))
        else:p=units(needle[1])
        i=args[1][1] if len(args)==2 else (len(s) if name=='lastIndexOf' else 0)
        if name=='lastIndexOf':
            i=min(i,len(s)-len(p))
            return -1 if i<0 else s.rfind(p,0,i+len(p)) if p else i
        i=max(i,0)
        return min(i,len(s)) if not p else s.find(p,i)

    def object_new(self, n):
        name = txt(n.child_by_field_name('type')).split('.')[-1]
        if name not in ('String', 'StringBuilder'):
            return super().object_new(n)
        args = [self.expr(a) for a in children(n.child_by_field_name('arguments'))]
        pre = []
        vals = [self.freeze(a, pre) for a in args]
        seq = 'Seq[Int]()'
        if not args:
            capacity = '16'
        elif len(args) == 1 and args[0].typ == STRING:
            pre += [f'assert {vals[0]} != null']
            self.fields['str_chars'] = 'Seq[Int]'
            seq = vals[0] + '.str_chars'
            capacity = f'|{seq}| + 16'
        elif name == 'StringBuilder' and len(args) == 1 and args[0].typ.integer:
            capacity = vals[0]
            pre += [f'assert {capacity} >= 0']
        elif name == 'String' and len(args) in (1, 3) and args[0].typ == JType('char', 1):
            seq = vals[0] + '.' + self.field(args[0].typ)
            pre += [f'assert {vals[0]} != null']
            if len(args) == 3:
                if not all(a.typ.integer for a in args[1:]):
                    self.fail('String slice requires integer offset/count', n)
                off, count = vals[1:]
                pre += [f'assert 0 <= {off} && 0 <= {count} && {off} + {count} <= |{seq}|']
                seq = f'{seq}[{off}..{off}+{count}]'
        else:
            self.fail('Unsupported ' + name + ' constructor signature', n)
        if name == 'String':
            return self.make_string(seq, pre, n)
        self.fields.update(sb_chars='Seq[Int]', sb_capacity='Int')
        v = self.fresh(BUILDER, pre)
        pre += [f'{v} := new(sb_chars, sb_capacity)', f'{v}.sb_chars := {seq}', f'{v}.sb_capacity := {capacity}']
        self.notes.add('StringBuilder models UTF-16 contents and capacity; allocation exhaustion is outside the model.')
        return Expr(v, BUILDER, pre)

    def content(self, a, n):
        if a.typ == STRING:
            self.fields['str_chars'] = 'Seq[Int]'
            return f'({a.value} == null ? Seq(110,117,108,108) : {a.value}.str_chars)'
        if a.typ == NULL:
            return 'Seq(110,117,108,108)'
        if a.typ == BOOL:
            return f'({a.value} ? Seq(116,114,117,101) : Seq(102,97,108,115,101))'
        if a.typ == JType('char'):
            return f'Seq({a.value})'
        if a.typ.integer:
            self.runtime.update(('decimal_string', 'division'))
            return f'c0_decimal({a.value})'
        self.fail('String conversion needs an explicit model for ' + str(a.typ), n)

    def operation(self, a, b, op, n):
        if op in ('==', '!=') and a.typ == b.typ == STRING:
            # An explicit new String is fresh; comparison with its source is
            # valid without a global string-intern pool. Reject other identities.
            locals_by_name = {txt(d.child_by_field_name('name')): d.child_by_field_name('value')
                              for x in walk(self.current.node) if x.type == 'local_variable_declaration'
                              for d in children(x) if d.type == 'variable_declarator'}
            left, right = n.child_by_field_name('left'), n.child_by_field_name('right')
            def fresh_from(x, other):
                init = locals_by_name.get(txt(x)) if x.type == 'identifier' else x
                if init is None or init.type != 'object_creation_expression' or txt(init.child_by_field_name('type')) != 'String':
                    return False
                args = children(init.child_by_field_name('arguments'))
                if len(args) != 1 or txt(args[0]) != txt(other):
                    return False
                return not any(z.type == 'assignment_expression' and txt(z.child_by_field_name('left')) in (txt(x), txt(other)) for z in walk(self.current.node))
            if fresh_from(left, right) or fresh_from(right, left):
                return Expr(f'({a.value} {op} {b.value})', BOOL, a.pre+b.pre)
            self.fail('String identity requires an intern-pool model outside explicit fresh/source comparisons', n)
        if op == '+' and (a.typ == STRING or b.typ == STRING):
            if a.typ == STRING and b.typ == JType('char'):
                return super().operation(a, b, op, n)
            return self.make_string(self.content(a, n) + ' ++ ' + self.content(b, n), a.pre + b.pre, n)
        return super().operation(a, b, op, n)

    def call(self, n):
        owner = txt(n.child_by_field_name('object'))
        name = txt(n.child_by_field_name('name'))
        nodes = children(n.child_by_field_name('arguments'))
        if owner == 'String' and name in ('valueOf', 'copyValueOf'):
            args = [self.expr(a) for a in nodes]
            pre = []; vals = [self.freeze(a, pre) for a in args]
            args = [Expr(v, a.typ) for v, a in zip(vals, args)]
            if len(args) in (1, 3) and args[0].typ == JType('char', 1):
                s = vals[0] + '.' + self.field(args[0].typ)
                pre += [f'assert {vals[0]} != null']
                if len(args) == 3:
                    off, count = vals[1:]
                    pre += [f'assert 0 <= {off} && 0 <= {count} && {off}+{count} <= |{s}|']
                    s = f'{s}[{off}..{off}+{count}]'
                return self.make_string(s, pre, n)
            if len(args) == 1 and name == 'valueOf':
                return self.make_string(self.content(args[0], n), pre, n)
            self.fail('Unsupported String static conversion signature', n)
        obj = n.child_by_field_name('object')
        # Resolve the receiver once. Delegate non-builder expressions without
        # generating duplicate effects or consuming fresh temporaries.
        if obj is not None and obj.type == 'identifier':
            local = self.lookup(owner)
            if local and local[1] == BUILDER:
                pre = []
                recv = self.freeze(self.expr(obj), pre)
                args = [self.expr(a) for a in nodes]
                vals = [self.freeze(a, pre) for a in args]
                return self.builder_call(recv, name, [Expr(v, a.typ) for v, a in zip(vals, args)], pre, n)
        return super().call(n)

    def string_call(self, recv, name, args, pre, n):
        s = recv.value + '.str_chars'
        self.fields['str_chars'] = 'Seq[Int]'
        pre = list(pre)
        if name in ('substring', 'subSequence') and len(args) in (1, 2) and all(a.typ.integer for a in args):
            begin = args[0].value
            end = args[1].value if len(args) == 2 else f'|{s}|'
            pre += [f'assert {recv.value} != null', f'assert 0 <= {begin} && {begin} <= {end} && {end} <= |{s}|']
            return self.make_string(f'{s}[{begin}..{end}]', pre, n)
        if name == 'concat' and len(args) == 1 and args[0].typ == STRING:
            pre += [f'assert {recv.value} != null && {args[0].value} != null']
            return self.make_string(f'{s} ++ {args[0].value}.str_chars', pre, n)
        if name == 'toString' and not args:
            pre += [f'assert {recv.value} != null']
            return Expr(recv.value, STRING, pre)
        if name == 'replace' and len(args) == 2 and all(a.typ == JType('char') for a in args):
            pre += [f'assert {recv.value} != null']
            self.runtime.add('replace_string')
            return self.make_string(f'c0_replace({s}, {args[0].value}, {args[1].value})', pre, n)
        if name in ('contains', 'indexOf', 'lastIndexOf') and len(args) in (1, 2):
            constant=self.constant_search(n,name)
            if constant is not None:
                self.notes.add('Closed constant String search calls folded over exact UTF-16 values; arguments/effects still evaluated, symbolic calls retain general definitions.')
                return Expr(('true' if constant>=0 else 'false') if name=='contains' else str(constant),BOOL if name=='contains' else INT,pre)
            if args[0].typ == STRING:
                p = args[0].value + '.str_chars'
                pre += [f'assert {args[0].value} != null']
            elif args[0].typ.integer:
                # String.indexOf(int) accepts a Unicode code point, including
                # supplementary characters encoded as a UTF-16 surrogate pair.
                self.runtime.add('codepoint')
                p = f'c0_codepoint({args[0].value})'
            else:
                self.fail('Unsupported String search signature', n)
            pre += [f'assert {recv.value} != null']
            self.runtime.add('search_string')
            if name == 'contains':
                if len(args) != 1 or args[0].typ != STRING:
                    self.fail('contains expects String', n)
                v = self.fresh(INT, pre)
                pre += [f'{v} := c0_index_m({s}, {p}, 0)']
                return Expr(v + ' >= 0', BOOL, pre)
            off = args[1].value if len(args) == 2 else (f'|{s}|' if name == 'lastIndexOf' else '0')
            fn = 'c0_last_m' if name == 'lastIndexOf' else 'c0_index_m'
            value = self.fresh(INT, pre)
            if args[0].typ.integer:
                pre += [f'if (0 <= {args[0].value} && {args[0].value} <= 1114111) {{',
                        f'  {value} := {fn}({s}, {p}, {off})', f'}} else {{ {value} := -1 }}']
            else:
                pre += [f'{value} := {fn}({s}, {p}, {off})']
            return Expr(value, INT, pre)
        if name == 'compareTo' and len(args) == 1 and args[0].typ == STRING:
            pre += [f'assert {recv.value} != null && {args[0].value} != null']
            self.runtime.add('compare_string')
            return Expr(f'c0_compare({s}, {args[0].value}.str_chars)', INT, pre)
        if name == 'regionMatches' and len(args) == 4 and args[1].typ == STRING:
            a, p, b, count = [x.value for x in args]
            pre += [f'assert {recv.value} != null && {p} != null']
            t = p + '.str_chars'
            # Java accepts negative len if both offset/remaining checks pass.
            return Expr(f'(0 <= {a} && 0 <= {b} && {a} <= |{s}| - {count} && {b} <= |{t}| - {count} && ({count} <= 0 || {s}[{a}..{a}+{count}] == {t}[{b}..{b}+{count}]))', BOOL, pre)
        return super().string_call(recv, name, args, pre, n)

    def builder_call(self, recv, name, args, pre, n):
        self.fields.update(sb_chars='Seq[Int]', sb_capacity='Int')
        s, cap = recv + '.sb_chars', recv + '.sb_capacity'
        pre += [f'assert {recv} != null']
        if name in ('length', 'capacity') and not args:
            return Expr(f'|{s}|' if name == 'length' else cap, INT, pre)
        if name == 'toString' and not args:
            return self.make_string(s, pre, n)
        if name == 'charAt' and len(args) == 1 and args[0].typ.integer:
            return Expr(f'{s}[{args[0].value}]', JType('char'), pre)
        if name == 'setCharAt' and len(args) == 2 and args[0].typ.integer and args[1].typ == JType('char'):
            i, ch = [a.value for a in args]
            pre += [f'assert 0 <= {i} && {i} < |{s}|', f'{s} := {s}[{i} := {ch}]']
            return Expr('', VOID, pre)
        if name == 'ensureCapacity' and len(args) == 1 and args[0].typ.integer:
            need = args[0].value
            pre += [f'if ({need} > {cap}) {{', f'  {cap} := (2*{cap}+2 >= {need} ? 2*{cap}+2 : {need})', '}']
            return Expr('', VOID, pre)
        if name == 'setLength' and len(args) == 1 and args[0].typ.integer:
            need = args[0].value
            self.runtime.add('zeros_string')
            pre += [f'assert {need} >= 0', f'if ({need} > {cap}) {{', f'  {cap} := (2*{cap}+2 >= {need} ? 2*{cap}+2 : {need})', '}',
                    f'{s} := ({need} <= |{s}| ? {s}[..{need}] : {s} ++ c0_zeros({need} - |{s}|))']
            return Expr('', VOID, pre)
        if name == 'reverse' and not args:
            self.runtime.add('reverse_string')
            pre += [f'{s} := c0_fixpairs(c0_reverse({s}))']
            return Expr(recv, BUILDER, pre)
        if name == 'append' and len(args) == 1:
            a = args[0]
            if a.typ == JType('char', 1):
                pre += [f'assert {a.value} != null']
                value = a.value + '.' + self.field(a.typ)
            else:
                value = self.content(a, n)
            snap = f'_c0_append{self.counter}'; self.counter += 1
            pre += [f'var {snap}: Seq[Int]', f'{snap} := {value}', f'if (|{s}| + |{snap}| > {cap}) {{',
                    f'  {cap} := (2*{cap}+2 >= |{s}|+|{snap}| ? 2*{cap}+2 : |{s}|+|{snap}|)', '}', f'{s} := {s} ++ {snap}']
            return Expr(recv, BUILDER, pre)
        self.fail('Unsupported StringBuilder method ' + name, n)

    def helpers(self):
        out = super().helpers()
        if 'decimal_string' in self.runtime:
            out += ['function c0_natdecimal(x: Int): Seq[Int] requires x >= 0',
                    '{ x < 10 ? Seq(48+x) : c0_natdecimal(x/10) ++ Seq(48+x%10) }',
                    'function c0_decimal(x: Int): Seq[Int]',
                    '{ x < 0 ? Seq(45) ++ c0_natdecimal(-x) : c0_natdecimal(x) }']
        if 'codepoint' in self.runtime:
            out += ['function c0_codepoint(x: Int): Seq[Int]',
                    '{ x < 65536 ? Seq(x) : Seq(55296+(x-65536)/1024, 56320+(x-65536)%1024) }']
        if 'search_string' in self.runtime:
            # These are implemented library routines, not trusted property axioms.
            # Their internal invariants are verified with every candidate.
            out += '''function c0_match(s: Seq[Int], p: Seq[Int], i: Int): Bool
{ 0 <= i && i+|p| <= |s| && s[i..i+|p|] == p }
method c0_index_m(s: Seq[Int], p: Seq[Int], i: Int) returns (res: Int)
  ensures |p| == 0 ==> res == (i < 0 ? 0 : (i <= |s| ? i : |s|))
  ensures |p| > 0 ==> (res == -1 || (i <= res && c0_match(s,p,res)))
  ensures |p| > 0 ==> (forall j: Int :: {c0_match(s,p,j)} 0 <= j && i <= j && j+|p| <= |s| && (res == -1 || j < res) ==> !c0_match(s,p,j))
{
  if (|p| == 0) { res := (i < 0 ? 0 : (i <= |s| ? i : |s|)) }
  else {
    var k: Int := (i < 0 ? 0 : i)
    while (k+|p| <= |s| && !c0_match(s,p,k))
      invariant 0 <= k && i <= k
      invariant forall j: Int :: {c0_match(s,p,j)} 0 <= j && i <= j && j < k && j+|p| <= |s| ==> !c0_match(s,p,j)
    { k := k+1 }
    res := (k+|p| <= |s| ? k : -1)
  }
}
method c0_last_m(s: Seq[Int], p: Seq[Int], i: Int) returns (res: Int)
  ensures res == -1 || (res <= i && c0_match(s,p,res))
  ensures forall j: Int :: {c0_match(s,p,j)} 0 <= j && j <= i && j+|p| <= |s| && res < j ==> !c0_match(s,p,j)
{
  var k: Int := (i <= |s|-|p| ? i : |s|-|p|)
  while (0 <= k && !c0_match(s,p,k))
    invariant k <= i && k+|p| <= |s|
    invariant forall j: Int :: {c0_match(s,p,j)} 0 <= j && j <= i && j+|p| <= |s| && k < j ==> !c0_match(s,p,j)
  { k := k-1 }
  res := (k < 0 ? -1 : k)
}'''.splitlines()
        if 'compare_string' in self.runtime:
            out += ['function c0_compare(s: Seq[Int], t: Seq[Int]): Int',
                    '{ |s| == 0 || |t| == 0 ? |s|-|t| : (s[0] != t[0] ? s[0]-t[0] : c0_compare(s[1..],t[1..])) }']
        if 'zeros_string' in self.runtime:
            out += ['function c0_zeros(n: Int): Seq[Int] requires n >= 0', '{ n == 0 ? Seq[Int]() : Seq(0) ++ c0_zeros(n-1) }']
        if 'replace_string' in self.runtime:
            out += ['function c0_replace(s: Seq[Int], a: Int, b: Int): Seq[Int]', '{ |s| == 0 ? s : Seq(s[0] == a ? b : s[0]) ++ c0_replace(s[1..],a,b) }']
        if 'reverse_string' in self.runtime:
            out += ['function c0_reverse(s: Seq[Int]): Seq[Int] { |s| == 0 ? s : c0_reverse(s[1..]) ++ Seq(s[0]) }',
                    'function c0_fixpairs(s: Seq[Int]): Seq[Int]',
                    '{ |s| < 2 ? s : (56320 <= s[0] && s[0] <= 57343 && 55296 <= s[1] && s[1] <= 56319 ? Seq(s[1],s[0]) ++ c0_fixpairs(s[2..]) : Seq(s[0]) ++ c0_fixpairs(s[1..])) }']
        measures = {'c0_natdecimal': 'x', 'c0_index': '(|s|-i < 0 ? 0 : |s|-i)+1',
                    'c0_last': '(i < 0 ? 0 : i+1)', 'c0_compare': '|s|', 'c0_zeros': 'n',
                    'c0_replace': '|s|', 'c0_reverse': '|s|', 'c0_fixpairs': '|s|',
                    'c0_pow2': 'n', 'c0_and': 'n', 'c0_or': 'n', 'c0_xor': 'n', 'c0_floor': 'hi-lo'}
        result = []; pending = None
        for line in out:
            if line.startswith('function '):
                name = line.split('(')[0].split()[1]
                if name in measures:
                    if ' {' in line:
                        head, body = line.split(' {', 1)
                        result += [head, '  decreases ' + measures[name], '{' + body]
                    else:
                        result.append(line); pending = measures[name]
                    continue
            if pending and line.startswith('{'):
                result.append('  decreases ' + pending); pending = None
            result.append(line)
        return result
