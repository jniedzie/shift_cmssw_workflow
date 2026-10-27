#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded paired decay-proposal diagnostic.
"""Replay one frozen original, audit its density, and run the unchanged detector.

Ordinary and biased arms stay separate conditional diagnostics. Their copied
source exposure must never be added, and this entry never launches bulk work.
"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time

from run_shift_gen_to_nano import command
from run_shift_sm_postprocessing import download
from run_shift_source_preparation import (audit_completion, audit_genparticles,
    compare_graphs, dump, graph, metadata, product, require, sha, source_configuration)
from run_shift_weighted_gen import publish


TOOLS = Path(__file__).resolve().parents[1]


def validate(request):
    require(request['diagnostic_only'] is True and request['new_GEN_exposure']==0,
            'A decay pilot is not production exposure')
    require(request['arm'] in ('ordinary','mixture'),'Unknown paired-test arm')
    source=request['original_source']
    require(source['generator_profile']=='ordinary' and source['decay_dependent_filter'] is False,
            'Redraw before the decay-dependent muon filter')
    require(source['full_source_trials']>0 and source['saved_events']>0,'Missing full original exposure')
    require(len(request['event_id'])==3 and request['native_weight']>0,'Invalid original authority')
    require(len(request['parent_barcodes'])==len(set(request['parent_barcodes']))==2
            and len(request['first_depth_one_distances_cm'])==2
            and all(math.isfinite(x) and x>0 for x in request['first_depth_one_distances_cm']),
            'Require two distinct original parents and positive measured material scales')
    require(request['publication_directory'].startswith('/eos/user/j/jniedzie/shift_cmssw_support/'),
            'Conditional pilot outputs must not become ordinary plotter inputs')


def install_plugins(request):
    release=Path(os.environ['CMSSW_BASE']).resolve()
    scratch=Path(os.environ['_CONDOR_SCRATCH_DIR']).resolve()
    require(release.is_relative_to(scratch),'Refuse shared-release modification')
    destination=release/'lib'/os.environ['SCRAM_ARCH']
    for name,expected in request['preparation_plugins'].items():
        origin=TOOLS/'plugin'/name
        require(sha(origin)==expected,'Frozen source plugin changed: '+name)
        require(not (destination/name).is_symlink(),'Refuse linked plugin replacement')
        shutil.copyfile(origin,destination/name)
    command(['edmPluginRefresh',str(destination)],timeout=120)


def prepare(request):
    from DataFormats.FWLite import Events
    install_plugins(request)
    source=request['original_source'];local=Path('input.root')
    download(source['url'],local,source['gen_sha256'])
    events=Events(str(local));require(int(events.size())==source['saved_events'],'Raw GEN census changed')
    events.to(request['original_index']);event=events
    from audit_shift_material_scout import identity
    require(list(identity(event))==request['event_id'],'Wrong raw GEN offset')
    before=graph(event,'generatorSmeared');_,info=product(event,'generator','GenEventInfoProduct')
    require(float(info.weight())==request['native_weight'],'Wrong native original weight')
    parents={p['barcode']:p for p in before['particles']}
    scales=[]
    for barcode,distance in zip(request['parent_barcodes'],request['first_depth_one_distances_cm']):
        p=parents[barcode];require(abs(p['pdg'])==211 and p['status']==1 and p['end_vertex'] is None,
                                  'Proposal parent is not an original undecayed charged pion')
        momentum=math.sqrt(sum(x*x for x in p['momentum'][:3]))
        require(momentum>0 and p['generated_mass']>0,'Parent momentum/mass unavailable')
        scales.append(distance*10.*p['generated_mass']/momentum)
    numbers=request['parent_barcodes']
    definition=dict(ordinary_fraction=.1,components=[
        dict(probability=.2,parent_barcodes=numbers[:1],mean_proper_lifetime_mm=scales[:1]),
        dict(probability=.2,parent_barcodes=numbers[1:],mean_proper_lifetime_mm=scales[1:]),
        dict(probability=.5,parent_barcodes=numbers,mean_proper_lifetime_mm=scales)])
    if request['arm']=='ordinary':definition=dict(ordinary_fraction=1.,components=[])
    unit=dict(input_label='generatorSmeared',event_ids=[request['event_id']],seed=request['decay_seed'],
              positions=[dict(event_id=request['event_id'],target_position_cm=request['target_position_cm'])])
    config=source_configuration(unit,dict(test_scope='assumed-jaw-depth-canary'))
    config+='\np.shiftFullMuonDecays.expectedRun=cms.uint32('+str(request['event_id'][0])+')\n'
    config+='p.shiftFullMuonDecays.expectedLuminosityBlock=cms.uint32('+str(request['event_id'][1])+')\n'
    config+='p.shiftFullMuonDecays.expectedEvent=cms.uint64('+str(request['event_id'][2])+')\n'
    config+='p.shiftFullMuonDecays.ordinaryFraction=cms.double('+repr(definition['ordinary_fraction'])+')\n'
    config+='p.shiftFullMuonDecays.smoothLifetimeComponents=cms.VPSet(*[cms.PSet(probability=cms.double(c["probability"]),parentBarcodes=cms.vint32(*c["parent_barcodes"]),proposalMeanProperLifetimeMm=cms.vdouble(*c["mean_proper_lifetime_mm"])) for c in '+repr(definition['components'])+'])\n'
    config+='open("resolved_cfg.py","w").write(p.dumpPython())\n'
    Path('prepare_cfg.py').write_text(config)
    with Path('prepare.log').open('w') as log:
        command(['cmsRun','prepare_cfg.py'],stdout=log,stderr=log,timeout=900)
    prepared=Events('placed.root');require(int(prepared.size())==1,'GEN preparation lost its original')
    event=next(iter(prepared));placed=graph(event,'shiftSourcePlacement')
    target=[x*10. for x in request['target_position_cm']]
    placement=compare_graphs(before,placed,target,0.)
    complete=graph(event,'shiftFullMuonDecays')
    audit=audit_completion(event,placed,complete,definition if request['arm']=='mixture' else None)
    timed=graph(event,'shiftEventTime');timing=compare_graphs(complete,timed,target,-target[2])
    truth=audit_genparticles(event,timed)
    require(metadata(local)==metadata('placed.root'),'Full original Run/Lumi accounting changed')
    _,native=product(event,'generator','GenEventInfoProduct')
    require(float(native.weight())==request['native_weight'],'Prepared GEN changed native weight')
    particles={p['barcode']:p for p in complete['particles']}
    vertices={v['barcode']:v for v in complete['vertices']}
    daughters=[]
    for barcode in numbers:
        parent=particles[barcode];vertex=vertices[parent['end_vertex']]
        daughters.append([particles[n] for n in vertex['outgoing']
                          if abs(particles[n]['pdg'])==13 and particles[n]['status']==1])
    pairs=[]
    for a in daughters[0]:
        for b in daughters[1]:
            if a['pdg']*b['pdg']>=0:continue
            p4=[x+y for x,y in zip(a['momentum'],b['momentum'])]
            pairs.append(dict(muon_barcodes=[a['barcode'],b['barcode']],
                mass_GeV=math.sqrt(max(0.,p4[3]**2-sum(x*x for x in p4[:3]))),
                pt_GeV=math.hypot(*p4[:2])))
    audit.update(placement=placement,timing=timing,truth=truth,proposal_definition=definition,
        original_full_source=source,original_event_id=request['event_id'],new_GEN_exposure=0,
        conditional_original_choice=True,prepared_GEN_sha256=sha('placed.root'),
        selected_original_pion_direct_muon_pairs=pairs)
    dump('gen_audit.json',audit)
    return audit


def preflight(request,descriptor):
    from run_shift_sm_primary_preflight import install_addon, verify_originals, check_libraries
    source=dict(source_id=request['case_id'],gen_path=descriptor['gen'],
        gen_sha256=descriptor['gen_sha256'],full_event_ids=descriptor['event_ids'],source_descriptor=descriptor)
    gate=dict(schema='shift-SM-primary-preflight-request-v1',runtime_sha256=request['runtime_sha256'],
              preflight_addon=request['preflight_addon'],sources=[source])
    dump('preflight_request.json',gate);receipt=install_addon(gate,TOOLS)
    verify_originals(Path('placed.root'),source)
    with Path('preflight_config.log').open('w') as log:
        command([sys.executable,str(TOOLS/'scripts/run_shift_sm_primary_preflight.py'),
                 'preflight_request.json','--build-config',source['source_id'],
                 str(Path('placed.root').resolve()),str(Path('preflight_cfg.py').resolve())],
                 stdout=log,stderr=log,timeout=600)
    with Path('preflight.log').open('w') as log:
        command(['cmsRun','preflight_cfg.py'],stdout=log,stderr=log,timeout=900)
    result=json.loads(Path('preflight_cfg.json').read_text())
    require(result['complete'] and result['event_ids']==[request['event_id']],
            'Actual primary converter did not cover the complete prepared variant')
    check_libraries(Path(os.environ['CMSSW_BASE']),receipt)
    return result


def run(request,prepare_only=False):
    validate(request);started=time.monotonic()
    report=dict(complete=False,diagnostic_only=True,new_GEN_exposure=0,case_id=request['case_id'],
                arm=request['arm'],original_event_id=request['event_id'])
    dump('pilot_report.json',report)
    try:
        audit=prepare(request);report['GEN_audit']=audit;dump('pilot_report.json',report)
        descriptor=copy.deepcopy(request['detector_source'])
        descriptor.update(gen=str(Path('placed.root').resolve()),gen_path_resolved=True,
            gen_sha256=sha('placed.root'),event_ids=[request['event_id']],weights=[request['native_weight']],
            selected_indices=[0],sampling_probabilities=[1.])
        dump('pilot_admission_authority.json',dict(diagnostic_only=True,
            conditional_original_choice=True,production_admission_policy_applied=False,
            original_source_sha256=request['original_source']['gen_sha256'],
            event_id=request['event_id'],proposal_draw_id=request['decay_seed'],
            prepared_GEN_sha256=descriptor['gen_sha256'],sampling_probability=1.))
        # Reuse only the frozen physical transport contract. Never label the
        # diagnostic full admission as the old production Bernoulli draw.
        descriptor['production_selection']['ledger_sha256']=sha('pilot_admission_authority.json')
        descriptor['production_selection']['diagnostic_full_admission']=True
        descriptor['sampling']['plan_sha256']=sha('pilot_admission_authority.json')
        report['preflight']=preflight(request,descriptor)
        report['prepared_GEN_sha256']=descriptor['gen_sha256']
        if prepare_only:
            report.update(complete=True,detector_run=False,wall_seconds=time.monotonic()-started)
            dump('pilot_report.json',report);return
        gen_publications=publish([(Path('placed.root'),'gen.root'),(Path('gen_audit.json'),'gen_audit.json')],
                                  request['publication_directory']+'/prepared')
        descriptor.update(gen=gen_publications[0]['path'])
        dump('source.json',descriptor)
        command([sys.executable,str(TOOLS/'scripts/run_shift_gen_to_nano.py'),'source.json',
            '--skip','0','--count','1','--job',str(request['detector_job']),
            '--templates',str(TOOLS/'templates'),'--remove-consumed-intermediates',
            '--absolute-stage-timeouts','--stage-timeouts','5400,900,1800,3600',
            '--first-event-stall-timeout','3600','--event-stall-timeout','1800'],timeout=13500)
        detector=json.loads(Path('report.json').read_text());require(detector['complete'],'Incomplete detector chain')
        from shift_event_weights import annotate_proposal_weights
        weight=request['native_weight'];density=audit['proposal_density_ratio'];correction=audit['proposal_correction']
        authority=dict(event_id=request['event_id'],source_id=request['original_source']['source_id'],
            original_source_sha256=request['original_source']['gen_sha256'],proposal_draw_id=request['decay_seed'],
            native_weight=weight,sampling_probability=1.,sampling_gen_weight=weight,
            proposal_density_ratio=density,proposal_correction=correction,proposal_gen_weight=weight*correction,
            proposal_kind='ordinary' if request['arm']=='ordinary' else 'importance')
        exposure=dict(source_id=authority['source_id'],original_source_sha256=authority['original_source_sha256'],
                      full_source_trials=request['original_source']['full_source_trials'])
        annotation=annotate_proposal_weights('nano.root',[authority],[exposure],
                                            expected_nano_sha256=detector['nano_sha256'])
        from shift_sm_response_labels import read_nano
        labels=read_nano('nano.root',[dict(event_id=request['event_id'],gen_sha256=authority['original_source_sha256'],
                        native_weight=weight,sampling_probability=1.)],sha('nano.root'))
        dump('weight_authority.json',[authority]);dump('weight_annotation.json',annotation)
        dump('response_labels.json',labels);dump('detector_report.json',detector)
        outputs=[(Path(name),name) for name in ['nano.root','gen_audit.json','weight_authority.json',
            'weight_annotation.json','response_labels.json','detector_report.json','pilot_admission_authority.json']]
        require(sum(path.stat().st_size for path,_ in outputs)+sum(v['bytes'] for v in gen_publications)
                <request['reservation_bytes'],'Pilot publication exceeds its registered bound')
        report.update(complete=True,GEN_publications=gen_publications,
            publications=publish(outputs,request['publication_directory']+'/response'),
            labels=labels,weight_authority=authority,detector=detector,
            wall_seconds=time.monotonic()-started,detector_run=True)
        dump('pilot_report.json',report)
    except Exception as error:
        report.update(error=repr(error),wall_seconds=time.monotonic()-started)
        dump('pilot_report.json',report);raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request',type=Path);parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();run(json.loads(args.request.read_text()),args.prepare_only)
