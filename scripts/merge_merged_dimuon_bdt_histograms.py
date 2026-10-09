#!/usr/bin/env python3
"""Merge completed score-only MC histograms without merging event trees.

The exact production plan, all batch statuses and all per-file completion
markers must agree before a histogram is merged. New outputs are published with
checksum readback and a completion marker last. Original inputs stay intact.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess

import prepare_dimuon_score_production as io
import run_merged_dimuon_bdt as producer

SCHEMA = 'shift-dimuon-bdt-bin-inputs-v1'
LABELS = ('Genuine', 'Accidental', 'Unknown')
WRITER_REPAIR_DEPENDENCIES = frozenset((
    'classifier/add_bdt_score.py', 'workflow/scripts/run_merged_dimuon_bdt.py',
))


def read_json(path):
    return json.loads(Path(path).read_text())


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('Invalid finite number: ' + name)
    return float(value)


def count(value, name):
    if type(value) is not int or value < 0:
        raise ValueError('Invalid nonnegative count: ' + name)
    return value


def valid_digest(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def load_plan(path, expected_digest):
    plan = producer.validate_plan(path, expected_digest)
    if plan.get('mc_to_data_normalization_measured') is not False:
        raise ValueError('This merge does not measure MC-to-data normalization')
    rows = plan['files']
    if not rows or [r['index'] for r in rows] != list(range(len(rows))):
        raise ValueError('Production file indices must be complete and unique')
    flattened = [i for batch in plan['batches'] for i in batch]
    if len(flattened) != len(rows) or set(flattened) != set(range(len(rows))):
        raise ValueError('Production batches do not partition the file inventory')
    dependency = plan['dependencies']['source_inventory.json']
    if dependency['path'] != plan['source_inventory'] or io.digest(dependency['path']) != dependency['sha256']:
        raise ValueError('Frozen source inventory differs from the production plan')
    sources = read_json(plan['source_inventory'])
    if sources.get('complete') is not True:
        raise ValueError('Frozen source inventory coverage is incomplete')
    validate_compatibility(plan, expected_digest)
    return plan, sources


def validate_compatibility(plan, plan_digest):
    """Accept an immutable earlier publication only after a writer-only proof.

    Recovery status files must still belong to the current plan. Earlier
    completion markers and receipts retain their original hashes and contents.
    """
    proofs = plan.get('compatible_previous_plans', {})
    if not isinstance(proofs, dict):
        raise ValueError('Previous-plan compatibility proofs must be a mapping')
    if not proofs:
        return {plan_digest}
    previous_plans, proof_dependencies = [], set()
    for previous_digest, proof in proofs.items():
        if (not valid_digest(previous_digest) or previous_digest == plan_digest
                or not isinstance(proof, dict) or proof.get('sha256') != previous_digest
                or not isinstance(proof.get('path'), str)):
            raise ValueError('Invalid previous-plan compatibility proof identity')
        if io.digest(proof['path']) != previous_digest:
            raise ValueError('Previous-plan compatibility proof checksum differs')
        registered = [name for name, item in plan['dependencies'].items()
                      if item.get('path') == proof['path'] and item.get('sha256') == previous_digest]
        if len(registered) != 1 or registered[0] in WRITER_REPAIR_DEPENDENCIES:
            raise ValueError('Previous-plan proof is not an exact frozen dependency')
        proof_dependencies.add(registered[0])
        previous = read_json(proof['path'])
        for key in ('schema', 'campaign', 'model_sha256', 'files', 'batches',
                    'input_campaign_complete', 'missing_detector_jobs', 'storage_contract'):
            if previous.get(key) != plan.get(key):
                raise ValueError('Recovery changed the population/model/storage contract: ' + key)
        if (previous.get('selection_applied') is not False
                or previous.get('mc_to_data_normalization_measured') is not False):
            raise ValueError('Previous plan lacks the score-only simulation contract')
        if previous.get('compatible_previous_plans'):
            raise ValueError('Nested previous-plan compatibility requires an explicit flattened proof inventory')
        previous_plans.append(previous)
    current_names = set(plan['dependencies']) - proof_dependencies
    if not WRITER_REPAIR_DEPENDENCIES.issubset(current_names):
        raise ValueError('Recovery omits the file-writer/resume dependency contract')
    for previous in previous_plans:
        if set(previous['dependencies']) != current_names:
            raise ValueError('Recovery changed the non-proof dependency inventory')
        for name in current_names - WRITER_REPAIR_DEPENDENCIES:
            if previous['dependencies'][name]['sha256'] != plan['dependencies'][name]['sha256']:
                raise ValueError('Recovery changed a non-writer physics/analysis dependency: ' + name)
    return {plan_digest, *proofs}


def expected_publications(row):
    return {'nano': row['output_nano'], 'histograms': row['output_histograms'],
            **{name: row[name] for name in ('score_receipt', 'bdt_hist_receipt', 'score_log', 'histogram_log')}}


def validate_marker(marker, row, plan, plan_digest, allowed_plan_digests=None):
    expected = expected_publications(row)
    allowed = {plan_digest} if allowed_plan_digests is None else allowed_plan_digests
    if (marker.get('schema') != 'shift-merged-dimuon-bdt-complete-v1'
            or marker.get('complete') is not True or marker.get('plan_sha256') not in allowed
            or marker.get('source') != row['source_logical_path']
            or marker.get('events') != row['expected_events']
            or marker.get('model_sha256') != plan['model_sha256']
            or marker.get('selection_applied') is not False
            or not valid_digest(marker.get('source_sha256'))
            or (row.get('source_sha256') and row['source_sha256'] != marker['source_sha256'])
            or set(marker.get('published', {})) != set(expected)):
        raise ValueError('Completion marker differs from the exact production plan')
    count(marker.get('events'), 'marker.events')
    count(marker.get('pairs'), 'marker.pairs')
    for name, path in expected.items():
        item = marker['published'][name]
        if (item.get('path') != path or not valid_digest(item.get('sha256'))
                or type(item.get('bytes')) is not int or item['bytes'] <= 0):
            raise ValueError('Published artifact identity differs: ' + name)


def completed_statuses(plan, plan_digest, directory):
    """A recovered batch may have retained failed attempts plus a valid success."""
    successes, failures = {}, []
    allowed = validate_compatibility(plan, plan_digest)
    for path in sorted(Path(directory).glob('batch*.json')):
        status = read_json(path)
        if status.get('plan_sha256') != plan_digest or status.get('selection_applied') is not False:
            raise ValueError('Returned batch status belongs to another plan: ' + str(path))
        batch = status.get('batch')
        if type(batch) is not int or not 0 <= batch < len(plan['batches']) or status.get('pilot') is not False:
            raise ValueError('Returned status is not a production batch: ' + str(path))
        if status.get('complete') is not True:
            failures.append({'path': str(path), 'sha256': io.digest(path), 'batch': batch})
            continue
        expected = plan['batches'][batch]
        rows = status.get('files', [])
        if (status.get('pending_indices') != [] or status.get('expected_indices') != expected
                or [r.get('index') for r in rows] != expected or any(r.get('complete') is not True for r in rows)):
            raise ValueError('Successful status lacks its complete exact batch inventory: ' + str(path))
        for row in rows:
            validate_marker(row['marker'], plan['files'][row['index']], plan, plan_digest, allowed)
        if batch in successes and successes[batch]['status']['files'] != rows:
            raise ValueError('Conflicting successful batch statuses')
        successes[batch] = {'path': str(path), 'sha256': io.digest(path), 'status': status}
    missing = sorted(set(range(len(plan['batches']))) - set(successes))
    if missing:
        raise ValueError(f'Production is not complete: {len(missing)} batches lack an exact successful status: {missing[:12]}')
    return successes, failures


def group_inventory(plan, sources):
    groups = {}
    signal_sources = {row['point']: row for row in sources['signals']}
    for row in plan['files']:
        signal = row['process'] == 'signal'
        name = row['point'] if signal else row['stratum']
        directory = Path(row['output_histograms']).parent.parent
        target = str(directory / 'histograms_merged/ntuple_0.root')
        metadata = directory / 'metadata/bdt_histogram_merge'
        if name not in groups:
            if signal:
                original = signal_sources[name]
                identity = {key: original[key] for key in ('point', 'mass_gev', 'epsilon', 'proper_ctau_mm')}
                identity.update(label=f"A' {original['mass_gev']:g} GeV, {name.split('_', 1)[1]}",
                                cross_section_pb=None, full_gen_sumw=None,
                                normalization='native unit MC weights; no signal production-rate claim')
            else:
                original = sources['bins'][name]
                identity = {'bin': original['bin'], 'label': name,
                            'cross_section_pb': finite(original['cross_section_pb'], name + '.cross_section'),
                            'full_gen_sumw': finite(original['full_parent_gen_sumw'], name + '.full_gen_sumw'),
                            'normalization': 'stored W/p once; original full parent GEN sumw denominator'}
                if identity['full_gen_sumw'] <= 0:
                    raise ValueError('Full parent GEN exposure must be positive')
            groups[name] = {'name': name, 'process': row['process'], 'histogram_path': target,
                            'receipt_path': str(metadata / 'merge_receipt.json'),
                            'log_path': str(metadata / 'merge.log'),
                            'complete_marker': str(metadata / 'complete.json'),
                            'indices': [], **identity}
        if groups[name]['histogram_path'] != target or groups[name]['process'] != row['process']:
            raise ValueError('A process/bin maps to multiple output directories')
        groups[name]['indices'].append(row['index'])
    if len({row['histogram_path'] for row in groups.values()}) != len(groups):
        raise ValueError('Distinct process/bins share a merge target')
    expected = set(sources['bins']) | set(signal_sources)
    if set(groups) != expected:
        raise ValueError('Production groups differ from frozen source bins/signal points')
    return [groups[name] for name in sorted(groups)]


def stage_checked(item, destination):
    io.get_remote(item['path'], destination)
    if io.digest(destination) != item['sha256'] or destination.stat().st_size != item['bytes']:
        raise ValueError('Published artifact failed checksum/size readback: ' + item['path'])


def validate_receipts(score, histogram, marker, row, plan_digest):
    if (score.get('complete') is not True or score.get('source_sha256') != marker['source_sha256']
            or score.get('source') != row['source_logical_path']
            or score.get('source_transport') != row['source_transport_path']
            or score.get('output') != row['output_nano']
            or score.get('output_sha256') != marker['published']['nano']['sha256']
            or score.get('model_sha256') != marker['model_sha256']
            or score.get('events') != marker['events'] or score.get('retained_pairs') != marker['pairs']
            or score.get('selection_applied') is not False or score.get('plan_sha256') != marker['plan_sha256']
            or score.get('verification', {}).get('all_original_content_equal') is not True
            or score.get('verification', {}).get('all_scores_recomputed_equal') is not True):
        raise ValueError('Score receipt lacks exact original-content/score verification')
    if (histogram.get('schema') != 'shift-dimuon-bdt-histograms-v1'
            or histogram.get('complete') is not True or histogram.get('plan_sha256') != marker['plan_sha256']
            or histogram.get('events') != marker['events'] or histogram.get('pairs') != marker['pairs']
            or histogram.get('output') != row['output_histograms']
            or histogram.get('input') != row['output_nano']
            or histogram.get('output_sha256') != marker['published']['histograms']['sha256']
            or histogram.get('score_selection_applied') is not False
            or histogram.get('weights_branch') != row['weight_branch']
            or histogram.get('weight_application_count') != 1):
        raise ValueError('Histogram receipt differs from its exact scored population')
    for name in ('event_sumw', 'event_sumw2', 'pair_sumw', 'pair_sumw2', 'standard_cutflow_float32_sumw'):
        value = finite(histogram.get(name), name)
        if name.endswith('sumw2') and value < 0:
            raise ValueError('Squared weight sum cannot be negative')
    labels = histogram.get('label_pairs', {})
    categories = histogram.get('category_pairs', {})
    if (set(labels) != set(LABELS) or sum(count(labels[k], k) for k in LABELS) != marker['pairs']
            or categories.get('') != marker['pairs']):
        raise ValueError('Histogram labels do not partition every retained pair')
    for key, value in categories.items():
        if not isinstance(key, str) or count(value, key) > marker['pairs']:
            raise ValueError('Invalid BDT category pair count')


def freeze_inputs(plan, plan_digest, successes, work):
    metadata = work / 'input_metadata'
    metadata.mkdir()
    records = {}
    allowed = validate_compatibility(plan, plan_digest)
    status_markers = {row['index']: row['marker'] for record in successes.values() for row in record['status']['files']}
    for row in plan['files']:
        directory = metadata / f"f{row['index']:04d}"
        directory.mkdir()
        io.get_remote(row['complete_marker'], directory / 'complete.json')
        marker = read_json(directory / 'complete.json')
        validate_marker(marker, row, plan, plan_digest, allowed)
        if marker != status_markers[row['index']]:
            raise ValueError('Remote completion marker differs from returned successful status')
        for key in ('score_receipt', 'bdt_hist_receipt'):
            stage_checked(marker['published'][key], directory / (key + '.json'))
        score = read_json(directory / 'score_receipt.json')
        histogram = read_json(directory / 'bdt_hist_receipt.json')
        validate_receipts(score, histogram, marker, row, plan_digest)
        records[row['index']] = {'index': row['index'], 'marker_path': row['complete_marker'],
                                 'marker_sha256': io.digest(directory / 'complete.json'),
                                 'marker': marker, 'histogram_receipt': histogram}
    io.write_json(work / 'validated_input_markers.json', list(records.values()))
    return records


def totals(records):
    histogram_receipts = [row['histogram_receipt'] for row in records]
    category_names = set(histogram_receipts[0]['category_pairs'])
    if any(set(row['category_pairs']) != category_names for row in histogram_receipts):
        raise ValueError('BDT category inventories differ between histogram files')
    result = {'events': sum(row['events'] for row in histogram_receipts),
              'pairs': sum(row['pairs'] for row in histogram_receipts),
              'label_pairs': {name: sum(row['label_pairs'][name] for row in histogram_receipts) for name in LABELS},
              'category_pairs': {name: sum(row['category_pairs'][name] for row in histogram_receipts) for name in category_names}}
    for name in ('event_sumw', 'event_sumw2', 'pair_sumw', 'pair_sumw2', 'standard_cutflow_float32_sumw'):
        result[name] = math.fsum(row[name] for row in histogram_receipts)
    return result


def histogram_only(directory):
    import ROOT
    for key in directory.GetListOfKeys():
        klass = ROOT.TClass.GetClass(key.GetClassName())
        if not klass:
            raise ValueError('Histogram output contains an unknown object type')
        if klass.InheritsFrom('TTree'):
            raise ValueError('Refuse to merge event trees in histogram production')
        if klass.InheritsFrom('TDirectory'):
            histogram_only(key.ReadObj())


def score_name(category='', label='', unweighted=False):
    return 'dimuon_bdt/ShiftDimuonVertex' + category + '_bdtScore' + label + ('Unweighted' if unweighted else '')


def histogram_values(root, path):
    hist = root.Get(path)
    if not hist or not hist.InheritsFrom('TH1') or hist.GetDimension() != 1:
        raise ValueError('Missing required one-dimensional histogram: ' + path)
    values = [float(hist.GetBinContent(i)) for i in range(hist.GetNcells())]
    errors2 = [float(hist.GetBinError(i)) ** 2 for i in range(hist.GetNcells())]
    if not all(math.isfinite(x) for x in values + errors2):
        raise ValueError('Nonfinite histogram values or errors: ' + path)
    return hist, values, errors2


def close_number(actual, expected, name):
    if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8):
        raise ValueError(f'Histogram total differs for {name}: {actual} != {expected}')


def validate_histograms(path, expected):
    import ROOT
    root = ROOT.TFile.Open(str(path))
    if not root or root.IsZombie() or root.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unreadable or recovered histogram ROOT output')
    try:
        histogram_only(root)
        raw = root.Get('rawEventsCutFlow')
        weighted = root.Get('cutFlow')
        if not raw or not weighted or float(raw.GetBinContent(1)) != expected['events']:
            raise ValueError('Raw cutflow differs from exact scored event count')
        close_number(float(weighted.GetBinContent(1)), expected['standard_cutflow_float32_sumw'], 'cutFlow')
        _, inclusive, errors2 = histogram_values(root, score_name())
        close_number(math.fsum(inclusive), expected['pair_sumw'], 'BDT pair sumw')
        close_number(math.fsum(errors2), expected['pair_sumw2'], 'BDT pair sumw2')
        for category, pairs in expected['category_pairs'].items():
            _, raw_values, _ = histogram_values(root, score_name(category, unweighted=True))
            if math.fsum(raw_values) != pairs:
                raise ValueError('Raw BDT category count differs: ' + category)
            _, weighted_values, _ = histogram_values(root, score_name(category))
            weighted_subsets, raw_subsets = [], []
            for label in LABELS:
                _, label_weighted, _ = histogram_values(root, score_name(category, label))
                _, label_raw, _ = histogram_values(root, score_name(category, label, True))
                if len(label_raw) != len(raw_values) or len(label_weighted) != len(weighted_values):
                    raise ValueError('Labelled score histogram binning differs')
                weighted_subsets.append(label_weighted)
                raw_subsets.append(label_raw)
                if category == '' and math.fsum(label_raw) != expected['label_pairs'][label]:
                    raise ValueError('Raw BDT label count differs: ' + label)
            for index in range(len(raw_values)):
                if math.fsum(row[index] for row in raw_subsets) != raw_values[index]:
                    raise ValueError('Raw labelled score subsets fail to partition the histogram')
                close_number(math.fsum(row[index] for row in weighted_subsets), weighted_values[index], 'weighted label partition')
        return {'histogram_only': True, 'events': expected['events'], 'pairs': expected['pairs'],
                'raw_cutflow_equal': True, 'sum_of_group_float32_cutflows_equal': True,
                'bdt_pair_sumw_equal': True, 'bdt_pair_sumw2_equal': True,
                'label_partitions_equal': True, 'all_flow_bins_included': True}
    finally:
        root.Close()


def merge_group(group, records, plan_digest, model_digest, work):
    directory = work / group['name']
    directory.mkdir()
    selected = [records[index] for index in group['indices']]
    expected = totals(selected)
    inputs = [{'index': row['index'], 'complete_marker': row['marker_path'],
               'complete_marker_sha256': row['marker_sha256'],
               'publication_plan_sha256': row['marker']['plan_sha256'],
               **row['marker']['published']['histograms']}
              for row in selected]
    receipt = {'schema': 'shift-dimuon-bdt-histogram-merge-v1', 'complete': False,
               'plan_sha256': plan_digest, 'model_sha256': model_digest,
               'name': group['name'], 'process': group['process'], 'inputs': inputs,
               'output': group['histogram_path'], 'totals': expected,
               'selection_applied': False, 'mc_to_data_normalization_measured': False,
               'cutflow_policy': 'Sum completed per-group Float32 cutflows in Double; never recompute a full-bin Float32 accumulator.',
               'created_utc': datetime.now(timezone.utc).isoformat()}
    identity_keys = ('schema', 'plan_sha256', 'model_sha256', 'name', 'process', 'inputs', 'output', 'totals',
                     'selection_applied', 'mc_to_data_normalization_measured')
    if io.remote_exists(group['complete_marker']):
        io.get_remote(group['complete_marker'], directory / 'existing_complete.json')
        marker = read_json(directory / 'existing_complete.json')
        planned = {'histograms': group['histogram_path'], 'receipt': group['receipt_path'], 'log': group['log_path']}
        if (marker.get('schema') != 'shift-dimuon-bdt-histogram-merge-complete-v1'
                or marker.get('complete') is not True or marker.get('plan_sha256') != plan_digest
                or marker.get('model_sha256') != model_digest or marker.get('name') != group['name']
                or marker.get('selection_applied') is not False
                or set(marker.get('published', {})) != set(planned)
                or any(marker['published'][key].get('path') != path for key, path in planned.items())):
            raise ValueError('Existing histogram merge completion differs from this exact plan')
        for key, item in marker['published'].items():
            stage_checked(item, directory / ('existing_' + key))
        existing = read_json(directory / 'existing_receipt')
        if (existing.get('complete') is not True or any(existing.get(key) != receipt[key] for key in identity_keys)
                or existing.get('output_sha256') != marker['published']['histograms']['sha256']
                or existing.get('output_bytes') != marker['published']['histograms']['bytes']):
            raise ValueError('Existing merged histogram receipt differs from exact inputs')
        validate_histograms(directory / 'existing_histograms', expected)
        return {**group, 'histogram_sha256': marker['published']['histograms']['sha256'],
                'events': expected['events'], 'pairs': expected['pairs'], 'reused': True}
    if any(io.remote_exists(group[key]) for key in ('histogram_path', 'receipt_path', 'log_path')):
        raise FileExistsError('Incomplete histogram merge publication exists; preserve it without overwrite')
    staged = []
    for row in selected:
        local = directory / f"histogram_{row['index']:04d}.root"
        stage_checked(row['marker']['published']['histograms'], local)
        validate_histograms(local, totals([row]))
        staged.append(local)
    output, log_path = directory / 'ntuple_0.root', directory / 'merge.log'
    command = ['hadd', str(output), *map(str, staged)]
    receipt['command'] = command
    with log_path.open('x') as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    receipt['verification'] = validate_histograms(output, expected)
    receipt.update(complete=True, output_sha256=io.digest(output), output_bytes=output.stat().st_size)
    receipt_path = directory / 'merge_receipt.json'
    io.write_json(receipt_path, receipt)
    destinations = {'histograms': group['histogram_path'], 'receipt': group['receipt_path'], 'log': group['log_path']}
    local_files = {'histograms': output, 'receipt': receipt_path, 'log': log_path}
    for target in sorted({str(Path(path).parent) for path in destinations.values()} | {str(Path(group['complete_marker']).parent)}):
        io.transfer(['/usr/bin/xrdfs', 'root://eosuser.cern.ch', 'mkdir', '-p', target])
    published = {}
    for name, local in local_files.items():
        io.put_remote(local, destinations[name])
        item = {'path': destinations[name], 'sha256': io.digest(local), 'bytes': local.stat().st_size}
        stage_checked(item, directory / ('readback_' + name))
        published[name] = item
    marker = {'schema': 'shift-dimuon-bdt-histogram-merge-complete-v1', 'complete': True,
              'plan_sha256': plan_digest, 'model_sha256': model_digest, 'name': group['name'],
              'selection_applied': False, 'published': published}
    io.write_json(directory / 'complete.json', marker)
    io.put_remote(directory / 'complete.json', group['complete_marker'])
    io.get_remote(group['complete_marker'], directory / 'readback_complete.json')
    if io.digest(directory / 'complete.json') != io.digest(directory / 'readback_complete.json'):
        raise ValueError('Merged completion marker failed checksum readback')
    return {**group, 'histogram_sha256': receipt['output_sha256'], 'events': expected['events'],
            'pairs': expected['pairs'], 'reused': False}


def run(plan_path, plan_digest, results_directory, work_directory, *, prepare_only=False):
    plan, sources = load_plan(plan_path, plan_digest)
    successes, retained_failures = completed_statuses(plan, plan_digest, results_directory)
    groups = group_inventory(plan, sources)
    work = Path(work_directory)
    work.mkdir(parents=True, exist_ok=False)
    io.write_json(work / 'completed_batch_statuses.json', {'successes': list(successes.values()), 'retained_failed_attempts': retained_failures})
    records = freeze_inputs(plan, plan_digest, successes, work)
    preparation = {'schema': 'shift-dimuon-bdt-histogram-merge-preparation-v1',
                   'complete': True, 'plan_sha256': plan_digest, 'model_sha256': plan['model_sha256'],
                   'input_files': len(records), 'groups': groups, 'selection_applied': False,
                   'compatible_previous_plan_sha256': sorted(plan.get('compatible_previous_plans', {})),
                   'input_campaign_complete': plan['input_campaign_complete'],
                   'missing_detector_jobs': plan['missing_detector_jobs']}
    io.write_json(work / 'merge_plan.json', preparation)
    if prepare_only:
        return preparation
    merged = [merge_group(group, records, plan_digest, plan['model_sha256'], work) for group in groups]
    result = {'schema': SCHEMA, 'complete': True, 'plan_sha256': plan_digest,
              'model_sha256': plan['model_sha256'], 'campaign': plan['campaign'],
              'all_production_files_complete': True, 'input_files': len(records),
              'input_campaign_complete': plan['input_campaign_complete'],
              'missing_detector_jobs': plan['missing_detector_jobs'],
              'selection_applied': False, 'mc_to_data_normalization_measured': False,
              'compatible_previous_plan_sha256': sorted(plan.get('compatible_previous_plans', {})),
              'unavailable_signal_points': sources.get('unavailable_signal_points', []), 'rows': merged}
    io.write_json(work / 'bin_histogram_inputs.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--results-directory', type=Path, required=True)
    parser.add_argument('--work-directory', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true', help='Validate all completed inputs and freeze merge groups without merging or publishing.')
    args = parser.parse_args()
    result = run(args.plan, args.plan_sha256, args.results_directory, args.work_directory, prepare_only=args.prepare_only)
    print(json.dumps({'complete': result['complete'], 'groups': len(result.get('rows', result.get('groups', []))),
                      'input_files': result['input_files'], 'prepare_only': args.prepare_only}))


if __name__ == '__main__':
    main()
