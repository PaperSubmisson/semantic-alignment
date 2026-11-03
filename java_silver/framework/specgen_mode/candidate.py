"""Explicit root-ensures policy; default fixed-goal construction remains unchanged."""
from framework.verification.candidate import build as shared_build, check


def method_policy(mid, clauses, task):
    check(task.get('mode') == 'spec_generation', 'Wrong task mode')
    check(isinstance(clauses, dict), 'Method contract must be an object')
    if mid in task.get('entrypoints', []):
        check(not clauses.get('requires_add'), 'Root input conditions are fixed')


def build(base, ast, task, candidate):
    check(task.get('mode') == 'spec_generation', 'Spec-generation checker needs a spec-generation task')
    check(isinstance(candidate, dict), 'Proof must be a JSON object')
    methods = candidate.get('methods', {})
    check(isinstance(methods, dict), 'methods must be an object')
    for mid in task.get('spec_methods', []):
        clauses = methods.get(mid, {})
        check(isinstance(clauses, dict) and bool(clauses.get('ensures_add')),
              'Generate at least one postcondition for ' + mid)
    proof = dict(candidate)
    proof.setdefault('loops', {})
    from framework.verification.bridge import index
    slots = index(base, ast)
    editable = set(task['editable_methods'])
    for mid in proof.get('ghosts',{}):
        check(mid in editable,'Library implementation proof slots are fixed: '+mid)
    for lid in proof['loops']:
        check(lid in slots['loops'] and slots['loops'][lid]['method'] in editable,
              'Library implementation proof slots are fixed: ' + lid)
    proof['loops']=dict(proof['loops'])
    for lid,slot in slots['loops'].items():
        if slot['method'] not in editable:
            proof['loops'][lid]={'invariants':[]}
    for insertion in proof.get('insertions', []):
        aid = insertion.get('anchor_id')
        check(aid in slots['anchors'] and slots['anchors'][aid]['method'] in editable,
              'Library implementation proof slots are fixed: ' + str(aid))
    return shared_build(base, ast, task, proof, method_policy=method_policy, ordered_contracts=True)
