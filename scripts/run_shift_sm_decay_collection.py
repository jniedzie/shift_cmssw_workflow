#!/usr/bin/env python3
# Code style: AI-code. AI-generated collection of paired conditional decay tests.
"""Check real weighted Nano, standard histograms, and original-group statistics."""
import argparse
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time

from run_shift_sm_postprocessing import (digest, download, hadd_command, transfer_env,
                                        validate_dimuon_histograms)
from run_shift_weighted_gen import publish
from shift_event_weights import validate_proposal_rows, WEIGHT_POLICY
from shift_sm_response_labels import read_nano


def statistics(rows,category,tail=None):
    values=[];raw=0
    for row in rows:
        label=row['labels'];count=(label['pair_counts'][category] if tail is None
                                 else label['tail_counts'][category][tail])
        raw+=count;values.append(row['authority']['proposal_gen_weight']*count)
    total=math.fsum(values);second=math.fsum(v*v for v in values)
    return dict(raw_pairs=raw,positive_originals=sum(v!=0 for v in values),sumw=total,
                grouped_second_moment=second,effective_originals=total*total/second if second else 0.,
                zero_observed_is_not_zero_rate=raw==0)


def natural_sampling_comparison(rows,ordinary_rows,category,tail=None,feature_seconds=0.):
    """Estimate natural-sampling second moments from the known decay density.

    For a whole-original pair count K, E_q[(f/q)(W K)^2] is the
    ordinary-decay second moment. The observed admitted estimator therefore
    contributes (f/q)(W K)^2/p, whereas its importance counterpart contributes
    ((f/q)W K/p)^2. This is an importance-based estimate, not observed ordinary
    tail statistics or a confidence interval. Fixed-size sampling covariance
    and finite-source uncertainty still need separate treatment.
    """
    ordinary={tuple(r['labels']['event_id']):r for r in ordinary_rows}
    identities={tuple(r['labels']['event_id']) for r in rows}
    if (len(ordinary)!=len(ordinary_rows) or len(identities)!=len(rows) or set(ordinary)!=identities):
        raise ValueError('Precision comparison requires the same distinct original events')
    if not math.isfinite(feature_seconds) or feature_seconds<0:
        raise ValueError('Feature cost must be finite and nonnegative')
    natural=[];importance=[];cost=[]
    for row in rows:
        label=row['labels'];count=(label['pair_counts'][category] if tail is None
                                 else label['tail_counts'][category][tail])
        authority=row['authority'];probability=authority['sampling_probability']
        natural.append((authority['native_weight']*count)**2*authority['proposal_correction']/probability)
        importance.append((authority['proposal_gen_weight']*count)**2)
        baseline=ordinary[tuple(label['event_id'])]
        if (baseline['authority']['sampling_probability']!=probability
                or baseline['authority']['native_weight']!=authority['native_weight']):
            raise ValueError('Paired source weights or inclusion probabilities differ')
        cost.append(baseline['payload_seconds']/probability)
    first=math.fsum(natural);second=math.fsum(importance)
    baseline_cost=math.fsum(cost);candidate_cost=math.fsum(r['payload_seconds'] for r in rows)+feature_seconds
    ratio=first/second if second else None
    gain=ratio*baseline_cost/candidate_cost if ratio is not None and candidate_cost else None
    return dict(natural_decay_second_moment_estimate=first,importance_second_moment=second,
        second_moment_ratio=ratio,ordinary_full_census_wall_seconds_estimate=baseline_cost,
        importance_validation_wall_seconds=candidate_cost,
        second_moment_per_wall_time_gain_estimate=gain,
        baseline_estimated_with_exact_likelihood_not_observed_tail=True,
        fixed_size_control_covariance_not_included=True,confidence_interval=None,
        production_gain_qualified=False)


def cohort_variance(rows,draw,full_source_trials,category,tail=None):
    """Include the fixed-size control covariance and unsaved source slots.

    Conditional on a complete counterfactual vector Y=W(f/q)K, the control
    sample has the usual without-replacement variance V_s. With independent
    source slots, (N*S2_hat-T_hat^2+N*V_s_hat)/(N-1) estimates the total
    source/decay/transport/sampling variance. Here S2_hat=sum(Y^2/p), not
    sum((Y/p)^2). Unsaved source slots have zero contribution, but still
    belong to N. Low-count standard errors are not confidence intervals.
    """
    if type(full_source_trials) is not int or full_source_trials<max(2,len(draw)):
        raise ValueError('Full original source denominator is missing or too small')
    population={tuple(r['event_id']):r for r in draw}
    selected={identity for identity,row in population.items() if row['selected']}
    observed={tuple(r['labels']['event_id']):r for r in rows}
    if (len(population)!=len(draw) or len(observed)!=len(rows) or set(observed)!=selected):
        raise ValueError('Variance needs every selected original exactly once; missing is not zero')
    values={};total=[];second=[];natural=[]
    for identity,row in observed.items():
        authority=row['authority'];reference=population[identity];p=reference['sampling_probability']
        if (authority['sampling_probability']!=p
                or authority['native_weight']!=reference['native_weight']):
            raise ValueError('Variance authority differs from the frozen original draw')
        label=row['labels'];count=(label['pair_counts'][category] if tail is None
                                 else label['tail_counts'][category][tail])
        w=authority['native_weight'];r=authority['proposal_correction'];y=w*r*count
        if not math.isclose(authority['proposal_gen_weight']*count,y/p,rel_tol=2.e-12,abs_tol=0.):
            raise ValueError('Variance received a duplicated or missing weight correction')
        values[identity]=y;total.append(y/p);second.append(y*y/p)
        natural.append((w*count)**2*r/p)
    controls=[identity for identity,row in population.items() if row['region']=='control']
    sampled=[identity for identity in controls if population[identity]['selected']]
    n=len(sampled);size=len(controls);design=0.
    if size:
        p=n/size
        if not p or any(population[i]['sampling_probability']!=p for i in controls):
            raise ValueError('Control probabilities do not match the fixed-size draw')
        if n<size and n<2:
            return dict(complete=False,uncertainty_unknown=True,
                        reason='Fewer than two controls cannot estimate without-replacement variance')
        if n<size:
            mean=math.fsum(values[i] for i in sampled)/n
            sample_variance=math.fsum((values[i]-mean)**2 for i in sampled)/(n-1)
            design=size*size*(1.-p)*sample_variance/n
    estimate=math.fsum(total);nsource=full_source_trials
    variance=(nsource*math.fsum(second)-estimate*estimate+nsource*design)/(nsource-1)
    natural_variance=math.fsum(natural)-estimate*estimate/nsource+variance/nsource
    return dict(complete=True,full_source_trials=nsource,saved_originals=len(draw),
        reconstructed_originals=len(rows),sumw=estimate,
        fixed_size_control_variance_estimate=design,total_variance_estimate=variance,
        standard_error=math.sqrt(variance) if variance>=0 else None,
        uncertainty_unknown=variance<0,
        natural_sampling_variance_estimate=natural_variance,
        natural_baseline_is_likelihood_estimate=True,source_slots_assumed_independent=True,
        zero_observed_is_not_zero_rate=not any(values.values()),confidence_interval=None,
        low_count_standard_error_is_not_a_confidence_bound=True)


def histogram_arm(arm,rows,payload,conditional=True):
    import ROOT
    from shift_nano_integrity import compare_all_branches
    annotations=combine_annotation_metadata(rows)
    merged=Path(arm+'_nano.root')
    with Path(arm+'_merge.log').open('w') as log:
        subprocess.run(hadd_command(merged,[r['local'] for r in rows],25),
                       check=True,stdout=log,stderr=log,timeout=900)
    integrity=compare_all_branches([r['local'] for r in rows],merged)
    env=transfer_env();env.update(PYTHONHOME=str(payload/'runtime'),ROOTSYS=str(payload/'runtime'),
        PYTHONPATH=str(payload/'configs')+':'+str(payload/'bin'),
        LD_LIBRARY_PATH=str(payload/'runtime/lib64')+':'+str(payload/'runtime/lib64/root'),
        PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
    config=Path(arm+'_histogram_config.py')
    config.write_text("# Code style: AI-code. Frozen diagnostic weight overlay.\nfrom shift_histogrammer_config import *\nenableBdtDiagnostics = False\nweightsBranchName = 'shiftProposalGenWeight'\n")
    output=Path(arm+'_histograms.root')
    with Path(arm+'_histogram.log').open('w') as log:
        subprocess.run([str(payload/'bin/shift_histogrammer'),'--config',str(config),
            '--input_path',str(merged),'--output_hists_path',str(output)],env=env,
            check=True,stdout=log,stderr=log,timeout=1800)
    file=ROOT.TFile.Open(str(output),'UPDATE')
    if not file or file.IsZombie():raise ValueError('Standard histogrammer failed')
    try:
        validate_dimuon_histograms(file)
        event=file.Get('event/Event_nShiftMuon');expected=math.fsum(r['authority']['proposal_gen_weight'] for r in rows)
        actual=math.fsum(event.GetBinContent(i) for i in range(event.GetNcells()))
        if not math.isclose(actual,expected,rel_tol=1.e-9,abs_tol=1.e-300):
            raise ValueError('Final decay correction did not enter the event histogram exactly once')
        if file.Get('rawEventsCutFlow').GetBinContent(1)!=len(rows):
            raise ValueError('Raw processed-original count changed')
        for category,key in [('inclusive',''),('both_both','Both-Both'),('good','Good')]:
            for variable in ['mass','pt','logMass']:
                hist=file.Get('dimuon/ShiftDimuonVertex'+key+'_'+variable)
                bins=defaultdict(list)
                from shift_sm_response_labels import pair_categories
                for row in rows:
                    for pair in row['labels']['retained_pairs']:
                        if not pair_categories(pair)[category]:continue
                        value=pair['mass'] if variable=='logMass' else pair[variable]
                        if value is None or variable=='logMass' and value<=0:continue
                        bins[hist.FindBin(math.log10(value) if variable=='logMass' else value)].append(
                            row['authority']['proposal_gen_weight'])
                for number in range(hist.GetNcells()):
                    reference=math.fsum(bins[number])
                    if not math.isclose(hist.GetBinContent(number),reference,rel_tol=1.e-8,abs_tol=1.e-300):
                        raise ValueError('Standard histogram differs from actual weighted pairs: '+key+variable)
                    squared=math.fsum(w*w for w in bins[number])
                    if not math.isclose(hist.GetBinError(number)**2,squared,rel_tol=1.e-8,abs_tol=1.e-300):
                        raise ValueError('Standard histogram lost weighted sumw2: '+key+variable)
        metadata=dict(schema='shift-conditional-decay-pilot-histogram-v1',arm=arm,
            diagnostic_only=True,physics_normalization_ready=False,histogram_weight=WEIGHT_POLICY,
            independent_originals=len(rows),conditional_original_choice=conditional,
            source_exposures=annotations['source_exposures'],
            source_exposure_counted_once=True,repeated_arm_exposures_must_not_be_added=True,
            pair_errors_need_original_grouping=True)
        ROOT.TObjString(json.dumps(metadata,sort_keys=True)).Write('ShiftDecayPilot')
    finally:file.Close()
    return dict(merged=merged,histograms=output,integrity=integrity,
                frozen_binary_sha256=digest(payload/'bin/shift_histogrammer'))


def combine_annotation_metadata(rows):
    """Give histogram working copies one complete arm record before hadd.

    Per-original Nano files remain immutable on EOS. Check every typed value
    and opaque EDM basket before/after replacing only this known string key.
    The shared strict merge checker can then verify the grouped replacement.
    """
    import ROOT
    from shift_nano_integrity import semantic_inventory
    key='ShiftDecayProposalWeights';inventories=[];members=[];identities=set();exposures={}
    for row in rows:
        inventory=semantic_inventory([row['local']]);annotation=json.loads(inventory[key]['value'])
        original_ids=[tuple(v) for v in annotation['original_event_ids']]
        if (len(original_ids)!=1 or original_ids[0] in identities
                or annotation['decay_correction_application_count']!=1
                or annotation['weight_policy']!=WEIGHT_POLICY):
            raise ValueError('Repeated or incompatible original decay annotation')
        identities.update(original_ids);inventories.append(inventory)
        members.append(dict(upstream_Nano_sha256=digest(row['local']),annotation=annotation))
        for exposure in annotation.get('source_exposures',[]):
            source=(exposure['source_id'],exposure['original_source_sha256'])
            if source in exposures and exposures[source]!=exposure:
                raise ValueError('Original source exposure differs between annotations')
            exposures[source]=exposure
    metadata=dict(schema='shift-decay-proposal-arm-annotations-v1',
        weight_policy=WEIGHT_POLICY,independent_originals=len(identities),members=members,
        source_exposures=list(exposures.values()),
        one_draw_per_original=True,diagnostic_only=True,normalization_ready=False,
        source_exposure_must_be_deduplicated_across_members=True)
    combined=json.dumps(metadata,sort_keys=True)
    for row,before in zip(rows,inventories):
        file=ROOT.TFile.Open(str(row['local']),'UPDATE')
        if not file or file.IsZombie():raise ValueError('Cannot open histogram working copy')
        try:ROOT.TObjString(combined).Write(key,ROOT.TObject.kOverwrite)
        finally:file.Close()
        after=semantic_inventory([row['local']]);expected=dict(before)
        expected[key]=dict(classname='TObjString',value=combined)
        if after!=expected:raise ValueError('Combining annotations changed Nano values or native metadata')
    return metadata


def wait_for_case_results(plan,seconds):
    """Bound a cross-DAG comparison wait; never resubmit or hide a failure."""
    if isinstance(seconds,bool) or not math.isfinite(seconds) or seconds<0:
        raise ValueError('Invalid parent-result wait bound')
    deadline=time.monotonic()+seconds
    while True:
        missing=[]
        for case in plan['cases']:
            path=Path(case['path']).with_name('result.json')
            if not path.is_file():
                missing.append(case['case_id'])
            else:
                result=json.loads(path.read_text())
                if 'exit_code' in result and (result['exit_code']!=0 or not result['complete']):
                    raise ValueError('Parent response failed, not a physics rejection: '+case['case_id'])
        if not missing:
            return
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise TimeoutError('Missing paired parent results: '+','.join(missing))
        time.sleep(min(30.,remaining))


def run(path):
    request=json.loads(Path(path).read_text());plan_path=Path(request['plan_path'])
    if digest(plan_path)!=request['plan_sha256']:raise ValueError('Pilot plan changed')
    plan=json.loads(plan_path.read_text());started=time.monotonic();report=dict(complete=False,
        benchmark_plan_sha256=request['plan_sha256'],complete_frozen_draw_collected=False,
        diagnostic_only=True,physics_normalization_ready=False,production_policy_qualified=False)
    Path('report.json').write_text(json.dumps(report)+'\n')
    try:
        groups=plan.get('comparison_groups',['ordinary','mixture'])
        if len(groups)!=2 or len(set(groups))!=2 or any(not re.fullmatch('[A-Za-z0-9_-]+',v) for v in groups):
            raise ValueError('Require exactly two distinct safe comparison groups')
        wait_for_case_results(plan,request.get('wait_for_case_results_seconds',0))
        grouped=defaultdict(list)
        for case in plan['cases']:
            case_request=Path(case['path'])
            if digest(case_request)!=case['sha256']:raise ValueError('Case authority changed')
            frozen=json.loads(case_request.read_text());result_path=case_request.with_name('result.json')
            group=case.get('comparison_group',frozen['arm'])
            if group not in groups or group!=frozen.get('comparison_group',frozen['arm']):
                raise ValueError('Comparison group differs from its frozen request')
            result=json.loads(result_path.read_text())
            if (not result['complete'] or result['exit_code']!=0
                    or result['request_sha256']!=case['sha256']
                    or result['original_event_id']!=frozen['event_id']):
                raise ValueError('Missing/failed response is not a physics rejection: '+case['case_id'])
            authority=result['weight_authority'];validate_proposal_rows([authority])
            probability=frozen.get('source_inclusion_probability',1.)
            if authority['sampling_probability']!=probability:
                raise ValueError('Original draw probability differs from its frozen request')
            pin=next(v for v in result['publications'] if v['name']=='nano.root')
            local=Path(case['case_id']+'.root');download(pin['path'],local,pin['sha256'])
            expected=dict(event_id=frozen['event_id'],gen_sha256=frozen['original_source']['gen_sha256'],
                          native_weight=frozen['native_weight'],sampling_probability=probability)
            labels=read_nano(local,[expected],pin['sha256'])[0]
            if labels!=result['labels'][0]:raise ValueError('Independent Nano response changed')
            import ROOT
            file=ROOT.TFile.Open(str(local));tree=file.Get('Events');tree.GetEntry(0)
            refs=validate_proposal_rows([authority])[0]
            for field in ['shiftDecayProposalDensity','shiftDecayProposalWeight','shiftProposalGenWeight']:
                if (tree.GetLeaf(field).GetTypeName()!='Double_t' or
                        not math.isclose(float(getattr(tree,field)),refs[field],rel_tol=2.e-12,abs_tol=0)):
                    raise ValueError('Persisted decay weight changed: '+field)
            file.Close()
            grouped[group].append(dict(local=local,labels=labels,authority=authority,
                sim_seconds=result['detector']['stages']['1']['seconds'],
                payload_seconds=result['detector']['wall_seconds'],case_id=case['case_id'],
                GEN_pairs=result['GEN_audit']['selected_original_pion_direct_muon_pairs']))
        if set(grouped)!=set(groups) or any(len(v)!=plan['independent_originals'] for v in grouped.values()):
            raise ValueError('Paired original population is incomplete')
        ids=[{tuple(r['labels']['event_id']) for r in grouped[arm]} for arm in groups]
        if ids[0]!=ids[1] or len(ids[0])!=plan['independent_originals']:raise ValueError('Arms do not share exact originals')
        capsule=Path('histogram_runtime.tar.gz');pin=request['histogram_runtime']
        download(pin['path'],capsule,pin['sha256'])
        with tarfile.open(capsule) as archive:
            if any(Path(m.name).is_absolute() or '..' in Path(m.name).parts for m in archive):raise ValueError('Unsafe histogram capsule')
            archive.extractall(Path.cwd())
        capsule.unlink();payload=Path('payload').resolve()
        for name,record in json.loads((payload/'manifest.json').read_text()).items():
            if digest(payload/name)!=record['sha256']:raise ValueError('Frozen histogram payload changed')
        summary={};outputs=[]
        for arm,rows in grouped.items():
            hist=histogram_arm(arm,rows,payload,plan.get('conditional_original_choice',True))
            summary[arm]=dict(events=len(rows),SIM_seconds=math.fsum(r['sim_seconds'] for r in rows),
                payload_seconds=math.fsum(r['payload_seconds'] for r in rows),
                categories={category:dict(all=statistics(rows,category),
                    mass_gt20=statistics(rows,category,'mass_gt_20'),pt_gt10=statistics(rows,category,'pt_gt_10'))
                    for category in ['inclusive','both_both','both_both_good']},
                GEN_direct_parent_dimuon_mass_gt20_originals=sum(any(p['mass_GeV']>20 for p in r['GEN_pairs']) for r in rows),
                frozen_binary_sha256=hist['frozen_binary_sha256'],full_branch_merge_verified=True)
            outputs.extend([(hist['merged'],arm+'_nano.root'),(hist['histograms'],arm+'_histograms.root')])
        if sum(f.stat().st_size for f,_ in outputs)>request['reservation_bytes']-1048576:raise ValueError('Collection exceeds reservation')
        report.update(complete=True,complete_frozen_draw_collected=True,arms=summary,
            conditional_original_choice=plan.get('conditional_original_choice',True),
            original_population_events=plan.get('original_population_events'),
            sampling_design=plan.get('sampling_design'),
            publications=publish(outputs,request['publication_directory']),wall_seconds=time.monotonic()-started,
            note='Frozen paired validation; ordinary and biased copies are not added exposure. Native W/p and full-mixture f/q enter once. Zero observed controls do not establish zero physical rate. Tail gain needs independent-original closure and adequate grouped effective statistics.')
        Path('report.json').write_text(json.dumps(report,indent=2)+'\n')
    except Exception as error:
        report.update(error=repr(error),wall_seconds=time.monotonic()-started)
        Path('report.json').write_text(json.dumps(report,indent=2)+'\n');raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('request',type=Path)
    run(parser.parse_args().request)
