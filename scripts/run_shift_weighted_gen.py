#!/usr/bin/env python3
# Code style: AI-code. AI-generated frozen GEN worker.
"""Frozen fixed-trial GEN worker; always returns receipts and diagnostic archive."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import zipfile


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def atomic_json(path,value):
    temp=Path(str(path)+'.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)


def command(args,log,env=None,timeout=3600,source_history_bound=None):
    with Path(log).open('w') as stream:
        if source_history_bound is None:
            subprocess.run(args,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout,check=True)
            return
        # This opt-in pilot history must be preserved, never silently truncated.
        # Stop a runaway attempt and retain its entire partial failure bundle.
        with subprocess.Popen(args,env=env,stdout=stream,stderr=subprocess.STDOUT) as process:
            deadline=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    history=Path('hard_channel_history.jsonl')
                    if history.exists() and history.stat().st_size>source_history_bound:
                        raise ValueError('Source history exceeded its frozen storage bound')
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise subprocess.TimeoutExpired(args,timeout)
                    try:process.wait(timeout=min(.5,remaining))
                    except subprocess.TimeoutExpired:pass
                if process.returncode:raise subprocess.CalledProcessError(process.returncode,args)
            except BaseException:
                process.terminate()
                try:process.wait(timeout=30)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                raise


def source_artifacts(manifest):
    return (['prefilter.root','hard_channel_history.jsonl','source_proposal_audit.json']
            if manifest.get('source_proposal') is not None else [])


def check_source_publication_size(manifest,names,record):
    if manifest.get('source_proposal') is None:return
    # Charge the full final receipt and ample room for the remote-stat/readback
    # receipts. A failed size guard preserves the attempt and publishes nothing.
    payload=sum(Path(name).stat().st_size for name in names)
    receipt_bytes=len(json.dumps(record,indent=2).encode())
    projected=payload+receipt_bytes+1024*1024
    bound=manifest['maximum_publication_bytes_per_worker']
    if projected>bound:raise ValueError('First-MPI pilot publication exceeds frozen byte reservation')
    record['publication_size_guard']=dict(payload_bytes=payload,receipt_bytes=receipt_bytes,
        receipt_margin_bytes=1024*1024,projected_bytes=projected,reserved_bytes=bound)


def publish(files,destination):
    """Fresh node directory, payloads first and completion receipt last."""
    subprocess.run(['xrdfs','root://eosuser.cern.ch','mkdir','-p',destination],check=True,timeout=120)
    metadata=[]
    for local,remote in files:
        target=destination+'/'+remote
        # Never force overwrite. A partial failed publication remains evidence.
        subprocess.run(['xrdcp','--cksum','adler32',str(local),'root://eosuser.cern.ch/'+target],check=True,timeout=900)
        stat=subprocess.run(['xrdfs','root://eosuser.cern.ch','stat',target],check=True,capture_output=True,text=True,timeout=120)
        readback=Path('readback_'+remote)
        subprocess.run(['xrdcp','--cksum','adler32','root://eosuser.cern.ch/'+target,str(readback)],check=True,timeout=900)
        if readback.stat().st_size!=Path(local).stat().st_size or sha(readback)!=sha(local):raise ValueError('Published bytes differ on independent readback: '+remote)
        metadata.append(dict(name=remote,path=target,sha256=sha(local),bytes=Path(local).stat().st_size,remote_stat=stat.stdout,independent_sha256_readback=True))
        readback.unlink()
    return metadata


def existing_publication(manifest,row,expected,env,manifest_path='manifest.json'):
    destination=row.get('output_base',manifest['eos_base'])+'/'+row['id']
    receipt=destination+'/publication_receipt.json'
    stat=subprocess.run(['xrdfs','root://eosuser.cern.ch','stat',receipt],capture_output=True,text=True,timeout=120)
    if stat.returncode:
        error=stat.stdout+stat.stderr
        if '[3011]' in error or 'No such file' in error:return None
        raise ValueError('Existing publication state is unknown: '+error)
    subprocess.run(['xrdcp','--cksum','adler32','root://eosuser.cern.ch/'+receipt,'existing_publication.json'],check=True,timeout=120)
    saved=json.loads(Path('existing_publication.json').read_text())
    if saved.get('schema')!='shift-weighted-gen-receipt-v3' or not saved.get('complete') or not saved.get('generated_edm') or not saved.get('publication_complete') or saved.get('request')!=row or saved.get('manifest_sha256')!=expected or saved.get('source_inputs')!=manifest['inputs'] or saved.get('algorithm')!=manifest['algorithm']:raise ValueError('Existing weighted completion receipt does not match frozen request')
    publications={item['name']:item for item in saved['publication']}
    for name in ['gen.root','resolved_gen_cfg.py','proposal_ledger.json','semantic_audit.json']+source_artifacts(manifest):
        item=publications[name]
        if item['path']!=destination+'/'+name or not item.get('independent_sha256_readback'):raise ValueError('Existing artifact publication contract differs')
        subprocess.run(['xrdcp','--cksum','adler32','root://eosuser.cern.ch/'+item['path'],name],check=True,timeout=900)
        if Path(name).stat().st_size!=item['bytes'] or sha(name)!=item['sha256']:raise ValueError('Existing weighted artifact changed: '+name)
    audit_command=['python3','audit_shift_weighted_gen.py','--input','gen.root','--ledger','proposal_ledger.json','--config','resolved_gen_cfg.py','--output','resumption_audit.json','--generator-profile',manifest.get('generator_profile','ordinary')]
    command(audit_command,'resumption_audit.log',env,900)
    fresh=json.loads(Path('resumption_audit.json').read_text())
    if fresh['ledger']!=saved['ledger'] or fresh['event_ids']!=saved['semantic_audit']['event_ids'] or fresh['weights']!=saved['semantic_audit']['weights']:raise ValueError('Existing EDM differs from saved semantic receipt')
    if manifest.get('source_proposal') is not None:
        old_source_audit=sha('source_proposal_audit.json')
        command(['python3','shift_first_mpi_source_proposal.py','--manifest',str(Path(manifest_path).resolve()),'--node',row['id']],
                'resumption_source_audit.log',env,900)
        if sha('source_proposal_audit.json')!=old_source_audit or saved.get('source_proposal_audit')!=json.loads(Path('source_proposal_audit.json').read_text()):raise ValueError('Existing first-MPI source audit does not reproduce')
    saved['resumed_existing_publication']=True;saved['resumption_audit_sha256']=sha('resumption_audit.json')
    return saved


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True);parser.add_argument('--expected-sha256',required=True);parser.add_argument('--node',required=True)
    args=parser.parse_args();start=time.monotonic();status=1
    record=dict(schema='shift-weighted-gen-receipt-v3',node_id=args.node,manifest_sha256=args.expected_sha256,complete=False,generated_edm=False,physics_valid=False,normalization_ready=False,publication_complete=False)
    source_proposal=None
    atomic_json('result.json',record)
    try:
        if sha(args.manifest)!=args.expected_sha256:raise ValueError('Frozen weighted manifest changed')
        manifest=json.loads(Path(args.manifest).read_text());row=next(j for j in manifest['jobs'] if j['id']==args.node);record['request']=row
        source_proposal=None
        if manifest.get('source_proposal') is not None:
            from shift_first_mpi_source_proposal import validate, generation_environment
            source_proposal=validate(manifest,row)
            record['source_proposal']=source_proposal
        profile=manifest.get('generator_profile','ordinary')
        if profile not in ('ordinary','shift-qcd-mu-enriched-cylinder-v1'):
            raise ValueError('Unsupported frozen generator profile')
        if profile!='ordinary' and (row['sample']!='qcd' or 'qcd_mu_enrichment.py' not in manifest['inputs']):
            raise ValueError('Muon-enriched profile requires QCD and its frozen profile helper')
        record['generator_profile']=profile
        if row.get('stage')=='production':
            gate=json.loads(Path('scientific_gate.json').read_text())
            if gate.get('schema')!='shift-weighted-high-bin-scientific-gate-v2' or not gate.get('pass') or gate.get('manifest_sha256')!=args.expected_sha256:raise ValueError('Missing frozen-manifest scientific gate')
            if gate.get('generator_profile','ordinary')!=profile:
                raise ValueError('Scientific gate and frozen source use different generator profiles')
            allocation=gate['production_plan'][row['stratum']]
            if row['chunk_index']>=allocation['chunks']:raise ValueError('Inactive production node should have been PRE_SKIPPED')
            record['scientific_gate_sha256']=sha('scientific_gate.json')
        for name,checksum in manifest['inputs'].items():
            if sha(name)!=checksum:raise ValueError('Frozen input changed: '+name)
        record['source_inputs']=manifest['inputs'];record['algorithm']=manifest['algorithm']
        work=Path('runtime').resolve();work.mkdir()
        with tarfile.open('runtime.tar.gz','r:gz') as archive:archive.extractall(work)
        release=work/'CMSSW_17_0_0_pre4';lib=release/'lib/el9_amd64_gcc13'
        import shutil
        shutil.copyfile('pluginShiftMpiWeightedProposalHook.so',lib/'pluginShiftMpiWeightedProposalHook.so')
        (lib/'ShiftMpiWeightedProposalHook.edmplugin').write_text('module pluginShiftMpiWeightedProposalHook.so\n')
        overlay=str(Path('libShiftMpiTailProposal.so').resolve())
        bootstrap='''set -euo pipefail
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd runtime/CMSSW_17_0_0_pre4
scram b ProjectRename > ../../relocate.log 2>&1
eval "$(scram runtime -sh)"
cd ../..
LD_PRELOAD="$PWD/libShiftMpiTailProposal.so" edmPluginRefresh "$PWD/runtime/CMSSW_17_0_0_pre4/lib/el9_amd64_gcc13"
python3 -c 'import json,os;json.dump(dict(os.environ),open("runtime_env.json","w"))'
'''
        Path('bootstrap.sh').write_text(bootstrap)
        bootstrap_env=None
        if source_proposal is not None:
            bootstrap_env={key:value for key,value in os.environ.items()
                           if not key.startswith(('SHIFT_MPI_','SHIFT_HEAVY_')) and key!='LD_PRELOAD'}
        command(['bash','bootstrap.sh'],'bootstrap.log',env=bootstrap_env,timeout=600)
        env=json.loads(Path('runtime_env.json').read_text());env.pop('LD_PRELOAD',None)
        if row.get('publish',False):
            saved=existing_publication(manifest,row,args.expected_sha256,env,args.manifest)
            if saved:
                record.update(saved);status=0;return status
        sample=row['sample'];fragment='QCD' if sample=='qcd' else 'Charmonium';fragment+='_'+'SoftMpiPartition_FixedTarget_13p6TeV'
        driver=['cmsDriver.py','Configuration/GenProduction/python/'+fragment+'_pythia8_cff.py','--step','GEN','--conditions','auto:phase1_2023_realistic','--beamspot','Realistic25ns13p6TeVEarly2023Collision','--datatier','GEN','--eventcontent','FEVTDEBUG','--geometry','DB:Extended','--era','Run3_2023','--fileout','file:gen.root','--python_filename','gen_cfg.py','--no_exec','-n',str(row['trials'])]
        command(driver,'driver.log',env,600)
        n,run,seed=row['trials'],row['run'],row['seed'];low,up=row['bounds']
        with Path('gen_cfg.py').open('a') as config:
            config.write(f'''\nfrom PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customiseKeepShiftTruth
process = customiseKeepShiftTruth(process)
process.RandomNumberGeneratorService.generator.initialSeed = cms.untracked.uint32({seed})
process.source.firstRun = cms.untracked.uint32({run})
process.source.firstLuminosityBlock = cms.untracked.uint32(1)
process.source.firstEvent = cms.untracked.uint64(1)
process.source.numberEventsInLuminosityBlock = cms.untracked.uint32({n+1})
process.source.numberEventsInRun = cms.untracked.uint32({n+1})
process.options.numberOfThreads = cms.untracked.uint32(1)
process.options.numberOfStreams = cms.untracked.uint32(1)
process.MessageLogger.cerr.FwkReport.reportEvery = cms.untracked.int32(10000)
process.generator.UserCustomization[0].pluginName = cms.string('ShiftMpiWeightedProposalHook')
process.generator.UserCustomization[0].pTHatMin = cms.double({low})
process.generator.UserCustomization[0].pTHatMax = cms.double({up})
process.generator.UserCustomization[0].proposalTrials = cms.uint32({n})
process.generator.UserCustomization[0].eventRun = cms.uint32({run})
process.generator.UserCustomization[0].statisticsFile = cms.string('proposal_ledger.json')
process.generator.PythiaParameters.processParameters.append('Check:abortIfVeto = on')
process.shiftWeightedNormalization = cms.EDProducer('ShiftWeightedGenRunInfoProducer', statisticsFile=cms.string('proposal_ledger.json'))
process.weightedNormalization_step = cms.EndPath(process.shiftWeightedNormalization)
process.schedule.insert(0, process.weightedNormalization_step)
process.FEVTDEBUGoutput.outputCommands.append('keep *_shiftWeightedNormalization_*_*')
''')
            if profile!='ordinary':
                config.write("\nfrom qcd_mu_enrichment import configure\nprocess = configure(process)\n")
            if source_proposal is not None:
                config.write('''
# Reuse cached native GEN modules; this path does not draw another event.
process.sourceProposalBeforeMuonFilter_step = cms.Path(process.ProductionFilterSequence + process.pgen)
process.schedule.insert(1, process.sourceProposalBeforeMuonFilter_step)
process.sourceProposalBeforeMuonFilterOutput = process.FEVTDEBUGoutput.clone(
    fileName = cms.untracked.string('file:prefilter.root'),
    SelectEvents = cms.untracked.PSet(SelectEvents = cms.vstring('sourceProposalBeforeMuonFilter_step')))
process.sourceProposalBeforeMuonFilterOutput_step = cms.EndPath(process.sourceProposalBeforeMuonFilterOutput)
process.schedule.append(process.sourceProposalBeforeMuonFilterOutput_step)
''')
        command(['edmConfigDump','gen_cfg.py'],'resolved_gen_cfg.py',env,600)
        weighted_env=dict(env,LD_PRELOAD=overlay)
        if source_proposal is not None:weighted_env=generation_environment(weighted_env,source_proposal)
        runstart=time.monotonic();command(['cmsRun','gen_cfg.py'],'cmsRun.log',weighted_env,row.get('timeout_seconds',3600),
            source_history_bound=source_proposal['maximum_history_bytes'] if source_proposal is not None else None);record['generation_seconds']=time.monotonic()-runstart
        command(['python3','audit_shift_weighted_gen.py','--input','gen.root','--ledger','proposal_ledger.json','--config','resolved_gen_cfg.py','--output','semantic_audit.json','--generator-profile',profile],'audit.log',env,900)
        audit=json.loads(Path('semantic_audit.json').read_text());ledger=json.loads(Path('proposal_ledger.json').read_text())
        base_algorithm=source_proposal['base_sampler_algorithm'] if source_proposal is not None else manifest['algorithm']
        if ledger['algorithm']!=base_algorithm or ledger['requested_trials']!=n or ledger['run']!=run or ledger['lower']!=low or ledger['upper']!=up:raise ValueError('Returned ledger differs from frozen request')
        if source_proposal is not None:
            command(['python3','shift_first_mpi_source_proposal.py','--manifest',args.manifest,'--node',args.node],
                    'source_proposal_audit.log',env,900)
            record['source_proposal_audit']=json.loads(Path('source_proposal_audit.json').read_text())
        artifact_names=['gen.root','gen_cfg.py','resolved_gen_cfg.py','proposal_ledger.json','semantic_audit.json','cmsRun.log']+source_artifacts(manifest)
        record.update(complete=True,generated_edm=True,event_count=audit['events'],ledger=ledger,semantic_audit=audit,artifacts={name:dict(sha256=sha(name),bytes=Path(name).stat().st_size) for name in artifact_names})
        measured_payload=sum(Path(name).stat().st_size for name in ['gen.root']+source_artifacts(manifest))
        if measured_payload*2+32000000>manifest.get('peak_bytes_per_worker',1000000000):raise ValueError('Measured generation/readback peak exceeds reserved worker storage')
        publication_names=['gen.root','resolved_gen_cfg.py','proposal_ledger.json','semantic_audit.json']+source_artifacts(manifest)
        check_source_publication_size(manifest,publication_names,record)
        if row.get('publish',False):
            destination=row.get('output_base',manifest['eos_base'])+'/'+args.node
            record['publication']=publish([(Path(name),name) for name in publication_names],destination)
            record['publication_complete']=True;record['publication_directory']=destination
            atomic_json('publication_receipt.json',record)
            publish([(Path('publication_receipt.json'),'publication_receipt.json')],destination)
        status=0
    except Exception as error:
        record.update(complete=False,error=repr(error),error_type=type(error).__name__)
        print('Weighted GEN failed; all evidence retained:',repr(error),file=sys.stderr)
    finally:
        record['wall_seconds']=time.monotonic()-start;record['exit_status']=status;atomic_json('result.json',record)
        # Fixed transfer set exists even when generator aborts before any EDM file.
        with zipfile.ZipFile('artifacts.zip','w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
            for pattern in ['*.log','*_cfg.py','*ledger*.json','*audit*.json','result.json']:
                for path in sorted(Path('.').glob(pattern)):
                    if path.is_file():archive.write(path,path.name)
            if Path('gen.root').is_file() and not record.get('publication_complete'):archive.write('gen.root')
            if source_proposal is not None and not record.get('publication_complete'):
                for name in ['prefilter.root','hard_channel_history.jsonl']:
                    if Path(name).is_file():archive.write(name)
    return status


if __name__=='__main__':sys.exit(main())
