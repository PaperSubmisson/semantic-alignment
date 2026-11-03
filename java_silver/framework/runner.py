"""Bounded proof repair; only logical failures update the prompt."""
from pathlib import Path
import json
import time
from .verification.common import read_json, write_json
from .verification.reply import parse_reply

def timestamp(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def task_run(folder, client, output, max_rounds=10, timeout=60):
    folder, output = Path(folder), Path(output)
    mode = read_json(folder / "task.json")["mode"]
    if mode == "spec_generation":
        from .specgen_mode.proof import check
        from .specgen_mode.prompting import retry_messages
    else:
        from .j2silver.proof import check
        from .j2silver.prompting import retry_messages
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'result.json').exists(): return read_json(output / 'result.json')
    init = read_json(folder / 'initial.messages.json')
    messages, history, start = init, [], 1
    checkpoint = output / 'checkpoint.json'
    if checkpoint.exists():
        state = read_json(checkpoint)
        messages, history, start = state['messages'], state['history'], state['next_round']
    for i in range(start, max_rounds + 1):
        d = output / f'round-{i:02d}'
        d.mkdir(exist_ok=True)
        write_json(d / 'request.json', {'messages': messages, 'started_at': timestamp()})
        # An interruption after a request has begun consumes that request slot.
        write_json(checkpoint, {'next_round': i + 1, 'messages': messages, 'history': history})
        candidate = None
        try:
            response = client.query(messages)
            write_json(d / 'response.json', response)
        except Exception as error:
            result = {'status': 'request_failed', 'error_type': type(error).__name__,
                      'message': str(error).replace(getattr(client, 'key', '') or '\0', '[REDACTED]')}
        else:
            try: candidate = parse_reply(response['content'])
            except (ValueError, TypeError, KeyError) as error:
                result = {'status': 'json_parse_failed', 'message': str(error)}
            else:
                try: result = check(folder, candidate, d, timeout)
                except Exception as error:
                    result = {'status': 'tool_error', 'error_type': type(error).__name__, 'message': str(error)}
        result['update_prompt'] = result['status'] == 'verification_failed' and bool(result.get('diagnostics'))
        if result['update_prompt']:
            history.append({'round': i, 'kinds': [x['kind'] for x in result['diagnostics']]})
            messages = retry_messages(messages, candidate, result, history, init)
        write_json(d / 'result.json', result)
        write_json(checkpoint, {'next_round': i + 1, 'messages': messages, 'history': history})
        print(json.dumps({'task': read_json(folder / 'task.json')['id'], 'round': i, 'status': result['status']}), flush=True)
        if result['status'] == 'verified':
            final = {'status': 'verified', 'success_round': i, 'finished_at': timestamp(), 'trivial_postconditions': result.get('trivial_postconditions', [])}
            write_json(output / 'result.json', final)
            return final
    final = {'status': 'budget_exhausted', 'completed_rounds': max_rounds, 'finished_at': timestamp()}
    write_json(output / 'result.json', final)
    return final
