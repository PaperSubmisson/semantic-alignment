[Silver and arithmetic]
Use the declarations in the supplied program. Int is unbounded. Perm is used for exact real-valued arithmetic: write is one and none is zero; 3 * write / 10 is three tenths. A Perm numeric variable is not an acc assertion. Use &&, ||, !, ==>, ==, != and condition ? a : b. Quantifiers use forall q: Int :: guard ==> property and exists q: Int :: guard && property. Guard every sequence index by its valid range.

When declared, jdiv(x,y) divides integers toward zero and jrem(x,y) is its corresponding remainder; both require y != 0. Bare / and % are Silver operators. For nonnegative operands and a positive divisor they agree with the corresponding integer helpers; for signed operands use the supplied helper definitions. Do not invent helper names.

[Heap and sequences]
Ref identifies a heap object; null is the null reference. Heap storage is a reference plus a declared field. Typical fields, only when present in the program, are:
    a.arr_int: Seq[Int]       integer-array contents
    a.arr_char: Seq[Int]      character-array contents
    a.arr_ref: Seq[Ref]       array of object/row references
    s.str_chars: Seq[Int]     string character contents
Object fields use their actual declared names, for example node.Node_left.

|a.arr_int| is the array length and a.arr_int[q] its element. Each array owns one sequence-valued heap location, rather than a separate heap location for each index. An element update is a field update such as a.arr_int := a.arr_int[q := value]. Sequences are immutable values; ++ concatenates and s[lo..hi] slices. Reading or changing a local Seq value requires no heap permission.

acc(a.arr_int) means full permission to the entire field. acc(a.arr_int, 1/2) permits reading; writing needs full permission. Permissions imply the reference is nonnull but do not determine the field value or array bounds. They must be carried through loops and transferred/returned in method contracts. Do not request full permission repeatedly for aliased references. Different field names on one reference denote different locations.

For a matrix m, reading m.arr_ref[i].arr_int[j] needs permission to m.arr_ref, a valid outer index, permission to that row's arr_int, and a valid inner index. Rows may alias if the fixed conditions permit it. Quantifying full row permission over indices can accidentally require rows to be distinct. Retain the supplied fractions or package resources without duplicating them. A quantified assertion over distinct Ref values can describe shared rows once.

Place resource clauses before clauses that read the current heap. For example, a zero-filled prefix may require:
    invariant acc(a.arr_int)
    invariant 0 <= i && i <= |a.arr_int|
    invariant forall q: Int :: 0 <= q && q < i ==> a.arr_int[q] == 0
These are examples, not task invariants to copy indiscriminately. Existing fixed contracts already supply their listed resources.

[Old values, snapshots, and calls]
old(e) refers to the containing method's entry heap. It does not save a changing local variable or mean the previous iteration. Save a scalar in a declared ghost when needed. Copying a Ref preserves aliasing, not contents; assigning g_snapshot := a.arr_int into a Seq[Int] ghost saves the current sequence value, provided the read permission is available. It needs no undeclared snapshot function.

Silicon checks calls modularly. The caller proves the callee's requires and receives its ensures. Returning acc(a.arr_int) returns permission but does not state the contents are unchanged. An unchanged input needs a relation such as a.arr_int == old(a.arr_int); an update needs an exact update/frame relation. Internal contracts may be extended only for the listed editable methods; all bodies and call sites remain proof obligations.

[Control flow and proof helpers]
For a Label anchor, insert with position "after". These statements run on paths that reach that label, including goto jumps to it. This is useful for folding predicates or calling reconstruction lemmas after a loop exit. An insertion after the While statement but before its exit label is skipped by a goto to that label; choose the label anchor when those exit paths need the proof. Labels and jumps themselves remain unchanged.

Use the actual while conditions, labels, goto paths, flags and temporary variables visible in Silver. A lowered loop condition may be a Boolean gate computed before and after iterations. Preserve its relationship to counters/data; the negated gate alone need not state the original stopping condition. Early exits have their own path conditions. Check loop-entry facts even for zero iterations. Do not use out-of-scope locals or treat generated temporaries as stable across iterations without an invariant.

Defined functions express mathematical summaries. A function body does not automatically establish every induction theorem; an implemented lemma may be needed to connect a sequence update to a count or other recurrence. Recursive functions and lemmas require a checked decreasing measure. Nonrecursive helpers use an empty decreases list. Predicates package existing permissions and facts: fold consumes the body resources, and unfold exposes them; neither creates ownership. To read through a folded predicate, use an appropriate unfolding expression or an insertion at an available anchor.
