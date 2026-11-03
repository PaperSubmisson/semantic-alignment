import os
import re
import subprocess
import copy
import json
import logging
import concurrent.futures
from openai import OpenAI
from pycparser import c_parser, c_ast
from translator_v3 import trans_c_to_boogie, preprocess_code,insert_inv_to_boogie_with_snap,insert_inv_to_boogie_FULLSNAP
from c_instrumenter import get_final_Ccode, parse_boogie_model, get_final_Ccode_2
from lemma_guard import audit_contract

# ================= Config section =================
API_KEY = "" 
BASE_URL = "" 
MODEL_NAME = "" 
TARGET_FOLDER = "benchmark/"   #select the task folder(task1-task6)
MAX_WORKERS = 11                    # Number of concurrent tasks
LOG_FOLDER = f"{TARGET_FOLDER}/gpt_logs"         
# ===========================================
COMMON_PROMPT = """
[Boogie Core Syntax Guide]
Please strictly use only the following Boogie-native syntax. Do NOT use symbols specific to other programming languages (such as %, <<, >>) or non-standard aggregate notation (such as sum, product):

1. Types and Arithmetic Operations (Extremely Important; Boogie is strictly strongly typed):
   - Integers (`int`): use `+`, `-`, and `*` for addition, subtraction, and multiplication. Integer division must use `div` (e.g., `x div 2`), and modulo must use `mod` (e.g., `x mod 2`).
   - Reals (`real`): correspond to C `float`/`double`. Use `+`, `-`, and `*` for addition, subtraction, and multiplication. Real division must use `/` (e.g., `x / 2.0`).
   - Fatal warning about type mixing: Boogie absolutely forbids mixed arithmetic or comparison between `int` and `real`. For example, `real_var - int_var` will cause a syntax error.
     - Correct approach: when writing contracts, both sides of an equality must have pure types. If you must use an integer variable in a real-valued formula, use the built-in conversion function `real(int_var)` to cast it to `real` before performing operations.
       Wrong example: `(4.0 * s) - (12 * r) == 1.0`  (assuming `r` is an `int`)
       Correct example: `(4.0 * s) - (12.0 * real(r)) == 1.0`

2. Logical Operations:
   - Basic operators: `&&` (and), `||` (or), `!` (not), `==` (equals), `!=` (not equals).
   - Implication: `==>` (e.g., `x > 0 ==> y > 0`).
   - Biconditional: `<==>`.
   - Strict parentheses requirement: Boogie absolutely forbids mixing `&&` and `||` without explicit parentheses! For example, writing `A && B || C && D` will directly trigger a syntax error. You must parenthesize them explicitly: write `(A && B) || (C && D)`.
   - Parenthesizing implications: to avoid precedence confusion, when using `==>`, you must fully parenthesize both the antecedent and the consequent. For example: `(A && B) ==> (C || D)`.

3. Quantifiers:
   - Universal: `forall k: int :: 0 <= k && k < n ==> a[k] == 0`
   - Existential: `exists k: int :: a[k] == target`

4. Conditional Expressions:
   - Use `if ... then ... else ...` (for example, `val == (if x > 0 then x else -x)`), and do not use the `?:` ternary operator.

[Global Flat Memory Model and Contract-Writing Guidelines]
The current Boogie program uses a global flat memory model. Complex data structures are uniformly stored in the following global maps:

Memory components:
- `Mem_int: [int]int`
  Global integer memory.
- `Mem_real: [int]real`
  Global real-valued memory.
- `Alloc: [int]bool`
  Allocation-validity map. If `Alloc[base] == true`, then the base address is live/valid.
- `Size: [int]int`
  Records the total number of contiguous memory cells allocated at a base address. It is used for bounds checking.

Address variables and offsets:
- In the Boogie code, some variables may appear to be ordinary `int` variables, such as `in_p` or `in_a`. However, if such a variable is used as an index into `Mem_int` or `Mem_real`, for example in an expression like `Mem_int[in_a + i]`, then it should be understood as a memory base address.
- Data reads and writes are represented as map accesses with offsets.

Mandatory rules for writing memory-related contracts:
1. Validity and aliasing
- If a parameter is used as a memory address, such as `in_p`, its validity must be declared in the `requires` clause:
  `requires in_p > 0 && Alloc[in_p] == true;`
- If there are multiple pointer-like parameters and their memory regions should not overlap, you must add a separation condition in the `requires` clause:
  `requires (in_a + Size[in_a] <= in_b) || (in_b + Size[in_b] <= in_a);`

2. Bounds safety
- If the code accesses `Mem_...[in_p + i]`, the contract must ensure that the access is in bounds:
  `0 <= i && i < Size[in_p];`

3. Frame problem and loop amnesia for global memory  [Extremely important]
- Pay close attention to the `modifies` clause in each procedure signature. Whenever a global variable such as `Mem_int`, `Mem_real`, `Alloc`, `Size`, or `Allocator` appears in the `modifies` list, the solver requires an explicit description of its state when the procedure exits.
- Protecting data memory (`Mem_xxx`):
  If `Mem_xxx` is modified by the procedure, you must explicitly state in the `ensures` clause that all memory regions not actually modified by the procedure remain unchanged when the procedure exits.
  Generic example:
  `ensures (forall addr: int :: !(addr is in the modified range) ==> Mem_int[addr] == old(Mem_int)[addr]);`
- Protecting allocation state (`Alloc`, `Size`):
  If `Alloc` and `Size` are modified, and the procedure only allocates new memory without freeing old memory, then you must guarantee in the `ensures` clause that all previously allocated memory remains valid and keeps the same size after the procedure exits.
  Example:
  `ensures (forall addr: int :: old(Alloc)[addr] == true ==> Alloc[addr] == true && Size[addr] == old(Size)[addr]);`
- Protecting the allocation cursor (`Allocator`):
  If `Allocator` is modified, you must ensure that the allocator is monotonically nondecreasing at procedure exit, so that it does not move backwards and overwrite old data.
  Example:
  `ensures Allocator >= old(Allocator);`
- For `loop_X` procedures:
  You must add corresponding anti-amnesia assertions as loop `invariant`s as well, such as preservation of old allocation validity and preservation of unmodified memory regions. Otherwise, the verifier will not be able to prove the `ensures` clauses.

4. Preventing context loss through strict preconditions
- Modular verification means that each procedure is verified in isolation. The solver may assign adversarial arbitrary values to all `in_` parameters.
- Therefore, you must constrain all input parameters precisely in the `requires` clauses, especially variables used as state flags or loop counters. For example, if a flag is initialized to `1` before entering a loop, you must explicitly write `requires in_flag == 1;`. Otherwise, the solver may instantiate it as `0`, which can break subsequent reasoning.

5. Quantifiers and relational arrays  [Extremely important]
- SMT solvers such as Z3 are very sensitive to quantifiers. In particular, Z3 often performs poorly when map indices contain arithmetic expressions such as `Mem_int[in_a + k]`. Such expressions may lead to ineffective triggers and spurious counterexamples.
- rule:
  In any `forall` formula that traverses an array, the quantified variable must represent an absolute address, such as `addr`, and the map access should be written directly as `Mem_int[addr]`.
- Bad style, which often makes Z3 fail:
  `forall k: int :: 0 <= k && k < n ==> Mem_int[in_acopy + k] == Mem_int[in_a + k]`
- Correct style:
  `forall addr: int :: in_acopy <= addr && addr < in_acopy + n ==> Mem_int[addr] == Mem_int[in_a + (addr - in_acopy)]`
- For assignment-copy loops, comparison-check loops, or any loop that traverses an array, always use the absolute-address style above. Do not use a relative offset variable such as `k` as the direct quantified index.

6. Loop counter monotonicity
- When Boogie verifies loop preservation, it havoc-randomizes all modified variables.
- If a loop variable such as `i` increases from an initial value, you must explicitly state its relation to the initial parameter in the loop invariant.
  Example:
  `invariant i >= in_i;`

7. Strict restrictions on the `old()` keyword
- The expression `old(e)` means the value of expression `e` in the initial state at procedure entry.
- Never use `old()` in a `requires` clause. At procedure entry, the current state is already the initial state, so you should directly write expressions such as `Mem_int[addr]`. Using `old()` in a `requires` clause will cause a syntax error.
- `old()` may only be used in `ensures` clauses and loop `invariant`s, where it refers to the old memory state at procedure entry.
  Correct example:
  `Mem_int[addr] == old(Mem_int)[addr]`

[Advanced Guidance: Auxiliary Functions and Verified Lemmas]
Use the "global" field only when the required program logic cannot be expressed directly using ordinary `requires`, `ensures`, and `invariant` clauses.
Typical examples include nonlinear arithmetic, recursive summaries, and array aggregations such as sums or maxima.

=========================================================
[THE CENTRAL RULE: NEVER USE `axiom`. PROVE IT AS A LEMMA.]
=========================================================
An `axiom` is an UNPROVED assumption: the solver believes it unconditionally, so a single wrong axiom silently
proves everything and the verification result is worthless.
Therefore, in this task `axiom` is FORBIDDEN. Every auxiliary fact must instead be a `procedure` named `Lemma_*`
that Boogie actually verifies. Any reply containing `axiom` is rejected.

There are exactly two things you may put in "global":
  (1) a helper `function` WITH A BODY, and
  (2) a `procedure Lemma_*(...)` whose body establishes its own `ensures`.

---------------------------------------------------------
Part 1: A helper function must carry its own recursive body
---------------------------------------------------------
Write the recursion directly in the function body with `if ... then ... else ...`.
Do NOT write a signature followed by axioms; such a function is uninterpreted and proves nothing.

  function Pow2(k: int) : int
  {
    if k <= 0 then 1 else 2 * Pow2(k - 1)
  }

A function is PURE. It may not refer to global program variables such as `Mem_int`, `Mem_real`, `Alloc`,
or `Size` — Boogie rejects that with "cannot refer to a global variable". Pass the memory map in explicitly
as a `[int]int` / `[int]real` parameter instead.

---------------------------------------------------------
Part 2: Every auxiliary fact is a recursive Lemma procedure
---------------------------------------------------------
A lemma is an ordinary Boogie `procedure` with `requires` (what it may assume) and `ensures` (what it proves),
whose body is a RECURSIVE CALL TO ITSELF on a smaller argument. That self-call is the induction hypothesis:
the `requires` must be strong enough that the recursive instance is applicable, and the `ensures` must be the
fact you want at the top level. Boogie verifies the procedure like any other, so the fact is PROVED, not assumed.

Three rules that decide whether the induction goes through:
  a) The `if` guard must make the recursive call sound. When you call yourself on `n - 1`, the preconditions
     mention `n`, so you must guard the call such that `n - 1` still satisfies them.
  b) The frame condition must be stated per-element, not globally. Use an absolute-address `forall` over the
     untouched region and let the recursion peel one element at a time.
  c) A recursive lemma must TERMINATE, and you must say why. Boogie by itself does not check termination, and a
     lemma that recursed forever would "prove" anything. So every lemma that calls itself carries a measure,
     written as a comment on its own line between the signature and the first `requires`:
         // decreases: n
     The measure is an `int` expression over the lemma's parameters. Before each recursive call the framework
     checks `0 <= measure(new arguments) < measure(current arguments)`; if that fails, the lemma fails.
     Peeling `n` to `n - 1` under `if (n > 0)` uses `// decreases: n`; shrinking a range `[lo, hi)` uses
     `// decreases: hi - lo`. A lemma that does not call itself needs no measure.

What a lemma body may contain (anything else makes the framework reject the whole reply):
  - `if` / `else`, `assert`, local `var` declarations, assignments to locals, and `call` to OTHER `Lemma_*`
    procedures declared in "global" (or to itself). Two lemmas must not call each other (no mutual recursion).
  - NOT allowed: `assume`, `free`, `modifies`, `while`, `goto`, `havoc` of a parameter, `{:attributes}`,
    `where` clauses, a separate `implementation`, a lemma without a body, or a call to a program procedure.
    Each of these lets a lemma "verify" without proving its ensures.

Worked example — adding `d` to ONE cell of an array raises the total of the array by exactly `d`.
(This is an illustration of the technique only; it is not tied to the program you are given.)

  function Total(M: [int]int, base: int, lo: int, hi: int): int
  {
    if lo >= hi then 0
    else Total(M, base, lo, hi - 1) + M[base + hi - 1]
  }

  // Helper lemma: two maps that agree on [base, base+n) have the same total there.
  procedure Lemma_Same(M: [int]int, N: [int]int, base: int, n: int)
    // decreases: n
    requires 0 <= n;
    requires (forall addr: int :: base <= addr && addr < base + n ==> N[addr] == M[addr]);
    ensures Total(N, base, 0, n) == Total(M, base, 0, n);
  {
    if (n > 0) { call Lemma_Same(M, N, base, n - 1); }
  }

  // Main lemma: N is M with cell k increased by d.
  procedure Lemma_Bump(M: [int]int, N: [int]int, base: int, k: int, d: int, n: int)
    // decreases: n
    requires 0 <= k && k < n;
    requires N[base + k] == M[base + k] + d;
    requires (forall addr: int :: base <= addr && addr < base + n && addr != base + k ==> N[addr] == M[addr]);
    ensures Total(N, base, 0, n) == Total(M, base, 0, n) + d;
  {
    if (n > k + 1) { call Lemma_Bump(M, N, base, k, d, n - 1); }
    else { call Lemma_Same(M, N, base, k); }
  }

Read the shape of that proof, because it is the shape you must reuse:
  - the statement relates `Total(N, ...)` and `Total(M, ...)` -- two DIFFERENT maps. No axiom relates them;
    the lemma proves it.
  - the `requires` say N and M agree on every address in `[base, base+n)` EXCEPT `base + k`, and say exactly
    how they differ there. The `forall` covers only the untouched cells.
  - the recursive call at `n - 1` peels the rightmost cell, which is untouched while `n > k + 1`. When the
    range ends right after the changed cell (`n == k + 1`), what remains is the prefix `[0, k)` -- which
    is untouched, so the helper lemma closes it. The guard `n > k + 1` is what keeps `k < n - 1`, i.e. the
    recursive call's precondition, true.
  - a lemma may call OTHER lemmas: splitting a proof into a small frame lemma plus the main lemma is normal.
  - both recursive calls shrink `n` by one under a guard that keeps it non-negative, hence `// decreases: n`.

---------------------------------------------------------
Part 3: Direction consistency for array aggregations
---------------------------------------------------------
If the loop variable `i` increases from left to right (the processed interval grows on the right), then the
recursive definition must peel off the RIGHT endpoint, exactly as `Total` above does.
This way, when the loop body adds one element to the range, the solver can unfold the recurrence once.
A left-to-right loop paired with a definition that peels the left endpoint forces an unprovable reversal.

---------------------------------------------------------
Part 4: Making a lemma usable — snapshots and inserted calls
---------------------------------------------------------
A lemma is a procedure, so inside a program procedure it does nothing unless you CALL it. You cannot put a
`call` in a `requires`/`ensures`/`invariant`, and you must not restate the proof inline. Instead you list the
calls in an "insertions" object, keyed by procedure, and say after which skeleton line each one goes.

The memory argument of a lemma is a snapshot of the global map, taken as a value:
  M0 := Mem_int;
This copies the CURRENT contents of `Mem_int` into `M0`. Since `Mem_int` keeps changing, the ORDER MATTERS:
you must snapshot the old state BEFORE the mutation and call the lemma AFTER it, e.g. for a skeleton
that increments one cell `p[j]` of an array of length `len`:

  M0 := Mem_int;                                    <-- old contents, before the write
  Mem_int[(p + j)] := (Mem_int[(p + j)] + 5);      <-- the mutation itself (already in the skeleton)
  call Lemma_Bump(M0, Mem_int, p, j, 5, len);       <-- relates old (M0) to new (Mem_int)

Snapshot variables must ALSO be declared. List them under "decls" as a bare
`name: type` fragment (no `var`, no semicolon); the framework turns it into `var M0: [int]int;`
in that procedure's declaration block:
  "decls": ["M0: [int]int", "M1: [int]int"]

How `after_line` works — read this carefully:
  - Every skeleton line emitted from the C code carries a trailing label `// #N` (the global lemma/function
    definitions at the top do NOT carry labels, because they are not skeletal).
  - `after_line: N` means "insert the statements immediately after skeleton line #N".
  - The labels are STABLE across rounds: each round re-translates the same C code and replays your
    insertions, so #N names the same statement every time. Anchor on the numbers you see, never on
    counting lines yourself.
  - Several entries may share one `after_line`; they are executed in array order. That is how you guarantee
    a snapshot precedes the call that consumes it — put the snapshot entry first.
  - Inserted statements carry no label themselves. Only skeleton lines do.

What an inserted statement may be (whitelist — anything else is a syntax failure):
  - a lemma call:            "call Lemma_SomeName(arg1, arg2, ...);"
  - a memory snapshot:       "M0 := Mem_int;"   "M1 := Mem_real;"
  - a conditional wrapper
      around those two:      "if (len > 0) { call Lemma_SomeName(M0, Mem_int, p, len); }"
You may NOT insert assignments to program variables, allocations, asserts, or arbitrary Boogie statements.
The program text is fixed; your only control over the program is which lemmas are called where.

---------------------------------------------------------
Part 5: Where a lemma is needed at all
---------------------------------------------------------
Only reach for lemmas when the claim cannot follow from the contracts alone. In practice that means a
`assert` or a postcondition that relates aggregations of two DIFFERENT memory states
(before/after the program rearranges, copies, or overwrites part of an array). If both sides of the goal are written over the
same `Mem_int`, an invariant is usually enough and no lemma is needed.

[Output Format Requirements]
Please return only a valid JSON object, without Markdown formatting or other explanations.
The structure is shown below. Omit "global" when no helper function or lemma is needed, and omit
"insertions" when no lemma has to be called.

{{
  "global": [
    "function Total(M: [int]int, base: int, lo: int, hi: int): int { if lo >= hi then 0 else Total(M, base, lo, hi - 1) + M[base + hi - 1] }",
    "procedure Lemma_Same(M: [int]int, N: [int]int, base: int, n: int)\\n  // decreases: n\\n  requires 0 <= n; requires (forall addr: int :: base <= addr && addr < base + n ==> N[addr] == M[addr]); ensures Total(N, base, 0, n) == Total(M, base, 0, n); { if (n > 0) { call Lemma_Same(M, N, base, n - 1); } }",
    "procedure Lemma_Bump(M: [int]int, N: [int]int, base: int, k: int, d: int, n: int)\\n  // decreases: n\\n  requires 0 <= k && k < n; requires N[base + k] == M[base + k] + d; requires (forall addr: int :: base <= addr && addr < base + n && addr != base + k ==> N[addr] == M[addr]); ensures Total(N, base, 0, n) == Total(M, base, 0, n) + d; { if (n > k + 1) { call Lemma_Bump(M, N, base, k, d, n - 1); } else { call Lemma_Same(M, N, base, k); } }"
  ],
  "insertions": {{
    "main": {{
      "decls": ["M0: [int]int"],
      "stmts": [
        {{"after_line": 7, "code": ["M0 := Mem_int;"]}},
        {{"after_line": 8, "code": ["call Lemma_Bump(M0, Mem_int, p, j, 5, len);"]}}
      ]
    }}
  }},
  "procedure name (e.g., loop_0)": {{
    "requires": ["condition expression 1", "condition expression 2"],
    "ensures": ["condition expression 1", "condition expression 2"],
    "invariants(can be omitted if there are no loops inside the procedure)": ["condition expression 1", "condition expression 2"]
  }},
  ...
}}
The numbers in `after_line` above are only placeholders — always take them from the `// #N` labels in the
skeleton you were given. Inside a JSON string, write the line break before and after `// decreases: ...` as
`\\n` (as above): a `//` comment runs to the end of its line.
If the Boogie code you are shown contains `function Lemma_X__measure` or an `assert ... __measure(...)` marked
`[framework]`, those were generated from your `// decreases:` comment; you do not need to write them yourself.

[Notes]
1. Please use only core Boogie syntax in expressions. Using undefined functions or chained comparisons such as `a <= b <= c` will cause syntax errors.
2. Requires (preconditions): a `requires` clause may refer only to input parameters whose names start with `in_` (e.g., `in_x`) and global variables. It is strictly forbidden to refer to local variables or return variables without the `in_` prefix, such as `x` or `i`.
3. Ensures (postconditions): an `ensures` clause may refer only to input parameters whose names start with `in_`, return variables declared in the `returns` clause, and global variables.
4. for a procedure declared as `procedure sum(...) returns (...)`, the procedure name is `sum`.
5. Boogie verifies loops inductively. The verification logic is:
   - Loop entry: `Loop_Head_State` must satisfy `(Invariant) && (Loop_Condition)`.
   - Loop exit: `Exit_State` must satisfy `(Invariant) && !(Loop_Condition)`.
"""

def setup_task_logger(task_id):
    logger = logging.getLogger(task_id)
    logger.setLevel(logging.INFO)
    
    if logger.handlers:
        logger.handlers = []

    log_path = os.path.join(LOG_FOLDER, f"{task_id}.log")
    file_handler = logging.FileHandler(log_path, mode='w', encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s - %(message)s')
    file_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    
    logger.propagate = False 
    
    return logger

try:
    global_openai_client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
except Exception as e:
    print(f"Global OpenAI client initialization failed: {e}")
    exit(1)

class LLMClient:
    def __init__(self, name, role):
        self.name = name
        self.role = role

        self.client = global_openai_client

    def query(self, prompt, logger):
        if not hasattr(self, 'client') or self.client is None:
            logger.error("API client not initialized, cannot make a request.")
            return None
        logger.info(f">>> Sending request to {self.name} ...")
        
        messages = [
            {"role": "system", "content": self.role},
            {"role": "user", "content": prompt}
        ]
        
        try:
            response = self.client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.7, 
            )
            
            content = response.choices[0].message.content
            
            logger.info(f"<<< {self.name} : \n{content}\n{'='*20}")
            return content
        except Exception as e:
            logger.error(f"API call error: {e}")
            return None

class BoogieFeedbackGenerator:
    def __init__(self, bpl_code, model_data, var_types, snapshot_is_address, struct_env):
        self.bpl_lines = bpl_code.split('\n')
        self.model_data = parse_boogie_model(model_data)
        self.var_types = var_types
        self.snapshot_is_address = snapshot_is_address
        self.struct_env = struct_env

        self.proc_line = {}
        self.line_map = self._build_line_map()
        cleaned_lines = []
        for line in self.bpl_lines:
            
            if "//" in line:
                line = line.split("//")[0]
            
            cleaned_lines.append(line.rstrip())
            
        self.bpl_lines = cleaned_lines

    def _build_line_map(self):
        mapping = {}
        
        current_module = "global"  
        current_loop_id = 0        
        fallback_assertion_counter = 0 
        
        last_seen_assertion_snap_id = None 
        re_proc = re.compile(r'procedure\s+(\w+)')
        re_call = re.compile(r'call\s+(?:[\w\s,]+:=\s+)?(\w+)\(')
        re_call_id_comment = re.compile(r'//\s*CallID:\s*(\d+)')
        re_snap_assign = re.compile(r'snap_assertion(\d+)_\w+\s*:=')
        for idx, line in enumerate(self.bpl_lines):
            line_num = idx + 1
            stripped = line.strip()
            
            if stripped.startswith("snap_assertion"):
                match = re_snap_assign.match(stripped)
                if match:
                    last_seen_assertion_snap_id = match.group(1)
            
            proc_match = re_proc.match(stripped)
            if proc_match:
                current_module = proc_match.group(1)
                self.proc_line[current_module] = line_num
                
                if current_module.startswith("loop_"):
                    try:
                        current_loop_id = int(current_module.split('_')[1])
                    except (IndexError, ValueError):
                        current_loop_id = 0
                else:
                    current_loop_id = 0
            
            stmt_type = "common"
            stmt_info = "0"
            
            if stripped.startswith("assert"):
                stmt_type = "assert"
                
                if "[Safety]" in stripped:
                    stmt_info = f"safety_{fallback_assertion_counter}"
                    fallback_assertion_counter += 1
                else:
                    if last_seen_assertion_snap_id is not None:
                        
                        stmt_info = last_seen_assertion_snap_id
                        
                        last_seen_assertion_snap_id = None
                    else:
                        stmt_info = f"{fallback_assertion_counter}"
                        fallback_assertion_counter += 1
                
            
            elif stripped.startswith("call"):
                stmt_type = "call"
                call_match = re_call.match(stripped)
                if call_match:
                    stmt_info = call_match.group(1) 
                    id_match = re_call_id_comment.search(stripped)
                    if id_match:
                        stmt_info = id_match.group(1)
                else:
                    stmt_info = "unknown_call"
            
            mapping[line_num] = {
                "module": current_module,   
                "loop_id": current_loop_id, 
                "type": stmt_type,          
                "info": stmt_info,          
                "msg": stripped             
            }    
        return mapping

    def _parse_map_string(self, map_str):
        res = {}
        if not map_str or map_str == "Unknown Array":
            return res
            
        for item in map_str.split(','):
            item = item.strip()
            if "=" in item and "[" in item:
                
                m = re.search(r'\[(-?\d+)\]=(.*)', item)
                if m:
                    res[int(m.group(1))] = m.group(2).strip()
        return res

    def _detect_memory_overlap(self, snap_prefix):
        
        target_prefix = snap_prefix + "_" if not snap_prefix.endswith("_") else snap_prefix
        
        mem_blocks = {} 
        
        alloc_map = {}
        size_map = {}
        
        for key, val in self.model_data.items():
            clean_key = key.split('@')[0]
            if clean_key == f"{target_prefix}Alloc":
                alloc_map = self._parse_map_string(val)
            elif clean_key == f"{target_prefix}Size":
                size_map = self._parse_map_string(val)
                
        for key, val in self.model_data.items():
            clean_key = key.split('@')[0]
            if clean_key.startswith(target_prefix):
                var_name = clean_key[len(target_prefix):]
                
                if clean_key in self.snapshot_is_address:
                    try:
                        base_addr = int(val)
                        if alloc_map.get(base_addr, "false") == "true":
                            size = int(size_map.get(base_addr, 1))
                            mem_blocks[var_name] = (base_addr, size)
                    except:
                        pass
        
        warnings =[]
        vars_list = list(mem_blocks.keys())
        for i in range(len(vars_list)):
            for j in range(i + 1, len(vars_list)):
                v1, v2 = vars_list[i], vars_list[j]
                b1, s1 = mem_blocks[v1]
                b2, s2 = mem_blocks[v2]
                
                if not (b1 + s1 <= b2 or b2 + s2 <= b1):
                    warnings.append(f"    [{v1}] (base:{b1}, region:[{b1}, {b1+s1})) and [{v2}] (base:{b2}, region:[{b2}, {b2+s2})) are overlapping!")
                    
        return warnings

    def _nearest_state_before(self, start_line, error_line, skip_prefixes=()):
        """Best-effort state at a point that has no snapshot of its own.

        [PATCH L4] Lemma call sites are not instrumented, so when a lemma's precondition fails
        there is no `snap_call_<lemma>_pre`. Walk backwards from the failing line and look for
        any snapshot variable whose model-data counterpart actually has data. Return the first
        non-empty one, labelled with the statement it came from, so the reader understands it is
        the nearest instrumented point, not the call's own pre-state.
        """
        # Build a prefix set from model_data to avoid probing prefixes that have no data at all.
        # Snapshot keys look like `snap_call_1_post_x@0`: strip `@...` then the last `_word` to
        # get the prefix `snap_call_1_post`. (The `@...` suffix is the LFSC model elaboration.)
        known_prefixes = set()
        for key in self.model_data:
            if "@" not in key:
                continue
            base = key.split("@")[0]
            parts = base.rsplit("_", 1)
            if len(parts) == 2 and parts[1]:
                known_prefixes.add(parts[0])

        for i in range(error_line - 1, start_line - 1, -1):
            info = self.line_map.get(i)
            if not info:
                continue
            callee = info.get("info", "")
            module = info.get("module", "")
            candidates = []
            # Non-lemma call sites are instrumented with a post-call snapshot.
            if info["type"] == "call" and not callee.startswith("Lemma_"):
                if "loop" in callee:
                    candidates.append(f"snap_{callee}_called")
                else:
                    candidates.append(f"snap_call_{callee}_post")
            # Always include loop-body and entry snapshots as fallbacks.
            if module.startswith("loop_"):
                lid = info.get("loop_id", 0)
                candidates.extend([f"snap_loop{lid}_head", f"snap_loop{lid}_body"])

            for prefix in candidates:
                if prefix in skip_prefixes:
                    continue
                if prefix not in known_prefixes:
                    continue
                rendered = self._get_vars_from_snapshot(prefix)
                if rendered and rendered != "(No data)":
                    return rendered, info["msg"]

        return None, None

    def _describe_call_transition(self, trace_info):
        """Render one 'after a call' step of an execution trace.

        [PATCH L4] Lemma calls are injected by the contract, not translated from C, so the snapshot
        machinery never instruments them and no `snap_call_<lemma>_post` variable exists. Querying
        one printed "(No data)" for every lemma, which reads as "the lemma had no effect" and
        actively misleads the next round. For a lemma we instead assert the fact it establishes;
        its `ensures` genuinely holds after the call, that being the point of a verified lemma.
        """
        callee = trace_info['info']
        if callee.startswith("Lemma_"):
            return (f"-> After {trace_info['msg']}, the auxiliary fact proved by that lemma is "
                    f"available (its ensures clause holds at this point).\n")
        if "loop" in callee:
            s2 = self._get_vars_from_snapshot(f"snap_{callee}_called")
        else:
            s2 = self._get_vars_from_snapshot(f"snap_call_{callee}_post")
        return (f"-> After {trace_info['msg']}, the variable state provided by Boogie according to "
                f"the ensures (postcondition) of the called procedure is:\n{s2}\n")

    def _get_vars_from_snapshot(self, prefix):
        target_prefix = prefix + "_" if not prefix.endswith("_") else prefix
        raw_vals = {}
        
        for key, val in self.model_data.items():
            if "@" in key:
                clean_key = key.split('@')[0]
                if clean_key.startswith(target_prefix):
                    var_name = clean_key[len(target_prefix):]
                    # [PATCH L4] A key that IS the prefix (`snap_main_entry@0`) also starts with
                    # it. That yielded an empty variable name, so raw_vals came back non-empty
                    # while every entry was then dropped by the filter below -- the caller got an
                    # empty string instead of "(No data)" and a whole state silently vanished from
                    # the report. Require a real variable name.
                    if not var_name:
                        continue
                    raw_vals[var_name] = val

        if not raw_vals:
            return "(No data)"

        mem_int = self._parse_map_string(raw_vals.get("Mem_int", ""))
        mem_real = self._parse_map_string(raw_vals.get("Mem_real", ""))
        alloc_map = self._parse_map_string(raw_vals.get("Alloc", ""))
        size_map = self._parse_map_string(raw_vals.get("Size", ""))

        result =[]
        
        for var_name, val in raw_vals.items():
            if var_name in["Mem_int", "Mem_real", "Alloc", "Size", "Allocator"]:
                continue
                
            full_snap_name = f"{target_prefix}{var_name}"
            is_address = full_snap_name in self.snapshot_is_address
            
            if not is_address:
                result.append(f"{var_name} = {val}")
                continue
                
            try:
                base_addr = int(val)
            except:
                result.append(f"{var_name} = {val} (Invalid Address Format)")
                continue
                
            is_alloc = alloc_map.get(base_addr, "false") == "true"
            
            if not is_alloc:
                result.append(f"{var_name} = {base_addr} (INVALID / NULL / Freed)")
                continue
                
            alloc_size = int(size_map.get(base_addr, 1))
            mem_slice =[]
            
            for target_addr, m_val in mem_int.items():
                if base_addr <= target_addr < base_addr + alloc_size:
                    offset = target_addr - base_addr
                    addr_expr = var_name if offset == 0 else f"{var_name} + {offset}"
                    mem_slice.append(f"Mem_int[{addr_expr}] = {m_val}")
                    
            for target_addr, m_val in mem_real.items():
                if base_addr <= target_addr < base_addr + alloc_size:
                    offset = target_addr - base_addr
                    addr_expr = var_name if offset == 0 else f"{var_name} + {offset}"
                    mem_slice.append(f"Mem_real[{addr_expr}] = {m_val}")
                    
            slice_str = ""
            if mem_slice:
                slice_str = "\n    " + "\n    ".join(mem_slice)
            else:
                slice_str = "  (The value of this memory block is left to Z3's discretion or kept in a Havoc state, which typically does not affect the core logic of the counterexample.)"
                
            result.append(f"{var_name} = {base_addr} (valid memory, Size: {alloc_size}){slice_str}")

        result.sort()
        return "\n  ".join(result)

    def _find_next_procedure_start(self, error_line):
        
        total_lines = len(self.bpl_lines)
        
        for idx in range(error_line, total_lines):
            line = self.bpl_lines[idx].strip()
            
            if line.startswith("procedure"):
                return idx + 1 
        
        return None 

    def generate_report(self, boogie_output):
        match = re.search(r'\.bpl\((\d+),\d+\): Error: (.*)', boogie_output)

        if not match:
            return "No standard Boogie error format detected."
            
        error_line = int(match.group(1))
        error_msg = match.group(2).strip()
        end_line = self._find_next_procedure_start(error_line)
        
        err_info = self.line_map.get(error_line)
        if not err_info: return f"Unable to locate context for line number {error_line}."
        
        module = err_info['module']
        loop_id = err_info['loop_id']
        type = err_info['type']
        info = err_info['info']
        msg = err_info['msg']
        
        is_loop = module.startswith("loop_")

        # [PATCH L4] A failing `procedure Lemma_*` is a different animal from a failing program
        # procedure, and the generic branches below would misdescribe it: they report a "requires
        # of the module" or a "loop invariant" problem, when the actual fault is that the induction
        # does not go through. The report is generated per procedure, so handling this here covers
        # both the plain and the snapshot builds.
        if module.startswith("Lemma_"):
            report = "[Verification Failure Report]\n"
            report += f"Location: auxiliary lemma {module}\n"
            report += f"Error message: for lemma {module}, boogie returned {error_msg}\n"
            if "__measure" in str(msg):
                # [PATCH L6] The failing statement is the framework's termination check, compiled
                # from the lemma's `// decreases:` comment (see lemma_guard.py).
                report += (f"-> This is the TERMINATION check of {module}. Before every recursive "
                           f"call the framework asserts `0 <= measure(new arguments) < "
                           f"measure(current arguments)`, where `measure` is the expression in the "
                           f"lemma's `// decreases:` comment. It failed, so on some path the measure "
                           f"is negative at the recursive call or does not strictly decrease. Without "
                           f"this check, a lemma that recursed forever would 'prove' anything.\n")
                report += (f"-> (Hint) Make the recursive call act on a strictly smaller argument "
                           f"(e.g. `n - 1`), guard it so that the measure stays >= 0 there (e.g. "
                           f"`if (n > 0)`), and choose a measure that tracks that argument (e.g. "
                           f"`// decreases: n`, or `hi - lo` for a range).\n")
                report += f"-> The failing statement is: {msg}\n"
                return report
            report += (f"-> A lemma is proved by recursion: the recursive call inside its body is the "
                       f"induction hypothesis, and Boogie verifies the procedure like any other. "
                       f"This failure means the induction does not go through, not that a program "
                       f"contract is wrong.\n")
            report += f"-> (Hint) Check, in this order:\n"
            report += (f"   1. the `if` guard. If the lemma calls itself on `n - 1`, the guard must be "
                       f"strong enough that `n - 1` still satisfies the requires (e.g. `if (n > 1)` "
                       f"for `requires 1 <= n`). A guard that is too weak lets the recursive call "
                       f"violate its own precondition.\n")
            report += (f"   2. the frame condition. It must be stated per-element over absolute "
                       f"addresses and must cover exactly the untouched region, so that peeling one "
                       f"element leaves a hypothesis of the same shape.\n")
            report += (f"   3. the base case. A one-element range must be discharged from the "
                       f"requires alone; if it is not, the requires are too weak.\n")
            report += f"-> The failing statement is: {msg}\n"
            return report

        start_line = self.proc_line.get(module)
        if not start_line: return f"Unable to locate position in module {module}."

        report = f"[Verification Failure Report]\n"
        report += f"Location: procedure {module}\n"
        report += f"Error message: for procedure {module},boogie returned {error_msg}\n"
        
        if "this assertion could not be proved" in error_msg:
            state = self._get_vars_from_snapshot(f"snap_assertion{info}")
            report += "Execution trace:\n"
            if is_loop:
                state_entry = self._get_vars_from_snapshot(f"snap_loop{loop_id}_entry")
                report += f"-> At the external entry point of the loop, the variable state provided by Boogie according to the requires(precondition) is:\n{state_entry}\n"
            else:
                state_entry = self._get_vars_from_snapshot(f"snap_{module}_entry")
                report += f"-> At the entry of {module}, the variable state provided by Boogie according to the requires(precondition) is:\n{state_entry}\n"
            after_while = False
            after_call = False
            for i in range(start_line, error_line):
                trace_info = self.line_map.get(i)
                if is_loop and trace_info and 'while' in trace_info['msg']:
                    after_while = True
                    s1 = self._get_vars_from_snapshot(f"snap_loop{loop_id}_head")
                    report += f"-> At the beginning of the loop body, the variable state provided by Boogie according to the verification logic is:\n{s1}\n"
                elif trace_info and 'call ' in trace_info['msg']:
                    after_call = True
                    report += self._describe_call_transition(trace_info)
            report += f"-> {msg} does not hold; the variable state at this point is:\n{state}\n"
            
            if "[Safety] Null pointer deref" in msg:
                report += f"-> (Hint) Null Pointer Dereference occurred! Please check whether the pointer is properly allocated, or add a constraint such as ptr > 0 in the Requires/Invariant clauses.\n"
            elif "[Safety] Free Non-null" in msg:
                report += f"-> (Hint) Attempt to free a null pointer! Please check whether the pointer is properly allocated, or add a constraint such as ptr > 0 in the Requires/Invariant clauses.\n"
            elif "[Safety] Use-After-Free" in msg:
                report += f"-> (Hint) Memory safety error: attempted to access invalid memory (Use‑After‑Free)! Please check whether the validity constraint Alloc[ptr] == true is included in the Requires/Invariant clauses.\n"
            elif "[Safety] Prevent Double Free" in msg:
                report += f"-> (Hint) Memory safety error: attempted to free invalid memory (Double Free)! Please check whether the validity constraint Alloc[ptr] == true is included in the Requires/Invariant clauses.\n"
            else:
                if after_while:
                    report += f"-> (Hint) Please check whether the loop invariant inside {module} needs to be strengthened or corrected.\n"
                if after_call:
                    report += f"-> (Hint) Please check whether the ensures (postcondition) of the called procedure needs to be strengthened or corrected.\n"
                report += f"-> (Hint) Please check whether the requires (precondition) of {module} needs to be strengthened or corrected.\n"

        elif "this loop invariant could not be proved on entry" in error_msg:
            state = self._get_vars_from_snapshot(f"snap_loop{loop_id}_entry")
            report += "Execution trace:\n"
            for i in range(start_line, error_line):
                trace_info = self.line_map.get(i)
                if trace_info and 'while' in trace_info['msg']:
                    s1 = self._get_vars_from_snapshot(f"snap_loop{loop_id}_entry")
                    report += f"-> At the external entry point of the loop, the variable state provided by Boogie according to the requires(precondition) is:\n{s1}\n"
            report += f"-> {msg} does not hold; the variable state at this point is:\n{state}\n"
            report += f"-> (Hint) Please check whether the requires (precondition) of {module} needs to be strengthened or corrected.\n"
            report += f"-> (Hint) Please check whether the invariant itself needs to be corrected.\n"

        elif "this invariant could not be proved to be maintained by the loop" in error_msg:
            state = self._get_vars_from_snapshot(f"snap_loop{loop_id}_body")
            report += "Execution trace:\n"
            if "loop" in module:
                state_entry = self._get_vars_from_snapshot(f"snap_loop{loop_id}_entry")
                report += f"-> At the external entry point of the loop, the variable state provided by Boogie according to the requires(precondition) is:\n{state_entry}\n"
            after_call = False
            for i in range(start_line, end_line):
                trace_info = self.line_map.get(i)
                if trace_info and 'while' in trace_info['msg']:
                    s1 = self._get_vars_from_snapshot(f"snap_loop{loop_id}_head")
                    report += f"-> At the beginning of the loop body, the variable state provided by Boogie according to the verification logic is:\n{s1}\n"
                elif trace_info and 'call ' in trace_info['msg']:
                    after_call = True
                    report += self._describe_call_transition(trace_info)
            report += f"-> At the end of the loop body, {msg} does not hold; the variable state at this point is:\n{state}\n"
            report += f"-> (Hint) Please check whether the loop invariant inside {module} needs to be strengthened or corrected.\n"
            report += f"-> (Hint) If auxiliary functions or lemmas are involved, consider checking whether their definitions (the function body, or the requires/ensures of the Lemma_* procedure) need to be supplemented, strengthened, or revised.\n"
            if after_call:
                report += f"-> (Hint) Please check whether the ensures (postcondition) of the called procedure needs to be strengthened or corrected.\n"

        elif "a precondition for this call could not be proved" in error_msg:
            if str(info).startswith("Lemma_"):
                # [PATCH L4] A lemma call site has no snapshot variables, so the usual lookups would
                # yield "(No data)". What the next round needs here is which lemma failed and the
                # state at the call, so fall back to the nearest instrumented point and label it
                # as such rather than pretending it is the call's own pre-state.
                state, state_at = self._nearest_state_before(start_line, error_line)
                if state is None:
                    state = "(No data: lemma call sites are not instrumented by the snapshot machinery)"
                elif state_at:
                    state = f"(state at the nearest instrumented point: {state_at})\n{state}"
            elif "loop" in info:
                state = self._get_vars_from_snapshot(f"snap_{info}_call")
            else:
                state = self._get_vars_from_snapshot(f"snap_call_{info}_pre")
            report += "Execution trace:\n"
            if is_loop:
                state_entry = self._get_vars_from_snapshot(f"snap_loop{loop_id}_entry")
                report += f"-> At the external entry point of the loop, the variable state provided by Boogie according to the requires(precondition) is:\n{state_entry}\n"
            else:
                state_entry = self._get_vars_from_snapshot(f"snap_{module}_entry")
                report += f"-> At the entry of {module}, the variable state provided by Boogie according to the requires(precondition) is:\n{state_entry}\n"
            after_while = False
            after_call = False
            for i in range(start_line, error_line):
                trace_info = self.line_map.get(i)
                if trace_info and 'while' in trace_info['msg']:
                    after_while = True
                    s1 = self._get_vars_from_snapshot(f"snap_loop{loop_id}_head")
                    report += f"-> At the beginning of the loop body, the variable state provided by Boogie according to the verification logic is:\n{s1}\n"
                elif trace_info and 'call ' in trace_info['msg']:
                    after_call = True
                    report += self._describe_call_transition(trace_info)
            report += f"-> The requires(precondition) of {info} does not hold; the variable state at this point is:\n{state}\n"
            if str(info).startswith("Lemma_"):
                report += (f"-> (Hint) {info} is a lemma call injected by the contract, not a call in the "
                           f"C program. Either weaken its requires so that it covers exactly what the "
                           f"code has modified, move the call, or take the memory snapshot earlier "
                           f"(`X := Mem_int;` before the mutation) and pass the older map to it.\n")
            if after_while:
                report += f"-> (Hint) Please check whether the loop invariant inside {module} needs to be strengthened or corrected.\n"
            if str(info).startswith("Lemma_"):
                # Generic hints below are phrased for "caller's contract is too weak"; for a lemma
                # call that advice is wrong and would send the next round after the wrong clause.
                pass
            else:
                if after_call:
                    report += f"-> (Hint) Please check whether the ensures (postcondition) of the called procedure needs to be strengthened or corrected.\n"
                report += f"-> (Hint) Please check whether the requires (precondition) of {module} needs to be strengthened or corrected.\n"
                report += f"-> (Hint) Please check whether the requires (precondition) of {info} itself needs to be corrected.\n"

        elif "a postcondition could not be proved on this return path" in error_msg:
            report += "Execution trace:\n"
            if is_loop:
                state = self._get_vars_from_snapshot(f"snap_loop{loop_id}_done")
                state_entry = self._get_vars_from_snapshot(f"snap_loop{loop_id}_entry")
                report += f"-> At the external entry point of the loop, the variable state provided by Boogie according to the requires(precondition) is:\n{state_entry}\n"
                report += f"-> At the exit of the loop, the variable state provided by Boogie according to the verification logic is:\n{state}\n"
            else:
                state = self._get_vars_from_snapshot(f"snap_{module}_exit")
                state_entry = self._get_vars_from_snapshot(f"snap_{module}_entry")
                report += f"-> At the entry of {module}, the variable state provided by Boogie according to the requires (precondition) is:\n{state_entry}\n"
                for i in range(start_line, error_line):
                    trace_info = self.line_map.get(i)
                    if trace_info and 'call ' in trace_info['msg']:
                        after_call = True
                        report += self._describe_call_transition(trace_info)
                report += f"-> At the exit of {module}, the variable state at this point is:\n{state}\n"
            post = ""
            for i in range(start_line, error_line):
                trace_info = self.line_map.get(i)
                if trace_info and 'ensures' in trace_info['msg']:
                    post = trace_info['msg']
                    break
            
            report += f"-> {post} does not hold; the variable state at this point is:\n{state}\n"
            report += f"-> (Hint) Please check whether the requires (precondition) of {module} itself needs to be strengthened or corrected.\n"
            if is_loop:
                report += f"-> (Hint) Please check whether the loop invariant inside {module} needs to be strengthened or corrected.\n"
            report += f"-> (Hint) Please check whether the ensures (postcondition) of {module} itself needs to be corrected.\n"
        else:
            report += "Unknown logic error; please inspect manually."
        
        snap_prefix_for_overlap = None
        if "this assertion could not be proved" in error_msg:
            snap_prefix_for_overlap = f"snap_assertion{info}"
            
        elif "this loop invariant could not be proved on entry" in error_msg:
            snap_prefix_for_overlap = f"snap_loop{loop_id}_entry"
            
        elif "this invariant could not be proved to be maintained by the loop" in error_msg:
            snap_prefix_for_overlap = f"snap_loop{loop_id}_body"
            
        elif "a precondition for this call could not be proved" in error_msg:
            if "loop" in str(info):
                snap_prefix_for_overlap = f"snap_{info}_call"
            else:
                snap_prefix_for_overlap = f"snap_call_{info}_pre"
                
        elif "a postcondition could not be proved on this return path" in error_msg:
            if is_loop:
                snap_prefix_for_overlap = f"snap_loop{loop_id}_done"
            else:
                snap_prefix_for_overlap = f"snap_{module}_exit"
        
            
        if snap_prefix_for_overlap:
            overlap_warnings = self._detect_memory_overlap(snap_prefix_for_overlap)
            if overlap_warnings:
                report += "\n[Memory Overlap Warning]\n"
                report += "In the counterexample constructed by Z3, the following memory blocks are found to overlap in physical addresses:\n"
                for w in overlap_warnings:
                    report += w + "\n"
                report += "-> (Hint) If this is not the intended aliasing behavior, be sure to add a separation assertion in the initial Requires or elsewhere, for example `(in_a + Size[in_a] <= in_b) || (in_b + Size[in_b] <= in_a)` to ensure memory safety.\n"
        return report

def extract_procedure_names(boogie_code):
    return re.findall(r'procedure\s+(\w+)\s*\(', boogie_code)

def _find_axioms(parsed_contract):
    """Return a list of JSON fragments that declare a Boogie `axiom`.

    [PATCH L1] The framework now targets the verified-lemma route, where auxiliary facts must be
    proved by a `procedure Lemma_*` rather than assumed. An `axiom` would be an unproved
    assumption, so it is rejected before any verification runs. We scan the structural fields the
    LLM controls, not the raw string, so that the word "axiom" appearing inside a comment or a
    variable name does not trigger a false rejection.
    """
    hits = []
    if not isinstance(parsed_contract, dict):
        return hits

    def _scan(snippet):
        if isinstance(snippet, str) and re.search(r'\baxiom\b', snippet):
            hits.append(snippet)

    for item in parsed_contract.get("global") or []:
        _scan(item)

    insertions = parsed_contract.get("insertions")
    if isinstance(insertions, dict):
        for proc_spec in insertions.values():
            if not isinstance(proc_spec, dict):
                continue
            for code in proc_spec.get("code") or []:
                _scan(code)
            for entry in proc_spec.get("stmts") or []:
                if isinstance(entry, dict):
                    for code in entry.get("code") or []:
                        _scan(code)

    return hits

_BOOGIE_SUMMARY_RE = re.compile(r'finished with (\d+) verified, (\d+) errors?')

def _boogie_counts(output):
    """(verified, errors) from Boogie's summary line, or None if there is no summary.

    [PATCH L6] Replaces the substring test `"0 errors" in output`, which is also true of
    "10 errors", "20 errors", ...
    """
    m = _BOOGIE_SUMMARY_RE.search(output or "")
    return (int(m.group(1)), int(m.group(2))) if m else None

def run_boogie_tool(boogie_code, task_id, logger, seed=1):
    
    filename = f"task_{task_id}_{seed}.bpl" 
    model_filename = f"task_{task_id}_{seed}.model"
    try:
        with open(filename, "w") as f:
            f.write(boogie_code)
        
        proc_names = extract_procedure_names(boogie_code)
        
        for proc_name in proc_names:
            cmd = ["boogie",f"/proverOpt:O:smt.random_seed={seed}",  f"-proc:{proc_name}",f"-mv:{model_filename}","/timeLimit:15", filename]
        
            try:
                
                if os.path.exists(model_filename):
                    os.remove(model_filename)
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout= 17)
                output = result.stdout

                if "time out" in output.lower() or "timeout" in output.lower():
                    return False, {"type": "timeout", "data": "Z3 Solver Timeout"}

                counts = _boogie_counts(output)
                if counts == (0, 0):
                    # [PATCH L6] Nothing was checked: a body-less declaration, or a procedure
                    # excused from verification. Every procedure the translator emits has a body
                    # (checked on all 744 translatable benchmarks), and lemma_guard rejects
                    # body-less lemmas, so this is a defence-in-depth stop, never a pass.
                    return False, {"type": "syntax",
                                   "data": f"procedure {proc_name}: 0 verified, 0 errors -- "
                                           "nothing was checked, refusing to count it as proved\n"
                                           + output}
                if counts is not None and counts[1] == 0:
                    continue
                else:
                    if os.path.exists(model_filename):
                        with open(model_filename, "r") as mf:
                            model_content = mf.read()
                        return False, {"type": "logic", "data": model_content, "output": output}
                    else:
                        return False, {"type": "syntax", "data": output}
            except subprocess.TimeoutExpired:
                logger.warning(f"Boogie timeout")
                return False, {"type": "timeout", "data": "Subprocess TimeoutExpired"}
            except Exception as e:
                logger.error(f"Boogie error: {e}")
                return False, {"type": "error", "data": str(e)}
        
        return True, {}
    finally:
        if os.path.exists(filename): os.remove(filename)
        if os.path.exists(model_filename): os.remove(model_filename)
    
def _procs_with_smoke_guard(boogie_code):
    """Names of procedures whose body contains the `[SMOKE TEST]` guard.

    [PATCH L5] Only translated program procedures get the `assert false` smoke guard. Auxiliary
    `procedure Lemma_*` definitions come verbatim from the contract's `global` field and have no
    guard. A correct lemma therefore verifies with "0 errors" in the smoke build too, and the old
    loop -- which declared the run vacuous as soon as ANY procedure verified -- discarded every
    successful round that used a lemma. Restricting the check to guarded procedures keeps the
    original meaning: "some program procedure can prove `false`, so its contract is vacuous".
    """
    names = []
    for seg in re.split(r'(?m)^(?=\s*procedure\s)', boogie_code):
        m = re.match(r'\s*procedure\s+(\w+)\s*\(', seg)
        if m and "[SMOKE TEST]" in seg:
            names.append(m.group(1))
    return names

def run_smoke_test(boogie_code, task_id, logger, seed=1):
    filename = f"task_{task_id}_smoke_{seed}.bpl"
    try:
        with open(filename, "w") as f:
            f.write(boogie_code)
        proc_names = _procs_with_smoke_guard(boogie_code)
        
        for proc_name in proc_names:
            cmd = ["boogie", f"/proverOpt:O:smt.random_seed={seed}", f"-proc:{proc_name}", "/timeLimit:15", filename]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=17)
                output = result.stdout

                counts = _boogie_counts(output)
                if counts is not None and counts[1] == 0:
                    return True, proc_name
            except subprocess.TimeoutExpired:
                pass
            except Exception as e:
                pass
        
        return False, ""
    finally:
        if os.path.exists(filename): os.remove(filename)

def run_c_dynamic_analysis(debug_c_code, task_id, logger):
    
    c_filename = f"debug_task_{task_id}.c"
    exe_filename = f"./debug_task_{task_id}.exe"
    
    with open(c_filename, "w") as f:
        f.write(debug_c_code)
        
    
    try:
        compile_proc = subprocess.run(["gcc", c_filename, "-o", exe_filename], check=True, capture_output=True, text=True)
        
        run_result = subprocess.run([exe_filename], capture_output=True, text=True, timeout=5)

        trace_log = run_result.stdout
        
        return trace_log

    except Exception as e:
        logger.info(f" Error during dynamic execution of C code: {e}\n{debug_c_code}")
        
        return f"Execution Failed: {e}"
    finally:
        os.remove(c_filename)
        if os.path.exists(exe_filename):
            os.remove(exe_filename)

def check_round(ast, parsed, invariant, task_id, logger):
    """Vet one parsed LLM contract and decide the round's verdict.

    Factored out of `verify_single_file` so that the soundness regression suite
    (`tests/soundness/`) exercises exactly the code the CEGIS loop runs.

    Returns a dict with:
      verdict       "rejected" | "vacuous" | "success" | "failed"
      invariant     the contract JSON string actually handed to the translator
      boogie_final  the generated Boogie text (None when rejected)
      result        run_boogie_tool's failure info (only when verdict == "failed")
      problems      rejection reasons (only when verdict == "rejected")
    """
    # [PATCH L1] The lemma route forbids `axiom` outright: an axiom is an unproved assumption,
    # so a reply containing one cannot be reported as a proof. Reject the round instead of
    # silently verifying against a soft admit. This is a hard guard, not just prompt text.
    axiom_hits = _find_axioms(parsed)
    if axiom_hits:
        logger.warning(
            " Rejected round: the reply used `axiom`, which the lemma route forbids. "
            f"Offending global entries: {axiom_hits}\n"
        )
        return {"verdict": "rejected", "invariant": invariant, "boogie_final": None,
                "result": None, "problems": [f"axiom: {h}" for h in axiom_hits]}

    # [PATCH L6] Boogie proves procedures only partially correct, so a lemma that never returns,
    # has no body, assumes its goal, or is excused from checking would hand any `ensures` to its
    # callers for free. `audit_contract` whitelists what `global` may contain and compiles each
    # recursive lemma's `// decreases:` measure into termination asserts. See lemma_guard.py.
    vetted, guard_problems = audit_contract(parsed)
    if guard_problems:
        logger.warning(
            " Rejected round: the lemma soundness guard refused the `global` field:\n  "
            + "\n  ".join(guard_problems) + "\n"
        )
        return {"verdict": "rejected", "invariant": invariant, "boogie_final": None,
                "result": None, "problems": guard_problems}
    if vetted is not parsed:
        invariant = json.dumps(vetted, ensure_ascii=False)

    boogie_final, _, _, _ = trans_c_to_boogie(ast, invariant)
    success, result = run_boogie_tool(boogie_final, task_id, logger)

    if success:
        boogie_smoke, _, _, _ = trans_c_to_boogie(ast, invariant, smoke_test=True)

        dummy_logger = logging.getLogger("dummy_smoke_logger")
        dummy_logger.addHandler(logging.NullHandler())
        dummy_logger.propagate = False

        smoke_success, _ = run_smoke_test(boogie_smoke, task_id + "_smoke", dummy_logger)
        verdict = "vacuous" if smoke_success else "success"
        return {"verdict": verdict, "invariant": invariant, "boogie_final": boogie_final,
                "result": None, "problems": []}

    return {"verdict": "failed", "invariant": invariant, "boogie_final": boogie_final,
            "result": result, "problems": []}

def verify_single_file(file_path):
    file_name = os.path.basename(file_path)
    
    task_id = file_name
    
    logger = setup_task_logger(task_id)
    logger.info(f"Starting to process file: {file_path}")
    
    agent_B1 = LLMClient("B1", "Expert in formal verification and Boogie syntax")

    with open(file_path, "r") as c:
        c_code = c.read()
    
    c_code = preprocess_code(c_code)
    logger.info(f"\nThe C code to be verified is:\n{c_code}\n")
    
    parser = c_parser.CParser()
    ast = parser.parse(c_code)

    max_retries = 10 
    boogie_template, v_types, ptr_types, s_env = trans_c_to_boogie(ast, {}) 
    logger.info(f"\nThe corresponding Boogie code is:\n{boogie_template}\n")
    
    prompt = f"""
{boogie_template}
Please analyze the semantics of this code, and then generate verification contracts using a 'modular verification' approach. 
Please complete the requires, ensures, and internal loop invariants for each procedure module, so that verification can be performed. The true in the code is a placeholder default value.

{COMMON_PROMPT}
"""

    for i in range(max_retries):
        logger.info(f"\n------- CEGIS round {i+1}  -------")
        c_code_run0 = get_final_Ccode_2(ast)
        execution_trace = run_c_dynamic_analysis(c_code_run0,task_id,logger)
        if "Assertion failed" in execution_trace:
            logger.info("\nThe program itself contains errors; verification complete.")
            return f"{file_name}: BUG_FOUND"
        
        invariant = agent_B1.query(prompt, logger)
        
        if invariant is None:
            logger.warning("API request failed or timed out; waiting 3 seconds before retrying this round...")
            import time
            time.sleep(3)
            continue  
        try:
            
            clean_json = invariant.replace("```json", "").replace("```", "").strip()
            
            start_idx = clean_json.find('{')
            end_idx = clean_json.rfind('}')
            if start_idx != -1 and end_idx != -1:
                clean_json = clean_json[start_idx:end_idx+1]
            
            
            parsed = json.loads(clean_json)
            invariant = clean_json
        except Exception as e:
            continue

        outcome = check_round(ast, parsed, invariant, task_id, logger)
        if outcome["verdict"] == "rejected":
            continue
        invariant = outcome["invariant"]
        boogie_final = outcome["boogie_final"]
        if outcome["verdict"] == "vacuous":
            continue
        if outcome["verdict"] == "success":
            logger.info(f"\nSuccess! Verification complete. The full Boogie code is: \n{boogie_final}")
            return f"{file_name}: SUCCESS"
        result = outcome["result"]

        failure_type = result.get("type", "error")
        failure_data = result.get("data", "")

        execution_trace = ""

        if failure_type == "timeout":
            logger.warning(f" Solver timeout: the quantifiers generated by the LLM are too complex; proceeding to the next round...")
            continue
        elif failure_type == "syntax":
            logger.error(f" Syntax error occurred. Please check the translator logic... The Boogie code is: \n{boogie_final}")
            
            continue
        elif failure_type == "logic":
            logger.info(f" Verification logic failed; entering dynamic analysis...")
            
            ast_copy = copy.deepcopy(ast)
            boogie_snap, _, _, _ = insert_inv_to_boogie_with_snap(ast_copy,invariant)
            state, msg = run_boogie_tool(boogie_snap,task_id,logger)
            if not state:
                model_data = msg.get("data","")
                ast_copy = copy.deepcopy(ast)
                c_code_run =  get_final_Ccode(ast_copy,model_data)
                execution_trace = run_c_dynamic_analysis(c_code_run,task_id,logger)
        
       
        if "Assertion failed" in execution_trace:
            logger.info("\nThe program itself contains errors; verification complete.")
            return f"{file_name}: BUG_FOUND"
        else:
            ast_copy = copy.deepcopy(ast)
            boogie_FULLSNAP, v_types, ptr_types, s_env  = insert_inv_to_boogie_FULLSNAP(ast_copy,invariant)
            
            state1, msg1 = run_boogie_tool(boogie_FULLSNAP,task_id,logger,seed=1)
            output1 = msg1.get("output","")
            model_data1 = msg1.get("data","")

            state2, msg2 = run_boogie_tool(boogie_FULLSNAP,task_id,logger,seed=2)
            output2 = msg2.get("output","")
            model_data2 = msg2.get("data","")
            
            feedback1 = BoogieFeedbackGenerator(boogie_FULLSNAP, model_data1, v_types, ptr_types, s_env)
            report1 = feedback1.generate_report(output1)
            
            feedback2 = BoogieFeedbackGenerator(boogie_FULLSNAP, model_data2, v_types, ptr_types, s_env)
            report2 = feedback2.generate_report(output2)
            
            
            prompt = f"{boogie_final}\n"
            prompt += "### Verification failed: two counterexample scenarios were found. ###\n"
            prompt += f"--- Counterexample scenario A ---\n{report1}\n"
            prompt += f"--- Counterexample scenario B ---\n{report2}\n"
            prompt += f"""
Based on the above information, please further refine the input state (Requires) and output state (Ensures) of the corresponding modules, as well as the internal invariants (Invariants), so that the reported errors are fixed.
The output must return the requires, ensures, and invariants for all procedure modules. If auxiliary functions or lemmas are needed, they must be returned together under the global item — helper functions WITH their bodies, and every auxiliary fact as a `procedure Lemma_*` that proves itself by recursion. Do not use `axiom`. If a lemma has to be called inside a procedure, also return the "insertions" object (snapshot declarations, and the calls anchored by the `// #N` labels of the skeleton).

{COMMON_PROMPT}
"""
            logger.info(prompt)

    logger.info(f"Maximum retry attempts reached; verification stopped.\n")
    return f"{file_name}: TIMEOUT"


if __name__ == "__main__":
    
    if not os.path.exists(TARGET_FOLDER):
        os.makedirs(TARGET_FOLDER)
        print(f"Please create the {TARGET_FOLDER} folder and place the code inside.")
        exit()
        
    if not os.path.exists(LOG_FOLDER):
        os.makedirs(LOG_FOLDER)

    files = [os.path.join(TARGET_FOLDER, f) for f in os.listdir(TARGET_FOLDER) if f.endswith(".c")]
    
    if not files:
        print("No files.")
        exit()

    print(f"Starting verification of {len(files)} tasks...")
    print(f"For detailed logs, please check the individual log files under the {LOG_FOLDER} folder.")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_file = {executor.submit(verify_single_file, f): f for f in files}
        
        for future in concurrent.futures.as_completed(future_to_file):
            file_name = future_to_file[future]
            try:
                res = future.result()
                results.append(res)
                
                print(f"[{file_name}] completed -> {res}")
            except Exception as exc:
                print(f"[{file_name}] thread error -> {exc}")

    summary_lines = []
    summary_lines.append("=== Summary ===")
    for res in sorted(results):
        summary_lines.append(str(res))
    
    
    print("\n" + "\n".join(summary_lines))
    
    summary_file = os.path.join(LOG_FOLDER, "summary.txt")
    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write("\n".join(summary_lines))
    
    print(f"\nSummary saved to: {summary_file}")