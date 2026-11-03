Return one JSON object; unused optional fields may be omitted. Return the complete candidate each time, not a patch to the last reply.

- methods: object keyed by editable method ID, each value {"requires_add": [], "ensures_add": ["Silver expression"]}. Every specification method needs at least one ensures_add. Root requires_add must be empty or omitted.
- loops: {} for this route, unless loop IDs are explicitly supplied; then include every ID with {"invariants": ["expression"]}.
- functions: [{"name":"proof_f", "params":[{"name":"x","type":"Int"}], "return_type":"Int", "requires":[], "ensures":[], "body_expr":"x", "decreases":[]}].
- predicates: [{"name":"Data", "params":[], "body_assertion":"..."}].
- ghosts: object keyed by method ID with [{"name":"g_snapshot","type":"Seq[Int]"}].
- lemmas: [{"name":"Lemma_fact", "params":[], "requires":[], "ensures":[], "locals":[], "body":[], "decreases":[]}]. Parameters and locals use name/type objects. Every lemma has a body.
- insertions: [{"method_id":"...", "anchor_id":"...", "position":"after", "statements":[]}].

Proof statements: {"kind":"assert","expr":"..."}, {"kind":"ghost_assign","target":"g_snapshot","expr":"..."}, {"kind":"lemma_call","name":"Lemma_fact","args":[]}, {"kind":"fold","predicate":"acc(Data(...))"}, {"kind":"unfold","predicate":"acc(Data(...))"}, or {"kind":"if","condition":"...","then":[],"else":[]}.
Assignments may target only declared ghost or lemma-local variables. Helper names must be fresh; lemma names start with Lemma_. Use decreases [] for nonrecursive helpers and a checked measure for recursive helpers. Expressions contain no comments or statement/declaration delimiters. Do not supply task IDs, hashes or schema metadata.
