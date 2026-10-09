#!/usr/bin/env python3
"""Compute-node canary/storage gate and final complete-histogram audit."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


def native(argv, timeout=120):
    env=dict(os.environ)
    for name in ('LD_LIBRARY_PATH','LD_PRELOAD','PYTHONPATH','PYTHONHOME','ROOTSYS'):
        env.pop(name,None)
    env['PATH']='/usr/bin:/bin'
    return subprocess.run(argv,env=env,check=True,capture_output=True,text=True,timeout=timeout).stdout


def download(path, target):
    native(['/usr/bin/xrdcp','--silent','--cksum','adler32',
            'root://eosuser.cern.ch/'+path,str(target)],600)


def histogram(path):
    import ROOT
    f=ROOT.TFile.Open(str(path))
    if not f or f.IsZombie() or f.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unreadable/recovered histogram ROOT file')
    raw,weighted=f.Get('rawEventsCutFlow'),f.Get('cutFlow')
    muons=f.Get('muon/ShiftMuon_pt_variable')
    if not raw or not weighted or not muons:
        raise ValueError('Missing required cutflow or muon histogram')
    values=dict(raw_events=float(raw.GetBinContent(1)),weighted_events=float(weighted.GetBinContent(1)),
        muon_sumw=float(muons.Integral(0,muons.GetNbinsX()+1)),keys=f.GetNkeys(),bytes=Path(path).stat().st_size)
    f.Close()
    return values


def gate(request, scratch):
    results=[]
    for index,c in enumerate(request['canaries']):
        status=json.loads(Path(c['status']).read_text())
        tiers={tier:c['events'] for tier in ('GEN','SIM','DIGIHLT','RECO','NANO')}
        if (status.get('exit_code')!=0 or not status.get('complete') or
                status.get('events')!=c['events'] or status.get('source_stratum')!=c['stratum'] or
                status.get('validated_tier_events')!=tiers):
            raise ValueError('Canary did not validate all detector tiers: '+c['stratum'])
        receipt=scratch/f'receipt{index}.json';download(c['receipt'],receipt)
        if hashlib.sha256(receipt.read_bytes()).hexdigest()!=status['report_sha256']:
            raise ValueError('Published canary receipt differs from worker status')
        report=json.loads(receipt.read_text())
        if (report.get('source_descriptor_sha256') !=
                request['source_descriptor_hashes'][str(c['job']-800000)] or
                not report.get('complete') or not report.get('muon_decay_audit')):
            raise ValueError('Canary did not use the frozen decay-corrected source')
        decay=report['muon_decay_audit']
        if decay.get('events')!=c['events'] or decay['decayed_parents']<=0:
            raise ValueError('Canary contains no validated residual hadron decays')
        root=scratch/f'hist{index}.root';download(c['histogram'],root)
        h=histogram(root)
        if h['raw_events']!=c['events'] or not math.isclose(h['weighted_events'],
                report['nano']['sampling_corrected_sumw'],rel_tol=1e-6,abs_tol=1e-14):
            raise ValueError('Histogram canary count/weight mismatch')
        results.append(dict(stratum=c['stratum'],events=c['events'],decay=decay,histogram=h,
                            nano_bytes=status['nano_bytes'],evidence_bytes=status['evidence_bytes'],
                            compressed_event_bytes=status['compressed_event_bytes']))
    if not sum(r['decay']['added_muons'] for r in results):
        raise ValueError('No additional muons in complete canary sample')
    text=native(['/usr/bin/eos','-b','root://eoshome-j.cern.ch','quota','ls','-m','/eos/user/j/jniedzie'])
    rows=[dict(re.findall(r'(\w+)=([^\s]+)',line)) for line in text.splitlines()]
    row=next(r for r in rows if r.get('space')=='/eos/user/j/jniedzie/')
    free=int(row['maxlogicalbytes'])-int(row['usedlogicalbytes'])
    files=int(row['maxfiles'])-int(row['usedfiles'])
    # Scale event payloads and fixed per-file headers separately for each bin.
    projected=0
    for r in results:
        event_size=r['compressed_event_bytes']/r['events']
        header=max(0,r['nano_bytes']-r['compressed_event_bytes'])
        projected += event_size*request['strata'][r['stratum']]+request['stratum_jobs'][r['stratum']]*(
            header+r['histogram']['bytes']+r['evidence_bytes'])
    projected=math.ceil(2*projected)
    if free<projected+50000000000 or files<4*request['expected_jobs']+20000:
        raise ValueError(f'Insufficient EOS quota: free_bytes={free}, projected={projected}, free_files={files}')
    return dict(canaries=results,free_bytes=free,free_files=files,projected_bytes=projected)


def final(request,scratch):
    results=[]
    for index,b in enumerate(request['bins']):
        root=scratch/f'merged{index}.root';download(b['output'],root)
        h=histogram(root)
        if h['raw_events']!=b['events']:
            raise ValueError('Merged raw event count differs from exact complete inventory: '+b['stratum'])
        if not math.isclose(h['weighted_events'],b['corrected_sumw'],rel_tol=1e-6,abs_tol=1e-14):
            raise ValueError('Merged weights differ from original sampling ledger: '+b['stratum'])
        results.append(dict(b,**h,sha256=hashlib.sha256(root.read_bytes()).hexdigest()))
    if sum(r['raw_events'] for r in results)!=request['expected_events']:
        raise ValueError('Final all-bin event total differs')
    return dict(bins=results,events=request['expected_events'],nano_jobs=request['expected_jobs'],
                histogram_jobs=request['expected_jobs'],merged_bins=len(results))


def main():
    request=json.loads(Path(sys.argv[1]).read_text())
    result=dict(complete=False,phase=request['phase'],tag=request['tag'],
                manifest_sha256=request['manifest_sha256'],checked_at_epoch=time.time())
    try:
        with tempfile.TemporaryDirectory(prefix='shift_decay_audit_') as tmp:
            function=gate if request['phase']=='gate' else final
            result.update(function(request,Path(tmp)),complete=True)
    except Exception as error:
        result['error']=repr(error)
        raise
    finally:
        Path('audit_result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)


if __name__=='__main__':
    main()
