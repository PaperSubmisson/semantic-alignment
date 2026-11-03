"""Math arithmetic plus explicit Java-width bit operations and narrowing casts."""
from framework.j2silver.model import *
from .types import parse_type


class NumericMixin:
    def narrow_local_update(self,n):
        if n.type!='update_expression':return False
        target=children(n)[0]
        local=self.lookup(txt(target)) if target.type=='identifier' else None
        return bool(local and local[1].base in ('char','byte','short') and not local[1].dims)

    def statement_expr(self,n):
        if self.narrow_local_update(n):return self.expr(n).pre
        return super().statement_expr(n)

    def array_new(self,n):
        typ=parse_type(n.child_by_field_name('type'))
        if typ.base in ('byte','short'):
            from .types import NarrowType
            pre=[];sizes=[]
            for child in children(n):
                if child.type=='dimensions_expr':sizes.append(self.freeze(self.expr(children(child)[0]),pre))
            if len(sizes)!=1:self.fail('Narrow arrays support one initialized dimension',n)
            pre+=self.guard(sizes[0]+' >= 0','NegativeArraySizeException')
            target=NarrowType(typ.base,1)
            return Expr(self.allocate(target,sizes,pre,n),target,pre)
        return super().array_new(n)
    def expr(self, n, expected=None):
        if self.narrow_local_update(n):
            lv=self.lvalue(children(n)[0]);pre=list(lv.pre)
            if lv.value not in self.assigned:self.fail('Update of uninitialized narrow local',n)
            old=self.fresh(lv.typ,pre,lv.value);self.runtime.add('bitops')
            fn='c0_unsigned' if lv.typ.base=='char' else 'c0_signed';bits=8 if lv.typ.base=='byte' else 16
            op='+' if '++' in txt(n) else '-'
            value=self.fresh(lv.typ,pre,f'{fn}({old}{op}1,{bits})');pre.append(lv.store(value))
            return Expr(value if txt(n).startswith(('++','--')) else old,lv.typ,pre)
        if n.type == 'cast_expression':
            target = parse_type(n.child_by_field_name('type'))
            value = self.expr(n.child_by_field_name('value'))
            if target.integer and value.typ.integer:
                if target.base in ('char', 'byte', 'short') or (target.base == 'int' and value.typ.base == 'long'):
                    self.runtime.add('bitops')
                    bits = {'byte': 8, 'short': 16, 'char': 16, 'int': 32}[target.base]
                    fn = 'c0_unsigned' if target.base == 'char' else 'c0_signed'
                    return Expr(f'{fn}({value.value}, {bits})', target, value.pre)
                return Expr(value.value, target, value.pre)
            if target.integer and value.typ.real:
                self.runtime.add('real_cast')
                bits = 64 if target.base == 'long' else 32
                upper = 2**(bits-1)-1; lower = -2**(bits-1)
                x = value.value
                pre = list(value.pre); frozen = self.fresh(REAL, pre, x)
                result = self.fresh(INT, pre)
                pre += [f'if ({frozen} >= {upper}*write) {{ {result} := {upper} }} else {{',
                        f'  if ({frozen} <= {lower}*write) {{ {result} := {lower} }} else {{',
                        f'    if ({frozen} >= none) {{ {result} := c0_floor_m({frozen}, {upper}) }} else {{',
                        f'      {result} := c0_floor_m(-{frozen}, {-lower})', f'      {result} := -{result}', '    }', '  }', '}']
                trunc = result
                if target.base in ('byte', 'short', 'char'):
                    self.runtime.add('bitops')
                    fn = 'c0_unsigned' if target.base == 'char' else 'c0_signed'
                    trunc = f'{fn}({trunc},{8 if target.base == "byte" else 16})'
                self.notes.add('Real-to-integer casts truncate toward zero and clamp to the Java int/long target range; IEEE NaN/Infinity are outside the mathematical-real model.')
                return Expr(trunc, target, pre)
        if n.type == 'unary_expression' and txt(n.child_by_field_name('operator')) == '~':
            a = self.expr(n.child_by_field_name('operand'))
            if not a.typ.integer:
                self.fail('Bit complement needs an integer', n)
            self.runtime.add('bitops')
            bits = 64 if a.typ.base == 'long' else 32
            return Expr(f'c0_signed(-{a.value}-1, {bits})', JType('long') if bits == 64 else INT, a.pre)
        return super().expr(n, expected)

    def operation(self, a, b, op, n):
        if a.typ.integer and b.typ.integer and op in ('&', '|', '^', '<<', '>>', '>>>'):
            self.runtime.add('bitops')
            bits = 64 if (a.typ.base == 'long' or (op in ('&', '|', '^') and b.typ.base == 'long')) else 32
            av, bv = a.value, b.value
            if op in ('&', '|', '^'):
                fn = {'&': 'c0_and', '|': 'c0_or', '^': 'c0_xor'}[op]
                value = f'{fn}(c0_unsigned({av}, {bits}), c0_unsigned({bv}, {bits}), {bits})'
                if op == '&':
                    for mask, operand in ((av,bv),(bv,av)):
                        try: m=int(mask)
                        except ValueError:continue
                        if 0 <= m < 2**(bits-1) and ((m+1)&m)==0:
                            value=f'c0_unsigned({operand}, {(m+1).bit_length()-1})'
                            break
            else:
                # The right distance is masked to 5/6 bits independently of its
                # static integer type. Signed right shift uses floor division.
                distance = f'c0_unsigned({bv}, {5 if bits == 32 else 6})'
                power = f'c0_pow2({distance})'
                if op == '<<':
                    value = f'c0_unsigned({av}, {bits}) * {power}'
                elif op == '>>>':
                    value = f'c0_unsigned({av}, {bits}) / {power}'
                else:
                    signed = f'c0_signed({av}, {bits})'
                    value = f'({signed} >= 0 ? {signed}/{power} : -((-{signed}+{power}-1)/{power}))'
            self.notes.add('Ordinary arithmetic is mathematical; bitwise operations and explicit narrowing use Java 32/64-bit two-complement widths and masked shift distances.')
            return Expr(f'c0_signed({value}, {bits})', JType('long') if bits == 64 else INT, a.pre + b.pre)
        result = super().operation(a, b, op, n)
        if result.typ == INT and (a.typ.base == 'long' or b.typ.base == 'long'):
            result.typ = JType('long')
        return result

    def helpers(self):
        out = super().helpers()
        if 'real_cast' in self.runtime:
            out += ['method c0_floor_step(x: Perm, lo: Int, hi: Int) returns (res: Int)',
                    '  requires 0 <= lo && lo <= hi && lo*write <= x && x < (hi+1)*write',
                    '  ensures lo <= res && res <= hi && res*write <= x && x < (res+1)*write',
                    '  decreases hi-lo',
                    '{ if (lo == hi) { res := lo } else { var mid: Int := (lo+hi+1)/2',
                    '    if (mid*write <= x) { res := c0_floor_step(x,mid,hi) } else { res := c0_floor_step(x,lo,mid-1) } } }',
                    'method c0_floor_m(x: Perm, limit: Int) returns (res: Int)',
                    '  requires 0 <= limit && none <= x && x < (limit+1)*write',
                    '  ensures 0 <= res && res <= limit && res*write <= x && x < (res+1)*write',
                    '  decreases limit+1',
                    '{ res := c0_floor_step(x,0,limit) }']
        if 'bitops' in self.runtime:
            def powers(lo, hi):
                if lo == hi:return str(2**lo)
                mid = (lo+hi)//2
                return f'(n <= {mid} ? {powers(lo,mid)} : {powers(mid+1,hi)})'
            out += ['function c0_pow2(n: Int): Int requires 0 <= n && n <= 64 ensures result > 0 { ' + powers(0,64) + ' }',
                    'function c0_unsigned(x: Int, n: Int): Int requires 0 <= n && n <= 64',
                    '  ensures 0 <= result && result < c0_pow2(n)',
                    '{ ((x % c0_pow2(n)) + c0_pow2(n)) % c0_pow2(n) }',
                    'function c0_signed(x: Int, n: Int): Int requires 0 < n && n <= 64',
                    '{ c0_unsigned(x,n) < c0_pow2(n-1) ? c0_unsigned(x,n) : c0_unsigned(x,n)-c0_pow2(n) }']
            for fn, bit in [('and', '(x%2 == 1 && y%2 == 1 ? 1 : 0)'), ('or', '(x%2 == 1 || y%2 == 1 ? 1 : 0)'), ('xor', '(x%2 != y%2 ? 1 : 0)')]:
                out += [f'function c0_{fn}(x: Int, y: Int, n: Int): Int requires x >= 0 && y >= 0 && n >= 0',
                        f'{{ n == 0 ? 0 : {bit} + 2*c0_{fn}(x/2,y/2,n-1) }}']
        return out
