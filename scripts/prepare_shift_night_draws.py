#!/usr/bin/env python3
# Code style: AI-code. AI-generated independent saved-GEN night-draw preparer.
"""Freeze bounded complete draws; scheduling never chooses or reweights events."""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import secrets
import shutil
import tarfile

from shift_night_plan import event_contract, sha


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')


def identity(row):
    return int(row['source']), int(row['raw_index'])


def read_rows(path):
    with gzip.open(path, 'rt') as stream:
        return [json.loads(line) for line in stream]


def uniform_integer(seed, source, raw, event):
    key = [source['stratum'], source['original_gen_sha256'], str(source['index']),
           str(raw), *(str(value) for value in event)]
    return int.from_bytes(hashlib.sha256(bytes.fromhex(seed) + b'\0' + '\0'.join(key).encode()).digest(), 'big')


def union_probability(old_probability, tail_probability, completed_epochs):
    return 1.0 - (1.0 - old_probability) * (1.0 - tail_probability) * .99 ** completed_epochs


def prepare(workspace, output, epochs):
    study = workspace / 'validation/qcd_gen_recovery_20261014'
    if output.exists():
        raise RuntimeError('Refuse to replace an existing frozen night draw')
    output.mkdir(parents=True)
    rule = dict(schema='shift-independent-QCD-night-draw-rule-v1', epochs=epochs, q=.01,
                seeds_hex256=[secrets.token_hex(32) for _ in range(epochs)],
                probability_rule='Uniform independent full256-bit Bernoulli draw over every original QCD GEN event.',
                threshold_integer=str((1 << 256) // 100),
                floor_rounding='Stored Double .01 is the correctly rounded full256-bit hash threshold probability.',
                no_detector_outcomes_used=True, no_selected_subset_topups=True,
                complete_epoch_required=True, response='Identical frozen V10, diagnostic detector response.',
                analysis_release=False, physics_valid=False)
    write_json(output / 'rule_freeze.json', rule)
    plan_path = study / 'full_gen_scan/plan.json'
    scan = json.loads(plan_path.read_text())
    reconciliation = json.loads((study / 'full_gen_scan/reconciliation_all20.json').read_text())
    if not reconciliation['complete'] or reconciliation['originals_validated'] != 2866485:
        raise RuntimeError('The full original QCD population is not reconciled')
    sources = []
    for batch in scan['batches']:
        path = Path(batch['manifest'])
        if sha(path) != batch['manifest_sha256']:
            raise RuntimeError('A full original-source manifest changed')
        sources.extend(json.loads(gzip.decompress(path.read_bytes()))['sources'])
    sources.sort(key=lambda row: row['index'])
    if len(sources) != 709 or len({row['index'] for row in sources}) != 709:
        raise RuntimeError('Full original-source inventory differs')
    tail_root = study / 'independent_tail_draw_v1'
    tail = json.loads((tail_root / 'draw_plan.json').read_text())
    for name, digest in tail['ledgers'].items():
        if sha(tail_root / name) != digest:
            raise RuntimeError('An earlier frozen draw ledger changed: ' + name)
    old = {identity(row): row for row in read_rows(tail_root / 'old_selected_union_overlay_proposal.jsonl.gz')}
    current = {identity(row): row for row in read_rows(tail_root / 'new_reconstruction_events.jsonl.gz')}
    tail_keys = {identity(row) for row in read_rows(tail_root / 'full_population_GEN_tail_candidates.jsonl.gz')}
    if len(old) != 60158 or len(current) != 496 or len(tail_keys) != 229 or set(old) & set(current):
        raise RuntimeError('Earlier selected-original coverage differs')
    prior_by_source = {}
    for (source, raw), row in {**old, **current}.items():
        prior_by_source.setdefault(source, {})[raw] = row
    authority_root = study / 'sampling_weights/full_GEN_normalization_v1'
    sampling = json.loads((authority_root / 'sampling_plan.json').read_text())
    base = output / 'base'
    base.mkdir()
    response = study / 'tail_reco_launch_v1/provenance/response_contract.json'
    response_sha = sha(response)
    shutil.copyfile(response, base / 'response_contract.json')
    shutil.copytree(study / 'tail_reco_launch_v1/templates', base / 'templates')
    shutil.copytree(authority_root, base / 'normalization_authority',
                    ignore=shutil.ignore_patterns('source', '*.log', 'shared_helper*', 'seal*'))
    prior = dict(old_plan_sha256=tail['old_sampling_plan_sha256'],
                 old_ledger_sha256=tail['old_sampling_ledger_sha256'],
                 tail_draw_sha256=sha(tail_root / 'draw_plan.json'),
                 tail_rule_sha256=tail['rule_freeze_sha256'],
                 old_selected_count=60158, current_new_work_count=496,
                 old_repairs_required=78, cumulative_weight='Native Double W / pi_k, once.',
                 pi_k='1-(1-p0)*(1-p1)*0.99^k; k complete included night epochs.',
                 prior_event_weights_must_be_versioned_again_at_each_complete_epoch=True,
                 full_original_GEN_normalization_applied_once=True,
                 no_partial_epoch_or_failed_job_omission=True,
                 frozen_V10_parent_transport_is_not_physics_qualified=True)
    write_json(base / 'prior_draw_authorities.json', prior)
    selected_handles = []
    jobs = [[] for _ in range(epochs)]
    next_jobs = [2000000 + index * 100000 for index in range(epochs)]
    totals = [dict(selected=0, new=0, reused=0, by_stratum={}) for _ in range(epochs)]
    source_population = []
    bin_totals = {}
    try:
        for index in range(epochs):
            directory = output / f'epoch{index+1:02d}'
            directory.mkdir()
            selected_handles.append(gzip.open(directory / 'selection_ledger.jsonl.gz', 'wt'))
        for source in sources:
            path = Path(source['descriptor'])
            if sha(path) != source['descriptor_sha256']:
                raise RuntimeError('Original source descriptor changed: ' + str(path))
            descriptor = json.loads(path.read_text())
            events = descriptor['event_ids']
            count = source['frozen_accepted_events']
            if len(events) != count or descriptor['gen_sha256'] != source['original_gen_sha256']:
                raise RuntimeError('Original GEN identity arrays differ')
            weights = descriptor.get('weights', [1.0] * count)
            if len(weights) != count or any(not math.isfinite(value) for value in weights):
                raise RuntimeError('Original native weight arrays differ')
            source_key = source['index']
            for raw, expected in prior_by_source.get(source_key, {}).items():
                if events[raw] != expected['event_id'] or float(weights[raw]).hex() != expected['native_double_weight_hex']:
                    raise RuntimeError('Old/current source identity or native Double weight differs')
            descriptor['logical_normalization_record'] = descriptor.get('normalization_record')
            if source.get('ledger'):
                descriptor['normalization_record'] = dict(path=source['ledger']['path'].replace('/eos/home-j/', '/eos/user/j/'),
                                                            sha256=source['ledger']['sha256'])
            for key in ('selected_indices', 'sampling_probabilities', 'sampling'):
                descriptor.pop(key, None)
            descriptor['complete_muon_decays'] = True
            descriptor['original_scan_descriptor_sha256'] = source['descriptor_sha256']
            base_source = base / 'sources' / f'source{source_key:05d}.json'
            write_json(base_source, descriptor)
            base_source_sha = sha(base_source)
            source_population.append(dict(source=source_key, stratum=source['stratum'], events=count,
                                          gen_sha256=descriptor['gen_sha256'], descriptor_sha256=base_source_sha))
            bin_totals.setdefault(source['stratum'], []).append((count, math.fsum(weights)))
            seen = set(prior_by_source.get(source_key, {}))
            scale = sampling['strata'][source['stratum']]['scale']
            for epoch_index, seed in enumerate(rule['seeds_hex256']):
                selected = []
                for raw, event in enumerate(events):
                    draw = uniform_integer(seed, source, raw, event)
                    if draw >= (1 << 256) // 100:
                        continue
                    native = float(weights[raw])
                    p0 = min(1.0, scale * abs(native))
                    p1 = 1.0 if (source_key, raw) in tail_keys else .0001
                    probability = union_probability(p0, p1, epoch_index + 1)
                    row = dict(epoch=epoch_index+1, source=source_key, raw_index=raw, stratum=source['stratum'],
                               source_gen_sha256=descriptor['gen_sha256'], event_id=event,
                               native_double_weight=native, native_double_weight_hex=native.hex(),
                               old_probability=p0, tail_probability=p1, epoch_probability=.01,
                               union_probability=probability, union_probability_hex=probability.hex(),
                               union_gen_weight=native/probability, hash_integer_hex=f'{draw:064x}',
                               reused_original=raw in seen)
                    selected_handles[epoch_index].write(json.dumps(row, separators=(',', ':')) + '\n')
                    totals[epoch_index]['selected'] += 1
                    totals[epoch_index]['by_stratum'].setdefault(source['stratum'], 0)
                    totals[epoch_index]['by_stratum'][source['stratum']] += 1
                    if raw in seen:
                        totals[epoch_index]['reused'] += 1
                    else:
                        selected.append(row)
                        seen.add(raw)
                        totals[epoch_index]['new'] += 1
                for start in range(0, len(selected), 20):
                    rows = selected[start:start+20]
                    job = next_jobs[epoch_index]
                    next_jobs[epoch_index] += 1
                    row = dict(job=job, epoch=epoch_index+1, source=source_key, skip=0, count=len(rows),
                               seed=230000000+job*100, source_gen_sha256=descriptor['gen_sha256'],
                               raw_indices=[value['raw_index'] for value in rows],
                               event_ids=[value['event_id'] for value in rows],
                               native_weights=[value['native_double_weight'] for value in rows],
                               sampling_probabilities=[value['union_probability'] for value in rows],
                               source_base_path=f'sources/source{source_key:05d}.json', source_base_sha256=base_source_sha,
                               response_contract_sha256=response_sha,
                               output_base='/eos/user/j/jniedzie/shift_cmssw_support/ntuple_production/qcd_night_reco_20261014_v1',
                               analysis_release=False, physics_valid=False)
                    if row['seed'] > 900000000:
                        raise RuntimeError('Night job seed exceeds the unchanged worker range')
                    row['event_contract_sha256'] = event_contract(row, response_sha)
                    selection = output / f'epoch{epoch_index+1:02d}' / 'selections' / f'job{job}.json'
                    write_json(selection, row)
                    row['selection_path'] = str(selection.resolve())
                    row['selection_sha256'] = sha(selection)
                    jobs[epoch_index].append(row)
            print(json.dumps(dict(source=source_key, originals=count)), flush=True)
    finally:
        for handle in selected_handles:
            handle.close()
    for name, values in bin_totals.items():
        original = sampling['strata'][name]
        if (sum(value[0] for value in values) != original['original_events'] or
                not math.isclose(math.fsum(value[1] for value in values), original['original_sumw'], rel_tol=1e-12, abs_tol=0)):
            raise RuntimeError('Full original native GEN denominator differs: ' + name)
    write_json(base / 'population_inventory.json', source_population)
    archive = output / 'base_bundle.tar.gz'
    with tarfile.open(archive, 'w:gz') as target:
        for file in sorted(base.rglob('*')):
            if file.is_file():
                target.add(file, arcname=str(file.relative_to(base)))
    metadata = []
    for index, rows in enumerate(jobs):
        directory = output / f'epoch{index+1:02d}'
        job_path = directory / 'jobs.jsonl.gz'
        with gzip.open(job_path, 'wt') as target:
            for row in rows:
                target.write(json.dumps(row, separators=(',', ':'))+'\n')
        ledger_path = directory / 'selection_ledger.jsonl.gz'
        epoch = dict(epoch=index+1, q=.01, seed_hex256=rule['seeds_hex256'][index],
                     population_originals=sum(row['events'] for row in source_population), population_sources=len(sources),
                     jobs_path=str(job_path.resolve()), jobs_sha256=sha(job_path),
                     selection_ledger_path=str(ledger_path.resolve()), selection_ledger_sha256=sha(ledger_path),
                     work_events=totals[index]['new'], work_jobs=len(rows),
                     first_job=rows[0]['job'], last_job=rows[-1]['job'], totals=totals[index],
                     prior_draw_authorities=prior, rule_freeze_sha256=sha(output/'rule_freeze.json'),
                     full_population_sha256=sha(base/'population_inventory.json'))
        write_json(directory / 'draw_manifest.json', epoch)
        epoch['draw_manifest_sha256'] = sha(directory/'draw_manifest.json')
        metadata.append(epoch)
    plan = dict(schema='shift-night-reconstruction-plan-v1', campaign='qcd_night_reco_20261014_v1',
                tag='20261014-qcd-night-reco-v1', response_contract_sha256=response_sha,
                normalization_authority_sha256=sha(authority_root/'authority.json'),
                base_bundle_sha256=sha(archive), base_bundle=str(archive.resolve()),
                base_bundle_url='root://eosuser.cern.ch//eos/user/j/jniedzie/shift_cmssw_support/ntuple_production/qcd_night_reco_20261014_v1/base_bundle.tar.gz',
                rule_freeze_sha256=sha(output/'rule_freeze.json'), epochs=metadata,
                analysis_release=False, normalization_ready=False, physics_valid=False,
                parent_transport_and_physical_response_gate_open=True)
    write_json(output/'night_plan.json', plan)
    print(json.dumps(dict(prepared=True, epochs=epochs, total_new_originals=sum(row['work_events'] for row in metadata),
                          total_jobs=sum(row['work_jobs'] for row in metadata), plan=str(output/'night_plan.json'))), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=14)
    args = parser.parse_args()
    if not 1 <= args.epochs <= 14:
        parser.error('Require one to fourteen bounded independent epochs')
    prepare(args.workspace.resolve(), args.output.resolve(), args.epochs)


if __name__ == '__main__':
    main()
