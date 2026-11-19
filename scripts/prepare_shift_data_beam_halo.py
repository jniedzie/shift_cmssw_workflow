#!/usr/bin/env python3
# Code style: AI-code.
"""Prepare bounded halo-selected responses with a separate full-LS exposure contract.

This tool submits nothing. The selected census serves the ordinary collector;
only the separate exposure contract can close full certified lumisections.
"""
import argparse
import collections
import copy
import datetime
import json
import os
from pathlib import Path
import re

from audit_shift_data_luminosity import identity, identity_digest, lumimask, pin, require, sha256, write_json
from prepare_shift_data_lumisections import build_full_lumi_plan, canonical_lfn, selected_lumisections
from run_shift_data_reconstruction import validate_request
from scan_shift_data_beam_halo import digest, merge_shards, validate_raw_mapping


SCIENCE_FIELDS = ('payload_files', 'runtime_url', 'runtime_sha256', 'table_sha256')
CONTRACT_FIELDS = ('representation', 'policy', 'policy_sha256', 'lumimask', 'datasets',
                   'aod_datasets', 'original_events', 'original_identity_sha256',
                   'selected_events', 'selected_identity_sha256', 'lumisections',
                   'qualification', 'pins', 'worker_science', 'worker_settings')


def build_halo_plan(aod_census, raw_census, max_events_per_job=10):
    require(type(max_events_per_job) is int and 1 <= max_events_per_job <= 100,
            'Halo worker size must be between one and 100 selected originals')
    mapping = validate_raw_mapping(aod_census, raw_census)
    require(aod_census.get('policy_sha256') == digest(aod_census['policy']), 'Halo policy digest changed')
    raw_rows = raw_census['events']
    require({row['globalcohort_entry'] for row in raw_rows} == set(range(len(raw_rows))),
            'Original RAW cohort offsets are missing or repeated')
    selected = sorted(mapping['events'], key=lambda row: (
        row['run'], row['luminosityBlock'], row['source_index'], row['source_entry']))
    require(len(selected) <= 1000000, 'Selected cohort exceeds the ordinary collector event budget')
    events = []
    for offset, row in enumerate(selected):
        index, entry = row['source_index'], row['source_entry']
        require(type(index) is int and 0 <= index < len(raw_census['sources']), 'Unknown selected RAW source')
        source = raw_census['sources'][index]
        require(type(entry) is int and 0 <= entry < source['events_tree_entries']
                and row['source_dataset'] == source['dataset'] and row['source_url'] == source['source_url'],
                'Selected RAW source ownership changed')
        events.append(dict(row, globalcohort_entry=offset,
                           original_globalcohort_entry=row['globalcohort_entry']))
    selected_census = dict(schema='shift-data-input-header-census-v2', era='Run2023D',
        representation='halo_selected_responses', header_only=True, all_headers_is_real_data=True,
        candidate_branches_read=False, product_handles_requested=False,
        all_required_files_scanned_to_eof=False, projection_of_complete_raw_header_census=True,
        event_limit_applied=False, prior_events_excluded=False, beam_halo_preselection_applied=True,
        datasets=sorted(raw_census['datasets']), selected_lumis=copy.deepcopy(raw_census['selected_lumis']),
        unique_originals=len(events), sources=copy.deepcopy(raw_census['sources']), events=events,
        expected_identity_sha256=identity_digest([identity(row) for row in events]),
        processed_identity_sha256=digest([list(identity(row)) for row in events]),
        policy_sha256=aod_census['policy_sha256'],
        luminosity_credit='none; selected response projection is not the full original denominator')
    jobs = []
    by_lumi = collections.defaultdict(list)
    offset = 0
    while offset < len(events):
        first = events[offset]
        key = first['run'], first['luminosityBlock'], first['source_index']
        end = offset+1
        while end < len(events) and end-offset < max_events_per_job:
            row = events[end]
            if (row['run'], row['luminosityBlock'], row['source_index']) != key:
                break
            end += 1
        rows = events[offset:end]
        job = dict(name=f'r{key[0]:06d}_ls{key[1]:06d}_s{key[2]:04d}_o{offset:09d}',
                   run=key[0], luminosityBlock=key[1], source_index=key[2],
                   source_offset=offset, expected_event_count=len(rows), events=rows,
                   identity_sha256=identity_digest([identity(row) for row in rows]))
        jobs.append(job)
        by_lumi[key[:2]].append(job['name'])
        offset = end
    original_by_lumi, selected_by_lumi = collections.defaultdict(list), collections.defaultdict(list)
    for row in raw_rows:
        original_by_lumi[identity(row)[:2]].append(identity(row))
    for row in events:
        selected_by_lumi[identity(row)[:2]].append(identity(row))
    lumis = [dict(run=run, luminosityBlock=lumi,
                   original_events=len(original_by_lumi[run, lumi]),
                   selected_events=len(selected_by_lumi[run, lumi]),
                   rejected_events=len(original_by_lumi[run, lumi])-len(selected_by_lumi[run, lumi]),
                   unknown_events=0, original_identity_sha256=identity_digest(original_by_lumi[run, lumi]),
                   selected_identity_sha256=identity_digest(selected_by_lumi[run, lumi]),
                   job_names=by_lumi[run, lumi])
             for run, lumi in sorted(selected_lumisections(raw_census['selected_lumis']))]
    return dict(selected_census=selected_census, jobs=jobs, lumisections=lumis,
                lumimask=copy.deepcopy(raw_census['selected_lumis']), datasets=sorted(raw_census['datasets']),
                aod_datasets=sorted(aod_census['datasets']), policy=copy.deepcopy(aod_census['policy']),
                policy_sha256=aod_census['policy_sha256'], original_events=len(raw_rows),
                original_identity_sha256=mapping['original_identity_sha256'],
                selected_events=len(events), selected_identity_sha256=mapping['selected_identity_sha256'])


def worker_request(template, job, selected_census, directory, cohort_sha256, selected_census_sha256,
                   campaign_date, settings):
    request = copy.deepcopy(template)
    require(request.get('schema') == 'shift-data-reconstruction-job-v2', 'Require qualified version-two worker template')
    for key in tuple(request):
        if (key.startswith(('remote_', 'storage_claim', 'transport_qualification', 'recovery_', 'timeout_recovery'))
                or key in ('afs_allocation', 'operational_canary', 'paired_group', 'representation',
                           'source_file_adler32', 'full_lumisection_cohort_sha256')):
            request.pop(key)
    source = selected_census['sources'][job['source_index']]
    primary = source['dataset'].split('/')[1]
    request.update(directory=str(directory), job_name=job['name'], dataset=source['dataset'], era='Run2023D',
                   batch_name=f'DATA_Run2023D_{primary}_SHIFTReco_{campaign_date}_halo_{cohort_sha256[:8]}',
                   source_url=source['source_url'], source_offset=job['source_offset'],
                   events=[{key: row[key] for key in ('run', 'luminosityBlock', 'event')} for row in job['events']],
                   representation='halo_selected_responses', input_census_sha256=selected_census_sha256,
                   beam_halo_cohort_sha256=cohort_sha256, beam_halo_policy_sha256=selected_census['policy_sha256'],
                   storage_mode='restricted_AFS_no_EOS',
                   exposure_credit='Selected response only; full original exposure belongs to the halo exposure contract')
    if source.get('adler32'):
        request['source_file_adler32'] = source['adler32']
    request.update(copy.deepcopy(settings))
    validate_request(request)
    return request


def validate_contract(contract, verify_files=True):
    require(contract.get('schema') == 'shift-data-beam-halo-exposure-contract-v1'
            and contract.get('preparation_only') is True
            and contract.get('representation') == 'halo_selected_responses', 'Require separate halo exposure contract')
    require(contract['cohort_sha256'] == digest({key: contract[key] for key in CONTRACT_FIELDS})
            and contract['policy_sha256'] == digest(contract['policy']), 'Halo exposure contract binding changed')
    qualification = contract['qualification']
    require(qualification == dict(all_original_AOD_files_scanned_to_eof=True,
                                 all_original_RAW_files_scanned_to_eof=True,
                                 flag_census_closed=True, exact_AOD_RAW_identity_match=True,
                                 event_limit_applied=False), 'Full original census qualification is incomplete')
    for value in contract['pins'].values():
        require(Path(value['path']).is_absolute() and re.fullmatch(r'[0-9a-f]{64}', value['sha256']),
                'Invalid frozen halo metadata pin')
        if verify_files:
            require(sha256(value['path']) == value['sha256'], 'Frozen halo metadata changed')
    if verify_files:
        census = json.loads(Path(contract['pins']['selected_census']['path']).read_text())
        require(census['representation'] == 'halo_selected_responses'
                and census['unique_originals'] == len(census['events']) == contract['selected_events']
                and census['expected_identity_sha256'] == contract['selected_identity_sha256'],
                'Selected response census differs from exposure contract')
    jobs = {}
    offsets = set()
    for job in contract['jobs']:
        require(job['name'] not in jobs and re.fullmatch(r'[0-9a-f]{64}', job['request_sha256'])
                and Path(job['request_path']).is_absolute()
                and type(job['expected_event_count']) is int and 1 <= job['expected_event_count'] <= 100,
                'Invalid or duplicate halo worker')
        span = set(range(job['source_offset'], job['source_offset']+job['expected_event_count']))
        require(not offsets & span, 'Overlapping selected response offsets')
        offsets.update(span)
        jobs[job['name']] = job
        if verify_files:
            require(sha256(job['request_path']) == job['request_sha256'], 'Frozen halo worker request changed')
            request = json.loads(Path(job['request_path']).read_text())
            ids = validate_request(request)
            selected_rows = census['events'][job['source_offset']:job['source_offset']+job['expected_event_count']]
            require(identity_digest(ids) == job['identity_sha256']
                    and ids == [identity(row) for row in selected_rows]
                    and request['source_offset'] == job['source_offset']
                    and all(request['dataset'] == row['source_dataset']
                            and request['source_url'] == row['source_url'] for row in selected_rows)
                    and all(request[key] == contract['worker_science'][key] for key in SCIENCE_FIELDS)
                    and request['input_census_sha256'] == contract['pins']['selected_census']['sha256']
                    and request['beam_halo_cohort_sha256'] == contract['cohort_sha256'],
                    'Halo worker science or selected census binding changed')
    require(offsets == set(range(contract['selected_events'])), 'Selected worker union is incomplete')
    selected = selected_lumisections(contract['lumimask'])
    observed, referenced = set(), []
    for row in contract['lumisections']:
        key = row['run'], row['luminosityBlock']
        require(key in selected and key not in observed and row['unknown_events'] == 0
                and 0 <= row['selected_events'] <= row['original_events']
                and row['rejected_events'] == row['original_events']-row['selected_events'],
                'Invalid full original lumisection accounting')
        observed.add(key)
        names = row['job_names']
        require(len(names) == len(set(names)) and all(name in jobs for name in names)
                and all((jobs[name]['run'], jobs[name]['luminosityBlock']) == key for name in names)
                and sum(jobs[name]['expected_event_count'] for name in names) == row['selected_events'],
                'Lumisection selected response workers differ')
        referenced.extend(names)
    require(observed == selected and len(referenced) == len(jobs) and set(referenced) == set(jobs)
            and sum(row['original_events'] for row in contract['lumisections']) == contract['original_events']
            and sum(row['selected_events'] for row in contract['lumisections']) == contract['selected_events'],
            'Full lumisection exposure union differs')
    return contract


def completed_lumisections(contract, verified_request_sha256s):
    validate_contract(contract)
    jobs = {job['name']: job for job in contract['jobs']}
    verified = set(verified_request_sha256s)
    require(verified <= {job['request_sha256'] for job in jobs.values()}, 'Unknown completed halo request')
    rows, closed, pending = [], set(), set()
    for row in contract['lumisections']:
        missing = [jobs[name]['request_sha256'] for name in row['job_names']
                   if jobs[name]['request_sha256'] not in verified]
        (pending if missing else closed).add((row['run'], row['luminosityBlock']))
        rows.append(dict(row, complete=not missing, missing_request_sha256s=missing))
    return dict(closed_lumimask=lumimask(closed), pending_lumimask=lumimask(pending), lumisections=rows,
                closed_original_events=sum(row['original_events'] for row in rows if row['complete']),
                closed_selected_events=sum(row['selected_events'] for row in rows if row['complete']),
                luminosity_credit='closed full original lumisections; apply the same halo selection to MC')


def metadata_path(explicit, census, digest_field, path_field=None):
    if explicit is not None:
        path = Path(explicit)
    elif path_field and census.get(path_field):
        path = Path(census[path_field])
    else:
        matches = [Path(path) for path, value in census.get('input_pins', {}).items()
                   if value == census[digest_field]]
        require(len(matches) == 1, 'Provide the exact frozen catalog or certification path')
        path = matches[0]
    require(sha256(path) == census[digest_field], 'Census catalog or certification pin changed')
    return path.resolve()


def prepare(aod_path, raw_path, template_path, directory, *, aod_catalog_path=None,
            raw_catalog_path=None, certification_path=None, max_events_per_job=10,
            campaign_date='20261119', settings=None):
    aod_path, raw_path, template_path, directory = map(Path, (aod_path, raw_path, template_path, directory))
    directory = directory.resolve()
    require(directory.parts[1] in ('afs', 'eos') and not directory.exists(),
            'Require a new absolute persistent halo preparation directory')
    require(re.fullmatch(r'[0-9]{8}', campaign_date), 'Require YYYYMMDD campaign date')
    aod, raw = json.loads(aod_path.read_text()), json.loads(raw_path.read_text())
    aod_catalog_path = metadata_path(aod_catalog_path, aod, 'full_lumisection_catalog_sha256')
    raw_catalog_path = metadata_path(raw_catalog_path, raw, 'full_lumisection_catalog_sha256', 'full_lumisection_catalog_path')
    certification_path = metadata_path(certification_path, raw, 'certification_sha256', 'certification_path')
    require(aod['certification_sha256'] == raw['certification_sha256'], 'AOD/RAW certification differs')
    cert = json.loads(certification_path.read_text())
    checked = merge_shards(json.loads(aod_catalog_path.read_text()), cert, [aod],
                          catalog_sha256=sha256(aod_catalog_path), certification_sha256=sha256(certification_path),
                          process=aod['policy']['label'][2])
    require(checked['flag_census_closed'] and checked['original_identity_sha256'] == aod['original_identity_sha256']
            and checked['selected_identity_sha256'] == aod['selected_identity_sha256'], 'AOD flag census failed readback')
    build_full_lumi_plan(json.loads(raw_catalog_path.read_text()), raw, cert)
    plan = build_halo_plan(aod, raw, max_events_per_job)
    template = json.loads(template_path.read_text())
    science = {key: copy.deepcopy(template[key]) for key in SCIENCE_FIELDS}
    settings = copy.deepcopy(settings or {})
    require(set(settings) <= {'afs_allocation', 'native_timeout_seconds', 'job_max_runtime_seconds'},
            'Only explicit operational settings may replace worker template values')
    pins = dict(aod_census=pin(aod_path), raw_census=pin(raw_path), worker_template=pin(template_path),
                aod_catalog=pin(aod_catalog_path), raw_catalog=pin(raw_catalog_path), certification=pin(certification_path))
    if 'afs_allocation' in settings:
        allocation = settings['afs_allocation']
        require(sha256(allocation['path']) == allocation['sha256'], 'New AFS allocation changed')
        pins['afs_allocation'] = allocation
    for key in ('native_timeout_seconds', 'job_max_runtime_seconds'):
        require(key not in settings or type(settings[key]) is int and settings[key] > 0, 'Invalid worker time setting')
    directory.mkdir(mode=0o700, parents=True)
    census_path = directory / 'selected_header_census.json'
    selected_census = dict(plan['selected_census'], original_raw_census=pins['raw_census'],
                           original_aod_flag_census=pins['aod_census'])
    write_json(census_path, selected_census)
    pins['selected_census'] = pin(census_path)
    contract = {key: copy.deepcopy(plan[key]) for key in ('policy', 'policy_sha256', 'lumimask', 'datasets',
                  'aod_datasets', 'original_events', 'original_identity_sha256', 'selected_events',
                  'selected_identity_sha256', 'lumisections')}
    contract.update(schema='shift-data-beam-halo-exposure-contract-v1', preparation_only=True,
                    representation='halo_selected_responses', pins=pins, worker_science=science,
                    worker_settings=settings, qualification=dict(all_original_AOD_files_scanned_to_eof=True,
                    all_original_RAW_files_scanned_to_eof=True, flag_census_closed=True,
                    exact_AOD_RAW_identity_match=True, event_limit_applied=False))
    contract['cohort_sha256'] = digest({key: contract[key] for key in CONTRACT_FIELDS})
    references = []
    for job in plan['jobs']:
        work = directory / job['name']
        work.mkdir(mode=0o700)
        request = worker_request(template, job, selected_census, work, contract['cohort_sha256'],
                                 pins['selected_census']['sha256'], campaign_date, settings)
        request_path = work / 'request.json'
        write_json(request_path, request)
        references.append({key: job[key] for key in ('name', 'run', 'luminosityBlock', 'source_index',
                           'source_offset', 'expected_event_count', 'identity_sha256')}
                          | dict(directory=str(work), request_path=str(request_path), request_sha256=sha256(request_path)))
    contract['jobs'] = references
    validate_contract(contract)
    require(all(sha256(value['path']) == value['sha256'] for value in pins.values()), 'Preparation input changed')
    contract_path = directory / 'beam_halo_exposure_contract.json'
    write_json(contract_path, contract)
    collector_plan = dict(schema='shift-data-beam-halo-reconstruction-plan-v1', jobs=references,
                          event_budget=max(1, plan['selected_events']), selected_event_count=plan['selected_events'],
                          source_census_sha256=pins['selected_census']['sha256'],
                          exposure_contract=pin(contract_path), representation='halo_selected_responses', submitted=False)
    write_json(directory/'reconstruction_plan.json', collector_plan)
    return contract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('aod-census', 'raw-census', 'worker-template', 'output-dir'):
        parser.add_argument('--'+name, type=Path, required=True)
    for name in ('aod-catalog', 'raw-catalog', 'certification', 'afs-allocation'):
        parser.add_argument('--'+name, type=Path)
    parser.add_argument('--max-events-per-job', type=int, default=10)
    parser.add_argument('--campaign-date', default=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d'))
    parser.add_argument('--native-timeout-seconds', type=int)
    parser.add_argument('--job-max-runtime-seconds', type=int)
    args = parser.parse_args()
    os.umask(0o077)
    settings = {key: value for key, value in (
        ('native_timeout_seconds', args.native_timeout_seconds),
        ('job_max_runtime_seconds', args.job_max_runtime_seconds)) if value is not None}
    if args.afs_allocation:
        settings['afs_allocation'] = pin(args.afs_allocation)
    result = prepare(args.aod_census, args.raw_census, args.worker_template, args.output_dir,
                     aod_catalog_path=args.aod_catalog, raw_catalog_path=args.raw_catalog,
                     certification_path=args.certification, max_events_per_job=args.max_events_per_job,
                     campaign_date=args.campaign_date, settings=settings)
    print(json.dumps(dict(prepared_only=True, submitted=False, original_events=result['original_events'],
                          selected_events=result['selected_events'], jobs=len(result['jobs']),
                          contract=str(args.output_dir.resolve()/'beam_halo_exposure_contract.json'))))


if __name__ == '__main__':
    main()
