"""Inspect Silver with the actual frontend and compile the Java bridge on demand."""
from pathlib import Path
from threading import Lock
from ..settings import settings, classpath
from .common import read_json, run_process, digest
from .ast_index import walk, offset, index

_compile_lock = Lock()


def inspect(path, output):
    path, output = Path(path).resolve(), Path(output).resolve()
    build = settings.build_dir
    source = Path(__file__).resolve().parents[1] / 'silver_bridge/SilverInspect.java'
    stamp = build / 'source.sha256'
    identity = digest(source.read_bytes() + settings.javac.encode())
    with _compile_lock:
        if not stamp.exists() or stamp.read_text() != identity or not (build / 'SilverInspect.class').exists():
            build.mkdir(parents=True, exist_ok=True)
            result, _, err = run_process([settings.javac, '-encoding', 'UTF-8', '-d', build, source],
                                         build, build / 'compile', 60)
            if result['returncode'] != 0:
                raise RuntimeError('Silver bridge compilation failed: ' + err)
            stamp.write_text(identity)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    result, _, _ = run_process([settings.java, '-Xss32m', '-cp', classpath(build),
                                'SilverInspect', path, output], settings.vercors_dir,
                               str(output) + '.frontend', 90)
    ast = read_json(output) if output.exists() else {'ok': False, 'errors': ['Frontend produced no AST']}
    if result['returncode'] != 0:
        ast['ok'] = False
    ast['process'] = result
    return ast
