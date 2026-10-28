#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded paired decay-proposal diagnostic.
"""Replay one frozen original, audit its density, and run the unchanged detector.

Ordinary and biased arms stay separate conditional diagnostics. Their copied
source exposure must never be added, and this entry never launches bulk work.
"""
import argparse
import copy
import gzip
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tarfile
import time

from run_shift_gen_to_nano import command
from run_shift_sm_postprocessing import download
from run_shift_source_preparation import (audit_completion, audit_genparticles,
    compare_graphs, dump, graph, metadata, product, require, sha, source_configuration)
from run_shift_weighted_gen import publish


TOOLS = Path(__file__).resolve().parents[1]


def native_ordinary(request):
    from audit_shift_weighted_gen import DEFERRED_CHARM_PROFILE
    from shift_sm_decay_bank import validate_settings
    deferred=request['original_source']['generator_profile']==DEFERRED_CHARM_PROFILE
    if request.get('decay_bank'):
        native=validate_settings(request['decay_bank'])=='native_ordinary'
        require(native==deferred,'Native ordinary banks require the declared fresh D411/D421 source profile')
        return native
    return deferred


def validate(request):
    require(request['diagnostic_only'] is True and request['new_GEN_exposure']==0,
            'A decay pilot is not production exposure')
    require(request['arm'] in ('ordinary','mixture'),'Unknown paired-test arm')
    source=request['original_source']
    native=native_ordinary(request)
    require((native or source['generator_profile']=='ordinary') and source['decay_dependent_filter'] is False,
            'Redraw before the decay-dependent muon filter')
    require(source['full_source_trials']>0 and source['saved_events']>0,'Missing full original exposure')
    require(len(request['event_id'])==3 and request['native_weight']>0,'Invalid original authority')
    probability=request.get('source_inclusion_probability',1.)
    require(math.isfinite(probability) and 0.<probability<=1.,
            'Invalid frozen original-event inclusion probability')
    scale=request.get('proposal_scale_multiplier',1.)
    require(not isinstance(scale,bool) and math.isfinite(scale) and scale>0.,
            'Lifetime proposal scale must be finite and positive')
    if not native:
        require(len(request['parent_barcodes'])==len(set(request['parent_barcodes']))==2
                and len(request['first_depth_one_distances_cm'])==2
                and all(math.isfinite(x) and x>0 for x in request['first_depth_one_distances_cm']),
                'Require two distinct original parents and positive measured material scales')
    else:
        require(request['arm']=='ordinary' or bool(request.get('decay_bank')),
                'A deferred-D mixture requires the explicit native ordinary bank')
        census=source.get('deferred_charm_frontiers')
        require(isinstance(census,list) and len(census)==source['saved_events'],
                'Native bank source lacks its complete frozen original D-frontier census')
        count=request.get('decay_bank',{}).get('size',1)
        require(type(request['decay_seed']) is int and 1<=request['decay_seed']<=899999922-count+1,
                'Native bank seeds need room for every member and the fixed completion offset')
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
        if not (destination/name).is_file() or sha(destination/name)!=expected:
            shutil.copyfile(origin,destination/name)
    command(['edmPluginRefresh',str(destination)],timeout=120)


def prepare(request,raw_input=None):
    from DataFormats.FWLite import Events
    install_plugins(request)
    source=request['original_source'];local=Path('input.root')
    if raw_input is None:
        download(source['url'],local,source['gen_sha256'])
    else:
        require(sha(raw_input)==source['gen_sha256'],'Cached raw GEN differs from original')
        os.link(raw_input,local)
    events=Events(str(local));require(int(events.size())==source['saved_events'],'Raw GEN census changed')
    events.to(request['original_index']);event=events
    from audit_shift_material_scout import identity
    require(list(identity(event))==request['event_id'],'Wrong raw GEN offset')
    before=graph(event,'generatorSmeared');_,info=product(event,'generator','GenEventInfoProduct')
    require(float(info.weight())==request['native_weight'],'Wrong native original weight')
    parents={p['barcode']:p for p in before['particles']}
    native=native_ordinary(request)
    scales=[]
    numbers=[] if native else request['parent_barcodes']
    for barcode,distance in zip(numbers,[] if native else request['first_depth_one_distances_cm']):
        p=parents[barcode];require(abs(p['pdg'])==211 and p['status']==1 and p['end_vertex'] is None,
                                  'Proposal parent is not an original undecayed charged pion')
        momentum=math.sqrt(sum(x*x for x in p['momentum'][:3]))
        require(momentum>0 and p['generated_mass']>0,'Parent momentum/mass unavailable')
        scales.append(request.get('proposal_scale_multiplier',1.)*distance*10.*p['generated_mass']/momentum)
    definition=dict(ordinary_fraction=.1,components=[
        dict(probability=.2,parent_barcodes=numbers[:1],mean_proper_lifetime_mm=scales[:1]),
        dict(probability=.2,parent_barcodes=numbers[1:],mean_proper_lifetime_mm=scales[1:]),
        dict(probability=.5,parent_barcodes=numbers,mean_proper_lifetime_mm=scales)])
    if request['arm']=='ordinary' or native:definition=dict(ordinary_fraction=1.,components=[])
    if native:
        from audit_shift_weighted_gen import deferred_charm_frontier
        expected=source['deferred_charm_frontiers'][request['original_index']]
        require(expected['event_id']==request['event_id']
                and expected['parents']==deferred_charm_frontier(before['particles']),
                'Native bank input differs from the complete original D-frontier census')
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
    if native:
        from audit_shift_weighted_gen import audit_deferred_charm_completion
        audit['deferred_charm_completion']=audit_deferred_charm_completion(placed,complete)
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
    for a in daughters[0] if daughters else []:
        for b in daughters[1]:
            if a['pdg']*b['pdg']>=0:continue
            p4=[x+y for x,y in zip(a['momentum'],b['momentum'])]
            pairs.append(dict(muon_barcodes=[a['barcode'],b['barcode']],
                mass_GeV=math.sqrt(max(0.,p4[3]**2-sum(x*x for x in p4[:3]))),
                pt_GeV=math.hypot(*p4[:2])))
    audit.update(placement=placement,timing=timing,truth=truth,proposal_definition=definition,
        original_full_source=source,original_event_id=request['event_id'],new_GEN_exposure=0,
        proposal_draw_seed=request['decay_seed'],
        conditional_original_choice=request.get('conditional_original_choice',True),
        prepared_GEN_sha256=sha('placed.root'),
        selected_original_pion_direct_muon_pairs=pairs,
        stable_muons=[dict(barcode=p['barcode'],pdg=p['pdg'],momentum_GeV=p['momentum'][:3],
            energy_GeV=p['momentum'][3],
            birth_cm=[x/10. for x in vertices[p['production_vertex']]['position'][:3]])
            for p in complete['particles'] if abs(p['pdg'])==13 and p['status']==1])
    if native:
        from shift_sm_decay_bank import audit_native_member
        audit['base_kernel']='native_ordinary'
        audit_native_member(audit)
    dump('gen_audit.json',audit)
    return audit


def prepare_bank(request):
    from shift_sm_decay_bank import choose, muon_score, audit_record, validate_settings
    from shift_gen_material_features import read_hepmc, bounded_event_features
    from DataFormats.FWLite import Events
    import ROOT
    settings=request['decay_bank'];size=settings['size'];floor=settings['control_fraction']
    kernel=validate_settings(settings)
    require(request['arm']=='mixture',
            'Require a bounded mixture bank, not new source exposure')
    root=Path.cwd();raw=root/'bank_input.root'
    download(request['original_source']['url'],raw,request['original_source']['gen_sha256'])
    material=settings['material']
    require(sha(material['geometry_path'])==material['geometry_sha256'],'Bank geometry changed')
    ROOT.TGeoManager.LockDefaultUnits(False)
    ROOT.TGeoManager.SetDefaultUnits(ROOT.TGeoManager.kRootUnits)
    ROOT.TGeoManager.LockDefaultUnits(True)
    geometry=ROOT.TGeoManager.Import(material['geometry_path'])
    require(bool(geometry),'Cannot load the registered bank geometry')
    audits=[];records=[]
    for index in range(size):
        directory=root/f'bank_{index:02d}';directory.mkdir()
        variant=copy.deepcopy(request);variant['decay_seed']=request['decay_seed']+index
        if kernel=='native_ordinary':variant['arm']='ordinary'
        try:
            os.chdir(directory);audit=prepare(variant,raw)
            event=next(iter(Events('placed.root')))
            particles,weight,weights,units=read_hepmc(event,'shiftEventTime')
            features=bounded_event_features(particles,request['event_id'],weight,weights,
                geometry,material['frame'],material['event_timeout_seconds'])
            dump('material_features.json',features)
        finally:
            os.chdir(root)
        score=muon_score(features['muons'],{p['barcode']:p for p in features['parent_flights']})
        audits.append(audit)
        records.append(dict(index=index,seed=variant['decay_seed'],score=score,
            GEN_sha256=audit['prepared_GEN_sha256'],audit_sha256=sha(directory/'gen_audit.json'),
            material_features_sha256=sha(directory/'material_features.json'),
            graph_sha256=features['graph_sha256'],
            lifetime_density_ratio=audit['proposal_density_ratio'],
            lifetime_correction=audit['proposal_correction']))
        if kernel=='native_ordinary':records[-1]['base_kernel']=kernel
    selection=choose([r['score']['score'] for r in records],floor,settings['selection_seed'])
    selected=selection['index'];directory=root/f'bank_{selected:02d}'
    for name in ['placed.root','gen_audit.json']:
        shutil.copyfile(directory/name,root/name)
    require(sha('placed.root')==records[selected]['GEN_sha256'],'Chosen GEN state changed')
    bank=dict(schema='shift-finite-decay-bank-v1',same_original_event_id=request['event_id'],
        size=size,control_fraction=floor,selection_seed=settings['selection_seed'],
        members=records,selection=selection,new_original_exposure=0,
        selected_GEN_sha256=sha('placed.root'),one_reconstructed_state_per_original=True,
        raw_original_GEN_sha256=request['original_source']['gen_sha256'],
        unselected_GEN_are_auxiliary_reproducible_states=True,
        correction_kind='base_full_lifetime_f_over_q/(bank_size*conditional_choice_probability)',
        material_survival_correction_applied=False)
    if kernel=='native_ordinary':
        bank.update(base_kernel=kernel,correction_kind='1/(bank_size*conditional_choice_probability)',
                    native_decay_bank_qualified=False)
    audit_record(bank,request['event_id'])
    dump('decay_bank.json',bank);audit=audits[selected]
    audit['decay_bank']=dict(path='decay_bank.json',sha256=sha('decay_bank.json'),
        size=size,selected_index=selected,selected_probability=selection['probabilities'][selected],
        selection_correction=selection['selection_correction'])
    if kernel=='native_ordinary':audit['decay_bank']['base_kernel']=kernel
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


def weight_authority(request,audit):
    """Keep source inclusion and the full decay likelihood as separate factors."""
    from shift_event_weights import validate_proposal_rows
    weight=request['native_weight'];probability=request.get('source_inclusion_probability',1.)
    authority=dict(event_id=request['event_id'],source_id=request['original_source']['source_id'],
        original_source_sha256=request['original_source']['gen_sha256'],proposal_draw_id=request['decay_seed'],
        native_weight=weight,sampling_probability=probability,sampling_gen_weight=weight/probability,
        proposal_density_ratio=audit['proposal_density_ratio'],proposal_correction=audit['proposal_correction'],
        proposal_gen_weight=weight/probability*audit['proposal_correction'],
        proposal_kind='ordinary' if request['arm']=='ordinary' else 'importance')
    if 'decay_bank' in audit:
        if audit['decay_bank'].get('base_kernel')=='native_ordinary':
            from shift_sm_decay_bank import audit_native_member
            audit_native_member(audit)
            require(audit['original_full_source']==request['original_source']
                    and audit['original_event_id']==request['event_id'],
                    'Native bank weight belongs to a different original source')
        bank=audit['decay_bank'];factor=bank['size']*bank['selected_probability']
        require(math.isclose(bank['selection_correction'],1./factor,rel_tol=2.e-15),
                'Finite-bank selection correction differs from its recorded choice')
        authority.update(lifetime_density_ratio=audit['proposal_density_ratio'],
            lifetime_correction=audit['proposal_correction'],bank_record=bank,
            proposal_density_ratio=audit['proposal_density_ratio']*factor,
            proposal_correction=audit['proposal_correction']/factor,
            proposal_gen_weight=weight/probability*audit['proposal_correction']/factor,
            proposal_kind='importance_bank')
    validate_proposal_rows([authority])
    return authority


def truth_diagnostics(path):
    """Evaluation only: retain momentum/hit associations without cutting pairs."""
    import ROOT
    file=ROOT.TFile.Open(str(path))
    if not file or file.IsZombie():
        raise ValueError('Cannot read Nano truth diagnostics')
    try:
        tree=file.Get('Events')
        require(tree and tree.GetEntries()==1,'Expected one original in truth readback')
        tree.GetEntry(0)
        names=[b.GetName() for b in tree.GetListOfBranches()]
        fields=[name for name in names if name.startswith('ShiftMuon_') and (
            name in ['ShiftMuon_pt','ShiftMuon_pz','ShiftMuon_charge','ShiftMuon_topology']
            or any(token in name.lower() for token in ['hitsimtrack','hitgenpart','hittruthpurity',
                'simtruthmatched','simpatfitted','qoverp','chi2','ndof']))]
        values={name:[float(v) if math.isfinite(float(v)) else None
                      for v in getattr(tree,name)] for name in fields}
        return dict(evaluation_only=True,applies_no_reco_or_histogram_cut=True,
            stored_fields=values,strict_hit_GEN_field_available='ShiftMuon_hitGenPartIdx' in fields,
            missing_truth_fields_are_not_zero_efficiency=True)
    finally:
        file.Close()


def prepared_outputs(audit):
    outputs=[(Path('placed.root'),'gen.root'),(Path('gen_audit.json'),'gen_audit.json')]
    if 'decay_bank' in audit:
        outputs.append((Path('decay_bank.json'),'decay_bank.json'))
        for index in range(audit['decay_bank']['size']):
            for local,name in [('gen_audit.json','audit.json'),('material_features.json','material_features.json')]:
                path=Path(f'bank_{index:02d}')/local
                compressed=path.with_suffix(path.suffix+'.gz')
                compressed.write_bytes(gzip.compress(path.read_bytes(),mtime=0))
                outputs.append((compressed,f'bank_{index:02d}_'+name+'.gz'))
    return outputs


def publish_prepared_source(request,audit,descriptor,authority):
    """Retain one exact decay draw and its separate weights before any SIM."""
    outputs=prepared_outputs(audit)
    base=request['publication_directory']+'/prepared'
    descriptor=copy.deepcopy(descriptor)
    descriptor['gen']=base+'/gen.root'
    dump('weight_authority.json',[authority])
    descriptor['prepared_weight_authority']=dict(path=base+'/weight_authority.json',sha256=sha('weight_authority.json'),
        required_for_downstream_weights=True,diagnostic_only=True)
    dump('prepared_source.json',descriptor)
    outputs.extend((Path(name),name) for name in
                   ['prepared_source.json','weight_authority.json','pilot_admission_authority.json'])
    require(sum(path.stat().st_size for path,_ in outputs)<request['reservation_bytes'],
            'Prepared GEN, authority and evidence exceed their declared publication bound')
    require(sha('placed.root')==descriptor['gen_sha256'],'Prepared GEN changed before publication')
    return publish(outputs,base),descriptor


def preserve_failed_prepared(request):
    """Return one bounded produced GEN failure through the existing dispatcher."""
    bound=request.get('failure_GEN_return_bound_bytes',0)
    if not bound:return None
    require(type(bound) is int and 0<bound<=1048576,'Failed GEN return archive must be bounded to at most1MiB')
    candidates=[Path('placed.root')]+sorted(Path('.').glob('bank_*/placed.root'),reverse=True)
    original=next((p for p in candidates if p.is_file() and not p.is_symlink()),None)
    if original is None:return dict(produced_GEN_available=False)
    receipt=dict(produced_GEN_available=True,path=str(original),bytes=original.stat().st_size,sha256=sha(original))
    archive=Path('failed_prepared_evidence.tar.gz')
    if original.stat().st_size>8*bound:
        receipt['archive_omitted']='Produced GEN exceeds the bounded diagnostic compression input'
    else:
        retained_bytes=0
        with tarfile.open(archive,'w:gz') as output:
            for name in ['placed.root','gen_audit.json','prepare_cfg.py','prepare.log']:
                path=original.with_name(name)
                if (path.is_file() and not path.is_symlink()
                        and retained_bytes+path.stat().st_size<=8*bound):
                    output.add(path,arcname=name);retained_bytes+=path.stat().st_size
        if archive.stat().st_size>bound:
            archive.unlink();receipt['archive_omitted']='Compressed GEN failure exceeds the declared return bound'
        else:receipt['archive']=dict(path=str(archive),bytes=archive.stat().st_size,sha256=sha(archive))
    dump('failed_prepared_receipt.json',receipt)
    return receipt


def run(request,prepare_only=False,publish_prepared=False):
    require(not publish_prepared or prepare_only,'--publish-prepared requires --prepare-only')
    validate(request);started=time.monotonic()
    report=dict(complete=False,diagnostic_only=True,new_GEN_exposure=0,case_id=request['case_id'],
                arm=request['arm'],original_event_id=request['event_id'])
    dump('pilot_report.json',report)
    try:
        audit=prepare_bank(request) if request.get('decay_bank') else prepare(request)
        report['GEN_audit']=audit;dump('pilot_report.json',report)
        descriptor=copy.deepcopy(request['detector_source'])
        probability=request.get('source_inclusion_probability',1.)
        descriptor.update(gen=str(Path('placed.root').resolve()),gen_path_resolved=True,
            gen_sha256=sha('placed.root'),event_ids=[request['event_id']],weights=[request['native_weight']],
            selected_indices=[0],sampling_probabilities=[probability])
        dump('pilot_admission_authority.json',dict(diagnostic_only=True,
            conditional_original_choice=request.get('conditional_original_choice',True),
            production_admission_policy_applied=False,
            original_source_sha256=request['original_source']['gen_sha256'],
            event_id=request['event_id'],proposal_draw_id=request['decay_seed'],
            prepared_GEN_sha256=descriptor['gen_sha256'],sampling_probability=probability,
            validation_draw_sha256=request.get('validation_draw_sha256')))
        # Reuse only the frozen physical transport contract. Never label the
        # diagnostic full admission as the old production Bernoulli draw.
        descriptor['production_selection']['ledger_sha256']=sha('pilot_admission_authority.json')
        descriptor['production_selection']['diagnostic_full_admission']=True
        descriptor['sampling']['plan_sha256']=sha('pilot_admission_authority.json')
        report['preflight']=preflight(request,descriptor)
        report['prepared_GEN_sha256']=descriptor['gen_sha256']
        report['weight_authority']=weight_authority(request,audit)
        if prepare_only:
            if publish_prepared:
                publications,saved=publish_prepared_source(request,audit,descriptor,report['weight_authority'])
                pins={p['name']:p for p in publications}
                report.update(GEN_publications=publications,prepared_source=saved,
                    prepared_source_receipt=pins['prepared_source.json'],
                    prepared_weight_authority=pins['weight_authority.json'],
                    published_prepared_state=True,prepared_state_must_not_be_redrawn=True)
            report.update(complete=True,detector_run=False,wall_seconds=time.monotonic()-started)
            dump('pilot_report.json',report);return
        gen_outputs=prepared_outputs(audit)
        if 'decay_bank' in audit:
            require(sum(path.stat().st_size for path,_ in gen_outputs)<request['reservation_bytes']//2,
                    'GEN bank exceeds its half of the publication reservation')
        gen_publications=publish(gen_outputs,request['publication_directory']+'/prepared')
        descriptor.update(gen=gen_publications[0]['path'])
        dump('source.json',descriptor)
        command([sys.executable,str(TOOLS/'scripts/run_shift_gen_to_nano.py'),'source.json',
            '--skip','0','--count','1','--job',str(request['detector_job']),
            '--templates',str(TOOLS/'templates'),'--remove-consumed-intermediates',
            '--absolute-stage-timeouts','--stage-timeouts','5400,900,1800,3600',
            '--first-event-stall-timeout','3600','--event-stall-timeout','1800'],timeout=13500)
        detector=json.loads(Path('report.json').read_text());require(detector['complete'],'Incomplete detector chain')
        from shift_event_weights import annotate_proposal_weights
        weight=request['native_weight'];authority=weight_authority(request,audit)
        exposure=dict(source_id=authority['source_id'],original_source_sha256=authority['original_source_sha256'],
                      full_source_trials=request['original_source']['full_source_trials'])
        annotation=annotate_proposal_weights('nano.root',[authority],[exposure],
                                            expected_nano_sha256=detector['nano_sha256'])
        from shift_sm_response_labels import read_nano
        labels=read_nano('nano.root',[dict(event_id=request['event_id'],gen_sha256=authority['original_source_sha256'],
                        native_weight=weight,sampling_probability=probability)],sha('nano.root'))
        truth=truth_diagnostics('nano.root');dump('truth_diagnostics.json',truth)
        dump('weight_authority.json',[authority]);dump('weight_annotation.json',annotation)
        dump('response_labels.json',labels);dump('detector_report.json',detector)
        outputs=[(Path(name),name) for name in ['nano.root','gen_audit.json','weight_authority.json',
            'weight_annotation.json','response_labels.json','detector_report.json',
            'pilot_admission_authority.json','truth_diagnostics.json']]
        require(sum(path.stat().st_size for path,_ in outputs)+sum(v['bytes'] for v in gen_publications)
                <request['reservation_bytes'],'Pilot publication exceeds its registered bound')
        report.update(complete=True,GEN_publications=gen_publications,
            publications=publish(outputs,request['publication_directory']+'/response'),
            labels=labels,weight_authority=authority,detector=detector,truth_diagnostics=truth,
            wall_seconds=time.monotonic()-started,detector_run=True)
        dump('pilot_report.json',report)
    except Exception as error:
        report.update(error=repr(error),wall_seconds=time.monotonic()-started)
        try:report['failed_prepared_evidence']=preserve_failed_prepared(request)
        except Exception as preservation_error:report['failed_prepared_preservation_error']=repr(preservation_error)
        dump('pilot_report.json',report);raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request',type=Path);parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--publish-prepared',action='store_true')
    args=parser.parse_args();run(json.loads(args.request.read_text()),args.prepare_only,args.publish_prepared)
