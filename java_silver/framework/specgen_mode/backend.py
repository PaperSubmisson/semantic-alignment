"""C0-only fallback for the native prover's model-extraction crash."""
from framework.verification.silicon_backend import verify as native_verify

def verify(candidate,directory,timeout=60,source_map=None):
    result=native_verify(candidate,directory,timeout=timeout,source_map=source_map)
    if any('Interaction with prover' in d.get('message','') for d in result.get('diagnostics',[])):
        original=result
        result=native_verify(candidate,directory,label='silicon-no-model',timeout=timeout,source_map=source_map,counterexample=False)
        result['counterexample_fallback']={'reason':'Native model extraction/prover interaction failed. Reverified the identical program without counterexample extraction.','original_status':original['status'],'original_stdout':original.get('stdout_file'),'original_stderr':original.get('stderr_file'),'counterexample_available':False}
    return result
