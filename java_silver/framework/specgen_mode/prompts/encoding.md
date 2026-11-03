[Values and expressions]
Int is mathematical and unbounded. Perm also represents exact real-valued arithmetic: write is one and none is zero. Use only declarations present in the supplied program. When supplied, jdiv/jrem implement signed integer division toward zero and its remainder; their divisor must be nonzero. Use &&, ||, !, ==>, and condition ? a : b. Guard sequence accesses with their index bounds.

[Heap model]
Ref denotes identity; null is the null reference. Arrays use a sequence-valued field, for example a.arr_int: Seq[Int]. Strings use s.str_chars: Seq[Int]. Object fields use their declared names. |s| is sequence length; s[i] is indexing; s[i := v] is an immutable updated sequence; ++ concatenates. Reading local sequence values requires no permission.

acc(a.arr_int) grants full access to that entire field. Reading requires positive permission, writing requires full permission. Permission does not establish values or bounds. Do not duplicate full permission for aliases. Baseline root resource clauses describe the supported input model and may not be strengthened. Return needed resources in ensures before postconditions that read those fields.

[Method contracts]
Silicon verifies method calls modularly: a caller establishes requires and learns ensures. Returning permission does not state that contents are unchanged; use a value relation such as a.arr_int == old(a.arr_int) when appropriate. old(e) refers to the enclosing method's entry state. Use formal input names shown in the signature, including any _j_in_ names, in contracts; local copies are not formal inputs. Read the actual declared output name, normally res.

Only listed methods are editable. Add a postcondition to every listed specification method. Constructors and internal methods may need additional resource or value summaries for their callers; their bodies are also verified. Predicates package existing resources and facts; fold/unfold do not create permissions. Functions have definitions, and additional induction facts may require implemented lemmas.

[Proof insertion]
Use before/after only where allowed by an anchor. Label anchors accept after: those insertions execute on jumps to that label too. Ghosts are local proof variables; copying a Ref preserves aliasing, while copying a sequence value stores its contents. Do not change executable statements or original assertions.
