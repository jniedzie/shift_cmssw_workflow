#!/usr/bin/env python3
"""Freeze a held DAG with independent pilots, automatic gate and bounded GEN."""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
ALGORITHM='independent-geometric-full-support-tail-channel-retry-rb-v4'
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(directory,assets,runtime):
    directory=Path(directory).resolve();assets=Path(assets).resolve()
    if directory.exists() or any(c.isspace() for c in str(directory)):raise ValueError('New manager directory without whitespace required')
    directory.mkdir(parents=True)
    for name in ('logs','results'):(directory/name).mkdir()
    shutil.copy2(runtime,directory/'runtime.tar.gz')
    names=['pluginShiftMpiWeightedProposalHook.so','libShiftMpiTailProposal.so','ordinary_reference.json','SCIENCE_METHOD.md','ShiftMpiWeightedProposalHook.cc','ShiftWeightedGenRunInfoProducer.cc','prepare_tail_overlay.py','prepare_weighted_stats_overlay.py','source_reference_sha256.txt']
    for name in names:shutil.copy2(assets/name,directory/name)
    for name in ['run_shift_weighted_gen.py','audit_shift_weighted_gen.py','generation_publication.py','soft_mpi_model.py','gate_shift_weighted_gen.py','shift_weighted_gen_pre.py','finalize_shift_weighted_gen.py']:
        shutil.copy2(REPO/'scripts'/name,directory/name)
    inputs={name:sha(directory/name) for name in ['runtime.tar.gz','pluginShiftMpiWeightedProposalHook.so','libShiftMpiTailProposal.so','run_shift_weighted_gen.py','audit_shift_weighted_gen.py','generation_publication.py','soft_mpi_model.py']}
    tag='shift_weighted_high_bins_20261001_v2';jobs=[]
    closures=[('qcd',(5,10),20000,30001001,800101),('jpsi',(2,5),60000,30002001,800201)]
    highs=[('qcd',(20,-1),30003001,800301),('jpsi',(5,10),30004001,800401),('jpsi',(10,20),30005001,800501),('jpsi',(20,-1),30006001,800601)]
    def stratum(sample,bounds):return sample+'_'+str(bounds[0])+'to'+str(bounds[1]).replace('-1','inf')
    def add(sample,bounds,trials,run,seed,stage,index):
        key=stratum(sample,bounds);ident=stage+'_'+key+'_'+str(index).zfill(4)
        jobs.append(dict(id=ident,stratum=key,sample=sample,bounds=list(bounds),trials=trials,run=run,seed=seed,stage=stage,chunk_index=index,publish=True,timeout_seconds=3600))
    for sample,bounds,trials,run,seed in closures:
        for index in range(3):add(sample,bounds,trials,run+index,seed+index,'closure',index)
    for sample,bounds,run,seed in highs:
        for index in range(2):add(sample,bounds,20000,run+index,seed+index,'pilot',index)
    max_chunks=1000;chunk_trials=20000
    for group,(sample,bounds,_,_) in enumerate(highs):
        for index in range(max_chunks):add(sample,bounds,chunk_trials,31000001+group*100000+index,301000001+group*10000000+index,'production',index)
    manifest=dict(schema='shift-weighted-high-bin-manager-v2',tag=tag,algorithm=ALGORITHM,source_model_settings_sha256='a6bb0fd762c5805f1cb2e08c50e713d53ae41a20399adb9c748ea91fb7f8e50e',inputs=inputs,jobs=jobs,eos_base='/eos/user/j/jniedzie/shift_cmssw/weighted_high_bins/'+tag,closure_strata=['qcd_5to10','jpsi_2to5'],effective_event_targets={'qcd_20toinf':100000,'jpsi_5to10':10000,'jpsi_10to20':10000,'jpsi_20toinf':10000},budget_safety_factor=2.,chunk_trials=chunk_trials,max_chunks_per_stratum=max_chunks,max_worker_hours_per_stratum=300,max_projected_production_bytes=100000000000,max_root_bytes_per_chunk=480000000,peak_bytes_per_worker=1000000000,scheduling_ceiling=80,initial_max_workers=8,physics_valid=False,normalization_ready=False,ordinary_reference_sha256=sha(directory/'ordinary_reference.json'),independent_pilots_excluded_from_production_normalization=True)
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');checksum=sha(directory/'manifest.json')
    shared=','.join(str(directory/name) for name in inputs)+','+str(directory/'manifest.json')
    worker=['universe = vanilla',f'initialdir = {directory}','executable = /usr/bin/python3',f'arguments = run_shift_weighted_gen.py --manifest manifest.json --expected-sha256 {checksum} --node $(node_id)','should_transfer_files = YES','when_to_transfer_output = ON_EXIT',f'transfer_input_files = {shared},$(gate_input)','transfer_output_files = result.json,artifacts.zip','transfer_output_remaps = "result.json=results/$(node_id).json; artifacts.zip=results/$(node_id).zip"','getenv = False','request_cpus = 1','request_memory = 4000','request_disk = 2000000','+MaxRuntime = 4200','+ShiftProductionSuite = true',f'+ShiftSuiteTag = "{tag}"','+ShiftWeightedGen = true','+ShiftWeightedGenStage = "$(stage)"','+ShiftPeakBytesPerWorker = 1000000000','on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)','periodic_release = False','output = logs/$(node_id).out','error = logs/$(node_id).err','log = events.log','queue 1','']
    (directory/'worker.sub').write_text('\n'.join(worker))
    (directory/'gate.sub').write_text('\n'.join(['universe = scheduler',f'initialdir = {directory}','executable = /usr/bin/python3',f'arguments = {directory}/gate_shift_weighted_gen.py {directory}','getenv = False',f'+ShiftSuiteTag = "{tag}"','+ShiftWeightedScientificGate = true','output = logs/scientific_gate.out','error = logs/scientific_gate.err','log = events.log','queue 1','']))
    dag=[];parents=[];production_nodes=[]
    for index,row in enumerate(jobs):
        node=f'W{index:04d}';dag.extend([f'JOB {node} worker.sub',f'VARS {node} node_id="{row["id"]}" stage="{row["stage"]}" gate_input="{directory / "scientific_gate.json" if row["stage"]=="production" else directory / "ordinary_reference.json"}"',f'CATEGORY {node} workers'])
        if row['stage']=='production':
            production_nodes.append(node)
            dag.extend([f'PARENT SCIENTIFIC_GATE CHILD {node}',f'SCRIPT PRE {node} /usr/bin/python3 {directory}/shift_weighted_gen_pre.py {directory}/manifest.json {checksum} {row["id"]}',f'PRE_SKIP {node} 99'])
        else:parents.append(node)
    dag.extend(['JOB SCIENTIFIC_GATE gate.sub','PARENT '+' '.join(parents)+' CHILD SCIENTIFIC_GATE','MAXJOBS workers 300','NODE_STATUS_FILE nodes.status 60 ALWAYS-UPDATE'])
    (directory/'finalize.sub').write_text('\n'.join(['universe = scheduler',f'initialdir = {directory}','executable = /usr/bin/python3',f'arguments = {directory}/finalize_shift_weighted_gen.py {directory}','getenv = False',f'+ShiftSuiteTag = "{tag}"','+ShiftWeightedFinalAccounting = true','output = logs/production_final.out','error = logs/production_final.err','log = events.log','queue 1','']))
    dag.extend(['JOB FINAL_ACCOUNTING finalize.sub','PARENT '+' '.join(production_nodes)+' CHILD FINAL_ACCOUNTING'])
    (directory/'weighted_gen.dag').write_text('\n'.join(dag)+'\n')
    subprocess.run(['condor_submit_dag','-no_submit','-maxjobs','8','-maxidle','8','-maxpre','20','-batch-name',tag,'-append','hold = True','-append','+ShiftCapacityWait = True','-append','+ShiftCapacityMonitor = 12794348','-append','+ShiftProductionController = True','-append','+ShiftWeightedGenController = True','-append',f'+ShiftSuiteTag = "{tag}"','weighted_gen.dag'],cwd=directory,check=True,capture_output=True,text=True)
    for stage,row in [('pilot',jobs[0]),('production',next(r for r in jobs if r['stage']=='production'))]:
        # Dry-run production transfer path exists only after scientific gate;
        # parsing ClassAd requires no payload read or submission.
        subprocess.run(['condor_submit','-dry-run',str(directory/(stage+'.ad')),str(directory/'worker.sub'),'node_id='+row['id'],'stage='+row['stage'],'gate_input='+str(directory/'ordinary_reference.json')],check=True,capture_output=True,text=True)
    frozen={p.name:sha(p) for p in directory.iterdir() if p.is_file() and p.suffix!='.ad'}
    (directory/'freeze.json').write_text(json.dumps(frozen,indent=2)+'\n')
    return dict(directory=str(directory),manifest_sha256=checksum,tag=tag,validation_nodes=len(parents),maximum_production_nodes=4*max_chunks,initial_workers=8,scheduling_ceiling=80,peak_bytes_per_worker=1000000000,held=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',required=True);p.add_argument('--assets',required=True);p.add_argument('--runtime',required=True);a=p.parse_args();print(json.dumps(prepare(a.directory,a.assets,a.runtime),indent=2))
