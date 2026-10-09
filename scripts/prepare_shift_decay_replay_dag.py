#!/usr/bin/env python3
"""Freeze a decay-completed GEN replay and a persistent Nano/histogram/merge DAG."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import shutil

from freeze_shift_gen_runtime import freeze
from prepare_shift_ntuple_restart import BOOTSTRAP
from prepare_shift_detector_sample import partition_jobs


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2)+'\n')


def prepare(parent, root, cmssw, tag, archive, histogram_evidence, workers, events_per_job=20):
    parent, root, cmssw = map(lambda p: Path(p).resolve(), (parent, root, cmssw))
    if root.exists():
        raise ValueError('Refusing an existing campaign directory')
    original = json.loads((parent/'manifest.json').read_text())
    complete = json.loads((parent/'production_complete.json').read_text())
    if not complete.get('complete') or complete['events'] != original['events']:
        raise ValueError('Parent Nano production is not complete')
    root.mkdir()
    for directory in ('sources', 'templates', 'results', 'canary_results', 'merge_jobs', 'histogram_results'):
        (root/directory).mkdir()
    eos = '/eos/user/j/jniedzie/shift_cmssw/ntuple_production/'+tag
    manifest = dict(original)
    manifest.update(eos_output=eos, complete_muon_decays=True,
        parent_campaign=str(parent), parent_manifest_sha256=sha(parent/'manifest.json'),
        correction='Complete every enabled positive-BR Pythia decay chain reaching muons; retain natural branching ratios.',
        approval_basis='User requested all-process all-bin muon decays, production, histogramming and complete merging on 2026-10-07.',
        scheduling_ceiling=workers, minimum_free_bytes=50000000000, minimum_free_files=20000,
        canary_passed=False)
    rows=partition_jobs(manifest['sources'],events_per_job)
    manifest.update(events_per_job=events_per_job,jobs=len(rows))
    for source in manifest['sources']:
        name=f'source{source["index"]:05d}.json'
        descriptor=json.loads((parent/'sources'/name).read_text())
        descriptor.update(complete_muon_decays=True, output_base=eos+'/'+source['stratum'],
                          decay_parent_descriptor_sha256=sha(parent/'sources'/name))
        write_json(root/'sources'/name, descriptor)
    for stage in range(1,5):
        shutil.copy2(parent/'templates'/f'step{stage}.py',root/'templates'/f'step{stage}.py')
    for name in ('sampling_plan.json','sampling_events.jsonl.gz'):
        shutil.copy2(parent/name, root/name)
    (root/'all_jobs.txt').write_text(''.join(f'{source:05d} {skip} {count} {job}\n'
        for source,skip,count,job in rows))
    canaries=json.loads((parent/'canaries.json').read_text())
    write_json(root/'canaries.json',canaries)
    write_json(root/'manifest.json',manifest)
    runtime=freeze(cmssw,archive)
    write_json(root/'runtime_freeze.json',runtime)
    bootstrap=dict(bundle_url='root://eosuser.cern.ch/'+eos+'/runtime.tar.gz',
        bundle_sha256=runtime['sha256'], sources={}, templates={},
        stage_timeouts_seconds=[72000,18000,54000,14400],
        config_timeout_seconds=600,event_stall_timeout_seconds=1800)
    for s in manifest['sources']:
        bootstrap['sources'][str(s['index'])]=sha(root/'sources'/f'source{s["index"]:05d}.json')
    for stage in range(1,5):
        bootstrap['templates'][str(stage)]=sha(root/'templates'/f'step{stage}.py')
    write_json(root/'bootstrap.json',bootstrap)
    (root/'bootstrap.sh').write_text(BOOTSTRAP)
    (root/'bootstrap.sh').chmod(0o755)
    bootstrap_sha=sha(root/'bootstrap.json')
    hist_runtime=json.loads((histogram_evidence/'local_runtime_manifest.json').read_text())
    hist_template=(histogram_evidence/'local_worker.template.sh').read_text()
    hist_script=hist_template.replace('@@SHA256@@',hist_runtime['sha256']).replace(
        '@@ARCHIVE_URL@@',hist_runtime['archive_url']).replace('@@INPUT_LIST@@','histogram_inputs.txt')
    # Preserve the exact proven local-runtime initialization for mergers/gates.
    local_setup=hist_script.split('cd "$tea_cache/bin"',1)[0]
    (root/'histogram.sh').write_text(hist_script)
    merger=Path(__file__).resolve().parents[2]/'tea_shift_cmssw/tea/apps/examples/merge.py'
    shutil.copy2(merger,root/'merge.py')
    merge_setup=local_setup.replace('[[ $# == 1 && "$1" =~ ^[0-9]+$ ]]','[[ $# == 1 ]]')
    (root/'merge.sh').write_text(merge_setup+'''
tea_phase="complete bin merge"
exec timeout -k 30 21600 "$tea_cache/runtime/bin/python" "$job_sandbox/merge.py" --job-file "$job_sandbox/${job_number}.json"
''')
    # Non-numeric gate/final names must not pass through the histogram-index guard.
    gate_setup=local_setup.replace('[[ $# == 1 && "$1" =~ ^[0-9]+$ ]]','[[ $# == 1 ]]')
    (root/'audit.sh').write_text(gate_setup+'''
tea_phase="campaign audit"
exec timeout -k 30 1800 "$tea_cache/runtime/bin/python" "$job_sandbox/audit_decay_campaign.py" "$job_sandbox/${job_number}_request.json"
''')
    shutil.copy2(Path(__file__).with_name('audit_decay_campaign.py'),root/'audit_decay_campaign.py')
    for name in ('histogram.sh','merge.sh','audit.sh'):
        (root/name).chmod(0o755)
    # Histogrammer input indices match immutable production job identities.
    rows=[tuple(map(int,line.split())) for line in (root/'all_jobs.txt').read_text().splitlines()]
    if sorted(r[3] for r in rows)!=list(range(manifest['jobs'])):
        raise ValueError('Production job inventory differs from manifest')
    by_source={s['index']:s for s in manifest['sources']}
    hist_rows=[None]*manifest['jobs']; bin_inputs=defaultdict(list); bin_nodes=defaultdict(list)
    for source,skip,count,job in rows:
        stratum=by_source[source]['stratum'];process,bin_name=stratum.split('_',1)
        nano=f'{eos}/{stratum}/job{job:07d}/nano.root'
        histogram=f'/eos/user/j/jniedzie/shift_cmssw/{process}/{tag}_{bin_name.replace("toinf","to-1")}/histograms/histograms_job{job:07d}.root'
        hist_rows[job]=(nano,'',histogram)
        bin_inputs[stratum].append(histogram);bin_nodes[stratum].append(f'H{job:05d}')
    canary_histograms=[]
    for c in canaries:
        index=len(hist_rows)
        c['histogram_index']=index
        output=f'{eos}/canaries/histograms/{c["stratum"]}.root'
        hist_rows.append((f'{eos}/{c["stratum"]}/canaries/job{c["job"]:07d}/nano.root','',output))
        canary_histograms.append(dict(stratum=c['stratum'],events=c['count'],histogram=output,
            receipt=f'{eos}/{c["stratum"]}/canaries/job{c["job"]:07d}/complete.json',
            status=f'status{c["job"]}.json',job=c['job']))
    (root/'histogram_inputs.txt').write_text(''.join(repr(row)+'\n' for row in hist_rows))
    for start in range(0,manifest['jobs'],500):
        directory=root/'histogram_groups'/f'g{start//500}'
        directory.mkdir(parents=True)
        (directory/'histogram_inputs.txt').write_text(''.join(repr(row)+'\n' for row in hist_rows[start:min(start+500,manifest['jobs'])]))
    directory=root/'histogram_groups'/'canary';directory.mkdir(parents=True)
    (directory/'histogram_inputs.txt').write_text(''.join(repr(row)+'\n' for row in hist_rows[manifest['jobs']:]))
    # Shard scheduler logs and transferred receipts to stay below AFS entry limits.
    for job in list(range(manifest['jobs']))+[c['job'] for c in canaries]:
        (root/'logs'/f'g{job//500}').mkdir(parents=True,exist_ok=True)
    common=f'''universe = vanilla
initialdir = {root}
getenv = False
notification = Never
should_transfer_files = YES
when_to_transfer_output = ON_EXIT
request_cpus = 1
request_memory = 4500
request_disk = 6000000
+WantIOProxy = true
+ShiftProductionSuite = true
+ShiftSuiteTag = "{tag}"
+ShiftDecayReplay = true
+MaxRuntime = 21600
on_exit_hold = False
periodic_release = False
periodic_remove = (JobStatus == 2 && time() - JobCurrentStartDate > 21600) || (JobStatus == 5 && time() - EnteredCurrentStatus > 180)
log = {root}/events.log
'''
    nano=common+f'''executable = {root}/bootstrap.sh
arguments = {bootstrap_sha} $(source) $(skip) $(count) $(job) $(mode)
transfer_input_files = {root}/bootstrap.json,{root}/templates,{root}/sources/source$(source).json
transfer_output_files = status.json
transfer_output_remaps = "status.json=$(status_dir)/status$(job).json"
+ShiftNtupleProduction = true
+ShiftNtupleJob = $(job)
+JobBatchName = "{tag}_nano"
output = {root}/logs/g$(group)/nano$(job)_$(Cluster)_$(Process).out
error = {root}/logs/g$(group)/nano$(job)_$(Cluster)_$(Process).err
queue 1
'''
    (root/'nano.sub').write_text(nano)
    histogram=common+f'''executable = {root}/histogram.sh
arguments = $(hist_index)
transfer_input_files = $(histogram_input)
transfer_output_files = ""
+JobBatchName = "{tag}_histogram"
output = {root}/logs/g$(group)/hist$(job)_$(Cluster)_$(Process).out
error = {root}/logs/g$(group)/hist$(job)_$(Cluster)_$(Process).err
queue 1
'''
    (root/'histogram.sub').write_text(histogram)
    (root/'merge.sub').write_text(common+f'''executable = {root}/merge.sh
arguments = $(stratum)
transfer_input_files = $(merge_config),{root}/merge.py
transfer_output_files = ""
request_disk = 6000000
+JobBatchName = "{tag}_merge"
output = {root}/merge_jobs/$(stratum)_$(Cluster)_$(Process).out
error = {root}/merge_jobs/$(stratum)_$(Cluster)_$(Process).err
queue 1
''')
    (root/'audit.sub').write_text(common+f'''executable = {root}/audit.sh
arguments = $(phase)
transfer_input_files = {root}/audit_decay_campaign.py,$(request),$(status_inputs)
transfer_output_files = audit_result.json
transfer_output_remaps = "audit_result.json={root}/$(phase)_complete.json"
+JobBatchName = "{tag}_audit"
output = {root}/$(phase)_$(Cluster)_$(Process).out
error = {root}/$(phase)_$(Cluster)_$(Process).err
queue 1
''')
    write_json(root/'gate_request.json',dict(phase='gate',tag=tag,eos_base=eos,
        canaries=canary_histograms,expected_events=manifest['events'],expected_jobs=manifest['jobs'],
        strata=manifest['strata'],stratum_jobs={s:len(paths) for s,paths in bin_inputs.items()},
        source_descriptor_hashes=bootstrap['sources'],manifest_sha256=sha(root/'manifest.json')))
    final_bins=[]
    for stratum,inputs in bin_inputs.items():
        process,bin_name=stratum.split('_',1)
        output=f'/eos/user/j/jniedzie/shift_cmssw/{process}/{tag}_{bin_name.replace("toinf","to-1")}/histograms_merged/ntuple_0.root'
        merge=dict(input_files=inputs,output_file=output,preserve_input_compression=False,
                   hadd_workers=1,hadd_files_per_pass=100)
        write_json(root/'merge_jobs'/f'{stratum}.json',merge)
        final_bins.append(dict(stratum=stratum,events=manifest['strata'][stratum],histograms=len(inputs),
                              output=output,corrected_sumw=json.loads((root/'sampling_plan.json').read_text())['strata'][stratum]['corrected_sumw']))
    write_json(root/'final_request.json',dict(phase='final',tag=tag,bins=final_bins,
        expected_events=manifest['events'],expected_jobs=manifest['jobs'],
        manifest_sha256=sha(root/'manifest.json')))
    dag=[];canary_children=[]
    def node(name,sub,variables,category,retries=2):
        dag.append(f'JOB {name} {root/sub}')
        dag.append('VARS '+name+' '+' '.join(f'{k}="{v}"' for k,v in variables.items()))
        dag.append(f'RETRY {name} {retries}');dag.append(f'CATEGORY {name} {category}')
    for canary_index,c in enumerate(canaries):
        name=f'C{c["source"]:05d}';hname='HC'+str(c['source'])
        node(name,'nano.sub',dict(source=f'{c["source"]:05d}',skip=c['skip'],count=c['count'],
             job=c['job'],group=c['job']//500,mode='canary',status_dir='canary_results'),'CANARY')
        node(hname,'histogram.sub',dict(hist_index=canary_index,job=c['job'],group=c['job']//500,
             histogram_input=root/'histogram_groups'/'canary'/'histogram_inputs.txt'),'CANARY')
        dag.append(f'PARENT {name} CHILD {hname}');canary_children.append(hname)
    status_inputs=','.join(str(root/'canary_results'/f'status{c["job"]}.json') for c in canaries)
    node('GATE','audit.sub',dict(phase='gate',request=root/'gate_request.json',status_inputs=status_inputs),'AUDIT')
    dag.append('PARENT '+' '.join(canary_children)+' CHILD GATE')
    nano_nodes=[]
    for source,skip,count,job in rows:
        name=f'N{job:05d}';hname=f'H{job:05d}'
        node(name,'nano.sub',dict(source=f'{source:05d}',skip=skip,count=count,job=job,
             group=job//500,mode='production',status_dir=f'results/g{job//500}'),'NANO')
        (root/'results'/f'g{job//500}').mkdir(exist_ok=True)
        node(hname,'histogram.sub',dict(hist_index=job%500,job=job,group=job//500,
             histogram_input=root/'histogram_groups'/f'g{job//500}'/'histogram_inputs.txt'),'HISTOGRAM')
        dag.append(f'PARENT {name} CHILD {hname}');nano_nodes.append(name)
    # Bound DAG line lengths rather than passing forty thousand children at once.
    for start in range(0,len(nano_nodes),100):
        dag.append('PARENT GATE CHILD '+' '.join(nano_nodes[start:start+100]))
    merge_nodes=[]
    for index,(stratum,names) in enumerate(bin_nodes.items()):
        name=f'M{index:02d}';merge_nodes.append(name)
        node(name,'merge.sub',dict(stratum=stratum,merge_config=root/'merge_jobs'/f'{stratum}.json'),'MERGE')
        for start in range(0,len(names),100):
            dag.append('PARENT '+' '.join(names[start:start+100])+' CHILD '+name)
    node('FINAL','audit.sub',dict(phase='final',request=root/'final_request.json',status_inputs=root/'README.md'),'AUDIT')
    dag.append('PARENT '+' '.join(merge_nodes)+' CHILD FINAL')
    dag += ['MAXJOBS CANARY 20',f'MAXJOBS NANO {workers}',
            'MAXJOBS HISTOGRAM 100','MAXJOBS MERGE 4','MAXJOBS AUDIT 1',
            f'NODE_STATUS_FILE {root}/node_status 60 ALWAYS-UPDATE']
    (root/'production.dag').write_text('\n'.join(dag)+'\n')
    write_json(root/'pipeline_freeze.json',dict(tag=tag,workers=workers,
        nano_jobs=len(rows),histogram_jobs=len(rows),merge_jobs=len(bin_inputs),
        canary_jobs=len(canaries),dag_sha256=sha(root/'production.dag'),
        histogram_runtime=hist_runtime,runtime=runtime,source_descriptor_hashes=bootstrap['sources']))
    (root/'README.md').write_text(f'''# Muon-decay correction {tag}

Reuses every GEN event and the original representative sampling draw from {parent.name}.
Replays all 19 process bins with complete natural muon-producing hadron decay chains.
Private detector material, fields and fixed Run-3 electronics settings are unchanged.
DAGMan gates bulk production on full-chain and histogram canaries; each validated
Nano job releases its histogram job, and every bin merge depends on all its inputs.
Each node has at most three attempts. All old outputs and source GEN are retained.
The final result is final_complete.json; gate_complete.json records the scaling gate.
The watchdog publishes production_complete.json as soon as every planned Nano
receipt passes validation, independently of later histogram and merge completion.
After DAG termination and child-job draining, the watchdog exits on success or
failure. Failures are recorded in production_failed.json and live_status.json;
an empty queue alone never counts as successful production.
Do not edit frozen launch files after submission. Logs, failed attempts and receipts
are retained. Histogram merging applies no additional cross-section normalization.
''')
    for name in ('watch_shift_decay_dag.py','shift_condor_native.py'):
        shutil.copy2(Path(__file__).with_name(name),root/name)
    (root/'watchdog.sub').write_text(f'''universe = scheduler
initialdir = {root}
executable = /usr/bin/python3
arguments = {root}/watch_shift_decay_dag.py {root}
getenv = False
notification = Never
+JobBatchName = "{tag}_watchdog"
output = {root}/watchdog.out
error = {root}/watchdog.err
log = {root}/watchdog.log
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
queue 1
''')
    return dict(campaign=str(root),tag=tag,events=manifest['events'],jobs=manifest['jobs'],runtime=runtime)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cmssw',type=Path,required=True)
    p.add_argument('--tag',required=True)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--histogram-runtime',type=Path,required=True)
    p.add_argument('--workers',type=int,default=500)
    p.add_argument('--events-per-job',type=int,default=20)
    a=p.parse_args()
    if not 1<=a.workers<=1000:
        p.error('Worker limit must be 1..1000')
    if a.events_per_job < 1:
        p.error('Events per job must be positive')
    print(json.dumps(prepare(a.parent,a.output,a.cmssw,a.tag,a.archive,a.histogram_runtime,a.workers,a.events_per_job),indent=2))


if __name__=='__main__':
    main()
