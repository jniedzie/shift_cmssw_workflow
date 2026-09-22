"""Run one exact manifest entry, with no reads from the shared AFS runtime."""
import json
import os
from pathlib import Path
import sys

root = Path(os.environ['RECOVERY_LOCAL_ROOT'])
manifest = json.loads((root / sys.argv[1]).read_text())
row = manifest['jobs'][int(sys.argv[2])]
env = os.environ.copy()
env.update(row['environment'])
env.update(CMSSW_SRC=str(root / 'CMSSW_17_0_0_pre4/src'),
           WORKDIR=str(root / 'work'), CMSSW_PREPARED='1',
           WORKFLOW_LOCAL_GENERATOR='1', CLEANUP_PREVIOUS_STEP='1',
           N_EVENTS=str(row['events']), N_JOBS=str(row['campaign_jobs']))
for key in ('CMSSW_BASE', 'CMSSW_SEARCH_PATH', 'LD_LIBRARY_PATH', 'PYTHONPATH'):
    if any(part.startswith('/afs/') for part in env.get(key, '').split(':')):
        raise ValueError(f'AFS dependency remains in {key}')
for name in ('PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise',
             'IOMC.ShiftEventTiming.shiftEventTiming_customise'):
    import importlib
    module = importlib.import_module(name)
    if not str(Path(module.__file__).resolve()).startswith(str(root)):
        raise ValueError(f'Custom runtime import is not staged: {name}')
Path(env['WORKDIR']).mkdir(exist_ok=True)
args = ['bash', str(root / 'workflow/scripts/run_condor_job.sh'), str(row['chunk']),
        str(root / 'workflow'), '1,2,3,4', '0', env['PROCESS'], row['sample'],
        Path(row['campaign']).name, '/eos/home-j/jniedzie/shift_cmssw',
        row['campaign'], str(row['events'])]
print(f'Recovery job {os.environ.get("RECOVERY_JOB_ID")}: {row["sample"]} '
      f'{row["bin"]} chunk {row["chunk"]}; runtime and workflow staged locally', flush=True)
os.execvpe(args[0], args, env)
