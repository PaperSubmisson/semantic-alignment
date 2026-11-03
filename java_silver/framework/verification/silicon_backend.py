"""Run Silicon with native diagnostics and variable counterexamples."""
from pathlib import Path
from ..settings import settings, classpath
from .common import run_process, write_json, digest
from .feedback import normalize


def verify(candidate, directory, label='silicon', timeout=60, source_map=None, counterexample=True):
    candidate, directory = Path(candidate).resolve(), Path(directory).resolve()
    command = [settings.java, '-Xss32m', '-cp', classpath(), 'viper.silicon.SiliconRunner',
               '--z3Exe', settings.z3, '--plugin', 'viper.silver.plugin.standard.termination.TerminationPlugin',
               '--numberOfParallelVerifiers', '1', '--timeout', str(timeout),
               '--assertTimeout', '10000', '--exhaleMode', '1']
    if counterexample:
        command += ['--counterexample', 'variables']
    command += ['--numberOfErrorsToReport', '0', candidate]
    result, stdout, stderr = run_process(command, settings.vercors_dir, directory / label, timeout + 30)
    feedback = normalize(result, stdout, stderr, candidate, source_map)
    feedback['candidate_sha256'] = digest(candidate.read_bytes())
    feedback['seconds'] = result['seconds']
    write_json(directory / (label + '.feedback.json'), feedback)
    return feedback
