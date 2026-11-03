"""C0 lexical extension, preserving the existing loop route."""
from pathlib import Path
from framework.j2silver.routing import classify as original_classify,LOOPS
from framework.j2silver.model import walk,TranslationError
from .frontend import parser
from .preprocess import normalize
def classify(sources):
    sources=[Path(p).resolve() for p in sources]
    if not any(b'\\u' in p.read_bytes() for p in sources):return original_classify(sources)
    loops=[]
    for path in sources:
        tree=parser().parse(normalize(path.read_bytes(),path))
        if tree.root_node.has_error:raise TranslationError('Invalid normalized source',path=path)
        loops += [{'file':str(path),'line':n.start_point.row+1,'column':n.start_point.column+1,'kind':n.type} for n in walk(tree.root_node) if n.type in LOOPS]
    return {'route':'fixed_goal' if loops else 'spec_generation','source_files':list(map(str,sources)),'loop_count':len(loops),'loops':loops,'scope':'All task sources after standalone Unicode character-literal normalization.'}
def translate(sources,spec=None):
    d=classify(sources)
    if d['loop_count']:
        from framework.j2silver.compiler import translate as emit
    else:
        from .compiler import translate as emit
    code,mapping=emit(sources,spec)
    return code,mapping,d
