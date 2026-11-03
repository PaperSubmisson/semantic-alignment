import argparse
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True

from framework.verification.common import read_json, write_json
from framework.workflow import ROOT, selected_tasks, prepare_tasks, run_tasks, summary, proof_module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('list', 'prepare', 'run'):
        p = sub.add_parser(command)
        p.add_argument('--manifest', type=Path, default=ROOT / 'benchmark/manifest.json')
        p.add_argument('--ids', nargs='+')
        p.add_argument('--categories', nargs='+', choices=['C0', 'C1', 'C2', 'C3', 'C4', 'C5', 'C6'])
        if command != 'list':
            p.add_argument('--out', type=Path, required=True)
            p.add_argument('--config', type=Path)
            p.add_argument('--workers', type=int, default=10)
        if command == 'prepare':
            p.add_argument('--translate-only', action='store_true')
        if command == 'run':
            p.add_argument('--rounds', type=int, default=10)
            p.add_argument('--timeout', type=int, default=60)
    p = sub.add_parser('translate')
    p.add_argument('sources', nargs='+', type=Path)
    p.add_argument('--spec', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('status')
    p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('prompt')
    p.add_argument('prepared', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('check-proof')
    p.add_argument('prepared', type=Path)
    p.add_argument('proof', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--config', type=Path)
    p.add_argument('--timeout', type=int, default=60)
    args = parser.parse_args(argv)
    if hasattr(args, 'workers') and not 1 <= args.workers <= 10:
        parser.error('--workers must be between 1 and 10')
    if hasattr(args, 'rounds') and not 1 <= args.rounds <= 10:
        parser.error('--rounds must be between 1 and 10')
    if hasattr(args, 'timeout') and args.timeout <= 0:
        parser.error('--timeout must be positive')
    config = read_json(args.config) if getattr(args, 'config', None) else {}
    if getattr(args, 'config', None) and config.get('api_key_file'):
        config['api_key_file'] = str((args.config.resolve().parent / config['api_key_file']).resolve())
    needs_tools = args.command in ('run', 'check-proof') or (args.command == 'prepare' and not args.translate_only)
    if needs_tools:
        if os.name != 'posix':
            raise RuntimeError('Run verification in Linux or WSL')
        from framework.settings import configure
        configure(config, args.out)
    if args.command in ('list', 'prepare', 'run'):
        benchmark, tasks = selected_tasks(args.manifest, args.ids, args.categories)
        if args.command == 'list':
            result = {'total': len(tasks), 'tasks': [{'id': t['id'], 'category': t['category'],
                       'configured': t.get('spec') is not None} for t in tasks]}
        elif args.command == 'prepare':
            result = prepare_tasks(tasks, benchmark, args.out, args.workers, not args.translate_only)
        else:
            result = run_tasks(tasks, benchmark, args.out, config, args.workers, args.rounds, args.timeout)
    elif args.command == 'translate':
        from framework.specgen_mode.routing import translate
        code, mapping, decision = translate(args.sources, read_json(args.spec) if args.spec else {})
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(code, encoding='utf8')
        write_json(args.out.with_suffix('.map.json'), mapping)
        result = {'status': 'translated', **decision}
    elif args.command == 'status':
        result = summary(args.out)
    else:
        task = read_json(args.prepared / 'task.json')
        module = proof_module(task)
        result = module.export_prompt(args.prepared, args.out) if args.command == 'prompt' else module.check(
            args.prepared, read_json(args.proof), args.out, args.timeout)
    print(json.dumps({k: v for k, v in result.items() if k not in ('results',)}, ensure_ascii=False, indent=2))
    if args.command == 'check-proof':
        return 0 if result['status'] == 'verified' else 1
    return 1 if any(k in result.get('counts', {}) for k in ('preparation_failed', 'frontend_failed', 'runner_error')) else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps({'status': 'error', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
