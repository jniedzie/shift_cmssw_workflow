#!/usr/bin/env python3
# Code style: AI-code. AI-generated fixed-source streaming task preparation.
"""Discover published GEN, then freeze small RECO cohorts and closed previews.

This helper never submits jobs. Source identities and sampling are fixed by the
scientific source plan. It writes immutable tasks for the scheduler controller.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import tarfile
import time

from run_shift_stream_controller import read, sha
from shift_streaming_qcd_inputs import (STRATA, digest, select_events,
    source_descriptor, validate_plan, validate_policy, write_new)


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


def immutable_worker_checksum(path, checked):
    """Hash a shared frozen runtime once per preparation, rejecting any drift."""
    path = Path(path)
    def signature():
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    before = signature()
    previous = checked.get(str(path))
    if previous is not None:
        if previous[0] != before:
            raise ValueError('Frozen worker input changed during task preparation: ' + str(path))
        return previous[1]
    checksum = sha(path)
    if signature() != before:
        raise ValueError('Frozen worker input changed while hashing: ' + str(path))
    checked[str(path)] = (before, checksum)
    return checksum


def source_output(source, campaign):
    """Keep detector tiers under the source's existing process-bin directory."""
    return str(Path(source['gen']).parents[3] / campaign)


def publication_bound(policy, stage):
    bounds = policy.get('stage_publication_bounds', {})
    if not isinstance(bounds, dict) or set(bounds) - {'histogram', 'merge'}:
        raise ValueError('Publication overrides may name only histogram and merge stages')
    bound = bounds.get(stage, policy.get('worker_publication_bytes', 32*1024**2))
    if type(bound) is not int or not 65536 < bound <= 512*1024**2:
        raise ValueError('Publication bounds need an explicit integer up to 512 MiB')
    return bound


def reco_batches(selected, policy, ordinal):
    """Partition one frozen selection into disjoint, auditable detector jobs.

    Defaults preserve the original one-event pass and its job IDs. A bulk pass
    must declare its wider job stride before any selected source is prepared.
    """
    size = policy.get('reco_events_per_job', 1)
    stride = policy.get('reco_job_stride', 32)
    offset = policy.get('reco_job_offset', 10000)
    if type(size) is not int or not 1 <= size <= 50:
        raise ValueError('RECO batch size must be an integer between 1 and 50')
    if type(stride) is not int or stride < 1 or type(offset) is not int or offset < 0:
        raise ValueError('RECO job stride and offset need nonnegative, distinct integer slots')
    rows = selected['selected_events']
    number = math.ceil(len(rows) / size)
    if number > stride:
        raise ValueError('Selected source exceeds its frozen RECO job stride: ' + selected['source_id'])
    batches = []
    for index, skip in enumerate(range(0, len(rows), size)):
        job = offset + ordinal * stride + index
        # run_shift_gen_to_nano assigns seed=230000000+job*100, then stage
        # offsets. Keep the whole assignment within the CMSSW/Pythia range.
        if 230000000 + job * 100 + 1000 >= 900000000:
            raise ValueError('RECO job allocation exceeds the supported random-seed range')
        members = rows[skip:skip + size]
        batches.append(dict(task_id=selected['source_id'] + '_reco_' + str(index).zfill(3),
            job=job, skip=skip, count=len(members), event_ids=[row['event_id'] for row in members]))
    return batches


def worker_host_requirements(policy):
    """Exclude a small explicit list of reviewed failing CERN workers."""
    if 'excluded_worker_hosts' not in policy:
        return ''
    hosts = policy['excluded_worker_hosts']
    if not isinstance(hosts, list) or not 1 <= len(hosts) <= 8:
        raise ValueError('excluded_worker_hosts needs one to eight explicit CERN hosts')
    hostname = r'(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+cern\.ch'
    if any(not isinstance(host, str) or len(host) > 253
           or not re.fullmatch(hostname, host) for host in hosts):
        raise ValueError('excluded_worker_hosts needs fully qualified CERN host names')
    if len({host.lower() for host in hosts}) != len(hosts):
        raise ValueError('excluded_worker_hosts must not contain duplicate hosts')
    return 'requirements = ' + ' && '.join(
        '(TARGET.Machine =!= ' + json.dumps(host) + ')' for host in hosts) + '\n'


def make_task(root, policy, identity, stage, cohort_id, request, inputs,
              dependencies=(), checks=None, canary=False, matching=(),
              manifest_path=None, working_directory=None, priority=None, worker_checksums=None):
    host_requirements = worker_host_requirements(policy)
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
    checked = {} if worker_checksums is None else worker_checksums
    frozen_checksums = {str(path): immutable_worker_checksum(path, checked) for path in (archive, worker)}
    if frozen_checksums[str(archive)] != worker_policy['tools_sha256']:
        raise ValueError('Frozen tools archive changed')
    if frozen_checksums[str(worker)] != worker_policy['executable_sha256']:
        raise ValueError('Frozen worker launcher changed')
    transfer = [request_path, archive] + [Path(name) for name in inputs]
    if len({name.name for name in transfer}) != len(transfer):
        raise ValueError('Task transfer inputs have colliding scratch basenames')
    if any(any(c.isspace() for c in str(name)) for name in transfer):
        raise ValueError('Task input paths must not contain whitespace')
    runtime = policy.get('worker_runtime_seconds', 32400)
    memory = policy.get('prepare_request_memory_mb', 2048) if stage == 'prepare' else policy.get('request_memory_mb', 6000)
    stage_priorities = policy.get('stage_worker_priorities', {})
    if (not isinstance(stage_priorities, dict) or set(stage_priorities) - {'histogram', 'merge'}
            or any(type(value) is not int or not -2147483648 <= value <= 2147483647
                   for value in stage_priorities.values())):
        raise ValueError('Histogram/merge priorities need native signed integers')
    worker_priority = stage_priorities.get(stage, policy.get('worker_priority', 200000)) + (priority or 0)
    if type(worker_priority) is not int or not -2147483648 <= worker_priority <= 2147483647:
        raise ValueError('Worker priority exceeds the native signed-integer range')
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
{host_requirements}priority = {worker_priority}
+WantIOProxy = true
+SendCredential = True
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
    pins = {str(name): frozen_checksums[str(name)] if str(name) in frozen_checksums else sha(name)
            for name in transfer + [worker, submit]}
    task = dict(schema='shift-stream-task-v1', task_id=identity, cohort_id=cohort_id,
        stage=stage, depends_on=list(dependencies), submit_file=str(submit),
        working_directory=str(directory), status_path=str(directory/'status.json'),
        result_path=str(directory/'result.json'), event_log=str(directory/'events.log'),
        input_sha256=pins, completion_checks={'complete': True, 'exit_code': 0,
            'cohort_id': cohort_id, 'stage': stage, **(checks or {})},
        matching_status_fields=list(matching), operational_canary=canary)
    if policy.get('stage_publication_bounds'):
        task['publication_reservation_bytes'] = publication_bound(policy, stage)
    if priority is not None:
        task['priority'] = priority
    unchanged(path, task)
    return task


def request(stage, cohort, environment, argv, result_path, policy, fields=None):
    return dict(schema='shift-stream-payload-v1', stage=stage, cohort_id=cohort,
        environment=environment, argv=argv, result_path=result_path,
        application_timeout_seconds=policy.get('application_timeouts', {}).get(stage,
            28800 if stage == 'reco' else 3600), status_fields=fields or {}, return_files=[])


def prepare_source(root, policy, plan, slot, ordinal, ledger, worker_checksums=None):
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
        [source_path], checks={'source_id': source_id, 'gen_sha256': source['gen_sha256']}, canary=True,
        worker_checksums=worker_checksums)
    if not done(ledger, scan_id):
        return
    selected_path = directory / 'selected.json'
    if selected_path.exists():
        selected = read(selected_path)
    else:
        selection_plan = plan
        if policy.get('selection_policy'):
            selection_plan = dict(plan, selection_policy=policy['selection_policy'],
                                  policy_sha256=digest(policy['selection_policy']))
            selection_plan.pop('plan_sha256')
            selection_plan['plan_sha256'] = digest(selection_plan)
        selected = select_events(source, selection_plan, read(scan['result_path']))
        base = source_output(source, policy['output_campaign'])
        selected.update(storage_contract='shift-process-bin-detector-v1',
                        nano_base=base+'/nanoAOD', metadata_base=base+'/metadata/reco',
                        maximum_publication_bytes=publication_bound(policy, 'reco'))
        unchanged(selected_path, selected)
    reco_ids, jobs = [], []
    if not selected['selected_events']:
        raise ValueError('Empty GEN source needs an explicit zero-entry histogram template; '
                         'its full source exposure must not be dropped: '+source_id)
    for batch in reco_batches(selected, policy, ordinal):
        identity = batch['task_id']
        reco_ids.append(identity)
        job = batch['job']
        jobs.append(job)
        event_sha = digest(batch['event_ids'])
        priority = None
        if policy.get('prioritize_GEN_strata', False):
            # Priority changes execution order only. Selection and inclusion
            # probabilities remain fixed, and histogram closure still waits
            # for the whole declared source, including its control events.
            rows = selected['selected_events'][batch['skip']:batch['skip'] + batch['count']]
            rank = max({'tail': 2, 'forward_OS': 1, 'remainder': 0}[row['feature_stratum']]
                       for row in rows)
            source_rank = slot['request']['chunk_index'] * len(STRATA) + STRATA.index(slot['stratum'])
            priority = rank * 10000000 - source_rank * 100 - batch['skip'] // batch['count']
        make_task(root, policy, identity, 'reco', cohort,
            request('reco', cohort, 'cmssw', ['python3', tools['reco'], 'selected.json',
                '--skip', str(batch['skip']), '--count', str(batch['count']), '--job', str(job),
                '--templates', tools['templates'], '--publish', '--absolute-stage-timeouts',
                '--stage-timeouts', policy.get('stage_timeouts', '7200,3600,7200,7200'),
                '--config-timeout', '600', '--event-stall-timeout', '3600',
                '--first-event-stall-timeout', '7200', '--remove-consumed-intermediates'],
                'report.json', policy), [selected_path], [scan_id],
            checks={'events': batch['count'], 'source_descriptor_sha256': sha(selected_path),
                    'selected_event_ids_sha256': event_sha}, canary=canary,
            matching=[['nano_sha256', 'nano_readback_sha256']], priority=priority,
            worker_checksums=worker_checksums)
    if not reco_ids or not all(done(ledger, identity) for identity in reco_ids):
        return
    if (root/'tasks'/(source_id+'_histogram.json')).exists():
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
        maximum_publication_bytes=publication_bound(policy, 'histogram'))
    histogram_input = unchanged(directory/'nano_cohort.json', histogram_descriptor)
    base = source_output(source, policy['output_campaign'])
    argv = ['python3', tools['histograms'], 'cohort',
            '--descriptor', 'nano_cohort.json', '--output-directory', '@SCRATCH@/histograms',
            '--histogrammer', tools['histogrammer'], '--merge-script', tools['merger'],
            '--publish-directory', base+'/histograms/'+cohort, '--retire-consumed-nano']
    if tools.get('nano_integrity_runtime'):
        runtime = Path(tools['nano_integrity_runtime'])
        if not runtime.is_absolute() or not str(runtime).startswith('/cvmfs/cms.cern.ch/'):
            raise ValueError('Nano integrity runtime must be an explicit stock CMSSW CVMFS path')
        argv.extend(['--nano-integrity-runtime', str(runtime)])
    hist_request = request('histogram', cohort, 'tea', argv,
                           'histograms/cohort_complete.json', policy)
    if tools.get('plotter') and policy.get('plot_source_histograms', True):
        argv.extend(['--plotter', tools['plotter'], '--plot-output', '@SCRATCH@/histograms/plots'])
        hist_request['return_files'] = ['histograms/plots']
    make_task(root, policy, source_id+'_histogram', 'histogram', cohort, hist_request,
        [histogram_input], reco_ids, checks={'descriptor_sha256': sha(histogram_input)}, canary=canary,
        worker_checksums=worker_checksums)


def prepare_snapshots(root, policy, plan, ledger, worker_checksums=None):
    """Periodically merge every completed source, independently across pThat bins."""
    if any(identity.startswith('snapshot_ready_') and state['phase'] not in ('complete', 'failed')
           for identity, state in ledger.items()):
        return
    members = [slot for slot in plan['source_slots']
               if done(ledger, slot['source_id']+'_histogram')]
    if not members:
        return
    tasks = [slot['source_id']+'_histogram' for slot in members]
    cohorts = ['source_'+slot['source_id'] for slot in members]
    events_by_stratum = {}
    for slot, identity in zip(members, tasks):
        task = read(root/'tasks'/(identity+'.json'))
        result = read(task['result_path'])
        count = result.get('nano_audit', {}).get('events', 0)
        events_by_stratum[slot['stratum']] = events_by_stratum.get(slot['stratum'], 0) + count
    latest_path = Path(policy['plot_output'])/'latest_ready.json'
    previous = read(latest_path) if latest_path.exists() else {}
    if set(cohorts).issubset(set(previous.get('closed_cohort_ids', []))):
        return
    now = time.time()
    elapsed = now - previous.get('merged_at_epoch', 0)
    if previous and elapsed < policy.get('merge_min_interval_seconds', 1800):
        return
    old_counts = previous.get('events_by_stratum', {})
    meaningful = any(count - old_counts.get(name, 0) >= policy.get('merge_min_new_events', 100)
                     and count >= old_counts.get(name, 0)*(1 + policy.get('merge_min_growth', .2))
                     for name, count in events_by_stratum.items())
    if previous and not meaningful and elapsed < policy.get('merge_max_interval_seconds', 21600):
        return
    signature = digest(tasks)[:12]
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))
    merge_tag = stamp + '_' + signature
    identity = 'snapshot_ready_'+signature
    if (root/'tasks'/(identity+'.json')).exists():
        return
    cohort = 'qcd_ready_'+merge_tag
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
        expected_cohort_ids=['source_'+slot['source_id'] for slot in members], cohort_receipts=receipts,
        maximum_publication_bytes=publication_bound(policy, 'merge'),
        events_by_stratum=events_by_stratum, frozen_at_epoch=now, merge_tag=merge_tag)
    path = unchanged(directory/'snapshot.json', inventory)
    argv = ['python3', policy['tools']['histograms'], 'snapshot', '--inventory', 'snapshot.json',
        '--output-directory', '@SCRATCH@/snapshot', '--merge-script', policy['tools']['merger'],
        '--publish-directory', policy['snapshot_eos_base']+'/'+cohort]
    if policy.get('campaign_base_path'):
        argv.extend(['--campaign-base-path', policy['campaign_base_path'],
                     '--campaign', policy['output_campaign']])
    reference_inputs = policy.get('reference_histogram_inputs')
    if reference_inputs:
        reference_path = Path(reference_inputs)
        if not reference_path.is_absolute() or not reference_path.is_file():
            raise ValueError('Reference histogram inputs need an existing immutable absolute path')
        if sha(reference_path) != policy.get('reference_histogram_inputs_sha256'):
            raise ValueError('Reference histogram inventory differs from its frozen checksum')
        receipt_inputs.append(reference_path)
        argv.extend(['--reference-inputs', reference_path.name])
    if policy['tools'].get('plotter') and policy.get('render_snapshot_plots', False):
        argv.extend(['--plotter', policy['tools']['plotter'], '--plot-output', '@SCRATCH@/snapshot/plots'])
    payload = request('merge', cohort, 'tea', argv, 'snapshot/snapshot_complete.json', policy)
    if policy['tools'].get('plotter') and policy.get('render_snapshot_plots', False):
        payload['return_files'] = ['snapshot/plots']
    make_task(root, policy, identity, 'merge', cohort,
        payload, [path]+receipt_inputs, tasks,
        checks={'inventory_sha256': sha(path)}, canary=False, worker_checksums=worker_checksums)


def publish_returned_plots(root, policy, ledger):
    """Publish only verified, regular PDF/JSON entries to the project plot tree."""
    plot_base = Path(policy['plot_output']).resolve()
    if not plot_base.is_absolute() or str(plot_base).startswith(('/tmp/', '/pool/condor/dir_')):
        raise ValueError('Final plots require a persistent project directory')

    def publish_snapshot_inputs(task, figures):
        if task['stage'] != 'merge':
            return
        result = read(task['result_path'])
        if not result.get('complete') or not result.get('plot_inputs'):
            raise ValueError('Completed snapshot lacks its plot input publication')
        record = result.get('plot_inputs_record')
        if record is None:
            # Old workers returned the checked samples but omitted the JSON.
            record = dict(schema='shift-streaming-plot-inputs-v1', samples=result['samples'],
                          snapshot_id=result['snapshot_id'], partial_production=True)
        if (record.get('schema') != 'shift-streaming-plot-inputs-v1' or
                record.get('snapshot_id') != result['snapshot_id'] or
                record.get('samples') != result['samples']):
            raise ValueError('Snapshot plot input records disagree')
        for sample in record['samples'] + record.get('reference_samples', []):
            for name in ('path', 'raw_path'):
                if name in sample and not sample[name].startswith('/eos/user/'):
                    raise ValueError('Snapshot plot inputs must use durable EOS payloads')
        contents = (json.dumps(record, indent=2, allow_nan=False) + '\n').encode()
        checksum = hashlib.sha256(contents).hexdigest()
        if checksum != result['plot_inputs']['sha256'] or len(contents) != result['plot_inputs']['bytes']:
            raise ValueError('Snapshot plot input readback differs from its worker checksum')
        target = plot_base/task['cohort_id']/'plot_inputs.json'
        if target.exists():
            if sha(target) != checksum:
                raise ValueError('Published snapshot inputs changed')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(contents)
        if sha(target) != checksum:
            raise ValueError('Project snapshot inputs differ after publication')
        # The alias advances only after the complete snapshot's final PDFs and
        # checked histogram references have reached the project directory.
        latest = plot_base/'latest_ready.json'
        count = len(result.get('closed_cohort_ids', []))
        if latest.exists():
            previous = read(latest)
            if (previous.get('seed_from_previous_production') is not True and
                    previous.get('snapshot_cohort_count', 0) > count):
                return
        ready = dict(record, snapshot_cohort_count=count,
                     closed_cohort_ids=result.get('closed_cohort_ids', []),
                     events_by_stratum=result.get('events_by_stratum', {}),
                     merged_at_epoch=result.get('merged_at_epoch', 0),
                     ready_plot_inputs=dict(path=str(target), sha256=checksum, bytes=len(contents)),
                     plot_publication=figures)
        temporary = latest.with_name('.latest_ready_' + task['cohort_id'] + '.json')
        temporary.write_text(json.dumps(ready, indent=2, allow_nan=False) + '\n')
        temporary.replace(latest)

    for identity, state in ledger.items():
        if state.get('phase') != 'complete':
            continue
        task = read(root/'tasks'/(identity+'.json'))
        if task['stage'] not in ('histogram', 'merge'):
            continue
        directory = Path(task['working_directory'])
        receipt_path = directory/'plots_published.json'
        if receipt_path.exists():
            figures = read(receipt_path)['files']
            for row in figures:
                if sha(row['path']) != row['sha256']:
                    raise ValueError('Published final plot changed or disappeared')
            publish_snapshot_inputs(task, figures)
            continue
        request_data = read(directory/'request.json')
        if not request_data.get('return_files'):
            if task['stage'] == 'merge':
                publish_snapshot_inputs(task, [])
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
        publish_snapshot_inputs(task, published)
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
            batch['task_id'] for batch in reco_batches(source, policy, 0)]
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
        allowed = ('histogram_worker', 'maximum_open_sources', 'selection_policy',
                   'merge_min_interval_seconds', 'merge_max_interval_seconds',
                   'merge_min_new_events', 'merge_min_growth', 'plot_source_histograms',
                   'render_snapshot_plots', 'campaign_base_path')
        original = dict(policy)
        for key in allowed:
            unchanged_policy.pop(key, None)
            original.pop(key, None)
        if unchanged_policy != original:
            raise ValueError('Replacement factory policy changed an undeclared detector, source or output setting')
        policy = replacement
    if type(policy.get('maximum_open_sources', 6)) is not int or policy.get('maximum_open_sources', 6) <= 0:
        raise ValueError('The open-source limit needs a positive integer')
    if type(policy.get('produce_snapshots', True)) is not bool:
        raise ValueError('produce_snapshots needs an explicit boolean')
    for key in ('merge_min_interval_seconds', 'merge_max_interval_seconds', 'merge_min_new_events'):
        if key in policy and (type(policy[key]) is not int or policy[key] <= 0):
            raise ValueError(key + ' needs a positive integer')
    growth = policy.get('merge_min_growth', .2)
    if type(growth) not in (int, float) or not math.isfinite(growth) or growth <= 0:
        raise ValueError('merge_min_growth needs a finite positive fraction')
    if 'selection_policy' in policy:
        validate_policy(policy['selection_policy'])
    for key in ('plot_source_histograms', 'render_snapshot_plots'):
        if key in policy and type(policy[key]) is not bool:
            raise ValueError(key + ' needs an explicit boolean')
    worker_host_requirements(policy)
    validate_plan(plan)
    ledger = read(root/'ledger.json') if (root/'ledger.json').exists() else {}
    publish_returned_plots(root, policy, ledger)
    complete_operational_gate(root, policy, plan, ledger)
    ordered = sorted(enumerate(plan['source_slots']),
        key=lambda pair: (pair[1]['request']['chunk_index'], STRATA.index(pair[1]['stratum'])))
    scan_complete = any(done(ledger,slot['source_id']+'_scan') for slot in plan['source_slots'])
    open_limit = policy.get('maximum_open_sources', 6) if scan_complete else policy.get(
        'maximum_open_sources_before_first_scan', 1)
    # A quarantined source cannot close its histogram. Keep its failures and
    # reservations intact, but stop preparing it or charging an open-source slot.
    quarantined = {read(root/'tasks'/(identity+'.json'))['cohort_id']
        for identity, record in ledger.items()
        if record.get('phase') == 'failed' and record.get('failure_scope') == 'cohort'}
    active = {slot['source_id'] for slot in plan['source_slots']
        if (root/'cohorts'/('source_'+slot['source_id'])/'source.json').exists()
        and 'source_'+slot['source_id'] not in quarantined
        and not done(ledger, slot['source_id']+'_histogram')}
    open_sources = len(active)
    worker_checksums = {}
    for ordinal, slot in ordered:
        identity = slot['source_id']
        if done(ledger, identity+'_histogram') or 'source_'+identity in quarantined:
            continue
        directory = root/'cohorts'/('source_'+identity)
        already_open = identity in active
        if not Path(slot['receipt']).exists():
            continue
        if not already_open and open_sources >= open_limit:
            continue
        if not already_open:
            open_sources += 1
        prepare_source(root, policy, plan, slot, ordinal, ledger, worker_checksums=worker_checksums)
    if policy.get('produce_snapshots', True):
        prepare_snapshots(root, policy, plan, ledger, worker_checksums=worker_checksums)
    return {'schema': 'shift-stream-preparation-checkpoint-v1', 'open_sources': open_sources,
            'planned_sources': len(plan['source_slots']), 'plan_sha256': plan['plan_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--policy', type=Path,
        help='Frozen replacement policy; only histogram_worker and maximum_open_sources may differ')
    args = parser.parse_args()
    print(json.dumps(prepare(args.directory, args.policy)))


if __name__ == '__main__':
    main()
