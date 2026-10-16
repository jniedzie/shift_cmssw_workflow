#!/usr/bin/env python3
# Code style: AI-code. AI-generated fixed-source streaming task preparation.
"""Discover published GEN, then freeze small RECO cohorts and closed previews.

This helper never submits jobs. Source identities and sampling are fixed by the
scientific source plan. It writes immutable tasks for the scheduler controller.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

from run_shift_stream_controller import read, sha
from shift_streaming_qcd_inputs import (STRATA, digest, select_events,
    source_descriptor, validate_plan, write_new)


def unchanged(path, value):
    """Permit preparation to resume, but never overwrite a previously frozen input."""
    path = Path(path)
    if path.exists():
        if read(path) != value:
            raise ValueError('Prepared streaming input changed: ' + str(path))
    else:
        write_new(path, value)
    return path


def done(ledger, identity):
    return ledger.get(identity, {}).get('phase') == 'complete'


def source_output(source, campaign):
    """Keep detector tiers under the source's existing process-bin directory."""
    return str(Path(source['gen']).parents[3] / campaign)


def make_task(root, policy, identity, stage, cohort_id, request, inputs,
              dependencies=(), checks=None, canary=False, matching=(),
              manifest_path=None, working_directory=None):
    path = Path(manifest_path) if manifest_path is not None else root / 'tasks' / (identity + '.json')
    if path.exists():
        return read(path)
    directory = (Path(working_directory) if working_directory is not None
                 else root / 'cohorts' / cohort_id / identity)
    directory.mkdir(parents=True, exist_ok=True)
    request_path = unchanged(directory / 'request.json', request)
    worker_policy = (policy.get('histogram_worker', policy['worker'])
                     if stage in ('histogram', 'merge') else policy['worker'])
    worker = Path(worker_policy['executable'])
    archive = Path(worker_policy['tools_archive'])
    if sha(archive) != worker_policy['tools_sha256']:
        raise ValueError('Frozen tools archive changed')
    if sha(worker) != worker_policy['executable_sha256']:
        raise ValueError('Frozen worker launcher changed')
    transfer = [request_path, archive] + [Path(name) for name in inputs]
    if len({name.name for name in transfer}) != len(transfer):
        raise ValueError('Task transfer inputs have colliding scratch basenames')
    if any(any(c.isspace() for c in str(name)) for name in transfer):
        raise ValueError('Task input paths must not contain whitespace')
    runtime = policy.get('worker_runtime_seconds', 32400)
    memory = policy.get('prepare_request_memory_mb', 2048) if stage == 'prepare' else policy.get('request_memory_mb', 6000)
    description = f'''# Code style: AI-code. AI-generated immutable streaming payload.
universe = vanilla
initialdir = {directory}
executable = {worker}
arguments = {sha(request_path)} {worker_policy['tools_sha256']} request.json
should_transfer_files = YES
when_to_transfer_output = ON_EXIT
transfer_input_files = {','.join(map(str, transfer))}
transfer_output_files = status.json,result.json,return_payload.tar.gz
getenv = False
notification = Never
request_cpus = 1
request_memory = {memory}
request_disk = {policy.get('request_disk_kb', 6000000)}
priority = {policy.get('worker_priority', 200000)}
+WantIOProxy = true
+MaxRuntime = {runtime}
+ShiftStreamStage = "{stage}"
+ShiftStreamCohort = "{cohort_id}"
+JobBatchName = "{policy['tag']}_{stage}"
output = worker.out
error = worker.err
log = events.log
on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)
periodic_hold = JobStatus == 2 && time() - JobCurrentStartDate > {runtime}
periodic_release = False
queue 1
'''
    submit = directory / 'worker.sub'
    submit.write_text(description)
    pins = {str(name): sha(name) for name in transfer + [worker, submit]}
    task = dict(schema='shift-stream-task-v1', task_id=identity, cohort_id=cohort_id,
        stage=stage, depends_on=list(dependencies), submit_file=str(submit),
        working_directory=str(directory), status_path=str(directory/'status.json'),
        result_path=str(directory/'result.json'), event_log=str(directory/'events.log'),
        input_sha256=pins, completion_checks={'complete': True, 'exit_code': 0,
            'cohort_id': cohort_id, 'stage': stage, **(checks or {})},
        matching_status_fields=list(matching), operational_canary=canary)
    unchanged(path, task)
    return task


def request(stage, cohort, environment, argv, result_path, policy, fields=None):
    return dict(schema='shift-stream-payload-v1', stage=stage, cohort_id=cohort,
        environment=environment, argv=argv, result_path=result_path,
        application_timeout_seconds=policy.get('application_timeouts', {}).get(stage,
            28800 if stage == 'reco' else 3600), status_fields=fields or {}, return_files=[])


def prepare_source(root, policy, plan, slot, ordinal, ledger):
    source_id, cohort = slot['source_id'], 'source_' + slot['source_id']
    directory = root / 'cohorts' / cohort
    directory.mkdir(parents=True, exist_ok=True)
    source_path = directory / 'source.json'
    if source_path.exists():
        source = read(source_path)
    else:
        source = source_descriptor(slot['receipt'], slot['manifest'])
        unchanged(source_path, source)
    canary = source_id in policy.get('canary_source_ids', [])
    tools = policy['tools']
    scan_id = source_id + '_scan'
    scan = make_task(root, policy, scan_id, 'prepare', cohort,
        request('prepare', cohort, 'cmssw', ['python3', tools['selection'], 'scan',
            '--source', 'source.json', '--output', 'scan.json'], 'scan.json', policy),
        [source_path], checks={'source_id': source_id, 'gen_sha256': source['gen_sha256']}, canary=True)
    if not done(ledger, scan_id):
        return
    selected_path = directory / 'selected.json'
    if selected_path.exists():
        selected = read(selected_path)
    else:
        selected = select_events(source, plan, read(scan['result_path']))
        base = source_output(source, policy['output_campaign'])
        selected.update(storage_contract='shift-process-bin-detector-v1',
                        nano_base=base+'/nanoAOD', metadata_base=base+'/metadata/reco',
                        maximum_publication_bytes=policy.get('worker_publication_bytes', 32*1024**2))
        unchanged(selected_path, selected)
    reco_ids, jobs = [], []
    if not selected['selected_events']:
        raise ValueError('Empty GEN source needs an explicit zero-entry histogram template; '
                         'its full source exposure must not be dropped: '+source_id)
    if len(selected['selected_events']) > 32:
        raise ValueError('A source can use at most 32 RECO job IDs: '+source_id)
    for index, event in enumerate(selected['selected_events']):
        identity = source_id + '_reco_' + str(index).zfill(3)
        reco_ids.append(identity)
        job = 10000 + ordinal * 32 + index
        jobs.append(job)
        event_sha = digest([event['event_id']])
        make_task(root, policy, identity, 'reco', cohort,
            request('reco', cohort, 'cmssw', ['python3', tools['reco'], 'selected.json',
                '--skip', str(index), '--count', '1', '--job', str(job),
                '--templates', tools['templates'], '--publish', '--absolute-stage-timeouts',
                '--stage-timeouts', policy.get('stage_timeouts', '7200,3600,7200,7200'),
                '--config-timeout', '600', '--event-stall-timeout', '3600',
                '--first-event-stall-timeout', '7200', '--remove-consumed-intermediates'],
                'report.json', policy), [selected_path], [scan_id],
            checks={'events': 1, 'source_descriptor_sha256': sha(selected_path),
                    'selected_event_ids_sha256': event_sha}, canary=canary,
            matching=[['nano_sha256', 'nano_readback_sha256']])
    if not reco_ids or not all(done(ledger, identity) for identity in reco_ids):
        return
    nano = []
    for identity, job in zip(reco_ids, jobs):
        task = read(root/'tasks'/(identity+'.json'))
        status = read(task['status_path'])
        nano.append(dict(job_id=job, path=status['nano_path'], sha256=status['nano_sha256'], events=status['events']))
    histogram_descriptor = dict(schema='shift-streaming-nano-cohort-v1', complete=True,
        cohort_id=cohort, stratum=selected['stratum'], selected_events=selected['selected_events'],
        expected_reco_job_ids=jobs, verified_nano=nano, normalization=selected['normalization'],
        source_descriptor_sha256=sha(selected_path), selection_plan_sha256=selected['sampling']['plan_sha256'],
        maximum_publication_bytes=policy.get('worker_publication_bytes', 32*1024**2))
    histogram_input = unchanged(directory/'nano_cohort.json', histogram_descriptor)
    base = source_output(source, policy['output_campaign'])
    argv = ['python3', tools['histograms'], 'cohort',
            '--descriptor', 'nano_cohort.json', '--output-directory', '@SCRATCH@/histograms',
            '--histogrammer', tools['histogrammer'], '--merge-script', tools['merger'],
            '--publish-directory', base+'/histograms/'+cohort, '--retire-consumed-nano']
    hist_request = request('histogram', cohort, 'tea', argv,
                           'histograms/cohort_complete.json', policy)
    if tools.get('plotter'):
        argv.extend(['--plotter', tools['plotter'], '--plot-output', '@SCRATCH@/histograms/plots'])
        hist_request['return_files'] = ['histograms/plots']
    make_task(root, policy, source_id+'_histogram', 'histogram', cohort, hist_request,
        [histogram_input], reco_ids, checks={'descriptor_sha256': sha(histogram_input)}, canary=canary)


def prepare_snapshots(root, policy, plan, ledger):
    """Only predetermined complete source-index prefixes can produce rate plots."""
    slots = plan['source_slots']
    maximum = min(sum(slot['stratum'] == stratum for slot in slots) for stratum in STRATA)
    # The final campaign has unequal source budgets (notably 500 and 145).
    # Its last snapshot must include those extra predetermined sources too.
    for prefix in list(range(1, maximum + 1)) + [None]:
        members = [slot for slot in slots if prefix is None or slot['request']['chunk_index'] < prefix]
        tasks = [slot['source_id']+'_histogram' for slot in members]
        if not all(done(ledger, identity) for identity in tasks):
            break
        identity = 'snapshot_complete_campaign' if prefix is None else 'snapshot_prefix_'+str(prefix).zfill(6)
        if (root/'tasks'/(identity+'.json')).exists():
            continue
        cohort = 'qcd_GEN_complete_production' if prefix is None else 'qcd_GEN_prefix_'+str(prefix).zfill(6)
        directory = root/'cohorts'/cohort
        directory.mkdir(parents=True, exist_ok=True)
        receipts, receipt_inputs = [], []
        for task_id in tasks:
            task = read(root/'tasks'/(task_id+'.json'))
            # Transfer compact receipts explicitly. Workers need no AFS read
            # permission and do not rely on scheduler paths inside scratch.
            receipt_path = unchanged(directory/(task_id+'_receipt.json'), read(task['result_path']))
            receipt_inputs.append(receipt_path)
            receipts.append(dict(path=receipt_path.name, sha256=sha(receipt_path)))
        inventory = dict(schema='shift-streaming-histogram-snapshot-inputs-v1', snapshot_id=cohort,
            expected_cohort_ids=['source_'+slot['source_id'] for slot in members], cohort_receipts=receipts)
        path = unchanged(directory/'snapshot.json', inventory)
        argv = ['python3', policy['tools']['histograms'], 'snapshot', '--inventory', 'snapshot.json',
            '--output-directory', '@SCRATCH@/snapshot', '--merge-script', policy['tools']['merger'],
            '--publish-directory', policy['snapshot_eos_base']+'/'+cohort]
        if policy['tools'].get('plotter'):
            argv.extend(['--plotter', policy['tools']['plotter'], '--plot-output', '@SCRATCH@/snapshot/plots'])
        payload = request('merge', cohort, 'tea', argv, 'snapshot/snapshot_complete.json', policy)
        if policy['tools'].get('plotter'):
            payload['return_files'] = ['snapshot/plots']
        make_task(root, policy, identity, 'merge', cohort,
            payload, [path]+receipt_inputs, tasks,
            checks={'inventory_sha256': sha(path)}, canary=False)


def publish_returned_plots(root, policy, ledger):
    """Publish only verified, regular PDF/JSON entries to the project plot tree."""
    plot_base = Path(policy['plot_output']).resolve()
    if not plot_base.is_absolute() or str(plot_base).startswith(('/tmp/', '/pool/condor/dir_')):
        raise ValueError('Final plots require a persistent project directory')
    for identity, state in ledger.items():
        if state.get('phase') != 'complete':
            continue
        task = read(root/'tasks'/(identity+'.json'))
        if task['stage'] not in ('histogram', 'merge'):
            continue
        directory = Path(task['working_directory'])
        receipt_path = directory/'plots_published.json'
        if receipt_path.exists():
            for row in read(receipt_path)['files']:
                if sha(row['path']) != row['sha256']:
                    raise ValueError('Published final plot changed or disappeared')
            continue
        request_data = read(directory/'request.json')
        if not request_data.get('return_files'):
            continue
        status = read(task['status_path'])
        archive_path = directory/'return_payload.tar.gz'
        if sha(archive_path) != status['return_payload_sha256']:
            raise ValueError('Returned plot archive differs from its worker status')
        prefix = 'histograms/plots' if task['stage'] == 'histogram' else 'snapshot/plots'
        published, total_bytes = [], 0
        with tarfile.open(archive_path, 'r:gz') as archive:
            entries = []
            names = set()
            for member in archive.getmembers():
                name = Path(member.name)
                if member.isdir():
                    continue
                if (not member.isfile() or name.is_absolute() or '..' in name.parts
                        or name.suffix.lower() not in ('.pdf', '.json')):
                    raise ValueError('Returned plot archive contains an unsafe entry')
                try:
                    relative = name.relative_to(prefix)
                except ValueError as error:
                    raise ValueError('Returned artifact is outside its declared plot directory') from error
                if str(relative) in names:
                    raise ValueError('Returned plot archive has duplicate paths')
                names.add(str(relative))
                target = (plot_base/task['cohort_id']/relative).resolve()
                if plot_base not in target.parents:
                    raise ValueError('Returned plot escapes its project directory')
                total_bytes += member.size
                if total_bytes > 100 * 1024**2:
                    raise ValueError('Returned plot archive exceeds its reviewed 100 MiB bound')
                entries.append((member, target))
            for member, target in entries:
                contents = archive.extractfile(member).read()
                checksum = hashlib.sha256(contents).hexdigest()
                if target.exists():
                    if sha(target) != checksum:
                        raise ValueError('Final plot already exists with different contents')
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('xb') as stream:
                        stream.write(contents)
                if sha(target) != checksum:
                    raise ValueError('Final project plot readback differs')
                published.append(dict(path=str(target), bytes=len(contents), sha256=checksum))
        if not any(Path(row['path']).suffix.lower() == '.pdf' for row in published):
            raise ValueError('Plot-producing task returned no final PDF')
        unchanged(receipt_path, dict(schema='shift-stream-project-plot-publication-v1', complete=True,
            task_id=identity, worker_archive_sha256=status['return_payload_sha256'], files=published,
            removed_duplicate_archive_bytes=archive_path.stat().st_size))
        archive_path.unlink()  # The project PDFs plus this receipt retain the result.


def complete_operational_gate(root, policy, plan, ledger):
    """Arm bulk only after the real detached canary reaches published PDFs."""
    path = root/'operational_gates/canary.json'
    if path.exists():
        return
    designated = policy.get('canary_source_ids', [])
    if not designated:
        return
    proofs = []
    for source_id in designated:
        cohort = 'source_'+source_id
        selected_path = root/'cohorts'/cohort/'selected.json'
        if not selected_path.exists():
            return
        source = read(selected_path)
        identities = [source_id+'_scan', source_id+'_histogram'] + [
            source_id+'_reco_'+str(i).zfill(3) for i in range(len(source['selected_events']))]
        for identity in identities:
            record = ledger.get(identity, {})
            if (record.get('phase') != 'complete' or record.get('execution_verified') is not True
                    or record.get('native_exit', {}).get('terminal') != 0
                    or sha(record['status_path']) != record['status_sha256']):
                return
            proofs.append(dict(task_id=identity, job=record['job'],
                status_path=record['status_path'], status_sha256=record['status_sha256']))
        histogram = read(root/'tasks'/(source_id+'_histogram.json'))
        figures = Path(histogram['working_directory'])/'plots_published.json'
        if not figures.exists():
            return
        publication = read(figures)
        if (publication.get('complete') is not True or not publication['files']
                or any(sha(row['path']) != row['sha256'] for row in publication['files'])):
            return
        result = read(histogram['result_path'])
        if not result.get('complete') or not result.get('merged_nano', {}).get('path', '').startswith('/eos/user/'):
            return
    unchanged(path, dict(schema='shift-stream-operational-gate-v1', passed=True,
        authorities=dict(tools_sha256=policy['worker']['tools_sha256'],
            histogram_tools_sha256=policy.get('histogram_worker', policy['worker'])['tools_sha256'],
            source_plan_sha256=plan['plan_sha256'], source_plan_file_sha256=sha(root/'source_plan.json')),
        checks=dict(native_submission_passed=True, reco_and_histogram_passed=True, plots_published=True),
        canary_source_ids=designated, native_task_proofs=proofs,
        analysis_release=False, normalization_ready=False,
        note='Operational pipeline canary only; physics response and statistical sufficiency remain separate.'))


def prepare(root, policy_path=None):
    root = Path(root).resolve()
    policy, plan = read(root/'factory_policy.json'), read(root/'source_plan.json')
    if policy_path is not None:
        replacement = read(policy_path)
        unchanged_policy = dict(replacement)
        unchanged_policy.pop('histogram_worker', None)
        original = dict(policy)
        original.pop('histogram_worker', None)
        if unchanged_policy != original:
            raise ValueError('Replacement factory policy may change only the histogram worker')
        policy = replacement
    validate_plan(plan)
    ledger = read(root/'ledger.json') if (root/'ledger.json').exists() else {}
    publish_returned_plots(root, policy, ledger)
    complete_operational_gate(root, policy, plan, ledger)
    ordered = sorted(enumerate(plan['source_slots']),
        key=lambda pair: (pair[1]['request']['chunk_index'], STRATA.index(pair[1]['stratum'])))
    scan_complete = any(done(ledger,slot['source_id']+'_scan') for slot in plan['source_slots'])
    open_limit = policy.get('maximum_open_sources', 6) if scan_complete else policy.get(
        'maximum_open_sources_before_first_scan', 1)
    active = {slot['source_id'] for slot in plan['source_slots']
        if (root/'cohorts'/('source_'+slot['source_id'])/'source.json').exists()
        and not done(ledger, slot['source_id']+'_histogram')}
    open_sources = len(active)
    for ordinal, slot in ordered:
        identity = slot['source_id']
        if done(ledger, identity+'_histogram'):
            continue
        directory = root/'cohorts'/('source_'+identity)
        already_open = identity in active
        if not Path(slot['receipt']).exists():
            continue
        if not already_open and open_sources >= open_limit:
            continue
        if not already_open:
            open_sources += 1
        prepare_source(root, policy, plan, slot, ordinal, ledger)
    prepare_snapshots(root, policy, plan, ledger)
    return {'schema': 'shift-stream-preparation-checkpoint-v1', 'open_sources': open_sources,
            'planned_sources': len(plan['source_slots']), 'plan_sha256': plan['plan_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--policy', type=Path,
        help='Frozen replacement policy; only histogram_worker may differ')
    args = parser.parse_args()
    print(json.dumps(prepare(args.directory, args.policy)))


if __name__ == '__main__':
    main()
