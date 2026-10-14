#!/usr/bin/env python3
# Code style: AI-code. AI-generated reusable grouped signal production support.
"""Validate bounded publication and merge complete, disjoint signal groups.

This module is copied into a new immutable deployment. Existing deployments
keep their original workers, finalizers, receipts and publication contracts.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tarfile


PROFILES = {
    'all-tiers-v1': ('GEN_SIM', 'DIGI_RAW_HLT', 'AODSIM', 'nanoAOD'),
    'nano-metadata-v1': ('nanoAOD',),
}


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def valid_digest(value):
    return (isinstance(value, str) and len(value) == 64 and
            all(character in '0123456789abcdef' for character in value))


def valid_artifact(item, path):
    return (isinstance(item, dict) and item.get('path') == path and
            type(item.get('bytes')) is int and item['bytes'] > 0 and valid_digest(item.get('sha256')))


def validate_complete_groups(row, expected):
    """Require an exact ordered partition, with no point or group overlap."""
    count = row['events']
    if (type(count) is not int or count < 1 or len(expected) != count or
            len(set(map(tuple, expected))) != count):
        raise ValueError('Full GEN population is incomplete or duplicated')
    chunks = row['chunks']
    by_id, offset, paths, jobs = {}, 0, set(), set()
    for chunk in chunks:
        number, size = chunk['chunk'], chunk['count']
        if (type(number) is not int or number < 0 or number in by_id or
                type(size) is not int or not 1 <= size <= 20 or chunk['skip'] != offset or
                type(chunk['job']) is not int or chunk['job'] in jobs or
                chunk['nano'] in paths):
            raise ValueError('Detector chunks do not exactly partition ordered GEN events')
        by_id[number] = chunk
        offset += size
        paths.add(chunk['nano'])
        jobs.add(chunk['job'])
    if offset != count:
        raise ValueError('Detector chunks omit full GEN events')
    groups, offset, covered = row.get('groups', []), 0, []
    if not groups:
        raise ValueError('Complete point has no bounded Nano groups')
    for number, group in enumerate(groups):
        size = group['count']
        if (group.get('group') != number or group.get('skip') != offset or
                type(size) is not int or size < 1 or size > row.get('group_events', 2000) or
                group.get('nano') != row['final_output_base'] +
                f'/nanoAOD_merged_unscored/ntuple_{number}.root'):
            raise ValueError('Nano groups are not a bounded ordered same-point partition')
        selected = [chunk['chunk'] for chunk in chunks
                    if offset <= chunk['skip'] < offset + size]
        if (group.get('chunks') != selected or not selected or
                sum(by_id[index]['count'] for index in selected) != size or
                any(by_id[index]['skip'] + by_id[index]['count'] > offset + size for index in selected)):
            raise ValueError('Nano groups overlap, omit chunks, or cut a detector chunk')
        covered.extend(selected)
        offset += size
    if offset != count or covered != [chunk['chunk'] for chunk in chunks]:
        raise ValueError('Nano groups omit or duplicate full GEN events')
    return groups


def publication_paths(chunk, profile):
    if profile not in PROFILES:
        raise ValueError('Unknown signal publication profile')
    base = Path(chunk['nano']).parent.parent
    name = f'chunk_{chunk["chunk"]:03d}.root'
    paths = {tier: str(base / tier / name) for tier in PROFILES[profile]}
    if paths['nanoAOD'] != chunk['nano'] or chunk['metadata'] != str(base / 'metadata' / name[:-5]):
        raise ValueError('Chunk output namespace differs from its stable identity')
    paths['metadata'] = chunk['metadata'] + '/metadata.tar.gz'
    return paths


def validate_publication_canary(path, budget, maximum_chunk_events):
    """Verify measured Nano/metadata byte bounds from an audited full-chain job."""
    path = Path(path).resolve()
    proof = read(path)
    if (proof.get('schema') != 'shift-dark-photon-publication-canary-v1' or
            proof.get('complete') is not True or proof.get('publication_profile') != 'nano-metadata-v1' or
            type(proof.get('tested_chunk_events')) is not int or
            proof['tested_chunk_events'] < maximum_chunk_events):
        raise ValueError('Nano publication needs a complete canary covering the largest chunk')
    status_path, seal_path = Path(proof['source_status_path']), Path(proof['source_seal_path'])
    if (not status_path.is_absolute() or not seal_path.is_absolute() or
            sha256(status_path) != proof['source_status_sha256'] or
            sha256(seal_path) != proof['source_seal_sha256']):
        raise ValueError('Publication canary source authority changed')
    status, seal = read(status_path), read(seal_path)
    if (status.get('complete') is not True or status.get('events') != proof['tested_chunk_events'] or
            status.get('native_sumw') != status['events'] or status.get('selection_applied') is not False or
            status.get('seal_sha256') != proof['source_seal_sha256']):
        raise ValueError('Publication canary is not a complete unfiltered detector audit')
    deployment = seal_path.parent
    plan_path = deployment / 'plan.json'
    if (status.get('plan_sha256') != sha256(plan_path) or
            seal.get('files', {}).get(str(plan_path)) != sha256(plan_path)):
        raise ValueError('Publication canary plan authority changed')
    expected = status.get('expected_event_ids', [])
    if len(expected) != status['events'] or len(set(map(tuple, expected))) != len(expected):
        raise ValueError('Publication canary event identities are incomplete')
    artifacts = proof.get('retained_artifacts', {})
    if set(artifacts) != {'nanoAOD', 'metadata'}:
        raise ValueError('Publication canary must measure Nano and complete metadata')
    dependencies = [path, status_path, seal_path, plan_path]
    for tier, item in artifacts.items():
        local = Path(item['path'])
        authority = status['published'].get(tier, {})
        if (not local.is_absolute() or local.stat().st_size != item['bytes'] or
                sha256(local) != item['sha256'] or
                any(item[key] != authority.get(key) for key in ('bytes', 'sha256'))):
            raise ValueError('Publication canary artifact differs from its audited output')
        dependencies.append(local)
    import uproot
    with uproot.open(artifacts['nanoAOD']['path']) as source:
        arrays = source['Events'].arrays(['run', 'luminosityBlock', 'event', 'genWeight'], library='np')
        actual = [[int(run), int(lumi), int(event)] for run, lumi, event in
                  zip(arrays['run'], arrays['luminosityBlock'], arrays['event'])]
        if actual != expected or any(float(value) != 1. for value in arrays['genWeight']):
            raise ValueError('Publication canary Nano identities or native weights changed')
    with tarfile.open(artifacts['metadata']['path']) as archive:
        members = {member.name: member for member in archive.getmembers()}
        if (not {'report.json', 'signal_report.json'} <= set(members) or
                any(member.name.startswith('/') or '..' in Path(member.name).parts or
                    not member.isfile() or member.name.endswith('.root') for member in members.values())):
            raise ValueError('Publication canary metadata is incomplete or unsafe')
        common = json.load(archive.extractfile(members['report.json']))
        signal = json.load(archive.extractfile(members['signal_report.json']))
        if (common.get('complete') is not True or signal.get('complete') is not True or
                signal.get('requested_event_ids') != expected or
                common.get('nano', {}).get('sumw') != len(expected) or
                common.get('nano_sha256') != artifacts['nanoAOD']['sha256']):
            raise ValueError('Publication canary lacks matching full-chain audit metadata')
    multiplier = budget.get('publication_safety_multiplier')
    if (type(multiplier) not in (int, float) or not math.isfinite(multiplier) or multiplier < 1 or
            budget.get('publication_profile') != proof['publication_profile'] or
            budget.get('publication_canary_sha256') != sha256(path)):
        raise ValueError('Storage budget does not bind its publication qualification')
    # New receipts retain scratch-tier hashes and profile metadata. Bound their
    # encoded size conservatively in addition to measured Nano/archive bytes.
    marker_bound = max(65536, len(json.dumps(status, indent=2, sort_keys=True).encode()) * 2)
    measured = sum(item['bytes'] for item in artifacts.values()) + marker_bound
    required = math.ceil(measured * multiplier)
    if (type(budget.get('bytes_per_detector_chunk')) is not int or
            budget['bytes_per_detector_chunk'] < required or
            type(budget.get('files_per_detector_chunk')) is not int or budget['files_per_detector_chunk'] < 3):
        raise ValueError('Storage reservation is below the measured publication bound')
    return dict(complete=True, publication_profile=proof['publication_profile'],
                tested_chunk_events=proof['tested_chunk_events'], measured_payload_bytes=measured,
                marker_bound_bytes=marker_bound, required_bound_bytes=required,
                publication_safety_multiplier=multiplier,
                dependencies=[dict(path=str(item), sha256=sha256(item)) for item in dependencies])


def histogram_inventory(path):
    """Read all histogram bins, flows, variances, axes and entry counts."""
    import numpy as np
    import uproot
    result = {}
    with uproot.open(path) as source:
        for name, classname in source.classnames(recursive=True, cycle=False).items():
            if not classname.startswith(('TH1', 'TH2', 'TH3')):
                continue
            histogram = source[name]
            values, variances = histogram.values(flow=True), histogram.variances(flow=True)
            if variances is None or not np.isfinite(values).all() or not np.isfinite(variances).all():
                raise ValueError('Histogram contents or variances are missing or nonfinite: ' + name)
            result[name] = dict(classname=classname, values=values, variances=variances,
                                axes=[axis.edges() for axis in histogram.axes],
                                entries=float(histogram.member('fEntries')))
    if not result:
        raise ValueError('Histogram output contains no histograms')
    return result


def validate_histogram_merge(inputs, merged):
    """Require the full histogram sum, including flow bins and squared weights."""
    import numpy as np
    if not inputs:
        raise ValueError('Histogram merge needs a complete nonempty input list')
    expected = None
    for path in inputs:
        inventory = histogram_inventory(path)
        if expected is None:
            expected = copy.deepcopy(inventory)
            continue
        if set(inventory) != set(expected):
            raise ValueError('Histogram group names differ')
        for name, value in inventory.items():
            target = expected[name]
            if (value['classname'] != target['classname'] or len(value['axes']) != len(target['axes']) or
                    any(not np.array_equal(left, right) for left, right in zip(value['axes'], target['axes']))):
                raise ValueError('Histogram group axes or types differ: ' + name)
            target['values'] += value['values']
            target['variances'] += value['variances']
            target['entries'] += value['entries']
    actual = histogram_inventory(merged)
    if set(actual) != set(expected):
        raise ValueError('Merged histogram names differ from the complete group inventory')
    for name, target in expected.items():
        value = actual[name]
        if (value['classname'] != target['classname'] or len(value['axes']) != len(target['axes']) or
                any(not np.array_equal(left, right) for left, right in zip(value['axes'], target['axes'])) or
                not np.allclose(value['values'], target['values'], rtol=2e-7, atol=1e-10) or
                not np.allclose(value['variances'], target['variances'], rtol=1e-10, atol=1e-10) or
                value['entries'] != target['entries']):
            raise ValueError('Merged histogram contents, variances, entries or axes differ: ' + name)
    return dict(complete=True, inputs=len(inputs), histograms=len(expected),
                flow_bins_checked=True, sum_weights_squared_checked=True, entry_counts_checked=True)


def validate_group_reports(row, parent, report, seal_hash, plan_hash, model_hash):
    """Bind published groups and their score receipts to the unique full parent."""
    expected = parent['event_ids']
    groups = validate_complete_groups(row, expected)
    if (report.get('complete') is not True or report.get('point') != row['point'] or
            report.get('events') != row['events'] or report.get('seal_sha256') != seal_hash or
            report.get('plan_sha256') != plan_hash or report.get('sample_role') != 'validation_only' or
            report.get('final_output_base') != row['final_output_base']):
        raise ValueError('Point report differs from the complete frozen production')
    union = report.get('union', {})
    published_groups, scored_groups = union.get('groups', []), report.get('groups', [])
    if (union.get('complete') is not True or union.get('point') != row['point'] or
            union.get('parent_gen') != parent or union.get('parent_gen_ledger_count') != 1 or
            union.get('event_ids') != expected or union.get('events') != row['events'] or
            union.get('native_sumw') != row['events'] or union.get('native_sumw2') != row['events'] or
            union.get('seal_sha256') != seal_hash or union.get('plan_sha256') != plan_hash or
            union.get('histogramming_started_after_complete_grouping') is not True or
            [item.get('path') for item in union.get('inputs', [])] != [chunk['nano'] for chunk in row['chunks']] or
            len(published_groups) != len(groups) or len(scored_groups) != len(groups)):
        raise ValueError('Point grouping changed its unique full GEN denominator')
    prefix = row['final_output_base']
    for group, publication, scored in zip(groups, published_groups, scored_groups):
        number, count = group['group'], group['count']
        identities = expected[group['skip']:group['skip'] + count]
        source = publication.get('grouped', {})
        marker = scored.get('score_histograms', {}).get('marker', {})
        if (publication.get('group') != number or publication.get('events') != count or
                publication.get('event_ids') != identities or publication.get('native_sumw') != count or
                publication.get('native_sumw2') != count or not valid_artifact(source, group['nano']) or
                scored.get('group') != number or marker.get('complete') is not True or
                marker.get('events') != count or marker.get('source') != group['nano'] or
                marker.get('source_sha256') != source.get('sha256') or
                marker.get('model_sha256') != model_hash or marker.get('plan_sha256') != seal_hash or
                type(marker.get('pairs')) is not int or marker['pairs'] < 0 or
                marker.get('selection_applied') is not False):
            raise ValueError('Group scoring differs from its exact grouped GEN slice')
        destinations = {
            'nano': prefix + f'/nanoAOD_merged/ntuple_{number}.root',
            'histograms': prefix + f'/histograms/ntuple_{number}.root',
            **{name: prefix + f'/metadata/bdt_grouped/group_{number:03d}/' + filename
               for name, filename in [('score_receipt', 'score.json'), ('bdt_hist_receipt', 'bdt_hist.json'),
                                      ('score_log', 'score.log'), ('histogram_log', 'histogram.log')]},
        }
        if set(marker.get('published', {})) != set(destinations):
            raise ValueError('Grouped scoring publication inventory is incomplete')
        for key, destination in destinations.items():
            item = marker['published'][key]
            if not valid_artifact(item, destination):
                raise ValueError('Grouped scoring publication changed its frozen namespace')
    final = report.get('score_histograms', {}).get('marker', {})
    if (final.get('complete') is not True or final.get('events') != row['events'] or
            final.get('plan_sha256') != seal_hash or final.get('model_sha256') != model_hash or
            final.get('selection_applied') is not False or final.get('groups') != len(groups) or
            final.get('pairs') != sum(item['score_histograms']['marker']['pairs'] for item in scored_groups) or
            not valid_artifact(final.get('published', {}).get('histograms'), prefix + '/histograms_merged/ntuple_0.root') or
            report.get('histogram_merge', {}).get('complete') is not True or
            report.get('histogram_merge', {}).get('inputs') != len(groups)):
        raise ValueError('Final merged histogram lacks complete-group validation')
    return published_groups, scored_groups


def finalize(point, seal_hash, cache):
    """Merge every Nano group before scoring, then validate their histogram sum."""
    import chunk_worker as worker
    import qualified_finalizer as qualified
    worker.verify_seal(seal_hash, point, scoring=True)
    plan, inventory = worker.read(worker.B / 'plan.json'), worker.read(worker.B / 'gen_inventory.json')
    rows = [row for row in plan['points'] if row['point'] == point]
    if len(rows) != 1:
        raise ValueError('Unknown or duplicate physical point')
    row = rows[0]
    row = dict(row, group_events=plan['group_events'])
    parent = inventory['points'][point]
    expected = parent['event_ids']
    groups = validate_complete_groups(row, expected)
    statuses = {chunk['chunk']: qualified.load_chunk_status(plan, inventory, point, chunk['chunk'], seal_hash)
                for chunk in row['chunks']}
    worker.prepare_publication_guard(seal_hash)
    worker.check_headroom()
    scratch = Path(os.environ['_CONDOR_SCRATCH_DIR']).resolve()
    work = scratch / 'groups'
    work.mkdir(exist_ok=False)
    chunks = {chunk['chunk']: chunk for chunk in row['chunks']}
    grouped_paths, publications, all_ids, all_weights, inputs, origins = [], [], [], [], [], []
    # Finish and verify all merges before invoking any histogrammer.
    for group in groups:
        number = group['group']
        directory = work / f'group_{number:03d}'
        directory.mkdir()
        paths, ids, weights = [], [], []
        for chunk_id in group['chunks']:
            chunk = chunks[chunk_id]
            status, origin = statuses[chunk_id]
            marker = directory / f'marker_{chunk_id:04d}.json'
            worker.xrd(['/usr/bin/xrdcp', '--silent', worker.HOST + '/' + chunk['metadata'] + '/complete.json', str(marker)])
            if worker.read(marker) != status:
                raise ValueError('EOS completion marker differs from its exact detector receipt')
            item = status['published']['nanoAOD']
            local = directory / f'chunk_{chunk_id:04d}.root'
            worker.xrd(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32', worker.HOST + '/' + item['path'], str(local)])
            if worker.sha(local) != item['sha256'] or local.stat().st_size != item['bytes']:
                raise ValueError('Detector Nano checksum or byte count changed')
            identities, native_weights = qualified.event_rows(local)
            qualified.validate_union(identities, native_weights, status['expected_event_ids'], chunk['count'])
            paths.append(local)
            ids.extend(identities)
            weights.extend(native_weights)
            inputs.append(item)
            origins.append(dict(chunk=chunk_id, **origin))
        selected = expected[group['skip']:group['skip'] + group['count']]
        qualified.validate_union(ids, weights, selected, group['count'])
        grouped = directory / 'grouped.root'
        with (directory / 'nano_merge.log').open('x') as log:
            subprocess.run(['hadd', str(grouped), *map(str, paths)], stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=1800)
        if qualified.event_rows(grouped) != (selected, weights):
            raise ValueError('Merged Nano changed event identities or native weights')
        item = worker.publish(grouped, group['nano'], directory)
        publications.append(dict(group=number, events=group['count'], event_ids=selected,
                                 native_sumw=sum(weights), native_sumw2=sum(value * value for value in weights),
                                 grouped=item))
        grouped_paths.append(grouped)
        all_ids.extend(ids)
        all_weights.extend(weights)
    qualified.validate_union(all_ids, all_weights, expected, row['events'])
    union = dict(schema='shift-independent-signal-union-v2', complete=True, point=point,
                 events=row['events'], event_ids=all_ids, native_sumw=sum(all_weights),
                 native_sumw2=sum(value * value for value in all_weights), inputs=inputs, groups=publications,
                 input_receipt_origins=origins, parent_gen=parent, parent_gen_ledger_count=1,
                 sample_role='validation_only', histogramming_started_after_complete_grouping=True,
                 seal_sha256=seal_hash, plan_sha256=worker.sha(worker.B / 'plan.json'))
    worker.write(work / 'union.json', union)
    worker.publish(work / 'union.json', row['final_output_base'] + '/metadata/grouping/union.json', work)
    sys.path.insert(0, str(worker.B / 'frozen/scoring/workflow/scripts'))
    import run_merged_dimuon_bdt as scorer
    original_put, checked_directories = scorer.io.put_remote, set()
    def guarded_put(local, remote):
        directory = Path(local).parent
        if directory not in checked_directories:
            qualified.validate_histogram_axes(directory / 'histograms.root',
                                              worker.B / 'frozen/scoring/sm_axis_authority.root')
            checked_directories.add(directory)
        worker.check_headroom()
        return original_put(local, remote)
    scorer.io.put_remote = guarded_put
    scoreplan = worker.read(worker.B / 'scoring_components.json')
    analysis = scorer.prepare_analysis(scoreplan, scratch, cache)
    scored, histogram_paths = [], []
    prefix = row['final_output_base']
    try:
        for group, publication, source in zip(groups, publications, grouped_paths):
            number = group['group']
            metadata = prefix + f'/metadata/bdt_grouped/group_{number:03d}'
            score_row = dict(index=number, source_transport_path=str(source), source_logical_path=group['nano'],
                source_sha256=worker.sha(source), source_bytes=source.stat().st_size, expected_events=group['count'],
                weight_branch='genWeight', output_nano=prefix + f'/nanoAOD_merged/ntuple_{number}.root',
                output_histograms=prefix + f'/histograms/ntuple_{number}.root', complete_marker=metadata + '/complete.json',
                score_receipt=metadata + '/score.json', bdt_hist_receipt=metadata + '/bdt_hist.json',
                score_log=metadata + '/score.log', histogram_log=metadata + '/histogram.log')
            directory = scratch / f'score_{number:03d}'
            result = scorer.run_file(scoreplan, score_row, directory, cache, analysis, seal_hash)
            if qualified.event_rows(directory / 'scored.root') != (publication['event_ids'], [1.] * group['count']):
                raise ValueError('Scoring changed grouped event identities or native weights')
            scored.append(dict(group=number, score_histograms=result))
            histogram_paths.append(directory / 'histograms.root')
    finally:
        scorer.io.put_remote = original_put
    merged = work / 'histograms_merged.root'
    with (work / 'histogram_merge.log').open('x') as log:
        subprocess.run(['hadd', str(merged), *map(str, histogram_paths)], stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=1800)
    merge_audit = validate_histogram_merge(histogram_paths, merged)
    qualified.validate_histogram_axes(merged, worker.B / 'frozen/scoring/sm_axis_authority.root')
    scorer.check_standard_histograms(merged, row['events'], row['events'])
    histogram = worker.publish(merged, prefix + '/histograms_merged/ntuple_0.root', work)
    # The publisher independently downloads and SHA-checks its destination.
    marker = dict(schema='shift-grouped-signal-histograms-complete-v1', complete=True,
                  events=row['events'], pairs=sum(item['score_histograms']['marker']['pairs'] for item in scored),
                  model_sha256=scoreplan['model_sha256'], plan_sha256=seal_hash, selection_applied=False,
                  groups=len(groups), published=dict(histograms=histogram))
    report = dict(schema='shift-independent-signal-point-complete-v2', complete=True, point=point,
                  events=row['events'], sample_role='validation_only', union=union, groups=scored,
                  score_histograms=dict(complete=True, marker=marker), histogram_merge=merge_audit,
                  seal_sha256=seal_hash, plan_sha256=worker.sha(worker.B / 'plan.json'), final_output_base=prefix,
                  canonical_sm_histogram_axes_verified=1081,
                  canonical_sm_histogram_authority_sha256=worker.sha(worker.B / 'frozen/scoring/sm_axis_authority.root'))
    validate_group_reports(row, parent, report, seal_hash, worker.sha(worker.B / 'plan.json'), scoreplan['model_sha256'])
    worker.write(work / 'point_complete.json', report)
    worker.publish(work / 'point_complete.json', prefix + '/metadata/grouping/point_complete.json', work)
    worker.write(worker.B / 'completed_points' / (point + '.json'), report)
    return report


def collect(seal_hash):
    """Collect every scored group while keeping the complete GEN parent once."""
    import chunk_worker as worker
    worker.verify_seal(seal_hash, scoring=True)
    plan, inventory = worker.read(worker.B / 'plan.json'), worker.read(worker.B / 'gen_inventory.json')
    plan_hash = worker.sha(worker.B / 'plan.json')
    model_hash = worker.read(worker.B / 'scoring_components.json')['model_sha256']
    inputs, histograms, all_ids = [], [], set()
    for raw in plan['points']:
        row = dict(raw, group_events=plan['group_events'])
        point, parent = row['point'], inventory['points'][row['point']]
        report = worker.read(worker.B / 'completed_points' / (point + '.json'))
        publications, scored = validate_group_reports(row, parent, report, seal_hash, plan_hash, model_hash)
        ids = set(map(tuple, parent['event_ids']))
        if ids.intersection(all_ids):
            raise ValueError('Production points share GEN event identities')
        all_ids.update(ids)
        meta = {key: row[key] for key in ('point', 'mass_gev', 'epsilon', 'proper_ctau_mm', 'target_mean_lab_flight_m')}
        for publication, result in zip(publications, scored):
            marker = result['score_histograms']['marker']
            published = marker['published']
            inputs.append(dict(meta, group=publication['group'], path=published['nano']['path'], process='signal',
                events=publication['events'], weight_branch='genWeight', mixture_scale=1.,
                nano_sha256=published['nano']['sha256'], score_receipt=published['score_receipt']['path'],
                sample_role='validation_only', parent_gen=parent,
                parent_gen_population_id=parent['gen_sha256'], parent_gen_count_policy='Unique gen_sha256 counted once'))
        histogram = report['score_histograms']['marker']['published']['histograms']
        flight = row['target_mean_lab_flight_m']
        label = f"A' {row['mass_gev']:g} GeV, " + ('prompt' if flight is None else f'mean lab flight {flight:g} m')
        histograms.append(dict(meta, name=point, process='signal', label=label,
            histogram_path=histogram['path'], histogram_sha256=histogram['sha256'], sample_role='validation_only'))
    if len(all_ids) != plan['events']:
        raise ValueError('Collected full GEN population differs from the frozen production')
    performance = dict(schema='shift-bdt-performance-inputs-v1', sample_kind='simulation_only', inputs_complete=True,
        expected_inputs=len(inputs), completed_inputs=len(inputs), inputs=inputs,
        full_gen_population_count=len(plan['points']), each_parent_ledger_counted_once=True,
        seal_sha256=seal_hash, plan_sha256=plan_hash)
    bins = dict(schema='shift-dimuon-bdt-bin-inputs-v1', complete=True, samples=histograms,
                model_sha256=model_hash, seal_sha256=seal_hash, plan_sha256=plan_hash)
    worker.write(worker.B / 'performance_inputs.json', performance)
    worker.write(worker.B / 'bin_histogram_inputs.json', bins)
