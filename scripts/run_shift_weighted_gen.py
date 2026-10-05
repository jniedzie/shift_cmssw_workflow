#!/usr/bin/env python3
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


def command(args,log,env=None,timeout=3600):
    with Path(log).open('w') as stream:
        subprocess.run(args,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout,check=True)


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


def existing_publication(manifest,row,expected,env):
    destination=manifest['eos_base']+'/'+row['id']
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
    for name in ['gen.root','resolved_gen_cfg.py','proposal_ledger.json','semantic_audit.json']:
        item=publications[name]
        if item['path']!=destination+'/'+name or not item.get('independent_sha256_readback'):raise ValueError('Existing artifact publication contract differs')
        subprocess.run(['xrdcp','--cksum','adler32','root://eosuser.cern.ch/'+item['path'],name],check=True,timeout=900)
        if Path(name).stat().st_size!=item['bytes'] or sha(name)!=item['sha256']:raise ValueError('Existing weighted artifact changed: '+name)
    command(['python3','audit_shift_weighted_gen.py','--input','gen.root','--ledger','proposal_ledger.json','--config','resolved_gen_cfg.py','--output','resumption_audit.json'],'resumption_audit.log',env,900)
    fresh=json.loads(Path('resumption_audit.json').read_text())
    if fresh['ledger']!=saved['ledger'] or fresh['event_ids']!=saved['semantic_audit']['event_ids'] or fresh['weights']!=saved['semantic_audit']['weights']:raise ValueError('Existing EDM differs from saved semantic receipt')
    saved['resumed_existing_publication']=True;saved['resumption_audit_sha256']=sha('resumption_audit.json')
    return saved


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True);parser.add_argument('--expected-sha256',required=True);parser.add_argument('--node',required=True)
    args=parser.parse_args();start=time.monotonic();status=1
    record=dict(schema='shift-weighted-gen-receipt-v3',node_id=args.node,manifest_sha256=args.expected_sha256,complete=False,generated_edm=False,physics_valid=False,normalization_ready=False,publication_complete=False)
    atomic_json('result.json',record)
    try:
        if sha(args.manifest)!=args.expected_sha256:raise ValueError('Frozen weighted manifest changed')
        manifest=json.loads(Path(args.manifest).read_text());row=next(j for j in manifest['jobs'] if j['id']==args.node);record['request']=row
        if row.get('stage')=='production':
            gate=json.loads(Path('scientific_gate.json').read_text())
            if gate.get('schema')!='shift-weighted-high-bin-scientific-gate-v2' or not gate.get('pass') or gate.get('manifest_sha256')!=args.expected_sha256:raise ValueError('Missing frozen-manifest scientific gate')
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
        Path('bootstrap.sh').write_text(bootstrap);command(['bash','bootstrap.sh'],'bootstrap.log',timeout=600)
        env=json.loads(Path('runtime_env.json').read_text());env.pop('LD_PRELOAD',None)
        if row.get('publish',False):
            saved=existing_publication(manifest,row,args.expected_sha256,env)
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
        command(['edmConfigDump','gen_cfg.py'],'resolved_gen_cfg.py',env,600)
        weighted_env=dict(env,LD_PRELOAD=overlay)
        runstart=time.monotonic();command(['cmsRun','gen_cfg.py'],'cmsRun.log',weighted_env,row.get('timeout_seconds',3600));record['generation_seconds']=time.monotonic()-runstart
        command(['python3','audit_shift_weighted_gen.py','--input','gen.root','--ledger','proposal_ledger.json','--config','resolved_gen_cfg.py','--output','semantic_audit.json'],'audit.log',env,900)
        audit=json.loads(Path('semantic_audit.json').read_text());ledger=json.loads(Path('proposal_ledger.json').read_text())
        if ledger['algorithm']!=manifest['algorithm'] or ledger['requested_trials']!=n or ledger['run']!=run or ledger['lower']!=low or ledger['upper']!=up:raise ValueError('Returned ledger differs from frozen request')
        record.update(complete=True,generated_edm=True,event_count=ledger['accepted'],ledger=ledger,semantic_audit=audit,artifacts={name:dict(sha256=sha(name),bytes=Path(name).stat().st_size) for name in ['gen.root','gen_cfg.py','resolved_gen_cfg.py','proposal_ledger.json','semantic_audit.json','cmsRun.log']})
        if Path('gen.root').stat().st_size*2+32000000>manifest.get('peak_bytes_per_worker',1000000000):raise ValueError('Measured generation/readback peak exceeds reserved worker storage')
        if row.get('publish',False):
            destination=manifest['eos_base']+'/'+args.node
            record['publication']=publish([(Path(name),name) for name in ['gen.root','resolved_gen_cfg.py','proposal_ledger.json','semantic_audit.json']],destination)
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
    return status


if __name__=='__main__':sys.exit(main())
