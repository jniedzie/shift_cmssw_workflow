#!/usr/bin/env python3
"""Freeze launch files for an inventoried GEN-to-Nano campaign."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


BOOTSTRAP = r'''#!/bin/bash
set -eo pipefail
bootstrap_sha=${1:?bootstrap SHA}
source_index=${2:?source index}
skip=${3:?source skip}
count=${4:?event count}
job=${5:?job identity}
mode=${6:-production}
scratch=${_CONDOR_SCRATCH_DIR:?Condor scratch required}
cd "$scratch"
finish() {
    result=$?
    python3 - "$result" "$job" <<'PY'
import hashlib,json,sys
from pathlib import Path
p=Path('report.json')
d=json.loads(p.read_text()) if p.exists() else {}
archive=Path('evidence.tar.gz')
status={'job':int(sys.argv[2]),'exit_code':int(sys.argv[1]),'complete':d.get('complete',False),
        'error':d.get('error') or ('Bootstrap failed; inspect worker logs' if int(sys.argv[1]) else None),
        'source_stratum':d.get('source_stratum'), 'nano_path':d.get('nano_path'),
        'nano_bytes':d.get('nano_bytes'),'events':d.get('events'),
        'wall_seconds':d.get('wall_seconds'),
        'stage_seconds':{k:v['seconds'] for k,v in d.get('stages',{}).items()},
        'validated_tier_events':d.get('validated_tier_events'),
        'compressed_event_bytes':d.get('nano',{}).get('compressed_event_bytes'),
        'evidence_bytes':archive.stat().st_size if archive.exists() else 0,
        'report_sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None}
Path('status.json').write_text(json.dumps(status)+'\n')
PY
    if [[ "$result" != 0 && -f evidence.tar.gz ]]; then
        destination=$(python3 -c 'import json,sys; print(json.load(open("source"+sys.argv[1]+".json"))["output_base"]+"/failures/job"+sys.argv[2])' "$source_index" "$job")
        env -u LD_LIBRARY_PATH -u LD_PRELOAD timeout 30 /usr/bin/xrdfs root://eosuser.cern.ch mkdir -p "$destination" || true
        env -u LD_LIBRARY_PATH -u LD_PRELOAD timeout 60 /usr/bin/xrdcp --silent --cksum adler32 evidence.tar.gz "root://eosuser.cern.ch/$destination/evidence.tar.gz" || true
        env -u LD_LIBRARY_PATH -u LD_PRELOAD timeout 30 /usr/bin/xrdcp --silent --cksum adler32 report.json "root://eosuser.cern.ch/$destination/failure.json" || true
    fi
    exit "$result"
}
trap finish EXIT
printf '%s  bootstrap.json\n' "$bootstrap_sha" | sha256sum -c -
python3 - "$source_index" <<'PY'
import hashlib,json,sys
from pathlib import Path
d=json.load(open('bootstrap.json'))
source='source'+sys.argv[1]+'.json'
if hashlib.sha256(Path(source).read_bytes()).hexdigest()!=d['sources'][str(int(sys.argv[1]))]:
    raise SystemExit('Frozen source descriptor changed')
for stage,expected in d['templates'].items():
    if hashlib.sha256(Path('templates/step'+stage+'.py').read_bytes()).hexdigest()!=expected:
        raise SystemExit('Frozen detector configuration changed')
PY
export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
unset LD_LIBRARY_PATH PYTHONPATH ROOTSYS CMSSW_BASE CMSSW_SEARCH_PATH CMSSW_RELEASE_BASE
readarray -t bundle < <(python3 -c 'import json;d=json.load(open("bootstrap.json"));print(d["bundle_url"]);print(d["bundle_sha256"])')
timeout 600 xrdcp --silent --cksum adler32 "${bundle[0]}" runtime.tar.gz
printf '%s  runtime.tar.gz\n' "${bundle[1]}" | sha256sum -c -
mkdir payload
tar -xzf runtime.tar.gz -C payload
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd payload/CMSSW_17_0_0_pre4/src
scram b ProjectRename
eval "$(scram runtime -sh)"
export LD_LIBRARY_PATH="$(python3 -c 'import os; print(":".join(p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":") if "/biglib/" not in p))')"
export TMPDIR="$scratch"
cd "$scratch"
extra=()
if [[ "$mode" == canary ]]; then extra+=(--canary); fi
stage_limits=$(python3 -c 'import json; print(",".join(str(value) for value in json.load(open("bootstrap.json"))["stage_timeouts_seconds"]))')
python3 payload/workflow/scripts/run_shift_gen_to_nano.py "source${source_index}.json" \
    --skip "$skip" --count "$count" --job "$job" --templates templates \
    --stage-timeouts "$stage_limits" --publish "${extra[@]}"
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('--email', required=True)
    parser.add_argument('--canary-events', type=int, default=2)
    parser.add_argument('--pilot-size', type=int, default=81)
    parser.add_argument('--benchmark-cluster', type=int)
    parser.add_argument('--worker-ceiling', type=int, default=100)
    parser.add_argument('--initial-workers', type=int, default=100)
    parser.add_argument('--capacity-step', type=int, default=50)
    parser.add_argument('--capacity-interval', type=int, default=1200)
    parser.add_argument('--capacity-completions', type=int, default=10)
    parser.add_argument('--worker-timeout-seconds', type=int,
                        help='Total worker limit; defaults to six hours for samples and thirty hours for full GEN slices')
    parser.add_argument('--stage-timeouts',
                        help='SIM,DIGIHLT,RECO,NANO limits in seconds; sampled jobs scale these per 100 events')
    args = parser.parse_args()
    if not (0 <= args.pilot_size <= args.worker_ceiling <= 1000) or not (1 <= args.initial_workers <= args.worker_ceiling):
        parser.error('Require 0 <= pilot size <= worker ceiling <= 1000 and 1 <= initial workers <= ceiling')
    if min(args.capacity_step,args.capacity_interval,args.capacity_completions) <= 0:
        parser.error('Capacity steps, intervals, completion gates and worker timeout must be positive')
    root = args.campaign.resolve()
    manifest = json.loads((root / 'manifest.json').read_text())
    sampled = bool(manifest.get('detector_sampling'))
    if args.worker_timeout_seconds is None:
        args.worker_timeout_seconds = 21600 if sampled else 108000
    if args.worker_timeout_seconds <= 0:
        parser.error('Worker timeout must be positive')
    default_limits = '72000,18000,54000,14400' if sampled else '10800,1800,5400,14400'
    try:
        stage_limits = [int(value) for value in (args.stage_timeouts or default_limits).split(',')]
    except ValueError:
        parser.error('Four positive integer stage timeouts required')
    if len(stage_limits) != 4 or min(stage_limits) <= 0:
        parser.error('Four positive integer stage timeouts required')
    runtime = json.loads((root / 'runtime_freeze.json').read_text())
    if (root / 'bootstrap.json').exists():
        raise ValueError('Campaign launch files already frozen')
    tag = Path(manifest['eos_output']).name
    policy = dict(tag=tag, attention_email=args.email, queue_timeout_seconds=7200,
                  setup_timeout_seconds=1800, worker_timeout_seconds=args.worker_timeout_seconds,
                  canary_timeout_seconds=1800, completion_timeout_seconds=args.worker_timeout_seconds,
                  stage_timeouts_seconds=stage_limits, notification='one_attempt_per_campaign',
                  automatic_retry=False)
    policy.update(config_timeout_seconds=600,event_stall_timeout_seconds=1800,
                  failure_policy='Isolate individual workers; keep healthy bins and persistent monitors active.',
                  absolute_limit_basis='Measured QCD20+ event tails up to1288s; absolute stage budgets are independent of the event-stall guard.')
    policy.update(worker_ceiling=args.worker_ceiling,initial_workers=args.initial_workers,
                  capacity_step=args.capacity_step,capacity_interval_seconds=args.capacity_interval,
                  capacity_completions=args.capacity_completions,max_idle_workers=args.initial_workers,
                  adaptive_capacity=args.worker_ceiling > args.initial_workers,
                  account_pool='tweetybird04.cern.ch',capacity_idle_fraction=0.25,
                  asynchronous_audits=True)
    if args.benchmark_cluster:
        policy['benchmark_cluster']=args.benchmark_cluster
    (root / 'policy.json').write_text(json.dumps(policy,indent=2)+'\n')
    manifest.update(scheduling_ceiling=args.worker_ceiling, minimum_free_bytes=50000000000, minimum_free_files=20000,
                    preparation_basis='Frozen launch files preserve the archived CMS IR5 recipe and exact input inventory.')
    (root / 'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for directory in ('logs','results'):
        (root / directory).mkdir(exist_ok=True)
    # AFS directories have a bounded number of entries. Two flat log files
    # per worker overflow for a roughly 20k-job production. Bound each shard
    # to 500 identities and include the Condor attempt identity in filenames.
    log_groups = {job // 500 for job in range(manifest['jobs'])}
    log_groups.update(row['job'] // 500 for row in json.loads((root/'canaries.json').read_text()))
    for group in sorted(log_groups):
        (root/'logs'/f'g{group}').mkdir(exist_ok=True)
    scripts = Path(__file__).resolve().parent
    for name in ('run_shift_ntuple_controller.py','shift_condor_native.py','run_shift_quota_audit.py'):
        shutil.copy2(scripts / name, root / name)
    bootstrap = dict(bundle_url='root://eosuser.cern.ch/' + manifest['eos_output'] + '/runtime.tar.gz',
                     bundle_sha256=runtime['sha256'], stage_timeouts_seconds=stage_limits,
                     sources={}, templates={})
    for source in manifest['sources']:
        bootstrap['sources'][str(source['index'])] = sha(root / 'sources' / f'source{source["index"]:05d}.json')
    for stage in range(1,5):
        bootstrap['templates'][str(stage)] = sha(root / 'templates' / f'step{stage}.py')
    (root / 'bootstrap.json').write_text(json.dumps(bootstrap,indent=2)+'\n')
    bootstrap_sha = sha(root / 'bootstrap.json')
    (root / 'bootstrap.sh').write_text(BOOTSTRAP)
    (root / 'bootstrap.sh').chmod(0o755)
    canaries = json.loads((root / 'canaries.json').read_text())
    by_index = {s['index']:s for s in manifest['sources']}
    for canary in canaries:
        canary['skip'] = 1
        canary['count'] = min(args.canary_events, by_index[canary['source']]['events']-1)
    (root / 'canaries.json').write_text(json.dumps(canaries,indent=2)+'\n')
    (root / 'canary_jobs.txt').write_text(''.join(f'{c["source"]:05d} {c["skip"]} {c["count"]} {c["job"]}\n' for c in canaries))
    # Consistent zero-padded input names on submit and execute hosts.
    lines = []
    for line in (root / 'jobs.txt').read_text().splitlines():
        source, skip, count, job = map(int,line.split())
        lines.append(f'{source:05d} {skip} {count} {job}\n')
    (root / 'all_jobs.txt').write_text(''.join(lines))
    from collections import deque
    grouped={s:deque() for s in manifest['strata']}
    for line in lines:
        index=int(line.split()[0]);grouped[by_index[index]['stratum']].append(line)
    pilot=[]
    while len(pilot)<args.pilot_size:
        for queue in grouped.values():
            if queue and len(pilot)<args.pilot_size: pilot.append(queue.popleft())
        if not any(grouped.values()): break
    ids={int(line.split()[3]) for line in pilot}
    (root / 'pilot_jobs.txt').write_text(''.join(pilot))
    # Fair scheduling continues after the initial pilot. Input-order factories
    # otherwise run entire low bins before reaching DY and the high bins.
    balanced=[]
    while any(grouped.values()):
        for queue in grouped.values():
            if queue:
                balanced.append(queue.popleft())
    (root / 'jobs.txt').write_text(''.join(balanced))
    pilot_strata={s:0 for s in manifest['strata']};pilot_sources={}
    for line in pilot:
        index,skip,count,job=map(int,line.split());s=by_index[index]['stratum']
        pilot_strata[s]+=count
        pilot_sources.setdefault(index,dict(index=index,stratum=s,events=0))['events']+=count
    manifest.update(pilot_jobs=sorted(ids),pilot_events=sum(pilot_strata.values()),
                    pilot_strata=pilot_strata,pilot_sources=list(pilot_sources.values()))
    (root / 'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    common = f'''log_group = int($(job) / 500)
universe = vanilla
initialdir = {root}
executable = {root}/bootstrap.sh
should_transfer_files = YES
when_to_transfer_output = ON_EXIT
transfer_input_files = {root}/bootstrap.json,{root}/templates,{root}/sources/source$(source).json
transfer_output_files = status.json
transfer_output_remaps = "status.json=results/status$(job).json"
getenv = False
notification = Never
request_cpus = 1
request_memory = 4500
request_disk = 6000000
+WantIOProxy = true
+MaxRuntime = {args.worker_timeout_seconds}
+ShiftProductionSuite = true
+ShiftSuiteTag = "{tag}"
+ShiftNtupleProduction = true
+ShiftNtupleJob = $(job)
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
periodic_release = False
periodic_hold = (JobStatus == 1 && time() - ifThenElse(isUndefined(JobMaterializeDate), EnteredCurrentStatus, ifThenElse(isUndefined(EnteredCurrentStatus) || JobMaterializeDate > EnteredCurrentStatus, JobMaterializeDate, EnteredCurrentStatus)) > 7200) || (JobStatus == 2 && (time() - JobCurrentStartDate > {args.worker_timeout_seconds} || (!isUndefined(ShiftNtupleProgressEpoch) && time() - ShiftNtupleProgressEpoch > ShiftNtupleStageTimeout + 180) || (!isUndefined(ShiftNtupleStageDeadlineEpoch) && ShiftNtupleStageDeadlineEpoch > 0 && time() > ShiftNtupleStageDeadlineEpoch + 180)))
periodic_hold_reason = "SHIFT production exceeded its queue, stage, or total wall-time limit"
log = events.log
'''
    for mode,name in [('canary','canary'),('production','bulk')]:
        suffix=f'''arguments = {bootstrap_sha} $(source) $(skip) $(count) $(job) {mode}
+ShiftNtupleCanary = {str(mode=='canary').lower()}
+JobBatchName = "{tag}_{mode}"
output = logs/g$INT(log_group)/{mode}$(job)_$(Cluster)_$(Process).out
error = logs/g$INT(log_group)/{mode}$(job)_$(Cluster)_$(Process).err
'''
        if mode=='production':
            suffix += f'max_materialize = {args.initial_workers}\nmax_idle = {policy["max_idle_workers"]}\n'
        suffix += f'queue source,skip,count,job from {root}/{"canary_jobs" if mode=="canary" else "jobs"}.txt\n'
        (root / (name+'.sub')).write_text(common+suffix)
    (root/'pilot.sub').write_text((root/'bulk.sub').read_text().replace(
        f'max_materialize = {args.initial_workers}',f'max_materialize = {max(1,args.pilot_size)}').replace(
        str(root/'jobs.txt'),str(root/'pilot_jobs.txt')).replace(f'{tag}_production',f'{tag}_pilot'))
    # A full validated sample can start directly at its initial capacity.
    # With a separate pilot, start small until its outstanding jobs drain.
    if args.pilot_size:
        (root/'bulk.sub').write_text((root/'bulk.sub').read_text().replace(
            f'max_materialize = {args.initial_workers}','max_materialize = 1'))
    controller_sub = f'''universe = scheduler
initialdir = {root}
executable = /usr/bin/python3
arguments = {root}/run_shift_ntuple_controller.py {root}
getenv = False
notification = Never
+JobBatchName = "{tag}_controller"
output = controller.out
error = controller.err
log = controller.log
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
periodic_release = False
queue 1
'''
    (root / 'controller.sub').write_text(controller_sub)
    (root / 'watchdog.sub').write_text(controller_sub.replace(
        f'arguments = {root}/run_shift_ntuple_controller.py {root}',
        f'arguments = {root}/run_shift_ntuple_controller.py {root} --watchdog').replace(
        f'{tag}_controller', f'{tag}_watchdog').replace('controller.out','watchdog.out').replace(
        'controller.err','watchdog.err').replace('controller.log','watchdog.log'))
    print(json.dumps(dict(campaign=str(root),canaries=len(canaries),canary_events=sum(c['count'] for c in canaries),
                         bulk_jobs=manifest['jobs'],bootstrap_sha256=bootstrap_sha)))


if __name__ == '__main__':
    main()
