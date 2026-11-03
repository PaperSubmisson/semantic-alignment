"""Classify a task's supplied Java sources, without changing either translator."""
from pathlib import Path
import re
from .frontend import parser
from .model import TranslationError, walk

LOOPS = {'for_statement', 'enhanced_for_statement', 'while_statement', 'do_statement'}


def classify(sources):
    sources = [Path(p).resolve() for p in sources]
    if not sources:
        raise ValueError('A task needs at least one Java source file')
    p = parser()
    loops = []
    for path in sources:
        data = path.read_bytes()
        if re.search(rb'\\u+[0-9a-fA-F]{4}', data):
            raise TranslationError('Unicode preprocessing required before routing', path=path)
        tree = p.parse(data)
        if tree.root_node.has_error:
            node = next((n for n in walk(tree.root_node) if n.type == 'ERROR' or n.is_missing), tree.root_node)
            raise TranslationError('Java syntax error during routing', node, path)
        for n in walk(tree.root_node):
            if n.type in LOOPS:
                loops.append({'file': str(path), 'line': n.start_point.row + 1,
                              'column': n.start_point.column + 1, 'kind': n.type})
    return {'route': 'fixed_goal' if loops else 'spec_generation',
            'source_files': [str(p) for p in sources], 'loop_count': len(loops), 'loops': loops,
            'scope': 'All supplied task sources; library implementations are not expanded.'}


def translate(sources, spec=None):
    decision = classify(sources)
    if decision['route'] == 'fixed_goal':
        from .compiler import translate as emit
    else:
        from framework.specgen_mode.compiler import translate as emit
    code, mapping = emit(sources, spec)
    return code, mapping, decision
