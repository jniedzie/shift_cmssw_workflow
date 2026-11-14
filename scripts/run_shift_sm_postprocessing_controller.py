#!/usr/bin/env python3
# Code style: AI-code. AI-generated detached rolling SM publication controller.
"""Follow verified SM Nano completions and publish meaningful per-bin updates."""
from collections import defaultdict
import argparse
import fcntl
import errno
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time

from run_shift_sm_postprocessing import (read, save, exposure, selected_rows, digest,
                                        read_response_pin, response_sampling_contract)
from shift_condor_native import query, run_condor, NativeCondorError
from monitor_shift_sm_response_benchmark import wait_for_lock
from run_shift_smart_sm_controller import (submission_journal_enabled, begin_submission_batch,
    submit_journal_task, finish_submission_batch, reconcile_submission_batch)


def descriptor_hashes(source):
    variants = [source, dict(source, gen_path_resolved=True)]
    return [hashlib.sha256((json.dumps(s, indent=2) + ending).encode()).hexdigest()
            for s in variants for ending in ('', '\n')]


def compact_source(source):
    selected = selected_rows(source)
    if source.get('response_sampling') is not None:
        keys = ('event_ids','weights','selected_indices','sampling_probabilities',
                'gen_sha256','normalization_record','response_sampling')
        return json.loads(json.dumps({key:source[key] for key in keys},allow_nan=False))
    if source.get('decay_proposal') is not None:
        # The optional authority covers rejected originals as well as selected rows.
        keys = ('event_ids', 'weights', 'selected_indices', 'sampling_probabilities', 'gen_sha256',
                'normalization_record', 'sampling', 'production_selection', 'decay_proposal')
        return json.loads(json.dumps({key: source[key] for key in keys}, allow_nan=False))
    return dict(event_ids=[r['event_id'] for r in selected],
                weights=[r['native_weight'] for r in selected],
                selected_indices=list(range(len(selected))),
                sampling_probabilities=[r['sampling_probability'] for r in selected])


def response_closed_source(prepared_manifest, collector_result):
    """Adapt one native-qualified response collector to ordinary snapshot input.

    The supervisor supplies pinned terminal receipts only after its native job
    checks. Pending or partial sources never acquire exposure by this route.
    Existing production source and discovery paths remain unchanged.
    """
    prepared = read_response_pin(prepared_manifest)
    if prepared.get('schema') != 'shift-sm-candidate-handoff-v1':
        raise ValueError('Response discovery requires the candidate source authority')
    collector = prepared['collector']
    request_pin = dict(path=str(Path(collector['job_directory'])/'request.json'),
                       sha256=collector['request_sha256'])
    request = read_response_pin(request_pin)
    if (Path(collector_result['path']) != Path(collector['job_directory'])/'result.json'
            or prepared['benchmark_plan'] != request['benchmark_plan']):
        raise ValueError('Response collector is outside its prepared source authority')
    plan = read_response_pin(request['benchmark_plan'])
    full = read_response_pin(request['full_population_exposure'])['sources']
    if len(full) != 1 or full[0]['source_id'] != prepared['source_id']:
        raise ValueError('Response prepared source has ambiguous full GEN exposure')
    normalization=full[0]['original_full_exposure']; selected=plan['selected']
    descriptor=dict(event_ids=[row['event_id'] for row in selected],
        weights=[row['native_weight'] for row in selected],selected_indices=list(range(len(selected))),
        sampling_probabilities=[row['existing_sampling_probability'] for row in selected],
        gen_sha256=full[0]['prepared_gen_sha256'],normalization_record=normalization,
        response_sampling=dict(collector_request=request_pin,collector_result=collector_result))
    _,_,nanos=response_sampling_contract(descriptor)
    return dict(normalization=normalization,selected=descriptor,nano_inputs=nanos,
        descriptor_hashes=sorted({row['source_descriptor_sha256'] for row in nanos}),
        original_result_path=collector_result['path'],original_result_sha256=collector_result['sha256'])



def response_monitor_pins(config):
    """Use one legacy authority or an explicit union of independent cohorts."""
    single = config.get('response_monitor_config')
    multiple = config.get('response_monitor_configs')
    if single is not None and multiple is not None:
        raise ValueError('Declare one response monitor or a list, not both')
    if multiple is None:
        return [] if not single else [single]
    if not isinstance(multiple, list) or not multiple:
        raise ValueError('Require a nonempty list of pinned response monitors')
    if len({pin['path'] for pin in multiple}) != len(multiple):
        raise ValueError('Repeated response monitor authority')
    return multiple


def response_source_cache(config, state, ads):
    """Combine complete increments without changing any draw or source weight."""
    pins = response_monitor_pins(config)
    if len(pins) == 1:
        return single_response_source_cache(dict(config, response_monitor_config=pins[0]), state, ads)
    cache = state.setdefault('response_sources', {})
    owners, inputs = {}, []
    for pin in pins:
        monitor = read_response_pin(pin)
        plan = read_response_pin(monitor['plan'])
        ids = [row['source_id'] for row in plan['sources'] + monitor.get('extra_sources', [])]
        for sid in ids:
            if sid in owners:
                raise ValueError('Response cohorts repeat an original source: ' + sid)
            owners[sid] = pin
        inputs.append((pin, set(ids)))
    if not set(cache) <= set(owners):
        raise ValueError('A cached source disappeared from the cumulative worklist')
    groups = defaultdict(list)
    for pin, ids in inputs:
        local = dict(response_sources={sid: value for sid, value in cache.items() if sid in ids},
                     response_native=state.setdefault('response_native', {}))
        found = single_response_source_cache(dict(config, response_monitor_config=pin), local, ads)
        cache.update(local['response_sources'])
        for stratum, sources in found.items():
            groups[stratum].extend(sources)
    return groups


def single_response_source_cache(config, state, ads):
    """Discover complete sources from an explicitly pinned response monitor.

    One fresh account queue observation is shared with publication admission.
    Cached sources keep their exact native closure and immutable receipt pins;
    pending, held or replaying source payloads never add publication exposure.
    The existing worker independently checks every selected producer again.
    """
    from monitor_shift_sm_candidate_search import (source_owner,
        native_collector_closure, complete_result, within)
    monitor = read_response_pin(config['response_monitor_config'])
    if (monitor.get('schema') != 'shift-sm-candidate-search-watchdog-v1'
            or monitor.get('individual_source_watchdogs', True)):
        raise ValueError('Response publication requires aggregate native collector authority')
    plan = read_response_pin(monitor['plan'])
    root = Path(monitor['root_directory'])
    expected = [dict(source_id=row['source_id'],
        reconstruction_directory=str(root/'reconstruction'/row['source_id']))
        for row in plan['sources']]
    expected.extend(monitor.get('extra_sources', []))
    ids = [row['source_id'] for row in expected]
    if len(ids) != len(set(ids)):
        raise ValueError('Response publication source authority repeats an original source')
    cache = state.setdefault('response_sources', {})
    if not set(cache) <= set(ids):
        raise ValueError('A cached response source disappeared from the frozen worklist')
    groups = defaultdict(list)
    wave_cache = {}  # Frozen manifests are checked once per fresh queue poll.
    for row in expected:
        sid = row['source_id']; output = Path(row['reconstruction_directory'])
        if (not output.is_absolute() or str(output).startswith(('/tmp/', '/pool/'))
                or not within(str(output), root/'reconstruction')):
            raise ValueError('Response source is outside its persistent reconstruction authority')
        # Check the fresh queue even after native proof has been cached. A new
        # replay under the same source must be reviewed rather than counted twice.
        active = [ad for ad in ads if within(ad.get('Iwd'), output/'jobs')
            and ad.get('JobUniverse') == 5 and ad.get('JobStatus') in (1,2,3,5,6,7)]
        if active:
            if sid in cache:
                raise ValueError('A native-qualified published source has an active replay: '+sid)
            continue
        cached = cache.get(sid)
        if cached is not None:
            for pin in cached['pins']:
                if digest(pin['path']) != pin['sha256']:
                    raise ValueError('Cached response native closure changed: '+pin['path'])
            prepared = read(output/'prepared.json'); submission = read(output/'submission.json')
            owner = source_owner(sid, output, prepared, submission, monitor, ads, wave_cache=wave_cache)
            if owner != cached['native_closure']['owner']:
                raise ValueError('Cached response source native owner changed')
            groups[cached['source']['normalization']['stratum']].append(cached['source'])
            continue
        paths = [output/'prepared.json', output/'submission.json', output/'jobs/collect/result.json']
        if not all(path.exists() for path in paths):
            continue
        prepared, submission, collector = [read(path) for path in paths]
        if not complete_result(collector):
            if collector.get('exit_code') not in (None, 0):
                raise ValueError('Response collector failed: '+sid)
            continue
        if (collector.get('benchmark_plan_sha256') != prepared['benchmark_plan']['sha256']
                or collector.get('complete_frozen_draw_collected') is not True
                or collector.get('events') != len(prepared['detector_jobs'])):
            raise ValueError('Response collector does not close its entire frozen source: '+sid)
        owner=source_owner(sid,output,prepared,submission,monitor,ads,wave_cache=wave_cache)
        closure = native_collector_closure(sid, output, prepared, submission, collector,
            monitor, state.setdefault('response_native', {}), ads,owner=owner)
        if closure is None:
            continue
        pins = [dict(path=str(path), sha256=digest(path)) for path in paths]
        pins.extend(dict(path=str(output/'jobs/collect'/name),
            sha256=digest(output/'jobs/collect'/name)) for name in ('request.json','status.json','events.log'))
        source = response_closed_source(pins[0], pins[2])
        if source['normalization']['source_id'] != sid:
            raise ValueError('Response source normalization differs from its frozen worklist')
        paid=[]
        if config.get('account_paid_outputs'):
            paid, paid_pins = response_paid_objects(prepared,source)
            pins.extend(paid_pins)
        cache[sid] = dict(pins=pins, native_closure=closure, source=source,
                          verified_paid_objects=paid,closed_epoch=time.time())
        groups[source['normalization']['stratum']].append(source)
    return groups


def closed_sources(sources, nanos):
    """Never publish a convenient subset of one source's frozen selection."""
    by_hash = defaultdict(list)
    for row in nanos:
        by_hash[row['source_descriptor_sha256']].append(row)
    closed = defaultdict(list)
    for source in sources:
        rows = [r for h in source['descriptor_hashes'] for r in by_hash.get(h, [])]
        paths = [r['path'] for r in rows]
        if len(paths) != len(set(paths)):
            raise ValueError('Duplicate Nano in closed-source census')
        count = sum(r['events'] for r in rows)
        selection = source['selected']
        expected = len(selection['selected_indices'] if selection.get('decay_proposal') is not None
                       else selection['event_ids'])
        if count > expected:
            raise ValueError('Nano source over-coverage')
        if count == expected:
            closed[source['normalization']['stratum']].append(dict(source, nano_inputs=rows))
    return closed


def meaningful_update(previous, current, minimum_new_sources=10, minimum_relative_growth=.2,
                      baseline_source_count=0):
    old = set(previous)
    new = set(current)
    if not old <= new:
        raise ValueError('A previously published original source disappeared')
    if (type(minimum_new_sources) is not int or minimum_new_sources < 1
            or type(baseline_source_count) is not int or baseline_source_count < 0
            or not isinstance(minimum_relative_growth,(int,float))
            or not math.isfinite(minimum_relative_growth) or minimum_relative_growth <= 0):
        raise ValueError('Invalid cumulative publication refresh thresholds')
    old_size,new_size = len(old)+baseline_source_count,len(new)+baseline_source_count
    return bool(new-old) and (not old_size or len(new-old)>=minimum_new_sources
        or new_size >= (1+minimum_relative_growth)*old_size)


def settled_stratum_status(parent, stratum):
    """Settled failures remain explicit missing responses, never completed physics."""
    science = {name:task for name,task in parent['tasks'].items()
               if task['kind'] in ('source','detector') and task['stratum']==stratum}
    terminal = ('done','failed','quarantined')
    for task in science.values():
        if task['phase'] in terminal:
            continue
        children = task.get('replacement_tasks',[])
        if (task['phase']!='replaced' or not children
                or any(name not in science or science[name]['phase'] not in terminal for name in children)):
            return None
    if not science:
        return None
    missing = [dict(task_name=name,kind=task['kind'],phase=task['phase'],
        source_id=task.get('source_id'),error=task.get('error'),
        selected_events=task.get('selected_events'),quarantined_exposure=task.get('quarantined_exposure'))
        for name,task in science.items() if task['phase'] in ('failed','quarantined')]
    return dict(all_attempts_settled=True,all_responses_complete=not missing,
                excluded_incomplete_tasks=missing,missing_responses_are_physics_rejections=False)


def source_cache(parent, state):
    for name, task in parent['tasks'].items():
        if task['kind'] != 'source' or task['phase'] != 'done':
            continue
        key = task['result_sha256']
        if key in state['sources']:
            continue
        path = Path(task['directory']) / 'result.json'
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != key:
            raise ValueError('Parent source receipt changed')
        result = json.loads(raw)
        if result.get('source_authority'):
            from run_shift_qcd_production_source import read_source_authority
            result = read_source_authority(path.parent, result)
        source = result.get('selected_source')
        if source is None:
            if result.get('empty_source') is not True or result.get('selected_events') != 0:
                raise ValueError('Completed source lacks its frozen selection: ' + name)
            # A genuinely empty native GEN cohort still owns its original
            # trial exposure. It has no detector descriptor or child jobs.
            state['sources'][key] = dict(normalization=result['source'],
                selected=dict(event_ids=[], weights=[], selected_indices=[], sampling_probabilities=[]),
                descriptor_hashes=[], original_result_sha256=key, original_result_path=str(path))
            continue
        normalization = result['source']
        if source.get('qcd_gen_filter') is not None:
            if (source['normalization_record'] != normalization
                    or result.get('qcd_gen_filter') != source['qcd_gen_filter']):
                raise ValueError('QCD generator-filter provenance differs from its source receipt')
            # Keep the original denominator/rate and retain the primary-filter
            # counters as provenance through ordinary and grouped publication.
            # They are never another event-weight or cross-section multiplier.
            normalization = dict(normalization, qcd_gen_filter=source['qcd_gen_filter'])
        state['sources'][key] = dict(normalization=normalization,
            selected=compact_source(source), descriptor_hashes=descriptor_hashes(source),
            original_result_sha256=key, original_result_path=str(path))


def nano_cache(parent, inventory, state):
    published = {Path(r['path']).name: r for r in inventory['nano_outputs']}
    for name, task in parent['tasks'].items():
        if task['kind'] != 'detector' or task['phase'] != 'done':
            continue
        key = task['result_sha256']
        if key in state['nanos']:
            continue
        suffix = name.rsplit('_', 1)[-1]
        row = published.get('nano_job' + suffix.zfill(7) + '.root') if suffix.isdigit() else None
        if row is None or row['stratum'] != task['stratum']:
            status = read(Path(task['directory']) / 'status.json')
            if not status['complete'] or status['exit_code'] != 0:
                raise ValueError('Completed parent Nano lacks successful status')
            row = dict(path=status['nano_path'], sha256=status['nano_sha256'],
                       events=status['events'], stratum=task['stratum'],
                       source_descriptor_sha256=status['source_descriptor_sha256'])
        state['nanos'][key] = dict(row, original_result_sha256=key)




def worker_publication_bound(config, stratum, *, interim=False):
    bound = config.get('worker_publication_bounds_by_stratum', {}).get(
        stratum,config['worker_publication_bound_bytes'])
    if interim:
        bound = min(bound,config.get('interim_histogram_publication_bound_bytes',bound))
    if type(bound) is not int or bound <= 0:
        raise ValueError('Worker publication reservation must be a positive integer')
    return bound


def complete_bin_source_ids(config, stratum):
    """Read the pinned union worklist for optional final-only Nano publication."""
    rows = []
    for pin in response_monitor_pins(config):
        monitor = read_response_pin(pin)
        plan = read_response_pin(monitor['plan'])
        rows.extend(plan['sources'] + monitor.get('extra_sources', []))
    if any('stratum' not in row for row in rows):
        raise ValueError('Final-bin publication requires explicit process/bin on every expected source')
    ids = [row['source_id'] for row in rows if row['stratum'] == stratum]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Final-bin publication has no unique frozen source census')
    return sorted(ids)


def append_baseline_publication(body, pin):
    """Include one qualified old histogram with its complete original exposure."""
    baseline = read_response_pin(pin)
    stratum = body['stratum']
    if baseline.get('schema') != 'shift-native-cumulative-baseline-v1' or baseline.get('stratum') != stratum:
        raise ValueError('Wrong cumulative baseline schema or process/bin')
    original = baseline['normalization_sources']
    ids = [source['source_id'] for source in original]
    current = [source['source_id'] for source in body['normalization_sources']]
    if (not ids or len(ids) != len(set(ids)) or len(current) != len(set(current))
            or len(baseline['baseline_source_ids']) != len(ids)
            or set(baseline['baseline_source_ids']) != set(ids) or set(ids).intersection(current)):
        raise ValueError('Overlapping or inconsistent baseline source IDs')
    selected = baseline['selected_sources']
    if len(selected) != len(original):
        raise ValueError('Baseline selection omits an original source or zero-event exposure')
    old_exposure = exposure(original,stratum)
    exposure(body['normalization_sources']+original,stratum)
    for key in ('source_family','generator_profile','generation_sampling','timing_input_label'):
        values = {json.dumps(source.get(key),sort_keys=True) for source in body['normalization_sources']+original}
        if len(values) != 1:
            raise ValueError('Baseline source class differs: '+key)
    if (baseline['full_original_GEN'] != old_exposure['full_source_sumw']
            or not math.isclose(baseline['cross_section_pb'],old_exposure['cross_section_pb'],rel_tol=1e-10)):
        raise ValueError('Baseline full original GEN exposure or class rate changed')
    old_rows = [row for source in selected for row in selected_rows(source)]
    new_rows = [row for source in body['selected_sources'] for row in selected_rows(source)]
    old_rles = [tuple(row['event_id']) for row in old_rows]
    all_rles = old_rles+[tuple(row['event_id']) for row in new_rows]
    if (len(all_rles) != len(set(all_rles)) or len(baseline['selected_event_ids']) != len(old_rles)
            or set(map(tuple,baseline['selected_event_ids'])) != set(old_rles)):
        raise ValueError('Overlapping or inconsistent baseline selected original identities')
    from run_shift_sm_postprocessing import histogram_weight_branch
    histogram_weight_branch(body['selected_sources']+selected)
    group = baseline['histogram_group']
    if len(group['source_ids']) != len(ids) or set(group['source_ids']) != set(ids):
        raise ValueError('Baseline histogram group omits full source exposure')
    receipt = read_response_pin(group['result_pin'])
    if (receipt.get('complete') is not True or receipt.get('exit_code',0) != 0
            or set(receipt['source_ids']) != set(ids) or len(receipt['source_ids']) != len(ids)
            or receipt['published']['merged_histogram'] != group['histogram']
            or receipt['full_original_exposure']['full_source_sumw'] != old_exposure['full_source_sumw']
            or not math.isclose(receipt['full_original_exposure']['cross_section_pb'],old_exposure['cross_section_pb'],rel_tol=1e-10)
            or len(receipt['full_original_exposure']['original_sources']) != len(original)
            or {r['source_id']:r for r in receipt['full_original_exposure']['original_sources']} !=
               {r['source_id']:r for r in original}):
        raise ValueError('Baseline lacks its exact qualified cumulative histogram receipt')
    weights = [row['native_weight']/row['sampling_probability'] for row in old_rows]
    for key,expected in [('events',len(old_rows)),('sumw',math.fsum(weights)),
                         ('sumw2',math.fsum(weight*weight for weight in weights))]:
        if any(not math.isclose(value[key],expected,rel_tol=1e-10,abs_tol=1e-300)
               for value in (baseline,group,receipt)):
            raise ValueError('Baseline selected event/weight census changed: '+key)
    if sum(row['events'] for row in baseline['nano_inputs']) != len(old_rows):
        raise ValueError('Baseline Nano metadata omits original selected events')
    body['normalization_sources'].extend(original)
    body['selected_sources'].extend(selected)
    body['nano_inputs'].extend(baseline['nano_inputs'])
    body['histogram_groups'].append(group)
    # The baseline histogram already covers ALL old sources, including zeros.
    # Only newly discovered zero-selected sources belong to the separate list.
    body['baseline_publication'] = pin
    return ids


def snapshot(root, config, stratum, sources, publication_status=None):
    sources = sorted(sources, key=lambda s: s['normalization']['source_id'])
    normalizations = [s['normalization'] for s in sources]
    discovery_ids = [source['source_id'] for source in normalizations]
    exposure(normalizations, stratum)
    identities = [tuple(row['event_id']) for source in sources for row in selected_rows(source['selected'])]
    if len(identities) != len(set(identities)):
        raise ValueError('Repeated selected original between source cohorts')
    inputs = [r for s in sources for r in s['nano_inputs']]
    baseline = config.get('baseline_publications_by_stratum',{}).get(stratum)
    baseline_enabled = (baseline and config.get('grouped_response_publication')
        and not config.get('source_group_only') and stratum.split('_')[0] in config.get('grouped_response_processes',['qcd']))
    if not inputs and not baseline_enabled:
        return None
    body = dict(schema='shift-smart-SM-postprocessing-v1', campaign=config['campaign'],
                stratum=stratum, complete_source_selections_only=True,
                normalization_sources=normalizations,
                selected_sources=[s['selected'] for s in sources], nano_inputs=inputs,
                histogram_runtime=config['histogram_runtime'],
                publish_merged_nano=config.get('publish_merged_nano', True),
                maximum_publication_bytes=worker_publication_bound(config,stratum))
    if config.get('compact_metadata') is not None:
        if type(config['compact_metadata']) is not bool:
            raise ValueError('Compact metadata policy must be boolean')
        body['compact_metadata'] = config['compact_metadata']
    if config.get('merged_nano_only_when_bin_complete') is True:
        expected = complete_bin_source_ids(config,stratum)
        observed = [row['source_id'] for row in normalizations]
        if not set(observed) <= set(expected):
            raise ValueError('Closed source lies outside the frozen bin worklist')
        body.update(publish_merged_nano=set(observed)==set(expected),expected_bin_source_ids=expected)
        body['maximum_publication_bytes'] = worker_publication_bound(config,stratum,
            interim=not body['publish_merged_nano'])
    if config.get('source_group_only'):
        body['source_group_only']=True
    if (config.get('grouped_response_publication') and not config.get('source_group_only')
            and stratum.split('_')[0] in config.get('grouped_response_processes',['qcd'])):
        body['histogram_groups']=[source['histogram_group'] for source in sources if source['nano_inputs']]
        body['zero_selected_source_ids']=[source['normalization']['source_id'] for source in sources if not source['nano_inputs']]
        body['publish_merged_nano']=False
        body['maximum_publication_bytes']=67108864
    baseline_ids = []
    if baseline_enabled and body.get('histogram_groups') is not None:
        baseline_ids = append_baseline_publication(body,baseline)
        identities = [tuple(row['event_id']) for source in body['selected_sources'] for row in selected_rows(source)]
    if publication_status is not None:
        body['parent_completion_status'] = publication_status
    checksum = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    directory = root / 'jobs' / (stratum + '_' + checksum[:12])
    timestamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    proc, bounds = stratum.split('_')
    sample = proc + ('_mHat' if proc == 'dy' else '_pThat') + bounds.replace('.', 'p') + 'GeV'
    body.update(sample_directory=config['output_base'] + '/' + sample + '/' + config['campaign'],
                snapshot_name='ntuple_ready_' + timestamp + '_' + checksum[:12])
    directory.mkdir(parents=True, exist_ok=False)
    save(directory / 'request.json', body, compact=body.get('compact_metadata',False))
    text = config['worker_template'].replace('@DIRECTORY@', str(directory))
    text = text.replace('@REQUEST_SHA@', digest(directory / 'request.json'))
    (directory / 'worker.sub').write_text(text)
    task = dict(directory=str(directory), phase='ready', stratum=stratum,
                request_sha256=digest(directory / 'request.json'),
                source_ids=discovery_ids, events=len(identities),
                publishes_merged_nano=body.get('publish_merged_nano',True),
                publication_bound_bytes=body['maximum_publication_bytes'])
    if baseline_ids:
        task.update(baseline_source_ids=baseline_ids,
                    published_source_ids=[s['source_id'] for s in body['normalization_sources']])
    if publication_status is not None:
        task['parent_completion_status'] = publication_status
    return task




def response_paid_objects(prepared, source):
    """Bind retained Nano sizes to the collector's already read-back producers."""
    objects=[]; pins=[]
    expected={row['path']:row['sha256'] for row in source['nano_inputs']}
    for job in prepared['detector_jobs']:
        directory=Path(job['job_directory']);path=directory/'result.json';result=read(path)
        if (result.get('complete') is not True or result.get('exit_code') != 0
                or result.get('request_sha256') != job['request_sha256']
                or result.get('nano_sha256') != result.get('nano_readback_sha256')
                or expected.get(result.get('nano_path')) != result.get('nano_sha256')
                or type(result.get('nano_bytes')) is not int or result['nano_bytes']<=0):
            raise ValueError('Paid Nano lacks its exact terminal collector/readback authority')
        objects.append(dict(kind='Nano',path=result['nano_path'],sha256=result['nano_sha256'],
                            bytes=result['nano_bytes']))
        pins.append(dict(path=str(path),sha256=digest(path)))
    preparation_pin=prepared.get('original_preparation_result')
    if preparation_pin:
        result=read_response_pin(preparation_pin)
        if result.get('complete') is not True or result.get('exit_code') != 0:
            raise ValueError('Paid preparation is not complete')
        for row in result.get('prepared_publications',[]):
            if row.get('independent_sha256_readback') is not True:
                raise ValueError('Paid preparation lacks independent retained readback')
            objects.append(dict(kind='preparation',**{key:row[key] for key in ('path','sha256','bytes')}))
        pins.append(preparation_pin)
    return objects,pins


def verified_science_credit(parent_config, state, quota_epoch):
    """Deduct disjoint paid bytes only, preserving unused allocation headroom."""
    allowed=set(parent_config['paid_science_source_ids'])
    preparations=set(parent_config.get('paid_preparation_source_ids',[]))
    objects={}
    for sid,source in state.get('response_sources',{}).items():
        if sid not in allowed or source.get('closed_epoch',float('inf'))>quota_epoch:
            continue
        for item in source.get('verified_paid_objects',[]):
            if item['kind']=='preparation' and sid not in preparations:
                continue  # Previously paid GEN must never be credited a second time.
            path=item['path']; previous=objects.setdefault(path,item)
            if previous != item or type(item['bytes']) is not int or item['bytes']<=0:
                raise ValueError('Conflicting or invalid paid science object')
    credit=sum(item['bytes'] for item in objects.values())
    if credit>parent_config['reserved_science_bytes']:
        raise ValueError('Paid science exceeds its unchanged registered allocation')
    return credit


def science_write_reservation(parent_config, state, quota_epoch):
    """Keep full allocation; credit future writes only after whole-source closure.

    A quota observation newer than our exact native collection proof already
    includes that cohort's completed GEN/Nano/collector publications. Partial
    cohorts, missing bounds and closures newer than quota keep their full claim.
    """
    total = parent_config.get('reserved_science_bytes', 0)
    if type(total) is not int or total<0:
        raise ValueError('Invalid full science allocation')
    if 'paid_science_source_ids' in parent_config:
        credit=verified_science_credit(parent_config,state,quota_epoch)
        return total,total-credit
    bounds = parent_config.get('science_source_write_bounds', {})
    if (type(total) is not int or total < 0 or not isinstance(bounds, dict)
            or any(not isinstance(sid,str) or type(value) is not int or value < 0
                   for sid,value in bounds.items()) or sum(bounds.values()) > total):
        raise ValueError('Invalid full science allocation or source write bounds')
    closed = state.get('response_sources', {})
    credit = sum(value for sid,value in bounds.items() if sid in closed
        and closed[sid].get('closed_epoch', float('inf')) <= quota_epoch)
    return total, total-credit


def refresh_quota(root, config, ads):
    """Own a quota-only ledger for a new response publisher, never an old campaign.

    This uses the normal smart-SM quota payload and native observer. It cannot
    admit physics jobs or share a writable ledger with another controller.
    """
    from run_shift_smart_sm_controller import make_task, observe, save as save_ledger
    directory = Path(config.get('quota_state', config['parent_state']))
    if directory != root/'quota':
        raise ValueError('Publisher quota authority must be its own private quota directory')
    directory.mkdir(exist_ok=True)
    quota_config = config.get('quota_config', config['parent_config'])
    if config.get('quota_config') and not config.get('quota_config_sha256'):
        raise ValueError('Publisher private quota configuration requires its frozen SHA-256')
    if config.get('quota_config_sha256') and digest(quota_config) != config['quota_config_sha256']:
        raise ValueError('Publisher private quota configuration changed')
    parent_config = read(quota_config)
    path = directory/'ledger.json'
    parent = read(path) if path.exists() else dict(tasks={})
    tasks = list(parent['tasks'].values())
    if any(task['kind'] != 'quota' for task in tasks):
        raise ValueError('Publisher cannot refresh another production ledger')
    for task in tasks:
        observe(task, ads)
    save_ledger(path, parent)
    expected_peer=config.get('peer_storage_authority')
    if expected_peer:
        compatible=[]
        for task in tasks:
            request_path=Path(task['directory'])/'request.json'
            if digest(request_path)!=task['request_sha256']:
                raise ValueError('Quota request authority changed')
            if read(request_path).get('peer_storage_authority')==expected_peer:
                compatible.append(task)
        tasks=compatible  # Old receipts cannot authorize this new peer scope.
    if any(task['phase'] == 'failed' for task in tasks):
        raise ValueError('Native quota worker failed; review before retry')
    completed = [task for task in tasks if task['phase'] == 'done']
    latest = max(completed, key=lambda task:task['finished_epoch']) if completed else None
    quota = read(Path(latest['directory'])/'result.json') if latest else {}
    if (time.time()-quota.get('checked_epoch', 0) >= 900
            and not any(task['phase'] in ('ready','submitted','submitting') for task in tasks)):
        name = 'quota_'+str(len(parent['tasks'])).zfill(5)
        request = dict(schema='shift-stream-payload-v1', environment='cmssw', stage='smart_sm_quota',
            cohort_id=config['campaign'], argv=['python3','@TOOLS@/scripts/run_shift_smart_sm_detector.py',
            'request.json'], application_timeout_seconds=300, result_path='report.json',
            return_files=[], status_fields={}, reservation_bytes=0)
        if parent_config.get('quota_path'):
            request['quota_path'] = parent_config['quota_path']
        if expected_peer:
            request.update(peer_storage_authority=expected_peer,application_timeout_seconds=600)
        parent['tasks'][name] = make_task(directory, parent_config, name, request, 'quota', '')
        save_ledger(path, parent)
    for task in parent['tasks'].values():
        if task['phase'] != 'ready':
            continue
        task.update(phase='submitting', intent_epoch=time.time())
        save_ledger(path, parent)
        answer = run_condor(['/usr/bin/condor_submit','-terse',
            str(Path(task['directory'])/'worker.sub')],cwd=task['directory'],timeout=45)
        match = re.fullmatch(r'(\d+)\.0 - \d+\.0\s*',answer)
        if not match:
            raise ValueError('Uncertain native quota submission requires review')
        task.update(phase='submitted', cluster=int(match[1]),submitted_epoch=time.time())
        save_ledger(path, parent)
    save_ledger(directory/'heartbeat.json',dict(checked_epoch=time.time(),
        phase='running',quota_only=True))



def publication_qualification_ready(config, state):
    """Wait for the named real native canary before ordinary admission."""
    name = config.get('qualification_task')
    if name is None:
        return True
    task = state['tasks'].get(name)
    if task is None or task.get('qualification_only') is not True:
        raise ValueError('Required positive publication canary is missing')
    if task['phase'] == 'failed':
        raise ValueError('Required positive publication canary failed; no ordinary publication')
    return task['phase'] == 'done'



def publication_history(tasks, live):
    """Read new terminal ads once, keeping the established native-ad format."""
    ids=sorted({t['cluster'] for t in tasks.values() if t['phase']=='submitted' and t['cluster'] not in live})
    if not ids:return {}
    constraint='Owner == "jniedzie" && member(ClusterId,{'+','.join(map(str,ids))+'})'
    rows=json.loads(run_condor(['/usr/bin/condor_history','-constraint',constraint,
        '-limit',str(len(ids)),'-json','-attributes','ClusterId,ProcId,Iwd,ExitCode,ExitBySignal'],timeout=45) or '[]')
    result={}
    for row in rows:
        if row.get('ClusterId') not in ids or row.get('ProcId')!=0 or row['ClusterId'] in result:
            raise ValueError('Unexpected or repeated native publication history record')
        result[row['ClusterId']]=row
    return result


def poll(root, config, state):
    journal = submission_journal_enabled(config)
    if journal:
        if type(config.get('maximum_starts_per_poll')) is not int or not 1 <= config['maximum_starts_per_poll'] <= 64:
            raise ValueError('Journaled publication requires a finite batch of 1 to 64 starts')
        reconcile_submission_batch(root, config, state, state_filename='state.json')
    hourly_limit = config.get('maximum_hourly_updates_per_stratum')
    if hourly_limit is not None and (type(hourly_limit) is not int or hourly_limit < 0):
        raise ValueError('Hourly publication limit must be a nonnegative integer')
    ads = query('Owner == "jniedzie"', ['ClusterId', 'ProcId', 'JobStatus', 'JobUniverse',
                'Iwd', 'DAGManJobId', 'Owner', 'GlobalJobId', 'NumJobStarts', 'HoldReason'], timeout=45)
    live = {a['ClusterId']: a for a in ads}
    terminals=publication_history(state['tasks'],live)
    for name, task in state['tasks'].items():
        if task['phase'] not in ('submitted', 'submitting'):
            continue
        if task['phase'] == 'submitting':
            raise ValueError('Uncertain submission requires review: ' + name)
        if task['cluster'] in live:
            if live[task['cluster']]['JobStatus'] == 5:
                task.update(phase='failed', error='Native worker is held; no automatic retry')
            continue
        result = Path(task['directory']) / 'result.json'
        if not result.exists():
            raise ValueError('Absent worker lacks returned result: ' + name)
        terminal=terminals.get(task['cluster'])
        if terminal is None:
            # Fresh queue absence can precede history publication. Keep this
            # response pending, never treat missing evidence as a failed event.
            continue
        if (terminal.get('Iwd')!=task['directory'] or terminal.get('ExitCode')!=0
                or terminal.get('ExitBySignal')):
            task.update(phase='failed',error='Native terminal failure retained',native=[terminal])
            continue
        receipt = read(result)
        if (not receipt['complete'] or receipt['request_sha256'] != task['request_sha256']
                or receipt['events'] != task['events']):
            raise ValueError('Returned worker receipt differs from frozen request')
        task.update(phase='done', native=terminal, result_sha256=digest(result),
                    published_bytes=sum(r['bytes'] for r in receipt['published'].values())
                    + result.stat().st_size + 65536, finished_epoch=time.time())
        if task.get('kind') != 'source_group' and not task.get('qualification_only'):
            state['latest'][task['stratum']] = task['source_ids']
    failed_workers = {name: {key: task[key] for key in (
        'directory','request_sha256','cluster','stratum','kind','events','source_ids','error') if key in task}
        for name,task in state['tasks'].items() if task['phase']=='failed'}
    if failed_workers:
        # Worker failures quarantine only those responses. Keep this separate
        # from a critical controller failure so independent admission can recover.
        save(root/'worker_attention_required.json',dict(checked_epoch=time.time(),
            failed_workers=failed_workers,
            action='Review retained worker attempts and partial outputs; no automatic retry. '
                   'Full publication bounds remain reserved; independent qualified work continues.'))
    qualified = publication_qualification_ready(config,state)
    parent_for_completion = None
    if not qualified:
        # The qualification itself still needs the ordinary quota-checked
        # submission path. Returning here deadlocks a ready canary forever.
        groups = {}
    elif response_monitor_pins(config):
        groups = response_source_cache(config, state, ads)
    else:
        parent = read(Path(config['parent_state']) / 'ledger.json')
        parent_for_completion = parent
        try:
            inventory = read(Path(config['parent_state']) / 'source_inventory.json')
        except FileNotFoundError:
            # The source controller emits its first inventory after a useful
            # increment. Completed Nanos still require nano_cache's real status.
            inventory = dict(nano_outputs=[])
        source_cache(parent, state)
        nano_cache(parent, inventory, state)
        groups = closed_sources(state['sources'].values(), state['nanos'].values())
    if qualified and config.get('grouped_response_publication'):
        ready=defaultdict(list)
        group_tasks={t['source_ids'][0]:t for t in state['tasks'].values() if t.get('kind')=='source_group'}
        for stratum,sources in groups.items():
            if stratum.split('_')[0] not in config.get('grouped_response_processes',['qcd']):
                ready[stratum].extend(sources);continue
            for source in sources:
                sid=source['normalization']['source_id']
                if not source['nano_inputs']:
                    ready[stratum].append(source);continue
                task=group_tasks.get(sid)
                if task is None:
                    local=dict(config,source_group_only=True,grouped_response_publication=False,
                        output_base=config['output_base']+'/_source_groups',publish_merged_nano=True,
                        worker_publication_bounds_by_stratum={stratum:max(67108864,4194304*len(source['nano_inputs'])+16777216)})
                    local.pop('merged_nano_only_when_bin_complete',None)
                    task=snapshot(root,local,stratum,[source]);task['kind']='source_group'
                    task['publication_bound_bytes']=max(67108864,4194304*task['events']+16777216)
                    state['tasks'][Path(task['directory']).name]=task
                    group_tasks[sid]=task
                    if not journal:
                        save(root/'state.json',state)
                if task['phase']=='done':
                    result=Path(task['directory'])/'result.json'
                    if digest(result)!=task['result_sha256']:raise ValueError('Qualified group receipt changed')
                    receipt=read(result)
                    source=dict(source,histogram_group=dict(source_ids=task['source_ids'],events=task['events'],
                        sumw=receipt['sumw'],sumw2=receipt['sumw2'],histogram=receipt['published']['merged_histogram'],
                        result_sha256=task['result_sha256']))
                    ready[stratum].append(source)
        # Never replace an existing cumulative snapshot with fewer sources while
        # the first qualification of its source groups is still in progress.
        groups={st:src for st,src in ready.items() if set(state['latest'].get(st,[]))<=
            {row['normalization']['source_id'] for row in src}}
    busy = {t['stratum'] for t in state['tasks'].values() if t.get('kind')!='source_group' and t['phase'] in ('ready', 'submitted', 'submitting')}
    failed = {t['stratum'] for t in state['tasks'].values() if t.get('kind')!='source_group' and t['phase'] == 'failed'}
    # At most one immutable snapshot per bin at a time; meaningful changes only.
    for stratum, sources in groups.items():
        if stratum in busy or stratum in failed:
            continue
        ids = [s['normalization']['source_id'] for s in sources]
        previous = state['latest'].get(stratum, [])
        last_finished = max((t.get('finished_epoch', 0) for t in state['tasks'].values()
                             if t['stratum'] == stratum and t['phase'] == 'done'
                             and t.get('kind')!='source_group' and not t.get('qualification_only')), default=0)
        batching = 'minimum_new_sources' in config or 'minimum_relative_source_growth' in config
        baseline_count = 0
        if batching:
            pin = config.get('baseline_publications_by_stratum',{}).get(stratum)
            if pin:
                baseline_count = len(read_response_pin(pin)['baseline_source_ids'])
            if not last_finished:
                last_finished = state.setdefault('publication_refresh_epochs',{}).setdefault(stratum,time.time())
        # Every created hourly snapshot retains metadata, even while pending or
        # after failure. Growth and final flushes do not consume this allowance.
        hourlies = sum(t.get('publication_trigger') == 'hourly' and t['stratum'] == stratum
                       for t in state['tasks'].values())
        hourly_update = (set(ids) > set(previous) and time.time() - last_finished > 3600
                        and (hourly_limit is None or hourlies < hourly_limit))
        final_due = (config.get('merged_nano_only_when_bin_complete') is True
            and set(ids) == set(complete_bin_source_ids(config,stratum))
            and not any(task['stratum']==stratum and task['phase']=='done'
                and task.get('publishes_merged_nano') is True for task in state['tasks'].values()))
        settled = settled_stratum_status(parent_for_completion,stratum) if batching and parent_for_completion else None
        final_settled = (settled is not None and set(ids)>set(previous) and not any(
            t['stratum']==stratum and t.get('kind')=='source_group' and t['phase'] in ('ready','submitted','submitting')
            for t in state['tasks'].values()))
        growth_update = meaningful_update(previous,ids,config.get('minimum_new_sources',10),
                config.get('minimum_relative_source_growth',.2),baseline_count)
        if not growth_update and not hourly_update and not final_due and not final_settled:
            continue
        task = snapshot(root,config,stratum,sources,publication_status=settled if final_settled else None)
        if task:
            task['publication_trigger'] = ('final' if final_due or final_settled else
                                           'growth' if growth_update else 'hourly')
            state['tasks'][Path(task['directory']).name] = task
            if not journal:
                save(root / 'state.json', state)
    # This is an extension of the registered SM production, not another GEN or
    # detector production. Keep its extra writes bounded and protect both the
    # parent's full allocation and all of the parent's external reservations.
    submitted = 0
    spent = sum(t.get('published_bytes', 0) for t in state['tasks'].values())
    reserved = sum(t.get('publication_bound_bytes',config['worker_publication_bound_bytes']) for t in state['tasks'].values()
                   # A failed worker can leave unverified partial EOS writes.
                   # Release its claim only after an explicit reviewed readback.
                   if t['phase'] in ('submitted', 'submitting', 'failed'))
    # Scheduler hosts have no EOS client. Reuse the main production's actual
    # authenticated quota worker and its pinned native-success receipt.
    if config.get('refresh_quota') is True:
        ads=query('Owner == "jniedzie"',['ClusterId','ProcId','JobStatus','JobUniverse',
            'Iwd','DAGManJobId','Owner','GlobalJobId','NumJobStarts','HoldReason'],timeout=45)
        refresh_quota(root,config,ads)
    parent=read(Path(config['parent_state'])/'ledger.json')
    quota_directory = Path(config.get('quota_state', config['parent_state']))
    separate_quota = quota_directory != Path(config['parent_state'])
    quota_parent = read(quota_directory/'ledger.json') if separate_quota else parent
    observations = [t for t in quota_parent['tasks'].values()
                    if t['kind'] == 'quota' and t['phase'] == 'done']
    if config.get('peer_storage_authority'):
        observations=[t for t in observations if read_response_pin(dict(
            path=str(Path(t['directory'])/'request.json'),sha256=t['request_sha256'])).get(
                'peer_storage_authority')==config['peer_storage_authority']]
    if not observations:
        save(root / 'state.json', state)
        save(root / 'heartbeat.json', dict(checked_epoch=time.time(), phase='waiting_for_quota',
            reason='Waiting for the first authenticated native quota worker; no publication admission'))
        return
    observation = max(observations, key=lambda t: t['finished_epoch'])
    result = Path(observation['directory']) / 'result.json'
    if digest(result) != observation['result_sha256'] or observation.get('native_exit_code') != 0:
        raise ValueError('Parent quota authority changed or lacks native success')
    quota = read(result)
    if not quota.get('complete') or time.time() - quota['checked_epoch'] > 1800:
        save(root / 'state.json', state)
        save(root / 'heartbeat.json', dict(checked_epoch=time.time(), phase='waiting_for_quota',
             reason='Waiting for the parent authenticated quota refresh; no new admission'))
        return
    parent_since = sum(t.get('published_bytes', 0) for t in parent['tasks'].values()
                       if t['phase'] == 'done' and t.get('finished_epoch', 0) > quota['checked_epoch'])
    own_since = sum(t.get('published_bytes', 0) for t in state['tasks'].values()
                    if t.get('finished_epoch', 0) > quota['checked_epoch'])
    free = quota['free_bytes'] - parent_since - own_since
    parent_config = read(config['parent_config'])
    if separate_quota:
        # Quota observation may outlive science admission. Its private ledger
        # must never replace the real science ledger in resource accounting.
        if (not config.get('refresh_quota') or quota_directory != root/'quota'
                or not config.get('parent_config_sha256')
                or digest(config['parent_config']) != config['parent_config_sha256']
                or type(parent_config.get('own_reservation_bytes')) is not int
                or parent_config['own_reservation_bytes'] <= 0):
            raise ValueError('Separate publisher quota requires its full pinned scientific parent')
        science = [t for t in parent['tasks'].values() if t['kind'] in ('source', 'detector')]
        if not science or any(t.get('native_exit_code') != 0
                or not re.fullmatch(r'[0-9a-f]{64}', t.get('result_sha256', ''))
                or type(t.get('published_bytes')) is not int or t['published_bytes'] < 0
                for t in science if t['phase'] == 'done'):
            raise ValueError('Scientific parent lacks native completed-work accounting')
    if (config.get('refresh_quota') is True and not separate_quota
            and 'reserved_science_bytes' not in parent_config):
        raise ValueError('Private quota publication requires an explicit full science write reservation')
    science_reserve, science_future = science_write_reservation(parent_config,state,quota['checked_epoch'])
    parent_committed = sum(t.get('published_bytes', 0) for t in parent['tasks'].values() if t['phase'] == 'done')
    parent_in_flight = sum(t.get('bound_bytes', 0) for t in parent['tasks'].values()
                           if t['phase'] in ('submitted', 'submitting'))
    # The quota-only ledger does not own physics task bookkeeping. Preserve
    # the entire registered allocation, while fresh quota can release future
    # writes for exact, wholly completed source cohorts.
    parent_future = parent_in_flight + science_future
    parent_remaining = max(0, parent_config['own_reservation_bytes'] - parent_committed)
    external=parent_config['external_future_reservation_bytes']
    if config.get('peer_storage_authority'):
        peer=quota.get('peer_storage',{})
        if (peer.get('schema')!='shift-SM-verified-peer-remaining-storage-v1'
                or peer.get('complete') is not True
                or peer.get('authority_sha256')!=config['peer_storage_authority']['sha256']
                or peer.get('fresh_native_queue_verified') is not True
                or peer.get('total_reserved_bytes')!=parent_config['peer_storage_reserved_bytes']
                or type(peer.get('verified_paid_bytes')) is not int
                or not 0<=peer['verified_paid_bytes']<=peer['total_reserved_bytes']
                or peer.get('remaining_bytes')!=peer['total_reserved_bytes']-peer['verified_paid_bytes']):
            raise ValueError('Fresh peer paid-output authority is missing or inconsistent')
        external-=peer['verified_paid_bytes']
        evidence=peer.get('own_verified_evidence_bytes',0)
        objects=peer.get('own_verified_evidence_objects',[])
        if (type(evidence) is not int or evidence<0 or evidence>science_future
                or len({row['path'] for row in objects})!=len(objects)
                or evidence!=sum(row['bytes'] for row in objects)):
            raise ValueError('Own evidence paid-byte authority is inconsistent')
        science_future-=evidence
        parent_future=parent_in_flight+science_future
    floor = external + parent_config['safety_floor_bytes']
    idle_limit = config.get('maximum_idle_workers',parent_config.get('maximum_idle_workers'))
    running_limit = config.get('account_worker_ceiling',parent_config['account_worker_ceiling'])
    if (type(running_limit) is not int or running_limit < 1
            or idle_limit is not None and (type(idle_limit) is not int or idle_limit < 1)):
        raise ValueError('Account running and idle limits must be positive integers')
    # Explicit separate limits preserve queue headroom for independent useful
    # work. Older policies without an idle limit retain their previous behavior.
    statuses = (2,6,7) if idle_limit is not None else (1,2,6,7)
    account_workers = sum(a.get('JobUniverse') == 5 and a['JobStatus'] in statuses for a in ads)
    account_idle = sum(a.get('JobUniverse') == 5 and a['JobStatus'] == 1 for a in ads)
    admission_wait = None
    submission_batch = None
    for name, task in sorted(state['tasks'].items(), key=lambda item: item[1]['events']):
        if task['phase'] != 'ready':
            continue
        if not qualified and task is not state['tasks'][config['qualification_task']]:
            continue
        if task.get('kind')=='source_group' and sum(t.get('kind')=='source_group' and
                t['phase'] in ('submitted','submitting') for t in state['tasks'].values())>=config.get('maximum_group_workers',48):
            continue
        bound = task.get('publication_bound_bytes',config['worker_publication_bound_bytes'])
        if spent + reserved + bound > config['maximum_publication_bytes']:
            admission_wait = 'publication_budget'
            break
        if parent_committed + parent_in_flight + science_reserve + spent + reserved + bound > parent_config['own_reservation_bytes']:
            admission_wait = 'science_allocation'
            break
        if free - reserved - bound < floor + parent_future:
            admission_wait = 'storage'
            break
        if account_workers + submitted >= running_limit:
            admission_wait = 'running_worker_limit'
            break
        if idle_limit is not None and account_idle + submitted >= idle_limit:
            admission_wait = 'idle_worker_limit'
            break
        # A small canary uses the exact real publication route; independent
        # bins are released together once that result has passed.
        if (not state['latest'] and not (qualified and config.get('qualification_task'))
                and any(t['phase'] == 'submitted' for t in state['tasks'].values())):
            break
        if journal:
            if submitted >= config['maximum_starts_per_poll']:
                break
            if submission_batch is None:
                submission_batch = begin_submission_batch(root, config, state, state_filename='state.json')
            submit_journal_task(root, config, state, submission_batch, name, task,
                                Path(task['directory'])/'worker.sub')
        else:
            task['phase'] = 'submitting'
            save(root / 'state.json', state)
            answer = run_condor(['/usr/bin/condor_submit', '-terse',
                                str(Path(task['directory']) / 'worker.sub')], cwd=task['directory'], timeout=45)
            match = re.fullmatch(r'(\d+)\.0 - \d+\.0\s*', answer)
            if not match:
                raise ValueError('Uncertain native submit result')
            task.update(phase='submitted', cluster=int(match[1]), submitted_epoch=time.time())
            save(root / 'state.json', state)
        reserved += bound
        submitted += 1
    waiting = any(t['phase'] == 'ready' for t in state['tasks'].values()) and not any(
        t['phase'] in ('submitted', 'submitting') for t in state['tasks'].values())
    save(root / 'heartbeat.json', dict(checked_epoch=time.time(), submitted_this_poll=submitted,
        phase='waiting_for_'+(admission_wait or 'admission') if waiting else 'running',
        admission_wait_reason=admission_wait,account_running_workers=account_workers,
        account_idle_workers=account_idle,account_worker_ceiling=running_limit,maximum_idle_workers=idle_limit,
        publication_budget_remaining_bytes=config['maximum_publication_bytes'] - spent - reserved,
        ready_sources={s: len(v) for s, v in groups.items()},
        tasks={phase: sum(t['phase'] == phase for t in state['tasks'].values())
               for phase in ('ready', 'submitted', 'done', 'failed')},
        EOS_free_bytes=free, protected_parent_remaining_bytes=parent_remaining,
        protected_external_and_floor_bytes=floor,
        protected_science_allocation_bytes=science_reserve,protected_science_future_bytes=science_future))
    if journal:
        finish_submission_batch(root, state, submission_batch, state_filename='state.json')
    else:
        save(root / 'state.json', state)


def validate_deployment_inputs(deployment, config, helpers_directory):
    """Check the actual helper closure and worker paths before submitting work."""
    deployment, helpers = Path(deployment), Path(helpers_directory)
    pinned = {}
    for name, checksum in config['frozen_files'].items():
        path = deployment / name
        if digest(path) != checksum:
            raise ValueError('Frozen postprocessing deployment changed: ' + name)
        pinned[path.resolve()] = checksum
    required = ['run_shift_sm_postprocessing', 'shift_condor_native',
                'shift_event_weights', 'shift_nano_integrity']
    if config.get('refresh_quota') or submission_journal_enabled(config):
        required.append('run_shift_smart_sm_controller')
    if config.get('compact_source_authority'):
        required.append('run_shift_qcd_production_source')
    if response_monitor_pins(config):
        required.append('monitor_shift_sm_candidate_search')
    for name in required:
        path = helpers / (name + '.py')
        if not path.is_file() or path.resolve() not in pinned:
            raise ValueError('Missing pinned publication helper: ' + str(path))
    fields = {}
    for line in config['worker_template'].splitlines():
        name, separator, value = line.partition('=')
        if separator:
            fields[name.strip().lower()] = value.strip()
    executable = Path(fields.get('executable', '').strip('"'))
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError('Publication worker executable is missing: ' + str(executable))
    arguments = fields.get('arguments', '')
    capsule_hashes = re.findall(r'(?<!\S)[0-9a-f]{64}(?!\S)', arguments)
    for name in fields.get('transfer_input_files', '').split(','):
        name = name.strip().strip('"')
        if not name or '@DIRECTORY@' in name:
            continue  # Each immutable job supplies its own request later.
        path = Path(name)
        if not path.is_absolute() or not path.is_file():
            raise ValueError('Publication worker input is missing: ' + name)
        if path.name.endswith('.tar.gz') and digest(path) not in capsule_hashes:
            raise ValueError('Publication worker archive differs from its argument hash: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--watchdog', action='store_true')
    parser.add_argument('--config-sha256')
    args = parser.parse_args()
    deployment = args.directory.resolve()
    config = read(deployment / 'config.json')
    if args.config_sha256 and digest(deployment / 'config.json') != args.config_sha256:
        raise ValueError('Frozen controller configuration changed')
    root = Path(config.get('state_directory', str(deployment)))
    if str(root).startswith(('/tmp/', '/pool/')):
        raise ValueError('A detached controller needs persistent storage')
    if args.watchdog:
        while True:
            try:
                ads = query('Iwd == ' + json.dumps(str(deployment)) +
                    ' && ShiftSMPostprocessingController =?= True',
                    ['ClusterId', 'JobStatus', 'Iwd'], timeout=45)
                heartbeat = read(root / 'heartbeat.json')
                problems = []
                if not ads or any(a['JobStatus'] == 5 for a in ads):
                    problems.append('Postprocessing controller missing or held')
                if time.time() - heartbeat.get('checked_epoch', 0) > 600:
                    problems.append('Postprocessing controller heartbeat stale')
                if heartbeat.get('tasks', {}).get('failed'):
                    problems.append('Failed histogram workers require review')
                record = dict(checked_epoch=time.time(), problems=problems, controller_ads=ads)
                save(root / 'watchdog_heartbeat.json', record)
                if problems:
                    save(root / 'watchdog_attention_required.json', record)
            except (OSError, NativeCondorError) as error:
                save(root / 'watchdog_query_failure.json', dict(checked_epoch=time.time(), error=repr(error)))
            if args.once:
                return
            time.sleep(60)
    with (root / 'controller.lock').open('a') as lock:
        wait_for_lock(lock)
        validate_deployment_inputs(deployment, config, Path(__file__).resolve().parent)
        state = read(root / 'state.json') if (root / 'state.json').exists() else dict(
            sources={}, nanos={}, tasks={}, latest={})
        while True:
            try:
                poll(root, config, state)
            except (OSError, NativeCondorError) as error:
                if isinstance(error, OSError) and error.errno not in (errno.EIO, errno.ESTALE, errno.ETIMEDOUT):
                    raise
                if any(t['phase'] == 'submitting' for t in state['tasks'].values()):
                    save(root / 'attention_required.json', dict(error=repr(error),
                        action='Uncertain submit; review native queue before recovery', checked_epoch=time.time()))
                    raise
                save(root / 'state.json', state)
                save(root / 'transient_failure.json', dict(error=repr(error), checked_epoch=time.time()))
                time.sleep(30)
                continue
            except Exception as error:
                save(root / 'attention_required.json', dict(error=repr(error), checked_epoch=time.time()))
                raise
            if args.once:
                break
            time.sleep(180)


if __name__ == '__main__':
    main()
