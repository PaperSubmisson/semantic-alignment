"""Local Silver proof/prompt interface, without an external model call.

Apply proof annotations through the Silver AST and return Silicon diagnostics.
"""
import json
from pathlib import Path


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def export_prompt(folder, output):
    from .prompting import initial
    messages = initial(folder)
    output = Path(output)
    write(output, messages)
    output.with_suffix('.md').write_text(
        '\n\n'.join('## ' + m['role'] + '\n\n' + m['content'] for m in messages) + '\n',
        encoding='utf-8')
    return {'status': 'prompt_exported', 'messages': str(output), 'preview': str(output.with_suffix('.md'))}


def check(folder, candidate, output, timeout=60):
    from framework.verification.bridge import inspect
    from framework.verification.candidate import build, audit_ast, Rejected
    from framework.verification.silicon_backend import verify
    from .termination_domains import GENERATED_DOMAINS
    folder, output = Path(folder).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    task = json.loads((folder / 'task.json').read_text(encoding='utf-8'))
    base = (folder / 'baseline.vpr').read_text(encoding='utf-8')
    base_ast = json.loads((folder / 'baseline.ast.json').read_text(encoding='utf-8'))
    try:
        text, plan = build(base, base_ast, task, candidate)
        path = output / 'candidate.vpr'
        path.write_text(text, encoding='utf-8')
        write(output / 'proof.json', candidate)
        write(output / 'insertion-map.json', plan['source_map'])
        ast = inspect(path, output / 'candidate.ast.json')
        if not ast.get('ok'):
            feedback = {'status': 'parse_type_error', 'diagnostics': ast.get('errors', [])}
        else:
            write(output / 'audit.json', audit_ast(base_ast, ast, plan, candidate, task, GENERATED_DOMAINS))
            feedback = verify(path, output, timeout=timeout, source_map=plan['source_map'])
            feedback.pop('candidate_sha256', None)
            feedback['model_caveat'] = 'Proof-obligation models need not be reachable program executions.'
            for diagnostic in feedback.get('diagnostics', []):
                diagnostic.pop('java_location', None)
    except (Rejected, KeyError, TypeError, AttributeError) as error:
        feedback = {'status': 'guard_rejected', 'diagnostics': [], 'message': str(error)}
    write(output / 'feedback.json', feedback)
    return feedback
