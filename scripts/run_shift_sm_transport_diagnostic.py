#!/usr/bin/env python3
# Code style: AI-code. AI-generated replay of exact saved SM detector inputs.
"""Record transport, hits and fitted states without redrawing GEN or selection.

The established detector runner owns every configuration and RNG assignment.
Only stage-1 cmsRun may receive a passive logger in its subprocess environment.
Logged physics results require the paired plain/logged native comparison.
"""
import argparse
import copy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

from run_shift_sm_postprocessing import download
from run_shift_sm_decay_pilot import install_plugins, preflight


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def stage_command(original, logger):
    """Inject no config or RNG change; ordinary commands keep their own env."""
    def command(argv, **kwargs):
        if logger is not None and len(argv)>1 and Path(argv[0]).name=='cmsRun' and Path(argv[1]).name=='step1_cfg.py':
            env=dict(kwargs.pop('env',os.environ))
            if env.get('LD_PRELOAD'):
                raise ValueError('Refuse to compose an unknown preloaded library')
            env['LD_PRELOAD']=str(logger)
            kwargs['env']=env
        return original(argv,**kwargs)
    return command


def nano_readback(path):
    """Keep original candidate identities and all existing fitted diagnostics."""
    import ROOT
    file=ROOT.TFile.Open(str(path))
    if not file or file.IsZombie():
        raise ValueError('Unreadable diagnostic Nano')
    try:
        tree=file.Get('Events')
        if not tree or tree.GetEntries()!=1:
            raise ValueError('Expected exactly one original in diagnostic Nano')
        tree.GetEntry(0)
        result={}
        for branch in tree.GetListOfBranches():
            name=branch.GetName()
            if name.startswith(('ShiftMuon_','ShiftDimuonVertex_')):
                values=[]
                for value in getattr(tree,name):
                    value=float(value)
                    values.append(value if math.isfinite(value) else None)
                result[name]=values
        return result
    finally:
        file.Close()


def run(request,prepare_only=False):
    import run_shift_gen_to_nano as chain
    from audit_shift_transport_response import native_snapshot,parse_logger,summarize_selected
    from audit_shift_material_scout import audit
    from shift_sm_response_labels import read_nano
    started=time.monotonic();report=dict(complete=False,diagnostic_only=True,new_GEN_exposure=0,
        case_id=request['case_id'],original_event_id=request['event_id'],mode=request['mode'])
    dump('diagnostic_report.json',report)
    try:
        prior=Path(request['original_request']['path'])
        result_path=Path(request['original_result']['path'])
        if sha(prior)!=request['original_request']['sha256'] or sha(result_path)!=request['original_result']['sha256']:
            raise ValueError('Original frozen request/result changed')
        original=json.loads(prior.read_text());previous=json.loads(result_path.read_text())
        if (not previous['complete'] or previous['exit_code']!=0
                or previous['request_sha256']!=request['original_request']['sha256']
                or original['event_id']!=request['event_id']):
            raise ValueError('Replay needs one exact successful prior detector response')
        install_plugins(original)
        source=copy.deepcopy(original['detector_source'])
        pin=next(p for p in previous['GEN_publications'] if p['name']=='gen.root')
        if pin!=request['selected_GEN']:
            raise ValueError('Replay must reuse the actually selected GEN state')
        source.update(gen=pin['path'],gen_path_resolved=True,gen_sha256=pin['sha256'],
            event_ids=[original['event_id']],weights=[original['native_weight']],selected_indices=[0],
            sampling_probabilities=[original.get('source_inclusion_probability',1.)])
        source['production_selection']=previous['detector']['production_selection']
        source['sampling']['plan_sha256']=previous['detector']['sampling_plan_sha256']
        dump('source.json',source)
        if prepare_only:
            download(pin['path'],Path('placed.root'),pin['sha256'])
            local=copy.deepcopy(source);local['gen']=str(Path('placed.root').resolve())
            check=preflight(original,local)
            report.update(complete=True,detector_run=False,preflight=check,
                selected_GEN_sha256=pin['sha256'],original_seed=previous['detector']['seed'],
                wall_seconds=time.monotonic()-started)
            dump('diagnostic_report.json',report)
            return
        logger=None
        if request['mode']=='logged':
            logger=Path(__file__).resolve().parents[1]/request['logger']['path']
            if sha(logger)!=request['logger']['sha256']:
                raise ValueError('Passive logger bytes changed')
        elif request['mode']!='plain':
            raise ValueError('Unknown diagnostic mode')
        saved_command=chain.command;argv=sys.argv
        try:
            chain.command=stage_command(saved_command,logger)
            sys.argv=[str(Path(chain.__file__)),'source.json','--skip','0','--count','1',
                '--job',str(original['detector_job']),'--templates',str(Path(__file__).resolve().parents[1]/'templates'),
                '--absolute-stage-timeouts','--stage-timeouts','5400,900,1800,3600',
                '--first-event-stall-timeout','3600','--event-stall-timeout','1800']
            chain.main()
        finally:
            chain.command=saved_command;sys.argv=argv
        detector=json.loads(Path('report.json').read_text())
        if not detector['complete'] or detector['seed']!=previous['detector']['seed']:
            raise ValueError('Detector replay failed or changed its seed')
        native=native_snapshot('step1.root');blob=canonical(native)
        Path('native_sim.json.gz').write_bytes(gzip.compress(blob,mtime=0))
        scout=audit(Path('gen.root'),Path('step1.root'),expected_event_ids={tuple(request['event_id'])})
        if not scout['passed']:
            raise ValueError('Saved GEN/SIM identity, weights, graph or metadata changed')
        Path('sim_audit.json.gz').write_bytes(gzip.compress(canonical(scout),mtime=0))
        logs=parse_logger(Path('step1.log').read_text(errors='replace'))
        if logger:
            paths={d['symbol']:d['path'] for d in logs['delegates']}
            for symbol,pin in request['native_delegates'].items():
                target=Path(paths[symbol])
                if target.name!=pin['name'] or sha(target)!=pin['sha256']:
                    raise ValueError('Passive logger did not delegate to the pinned native '+symbol)
            if not logs['terminals'] or not logs['decays'] or logs['errors']:
                raise ValueError('Missing/invalid passive transport records')
        elif logs['terminals'] or logs['decays']:
            raise ValueError('Plain control unexpectedly loaded the logger')
        Path('transport_log.json.gz').write_bytes(gzip.compress(canonical(logs),mtime=0))
        fields=nano_readback('nano.root');dump('nano_readback.json',fields)
        expected=dict(event_id=original['event_id'],gen_sha256=original['original_source']['gen_sha256'],
            native_weight=original['native_weight'],sampling_probability=original.get('source_inclusion_probability',1.))
        labels=read_nano('nano.root',[expected],sha('nano.root'))
        summary=summarize_selected(native,logs,request['selected_muon_barcodes'],
            original['parent_barcodes'],reco_labels=labels,reco_truth=fields)
        dump('transport_summary.json',summary)
        report.update(complete=True,detector=detector,labels=labels,
            native_sim_canonical_sha256=hashlib.sha256(blob).hexdigest(),
            native_sim_bytes=len(blob),selected_GEN_sha256=pin['sha256'],
            original_weight_authority=previous['weight_authority'],
            saved_original_exposure_not_added=True,transport_summary=summary,
            nano_diagnostics_canonical_sha256=hashlib.sha256(canonical(fields)).hexdigest(),
            wall_seconds=time.monotonic()-started,
            logged_physics_usable_only_after_paired_native_equality=request['mode']=='logged')
        # Native tiers remain worker-local. Compact proof is sufficient to
        # rerun from the retained exact selected GEN, runtime and seed.
        for name in ['step1_cfg.py','step2_cfg.py','step3_cfg.py','step4_cfg.py','report.json']:
            report.setdefault('config_and_chain_sha256',{})[name]=sha(name)
        dump('diagnostic_report.json',report)
        if (Path('diagnostic_report.json').stat().st_size
                +sum(Path(n).stat().st_size for n in request['return_files'] if Path(n).is_file())
                >request['maximum_return_bytes']):
            raise ValueError('Compact diagnostic proof exceeds its registered AFS bound')
    except Exception as error:
        # Preserve the first failed transport attempt before worker retirement;
        # canonical selected GEN is already retained independently on EOS.
        files=[Path(name) for name in ['step1.root','step2.root','step3.root','nano.root']
               if Path(name).is_file()]
        if files:
            try:
                if sum(f.stat().st_size for f in files)>request['maximum_failure_publication_bytes']:
                    raise ValueError('Failed native tiers exceed the registered retention bound')
                from run_shift_weighted_gen import publish
                report['first_failure_native_publications']=publish(
                    [(f,f.name) for f in files],request['failure_publication_directory'])
            except Exception as retention_error:
                report['failure_retention_error']=repr(retention_error)
        tails={}
        for name in ['step1.log','step2.log','step3.log','step4.log']:
            if Path(name).is_file():
                with Path(name).open('rb') as stream:
                    stream.seek(max(0,Path(name).stat().st_size-131072))
                    tails[name]=stream.read().decode(errors='replace')
        dump('failure_logs.json',tails)
        report.update(error=repr(error),wall_seconds=time.monotonic()-started)
        dump('diagnostic_report.json',report)
        raise


def collect(request):
    """Gate every logged interpretation on the unchanged native control."""
    plan_path=Path(request['plan_path'])
    if sha(plan_path)!=request['plan_sha256']:
        raise ValueError('Diagnostic plan changed')
    plan=json.loads(plan_path.read_text());rows={}
    for case in plan['cases']:
        path=Path(case['path']);result_path=path.with_name('result.json')
        if sha(path)!=case['sha256']:
            raise ValueError('Diagnostic worker authority changed')
        result=json.loads(result_path.read_text())
        if (not result['complete'] or result['exit_code']!=0
                or result['request_sha256']!=case['sha256']):
            raise ValueError('Failed/missing diagnostic is not a physics loss')
        original=json.loads(path.read_text());old=json.loads(Path(original['original_result']['path']).read_text())
        if result['labels']!=old['labels']:
            raise ValueError('Exact replay changed the original reconstructed candidates: '+case['case_id'])
        rows[case['case_id']]=result
    plain,logged=(rows[name] for name in plan['paired_logger_control'])
    if (plain['original_event_id']!=logged['original_event_id']
            or plain['selected_GEN_sha256']!=logged['selected_GEN_sha256']
            or plain['detector']['seed']!=logged['detector']['seed']
            or plain['native_sim_canonical_sha256']!=logged['native_sim_canonical_sha256']
            or plain['nano_diagnostics_canonical_sha256']!=logged['nano_diagnostics_canonical_sha256']):
        raise ValueError('Passive logger changed native truth/hits/metadata or fitted output')
    report=dict(complete=True,complete_frozen_draw_collected=True,
        benchmark_plan_sha256=request['plan_sha256'],diagnostic_only=True,new_GEN_exposure=0,
        original_event_count=len({tuple(v['original_event_id']) for v in rows.values()}),
        logger_current_runtime_nonperturbation_qualified=True,
        all_original_reconstructed_candidates_reproduced=True,
        physics_normalization_ready=False,production_policy_qualified=False,
        events={name:r['transport_summary'] for name,r in rows.items() if r['mode']=='logged'})
    dump('diagnostic_report.json',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('request',type=Path)
    parser.add_argument('--collect',action='store_true');parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();request=json.loads(args.request.read_text())
    collect(request) if args.collect else run(request,args.prepare_only)
