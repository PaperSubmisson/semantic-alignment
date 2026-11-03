"""External tool configuration shared by frontend and verifier."""
from dataclasses import dataclass
from pathlib import Path
import os
import shutil


@dataclass
class Settings:
    java: str = 'java'
    javac: str = 'javac'
    vercors_dir: Path = Path('.')
    z3: Path = Path('z3')
    build_dir: Path = Path('build')


settings = Settings()


def executable(value):
    found = shutil.which(str(value))
    if found:
        return str(Path(found).resolve())
    path = Path(value).expanduser()
    if path.is_file():
        return str(path.resolve())
    raise FileNotFoundError('Executable not found: ' + str(value))


def configure(config, output):
    tools = config.get('tools', {})
    settings.java = executable(tools.get('java', 'java'))
    settings.javac = executable(tools.get('javac', 'javac'))
    location = tools.get('vercors_dir') or os.environ.get('VERCORS_HOME')
    if not location:
        raise ValueError('Set tools.vercors_dir or VERCORS_HOME to a VerCors 2.4.0 installation')
    settings.vercors_dir = Path(location).expanduser().resolve()
    if not (settings.vercors_dir / 'vercors.jar').is_file():
        raise FileNotFoundError('vercors.jar not found in ' + str(settings.vercors_dir))
    settings.z3 = Path(executable(tools.get('z3') or settings.vercors_dir / 'deps/unix/z3/bin/z3'))
    settings.build_dir = Path(output).resolve() / 'bridge'


def classpath(*prefix):
    return os.pathsep.join(str(p) for p in (*prefix, settings.vercors_dir / 'vercors.jar',
        settings.vercors_dir / 'res', settings.vercors_dir / 'deps', settings.vercors_dir / 'lib'))
