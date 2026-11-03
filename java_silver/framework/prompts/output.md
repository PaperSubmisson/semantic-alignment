Return a JSON object, not a full program or patch. Use these fields; unused optional fields may be omitted:

- "loops": object containing EVERY supplied loop ID with {"invariants": [Silver expressions without the invariant keyword]}.
- "methods": object keyed only by listed editable internal-method IDs, with {"requires_add": [], "ensures_add": []}.
- "functions": [{"name": "proof_count", "params": [{"name": "s", "type": "Seq[Int]"}], "return_type": "Int", "requires": [], "ensures": [], "body_expr": "...", "decreases": ["|s|"]}]. Every function has a body.
- "predicates": [{"name": "Data", "params": [], "body_assertion": "..."}]. Every predicate has a body.
- "ghosts": object keyed by method IDs, with lists of {"name": "g_snapshot", "type": "Seq[Int]"}. Initialize through insertions before use.
- "lemmas": [{"name": "Lemma_update", "params": [], "requires": [], "ensures": [], "locals": [], "body": [], "decreases": []}]. All lemmas have implementations; params and locals use name/type objects.
- "insertions": [{"method_id": "...", "anchor_id": "...", "position": "before", "statements": []}]. Position is before or after; use the supplied IDs.

Label anchors list positions ["after"] and accept only "after". Use the supplied label anchor ID to place proof statements at a common loop exit or return label.

Statements in insertions and lemma bodies:
    {"kind":"ghost_assign", "target":"g_snapshot", "expr":"a.arr_int"}
    {"kind":"assert", "expr":"..."}
    {"kind":"lemma_call", "name":"Lemma_update", "args":["..."]}
    {"kind":"fold", "predicate":"acc(Data(...), write)"}
    {"kind":"unfold", "predicate":"acc(Data(...), write)"}
    {"kind":"if", "condition":"...", "then":[], "else":[]}

Assignments target only declared ghosts or lemma locals. Use fresh names; lemma names start with Lemma_. Expressions contain no comments, declarations, or statement delimiters. Do not return task IDs, hashes, or schema metadata. Return a complete replacement proof on every reply, including previously supplied helpers still needed; replies are not merged.
