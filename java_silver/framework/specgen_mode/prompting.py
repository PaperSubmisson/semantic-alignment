import json
from pathlib import Path

PROMPTS = Path(__file__).parent / 'prompts'


def load(name):
    return (PROMPTS / name).read_text(encoding='utf-8').strip()


def model_notes(base):
    notes=[]
    if '_c0_state' in base:
        notes.append('Shared state: _c0_state is one Ref forwarded across calls. c0_entry_ methods allocate and initialize it once. Internal methods receive the current state, not a freshly initialized state. Return permissions and state-value relations needed by callers; a caller must prove every internal requires. Fixed entry conditions cannot be strengthened.')
    if 'field c0_type:' in base:
        notes.append('Runtime tags: c0_type is an ordinary heap field holding the numeric tags assigned in allocation code. Reading a tag requires permission. Preserve tag resources and values through method calls where later subtype tests use them. A static Ref parameter alone does not establish a tag value.')
    if 'field c0_box:' in base:
        notes.append('Boxes: c0_box stores an integral payload. Its Ref is distinct from the payload. Dereferencing requires non-null and permission; do not duplicate permission for aliases or infer object identity from equal contents.')
    if 'field sb_chars:' in base:
        notes.append('Mutable text: sb_chars stores UTF-16 code units and sb_capacity stores capacity. Aliases share both fields. A value snapshot of sb_chars is immutable; a Ref snapshot remains aliased. Keep capacity and content facts separate.')
    if 'function c0_signed' in base:
        notes.append('Explicit width operations: c0_unsigned and c0_signed normalize to the width argument. Shifts mask their distance. Ordinary Int arithmetic stays mathematical; use the supplied helper definitions for explicit bitwise and narrowing behavior.')
    if 'method c0_floor_' in base:
        notes.append('Real conversion: implemented c0_floor helpers establish integral bounds against exact Perm-valued arithmetic. Caller code handles signs and target-range clamping. No NaN or Infinity values exist in this model.')
    if '_c0_catch' in base:
        notes.append('Exceptional control flow: conditional branches and goto target catch labels. Proofs must cover every reachable path, including catch paths. Only the listed anchors are editable; after a label executes on incoming jumps too.')
    if 'method c0_index_m' in base:
        notes.append('String search: c0_match defines a substring match. c0_index_m/c0_last_m have checked implementations and summaries specifying first/last matches and absence. Their library-loop invariants are fixed and are verified with the program; they are not editable task loops.')
    if 'function c0_char_' in base:
        notes.append('Character helpers are defined finite decision trees over UTF-16 code units. Use the exact supplied definitions; do not assume ASCII-only inputs.')
    if 'domain C0LibraryValues' in base:
        notes.append('C0LibraryValues contains conservative uninterpreted pure functions with no property axioms. Equal explicit arguments give equal results. No other result fact follows unless proved from surrounding code. Do not invent library properties or add axioms. Validity predicates in fixed requires describe the reviewed input domain.')
    if 'field c0_map_keys:' in base:
        notes.append('Maps: c0_map_keys and c0_map_values are equal-length parallel sequences. Keys are encoded text contents (a separate tag denotes null); newest bindings appear first. Aliases share these fields. Lookup and unique-key helpers have bodies; use those bodies and checked contracts. Key snapshots do not specify iteration order.')
    if '_c0_thrown' in base:
        notes.append('Exceptional results: _c0_thrown == null denotes normal return; otherwise it carries the thrown reference. Ordinary res matters only on normal return. Return tag/field permissions and prove value summaries needed by callers. Fixed root no-uncaught-exception conditions remain in force.')
    return '\n\n[Additional declarations used by this program]\n'+'\n'.join(notes) if notes else ''


def initial(folder):
    from framework.verification.bridge import index
    folder = Path(folder)
    task = json.loads((folder / 'task.json').read_text(encoding='utf-8'))
    if task.get('mode') != 'spec_generation':
        raise ValueError('Expected a spec-generation task')
    base = (folder / 'baseline.vpr').read_text(encoding='utf-8')
    ast = json.loads((folder / 'baseline.ast.json').read_text(encoding='utf-8'))
    slots = index(base, ast)
    editable = set(task['editable_methods'])
    ctx = {'mode': task['mode'], 'roots': task['entrypoints'],
           'specification_methods': task['spec_methods'], 'editable_methods': task['editable_methods'],
           'numeric_semantics': task['numeric_semantics'], 'environment': task.get('environment', []),
           'loops': {k: {'method': v['method'], 'line': v['line']} for k, v in slots['loops'].items() if v['method'] in editable},
           'anchors': {k: {f: v[f] for f in ('method', 'line', 'kind', 'positions') if f in v}
                       for k, v in slots['anchors'].items() if v['method'] in editable}}
    return [{'role': 'system', 'content': load('system.md')},
            {'role': 'user', 'content': load('encoding.md') + model_notes(base) + '\n\n' + load('output.md')
             + '\n\nTASK AND EDITABLE IDS\n' + json.dumps(ctx, ensure_ascii=False, separators=(',', ':'))
             + '\n\nSILVER IMPLEMENTATION\n' + base}]


def retry_messages(current, candidate, feedback, history, initial_messages):
    if feedback.get('status') != 'verification_failed' or not feedback.get('diagnostics'):
        return current
    result = [dict(m) for m in initial_messages]
    result[-1]['content'] += '\n\nRefine the complete specification using the logical feedback.\nCURRENT CANDIDATE\n' + json.dumps(candidate, ensure_ascii=False)
    result[-1]['content'] += '\nSILICON FEEDBACK\n' + json.dumps(feedback, ensure_ascii=False)
    result[-1]['content'] += '\nRECENT LOGICAL FAILURES\n' + json.dumps(history[-3:], ensure_ascii=False)
    return result
