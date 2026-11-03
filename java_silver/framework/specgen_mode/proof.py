import json
from pathlib import Path
from framework.j2silver.proof import write
from .candidate import build


def export_prompt(folder, output):
    from .prompting import initial
    messages = initial(folder)
    output = Path(output)
    write(output, messages)
    output.with_suffix('.md').write_text('\n\n'.join('## ' + m['role'] + '\n\n' + m['content'] for m in messages) + '\n', encoding='utf-8')
    return {'status': 'prompt_exported', 'messages': str(output)}


def check(folder, candidate, output, timeout=60):
    from framework.verification.bridge import inspect
    from framework.verification.candidate import Rejected, audit_ast
    from .backend import verify
    from framework.j2silver.termination_domains import GENERATED_DOMAINS
    folder, output = Path(folder).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    task = json.loads((folder / 'task.json').read_text(encoding='utf-8'))
    base = (folder / 'baseline.vpr').read_text(encoding='utf-8')
    base_ast = json.loads((folder / 'baseline.ast.json').read_text(encoding='utf-8'))
    try:
        code, plan = build(base, base_ast, task, candidate)
        path = output / 'candidate.vpr'
        path.write_text(code, encoding='utf-8')
        write(output / 'proof.json', candidate)
        write(output / 'insertion-map.json', plan['source_map'])
        ast = inspect(path, output / 'candidate.ast.json')
        if not ast.get('ok'):
            feedback = {'status': 'parse_type_error', 'diagnostics': ast.get('errors', [])}
        else:
            write(output / 'audit.json', audit_ast(base_ast, ast, plan, candidate, task, GENERATED_DOMAINS))
            feedback = verify(path, output, timeout=timeout, source_map=plan['source_map'])
            feedback.pop('candidate_sha256', None)
            feedback['model_caveat'] = 'A proof-obligation counterexample need not be a reachable execution.'
            feedback['trivial_postconditions'] = [mid for mid, v in candidate.get('methods', {}).items()
                if v.get('ensures_add') and all(x.strip().strip('() ').lower() == 'true' for x in v['ensures_add'])]
            for item in feedback.get('diagnostics', []):
                item.pop('java_location', None)
    except (Rejected, KeyError, TypeError, AttributeError) as error:
        feedback = {'status': 'guard_rejected', 'diagnostics': [], 'message': str(error)}
    write(output / 'feedback.json', feedback)
    return feedback
