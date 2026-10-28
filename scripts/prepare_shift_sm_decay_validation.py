#!/usr/bin/env python3
# Code style: AI-code. AI-generated independent paired QCD validation preparation.
"""Freeze a complete GEN census and a probability-sampled, paired detector test.

Parent rays and masses define sampling strata, never physical survival weights.
This prepares a bounded validation DAG; it neither submits nor launches bulk.
"""
import argparse
import copy
import gzip
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import shutil
import tarfile

from prepare_shift_sm_response_jobs import persistent_directory, sha, submit_text
from shift_sm_response_features import _station_misses


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def read_pin(pin):
    path=Path(pin['path'])
    if sha(path)!=pin['sha256']:
        raise ValueError('Validation input changed: '+str(path))
    with (gzip.open(path,'rt') if path.suffix=='.gz' else path.open()) as stream:
        return json.load(stream)


def original_census(audit, features, source):
    """Recover only original, undecayed pion parents using the completion audit."""
    if not audit['complete'] or len(audit['units'])!=1:
        raise ValueError('Require one complete audited original source')
    unit=audit['units'][0];events=unit['events']
    if (unit['source']!=source or len(events)!=source['saved_events']
            or [e['event_id'] for e in events]!=[e['event_id'] for e in features['events']]
            or len({tuple(e['event_id']) for e in events})!=len(events)):
        raise ValueError('Original census, source identity or ordered events differ')
    rows=[]
    for index,(event,feature) in enumerate(zip(events,features['events'])):
        if (event['native_weight']!=feature['native_weight']
                or feature['original_source_sha256']!=source['gen_sha256']):
            raise ValueError('Original native weight or source hash differs')
        completion=event['completion']
        if len(completion['parent_barcodes'])!=len(completion['parent_PDG_ids']):
            raise ValueError('Original parent inventory has incomplete PDG labels')
        roots={b for b,pdg in zip(completion['parent_barcodes'],completion['parent_PDG_ids'])
               if abs(pdg)==211}
        parents={p['barcode']:p for p in feature['parent_flights']}
        unknown=bool(roots-set(parents));pairs=[]
        for a,b in itertools.combinations([parents[n] for n in sorted(roots) if n in parents],2):
            if a['pdg']*b['pdg']>=0:
                continue
            rays=[_station_misses(p) for p in (a,b)]
            if any(r is None or r['two_station_miss'] is None for r in rays):
                # Original pions are born at the known jaw position. A ray
                # going away from a CMS hemisphere is a known geometric miss.
                if any(p.get('birth_cm') is None or p.get('momentum_GeV') is None for p in (a,b)):
                    unknown=True
                continue
            miss=max(r['two_station_miss'] for r in rays)
            momentum=[x+y for x,y in zip(a['momentum_GeV'],b['momentum_GeV'])]
            mass=math.sqrt(max(0.,(a['energy_GeV']+b['energy_GeV'])**2-sum(x*x for x in momentum)))
            distances=[p['material'].get('first_depth_one_distance_cm') for p in (a,b)]
            pairs.append(dict(parent_barcodes=[a['barcode'],b['barcode']],
                first_depth_one_distances_cm=distances,parent_pair_mass_GeV=mass,
                two_station_miss_cm=miss))
        tail=any(p['two_station_miss_cm']==0. and p['parent_pair_mass_GeV']>20. for p in pairs)
        known=[p for p in pairs if all(d is not None and math.isfinite(d) and d>0.
                                     for d in p['first_depth_one_distances_cm'])]
        if not known:
            # This test's two-parent proposal cannot model this original yet.
            # Stop rather than omit it or guess a material distance.
            raise ValueError('No measured two-parent proposal for original '+str(event['event_id']))
        best=max(known,key=lambda p:(p['two_station_miss_cm']==0.,
            p['parent_pair_mass_GeV']/(1.+p['two_station_miss_cm']/50.),
            tuple(p['parent_barcodes'])))
        rows.append(dict(event_id=event['event_id'],original_index=index,
            native_weight=event['native_weight'],target_position_cm=event['target_position_cm'],
            region='unknown' if unknown else 'tail' if tail else 'control',proposal=best))
    return rows


def frozen_draw(rows, seed, control_fraction=.1):
    """Fixed-size uniform controls; protect tail opportunities and unknowns."""
    if not 0.<control_fraction<=1. or not rows:
        raise ValueError('Require nonempty originals and a positive control fraction')
    if len({tuple(r['event_id']) for r in rows})!=len(rows):
        raise ValueError('Repeated original in sampling census')
    result=copy.deepcopy(rows);controls=[r for r in result if r['region']=='control']
    count=math.ceil(control_fraction*len(controls))
    for row in result:
        row['draw_hash']=hashlib.sha256((seed+':'+json.dumps(row['event_id'],separators=(',',':'))).encode()).hexdigest()
        row['sampling_probability']=count/len(controls) if row['region']=='control' else 1.
        row['selected']=row['region']!='control'
    chosen={tuple(r['event_id']) for r in sorted(controls,key=lambda r:r['draw_hash'])[:count]}
    for row in result:
        if row['region']=='control':
            row['selected']=tuple(row['event_id']) in chosen
    return result


def freeze_tools(config, directory):
    original=Path(config['base_tools']['path'])
    if sha(original)!=config['base_tools']['sha256']:
        raise ValueError('Original tools capsule changed')
    replacements={name:Path(__file__).with_name(name).read_bytes() for name in
        ['run_shift_sm_decay_pilot.py','run_shift_sm_decay_collection.py','shift_sm_decay_bank.py',
         'shift_sm_response_features.py','shift_gen_material_features.py','shift_material_path_score.py',
         'audit_shift_weighted_gen.py','shift_first_mpi_source_proposal.py','run_shift_smart_sm_source.py',
         'shift_production_selection.py','run_shift_gen_to_nano.py','run_shift_sm_postprocessing.py']}
    capsule=directory/'frozen_tools.tar.gz';changed=[]
    with tarfile.open(original) as before,tarfile.open(capsule,'w:gz') as after:
        names=set()
        for member in before:
            names.add(member.name)
            if member.name in {'scripts/'+name for name in replacements}:
                raw=replacements[Path(member.name).name];member=copy.copy(member)
                member.size=len(raw);after.addfile(member,io.BytesIO(raw));changed.append(member.name)
            else:
                after.addfile(member,before.extractfile(member) if member.isfile() else None)
        for name,raw in replacements.items():
            member_name='scripts/'+name
            if member_name not in names:
                member=tarfile.TarInfo(member_name);member.size=len(raw);member.mode=0o644
                after.addfile(member,io.BytesIO(raw));changed.append(member_name)
    if len(changed)!=len(replacements):
        raise ValueError('Incomplete frozen helper replacement')
    bootstrap=Path(config['bootstrap']['path'])
    if sha(bootstrap)!=config['bootstrap']['sha256']:
        raise ValueError('Frozen bootstrap changed')
    shutil.copyfile(bootstrap,directory/'run_shift_stream_worker.sh')
    (directory/'run_shift_stream_worker.sh').chmod(0o755)
    save(directory/'derivation.json',dict(base=config['base_tools'],changed_members=changed,
        helper_sha256={name:hashlib.sha256(raw).hexdigest() for name,raw in replacements.items()},
        runtime_and_all_other_members_unchanged=True))
    return dict(capsule=str(capsule),capsule_sha256=sha(capsule),
                bootstrap=str(directory/'run_shift_stream_worker.sh'))


def prepare(config, output):
    output=persistent_directory(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Refuse to overwrite an existing validation deployment')
    result=read_pin(config['reference_result']);audit=read_pin(config['audit']);features=read_pin(config['features'])
    if not result['complete'] or result['exit_code']!=0:
        raise ValueError('Reference source did not complete')
    pins={p['name']:p for p in result['prepared_publications']}
    if (config['audit']['sha256']!=pins['audit.json']['sha256']
            or config['features']['sha256']!=pins['qcd_features.json.gz']['sha256']):
        raise ValueError('Features/audit are not the published reference artifacts')
    census=original_census(audit,features,result['source'])
    descriptor=result['selected_source']
    if (descriptor['event_ids']!=[r['event_id'] for r in census]
            or descriptor['weights']!=[r['native_weight'] for r in census]
            or audit['units'][0]['output_sha256']!=descriptor['gen_sha256']
            or features['sources'][0]['sha256']!=descriptor['gen_sha256']):
        raise ValueError('Prepared GEN descriptor does not cover the exact full census')
    draw=frozen_draw(census,config['selection_seed']);selected=[r for r in draw if r['selected']]
    if len(selected)>config['maximum_selected_originals']:
        raise ValueError('Frozen draw exceeds the bounded worker/storage plan')
    output.mkdir(parents=True,exist_ok=True);frozen=freeze_tools(config,output)
    draw_path=output/'original_draw.json';save(draw_path,dict(source=result['source'],rows=draw,
        seed=config['selection_seed'],control_design='fixed-size uniform without replacement',
        tail_and_unknown_inclusion_probability=1.,control_fraction_at_least=.1,
        no_muon_pt_cut=True,no_physical_survival_weight=True))
    draw_sha=sha(draw_path);template=read_pin(config['template_request'])
    cases=[];nodes=[]

    def worker(name, request, maximum):
        directory=output/name;directory.mkdir();save(directory/'request.json',request)
        submit=submit_text(directory,request,frozen,100)
        submit=submit.replace('+MaxRuntime = 3600','+MaxRuntime = '+str(maximum))
        submit=submit.replace('request_memory = 4000','request_memory = 6000')
        (directory/'worker.sub').write_text(submit)
        return directory

    source=result['selected_source']
    gate=dict(schema='shift-stream-payload-v1',environment='cmssw',stage='sm_primary_preflight',
        cohort_id=config['cohort_id'],application_timeout_seconds=2700,result_path='report.json',
        return_files=[],failure_return_files=['preflight_0000.cmsRun.log','preflight_0000.configuration.log'],
        status_fields={},reservation_bytes=1048576,
        argv=['python3','@TOOLS@/scripts/run_shift_sm_primary_preflight.py','request.json'],
        runtime_sha256=template['runtime_sha256'],preflight_addon=template['preflight_addon'],
        sources=[dict(source_id=result['source_id'],gen_path=source['gen'],gen_sha256=source['gen_sha256'],
            full_event_ids=source['event_ids'],source_descriptor=source)])
    gate_dir=worker('census_preflight',gate,3600)
    nodes.append('JOB CENSUS '+str(gate_dir/'worker.sub'))
    qualified=False
    for index,row in enumerate(selected):
        for arm in ['ordinary','mixture']:
            name='e'+str(row['event_id'][2]).zfill(4)+'_'+arm
            request=copy.deepcopy(template)
            descriptor=copy.deepcopy(source)
            descriptor.update(event_ids=[row['event_id']],weights=[row['native_weight']],
                selected_indices=[0],sampling_probabilities=[row['sampling_probability']])
            request.update(stage='sm_decay_validation',cohort_id=config['cohort_id'],
                application_timeout_seconds=14400,reservation_bytes=8*1048576,
                argv=['python3','@TOOLS@/scripts/run_shift_sm_decay_pilot.py','request.json'],
                case_id=name,arm=arm,event_id=row['event_id'],native_weight=row['native_weight'],
                original_source=result['source'],original_index=row['original_index'],
                target_position_cm=row['target_position_cm'],
                parent_barcodes=row['proposal']['parent_barcodes'],
                first_depth_one_distances_cm=row['proposal']['first_depth_one_distances_cm'],
                decay_seed=config['decay_seed_base']+index,detector_job=config['detector_job_base']+index,
                detector_source=descriptor,source_inclusion_probability=row['sampling_probability'],
                conditional_original_choice=False,validation_draw_sha256=draw_sha,
                publication_directory=config['publication_directory']+'/'+name)
            directory=worker(name,request,18000)
            cases.append(dict(case_id=name,path=str(directory/'request.json'),sha256=sha(directory/'request.json')))
            nodes.extend(['JOB '+name+' '+str(directory/'worker.sub'),
                          'PARENT CENSUS GEN_ordinary GEN_mixture CHILD '+name])
            if not qualified and row['region']=='control':
                qualification=copy.deepcopy(request)
                qualification.update(stage='sm_decay_GEN_qualification',application_timeout_seconds=2400,
                    return_files=['gen_audit.json','preflight_cfg.json'],
                    argv=request['argv']+['--prepare-only'])
                qualification_dir=worker('GEN_'+arm,qualification,3600)
                nodes.append('JOB GEN_'+arm+' '+str(qualification_dir/'worker.sub'))
        if row['region']=='control':
            qualified=True
    if not qualified:
        raise ValueError('The independent validation needs random-control GEN qualifications')
    plan=dict(schema='shift-independent-decay-validation-v1',independent_originals=len(selected),
        original_population_events=len(census),conditional_original_choice=False,cases=cases,
        sampling_design='All GEN parent-tail opportunities and unknowns, plus fixed-size 10% random controls; exact cumulative inclusion in native W/p',
        original_draw=dict(path=str(draw_path),sha256=draw_sha),source_reference=config['reference_result'],
        frozen=frozen,production_policy_qualified=False,new_GEN_exposure=0)
    save(output/'plan.json',plan)
    collect=dict(schema='shift-stream-payload-v1',environment='cmssw',stage='sm_decay_collection',
        cohort_id=config['cohort_id'],application_timeout_seconds=14400,result_path='report.json',
        return_files=[],failure_return_files=['ordinary_merge.log','mixture_merge.log',
            'ordinary_histogram.log','mixture_histogram.log'],status_fields={},reservation_bytes=256*1048576,
        argv=['python3','@TOOLS@/scripts/run_shift_sm_decay_collection.py','request.json'],
        plan_path=str(output/'plan.json'),plan_sha256=sha(output/'plan.json'),
        histogram_runtime=config['histogram_runtime'],publication_directory=config['publication_directory']+'/collected')
    collect_dir=worker('collection',collect,18000)
    nodes.extend(['JOB COLLECT '+str(collect_dir/'worker.sub'),
        'PARENT '+' '.join(c['case_id'] for c in cases)+' CHILD COLLECT'])
    (output/'validation.dag').write_text('CONFIG '+str(output/'dagman.config')+'\nNODE_STATUS_FILE '+str(output/'node_status')+' 30 ALWAYS-UPDATE\n'+'\n'.join(nodes)+'\n')
    (output/'dagman.config').write_text('DAGMAN_MAX_JOBS_SUBMITTED = 1000\nDAGMAN_MAX_JOBS_IDLE = 1000\nDAGMAN_MAX_SUBMITS_PER_INTERVAL = 100\n')
    return plan


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();prepare(json.loads(args.configuration.read_text()),args.output)
