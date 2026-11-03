"""Small implemented libraries and conservative, explicitly recorded pure abstractions.

Opaque domain functions have NO axioms: all proofs must hold for every
interpretation, rather than assuming the desired library result.
"""
from fractions import Fraction
from framework.j2silver.model import *


class LibraryMixin:
    def receiver_type(self,n):
        obj=n.child_by_field_name('object')
        if not obj:return None
        if obj.type=='identifier':
            local=self.lookup(txt(obj))
            if local:return local[1]
            field=self.owner.fields.get(txt(obj))
            if field:return field.typ
        if obj.type=='field_access' and txt(obj.child_by_field_name('object'))=='this':
            field=self.owner.fields.get(txt(obj.child_by_field_name('field')))
            if field:return field.typ
        if obj.type=='object_creation_expression':return JType(txt(obj.child_by_field_name('type')))
        return None

    def expr(self,n,expected=None):
        if n.type=='field_access' and txt(n) in ('System.out','System.err','System.in'):
            pre=[];v=self.fresh(JType('C0Stream'),pre)
            self.notes.add('Standard streams are modeled as existing opaque handles. Output bytes are ignored; stream identity and reconfiguration are unsupported.')
            return Expr(v,JType('C0Stream'),pre+[v+' := new()'])
        return super().expr(n,expected)

    def object_new(self,n):
        name=txt(n.child_by_field_name('type'))
        nodes=children(n.child_by_field_name('arguments'))
        if name=='StringBuffer' and len(nodes)==1:
            e=self.expr(nodes[0]);pre=[];r=self.freeze(e,pre)
            if e.typ!=STRING:self.fail('StringBuffer adapter requires String',n)
            pre+=self.guard(r+' != null','NullPointerException')
            self.fields.update(sb_chars='Seq[Int]',sb_capacity='Int')
            v=self.fresh(JType('StringBuffer'),pre)
            pre += [v+' := new(sb_chars,sb_capacity)',v+'.sb_chars := '+r+'.str_chars',v+'.sb_capacity := |'+r+'.str_chars|+16']
            self.notes.add('StringBuffer(String) owns a fresh UTF-16 content/capacity buffer. Synchronization is irrelevant to this single-threaded task; unsupported buffer operations are rejected.')
            return Expr(v,JType('StringBuffer'),pre)
        if name=='Scanner' and len(nodes)==1:
            e=self.expr(nodes[0]);pre=[];v=self.freeze(e,pre)
            if e.typ!=STRING:self.fail('Scanner adapter supports a fixed String input',n)
            pre+=self.guard(v+' != null','NullPointerException')
            self.fields.update(c0_scan_text='Seq[Int]',c0_scan_pos='Int')
            scanner=self.fresh(JType('Scanner'),pre)
            pre += [scanner+' := new(c0_scan_text,c0_scan_pos)',scanner+'.c0_scan_text := '+v+'.str_chars',scanner+'.c0_scan_pos := 0']
            return Expr(scanner,JType('Scanner'),pre)
        if name.startswith('HashMap') and not nodes:
            self.fields.update(c0_map_keys='Seq[Seq[Int]]',c0_map_values='Seq[Ref]')
            pre=[];v=self.fresh(JType('C0Map'),pre)
            pre += [v+' := new(c0_map_keys,c0_map_values)',v+'.c0_map_keys := Seq[Seq[Int]]()',v+'.c0_map_values := Seq[Ref]()']
            return Expr(v,JType('C0Map'),pre)
        if name=='PrintWriter':
            pre=[]
            for x in nodes:self.freeze(self.expr(x),pre)
            v=self.fresh(JType('PrintWriter'),pre)
            self.notes.add('Returned writer is an opaque output handle. Anonymous callback bodies are not invoked by this task; future writer method calls are not supported by this adapter.')
            return Expr(v,JType('PrintWriter'),pre+[v+' := new()'])
        if name=='String' and len(nodes)==1:
            # Only choose this branch for a known byte[] argument.
            local=self.lookup(txt(nodes[0])) if nodes[0].type=='identifier' else None
            if local and local[1].base=='byte' and local[1].dims==1:
                a=self.expr(nodes[0]);pre=[];v=self.freeze(a,pre)
                self.opaque('c0_decode_default',['Seq[Int]'],'Seq[Int]')
                pre+=self.guard(v+' != null','NullPointerException')
                return self.make_string(f'c0_decode_default({v}.arr_int)',pre,n)
        return super().object_new(n)

    def opaque(self,name,params,result):
        self.opaque_functions[name]=(params,result)
        self.notes.add('Conservative pure-library abstraction: '+name+' is deterministic on its explicit arguments, with no result/property axioms. Library internals are not claimed verified.')
        return name

    def helpers(self):
        out=super().helpers()
        if self.opaque_functions:
            out+=['domain C0LibraryValues {']+[f'  function {name}('+', '.join(f'a{i}: {t}' for i,t in enumerate(params))+f'): {result}' for name,(params,result) in sorted(self.opaque_functions.items())]+['}']
        if 'sort_library' in self.runtime:
            out+='''function c0_sorted_insert(s: Seq[Int], x: Int): Seq[Int]
  ensures |result| == |s|+1
  decreases |s|
{ |s| == 0 ? Seq(x) : (x <= s[0] ? Seq(x) ++ s : Seq(s[0]) ++ c0_sorted_insert(s[1..],x)) }
function c0_sorted_sequence(s: Seq[Int]): Seq[Int]
  ensures |result| == |s|
  decreases |s|
{ |s| == 0 ? s : c0_sorted_insert(c0_sorted_sequence(s[1..]),s[0]) }'''.splitlines()
        if 'round_real' in self.runtime:
            out+='''method c0_round_real(x: Perm, lo: Int, hi: Int) returns (res: Int)
  requires lo < 0 && 0 < hi
  ensures lo <= res && res <= hi
  ensures x < lo*write ==> res == lo
  ensures hi*write <= x ==> res == hi
  ensures lo*write <= x && x < hi*write ==> res*write <= x + write/2 && x + write/2 < (res+1)*write
{
  if (x < lo*write) { res := lo } else {
    if (hi*write <= x) { res := hi } else {
      var r: Int
      r := c0_floor_m(x-lo*write + write/2, hi-lo)
      res := r+lo
    }
  }
}'''.splitlines()
        if 'map_library' in self.runtime:
            out+='''function c0_map_lookup(keys: Seq[Seq[Int]], values: Seq[Ref], key: Seq[Int]): Ref
  requires |keys| == |values|
  decreases |keys|
{ |keys| == 0 ? null : (keys[0] == key ? values[0] : c0_map_lookup(keys[1..],values[1..],key)) }
function c0_key_remove(keys: Seq[Seq[Int]], key: Seq[Int]): Seq[Seq[Int]]
  ensures |result| <= |keys|
  decreases |keys|
{ |keys| == 0 ? keys : (keys[0] == key ? c0_key_remove(keys[1..],key) : Seq(keys[0]) ++ c0_key_remove(keys[1..],key)) }
function c0_unique_keys(keys: Seq[Seq[Int]]): Seq[Seq[Int]]
  ensures |result| <= |keys|
  decreases |keys|
{ |keys| == 0 ? keys : Seq(keys[0]) ++ c0_unique_keys(c0_key_remove(keys[1..],keys[0])) }'''.splitlines()
        return out

    def call(self,n):
        owner=txt(n.child_by_field_name('object'));name=txt(n.child_by_field_name('name'))
        nodes=children(n.child_by_field_name('arguments'))
        if owner=='Runtime.getRuntime()' and name=='halt':
            pre=[]
            for x in nodes:self.freeze(self.expr(x),pre)
            self.notes.add('Source Runtime.halt terminates execution; this source-level termination edge has no normal successor (inhale false), not a generated proof assumption.')
            return Expr('',VOID,pre+['inhale false'])
        if owner=='System.in' and name=='available' and not nodes:
            ok=self.nondet(BOOL,[]);pre=list(ok.pre)
            pre+=self.guard(ok.value,'IOException')
            value=self.nondet(INT,pre);value.pre+=['inhale 0 <= '+value.value+' && '+value.value+' <= 2147483647']
            return value
        receiver=self.receiver_type(n)
        if owner=='Integer' and name=='parseInt' and len(nodes)==1:
            e=self.expr(nodes[0]);pre=[];v=self.freeze(e,pre)
            if e.typ not in (STRING,NULL):self.fail('parseInt requires String',n)
            self.opaque('c0_parse_int_valid',['Seq[Int]'],'Bool')
            self.opaque('c0_parse_int',['Seq[Int]'],'Int')
            pre+=self.guard(f'{v} != null && c0_parse_int_valid({v}.str_chars)','NumberFormatException')
            return Expr(f'c0_parse_int({v}.str_chars)',INT,pre)
        if receiver and receiver.base=='Scanner' and not nodes and name in ('nextInt','next'):
            pre=[];v=self.freeze(self.expr(n.child_by_field_name('object')),pre)
            pre+=self.guard(v+' != null','NullPointerException')
            s=v+'.c0_scan_text';i=v+'.c0_scan_pos'
            if name=='nextInt':
                self.opaque('c0_scan_int_valid',['Seq[Int]','Int'],'Bool')
                self.opaque('c0_scan_int',['Seq[Int]','Int'],'Int')
                pre+=self.guard(f'c0_scan_int_valid({s},{i})','InputMismatchException')
                r=self.fresh(INT,pre,f'c0_scan_int({s},{i})')
                pre+=[i+' := '+i+'+1']
                return Expr(r,INT,pre)
            self.opaque('c0_scan_has_token',['Seq[Int]','Int'],'Bool')
            self.opaque('c0_scan_token',['Seq[Int]','Int'],'Seq[Int]')
            pre+=self.guard(f'c0_scan_has_token({s},{i}) && |c0_scan_token({s},{i})| > 0','NoSuchElementException')
            e=self.make_string(f'c0_scan_token({s},{i})',pre,n)
            e.pre+=[i+' := '+i+'+1']
            return e
        if owner=='Character' and name in ('forDigit','digit') and len(nodes)==2:
            pre=[];args=[self.expr(x) for x in nodes];v=[self.freeze(x,pre) for x in args]
            if name=='digit':
                self.opaque('c0_digit_value',['Int','Int'],'Int')
                return Expr(f'c0_digit_value({v[0]},{v[1]})',INT,pre)
            d,r=v
            return Expr(f'(2 <= {r} && {r} <= 36 && 0 <= {d} && {d} < {r} ? ({d} < 10 ? 48+{d} : 87+{d}) : 0)',JType('char'),pre)
        if receiver and receiver.base=='C0Map':
            self.runtime.add('map_library')
            self.fields.update(c0_map_keys='Seq[Seq[Int]]',c0_map_values='Seq[Ref]',str_chars='Seq[Int]')
            pre=[];obj=self.freeze(self.expr(n.child_by_field_name('object')),pre)
            args=[self.expr(a) for a in nodes];vs=[self.freeze(a,pre) for a in args]
            pre+=self.guard(obj+' != null','NullPointerException')
            keys=obj+'.c0_map_keys';values=obj+'.c0_map_values'
            if name in ('get','put') and len(vs)==(1 if name=='get' else 2) and args[0].typ in (STRING,NULL):
                key=f'({vs[0]} == null ? Seq(-1) : Seq(0) ++ {vs[0]}.str_chars)'
                v=self.fresh(JType('Object'),pre,f'c0_map_lookup({keys},{values},{key})')
                if name=='put':pre += [f'{keys} := Seq({key}) ++ {keys}',f'{values} := Seq({vs[1]}) ++ {values}']
                return Expr(v,JType('Object'),pre)
            if name=='keySet' and not vs:
                self.fields['c0_key_snapshot']='Seq[Seq[Int]]'
                v=self.fresh(JType('C0Keys'),pre)
                pre += [v+' := new(c0_key_snapshot)',v+'.c0_key_snapshot := c0_unique_keys('+keys+')']
                self.notes.add('Map keys are content-based, null uses a separate key tag. Enumeration exposure records current unique keys; future iteration/order/concurrent modification is outside this task API.')
                return Expr(v,JType('C0Keys'),pre)
            self.fail('Map operation outside the selected get/put/keySet subset',n)
        if owner=='Collections' and name=='singleton' and len(nodes)==1:
            e=self.expr(nodes[0]);pre=[];r=self.freeze(e,pre)
            if e.typ not in (STRING,NULL):self.fail('singleton adapter supports String or null',n)
            self.fields.update(c0_key_snapshot='Seq[Seq[Int]]',str_chars='Seq[Int]')
            v=self.fresh(JType('C0Keys'),pre)
            key=f'({r} == null ? Seq(-1) : Seq(0) ++ {r}.str_chars)'
            pre += [v+' := new(c0_key_snapshot)',v+'.c0_key_snapshot := Seq('+key+')']
            self.notes.add('Collections.singleton(String) has one content-key snapshot (including null). Element iteration and String identity are outside this snapshot-only API.')
            return Expr(v,JType('C0Keys'),pre)
        if owner=='Collections' and name=='enumeration' and len(nodes)==1:
            e=self.expr(nodes[0]);pre=[];r=self.freeze(e,pre)
            if e.typ!=JType('C0Keys'):self.fail('enumeration requires supported key snapshot',n)
            v=self.fresh(JType('C0Enumeration'),pre)
            pre += [v+' := new(c0_key_snapshot)',v+'.c0_key_snapshot := '+r+'.c0_key_snapshot']
            return Expr(v,JType('C0Enumeration'),pre)
        if receiver and receiver.base=='Random':
            if name in ('nextLong','nextFloat','nextDouble','nextBytes'):
                obj=self.expr(n.child_by_field_name('object'));pre=[];r=self.freeze(obj,pre)
                pre+=self.guard(r+' != null','NullPointerException')
                if name=='nextBytes' and len(nodes)==1:
                    a=self.expr(nodes[0]);v=self.freeze(a,pre)
                    if a.typ.base!='byte' or a.typ.dims!=1:self.fail('nextBytes requires byte[]',n)
                    pre+=self.guard(v+' != null','NullPointerException')
                    seq=f'_c0_bytes{self.counter}';self.counter+=1
                    pre += [f'var {seq}: Seq[Int]',f'inhale |{seq}| == |{v}.arr_int|',
                            f'inhale forall j: Int :: 0 <= j && j < |{seq}| ==> -128 <= {seq}[j] && {seq}[j] <= 127',f'{v}.arr_int := {seq}']
                    self.notes.add('Random byte filling is an explicit source environment transition: arbitrary byte values, same array length.')
                    return Expr('',VOID,pre)
                if nodes:self.fail('Random adapter arity',n)
                t=JType('long') if name=='nextLong' else JType('float') if name=='nextFloat' else REAL
                value=self.nondet(t,pre)
                if t.real:value.pre+=['inhale none <= '+value.value+' && '+value.value+' < write']
                return value
        if owner in ('Math','java.lang.Math') and name in ('sqrt','sin','cos','round','toRadians'):
            if len(nodes)!=1:self.fail('Math library arity',n)
            a=self.expr(nodes[0]);pre=[];value=self.freeze(a,pre)
            x=self.convert(Expr(value,a.typ),REAL,n)
            if name=='round':
                self.runtime.update(('real_cast','round_real'))
                bits=32 if a.typ.base=='float' else 64
                r=self.fresh(INT,pre)
                pre+=[f'{r} := c0_round_real({x},{-2**(bits-1)},{2**(bits-1)-1})']
                return Expr(r,INT if bits==32 else JType('long'),pre)
            if name=='toRadians':
                # Mathematical arithmetic, a fixed finite approximation to pi.
                self.notes.add('Angle conversion uses exact arithmetic with pi represented by 3141592653589793/10^15; IEEE rounding is outside this model.')
                return Expr(f'({x} * (3141592653589793 * write / 180000000000000000))',REAL,pre)
            if name=='sqrt':pre+=['assert '+x+' >= none']
            fn=self.opaque('c0_math_'+name,['Perm'],'Perm')
            return Expr(f'{fn}({x})',REAL,pre)
        if owner=='Arrays' and name=='sort' and len(nodes)==1:
            a=self.expr(nodes[0]);pre=[];v=self.freeze(a,pre)
            if a.typ!=JType('int',1):self.fail('sort adapter currently supports int[]',n)
            self.runtime.add('sort_library');self.field(a.typ)
            pre+=[f'assert {v} != null',f'{v}.arr_int := c0_sorted_sequence({v}.arr_int)']
            self.notes.add('Array sort is implemented by defined recursive sequence insertion sort; no sortedness/permutation property is axiomatized.')
            return Expr('',VOID,pre)
        if owner=='java.net.URLDecoder' and name=='decode' and len(nodes)==2:
            if self.constant_value(nodes[1])!=('string','UTF-8'):self.fail('URL decode adapter supports fixed UTF-8 only',n)
            args=[self.expr(x) for x in nodes];pre=[];v=[self.freeze(a,pre) for a in args]
            self.opaque('c0_url_valid',['Seq[Int]'],'Bool')
            self.opaque('c0_url_decode',['Seq[Int]'],'Seq[Int]')
            pre+=self.guard(v[0]+' != null','NullPointerException')
            pre+=self.guard(f'c0_url_valid({v[0]}.str_chars)','IllegalArgumentException')
            return self.make_string(f'c0_url_decode({v[0]}.str_chars)',pre,n)
        return super().call(n)

    def string_call(self,recv,name,args,pre,n):
        if name in ('matches','equalsIgnoreCase','regionMatches'):self.fields['str_chars']='Seq[Int]'
        if name=='compareTo' and len(args)==1:
            left=self.constant_value(n.child_by_field_name('object'))
            right=self.constant_value(children(n.child_by_field_name('arguments'))[0])
            if left and right and left[0]==right[0]=='string':
                def units(s):
                    raw=s.encode('utf-16-le','surrogatepass')
                    return [int.from_bytes(raw[i:i+2],'little') for i in range(0,len(raw),2)]
                s,t=units(left[1]),units(right[1])
                result=next((a-b for a,b in zip(s,t) if a!=b),len(s)-len(t))
                return Expr(str(result),INT,pre)
        if name=='equalsIgnoreCase' and len(args)==1 and args[0].typ in (STRING,NULL):
            p=list(pre)+self.guard(recv.value+' != null','NullPointerException')
            if args[0].typ==NULL:return Expr('false',BOOL,p)
            left=self.constant_value(n.child_by_field_name('object'));right=self.constant_value(children(n.child_by_field_name('arguments'))[0])
            if left and right and left[0]==right[0]=='string' and (left[1]+right[1]).isascii():
                return Expr(str(left[1].lower()==right[1].lower()).lower(),BOOL,p)
            self.opaque('c0_equal_ignore_case',['Seq[Int]','Seq[Int]'],'Bool')
            return Expr(f'({args[0].value} != null && c0_equal_ignore_case({recv.value}.str_chars,{args[0].value}.str_chars))',BOOL,p)
        if name=='regionMatches' and len(args)==5 and args[0].typ==BOOL:
            flag,a,other,b,count=[x.value for x in args];s=recv.value+'.str_chars';t=other+'.str_chars'
            p=list(pre)+self.guard(recv.value+' != null && '+other+' != null','NullPointerException')
            constants=[self.constant_value(n.child_by_field_name('object'))]+[self.constant_value(x) for x in children(n.child_by_field_name('arguments'))]
            if all(x is not None for x in constants):
                sv,ignore,av,tv,bv,nv=[x[1] for x in constants]
                if isinstance(sv,str) and isinstance(tv,str) and (sv+tv).isascii():
                    value=0<=av and 0<=bv and av<=len(sv)-nv and bv<=len(tv)-nv
                    if value and nv>0:
                        x,y=sv[av:av+nv],tv[bv:bv+nv];value=x.lower()==y.lower() if ignore else x==y
                    return Expr(str(value).lower(),BOOL,p)
            self.opaque('c0_equal_ignore_case',['Seq[Int]','Seq[Int]'],'Bool')
            matched=f'({flag} ? c0_equal_ignore_case({s}[{a}..{a}+{count}],{t}[{b}..{b}+{count}]) : {s}[{a}..{a}+{count}] == {t}[{b}..{b}+{count}])'
            return Expr(f'(0 <= {a} && 0 <= {b} && {a} <= |{s}|-{count} && {b} <= |{t}|-{count} && ({count} <= 0 || {matched}))',BOOL,p)
        if name=='matches' and len(args)==1 and args[0].typ==STRING:
            nodes=children(n.child_by_field_name('arguments'))
            pattern=self.constant_value(nodes[0])
            if not pattern or pattern[0]!='string':self.fail('Regex abstraction needs a fixed valid pattern',n)
            import re
            try:re.compile(pattern[1])
            except re.error:self.fail('Regex syntax unsupported',n)
            # The currently used fixed ASCII patterns are separately exercised
            # with the JDK. Do not infer Java regex semantics from Python regex.
            self.opaque('c0_regex_matches',['Seq[Int]','Seq[Int]'],'Bool')
            p=list(pre)+self.guard(recv.value+' != null','NullPointerException')
            return Expr(f'c0_regex_matches({recv.value}.str_chars,{args[0].value}.str_chars)',BOOL,p)
        return super().string_call(recv,name,args,pre,n)

    def operation(self,a,b,op,n):
        if op in ('==','!=') and (a.typ.base=='C0Stream' or b.typ.base=='C0Stream') and a.typ!=NULL and b.typ!=NULL:
            self.fail('Standard stream identity is outside the output model',n)
        if op=='/' and a.typ.real and b.typ.integer:
            pre=a.pre+b.pre+['assert '+b.value+' != 0']
            return Expr(f'({a.value} / {b.value})',a.typ,pre)
        return super().operation(a,b,op,n)
