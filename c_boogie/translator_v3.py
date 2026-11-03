from pycparser import c_parser, c_ast
import re
import json

def preprocess_code(text):
    text = re.sub(r'/\*.*?\*/', '',text,flags = re.DOTALL)
    text = re.sub(r'//.*','',text)
    text = re.sub(r'__attribute__\s*\(\(.*\)\)', '', text)
    text = re.sub(r'void\s*\*\s*malloc\s*\(.*?\)\s*;', '', text)
    
    text = re.sub(r'\\\r?\n', ' ', text)
    lines = text.split('\n')
    cleaned_lines = []
    in_extern_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('extern '):
            in_extern_block = True
        
        if not in_extern_block:
            cleaned_lines.append(line)
        
        if in_extern_block and stripped.endswith(';'):
            in_extern_block = False
    text = '\n'.join(cleaned_lines)
    
    lines = text.split('\n')
    new_lines = []
    macros = {} 

    for line in lines:
        stripped = line.strip()
        
        if not stripped:
            continue

        if stripped.startswith('#define'):
            parts = stripped.split()
            if len(parts) >= 3:
                key = parts[1]
                val = " ".join(parts[2:])
                macros[key] = val
            continue 

        if stripped.startswith('#'):
            continue

        if stripped.startswith('extern'):
            continue
        
        for key, val in macros.items():
            pattern = r'\b' + re.escape(key) + r'\b'
            line = re.sub(pattern, lambda m, v=val: v, line)

        new_lines.append(line)

    return '\n'.join(new_lines)

def clean_brackets(expr_str):
    """
    Smart bracket cleanup: repair extra, misplaced, and missing brackets.
    e.g. "(a+b))" -> "(a+b)"
          "Mem[(a+b]" -> "Mem[(a+b)]"
          "((a+b)" -> "((a+b))"
    """
    stack = []
    result = []
    matching_close = {'(': ')', '[': ']', '{': '}'}

    for char in str(expr_str):
        if char in "([{":
            stack.append(char)
            result.append(char)
        elif char in ")]}":
            if not stack:
                continue
            
            top = stack.pop()
            expected_close = matching_close[top]
            
            result.append(expected_close)
        else:
            result.append(char)

    while stack:
        top = stack.pop()
        result.append(matching_close[top])

    return "".join(result)

class AddressTakenVisitor(c_ast.NodeVisitor):
    """
    The escape analyzer traverses the whole AST to identify every variable whose address is taken (e.g., &x). It also enforces scope isolation so that local variables with identical names in different functions are distinguished.
    """
    def __init__(self):
        #  func_name -> set(var_names)
        self.escaped_vars = {'global': set()} 
        self.current_func = 'global'

    def visit_FuncDef(self, node):
        self.current_func = node.decl.name
        self.escaped_vars[self.current_func] = set()
        self.generic_visit(node)
        self.current_func = 'global'

    def visit_UnaryOp(self, node):
        if node.op == '&':
            if isinstance(node.expr, c_ast.ID):
                self.escaped_vars[self.current_func].add(node.expr.name)
        self.generic_visit(node)

def _labels_in(node):
    """Names of every C label defined anywhere inside `node`. [PATCH T-C1]"""
    found = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if isinstance(n, c_ast.Label):
            found.add(n.name)
        stack.extend(ch for _, ch in n.children())
    return found


def _return_labels(func_body):
    """Map label -> c_ast.Return for labels that act as a function exit. [PATCH T-C1]

    A label counts as an exit when its statement is a `return`, or when it labels an empty
    statement that is the last item of the function body (falling off the end is a return).
    """
    out = {}
    if func_body is None:
        return out
    items = (func_body.block_items or []) if isinstance(func_body, c_ast.Compound) else []
    last = items[-1] if items else None
    stack = [func_body]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if isinstance(n, c_ast.Label):
            if isinstance(n.stmt, c_ast.Return):
                out[n.name] = n.stmt
            elif n is last and (n.stmt is None or isinstance(n.stmt, c_ast.EmptyStatement)):
                out[n.name] = c_ast.Return(expr=None, coord=n.coord)
        stack.extend(ch for _, ch in n.children())
    return out


class VariableAnalyzer(c_ast.NodeVisitor):
    """
    Scan C AST nodes (typically loop bodies) to analyze read/write liveness of variables. Used to determine in/out parameters for Boogie procedures.
    """
    def __init__(self, global_vars_set, func_params_set, func_locals_set, return_labels=None):
        # context information passed in from outside
        self.all_globals = global_vars_set
        self.func_params = func_params_set
        self.func_locals = func_locals_set
        # [PATCH T-C1] label name -> c_ast.Return for labels whose statement is a return
        self.return_labels = return_labels or {}
        self.inner_labels = set()
        self._root = None

        self.reads = set()  
        self.writes = set() 
        self.declares_in_loop = set() 

        # Auxiliary: track variables declared within loop bodies to prevent them from being mistakenly identified as external variables.
        self.current_loop_locals = set()

    def _record_var_access(self, var_name, is_write, c_name=True):
        # [PATCH B4] C identifiers true/false/result are renamed to *_c by the translator
        # (to avoid clashing with Boogie keywords and the procedure's return variable).
        # Record the renamed form, so that a loop procedure's parameters match its body.
        # c_name=False is used for the translator's own control variables (result of the
        # enclosing procedure, has_returned), which are not renamed.
        if c_name and var_name in ("true", "false", "result"):
            var_name = var_name + "_c"
        if var_name in self.all_globals:
            return

        if var_name in self.current_loop_locals:
            return

        if var_name in self.func_params or var_name in self.func_locals:
            if is_write:
                self.writes.add(var_name)
            else:
                self.reads.add(var_name)

    def visit_ID(self, node):
        """Handle standalone variable names (e.g., 'x' in 'if (x < 10)')"""
        # ID node could be an lvalue (write) or an rvalue (read)
        # Here we default to read, because writes are handled specifically in visit_Assignment
        self._record_var_access(node.name, False)

    def visit_Assignment(self, node):
        """Handle assignment statements (x = ...)"""
        # 1. Identify the lvalue (write)
        # We need to recursively strip ArrayRef to get the underlying variable name
        # Example: a[i] = ... → here a is being written
        # Example: m[x][y] = ... → here m is being written
        
        curr = node.lvalue
        # [PATCH B1] an element store a[i] = e / p->f = e writes the heap (Mem_*),
        # not the base variable. Recording the base as *written* made every loop
        # procedure return its array pointers, so the LLM had to re-prove
        # `ensures a == in_a` for each one. Only a '.'-member of a struct VALUE is
        # still treated as a write of the variable (conservative).
        elem_store = False; value_member = False
        while isinstance(curr, c_ast.ArrayRef) or isinstance(curr, c_ast.StructRef):
            if isinstance(curr, c_ast.ArrayRef):
                self.visit(curr.subscript) 
                elem_store = True
                curr = curr.name
            else:
                if curr.type == '.': value_member = True
                else: elem_store = True
                curr = curr.name 
            
        if isinstance(curr, c_ast.ID):
            self._record_var_access(curr.name, not (elem_store and not value_member))
        
        self.visit(node.rvalue)

    def visit_UnaryOp(self, node):
        if node.op in ['p++', '++', 'p--', '--']:
            curr = node.expr
            elem_store = False; value_member = False   # [PATCH B1] see visit_Assignment
            while isinstance(curr, c_ast.ArrayRef) or isinstance(curr, c_ast.StructRef):
                if isinstance(curr, c_ast.ArrayRef):
                    self.visit(curr.subscript)
                    elem_store = True
                    curr = curr.name
                else:
                    if curr.type == '.': value_member = True
                    else: elem_store = True
                    curr = curr.name
            
            if isinstance(curr, c_ast.ID):
                self._record_var_access(curr.name, not (elem_store and not value_member))
        
        self.visit(node.expr)

    def visit_Decl(self, node):
        name = node.name + "_c" if node.name in ("true", "false", "result") else node.name  # [PATCH B4]
        self.declares_in_loop.add(name)
        self.current_loop_locals.add(name) 
        
        if node.init:
            self.visit(node.init)

    def visit_Return(self, node):
        # Force control-flow variables to be passed as in/out parameters.
        self._record_var_access("result", True, c_name=False)
        self._record_var_access("result", False, c_name=False)
        self._record_var_access("has_returned", True)
        self._record_var_access("has_returned", False)
        if node.expr:
            self.visit(node.expr)
    
    def visit_Goto(self, node):
        """[PATCH T-C1] A goto that leaves the analysed loop for a return-like label is a return.

        The old rule compared the raw label against four lowercase names (`out`/`end`/`exit`/
        `done`) while the emitter lowercased it and used eight, so `goto END;` inside a loop never
        made `has_returned` an output of loop_k -- no EXIT_loop_k label and no post-call check
        were generated. Now the decision is structural and shared with the emitter: the target
        label must be in `return_labels` (its statement is a `return`) and must not lie inside the
        node being analysed.
        """
        target = self.return_labels.get(node.name)
        if target is not None and node.name not in self.inner_labels:
            self.visit_Return(target)

    def visit(self, node):
        # The first node visited is the analysis root (a loop): remember which labels live inside
        # it, so that a goto to one of them is recognised as staying within the loop.
        if self._root is None:
            self._root = node
            self.inner_labels = _labels_in(node)
        return super().visit(node)
    
    def generic_visit(self, node):
        super().generic_visit(node)


# =====================================================================================
# [PATCH L3] Insertion replay for the verified-lemma route
# =====================================================================================
# A lemma is a Boogie `procedure`, so within a program procedure it has no effect unless it is
# CALLED, and a `call` cannot appear inside requires/ensures/invariant. The contract therefore
# carries a separate `insertions` object addressed by skeleton label:
#
#   "insertions": {
#     "main": {
#       "decls": ["M0: [int]int"],
#       "stmts": [ {"after_line": 12, "code": ["M0 := Mem_int;"]},
#                  {"after_line": 18, "code": ["call Lemma_Swap(M0, Mem_int, x, 60);"]} ]
#     }
#   }
#
# `after_line: N` refers to the `// #N` label emitted by _emit(), NOT to a physical line number.
# Replay happens at the very end of get_code(), on the fully assembled text, so that it applies
# identically to all four generation modes (plain / smoke / with_snap / FULLSNAP). That is
# deliberate: the counterexample path builds FULLSNAP from the same contract, and a lemma call
# that vanished there would make the reported state trace disagree with what was verified.

# A skeleton label sits mid-line: `x := y;  // #7  // CallID: 3` is a legitimate shape, since
# _emit() puts the label in front of any trailing comment the emitter already added. So the label
# must be matched wherever it occurs, not only at end of line.
LABEL_RE = re.compile(r'//\s*#(\d+)(?=\s|$)')
PROC_START_RE = re.compile(r'^\s*procedure\s+(\w+)\s*\(')

# Whitelist. Inserted text must not be able to touch the program: the premise of this whole
# exercise is "the translation is faithful, the LLM only supplies contracts and lemma calls".
# A guard condition is allowed to be any Boolean expression, but the *only* statements that may
# sit inside it are lemma calls -- the guard can strengthen or weaken applicability, it cannot
# introduce new behaviour.
#
# [PATCH L6] The argument list and the guard used to be `.*` / `.+`, which also match `;`, `{` and
# `}`. Then `call Lemma_Ok(); assume false; call Lemma_Ok();` matched the call pattern as ONE
# statement and was spliced in verbatim (tests/soundness I1, I2: SUCCESS on a false assertion).
# Arguments and guards may now contain no `;`, `{`, `}` and must have balanced brackets, so the
# inserted text is exactly one statement. A snapshot may only assign to a variable declared in that
# procedure's `decls`: otherwise `Size := Mem_int;` (Size is a program global of the same type)
# would rewrite program state. Declared types are restricted to plain/map type syntax, which rules
# out `where` clauses (assumed, never checked) and `;` injection.
_NO_STMT_BREAK = r'[^;{}]'
RE_LEMMA_CALL = re.compile(r'^call\s+Lemma_\w+\s*\((' + _NO_STMT_BREAK + r'*)\)\s*;$')
RE_SNAP_ASSIGN = re.compile(r'^([A-Za-z_]\w*)\s*:=\s*Mem_(?:int|real)\s*;$')
RE_IF_WRAPPER = re.compile(
    r'^if\s*\((' + _NO_STMT_BREAK + r'+)\)\s*\{\s*call\s+Lemma_\w+\s*\((' + _NO_STMT_BREAK
    + r'*)\)\s*;\s*\}$'
)
# A declaration is a bare `name: type` fragment; the framework supplies `var` and the semicolon.
RE_DECL = re.compile(r'^([A-Za-z_]\w*)\s*:\s*((?:\[[\w\s,]*\]\s*)*[A-Za-z_]\w*)$')


def _balanced(text):
    depth = 0
    for c in text:
        if c in '([':
            depth += 1
        elif c in ')]':
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _boogie_real_literal(c_literal):
    """Normalise a C floating-point literal into Boogie's `D.D` / `D.DeN` form.

    [PATCH T-C3] Boogie requires digits on BOTH sides of the decimal point. C allows `.5`, `5.`,
    `1e3`, `1E-6`, `2.5f` and so on. The old code only fixed some of these: `.5` and `-.5` were
    emitted verbatim, Boogie parsed `.5` as an identifier, and the file was rejected
    ("undeclared identifier: .5").
    """
    v = str(c_literal).strip().rstrip('fFlL')
    m = re.fullmatch(r'(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?', v)
    if not m or (not m.group(1) and not m.group(2)):
        return v                       # hex float or something unusual: leave untouched
    int_part = m.group(1) or '0'
    frac_part = m.group(2) or '0'
    out = f"{int_part}.{frac_part}"
    if m.group(3) is not None:
        out += f"e{int(m.group(3))}"
    return out


class ContractError(Exception):
    """Raised when the LLM's contract is structurally unusable (treated as a syntax failure)."""


def _normalise_snippets(value, where):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out = []
        for item in value:
            if not isinstance(item, str):
                raise ContractError(f"{where}: expected a string, got {type(item).__name__}")
            out.append(item)
        return out
    raise ContractError(f"{where}: expected a string or list of strings")


def _validate_inserted_stmt(stmt, where, snapshot_names=()):
    """Enforce the insertion whitelist on one statement.

    `snapshot_names` are the variables declared in this procedure's `decls`; only those may be
    the target of a snapshot assignment. [PATCH L6]
    """
    text = " ".join(stmt.split())
    if not text:
        return None
    m = RE_LEMMA_CALL.match(text)
    if m and _balanced(m.group(1)):
        return text
    m = RE_SNAP_ASSIGN.match(text)
    if m:
        if m.group(1) in snapshot_names:
            return text
        raise ContractError(
            f"{where}: snapshot target {m.group(1)!r} is not declared in this procedure's \"decls\". "
            "A snapshot may only assign to a variable you declared for it, never to a program variable."
        )
    m = RE_IF_WRAPPER.match(text)
    if m and _balanced(m.group(1)) and _balanced(m.group(2)):
        return text
    raise ContractError(
        f"{where}: inserted statement is not permitted: {stmt!r}. "
        "Allowed forms are 'call Lemma_Name(...);', 'X := Mem_int;'/'X := Mem_real;', "
        "and 'if (<cond>) { call Lemma_Name(...); }', each a single statement."
    )


class InsertionSpec:
    """Parsed, validated `insertions` object: procedure -> (decls, [(anchor, [stmts])])."""

    def __init__(self, raw):
        self.decls = {}   # proc -> [decl fragment]
        self.stmts = {}   # proc -> {anchor_int -> [stmt]}
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise ContractError("insertions: expected an object keyed by procedure name")

        for proc, spec in raw.items():
            if not isinstance(spec, dict):
                raise ContractError(f"insertions.{proc}: expected an object")
            proc = str(proc)
            if proc.startswith("Lemma_"):
                # [PATCH L6] Lemmas are vetted as written in `global`; nothing may be spliced into
                # them afterwards (a declaration with `where false` used to make one vacuous).
                raise ContractError(
                    f"insertions.{proc}: insertions may only target program procedures, not lemmas"
                )

            decls, snapshot_names = [], set()
            for frag in _normalise_snippets(spec.get("decls"), f"insertions.{proc}.decls"):
                frag = " ".join(frag.split()).rstrip(";").strip()
                if frag.startswith("var "):
                    frag = frag[4:].strip()
                m = RE_DECL.match(frag)
                if not m or re.search(r'\bwhere\b', frag):
                    raise ContractError(
                        f"insertions.{proc}.decls: expected a bare 'name: type' fragment, got {frag!r}"
                    )
                decls.append(frag)
                snapshot_names.add(m.group(1))

            by_anchor = {}
            for idx, entry in enumerate(spec.get("stmts") or []):
                where = f"insertions.{proc}.stmts[{idx}]"
                if not isinstance(entry, dict):
                    raise ContractError(f"{where}: expected an object with after_line/code")
                anchor = entry.get("after_line")
                if isinstance(anchor, str) and anchor.strip().lstrip("#").isdigit():
                    anchor = int(anchor.strip().lstrip("#"))
                if not isinstance(anchor, int) or isinstance(anchor, bool):
                    raise ContractError(f"{where}.after_line: expected an integer skeleton label")
                code = _normalise_snippets(entry.get("code"), f"{where}.code")
                cleaned = []
                for stmt in code:
                    vetted = _validate_inserted_stmt(stmt, where, snapshot_names)
                    if vetted is not None:
                        cleaned.append(vetted)
                # Array order is significant: several entries may share one anchor and are meant
                # to run in the order given (snapshot first, then the call that consumes it).
                by_anchor.setdefault(anchor, []).extend(cleaned)

            if decls:
                self.decls[proc] = decls
            if by_anchor:
                self.stmts[proc] = by_anchor

    def __bool__(self):
        return bool(self.decls) or bool(self.stmts)


def close_modifies_over_calls(text, program_globals):
    """Make every procedure's `modifies` include the globals its callees modify. [PATCH T-C2]

    The translator collects `modifies` per procedure while visiting it. For *memory* globals it
    over-approximates on every call (any call adds Mem_int/Mem_real/Alloc/Size/Allocator), but a
    *scalar* program global written by the callee was never propagated: `loop_0` calling `foo()`,
    which does `x--`, got no `x` in its modifies. Boogie rejects that ("assigns to a global
    variable that is not in the enclosing procedure's modifies clause"), and every CEGIS round on
    such a task was wasted.

    A post-pass over the assembled text is the robust place for this: callers may appear before
    their callees, loop_k procedures are emitted out of order, and lemma procedures from `global`
    must be included in the call graph as well. Computes the transitive closure (recursion-safe
    fixpoint) and rewrites only the `modifies` line of procedures whose set actually grows, so
    output for programs without this pattern is byte-identical.
    """
    lines = text.split("\n")
    proc_re = re.compile(r'^\s*procedure\s+(\w+)\s*\(')
    mod_re = re.compile(r'^(\s*)modifies\s+([^;]*);')
    call_re = re.compile(r'\bcall\s+(?:[\w\s,\[\]\(\)\+\-]*:=\s*)?(\w+)\s*\(')

    # Locate procedures: name -> (header_start, body_open, body_end)
    procs = {}
    order = []
    i = 0
    while i < len(lines):
        m = proc_re.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        j = i
        while j < len(lines) and "{" not in lines[j]:
            j += 1
        if j >= len(lines):
            break
        depth, k = 0, j
        while k < len(lines):
            depth += lines[k].count("{") - lines[k].count("}")
            if depth <= 0:
                break
            k += 1
        procs[name] = (i, j, k)
        order.append(name)
        i = k + 1

    if not procs:
        return text

    mods, calls, mod_line = {}, {}, {}
    for name, (h, b, e) in procs.items():
        s = set()
        for ln in range(h, b + 1):
            mm = mod_re.match(lines[ln])
            if mm:
                s |= {v.strip() for v in mm.group(2).split(",") if v.strip()}
                mod_line[name] = ln
        # A one-line lemma `procedure L(...) requires ..; { ... }` keeps header and body together;
        # its modifies (if any) is on the same line, which the loop above already saw.
        mods[name] = s
        body = "\n".join(lines[b:e + 1])
        # Self-calls add nothing to the closure, so they are simply dropped.
        calls[name] = {c for c in call_re.findall(body) if c in procs and c != name}

    closed = {n: set(s) for n, s in mods.items()}
    changed = True
    while changed:
        changed = False
        for n in order:
            add = set()
            for c in calls[n]:
                add |= closed[c]
            add &= program_globals | {"Mem_int", "Mem_real", "Alloc", "Size", "Allocator"}
            if not add <= closed[n]:
                closed[n] |= add
                changed = True

    # Rewrite (or insert) modifies lines, bottom-up so earlier indices stay valid.
    edits = []
    for n in order:
        if closed[n] == mods[n]:
            continue
        h, b, e = procs[n]
        new_list = ", ".join(sorted(closed[n]))
        if n in mod_line:
            ln = mod_line[n]
            mm = mod_re.match(lines[ln])
            edits.append(("replace", ln, f"{mm.group(1)}modifies {new_list};"))
        else:
            # No modifies clause yet: put one right after the `procedure ...` header line.
            edits.append(("insert", h + 1, f"  modifies {new_list};"))
    for kind, ln, val in sorted(edits, key=lambda t: t[1], reverse=True):
        if kind == "replace":
            lines[ln] = val
        else:
            lines.insert(ln, val)
    return "\n".join(lines)


def apply_insertions(text, spec, indent_unit="  "):
    """Replay `insertions` onto fully assembled Boogie text, by skeleton label.

    Returns (new_text, problems). `problems` is a list of human-readable complaints; they are
    surfaced to the log rather than raised, because a mis-addressed anchor (a label that no longer
    exists, or a procedure name that was not emitted) is a *contract* mistake the next round can
    fix, not a translator bug.
    """
    if not spec:
        return text, []

    lines = text.split("\n")
    problems = []

    # Locate each procedure body once: [start_index, end_index).
    proc_ranges = {}
    for i, line in enumerate(lines):
        m = PROC_START_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        depth = 0
        opened = False
        end = len(lines)
        for j in range(i, len(lines)):
            depth += lines[j].count("{") - lines[j].count("}")
            if "{" in lines[j]:
                opened = True
            if opened and depth <= 0:
                end = j
                break
        proc_ranges.setdefault(name, (i, end))

    # anchor -> original index of the line carrying that label
    def anchors_of(start, end):
        found = {}
        for i in range(start, end):
            m = LABEL_RE.search(lines[i])
            if m:
                found.setdefault(int(m.group(1)), []).append(i)
        return found

    # Collect every splice first, then apply from the bottom up so earlier indices stay valid.
    splices = []
    for proc, by_anchor in spec.stmts.items():
        if proc not in proc_ranges:
            problems.append(f"insertions: no procedure named {proc!r} in the generated Boogie code")
            continue
        start, end = proc_ranges[proc]
        found = anchors_of(start, end)
        for anchor, stmts in by_anchor.items():
            if anchor not in found:
                problems.append(
                    f"insertions.{proc}: skeleton label #{anchor} does not exist "
                    f"(valid labels are #{min(found) if found else '-'}..#{max(found) if found else '-'})"
                )
                continue
            if len(found[anchor]) > 1:
                problems.append(
                    f"insertions.{proc}: skeleton label #{anchor} is ambiguous "
                    f"({len(found[anchor])} lines carry it); insertion skipped"
                )
                continue
            target = found[anchor][0]
            indent = lines[target][: len(lines[target]) - len(lines[target].lstrip())]
            splices.append((target + 1, [indent + s for s in stmts], proc, anchor))

    decl_splices = []
    for proc, decls in spec.decls.items():
        if proc not in proc_ranges:
            problems.append(f"insertions: no procedure named {proc!r} for declarations")
            continue
        start, end = proc_ranges[proc]
        brace = None
        for i in range(start, end):
            if "{" in lines[i]:
                brace = i
                break
        if brace is None:
            problems.append(f"insertions.{proc}: could not locate the procedure body")
            continue
        indent = indent_unit
        frags = [indent + f"var {d};" for d in decls]
        decl_splices.append((brace + 1, frags, proc, None))

    for pos, payload, _, _ in sorted(splices + decl_splices, key=lambda t: t[0], reverse=True):
        lines[pos:pos] = payload

    return "\n".join(lines), problems


class CToBoogieVisitor(c_ast.NodeVisitor):
    def __init__(self,specs_json_str):
        self.smoke_test = False 
        self.func_ret_types = {} 
        self.indent_level = 0
        self.output_global = [] #global variable declaration statements
        self.output = [] #final complete code
        self.boogie_axioms = [] #list for storing axioms and functions
        self.tmp_count = 0  # temporary variable counter

        self.current_decls = [] 
        self.current_stmts = [] 
        self.global_inits = [] 
        self.var_types = {} 
        self.ptr_target_types = {} # Record the pointed‑to type of a pointer (e.g., p → 'real').
        self.snapshot_is_address = set() # Record which snapshot variables represent memory base addresses.
        self.typedef_env = {} # Record typedef alias mappings (e.g., 'size_t' → 'int').
        self.struct_env = {}  # Record struct fields (e.g., 'Point' -> {'x': 'int', 'y': 'real'}).
        self.typedef_is_ptr = set() # Record whether the typedef is a pointer.
        self.all_globals = set()  
        # Register the global memory model.
        self.all_globals.update(["Mem_int", "Mem_real", "Allocator", "Alloc", "Size"])
        self.output_global.extend([
            "var Mem_int: [int]int;    ",
            "var Mem_real:[int]real;  ",
            "var Allocator: int;       ",
            "var Alloc: [int]bool;     ",
            "var Size:[int]int;       "
        ])
        self.var_types.update({
            "Mem_int": "[int]int",
            "Mem_real": "[int]real",
            "Allocator": "int",
            "Alloc": "[int]bool",
            "Size": "[int]int"
        })
        self.current_modifies = set() #Which global variables are modified by the current function
        self.vis_vars = set() # Used to dynamically track live variables within the current scope
        self.loop_counter = 0 
        self.call_counter = 0  
        
        self.extracted_procedures = []

        # Context information within the current function (filled by visit_FuncDef)
        self.current_func_params = set()
        self.current_func_locals = set()
        self.loop_specs = {}
        self.emitted_safety_checks = set()
        # [PATCH L3] parsed `insertions` object, plus any replay complaints for the caller to log
        self.insertions = InsertionSpec(None)
        self.insertion_problems = []
        self.stmt_label_counter = 0
        # [PATCH T-B2] continue support: stack of [label, used] per enclosing loop, the ids of
        # for-loop increment nodes, and a counter for inline-loop labels.
        self.continue_stack = []
        self.for_increments = set()
        self.inline_loop_seq = 0
        # [PATCH T-C1] goto handling: labels visible in the current Boogie procedure (a stack,
        # one entry per extracted loop_k), and the function's return-like labels.
        self.label_scopes = []
        self.current_return_labels = {}
        try:
            if isinstance(specs_json_str, dict):
                raw_map = specs_json_str
            else:
                clean_json = specs_json_str.replace("```json", "").replace("```", "").strip()
                raw_map = json.loads(clean_json)

            if "global" in raw_map:
                global_defs = raw_map["global"]
                if isinstance(global_defs, list):
                    self.boogie_axioms = global_defs
                elif isinstance(global_defs, str):
                    self.boogie_axioms = [global_defs]
        except:
            print("JSON parsing failed; default parameter 'true' will be used.")
            self.loop_specs = {}
            raw_map = {}

        # [PATCH L3] Parse insertions OUTSIDE the contract loop below, otherwise "insertions"
        # would be mistaken for a procedure name and get a bogus {requires:true,...} entry.
        try:
            self.insertions = InsertionSpec(raw_map.get("insertions"))
        except ContractError as e:
            # A malformed `insertions` object is a contract mistake, not a translator bug: drop the
            # insertions and record why. The round then simply fails to prove its lemma goals,
            # which is exactly the signal the CEGIS loop needs.
            self.insertions = InsertionSpec(None)
            self.insertion_problems.append(str(e))

        try:
            for loop_id, specs in raw_map.items():
                if loop_id in ("global", "insertions"):
                    continue
                if specs is None: 
                    specs = {} 
                processed_spec = {
                    "requires": [],
                    "ensures": [],
                    "invariants": []
                }
                for key in ["requires", "ensures", "invariants"]:
                    k_list = specs.get(key, [])
                    if isinstance(k_list, str): k_list = [k_list] 
                    if k_list: 
                        cleaned_list =[clean_brackets(i) for i in k_list]
                        final_list = []
                        for expr in cleaned_list:
                            expr = re.sub(r'(\d+e[+-]?\d+)(?!\.)', r'\1.0', expr)
                            
                            final_list.append(expr)
                        
                        combined_expr = " && ".join([f"({i})" for i in final_list])
                        
                        if "real" in str(self.var_types.values()): 
                            processed_spec[key] = combined_expr.replace('%',' mod ')
                        else:
                            processed_spec[key] = combined_expr.replace('/',' div ').replace('%',' mod ')
                    else:
                        processed_spec[key] = "true"
                self.loop_specs[str(loop_id)] = processed_spec
        except ContractError as e:
            # A malformed `insertions` object is a contract mistake, not a translator bug: drop the
            # insertions and record why. The round then simply fails to prove its lemma goals,
            # which is exactly the signal the CEGIS loop needs.
            self.insertions = InsertionSpec(None)
            self.insertion_problems.append(str(e))
        except:
            print("JSON parsing failed; default parameter 'true' will be used.")
            self.loop_specs = {}

    def _make_indent(self):
        return '  ' * self.indent_level

    def _emit(self, line):
        """Emit a SKELETON statement: a line produced by translating the C program.

        [PATCH L2] Every skeleton statement carries a stable `// #N` label. Inserted text never
        does. The labels are what the LLM anchors its lemma calls to, so they must satisfy two
        properties:

          1. Stability across rounds. Each round re-translates the same C code, so label N is
             always the same C statement.
          2. Stability across MODES. The skeleton is re-emitted four different ways (plain, smoke,
             with_snap, FULLSNAP) and those differ by *hundreds* of injected snapshot lines. A
             physical line number would therefore mean different things in different modes, and an
             anchor taken from the plain skeleton would land somewhere else in FULLSNAP — which is
             the exact file the counterexample report is generated from. Numbering is therefore
             per-procedure and counts only skeleton statements, so the label of a given statement
             is identical in every mode. Insertions are replayed by *searching for the label*, not
             by offsetting, which is what makes that work.
        """
        label = f"  // #{self.stmt_label_counter}"
        self.stmt_label_counter += 1
        # Some emitters append their own trailing comment (`// CallID: n`, `// [Safety] ...`).
        # Boogie treats everything after the first `//` as comment, so a label appended at the very
        # end would be invisible in the *file* even though LABEL_RE (anchored at end of line) still
        # finds it. Put the label in front of any existing comment so that a human, the LLM, and the
        # counterexample reader all see it in the same place.
        first_comment = line.find("//")
        if first_comment != -1:
            line = line[:first_comment].rstrip() + label + "  " + line[first_comment:]
            self.current_stmts.append(self._make_indent() + line)
        else:
            self.current_stmts.append(self._make_indent() + line + label)

    def _replay_captured(self, captured):
        """Re-emit statements that were first produced into a scratch buffer.

        Used by visit_While, which evaluates the loop condition into a buffer and replays those
        statements before the loop and again at the end of the body. Strips the stale `// #N`
        label each line got the first time, keeps structural braces unlabelled, and preserves the
        relative indentation inside the captured block (the guarded block that the short-circuit
        translation can produce). The old `self._emit(stmt.strip())` flattened such blocks.
        """
        if not captured:
            return
        def lead(s):
            return len(s) - len(s.lstrip())
        base = min(lead(s) for s in captured if s.strip())
        for stmt in captured:
            text = re.sub(r'\s*//\s*#\d+(?=\s|$)', '', stmt.rstrip()).strip()
            if not text:
                continue
            extra = max(0, (lead(stmt) - base) // 2)
            self.indent_level += extra
            if text in ('{', '}'):
                self._emit_unlabelled(text)
            else:
                self._emit(text)
            self.indent_level -= extra

    def _emit_unlabelled(self, line):
        """Emit an injected/synthesised line that must NOT consume a skeleton label.

        Used by the snapshot inserters: their lines are verification scaffolding, absent from the
        plain skeleton the LLM was shown, so giving them labels would desynchronise the numbering
        between modes.
        """
        self.current_stmts.append(self._make_indent() + line)

    def _resolve_base_type(self, node):
        """Recursively unwrap AST nodes to obtain the underlying C basic type."""
        if isinstance(node, c_ast.IdentifierType):
            name = node.names[0]
            if name in self.typedef_env:
                return self.typedef_env[name]
            if 'void' in node.names:
                return 'void'
            if 'float' in node.names or 'double' in node.names:
                return 'real'
            return 'int'
        
        elif isinstance(node, c_ast.Struct):
            return f"struct {node.name}" if node.name else "struct_anon"
        elif hasattr(node, 'type'):
            return self._resolve_base_type(node.type)
        return 'int'
    
    def _is_pointer_type(self, type_node):
        if isinstance(type_node, c_ast.PtrDecl):
            return True
        if isinstance(type_node, c_ast.TypeDecl) and isinstance(type_node.type, c_ast.IdentifierType):
            name = type_node.type.names[0]
            if name in getattr(self, 'typedef_is_ptr', set()):
                return True
        return False

    def _get_exit_label(self):
        proc_name = getattr(self, "current_proc_name", "default")
        return f"EXIT_{proc_name}"

    def _sizeof(self, type_node):
        if isinstance(type_node, c_ast.ArrayDecl):
            if type_node.dim:
                dim_expr = str(self._visit_expr(type_node.dim))
            else:
                dim_expr = "1"
                
            elem_size_expr = str(self._sizeof(type_node.type))
            
            if elem_size_expr == "1":
                return dim_expr
            elif dim_expr == "1":
                return elem_size_expr
            else:
                return f"({dim_expr} * {elem_size_expr})"
            
        elif self._is_pointer_type(type_node):
            return 1 
        else:
            base = self._resolve_base_type(type_node)
            if base.startswith("struct "):
                struct_name = base.split(" ")[1]
                if struct_name in self.struct_env:
                    return self.struct_env[struct_name]['size']
            return 1 

    def _is_escaped(self, var_name):
        """Determine whether a variable has escaped due to its address being taken (based on precise scope)."""
        if not hasattr(self, 'escaped_vars'):
            return False
            
        # Note: we use original_func_name here, so that even if loop_0 is extracted, we can still look up variables in the original function (e.g., main).
        curr_func = getattr(self, "original_func_name", "global")
        
        if var_name in self.current_func_locals or var_name in self.current_func_params:
            return var_name in self.escaped_vars.get(curr_func, set())
            
        if var_name in self.all_globals:
            return any(var_name in evs for evs in self.escaped_vars.values())
            
        return var_name in self.escaped_vars.get(curr_func, set())

    def _register_struct(self, struct_node):
        if struct_node.decls: 
            struct_name = struct_node.name
            if struct_name and struct_name not in self.struct_env:
                self.struct_env[struct_name] = {'size': 0, 'fields': {}}
                current_offset = 0
                
                for field in struct_node.decls:
                    field_name = field.name
                    field_base_type = self._resolve_base_type(field.type)
                    
                    is_ptr = self._is_pointer_type(field.type)
                    is_arr = isinstance(field.type, c_ast.ArrayDecl)
                    
                    field_size_expr = str(self._sizeof(field.type))
                    try:
                        field_size = eval(field_size_expr)
                    except:
                        field_size = 1 
                    
                    self.struct_env[struct_name]['fields'][field_name] = {
                        'offset': current_offset,
                        'b_type': field_base_type,
                        'is_ptr': is_ptr,  
                        'is_arr': is_arr   
                    }
                    current_offset += field_size 
                    
                self.struct_env[struct_name]['size'] = current_offset

    def _get_boogie_type(self, type_node, var_name=None):
        """Map C type nodes to Boogie types: int, real, [int]int, and [int]real."""
        base = self._resolve_base_type(type_node)

        is_ptr = self._is_pointer_type(type_node)
        is_arr = isinstance(type_node, c_ast.ArrayDecl)
        is_struct = base.startswith("struct ")

        is_escaped = False
        if var_name:
            is_escaped = self._is_escaped(var_name)
        
        if var_name and (is_ptr or is_arr or is_struct or is_escaped):
            if is_arr and isinstance(type_node.type, c_ast.PtrDecl):
                self.ptr_target_types[var_name] = "ptr_to_" + base
            else:
                self.ptr_target_types[var_name] = base
            
        # If it is an array, pointer, or struct instance, it is merely an address (int) in Boogie.
        if is_ptr or is_arr or is_struct or is_escaped:
            return "int"
        else:
            return base

    def _get_struct_name(self, node):
        if isinstance(node, c_ast.ID):
            t = self.ptr_target_types.get(node.name, "")
            if t.startswith("struct "):
                return t.split(" ")[1] 
        elif isinstance(node, c_ast.ArrayRef):
            return self._get_struct_name(node.name)
        elif isinstance(node, c_ast.UnaryOp) and node.op == '*':
            return self._get_struct_name(node.expr)
        
        elif isinstance(node, c_ast.StructRef):
            parent_struct = self._get_struct_name(node.name)
            if parent_struct in self.struct_env and node.field.name in self.struct_env[parent_struct]['fields']:
                b_type = self.struct_env[parent_struct]['fields'][node.field.name]['b_type']
                if b_type.startswith("struct "):
                    return b_type.split(" ")[1]
        return "UNKNOWN_STRUCT"

    def visit_Typedef(self, node):
        if hasattr(node.type, 'type') and isinstance(node.type.type, c_ast.Struct):
            struct_node = node.type.type
            if not struct_node.name:
                struct_node.name = node.name 
            self._register_struct(struct_node)
        
        base_type = self._resolve_base_type(node.type)
        self.typedef_env[node.name] = base_type
        
        if self._is_pointer_type(node.type):
            self.typedef_is_ptr.add(node.name)

    def _is_real_expr(self, expr_str):
        """Lightweight heuristic: check whether the expression string contains real variables or decimal constants."""
        expr_str = str(expr_str)
        if re.search(r'\d+\.\d+', expr_str):
            return True
        if "real(" in expr_str: 
            return True
        words = re.findall(r'\b[a-zA-Z_]\w*\b', expr_str)
        for w in words:
            t = self.var_types.get(w, "int")
            if "real" in t:
                return True
        return False
    
    def _ensure_real_literal(self, expr_str):
        expr_str = str(expr_str)
        
        if re.fullmatch(r'-?\d+', expr_str):
            return f"{expr_str}.0"
        
        if '.' in expr_str or expr_str.startswith("real("):
            return expr_str
            
        words = re.findall(r'\b[a-zA-Z_]\w*\b', expr_str)
        if words:
            all_real = True
            for w in words:
                if w in ["Mem_int", "Mem_real", "Alloc", "Size", "Allocator", "old"]:
                    continue
                t = self.var_types.get(w, "int")
                if "real" not in t:
                    all_real = False
                    break
            if not all_real:
                return f"real({expr_str})"
                
        return expr_str
    
    def _new_temp(self, b_type="int"):
        name = f"t{self.tmp_count}"
        self.tmp_count += 1
        
        self.current_decls.append(f"var {name}: {b_type};")
        self.var_types[name] = b_type 
        return name
    
    # [PATCH T-B3] Value range of each SV-COMP nondet generator. The old classifier only recognised
    # the substrings uint/bool/uchar/unsigned, so nondet_ushort / nondet_ulong / nondet_size_t were
    # treated as signed and unbounded (not even `>= 0`), and no type had an UPPER bound. Both
    # admit values the C function can never return, which makes true properties unprovable
    # (e.g. `s <= n*(2^32-1)` for a uint `v`). Bounding a freshly havoc'd value by its own type's
    # range only removes impossible behaviours, so this is sound.
    # `long` uses the LP64 width: SV-COMP mixes ILP32 and LP64 tasks, and the wider range is a
    # superset of the narrower one, so it stays sound for both data models.
    # Signed int / long / long long are deliberately left UNBOUNDED: the framework models C `int`
    # as a mathematical integer throughout (no overflow), and bounding only the nondet source of
    # those types would touch almost every program for no fidelity gain under that abstraction.
    _NONDET_RANGES = {
        "bool": (0, 1), "_bool": (0, 1),
        "char": (-128, 127), "schar": (-128, 127), "uchar": (0, 255),
        "short": (-32768, 32767), "ushort": (0, 65535),
        "int": None, "long": None, "longlong": None,
        "uint": (0, 4294967295),
        "unsigned": (0, 4294967295),
        "ulong": (0, 18446744073709551615),
        "ulonglong": (0, 18446744073709551615),
        "size_t": (0, 18446744073709551615),
    }

    def _nondet_range(self, node):
        """(lo, hi) for a nondet call, or None when the value is unbounded/real/pointer."""
        if not (isinstance(node, c_ast.FuncCall) and isinstance(node.name, c_ast.ID)):
            return None
        name = node.name.name
        if "nondet" not in name:
            return None
        suffix = name.split("nondet", 1)[1].lstrip("_").lower()
        if suffix in self._NONDET_RANGES:
            return self._NONDET_RANGES[suffix]
        # Unknown generator: keep the old conservative behaviour (non-negative if it looks
        # unsigned, otherwise unconstrained).
        if suffix.startswith("u") or "unsigned" in suffix:
            return (0, None)
        return None

    def _emit_nondet_range(self, target, node):
        """Emit the range assumption for a just-havoc'd nondet target, if it has one."""
        rng = self._nondet_range(node)
        if not rng:
            return
        lo, hi = rng
        parts = []
        if lo is not None:
            parts.append(f"{target} >= {lo}")
        if hi is not None:
            parts.append(f"{target} <= {hi}")
        if parts:
            self._emit(f"assume {' && '.join(parts)};")

    def _is_nondet_call(self, node):
        if isinstance(node, c_ast.FuncCall) and isinstance(node.name, c_ast.ID):
            name = node.name.name
            if "nondet" not in name:
                return None

            if "float" in name or "double" in name:
                return 3 # real
            elif self._nondet_range(node) and self._nondet_range(node)[0] == 0:
                return 2 # unsigned (range assumption emitted by _emit_nondet_range)
            else:
                return 1 # signed int (range assumption emitted by _emit_nondet_range)

        return None

    def visit_FileAST(self, node):
        at_visitor = AddressTakenVisitor()
        at_visitor.visit(node)
        self.escaped_vars = at_visitor.escaped_vars

        # [PATCH T-C4] Resolve every typedef BEFORE computing function return types. Previously the
        # return-type table was filled first, so in `typedef double NUM; NUM y(...)` the name NUM
        # was still unknown, fell back to `int`, and the procedure became `returns (result: int)`
        # while its body assigned a real -- a type error, and a wrong model of the C program.
        # visit_Typedef only updates typedef_env / struct_env / typedef_is_ptr and emits no code, so
        # running it here and again in the main pass below is harmless.
        for ext in node.ext:
            if isinstance(ext, c_ast.Typedef):
                self.visit(ext)

        for ext in node.ext:
            if isinstance(ext, c_ast.FuncDef):
                f_name = ext.decl.name
                try:
                    raw_ret_type_node = ext.decl.type.type
                    f_ret_type = self._get_boogie_type(raw_ret_type_node)
                except:
                    f_ret_type = "int" 
                self.func_ret_types[f_name] = f_ret_type
        for ext in node.ext:
            if isinstance(ext, c_ast.FuncDef):
                self.visit(ext)
            elif isinstance(ext, c_ast.Typedef):  
                self.visit(ext)
            
            elif isinstance(ext, c_ast.Decl):
                if isinstance(ext.type, c_ast.FuncDecl):
                    continue
                
                if isinstance(ext.type, c_ast.Struct):
                    self._register_struct(ext.type)
                
                
                if not ext.name:
                    continue
                
                var_name = ext.name
                is_escaped = self._is_escaped(var_name)
                if var_name in ["true", "false", "result"]:
                    var_name = var_name + "_c" 
                
                self.all_globals.add(var_name)
                
                boogie_type = self._get_boogie_type(ext.type, var_name=var_name)
                self.var_types[var_name] = boogie_type 
                self.output_global.append(f"var {var_name}: {boogie_type};")
                
                base_type = self._resolve_base_type(ext.type)
                is_ptr = self._is_pointer_type(ext.type)
                is_arr = isinstance(ext.type, c_ast.ArrayDecl)
                
                if is_arr or (base_type.startswith("struct ") and not is_ptr) or is_escaped:
                    size_val = self._sizeof(ext.type)
                    
                    self.global_inits.append(f"  {var_name} := Allocator; ")
                    self.global_inits.append(f"  Allocator := Allocator + {size_val};")
                    self.global_inits.append(f"  Alloc[{var_name}] := true;")
                    self.global_inits.append(f"  Size[{var_name}] := {size_val};")
                
                
                if ext.init:
                    
                    val = self._visit_expr(ext.init)
                    if "real" in boogie_type:
                        val = self._ensure_real_literal(val)
                    
                    if is_escaped:
                        mem_map = "Mem_real" if "real" in boogie_type else "Mem_int"
                        self.global_inits.append(f"  {mem_map}[{var_name}] := {val};")
                    else:
                        self.global_inits.append(f"  {var_name} := {val};")

    def visit_FuncDef(self, node):
        self.vis_vars = set()
        
        self.var_types = {k: v for k, v in self.var_types.items() if k in self.all_globals or k.startswith("Struct_")}
        self.ptr_target_types = {k: v for k, v in self.ptr_target_types.items() if k in self.all_globals}
        # =========================================================
        self.current_modifies = set()
        self.current_decls = []
        self.current_stmts = []
        # [PATCH L2] Labels are per-procedure: `insertions` is keyed by procedure name, so #N only
        # ever has to be unique within one procedure.
        self.stmt_label_counter = 0
        self.current_func_params = set()
        self.current_func_locals = set()

        # [PATCH T-C1] goto bookkeeping for this function
        self.current_return_labels = _return_labels(node.body)
        self.label_scopes = [_labels_in(node.body)]
        
        func_name = node.decl.name
        
        self.current_proc_name = func_name
        
        self.original_func_name = func_name
        
        ignored_funcs = ["__VERIFIER_assert", "__VERIFIER_error", "reach_error", 
                         "assume_abort_if_not", "__VERIFIER_nondet_int", 
                         "__VERIFIER_nondet_uint", "__VERIFIER_nondet_bool",
                         "printf", "exit", "abort"]
        if func_name in ignored_funcs or func_name.startswith("__VERIFIER_"):
            return

        if func_name == "main":
            
            self._emit("Allocator := 1; // Initialize memory allocator")
            self.current_modifies.add("Allocator")
            for init_stmt in self.global_inits:
                self._emit(init_stmt)
                
                g_name = init_stmt.strip().split(' ')[0]
                g_name = g_name.split('[')[0] 

                if g_name in self.all_globals:
                    self.current_modifies.add(g_name)
        
        params = []
        param_init_stmts = []
        if node.decl.type.args:
            for param in node.decl.type.args.params:
                
                p_name = param.name
                if p_name in ["true", "false", "result"]:  # [PATCH B4] same renaming as locals
                    p_name = p_name + "_c"
                if p_name:
                    self.vis_vars.add(p_name)
                    
                    p_type = self._get_boogie_type(param.type, var_name=p_name)
                    
                    self.var_types[p_name] = p_type
                    self.current_func_params.add(p_name)
                    
                    params.append(f"in_{p_name}: {p_type}")
                    
                    self.current_decls.append(f"var {p_name}: {p_type};")
                    
                    param_init_stmts.append(f"{p_name} := in_{p_name};")
        
        params_str = ", ".join(params)
        
        ret_type = self.func_ret_types.get(func_name, "int")
        is_void = (ret_type == "void")
        
        self.current_func_ret_type = ret_type 

        sig = f"procedure {func_name}({params_str})"
        if not is_void:
            sig += f" returns (result: {ret_type})"

        specs = self.loop_specs.get(func_name, {"requires": "true", "ensures": "true", "invariants": "true"})
        req_str = specs["requires"]
        ens_str = specs["ensures"]
        
        sig += f"\n  requires {req_str};"
        sig += f"\n  ensures {ens_str};"
        self.indent_level += 1
        
        self.current_decls.append("var has_returned: int;")
        self._emit("has_returned := 0;")
        self.current_func_locals.add("has_returned")
        
        if is_void:
            # Even for void, we also add a dummy result to unify the handling of return logic.
            self.current_decls.append("var result: int;")
        self.current_func_locals.add("result")
        
        for stmt in param_init_stmts:
            self._emit(stmt)
        
        self._hook_proc_entry(func_name)
        
        if node.body:
            self.visit(node.body)
        
        self.indent_level -= 1
        
        self._emit(f"{self._get_exit_label()}:")
        self.indent_level += 1
        self._hook_proc_exit(func_name)
        self._emit("return;")
        self.indent_level -= 1

        if self.current_modifies:
            mods = ", ".join(sorted(self.current_modifies))  # [PATCH B2] deterministic order (was set order, varied with PYTHONHASHSEED)
            sig += f"\nmodifies {mods};"
        
        self.output.append(sig)
        self.output.append("{")
        
        for decl in self.current_decls:
            self.output.append("  " + decl)
        
        if self.smoke_test:
            self.output.append("  assert false; // [SMOKE TEST] Check for Vacuous Truth")
        
        for stmt in self.current_stmts:
            self.output.append(stmt)
        self.output.append("}")

    def visit_Decl(self, node):
        if isinstance(node.type, c_ast.Struct):
            self._register_struct(node.type)
            
        if not node.name:
            return
        if not isinstance(node.type, c_ast.FuncDecl):
            if node.name:
                self.vis_vars.add(node.name)
        var_name = node.name
        is_escaped = self._is_escaped(var_name)
        
        if var_name in ["true", "false", "result"]:
             var_name = var_name + "_c"
        
        is_already_declared = var_name in self.current_func_locals
        self.current_func_locals.add(var_name)
        
        boogie_type = self._get_boogie_type(node.type, var_name=var_name) 
            
        self.var_types[var_name] = boogie_type
        
        if not is_already_declared:
            self.current_decls.append(f"var {var_name}: {boogie_type};")

            base_type = self._resolve_base_type(node.type)
            is_ptr = self._is_pointer_type(node.type)
            is_arr = isinstance(node.type, c_ast.ArrayDecl)
            
            if is_arr or (base_type.startswith("struct ") and not is_ptr) or is_escaped:
                size_val = self._sizeof(node.type)
                self._emit(f"{var_name} := Allocator; ")
                self._emit(f"Allocator := Allocator + {size_val};")
                self._emit(f"Alloc[{var_name}] := true;")
                self._emit(f"Size[{var_name}] := {size_val};")
                
                self.current_modifies.update(["Allocator", "Alloc", "Size"])

        if node.init:
            base_type = self._resolve_base_type(node.type)
            is_ptr = self._is_pointer_type(node.type)  # [PATCH B4] was only bound when first declared
            
            if base_type.startswith("struct ") and not is_ptr:
                rhs_addr = self._visit_expr(node.init)
                struct_name = base_type.split(" ")[1]
                
                if struct_name in self.struct_env:
                    fields = self.struct_env[struct_name]['fields']
                    for f_name, f_info in fields.items():
                        offset = f_info['offset']
                        b_type = f_info['b_type']
                        mem_map = "Mem_real" if b_type == "real" else "Mem_int"
                        self.current_modifies.add(mem_map)
                        self._emit(f"{mem_map}[{var_name} + {offset}] := {mem_map}[{rhs_addr} + {offset}];")
            else:
                # [PATCH B4] Scalar / pointer initialiser.  v2 had three defects here:
                #  (a) a non-escaped `T x = e;` emitted `x := e;` twice;
                #  (b) an escaped (address-taken) `T x = e;` emitted `x := e;` BEFORE the
                #      store `Mem[x] := e;`, overwriting the address that x holds with the
                #      value, so &x became e (unsound: `int x = 0; assert(&x == 0);` was
                #      proved).  A nondet initialiser did `havoc x;`, losing the address too;
                #  (c) it used node.name, ignoring the renaming of true/false/result to *_c,
                #      so `int result = 3;` assigned the procedure's return variable.
                # An escaped variable keeps its address; only the cell Mem[x] is written.
                mem_map = "Mem_real" if "real" in boogie_type else "Mem_int"
                nd = self._is_nondet_call(node.init)
                if nd:
                    if is_escaped:
                        temp = self._new_temp(b_type=("real" if nd == 3 else "int"))
                        self._emit(f"havoc {temp};")
                        self._emit_nondet_range(temp, node.init)
                        self._emit(f"{mem_map}[{var_name}] := {temp};")
                        self.current_modifies.add(mem_map)
                    else:
                        self._emit(f"havoc {var_name};")
                        self._emit_nondet_range(var_name, node.init)
                else:
                    # Note: C array initialization like int a[] = {1,2} is complex (InitList).
                    # For simplicity, we currently skip brace-enclosed array initializers and only handle assignments to ordinary variables/pointers.
                    if not isinstance(node.type, c_ast.ArrayDecl):
                        val = self._visit_expr(node.init)
                        if "real" in boogie_type and not self._is_real_expr(val):
                            val = self._ensure_real_literal(val)
                        if is_escaped:
                            self._emit(f"{mem_map}[{var_name}] := {val};")
                            self.current_modifies.add(mem_map)
                        else:
                            self._emit(f"{var_name} := {val};")

    def visit_Goto(self, node):
        target_name = node.name.lower()

        if "error" in target_name or "fail" in target_name or "abort" in target_name or "_exit" in target_name:
            self._emit("assert false; ")
            self._emit("assume false; // halt execution")
            return

        # [PATCH T-C1] Decide structurally instead of by a list of label names.
        # The old code turned `goto out/end/exit/done/...` into `has_returned := 1; goto EXIT_<p>;`
        # whatever the label actually did. Inside an extracted loop_k that jumped to an
        # EXIT_loop_k label that was never generated (VariableAnalyzer used a different,
        # case-sensitive name list, so `has_returned` was not an output) -> Boogie parse error.
        # At function level it also skipped the labelled statement itself (e.g. `END: return 0;`
        # lost its `result := 0`).
        #  1. Target label is in the same Boogie procedure -> a plain `goto` is exact.
        #  2. Target is outside the current loop_k and its statement is a return (or the label ends
        #     the function) -> translate it as that return. visit_Return sets has_returned and
        #     jumps to EXIT_loop_k; the caller re-checks has_returned after the call, and the
        #     analyzer (same rule) made has_returned/result outputs of the loop.
        #  3. Anything else -> plain goto (unchanged behaviour; Boogie will report it).
        scope = self.label_scopes[-1] if self.label_scopes else set()
        if node.name in scope:
            self._emit(f"goto {node.name};")
            return
        ret = self.current_return_labels.get(node.name)
        if ret is not None:
            self.visit_Return(ret)
            return
        self._emit(f"goto {node.name};")

    def visit_Label(self, node):
        
        self._emit(f"{node.name}:")
        
        if node.stmt:
            self.visit(node.stmt)
        else:
            self._emit("assume true; // dummy statement for label")

    def visit_Assignment(self, node):
        self.emitted_safety_checks.clear()
        
        lhs_type_raw = self._resolve_base_type(node.lvalue)
        
        if lhs_type_raw.startswith("struct "):
            struct_name = lhs_type_raw.split(" ")[1]
            
            lhs_addr = self._visit_expr(node.lvalue)
            rhs_addr = self._visit_expr(node.rvalue)
            
            if struct_name in self.struct_env:
                fields = self.struct_env[struct_name]['fields']
                for f_name, f_info in fields.items():
                    offset = f_info['offset']
                    b_type = f_info['b_type']
                    mem_map = "Mem_real" if b_type == "real" else "Mem_int"
                    
                    self.current_modifies.add(mem_map)
                    
                    self._emit(f"{mem_map}[{lhs_addr} + {offset}] := {mem_map}[{rhs_addr} + {offset}];")
                return 
        
        var_name = None
        if isinstance(node.lvalue, c_ast.ID):
            var_name = node.lvalue.name
        
        if var_name and var_name in self.all_globals:
                self.current_modifies.add(var_name)

        # [PATCH T-A1] The FuncCall fast path below assumes plain `=`. For a compound assignment
        # `s += f(x)` it used to emit `call s := f(x);`, silently dropping the `+` (UNSOUND:
        # `s = 3; s += f(2); assert(s == 2);` was proved). Route compound assignments through the
        # generic path at the end of this method instead: there `_visit_expr` turns the call into
        # `call tK := f(x);` and the result is combined as `s := (s + tK);`.
        if isinstance(node.rvalue, c_ast.FuncCall) and node.op == '=':
            
            func_name = ""
            if isinstance(node.rvalue.name, c_ast.ID):
                func_name = node.rvalue.name.name
            
            if "alloc" in func_name:
                lhs = self._visit_expr(node.lvalue)
                
                self.current_modifies.update(["Allocator", "Alloc", "Size"])
                
                map_name = lhs.split('[')[0]
                if map_name.startswith("Mem_"):
                    self.current_modifies.add(map_name)
                
                if func_name == "calloc" and node.rvalue.args and len(node.args.exprs) == 2:
                    arg1 = self._visit_expr(node.rvalue.args.exprs[0])
                    arg2 = self._visit_expr(node.rvalue.args.exprs[1])
                    size_expr = f"({arg1} * {arg2})"
                elif node.rvalue.args:
                    size_expr = self._visit_expr(node.rvalue.args.exprs[0])
                else:
                    size_expr = "1"
                    
                
                target = lhs
                if not isinstance(node.lvalue, c_ast.ID):
                    target = self._new_temp()
                
                self._emit(f"{target} := Allocator; ")
                self._emit(f"Allocator := Allocator + {size_expr};")
                self._emit(f"Alloc[{target}] := true;")
                self._emit(f"Size[{target}] := {size_expr};")
                
                if target != lhs:
                    self._emit(f"{lhs} := {target};")
                return
            
            nondet_type = self._is_nondet_call(node.rvalue)
            if nondet_type:
                
                lhs = self._visit_expr(node.lvalue)
                
                map_name = lhs.split('[')[0]
                if map_name.startswith("Mem_"):
                    self.current_modifies.add(map_name)
                
                b_t = "real" if nondet_type == 3 else "int"
                
                if isinstance(node.lvalue, c_ast.ID):

                    self._emit(f"havoc {lhs};")
                    self._emit_nondet_range(lhs, node.rvalue)
                else:
                    temp = self._new_temp(b_type=b_t)
                    self._emit(f"havoc {temp};")
                    self._emit_nondet_range(temp, node.rvalue)

                    self._emit(f"{lhs} := {temp};")
                return
            else:
                if func_name not in ["assume_abort_if_not", "reach_error"] or "VERIFIER_" not in func_name:
                    self.current_modifies.update(["Mem_int", "Mem_real", "Allocator", "Alloc", "Size"])
                
                args_str = []
                if node.rvalue.args:
                    for arg in node.rvalue.args.exprs:
                        args_str.append(self._visit_expr(arg))
                
                lhs = self._visit_expr(node.lvalue)

                current_call_id = str(self.call_counter)
                self.call_counter += 1
                self._hook_pre_call(current_call_id, func_name)

                if isinstance(node.lvalue, c_ast.ID):
                    self._emit(f"call {lhs} := {func_name}({', '.join(args_str)}); // CallID: {current_call_id}")
                    self._hook_post_call(current_call_id, func_name, return_var=lhs)
                else:
                    # [PATCH T-C5] Boogie only allows VARIABLES on the left of `call`. For a heap
                    # lvalue (`s1->n = f();`, `*p = f();`, `a[i] = f();`) the old code emitted
                    # `call Mem_int[(s1 + 1)] := f();`, which Boogie rejects ("invalid
                    # CallParams"). Call into a temp of the callee's return type, then store it.
                    ret_t = self.func_ret_types.get(func_name, "int")
                    temp = self._new_temp(b_type=ret_t)
                    self._emit(f"call {temp} := {func_name}({', '.join(args_str)}); // CallID: {current_call_id}")
                    self._hook_post_call(current_call_id, func_name, return_var=temp)
                    map_name = lhs.split('[')[0]
                    if map_name.startswith("Mem_"):
                        self.current_modifies.add(map_name)
                    self._emit(f"{lhs} := {temp};")
                return
        
        rhs = self._visit_expr(node.rvalue)
        lhs = self._visit_expr(node.lvalue) 
        
        map_name = lhs.split('[')[0]  
        if map_name.startswith("Mem_"):
            self.current_modifies.add(map_name)

        if node.op == '=':
            is_real_lhs = self._is_real_expr(lhs)
            is_real_rhs = self._is_real_expr(rhs)
            if is_real_lhs or is_real_rhs:
                if not is_real_rhs:
                    rhs = self._ensure_real_literal(rhs)
            self._emit(f"{lhs} := {rhs};")
        else:
            math_op = node.op[:-1]
            
            is_real_lhs = self._is_real_expr(lhs)
            is_real_rhs = self._is_real_expr(rhs)
            if is_real_lhs or is_real_rhs:
                if not is_real_rhs:
                    rhs = self._ensure_real_literal(rhs)
                if math_op == '/': math_op = '/' 
            else:
                if math_op == '/': math_op = 'div'
                if math_op == '%': math_op = 'mod'
            
            self._emit(f"{lhs} := ({lhs} {math_op} {rhs});")

    def _ensure_bool(self, node, expr_str):
        if expr_str in ["true", "false"]:
            return expr_str
        
        if expr_str.startswith("(if ") and expr_str.endswith(" then 1 else 0)"):
            return expr_str[4:-15]
        return f"({expr_str} != 0)"
    
    def _visit_short_circuit(self, node):
        """Translate `a && b` / `a || b` preserving C's short-circuit evaluation.

        [PATCH T-B1] `_visit_expr` returns a pure Boogie expression and pushes any side statements
        it needs (safety asserts, `call tK := f(..)` for user calls, `havoc` for nondet calls) into
        the current statement buffer. The old code visited both operands eagerly, so the side
        statements of `b` ran UNCONDITIONALLY, before the `if`/`while` -- even when C never
        evaluates `b`. Typical breakage: `i < N && a[i] != 0` emitted `assert i < Size[a]` with
        `i == N` reachable; `p != 0 && p->f` emitted `assert p > 0`. Both make a TRUE program
        unprovable, and a hoisted call to a function containing `assume` can even prune real paths.

        Fix: evaluate `b` into a scratch buffer. If it produced no statements (the overwhelmingly
        common case) the output is byte-identical to before. Otherwise the statements are guarded:
            a && b   ->   if (a) { <stmts of b> }
            a || b   ->   if (!a) { <stmts of b> }
        and the value is the usual `(if (a op b) then 1 else 0)`. When b is not evaluated, its
        temporaries are never read: `a && b` with a false is false regardless of b's value.
        """
        op = node.op
        left = self._visit_expr(node.left)
        left_b = self._ensure_bool(node.left, left)

        outer = self.current_stmts
        saved_label = self.stmt_label_counter
        # Safety checks emitted for `left` dominate `right`, but checks emitted inside the guarded
        # block must not suppress identical checks later on paths where the block did not run.
        saved_checks = set(self.emitted_safety_checks)
        scratch = []
        self.current_stmts = scratch
        right = self._visit_expr(node.right)
        self.current_stmts = outer
        right_b = self._ensure_bool(node.right, right)

        if scratch:
            # Re-emit through the shared replay helper so the guarded statements get skeleton
            # labels in a stable order and nested structure keeps its indentation; rewind the
            # counter first so labels are not consumed twice.
            self.stmt_label_counter = saved_label
            guard = left_b if op == '&&' else f"!({left_b})"
            self._emit(f"if ({guard})")
            self._emit_unlabelled("{")
            self.indent_level += 1
            self._replay_captured(scratch)
            self.indent_level -= 1
            self._emit_unlabelled("}")
            self.emitted_safety_checks = saved_checks
        return f"(if ({left_b} {op} {right_b}) then 1 else 0)"

    def _visit_expr(self, node):
        if isinstance(node, c_ast.BinaryOp) and node.op in ('&&', '||'):
            return self._visit_short_circuit(node)
        if isinstance(node, c_ast.BinaryOp):

            left = self._visit_expr(node.left)
            right = self._visit_expr(node.right)
            op = node.op
            is_real_left = self._is_real_expr(left)
            is_real_right = self._is_real_expr(right)
            
            if is_real_left or is_real_right:
                if not is_real_left:
                    left = self._ensure_real_literal(left)
                if not is_real_right:
                    right = self._ensure_real_literal(right)
                if op == '/': op = '/' 
            else:
                if op == '/': op = 'div'
                elif op == '%': op = 'mod'
            
            if op in ['&&','||']:
                left = self._ensure_bool(node.left, left)
                right = self._ensure_bool(node.right, right)
                return f"(if ({left} {op} {right}) then 1 else 0)"
                
            if op in ['<', '>', '<=', '>=', '==', '!=']:
                return f"(if ({left} {op} {right}) then 1 else 0)"
            return f"({left} {op} {right})"
        
        elif isinstance(node, c_ast.Constant):
            val = str(node.value)
            if node.type not in ['float', 'double', 'char', 'string']:
                
                clean_val = val.rstrip('uUlL')
                try:
                    val = str(int(clean_val, 0))
                except ValueError:
                    pass 
            if node.type in ['float', 'double']:
                val = _boogie_real_literal(val)
            return val
        elif isinstance(node, c_ast.ID):
            if node.name == "NULL":
                return "0"
            BUILTIN_MACROS = {
                "LARGE_INT": "1000000",
                "MAX_INT": "2147483647",
                "INT_MAX": "2147483647",
                "INT_MIN": "-2147483648",
                "UINT_MAX": "4294967295"
            }
            
            if node.name in BUILTIN_MACROS:
                return BUILTIN_MACROS[node.name]
            var_name = node.name
            
            if self._is_escaped(var_name):
                b_t = self.var_types.get(var_name, "int")
                mem_map = "Mem_real" if "real" in b_t else "Mem_int"
                return f"{mem_map}[{var_name}]"
            
            if var_name in ["true", "false", "result"]:
                var_name = var_name + "_c"
            
            if (var_name not in self.all_globals and 
                var_name not in self.current_func_locals and 
                var_name not in self.current_func_params and
                not var_name.startswith("in_") and
                var_name not in self.var_types):
                
                self.all_globals.add(var_name)
                self.var_types[var_name] = "int"
                self.output_global.append(f"var {var_name}: int;")

            return var_name
        elif isinstance(node, c_ast.Cast):
            return self._visit_expr(node.expr)
        
        elif isinstance(node, c_ast.TernaryOp):
            
            cond_str = self._visit_expr(node.cond)
            cond_str = self._ensure_bool(node.cond, cond_str)
            
            
            iftrue_str = self._visit_expr(node.iftrue)
            iffalse_str = self._visit_expr(node.iffalse)
            
            is_real_t = self._is_real_expr(iftrue_str)
            is_real_f = self._is_real_expr(iffalse_str)
            if is_real_t or is_real_f:
                if not is_real_t: iftrue_str = self._ensure_real_literal(iftrue_str)
                if not is_real_f: iffalse_str = self._ensure_real_literal(iffalse_str)
            
            return f"(if {cond_str} then {iftrue_str} else {iffalse_str})"
        
        elif isinstance(node, c_ast.ArrayRef):
            arr_base = self._visit_expr(node.name)
            subscript = self._visit_expr(node.subscript)
            
            target_type = self.ptr_target_types.get(arr_base, "int")
            
            
            elem_size = 1
            if target_type.startswith("struct "):
                struct_name = target_type.split(" ")[1]
                if struct_name in self.struct_env:
                    elem_size = self.struct_env[struct_name]['size']
            
            addr_expr = f"({arr_base} + {subscript} * {elem_size})" if elem_size > 1 else f"({arr_base} + {subscript})"
            
            if target_type.startswith("struct ") or target_type.startswith("[int]"):
                return addr_expr
            
            safe_null = f"assert {arr_base} > 0; // [Safety] Null pointer deref"
            safe_uaf = f"assert Alloc[{arr_base}] == true; //[Safety] Use-After-Free"
            
            if safe_null not in self.emitted_safety_checks:
                self._emit(safe_null)
                self.emitted_safety_checks.add(safe_null)
            if safe_uaf not in self.emitted_safety_checks:
                self._emit(safe_uaf)
                self.emitted_safety_checks.add(safe_uaf)
            
            safe_bound = f"assert 0 <= {subscript} && {subscript} < Size[{arr_base}]; // [Safety] Buffer Overflow"
            if safe_bound not in self.emitted_safety_checks:
                self._emit(safe_bound)
                self.emitted_safety_checks.add(safe_bound)
            mem_map = "Mem_real" if target_type == "real" else "Mem_int"
            return f"{mem_map}[{addr_expr}]"
        
        elif isinstance(node, c_ast.StructRef):
            base_expr = self._visit_expr(node.name)
            field_name = node.field.name
            struct_name = self._get_struct_name(node.name)
            
            offset = 0
            b_type = "int"
            is_ptr = False  
            is_arr = False  
            if struct_name in self.struct_env and field_name in self.struct_env[struct_name]['fields']:
                field_info = self.struct_env[struct_name]['fields'][field_name]
                offset = field_info['offset']
                b_type = field_info['b_type']
                is_ptr = field_info.get('is_ptr', False) 
                is_arr = field_info.get('is_arr', False) 
            
            
            addr_expr = f"({base_expr} + {offset})" if offset > 0 else base_expr
            
            
            if (b_type.startswith("struct ") and not is_ptr) or is_arr:
                return addr_expr
            
            safe_null = f"assert {base_expr} > 0; // [Safety] Null pointer deref"
            safe_uaf = f"assert Alloc[{base_expr}] == true; //[Safety] Use-After-Free"
            
            if safe_null not in self.emitted_safety_checks:
                self._emit(safe_null)
                self.emitted_safety_checks.add(safe_null)
            if safe_uaf not in self.emitted_safety_checks:
                self._emit(safe_uaf)
                self.emitted_safety_checks.add(safe_uaf)
            mem_map = "Mem_real" if b_type == "real" and not is_ptr else "Mem_int"
            return f"{mem_map}[{addr_expr}]"
        
        elif isinstance(node, c_ast.UnaryOp):
            
            if node.op == 'sizeof':
                return str(self._sizeof(node.expr.type)) if hasattr(node.expr, 'type') else "1"
            
            if node.op == '&':
                operand_expr = self._visit_expr(node.expr)
                
                m = re.match(r'Mem_\w+\[(.*)\]', operand_expr)
                if m:
                    return m.group(1)
                return operand_expr 
            
            if node.op == '*':
                ptr_expr = self._visit_expr(node.expr)
                target_type = self.ptr_target_types.get(ptr_expr, "int")
                
                if target_type.startswith("struct "):
                    return ptr_expr
                    
                mem_map = "Mem_real" if target_type == "real" else "Mem_int"
                
                safe_null = f"assert {ptr_expr} > 0; // [Safety] Null pointer deref"
                safe_uaf = f"assert Alloc[{ptr_expr}] == true; //[Safety] Use-After-Free"
                
                if safe_null not in self.emitted_safety_checks:
                    self._emit(safe_null)
                    self.emitted_safety_checks.add(safe_null)
                if safe_uaf not in self.emitted_safety_checks:
                    self._emit(safe_uaf)
                    self.emitted_safety_checks.add(safe_uaf)
                return f"{mem_map}[{ptr_expr}]"
            
            var_name = self._visit_expr(node.expr) 
            if node.op in ['p++','p--','++','--']:
                if isinstance(node.expr, c_ast.ID):
                    if var_name in self.all_globals:
                        self.current_modifies.add(var_name)

            v_type = self.var_types.get(var_name, "int")
            step = "1.0" if "real" in v_type else "1"
            
            if node.op == 'p++': 
                temp = self._new_temp()     
                self._emit(f"{temp} := {var_name};")     
                self._emit(f"{var_name} := {var_name} + {step};") 
                return temp                
            elif node.op == 'p--':
                temp = self._new_temp()
                self._emit(f"{temp} := {var_name};")
                self._emit(f"{var_name} := {var_name} - {step};")
                return temp
            
            elif node.op == '++':
                self._emit(f"{var_name} := {var_name} + {step};") 
                return var_name             
            elif node.op == '--':
                self._emit(f"{var_name} := {var_name} - {step};") 
                return var_name 
            
           
            elif node.op == '-':
                return f"-{var_name}"
            elif node.op == '!':
                if var_name.startswith("(if ") and var_name.endswith(" then 1 else 0)"):
                    inner = var_name[4:-15]
                    return f"(if (!{inner}) then 1 else 0)"
                else:
                    zero_str = "0.0" if self._is_real_expr(var_name) else "0"
                    return f"(if ({var_name} == {zero_str}) then 1 else 0)"
        
        elif isinstance(node, c_ast.Assignment):
            rhs = self._visit_expr(node.rvalue)
            lhs = self._visit_expr(node.lvalue)
            
           
            map_name = lhs.split('[')[0]
            if map_name.startswith("Mem_") or map_name in self.all_globals:
                self.current_modifies.add(map_name)

           
            if node.op == '=':
                is_real_lhs = self._is_real_expr(lhs)
                is_real_rhs = self._is_real_expr(rhs)
                if is_real_lhs or is_real_rhs:
                    if not is_real_rhs:
                        rhs = self._ensure_real_literal(rhs)
                self._emit(f"{lhs} := {rhs};")
            else:
                math_op = node.op[:-1]
                is_real_lhs = self._is_real_expr(lhs)
                is_real_rhs = self._is_real_expr(rhs)
                if is_real_lhs or is_real_rhs:
                    if not is_real_rhs:
                        rhs = self._ensure_real_literal(rhs)
                    if math_op == '/': math_op = '/' 
                else:
                    if math_op == '/': math_op = 'div'
                    if math_op == '%': math_op = 'mod'
                self._emit(f"{lhs} := ({lhs} {math_op} {rhs});")
            
            
            return lhs
       
        elif isinstance(node, c_ast.FuncCall):
            
            func_name = ""
            if isinstance(node.name, c_ast.ID):
                func_name = node.name.name
            
            nondet_type = self._is_nondet_call(node)
            if nondet_type:
                
                b_t = "real" if nondet_type == 3 else "int"
                temp = self._new_temp(b_type=b_t)

                self._emit(f"havoc {temp};")
                self._emit_nondet_range(temp, node)
                return temp
            elif "alloc" in func_name:
                if func_name == "calloc" and node.args and len(node.args.exprs) == 2:
                    arg1 = self._visit_expr(node.args.exprs[0])
                    arg2 = self._visit_expr(node.args.exprs[1])
                    size_expr = f"({arg1} * {arg2})"
                elif node.args:
                    size_expr = self._visit_expr(node.args.exprs[0])
                else:
                    size_expr = "1"
                    
                self.current_modifies.update(["Allocator", "Alloc", "Size"])
                
                temp = self._new_temp()
                self._emit(f"{temp} := Allocator; ")
                self._emit(f"Allocator := Allocator + {size_expr};")
                self._emit(f"Alloc[{temp}] := true;")
                self._emit(f"Size[{temp}] := {size_expr};")
                return temp
            else:
                
                if func_name not in ["assume_abort_if_not", "reach_error"] or "VERIFIER_" not in func_name:
                    self.current_modifies.update(["Mem_int", "Mem_real", "Allocator", "Alloc", "Size"])
                
                args_str = []
                if node.args:
                    for arg in node.args.exprs:
                        args_str.append(self._visit_expr(arg))
                
               
                func_name = node.name.name if isinstance(node.name, c_ast.ID) else ""
                actual_ret_type = self.func_ret_types.get(func_name, "int")
                
                ret_temp = self._new_temp(b_type=actual_ret_type)
                current_call_id = str(self.call_counter)
                self.call_counter += 1
            
                self._hook_pre_call(current_call_id, func_name)
               
                self._emit(f"call {ret_temp} := {func_name}({', '.join(args_str)}); // CallID: {current_call_id}")
                self._hook_post_call(current_call_id, func_name, return_var=ret_temp)
                
                return ret_temp
        
        return "UNKNOWN"
    
    def visit_UnaryOp(self, node):
        var_name = self._visit_expr(node.expr)
        
        if node.op in ['p++', 'p--', '++', '--']:
            if isinstance(node.expr, c_ast.ID):
                if var_name in self.all_globals:
                    self.current_modifies.add(var_name)
                    
            v_type = self.var_types.get(var_name, "int")
            step = "1.0" if "real" in v_type else "1"

            if node.op in ['p++', '++']:
                self._emit(f"{var_name} := {var_name} + {step};")
            elif node.op in ['p--', '--']:
                self._emit(f"{var_name} := {var_name} - {step};")

    def _visit_loop_body(self, body, cont_label):
        """Visit a loop body with `continue` support. [PATCH T-B2]

        Emits the body, then -- only if a `continue` actually targeted this loop -- the label
        `cont_label:`, then the for-loop increment if the body ends in one. Keeping the increment
        after the label is what makes `continue` in a `for` run the increment, as in C.
        """
        increment = None
        if (isinstance(body, c_ast.Compound) and body.block_items
                and id(body.block_items[-1]) in self.for_increments):
            increment = body.block_items[-1]
            body = c_ast.Compound(block_items=body.block_items[:-1], coord=body.coord)
        self.continue_stack.append([cont_label, False])
        if body is not None:
            self.visit(body)
        _, used = self.continue_stack.pop()
        if used:
            self._emit_unlabelled(f"{cont_label}:")
        if increment is not None:
            self.visit(increment)

    def visit_Break(self, node):
        self._emit("break;")

    def visit_Continue(self, node):
        """[PATCH T-B2] C `continue` -> `goto CONTINUE_<loop>;`.

        There was no visit_Continue at all, so pycparser's generic_visit emitted NOTHING for a
        `continue` and control fell through into the rest of the loop body. In psyco_abp_1-3 that
        dropped 37 `continue`s and made the error state reachable in Boogie while the C program is
        safe. Boogie has no `continue`; a goto to a label placed at the end of the loop body (see
        _visit_loop_body) is the standard encoding. For a `for` loop the increment is emitted after
        that label, so `continue` still runs it, as C requires.
        """
        if not self.continue_stack:
            self._emit("assert false; // [Translator] continue outside of a loop")
            return
        self.continue_stack[-1][1] = True
        self._emit(f"goto {self.continue_stack[-1][0]};")

    def visit_If(self, node):
        self.emitted_safety_checks.clear()
        cond_str = self._visit_expr(node.cond)
        cond_str = self._ensure_bool(node.cond, cond_str)

        self._emit(f"if ({cond_str})")
        self._emit("{")
        self.indent_level += 1
        if node.iftrue:
            self.visit(node.iftrue)
        self.indent_level -= 1
        self._emit("}")

        if node.iffalse:
            self._emit("else")
            self._emit("{")
            self.indent_level += 1
            self.visit(node.iffalse)
            self.indent_level -= 1
            self._emit("}")
    
    def visit_While(self, node):
        self.emitted_safety_checks.clear()
        analyzer = VariableAnalyzer(
            self.all_globals,
            self.current_func_params,
            self.current_func_locals,
            return_labels=self.current_return_labels,
        )

        analyzer.visit(node)
        # [PATCH T-C1] Inside loop_k only the labels defined within this loop are jump targets.
        self.label_scopes.append(_labels_in(node))
        
        actual_writes = analyzer.writes.copy()
        if "has_returned" in actual_writes:
            actual_writes.add("result") 
            
        inputs = sorted(list(analyzer.reads | actual_writes))
        outputs = sorted(list(actual_writes)) 
        
        visible_vars = analyzer.reads.union(analyzer.writes)
        inputs = sorted(list(analyzer.reads | analyzer.writes))
       
        outputs = sorted(list(analyzer.writes))
        
        main_stmts = self.current_stmts
        main_decls = self.current_decls
        main_modifies = self.current_modifies
        main_vis_vars = self.vis_vars.copy()               
        main_func_locals = self.current_func_locals.copy() 
        main_proc_name = getattr(self, "current_proc_name", "default") 
        
        proc_stmts = []
        proc_decls = []
        proc_modifies = set()

        enclosing_label_counter = self.stmt_label_counter
        self.stmt_label_counter = 0
        self.current_stmts = proc_stmts
        self.current_decls = proc_decls
        self.current_modifies = proc_modifies
        
        self.vis_vars = set(inputs)
        
        self.current_func_locals = set(inputs)
        
        current_id = str(self.loop_counter)
        self.loop_counter += 1
        proc_name = f"loop_{current_id}"
        self.current_proc_name = proc_name 
        specs = self.loop_specs.get(f"loop_{current_id}", {"requires": "true", "ensures": "true", "invariants": "true"})
        req_str = specs["requires"]
        ens_str = specs["ensures"]
        inv_str = specs["invariants"]

        original_stmts = self.current_stmts
        
        captured_stmts = []
        saved_label_counter = self.stmt_label_counter
        self.current_stmts = captured_stmts
        cond_str = self._visit_expr(node.cond)
        cond_str = self._ensure_bool(node.cond, cond_str)

        self.current_stmts = original_stmts
        # The loop condition is evaluated against a throwaway buffer; any statements produced there
        # were re-emitted below, so rewind the counter to keep exactly one label per statement.
        self.stmt_label_counter = saved_label_counter

        self._replay_captured(captured_stmts)
        self._hook_insert_snap_entry(current_id,visible_vars)
        
        self._emit(f"while ({cond_str})")
        # Structural lines cannot be insertion targets: a lemma call is never valid where an
        # `invariant` clause is expected, and labelling the braces would inflate every following
        # index for no benefit. Only the `while` line itself is numbered -- anchoring on it means
        # "immediately before the loop", and anchoring on the first statement of the body means
        # "at the top of the loop body".
        self._emit_unlabelled(f"invariant {inv_str};")
        self._emit_unlabelled("{")
        self.indent_level += 1
        self._hook_insert_snap_head(current_id,visible_vars)

        # [PATCH T-B2] `continue` target for this loop: see visit_Continue / _visit_loop_body.
        # The label sits at the end of the body, BEFORE the re-evaluation of the condition's side
        # statements, exactly where C's `continue` resumes. It is structural, so it does not
        # consume a skeleton label.
        self._visit_loop_body(node.stmt, f"CONTINUE_{proc_name}")

        self._replay_captured(captured_stmts)
        
        self._hook_insert_snap_body(current_id,visible_vars)
        self.indent_level -= 1
        self._emit_unlabelled("}")
        self._hook_insert_snap_done(current_id,visible_vars)
        
        
        self.current_stmts = main_stmts
        self.current_decls = main_decls
        self.current_modifies = main_modifies
        self.vis_vars = main_vis_vars                      
        self.current_func_locals = main_func_locals        
        self.current_proc_name = main_proc_name
        self.label_scopes.pop()   # [PATCH T-C1] back to the enclosing procedure's labels
        
        in_params_list = []
        for v in inputs:
            v_type = self.var_types.get(v, "int")
            in_params_list.append(f"in_{v}: {v_type}")
        in_params_str = ", ".join(in_params_list)

        out_params_list = []
        for v in outputs:
            v_type = self.var_types.get(v, "int")
            out_params_list.append(f"{v}: {v_type}")

        # [PATCH B7] Keep read-only shadow variables visible to contracts.
        # Since B1, a[i] = e writes only the heap, so the unchanged base a is
        # no longer a loop output. However, contracts such as ensures a == in_a
        # cannot refer to a local variable initialized by a := in_a.
        # Return any non-output shadow variable referenced by requires/ensures.
        # Its value is preserved: it is initialized once and never modified.
        _contract_text = (str(req_str) or "") + " " + (str(ens_str) or "")
        for v in inputs:
            if v in outputs:
                continue
            if re.search(r'(?<![A-Za-z0-9_])' + re.escape(v) + r'(?![A-Za-z0-9_])', _contract_text):
                outputs = outputs + [v]
                out_params_list.append(f"{v}: {self.var_types.get(v, 'int')}")

        out_params_str = ", ".join(out_params_list)
        
        init_shadow_vars = []
        for v in inputs:
            init_shadow_vars.append(f"  {v} := in_{v};")
            if v not in outputs:
                v_type = self.var_types.get(v, "int")
                
                proc_decls.append(f"var {v}: {v_type};")
        
        modifies_str = ""
        if proc_modifies:
            modifies_str = f"modifies {', '.join(sorted(proc_modifies))};"
            
            self.current_modifies.update(proc_modifies)
       
        if "has_returned" in outputs:
            # Structural epilogue, never an insertion target.
            proc_stmts.append(f"EXIT_{proc_name}:")
            proc_stmts.append("  return;")
        
        smoke_str = "  assert false; // [SMOKE TEST] Check for Vacuous Truth\n" if self.smoke_test else "" 
        
        proc_code = f"""
procedure {proc_name}({in_params_str}) returns ({out_params_str})
  {modifies_str}
  requires {req_str}; 
  ensures {ens_str};
{{
{chr(10).join(["  " + d for d in proc_decls])}

{smoke_str}{chr(10).join(init_shadow_vars)}

{chr(10).join(proc_stmts)}
}}
"""
        self.extracted_procedures.append(proc_code)

        # Back in the enclosing procedure: resume its own numbering where it left off.
        self.stmt_label_counter = enclosing_label_counter

        args = ", ".join(inputs)
        rets = ", ".join(outputs)
        self._hook_insert_snap_call(current_id,visible_vars)
        if rets:
            self._emit(f"call {rets} := {proc_name}({args});")
        else:
            self._emit(f"call {proc_name}({args});")
        self._hook_insert_snap_called(current_id,visible_vars)
       
        if "has_returned" in outputs:
            self._emit("if ((has_returned == 1))")
            self._emit("{")
            self.indent_level += 1
            self._emit(f"goto {self._get_exit_label()};")
            self.indent_level -= 1
            self._emit("}")
        
    def _hook_insert_snap_call(self,current_id,visible_vars):
        pass
    def _hook_insert_snap_called(self,current_id,visible_vars):
        pass
    def _hook_insert_snap_entry(self,current_id,visible_vars):
        pass
    def _hook_insert_snap_head(self,current_id,visible_vars):
        pass
    def _hook_insert_snap_body(self,current_id,visible_vars):
        pass
    def _hook_insert_snap_done(self,current_id,visible_vars):
        pass

    def visit_For(self, node):
        if node.init:
            self.visit(node.init)

        new_block_items = []
        
        if node.stmt:
            if isinstance(node.stmt, c_ast.Compound):
                if node.stmt.block_items:
                    new_block_items.extend(node.stmt.block_items)
            else:
                new_block_items.append(node.stmt)
        
        if node.next:
            # [PATCH T-B2] C's `continue` inside a `for` still executes the increment. The body
            # gets a `CONTINUE_x:` label at its very end (see visit_While), so the increment must
            # run AFTER that label. It stays a real AST node (so VariableAnalyzer still sees its
            # reads/writes); its identity is recorded and visit_While emits it after the label.
            new_block_items.append(node.next)
            self.for_increments.add(id(node.next))
          
        new_body = c_ast.Compound(block_items=new_block_items, coord=node.coord)
        
        new_cond = node.cond if node.cond else c_ast.Constant(type='int', value='1')
        
        fake_while_node = c_ast.While(cond=new_cond, stmt=new_body, coord=node.coord)
        
        self.visit_While(fake_while_node)

    def visit_FuncCall(self, node):
        self.emitted_safety_checks.clear()
        func_name = ""
        
        if isinstance(node.name, c_ast.ID):
            func_name = node.name.name
        
        if func_name not in ["__VERIFIER_assert", "assume_abort_if_not", "reach_error"]:
            self.current_modifies.update(["Mem_int", "Mem_real", "Allocator", "Alloc", "Size"])
        args_nodes = [] 
        args_str = []   
        if node.args:
            for arg in node.args.exprs:
                args_nodes.append(arg)
                args_str.append(self._visit_expr(arg))

        if func_name in ["__VERIFIER_assert", "___SL_ASSERT"]:
            
            if args_nodes:
                cond = self._ensure_bool(args_nodes[0], args_str[0])
                self._emit(f"assert {cond};")
            else:
                self._emit("assert true;") 

        elif func_name == "assume_abort_if_not":
            
            if args_nodes:
                cond = self._ensure_bool(args_nodes[0], args_str[0])
                self._emit(f"assume {cond};")
            
        
        elif func_name == "__VERIFIER_error" or func_name == "reach_error":
            
            self._emit("assert false;")
        
        elif func_name in ["abort", "exit"]:
            self._emit("assume false; // halt execution")
            return
        
        elif func_name == "free":
            if args_str:
                ptr_expr = args_str[0]
                self.current_modifies.add("Alloc")
                self._emit(f"assert {ptr_expr} > 0; // [Safety] Free Non-null")
                self._emit(f"assert Alloc[{ptr_expr}] == true; // [Safety] Prevent Double Free")
                self._emit(f"Alloc[{ptr_expr}] := false;")
            return
        
        
        else:
            args_joined = ", ".join(args_str)
            current_call_id = str(self.call_counter)
            self.call_counter += 1

            self._hook_pre_call(current_call_id, func_name)
            ret_type = self.func_ret_types.get(func_name, "void")
            
            if ret_type != "void":
                
                dummy_temp = self._new_temp(b_type=ret_type)
                self._emit(f"call {dummy_temp} := {func_name}({args_joined}); // CallID: {current_call_id} (Return Discarded)")
                self._hook_post_call(current_call_id, func_name, return_var=dummy_temp)
            else:
                
                self._emit(f"call {func_name}({args_joined}); // CallID: {current_call_id}")
                self._hook_post_call(current_call_id, func_name)
    
    def _hook_proc_entry(self, proc_name): pass
    def _hook_proc_exit(self, proc_name): pass
    def _hook_pre_call(self, call_id, callee_name): pass
    def _hook_post_call(self, call_id, callee_name, return_var=None): pass

    def visit_Return(self, node):
        self.emitted_safety_checks.clear() 
        if node.expr:
            val = self._visit_expr(node.expr)
            if getattr(self, "current_func_ret_type", "int") == "real":
                if not self._is_real_expr(val):
                    val = self._ensure_real_literal(val)
            self._emit(f"result := {val};")
        
        self._emit("has_returned := 1;")
        self._emit(f"goto {self._get_exit_label()};")

    def get_code(self, include_insertions=True):
        """Assemble the final Boogie text.

        [PATCH L3] Insertion replay happens HERE, on the fully assembled text, rather than inside
        the individual emitters. All five construction sites route through this method, so a lemma
        call added by the contract is present in the plain skeleton, the smoke variant, and both
        snapshot variants alike. `include_insertions=False` is available for callers that want to
        inspect the bare skeleton -- that is what shows the LLM which labels to anchor to.
        """
        axioms_code = "\n".join(self.boogie_axioms)

        global_code = "\n".join(self.output_global)

        procs_code = "\n".join(self.extracted_procedures)

        main_code = "\n".join(self.output)

        text = axioms_code + "\n"+global_code + "\n" + procs_code + "\n" + main_code
        text = close_modifies_over_calls(text, set(self.all_globals))
        if include_insertions:
            text, problems = apply_insertions(text, self.insertions)
            self.insertion_problems.extend(problems)
        return text


class SnapshotInserter(CToBoogieVisitor):
    def __init__(self, invariants_json):
        
        super().__init__(invariants_json)
        
        self.nondet_counter = 0
    
    def _add_snapshot(self, target_var, v_type=None):
        
        snap_name = f"snap_nondet_{self.nondet_counter}"
        self.nondet_counter += 1
        
        if v_type is None:  # [PATCH B4] optional explicit type, for Mem[x] targets
            v_type = self.var_types.get(target_var, "int")
        
        self.current_decls.append(f"var {snap_name}: {v_type};")
        
        zero_str = "0.0" if v_type == "real" else "0"
        self._emit_unlabelled(f"{snap_name} := {target_var} + {zero_str};")

    def visit_While(self, node):
        analyzer = VariableAnalyzer(
            self.all_globals, 
            self.current_func_params, 
            self.current_func_locals
        )
        analyzer.visit(node)
        
        active_vars = sorted(list(analyzer.reads | analyzer.writes))
        
        current_id = str(self.loop_counter)
        self.loop_counter += 1

        specs = self.loop_specs.get(f"loop_{current_id}", {"requires": "true", "ensures": "true", "invariants": "true"})
        inv_str = specs["invariants"]
        
        for var in active_vars:
            shadow_name = f"in_{var}"
            
            if f"in_{var}" in inv_str:
                v_type = self.var_types.get(var, "int")
                self.current_decls.append(f"var {shadow_name}: {v_type};")

                self._emit_unlabelled(f"{shadow_name} := {var};")
        
        original_stmts = self.current_stmts
        
        captured_stmts = []
        saved_label_counter = self.stmt_label_counter
        self.current_stmts = captured_stmts
        cond_str = self._visit_expr(node.cond)
        cond_str = self._ensure_bool(node.cond, cond_str)

        self.current_stmts = original_stmts
        self.stmt_label_counter = saved_label_counter

        self._replay_captured(captured_stmts)

        self._emit(f"while ({cond_str})")
        self._emit(f"invariant {inv_str};")
        self._emit("{")
        self.indent_level += 1

        # [PATCH T-B2] inline loops (SnapshotInserter) need their own continue target too.
        self.inline_loop_seq += 1
        self._visit_loop_body(node.stmt, f"CONTINUE_inl_{self.inline_loop_seq}")

        self._replay_captured(captured_stmts)
        
        self.indent_level -= 1
        self._emit("}")
        
        
    def visit_Decl(self, node):
        super().visit_Decl(node)
        
        if node.init and self._is_nondet_call(node.init):
            # [PATCH B4] snapshot the variable's VALUE: for an escaped variable that is
            # Mem[x] (x holds its address), and true/false/result are renamed to *_c.
            name = node.name + "_c" if node.name in ["true", "false", "result"] else node.name
            if self._is_escaped(node.name):
                b_t = self.var_types.get(name, "int")
                mem_map = "Mem_real" if "real" in b_t else "Mem_int"
                self._add_snapshot(f"{mem_map}[{name}]", v_type=b_t)
            else:
                self._add_snapshot(name)
    
    def visit_Assignment(self, node):
        super().visit_Assignment(node)
        
        if node.op == '=':
            if isinstance(node.rvalue, c_ast.FuncCall):
                if self._is_nondet_call(node.rvalue):
                    if isinstance(node.lvalue, c_ast.ID):
                        self._add_snapshot(node.lvalue.name)
    
    def _visit_expr(self, node):
        result_str = super()._visit_expr(node)
        
        if isinstance(node, c_ast.FuncCall):
            if self._is_nondet_call(node):
                self._add_snapshot(result_str)
        
        return result_str

class FullSnapshotInserter(CToBoogieVisitor):
    def __init__(self, invariants_json):
        super().__init__(invariants_json)
        self.assertion_counter = 0

    def _take_snapshot(self, prefix,visible_vars, extra_vars=None):
        all_vars = visible_vars.union(self.all_globals)
        if extra_vars:
            if isinstance(extra_vars, str):
                if re.match(r'^[a-zA-Z_]\w*$', extra_vars):
                    all_vars.add(extra_vars)
            else:
                for v in extra_vars:
                    if re.match(r'^[a-zA-Z_]\w*$', v):
                        all_vars.add(v)

        for var_name in sorted(list(all_vars)):
            snap_name = f"{prefix}_{var_name}"
            query_name = var_name[3:] if var_name.startswith("in_") else var_name
            if query_name in self.ptr_target_types:
                self.snapshot_is_address.add(snap_name)
           
            v_type = self.var_types.get(var_name, "int")
            if v_type.startswith("[int]"):
                self.current_decls.append(f"var {snap_name}: {v_type};")

                self._emit_unlabelled(f"havoc {snap_name};")
                self._emit_unlabelled(f"assume {snap_name} == {var_name};")
            else:
                self.current_decls.append(f"var {snap_name}: {v_type};")

                zero_str = "0.0" if v_type == "real" else "0"
                self._emit_unlabelled(f"{snap_name} := {var_name} + {zero_str};")

    def _hook_insert_snap_call(self,current_id,visible_vars):
        self._take_snapshot(f"snap_loop_{current_id}_call",visible_vars)
    def _hook_insert_snap_called(self,current_id,visible_vars):
        self._take_snapshot(f"snap_loop_{current_id}_called",visible_vars)
    def _hook_insert_snap_entry(self,current_id,visible_vars):
        self._take_snapshot(f"snap_loop{current_id}_entry",visible_vars)
    def _hook_insert_snap_head(self,current_id,visible_vars):
        self._take_snapshot(f"snap_loop{current_id}_head",visible_vars)
    def _hook_insert_snap_body(self,current_id,visible_vars):
        self._take_snapshot(f"snap_loop{current_id}_body",visible_vars)
    def _hook_insert_snap_done(self,current_id,visible_vars):
        self._take_snapshot(f"snap_loop{current_id}_done",visible_vars)
    def _hook_proc_entry(self, proc_name): 
        self._take_snapshot(f"snap_{proc_name}_entry", self.vis_vars)
    def _hook_proc_exit(self, proc_name):
        self._take_snapshot(f"snap_{proc_name}_exit", self.vis_vars)
    def _hook_pre_call(self, call_id, callee_name):
        self._take_snapshot(f"snap_call_{call_id}_pre", self.vis_vars)
    def _hook_post_call(self, call_id, callee_name, return_var=None):
        self._take_snapshot(f"snap_call_{call_id}_post", self.vis_vars,extra_vars=return_var)

    def visit_FuncCall(self, node):
        func_name = ""
        if isinstance(node.name, c_ast.ID):
            func_name = node.name.name
         
        if func_name == "__VERIFIER_assert":
            analyzer = VariableAnalyzer(
                self.all_globals, 
                self.current_func_params, 
                self.current_func_locals
            )
            
            analyzer.visit(node)
            
            visible_vars = analyzer.reads.union(analyzer.writes)
            self._take_snapshot(f"snap_assertion{self.assertion_counter}",visible_vars)
            self.assertion_counter += 1
            
        super().visit_FuncCall(node)

def trans_c_to_boogie(ast, invariants, smoke_test=False):
    translator = CToBoogieVisitor(invariants)
    translator.smoke_test = smoke_test 
    translator.visit(ast)
    return translator.get_code(), translator.var_types, translator.snapshot_is_address, translator.struct_env

def insert_inv_to_boogie_with_snap(ast, invariants):
    
    translator = SnapshotInserter(invariants)
    translator.visit(ast)
    return translator.get_code(), translator.var_types, translator.snapshot_is_address, translator.struct_env

def insert_inv_to_boogie_FULLSNAP(ast, invariants):
    translator = FullSnapshotInserter(invariants)
    translator.visit(ast)
    return translator.get_code(), translator.var_types, translator.snapshot_is_address, translator.struct_env
# --- test code ---
if __name__ == "__main__":
    c_code = """
extern void abort(void);
extern void __assert_fail(const char *, const char *, unsigned int, const char *);
void reach_error() { __assert_fail("0", "struct_test.c", 3, "reach_error"); }
void __VERIFIER_assert(int cond) {
  if (!(cond)) {
    ERROR: {reach_error();abort();}
  }
  return;
}

typedef struct {
    int x;
    int y;
} Point;

int main() {
    int a[10];
    int b[10];
    a[3] = 5;
    return 0;
}
    """
    c_code = preprocess_code(c_code)
    parser = c_parser.CParser()
    ast = parser.parse(c_code)
    response = {
  
}
    result,_,_,_ = trans_c_to_boogie(ast, response, False)
    result2,_,_,_ = insert_inv_to_boogie_FULLSNAP(ast, response)
    print("===  (Boogie) ===")
    print(result)