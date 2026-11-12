#!/usr/bin/env python3
# Code style: AI-code. AI-generated cumulative DP histogram publisher.
"""Pool completed compatible cohorts and publish a qualified signal catalog.

This process never generates events or submits detector work. It keeps each
original GEN denominator once, pools repeated estimates within a mechanism,
and adds the independently normalized SensCalc and MSHT20 DY components.
"""
import argparse
from datetime import datetime, timezone
import errno
import fcntl
import glob
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    while True:
        try:
            value=hashlib.sha256()
            with Path(path).open('rb') as source:
                for data in iter(lambda:source.read(1048576),b''):
                    value.update(data)
            return value.hexdigest()
        except OSError as error:
            if error.errno not in (errno.ETIMEDOUT,errno.ESTALE,errno.EIO):raise
            time.sleep(30)


def read(path):
    while True:
        try:return json.loads(Path(path).read_text())
        except OSError as error:
            if error.errno not in (errno.ETIMEDOUT,errno.ESTALE,errno.EIO):raise
            time.sleep(30)


def save(path,value):
    """Retry the same snapshot during an AFS outage, before another action."""
    path=Path(path)
    body=json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n'
    while True:
        try:
            temporary=path.with_suffix(path.suffix+'.next')
            temporary.write_text(body);temporary.replace(path)
            return
        except OSError as error:
            if error.errno not in (errno.ETIMEDOUT,errno.ESTALE,errno.EIO):raise
            time.sleep(10)


def pin(path):
    path=Path(path).resolve()
    return dict(path=str(path),sha256=sha(path))


def checked(record):
    if sha(record['path'])!=record['sha256']:
        raise ValueError('Pinned publisher input changed: '+record['path'])
    return Path(record['path'])


def configuration(path):
    config=read(path)
    if config.get('schema')!='shift-dp-cumulative-publisher-v1':
        raise ValueError('Unsupported DP publication configuration')
    if not config.get('output_base','').startswith('/eos/'):
        raise ValueError('Publication requires an explicit approved EOS base')
    from shift_eos_storage import eos_endpoint
    eos_endpoint(config['output_base'])
    for record in config['dependencies']:checked(record)
    checked(config['conditions'])
    checked(config['storage_registration'])
    for name in ('discovery_globs','prior_sources','retained_signals'):
        if not isinstance(config.get(name),list):raise ValueError('Require explicit '+name)
    for pattern in config['discovery_globs']:
        if not Path(pattern).is_absolute() or '**' in pattern or '..' in Path(pattern).parts:
            raise ValueError('Use bounded absolute cohort discovery globs')
    for path in config.get('analysis_import_paths',[]):
        sys.path.insert(0,path)
    return config


def authority(directory,point,conditions):
    directory=Path(directory)
    marker=directory/'completed_points'/(point+'.json')
    return dict(point=point,plan_path=str(directory/'plan.json'),
        plan_sha256=sha(directory/'plan.json'),seal_sha256=sha(directory/'seal.json'),
        completed_point_path=str(marker),completed_point_sha256=sha(marker),
        conditions_sha256=conditions)


def discovered_sources(config):
    sources=list(config['prior_sources'])
    pending=[]
    for pattern in config['discovery_globs']:
        for directory in sorted(glob.glob(pattern)):
            directory=Path(directory)
            if not (directory/'plan.json').is_file() or not (directory/'seal.json').is_file():continue
            plan=read(directory/'plan.json')
            if plan.get('detector_runtime',{}).get('runtime_sha256')!=config['detector_runtime_sha256']:
                pending.append(dict(directory=str(directory),quarantined_source=True,
                    reason='Discovered cohort uses an unqualified detector capsule'))
                continue
            settings=read(directory/'main_histogrammer.json')
            if settings.get('runtime',{}).get('sha256') not in config['histogram_runtime_sha256']:
                pending.append(dict(directory=str(directory),quarantined_source=True,
                    reason='Discovered cohort uses an unqualified histogram runtime'))
                continue
            for row in plan['points']:
                point=row['point'];marker=directory/'completed_points'/(point+'.json')
                if not marker.is_file() or read(marker).get('complete') is not True:
                    pending.append(dict(point=point,directory=str(directory)))
                    continue
                sources.append(authority(directory,point,config['conditions']['sha256']))
    result={}
    seen=set()
    for source in sources:
        key=(source['plan_path'],source['point'])
        if key in seen:continue
        seen.add(key);result.setdefault(source['point'],[]).append(source)
    return result,pending


def input_signature(sources):
    fields=('plan_sha256','seal_sha256','completed_point_sha256')
    rows=sorted(tuple(source[field] for field in fields) for source in sources)
    return hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()


def validated_epsilon_domain(rate):
    """Admit only the source's exact qualified prescription and epsilon range.

    Source loading already verifies its original helper and rate record. The
    publisher additionally checks the common authority before extending a scan.
    """
    epsilon = rate['epsilon']
    if not math.isfinite(epsilon) or epsilon <= 0:
        raise ValueError('Invalid signal epsilon')
    if rate['mechanism'] == 'pure-on-shell-drell-yan':
        from dark_photon_dy_prescription import prescription_metadata
        metadata = rate.get('dy_prescription', {})
        name = metadata.get('name')
        maximum = .1 if name == 'msht20-nnlo-v2' else .001
        if epsilon > maximum:
            raise ValueError('DY epsilon exceeds qualified authority')
        expected = prescription_metadata(name, rate['mass_gev'], epsilon)
        if expected is None:
            raise ValueError('Unapproved DY prescription')
        # Each source retains its own helper hash; helper revisions may add
        # another prescription without changing the older table or physics.
        actual = json.loads(json.dumps(metadata))
        for record in (actual, expected):
            record['authority'].pop('helper_sha256', None)
        if actual != expected:
            raise ValueError('DY rate differs from the pinned publication authority')
    if rate['mass_gev'] < 12.:
        from run_dark_photon_spectral_gen import photon_benchmark_epsilon
        experimental = rate.get('experimental_photon_benchmark') is True
        expected = photon_benchmark_epsilon(epsilon, experimental)
        if experimental and rate.get('photon_benchmark_approximation') != expected:
            raise ValueError('Changed photon-benchmark approximation')
        if epsilon > .01 and not experimental:
            raise ValueError('Extended photon benchmark must be explicit')
        if rate.get('physical_width_authority', {}).get('neutral_current_width_included') is not False:
            raise ValueError('Publication below12 GeV requires the pinned photon-like width benchmark')


def components_for(populations):
    """Only the approved DY prescription may contribute to a new DY catalog."""
    import aggregate_dark_photon_samples as aggregate
    groups={}
    for population in populations:
        rate=population['rate'];mechanism=rate['mechanism']
        if mechanism=='pure-on-shell-drell-yan':
            if 'dy_prescription' not in rate:
                raise ValueError('Legacy DY is not an MSHT20 response')
            aggregate.validated_dy_normalization(rate)
            validated_epsilon_domain(rate)
        elif mechanism!='senscalc-meson-fragmentation-bremsstrahlung':
            raise ValueError('Unapproved signal mechanism: '+mechanism)
        if mechanism=='senscalc-meson-fragmentation-bremsstrahlung':
            validated_epsilon_domain(rate)
        groups.setdefault(mechanism,[]).append(population)
    groups={name:aggregate.unique_populations(rows) for name,rows in groups.items()}
    first=next(iter(groups.values()))[0]['rate']
    required=({'senscalc-meson-fragmentation-bremsstrahlung','pure-on-shell-drell-yan'}
        if 1.5<=first['mass_gev']<2 else
        {'pure-on-shell-drell-yan'} if first['mass_gev']>=2 else
        {'senscalc-meson-fragmentation-bremsstrahlung'})
    if set(groups)!=required:return None
    return groups


def ready_entry(point,report,rate,signature,originals,parameters,qualification):
    from shift_eos_storage import xrootd_url
    histogram=report['published']['merged_histogram']
    return dict(name=point,point=point,mass_gev=parameters['mass_gev'],
        mean_lab_flight_m=parameters['mean_lab_flight_m'],epsilon=parameters['epsilon'],
        prompt=point.endswith('_prompt'),
        proper_ctau_mm=parameters['proper_ctau_mm'],histogram_path=histogram['path'],
        histogram_sha256=histogram['sha256'],histogram_bytes=histogram['bytes'],
        histogram_url=xrootd_url(histogram['path']),rate_path=report['cross_sections']['path'],
        rate_url=xrootd_url(report['cross_sections']['path']),
        rate_sha256=report['cross_sections']['sha256'],qualification=qualification,
        model='SensCalc-compatible meson/FR plus MSHT20 NNLO DY',
        full_benchmark_components=True,cumulative_compatible_inputs_included=True,input_signature=signature,
        original_GEN_sha256=sorted(originals),full_gen_sumw=reference_denominator(rate),
        cross_section_pb=rate['physical_selected_cross_sections_pb'][0]['value_pb'],
        passing_statistics=report['passing_statistics'],updated_utc=now())


def keep_all_originals(previous,originals):
    if previous and not set(previous.get('original_GEN_sha256',[]))<=set(originals):
        raise ValueError('New cumulative publication would drop compatible original GEN')


def safe_publish(path,directory):
    from export_dark_photon_cross_sections import publish_file_rpc
    return publish_file_rpc(path,directory.rstrip('/')+'/'+Path(path).name)


def reference_denominator(rate):
    """The composite reference denominator is not a common collision exposure."""
    if rate.get('schema')=='shift-dark-photon-senscalc-dy-component-rates-v1':
        value=rate['histogram_normalization']['reference_gen_weight_sum']
        if value!=sum(component['full_gen_weight_sum'] for component in rate['components']):
            raise ValueError('Component reference denominator differs from the preserved GEN records')
    else:
        value=rate['full_gen']['sum_weights']
    if not math.isfinite(value) or value<=0:raise ValueError('Invalid histogram reference denominator')
    return value


def histogram_statistics(path):
    import uproot
    with uproot.open(path) as source:
        histogram=source['event/Event_nShiftDimuonVertexBoth-Both']
        values=histogram.values(flow=True)[2:]
        variances=histogram.variances(flow=True)[2:]
        sumw=float(values.sum());sumw2=float(variances.sum())
    return dict(event_sumw=sumw,event_sumw2=sumw2,
        event_neff=sumw*sumw/sumw2 if sumw2 else 0.,raw_passing_events=None,
        original_source_collision_correlations_included=False,
        convention='One event entry with native W/p and component-rate scaling where applicable')


def verify_remote_artifact(item,directory):
    from run_shift_sm_postprocessing import download
    local=Path(directory)/Path(item['path']).name
    download(item['path'],local,item['sha256'])
    if local.stat().st_size!=item['bytes']:raise ValueError('Publication readback size differs')
    return local


def publication_prefix(config,point,signature,attempt):
    campaign=(config['campaign']+'-'+point+'-'+signature[:12]+'-'+
        config['configuration_sha256'][:8]+'-a'+str(attempt))
    return campaign,config['output_base'].rstrip('/')+'/darkphoton_'+point+'/'+campaign


def adopt_completed(config,point,signature,attempt):
    """Adopt a completed interrupted publication instead of replaying it."""
    from run_shift_gen_to_nano import remote_exists
    from run_shift_sm_postprocessing import download
    unused,prefix=publication_prefix(config,point,signature,attempt)
    remote=prefix+'/metadata/publisher/complete.json'
    if not remote_exists(remote):return None
    with tempfile.TemporaryDirectory(prefix='dp-publish-adopt-',dir=config.get('scratch_base','/tmp')) as temporary:
        directory=Path(temporary)
        marker=directory/'marker.json';download(remote,marker)
        result=read(marker)
        if (result.get('complete') is not True or result['configuration_sha256']!=config['configuration_sha256']
                or result['input_signature']!=signature or result['entry']['name']!=point):
            raise ValueError('Interrupted publication marker differs from its exact inputs')
        for index,item in enumerate(result['artifacts']):
            checked_directory=directory/str(index);checked_directory.mkdir()
            verify_remote_artifact(item,checked_directory)
        return result['entry']


def build_publication(config,point,sources,attempt,work,previous=None):
    import aggregate_dark_photon_samples as aggregate
    from run_shift_sm_postprocessing import download
    from shift_sample_paths import histogram_gen_normalization
    from export_dark_photon_cross_sections import publish_file_rpc
    work=Path(work)
    populations=[]
    for source in sources:
        plan=read(checked(dict(path=source['plan_path'],sha256=source['plan_sha256'])))
        row=next(row for row in plan['points'] if row['point']==point)
        model={key:row[key] for key in aggregate.MODEL_FIELDS}
        populations.append(aggregate.load_source(source,model,source['conditions_sha256']))
    groups=components_for(populations)
    if groups is None:return None
    populations=[population for group in groups.values() for population in group]
    originals=[population['parent']['gen_sha256'] for population in populations]
    keep_all_originals(previous,originals)
    retained = config.get('retained_original_GEN_sha256', {}).get(point)
    if retained:
        keep_all_originals(dict(original_GEN_sha256=retained), originals)
    from run_dark_photon_gen_stage import check_eos_margin
    check_eos_margin(dict(eos_quota_space=config['storage_quota']['space'],
        minimum_eos_free_bytes=config['storage_quota']['minimum_free_bytes'],
        minimum_eos_free_files=config['storage_quota']['minimum_free_files']),
        config['maximum_publication_bytes'],config['output_base'])
    first=populations[0]['rate']
    rows=[next(row for row in read(population['source']['plan_path'])['points'] if row['point']==point)
        for population in populations]
    signature=input_signature(sources)
    campaign,prefix=publication_prefix(config,point,signature,attempt)
    flight=rows[0].get('reference_mean_lab_flight_m',rows[0].get('mean_lab_flight_m'))
    if point.endswith('_prompt'):flight=None
    row=dict(point=point,**{key:first[key] for key in aggregate.MODEL_FIELDS},
        events=sum(len(population['selected_event_ids']) for population in populations),
        full_gen_events=sum(population['parent']['events'] for population in populations),
        final_output_base=prefix,target_mean_lab_flight_m=flight)
    parameters=dict(schema='shift-dark-photon-sample-v1',point=point,mass_gev=first['mass_gev'],
        epsilon=first['epsilon'],proper_ctau_mm=first['proper_ctau_mm'],
        sampling_epsilon=first['sampling_epsilon'],selector_kind='mean_lab_flight',
        mean_lab_flight_m=flight,
        each_original_GEN_counted_once=True)
    if point.endswith('_prompt'):parameters['mean_lab_flight_m']=None
    plan=dict(main_histogrammer=True,maximum_publication_bytes=config['maximum_publication_bytes'])
    plan_hash=hashlib.sha256(json.dumps(dict(sources=sources,row=row),sort_keys=True).encode()).hexdigest()
    output=work/'results';output.mkdir();(output/'completed_points').mkdir()
    if len(groups)==1:
        rate=aggregate.combined_rate(populations,point=point,campaign=campaign)
        report=aggregate.execute_main_histograms(plan,row,populations,rate,plan_hash,
            config['configuration_sha256'],work,output,publish_function=safe_publish)
        marker=report['main_histograms']
        normalized_report=dict(published=marker['published'],cross_sections=report['cross_sections'])
        saved=work/'main_histogram_aggregate/ntuple_0.root'
    else:
        components=[]
        for index,(mechanism,group) in enumerate(sorted(groups.items())):
            for number,population in enumerate(group):
                item=population['report']['main_histograms']['published']['merged_histogram']
                local=work/(str(index)+'_'+str(number)+'.root')
                download(item['path'],local,item['sha256'])
                if local.stat().st_size!=item['bytes']:raise ValueError('Source histogram size changed')
                population['local_histogram_path']=str(local)
            components.append(dict(name='dy' if mechanism=='pure-on-shell-drell-yan' else 'senscalc',
                conditions_sha256=config['conditions']['sha256'],populations=group))
        saved=work/'ntuple_0.root'
        combined=aggregate.prepare_senscalc_dy_histograms(components,saved,point=point,campaign=campaign)
        rate=combined['rate']
        histogram=publish_file_rpc(saved,prefix+'/histograms_merged/ntuple_0.root')
        rate_file=work/'cross_sections.json';save(rate_file,rate)
        rate_item=publish_file_rpc(rate_file,prefix+'/cross_sections.json')
        parameters_file=work/'sample_parameters.json';save(parameters_file,parameters)
        publish_file_rpc(parameters_file,prefix+'/sample_parameters.json')
        normalized_report=dict(published=dict(merged_histogram=histogram),cross_sections=rate_item)
    check=work/'remote_readback';check.mkdir()
    local=verify_remote_artifact(normalized_report['published']['merged_histogram'],check)
    actual=histogram_gen_normalization(str(local),'darkphoton_'+point)
    if (actual['initial_weight_sum']!=reference_denominator(rate) or
        not math.isclose(actual['cross_section_pb'],rate['physical_selected_cross_sections_pb'][0]['value_pb'],rel_tol=1e-12)):
        raise ValueError('Ordinary plot consumer changed the cumulative normalization')
    normalized_report['passing_statistics']=histogram_statistics(local)
    qualification_file=work/'qualification.json'
    qualification=dict(schema='shift-dp-cumulative-publication-qualification-v1',
        complete=True,point=point,input_signature=signature,originals=originals,
        ordinary_reader=actual,original_source_records=sources,all_ready_compatible_inputs_included=True,
        mechanism_denominators_retained=True,no_second_filter_or_branching=True,conditions=config['conditions'],
        detector_runtime_sha256=config['detector_runtime_sha256'],new_exposure_from_replays=False,
        passing_statistics=normalized_report['passing_statistics'],raw_MC_distinct_from_lumi=True)
    save(qualification_file,qualification)
    qualification_item=publish_file_rpc(qualification_file,prefix+'/metadata/publisher/qualification.json')
    entry=ready_entry(point,normalized_report,rate,signature,originals,parameters,qualification_item)
    complete_file=work/'complete.json'
    complete=dict(complete=True,configuration_sha256=config['configuration_sha256'],input_signature=signature,
        entry=entry,artifacts=[normalized_report['published']['merged_histogram'],normalized_report['cross_sections'],qualification_item])
    save(complete_file,complete)
    publish_file_rpc(complete_file,prefix+'/metadata/publisher/complete.json')
    return entry


def catalog(config,state,pending):
    entries={entry['name']:entry for entry in config['retained_signals']}
    entries.update(state['published'])
    return dict(schema='shift-dp-qualified-signal-catalog-v1',complete=True,updated_utc=now(),
        campaign=config['campaign'],configuration_sha256=config['configuration_sha256'],
        configuration=dict(path=config['configuration_path'],sha256=config['configuration_sha256']),
        publisher_code=pin(__file__),
        ready_signals=[entries[point] for point in sorted(entries)],pending_signals=pending,
        quarantined_points=state['quarantined'],partial_production=True,
        no_legacy_DY_responses=True,
        all_ready_compatible_increments_included=not state['quarantined'] and
            not any(row.get('status')=='operational_failure' for row in state['attempts'].values()) and
            not any(row.get('quarantined_source') for row in pending),
        plot_campaign=config.get('plot_campaign'),ordinary_campaign_alias_errors=state.get('alias_errors',{}))


def native_eos_path(path):
    """The analysis resolver preserves frontend spelling; native RPC cannot."""
    from shift_storage_paths import resolve_storage_path
    path=resolve_storage_path(path)
    prefix='/eos/home-j/jniedzie'
    if path==prefix or path.startswith(prefix+'/'):
        path='/eos/user/j/jniedzie'+path[len(prefix):]
    return path


def publish_plot_aliases(config,state,result):
    """Expose a single named campaign; preserve the user's existing latest."""
    if not config.get('plot_aliases'):return
    from shift_eos_storage import eos_endpoint
    from run_dark_photon_gen_stage import command
    from watch_dark_photon_production import EosRpc
    for entry in result['ready_signals']:
        point=entry['name']
        if point not in config['plot_aliases']:continue
        alias=native_eos_path(config['plot_aliases'][point])
        target=native_eos_path(str(Path(entry['histogram_path']).parent.parent))
        if state.get('aliases',{}).get(point,{}).get('target')==target:continue
        try:
            eos_endpoint(target)
            host=eos_endpoint(alias)
            command(['/usr/bin/xrdfs',host,'mkdir','-p',str(Path(alias).parent)])
            with tempfile.TemporaryDirectory(prefix='dp-alias-') as temporary:
                transport=EosRpc(host,Path(temporary),'/usr/bin/eos')
                previous=transport.metadata(alias)
                old=previous.get('target') if previous else None
                if previous and previous.get('status')!='symlink':
                    raise ValueError('Refuse to replace a real campaign directory')
                known=state.get('aliases',{}).get(point,{}).get('target')
                if old is not None and old not in (known,target):
                    raise ValueError('Campaign alias was changed outside this publisher')
                argv=['/usr/bin/eos',host,'file','symlink']
                if old is not None:argv.append('-f')
                command(argv+[alias,target])
                if transport.metadata(alias).get('target')!=target:
                    raise ValueError('Named campaign alias did not confirm its target')
            state.setdefault('aliases',{})[point]=dict(path=alias,target=target,utc=now())
            state.get('alias_errors',{}).pop(point,None)
        except Exception as error:
            state.setdefault('alias_errors',{})[point]=dict(error=repr(error),utc=now())
        save(config['state_path'],state)


def poll(config,state):
    sources,pending=discovered_sources(config)
    for point,inputs in sorted(sources.items()):
        signature=input_signature(inputs)
        previous=state['published'].get(point)
        if previous and previous['input_signature']==signature:continue
        if state.get('pending_components',{}).get(point)==signature:
            pending.append(dict(point=point,reason='Missing required SensCalc or DY component'))
            continue
        if state['quarantined'].get(point,{}).get('input_signature')==signature:continue
        prior=state['attempts'].get(point)
        if prior and prior['input_signature']==signature:
            try:
                adopted=adopt_completed(config,point,signature,prior['number'])
                if adopted is not None:
                    keep_all_originals(previous,adopted['original_GEN_sha256'])
                    state['published'][point]=adopted
                    prior.update(status='complete',adopted_after_interruption_utc=now())
                    save(config['state_path'],state)
                    continue
            except (subprocess.CalledProcessError,subprocess.TimeoutExpired,ConnectionError,RuntimeError) as error:
                prior.update(status='operational_failure',error=repr(error),utc=now())
                save(config['state_path'],state);continue
            except Exception as error:
                state['quarantined'][point]=dict(input_signature=signature,error=repr(error),utc=now())
                save(config['state_path'],state);continue
        number=prior['number']+1 if prior and prior['input_signature']==signature else 0
        if number>1:
            state['quarantined'][point]=dict(input_signature=signature,error='Bounded publication attempts exhausted',utc=now())
            save(config['state_path'],state);continue
        if prior and prior['input_signature']==signature:
            state.setdefault('preserved_incomplete_attempts',[]).append(dict(point=point,**prior))
        state['attempts'][point]=dict(number=number,input_signature=signature,status='started',utc=now())
        save(config['state_path'],state)
        try:
            with tempfile.TemporaryDirectory(prefix='dp-publish-',dir=config.get('scratch_base','/tmp')) as temporary:
                entry=build_publication(config,point,inputs,number,Path(temporary),previous)
            if entry is None:
                pending.append(dict(point=point,reason='Missing required SensCalc or DY component'))
                state['attempts'].pop(point,None)
                state.setdefault('pending_components',{})[point]=signature
            else:
                keep_all_originals(previous,entry['original_GEN_sha256'])
                state['published'][point]=entry
                state['attempts'][point]['status']='complete'
                state.get('pending_components',{}).pop(point,None)
                if point in state['quarantined']:
                    state.setdefault('resolved_quarantines',[]).append(dict(point=point,**state['quarantined'].pop(point)))
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired,ConnectionError) as error:
            state['attempts'][point].update(status='operational_failure',error=repr(error),utc=now())
        except Exception as error:
            if isinstance(error,RuntimeError) and str(error).startswith(('Unknown EOS sidecar state:','Publication state is unknown:')):
                state['attempts'][point].update(status='operational_failure',error=repr(error),utc=now())
            else:
                state['quarantined'][point]=dict(input_signature=signature,error=repr(error),utc=now())
        save(config['state_path'],state)
    state['last_poll_utc']=now()
    save(config['state_path'],state)
    result=catalog(config,state,pending)
    publish_plot_aliases(config,state,result)
    result['ordinary_campaign_alias_errors']=state.get('alias_errors',{})
    save(config['catalog_path'],result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--config-sha256',required=True)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if sha(args.config)!=args.config_sha256:
        raise ValueError('Frozen publisher configuration changed')
    config=configuration(args.config)
    config['configuration_sha256']=sha(args.config)
    config['configuration_path']=str(args.config.resolve())
    lock_path=Path(config['state_path']).with_suffix('.lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=read(config['state_path']) if Path(config['state_path']).is_file() else dict(published={},attempts={},quarantined={})
        deadline=datetime.fromisoformat(config['deadline_utc']).timestamp()
        while True:
            result=poll(config,state)
            print(json.dumps(dict(utc=now(),ready=len(result['ready_signals']),pending=len(result['pending_signals']),
                quarantined=len(result['quarantined_points']))),flush=True)
            if args.once or time.time()>=deadline:break
            time.sleep(config.get('poll_seconds',120))


if __name__=='__main__':main()
