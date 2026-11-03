"""Static soundness guard for the contract's `global` field.  [PATCH L6]

Why this exists
---------------
Boogie checks procedures for PARTIAL correctness only. A `procedure Lemma_*` that never returns,
has no body, assumes its goal, or is excused from verification still "verifies", and every
`call` to it then hands its `ensures` to the caller for free. With a false target property, any
of these made the framework report SUCCESS (see tests/soundness/cases.py, G1-G16). Neither the
`axiom` ban nor the smoke test catches them: the smoke guard sits at procedure entry, and the
lemma is only called later.

What this module enforces
-------------------------
1. Whitelist. After comments are removed, `global` may contain only
     - `function` declarations (with or without a body; `{:inline}` is the one allowed attribute),
     - `procedure Lemma_*` declarations WITH a body and only `requires` / `ensures` clauses,
     - plain `var` / `const` / `type` / `datatype` declarations.
   Forbidden anywhere: `axiom`, `assume`, `free`, `where`, `modifies`, `implementation`, `while`,
   `goto`, `call forall`, Civl keywords, any other `{:attribute}`, string literals.
2. Calls. A lemma body may only call `Lemma_*` procedures declared in the same `global`.
   Program procedures are off limits: they are only partially correct themselves.
3. No mutual recursion. The lemma call graph must be acyclic apart from self-loops.
4. Termination of self-recursion. A recursive lemma must declare an integer measure, written
       // decreases: <expr>
   between its signature and its body (a Dafny-style `decreases <expr>;` clause is accepted
   too). The measure is compiled into `function Lemma_X__measure(<params>): int { <expr> }`
   and every self-call is preceded by
       assert 0 <= Lemma_X__measure(<args>) && Lemma_X__measure(<args>) < Lemma_X__measure(<params>);
   Boogie in-parameters are immutable, so `Lemma_X__measure(<params>)` is the entry value.
   Together with (1)-(3) this gives total correctness by well-founded induction.

Known gap (by decision, 2026-10-17): recursive `function` bodies are not checked for
well-foundedness. A non-well-founded definition is an inconsistent axiom in disguise; the smoke
test catches it only when Z3 happens to find the contradiction.

Idempotence
-----------
The CEGIS loop shows the LLM the generated Boogie, so the next reply may contain the framework's
own measure functions and termination asserts. Those are recognised and regenerated, and the
`// decreases:` comment is preserved in the output, so `audit_global(audit_global(x)) ==
audit_global(x)`.
"""

import re

MEASURE_SUFFIX = "__measure"
FRAMEWORK_TAG = "[framework]"

_FORBIDDEN_WORDS = {
    "axiom": "`axiom` is an unproved assumption",
    "assume": "`assume` makes the lemma vacuous",
    "free": "`free` clauses are assumed, not checked",
    "where": "`where` clauses are assumed, not checked",
    "modifies": "a lemma must not change program state, so `modifies` is not allowed",
    "implementation": "write the body directly after the lemma's specification",
    "while": "loops are not checked for termination; use recursion with a `// decreases:` measure",
    "goto": "jumps can form loops that are not checked for termination",
    "yield": "Civl constructs are not allowed",
    "async": "Civl constructs are not allowed",
    "par": "Civl constructs are not allowed",
}
_FORBIDDEN_RE = re.compile(r'\b(' + '|'.join(_FORBIDDEN_WORDS) + r')\b')
_ATTR_RE = re.compile(r'\{\s*:')
_INLINE_ATTR_RE = re.compile(r'\{\s*:\s*inline\s*\}')
_DECL_KW_RE = re.compile(r'\b(function|procedure|var|const|type|datatype)\b')
_SENTINEL_RE = re.compile(r'__DECREASES_(\d+)__')
_CALL_RE = re.compile(r'\bcall\b')
_CALL_FORM_RE = re.compile(r'call\s+(?:([A-Za-z_][\w\s,]*?)\s*:=\s*)?([A-Za-z_]\w*)\s*\(')
_OWN_ASSERT_RE = re.compile(r'assert\s[^;]*?\w' + MEASURE_SUFFIX + r'\s*\([^;]*;\s*')
_CLAUSE_RE = re.compile(r'^(requires|ensures|decreases)\b\s*(.*)$', re.S)


class _Fail(Exception):
    pass


# ----------------------------------------------------------------------------------------------
# small lexical helpers
# ----------------------------------------------------------------------------------------------

def _match_close(text, open_idx):
    """Index of the bracket closing the one at `open_idx` (any of ([{), or -1."""
    pairs = {'(': ')', '[': ']', '{': '}'}
    stack = []
    for i in range(open_idx, len(text)):
        c = text[i]
        if c in pairs:
            stack.append(pairs[c])
        elif c in ')]}':
            if not stack or stack.pop() != c:
                return -1
            if not stack:
                return i
    return -1


def _depth_map(text):
    """depth[i] = nesting depth of ([{ just before character i."""
    depth, d = [], 0
    for c in text:
        depth.append(d)
        if c in '([{':
            d += 1
        elif c in ')]}':
            d = max(0, d - 1)
    return depth


def _split_top(text, sep):
    """Split on `sep` characters that are not nested in any bracket."""
    parts, cur, d = [], [], 0
    for c in text:
        if c in '([{':
            d += 1
        elif c in ')]}':
            d -= 1
        if c == sep and d == 0:
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append(''.join(cur))
    return parts


def _find_top(text, start, pattern):
    """First match of `pattern` at bracket depth 0 relative to `start`, or None."""
    d = 0
    i = start
    rx = re.compile(pattern)
    while i < len(text):
        if d == 0:
            m = rx.match(text, i)
            if m:
                return m
        c = text[i]
        if c in '([{':
            d += 1
        elif c in ')]}':
            d -= 1
        i += 1
    return None


def _norm(s):
    return " ".join(s.split())


# ----------------------------------------------------------------------------------------------
# step 1: measures and comments
# ----------------------------------------------------------------------------------------------

def _extract_measures(text, measures):
    """Replace `// decreases: e` and `/* decreases: e */` by sentinels; collect the expressions."""

    def block(m):
        measures.append(_norm(m.group(1)))
        return f" __DECREASES_{len(measures) - 1}__ "

    text = re.sub(r'/\*\s*decreases\b\s*:?(.*?)\*/', block, text, flags=re.S)

    out, pos = [], 0
    rx = re.compile(r'//\s*decreases\b\s*:?')
    while True:
        m = rx.search(text, pos)
        if not m:
            out.append(text[pos:])
            break
        out.append(text[pos:m.start()])
        eol = text.find('\n', m.end())
        eol = len(text) if eol == -1 else eol
        line = text[m.end():eol]
        # In a one-line entry the comment would swallow the rest of the line. The measure ends at
        # the first top-level `requires` / `ensures` / `{` / `;`; whatever follows is code again.
        stop = _find_top(line, 0, r'\b(?:requires|ensures)\b|[{;]')
        cut = stop.start() if stop else len(line)
        measures.append(_norm(line[:cut]))
        out.append(f" __DECREASES_{len(measures) - 1}__ ")
        out.append(line[cut:])
        pos = eol
    return ''.join(out)


def _check_measure(expr):
    """A measure comes out of a comment, i.e. from text the whitelist scan never saw. It is pasted
    into a function body, so it must be a single balanced expression that cannot close that body
    and open new declarations."""
    if re.search(r'[;{}"]', expr) or re.search(r'/[/*]', expr):
        raise _Fail(f"decreases measure {expr!r}: must be a plain integer expression "
                    "(no `;`, braces, quotes or comments)")
    m = _FORBIDDEN_RE.search(expr) or re.search(r'\b(?:function|procedure|call)\b', expr)
    if m:
        raise _Fail(f"decreases measure {expr!r}: `{m.group(0)}` is not allowed in a measure")
    d = 0
    for c in expr:
        d += c in '(['
        d -= c in ')]'
        if d < 0:
            break
    if d != 0:
        raise _Fail(f"decreases measure {expr!r}: unbalanced brackets")


def _strip_comments(text):
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)


# ----------------------------------------------------------------------------------------------
# step 2: declarations
# ----------------------------------------------------------------------------------------------

def _segments(text):
    depth = _depth_map(text)
    starts = [m for m in _DECL_KW_RE.finditer(text) if depth[m.start()] == 0]
    if not starts:
        if text.strip():
            raise _Fail(f"global: no declaration found in {_norm(text)[:80]!r}")
        return []
    lead = text[:starts[0].start()].strip()
    if lead:
        raise _Fail(f"global: unrecognised text before the first declaration: {_norm(lead)[:80]!r}")
    segs = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        segs.append((m.group(1), text[m.start():end].strip()))
    return segs


def _parse_params(ptext, lemma):
    """`a: int, b, c: [int]int` -> [('a','int'), ('b','[int]int'), ('c','[int]int')]."""
    out, pending = [], []
    for item in _split_top(ptext, ','):
        item = item.strip()
        if not item:
            continue
        if ':' in item:
            name, typ = item.split(':', 1)
            name, typ = name.strip(), _norm(typ)
            if not re.fullmatch(r'[A-Za-z_]\w*', name):
                raise _Fail(f"{lemma}: cannot read parameter {item!r}")
            for p in pending:
                out.append((p, typ))
            pending = []
            out.append((name, typ))
        elif re.fullmatch(r'[A-Za-z_]\w*', item):
            pending.append(item)
        else:
            raise _Fail(f"{lemma}: cannot read parameter {item!r}")
    if pending:
        raise _Fail(f"{lemma}: parameter(s) {pending} have no type")
    return out


class _Lemma:
    def __init__(self, name, params_text, params, returns_text, clauses, measure, body):
        self.name = name
        self.params_text = params_text
        self.params = params
        self.returns_text = returns_text
        self.clauses = clauses
        self.measure = measure
        self.body = body          # text between the outer braces, own asserts removed
        self.calls = []           # (start, end_of_open_paren, callee, args_text)


def _parse_lemma(seg, measures):
    m = re.match(r'procedure\s+([A-Za-z_]\w*)\s*\(', seg)
    if not m:
        raise _Fail(f"global: cannot read procedure header {_norm(seg)[:80]!r}")
    name = m.group(1)
    if not name.startswith("Lemma_"):
        raise _Fail(f"global: procedure {name!r} is not allowed; only `procedure Lemma_*` may appear in global")
    p_open = m.end() - 1
    p_close = _match_close(seg, p_open)
    if p_close == -1:
        raise _Fail(f"{name}: unbalanced parameter list")
    params_text = seg[p_open + 1:p_close]
    params = _parse_params(params_text, name)
    rest_at = p_close + 1

    returns_text = ""
    r = re.match(r'\s*returns\s*\(', seg[rest_at:])
    if r:
        r_open = rest_at + r.end() - 1
        r_close = _match_close(seg, r_open)
        if r_close == -1:
            raise _Fail(f"{name}: unbalanced returns list")
        returns_text = seg[r_open + 1:r_close]
        rest_at = r_close + 1

    if re.match(r'\s*;', seg[rest_at:]):
        raise _Fail(f"{name}: has no body. A lemma must carry a body that proves its ensures")
    b = _find_top(seg, rest_at, r'\{')
    if b is None:
        raise _Fail(f"{name}: has no body. A lemma must carry a body that proves its ensures")
    b_close = _match_close(seg, b.start())
    if b_close == -1:
        raise _Fail(f"{name}: unbalanced body braces")
    if seg[b_close + 1:].strip():
        raise _Fail(f"{name}: unexpected text after the body: {_norm(seg[b_close + 1:])[:60]!r}")

    for part, where in ((params_text, "parameter list"), (returns_text, "returns list"),
                        (seg[b.start():b_close + 1], "body")):
        if _SENTINEL_RE.search(part):
            raise _Fail(f"{name}: the `// decreases:` comment must sit between the signature and "
                        f"the body, not inside the {where}")

    spec = seg[rest_at:b.start()]
    found = [int(k) for k in _SENTINEL_RE.findall(spec)]
    spec = _SENTINEL_RE.sub(' ', spec)
    clauses, measure_exprs = [], [measures[k] for k in found]
    for raw in _split_top(spec, ';'):
        c = raw.strip()
        if not c:
            continue
        cm = _CLAUSE_RE.match(c)
        if not cm:
            raise _Fail(f"{name}: only `requires` / `ensures` clauses are allowed, found {_norm(c)[:60]!r}")
        if cm.group(1) == "decreases":
            measure_exprs.append(_norm(cm.group(2)))
        else:
            clauses.append(f"{cm.group(1)} {_norm(cm.group(2))};")
    if len(measure_exprs) > 1:
        raise _Fail(f"{name}: declares more than one decreases measure")
    measure = measure_exprs[0] if measure_exprs else None
    if measure is not None and not measure:
        raise _Fail(f"{name}: the decreases measure is empty")

    body = seg[b.start() + 1:b_close]
    body = _OWN_ASSERT_RE.sub('', body)   # our own termination asserts, copied back by the LLM
    lem = _Lemma(name, params_text, params, returns_text, clauses, measure, body)

    for cm in _CALL_RE.finditer(body):
        f = _CALL_FORM_RE.match(body, cm.start())
        if not f:
            raise _Fail(f"{name}: unsupported call form near {_norm(body[cm.start():cm.start() + 40])!r}")
        callee = f.group(2)
        if callee == "forall":
            raise _Fail(f"{name}: `call forall` is not allowed")
        a_open = f.end() - 1
        a_close = _match_close(body, a_open)
        if a_close == -1 or not re.match(r'\s*;', body[a_close + 1:]):
            raise _Fail(f"{name}: malformed call to {callee}")
        lem.calls.append((cm.start(), callee, body[a_open + 1:a_close]))
    return lem


def _check_function(seg, lemma_names):
    m = re.match(r'function\s+(?:\{\s*:\s*inline\s*\}\s*)?([A-Za-z_]\w*)', seg)
    if not m:
        raise _Fail(f"global: cannot read function header {_norm(seg)[:80]!r}")
    name = m.group(1)
    if name.endswith(MEASURE_SUFFIX):
        owner = name[:-len(MEASURE_SUFFIX)]
        if owner in lemma_names:
            return None          # the framework's own measure function, regenerated below
        raise _Fail(f"global: function names ending in {MEASURE_SUFFIX!r} are reserved")
    if _SENTINEL_RE.search(seg):
        seg = _SENTINEL_RE.sub(' ', seg)   # decreases on a function: harmless, not checked
    return seg


def _find_mutual_cycle(edges):
    """Return one cycle (list of names) of length >= 2 in `edges`, ignoring self-loops."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in edges}
    stack = []

    def dfs(n):
        color[n] = GREY
        stack.append(n)
        for v in sorted(edges[n]):
            if v == n:
                continue
            if color[v] == GREY:
                return stack[stack.index(v):] + [v]
            if color[v] == WHITE:
                cyc = dfs(v)
                if cyc:
                    return cyc
        stack.pop()
        color[n] = BLACK
        return None

    for n in sorted(edges):
        if color[n] == WHITE:
            cyc = dfs(n)
            if cyc:
                return cyc
    return None


# ----------------------------------------------------------------------------------------------
# step 3: emission
# ----------------------------------------------------------------------------------------------

def _emit_lemma(lem):
    out = []
    recursive = any(c == lem.name for _, c, _ in lem.calls)
    if recursive:
        fparams = ", ".join(f"{n}: {t}" for n, t in lem.params)
        out.append(f"// {FRAMEWORK_TAG} termination measure of {lem.name}, from its `decreases` comment\n"
                   f"function {lem.name}{MEASURE_SUFFIX}({fparams}): int {{ {lem.measure} }}")

    body = lem.body
    if recursive:
        here = ", ".join(n for n, _ in lem.params)
        entry = f"{lem.name}{MEASURE_SUFFIX}({here})"
        for start, callee, args in sorted(lem.calls, reverse=True):
            if callee != lem.name:
                continue
            nxt = f"{lem.name}{MEASURE_SUFFIX}({args})"
            check = (f"assert 0 <= {nxt} && {nxt} < {entry};"
                     f"  // {FRAMEWORK_TAG} termination: the measure must decrease\n    ")
            body = body[:start] + check + body[start:]

    header = f"procedure {lem.name}({_norm(lem.params_text)})"
    if lem.returns_text:
        header += f" returns ({_norm(lem.returns_text)})"
    lines = [header]
    if lem.measure is not None:
        lines.append(f"  // decreases: {lem.measure}")
    lines += [f"  {c}" for c in lem.clauses]
    lines.append("{" + body + "}")
    out.append("\n".join(lines))
    return out


# ----------------------------------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------------------------------

def audit_global(entries):
    """Vet and instrument the `global` field.

    Returns (new_entries, problems). When `problems` is non-empty the round must be rejected and
    `new_entries` is meaningless.
    """
    if entries is None:
        return [], []
    if isinstance(entries, str):
        entries = [entries]
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        return [], ["global: expected a list of strings"]
    if not any(e.strip() for e in entries):
        return [], []

    measures = []
    try:
        text = "\n".join(_extract_measures(e, measures) for e in entries)
        for expr in measures:
            _check_measure(expr)
        text = _strip_comments(text)

        if '"' in text:
            raise _Fail("global: string literals are not allowed")
        for m in _ATTR_RE.finditer(text):
            if not (_INLINE_ATTR_RE.match(text, m.start())
                    and re.search(r'\bfunction\s*$', text[:m.start()])):
                raise _Fail(f"global: attribute {_norm(text[m.start():m.start() + 30])!r} is not "
                            "allowed (only `{:inline}` on a function)")
        problems = []
        for m in _FORBIDDEN_RE.finditer(text):
            problems.append(f"global: `{m.group(1)}` is forbidden: {_FORBIDDEN_WORDS[m.group(1)]}")
        if re.search(r'\bcall\s+forall\b', text):
            problems.append("global: `call forall` is not allowed")
        if problems:
            return [], sorted(set(problems))

        segs = _segments(text)
        lemmas, order = {}, []
        for kind, seg in segs:
            if kind == "procedure":
                lem = _parse_lemma(seg, measures)
                if lem.name in lemmas:
                    raise _Fail(f"global: {lem.name} is declared twice")
                lemmas[lem.name] = lem
                order.append(("lemma", lem.name))
            else:
                if kind != "function" and _SENTINEL_RE.search(seg):
                    raise _Fail(f"global: a `// decreases:` comment belongs to a Lemma_* procedure, "
                                f"not to a {kind} declaration")
                order.append((kind, seg))

        for lem in lemmas.values():
            for _, callee, _ in lem.calls:
                if callee not in lemmas:
                    raise _Fail(f"{lem.name}: calls {callee!r}, which is not a Lemma_* procedure "
                                "declared in global. Lemmas may only call other lemmas")
        cyc = _find_mutual_cycle({n: {c for _, c, _ in l.calls} for n, l in lemmas.items()})
        if cyc:
            raise _Fail("global: mutual recursion between lemmas is not allowed: " + " -> ".join(cyc))
        for lem in lemmas.values():
            if any(c == lem.name for _, c, _ in lem.calls) and lem.measure is None:
                raise _Fail(f"{lem.name}: is recursive but declares no measure. Add a line "
                            "`// decreases: <int expression over the parameters>` between its "
                            "signature and its body")

        out = []
        for kind, item in order:
            if kind == "lemma":
                out.extend(_emit_lemma(lemmas[item]))
            elif kind == "function":
                seg = _check_function(item, set(lemmas))
                if seg is not None:
                    out.append(seg)
            else:
                out.append(item)
        return out, []
    except _Fail as e:
        return [], [str(e)]


def audit_contract(parsed):
    """Apply `audit_global` to a parsed contract dict. Returns (new_contract, problems)."""
    if not isinstance(parsed, dict):
        return parsed, []
    if "global" not in parsed:
        return parsed, []
    new_global, problems = audit_global(parsed.get("global"))
    if problems:
        return parsed, problems
    new = dict(parsed)
    new["global"] = new_global
    return new, []
