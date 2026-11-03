"""Prepare benchmark inputs and run the two verification routes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from pathlib import Path
import json

from .verification.common import read_json, write_json
from .specgen_mode.routing import classify

ROOT = Path(__file__).resolve().parents[1]


def selected_tasks(manifest_path, ids=None, categories=None):
    manifest_path = Path(manifest_path).resolve()
    tasks = read_json(manifest_path)['tasks']
    if ids and set(ids) - {t['id'] for t in tasks}:
        raise ValueError('Unknown task IDs: ' + ', '.join(sorted(set(ids) - {t['id'] for t in tasks})))
    selected = [t for t in tasks if (not ids or t['id'] in ids)
                and (not categories or t['category'] in categories)]
    if not selected:
        raise ValueError('No tasks selected')
    return manifest_path.parent, selected


def key(task):
    return task['id'].replace('/', '__')


def emit(task, benchmark):
    sources = [benchmark / p for p in task['sources']]
    decision = classify(sources)
    if decision['route'] != task['mode']:
        raise ValueError('Source loop classification disagrees with task mode: ' + task['id'])
    if task.get('spec') is None:
        raise ValueError('This task has no framework input specification yet')
    spec = read_json(benchmark / task['spec'])
    if task['mode'] == 'spec_generation':
        from .specgen_mode.compiler import translate
    else:
        from .j2silver.compiler import translate
    code, mapping = translate(sources, spec)
    return code, mapping, decision


def proof_module(task):
    if task['mode'] == 'spec_generation':
        from .specgen_mode import proof
    else:
        from .j2silver import proof
    return proof


def prepare_one(task, benchmark, folder, frontend=True):
    from .verification.bridge import inspect, index
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if task.get('spec') is None:
        result = {'id': task['id'], 'status': 'unsupported', 'reason': 'Framework input specification pending'}
        write_json(folder / 'preparation.json', result)
        return result
    try:
        code, mapping, decision = emit(task, benchmark)
        (folder / 'baseline.vpr').write_text(code, encoding='utf8')
        write_json(folder / 'baseline.map.json', mapping)
        write_json(folder / 'routing.json', decision)
        write_json(folder / 'task.json', task)
        result = {'id': task['id'], 'status': 'translated'}
        if frontend:
            ast = inspect(folder / 'baseline.vpr', folder / 'baseline.ast.json')
            if not ast.get('ok'):
                result.update(status='frontend_failed', errors=ast.get('errors', []))
            else:
                write_json(folder / 'slots.json', index(code, ast))
                proof_module(task).export_prompt(folder, folder / 'initial.messages.json')
                result['status'] = 'ready'
    except Exception as error:
        result = {'id': task['id'], 'status': 'preparation_failed',
                  'error_type': type(error).__name__, 'message': str(error)}
    write_json(folder / 'preparation.json', result)
    return result


def prepare_tasks(tasks, benchmark, output, workers=10, frontend=True):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(prepare_one, t, benchmark, output / 'inputs' / key(t), frontend): t for t in tasks}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    summary = {'total': len(tasks), 'counts': dict(Counter(r['status'] for r in results)),
               'results': sorted(results, key=lambda r: r['id'])}
    write_json(output / 'preparation.json', summary)
    write_json(output / 'inventory.json', {'tasks': tasks, 'total_tasks': len(tasks)})
    return summary


def summary(output):
    output = Path(output)
    tasks = read_json(output / 'inventory.json')['tasks']
    results, categories, counts = [], {}, Counter()
    requests = 0
    for task in tasks:
        folder = output / 'tasks' / key(task)
        rounds = sorted(folder.glob('round-*'))
        n = sum((d / 'request.json').exists() for d in rounds)
        requests += n
        if not (folder / 'result.json').exists():
            continue
        result = dict(read_json(folder / 'result.json'), id=task['id'], category=task['category'], model_requests=n)
        result['round_statuses'] = [read_json(d / 'result.json')['status'] for d in rounds if (d / 'result.json').exists()]
        results.append(result)
        counts[result['status']] += 1
        categories.setdefault(task['category'], Counter())[result['status']] += 1
    result = {'total': len(tasks), 'completed': len(results), 'model_requests': requests,
              'counts': dict(counts), 'categories': {k: dict(v) for k, v in categories.items()}, 'results': results}
    write_json(output / 'summary.json', result)
    return result


def run_tasks(tasks, benchmark, output, config, workers=10, rounds=10, timeout=60):
    from .verification.llm_client import LiveClient
    from .runner import task_run
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    public = {k: v for k, v in config.items() if k not in ('api_key_file', 'api_key_env', 'tools')}
    settings = {'tasks': tasks, 'model': public, 'rounds': rounds, 'timeout': timeout}
    settings_path = output / 'settings.json'
    if settings_path.exists() and read_json(settings_path) != settings:
        raise ValueError('Use a new output directory for different tasks or model settings')
    # Validate credentials before doing expensive preparation, but do not call the API yet.
    LiveClient(config)
    lock = output / 'active.lock'
    with lock.open('x', encoding='utf8') as file:
        file.write('Running; remove this file only after a terminated process has stopped.\n')
    try:
        write_json(settings_path, settings)
        prepare_tasks(tasks, benchmark, output, workers)
        def one(task):
            prepared = output / 'inputs' / key(task)
            dest = output / 'tasks' / key(task)
            state = read_json(prepared / 'preparation.json')
            if state['status'] != 'ready':
                result = {'status': state['status'], 'preparation': state}
                write_json(dest / 'result.json', result)
                return result
            return task_run(prepared, LiveClient(config), dest, rounds, timeout)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(one, t): t for t in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    future.result()
                except Exception as error:
                    write_json(output / 'tasks' / key(task) / 'result.json',
                               {'status': 'runner_error', 'error_type': type(error).__name__, 'message': str(error)})
                summary(output)
        return summary(output)
    finally:
        lock.unlink(missing_ok=True)
