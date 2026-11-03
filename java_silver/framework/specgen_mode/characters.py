"""Exact finite char-domain JDK tables, emitted as executable decision trees."""
import json
from pathlib import Path
from framework.j2silver.model import *


class CharactersMixin:
    def convert(self, e, target, n):
        if target == JType('char') and e.typ.integer:
            try:
                value = int(e.value)
            except ValueError:
                pass
            else:
                if 0 <= value <= 65535:
                    return e.value
        return super().convert(e, target, n)

    def call(self, n):
        owner = txt(n.child_by_field_name('object')); name = txt(n.child_by_field_name('name'))
        if owner == 'Character':
            table = json.loads(Path(__file__).with_name('character-table.json').read_text())
            nodes = children(n.child_by_field_name('arguments'))
            if name in table['tables'] and len(nodes) == 1:
                e = self.expr(nodes[0])
                if e.typ != JType('char'):
                    self.fail('Character adapter currently accepts char overloads only', n)
                constant=self.constant_value(nodes[0])
                if constant and constant[0]=='integer' and 0 <= constant[1] <= 65535:
                    c=constant[1];value=False if not name.startswith('to') else c
                    for lo,hi,delta in table['tables'][name]:
                        if lo <= c <= hi:
                            value=True if not name.startswith('to') else c+delta
                            break
                    return Expr(str(value).lower(), JType('char') if name.startswith('to') else BOOL,e.pre)
                self.runtime.add('character_' + name)
                self.notes.add('Character char-overload tables exhaustively extracted from JDK ' + table['jdk'] + '; locale-independent finite UTF-16 code-unit mapping.')
                typ = JType('char') if name.startswith('to') else BOOL
                return Expr(f'c0_char_{name}({e.value})', typ, e.pre)
        return super().call(n)

    def helpers(self):
        out = super().helpers()
        names = [x[len('character_'):] for x in self.runtime if x.startswith('character_')]
        if not names:
            return out
        tables = json.loads(Path(__file__).with_name('character-table.json').read_text())['tables']
        for name in sorted(names):
            boolean = not name.startswith('to')
            default = 'false' if boolean else 'c'
            def tree(rows):
                if not rows:return default
                i=len(rows)//2;lo,hi,value=rows[i]
                item='true' if boolean else f'c+({value})'
                return f'(c < {lo} ? {tree(rows[:i])} : (c <= {hi} ? {item} : {tree(rows[i+1:])}))'
            out += [f'function c0_char_{name}(c: Int): {"Bool" if boolean else "Int"}', '{ ' + tree(tables[name]) + ' }']
        return out
