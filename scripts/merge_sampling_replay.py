#!/usr/bin/env python3
"""Merge complete weighted replay bins; export parent normalization and keyed weights.

Only identifiers and topology counts are inspected. Native genWeight and the
repeated Runs counters are NOT sampling-aware; consumers must use the sidecars.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from merge_sampling_nano import inspect
from validate_sampling_ledger import validate


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def normalization(parent, ledger, log):
    """Restricted to a complete, unit-weight parent, with independent log checks."""
    attempts = ledger['framework_attempts']
    saved = ledger['upstream_saved_events']
    if (parent['contract']['sample'] != 'qcdmu' or
            attempts != parent['contract']['attempted_events'] or
            attempts != parent['generation']['attempted_events'] or
            saved != parent['generation']['accepted_events'] or
            not 0 < saved <= attempts or len(parent['generation']['runs']) != 1):
        raise ValueError('Require one complete QCD parent, not a prefix or combined run')
    sigma = parent['generation']['runs'][0]['internal_xsec_pb']
    error = parent['generation']['runs'][0]['error_pb']
    if not math.isfinite(sigma) or sigma <= 0 or not math.isfinite(error) or error < 0:
        raise ValueError('Invalid cross section')
    for kind in ('taking into account weights', 'event-level'):
        matches = re.findall(r'Filter efficiency \(' + re.escape(kind) +
                             r'\)= \((\d+)\) / \((\d+)\)', log)
        if not matches or tuple(map(int, matches[-1])) != (saved, attempts):
            raise ValueError('Parent filter counters disagree')
    for label, expected in [('Before Filter: total cross section', sigma),
                            ('After filter: final cross section', sigma * saved / attempts)]:
        values = re.findall(re.escape(label) + r' = ([0-9.eE+-]+) \+- [0-9.eE+-]+ pb', log)
        if not values or not math.isclose(float(values[-1]), expected, rel_tol=5e-4):
            raise ValueError('Parent cross section disagrees with generator log')
    return dict(before_filter_pb=sigma, before_filter_error_pb=error,
                after_filter_pb=sigma * saved / attempts,
                filter_efficiency=saved / attempts, unique_parent_attempts=attempts,
                upstream_saved_events=saved, simulated_events=ledger['selected_events'],
                event_weight_pb_formula='before_filter_pb / unique_parent_attempts / sampling_probability',
                unit_sampling_weight_pb=sigma / attempts,
                warning='Do not apply the upstream filter efficiency again. Do not divide by the simulated count or realized sum of weights. Do not sum replay Runs counters.')


def selected_weights(ledger, identities, norm):
    choices = {tuple(row['id']): row for row in ledger['rows'] if row['selected']}
    if len(choices) != ledger['selected_events'] or choices.keys() != identities:
        raise ValueError('Sampling weights do not exactly cover merged identities')
    result = []
    for identity, choice in choices.items():
        q = choice['probability']
        if not math.isfinite(q) or not 0 < q <= 1 or choice['inverse_probability'] != 1 / q:
            raise ValueError('Invalid sampling probability or inverse weight')
        result.append(dict(id=list(identity), sampling_probability=q, sampling_weight=1 / q,
                           event_weight_pb=norm['unit_sampling_weight_pb'] / q))
    return result


def write_json(path, content):
    path.write_text(json.dumps(content, indent=2) + '\n')


def publish(source, destination):
    """Exclusive publication; never overwrite previous campaign artifacts."""
    if destination.exists():
        raise ValueError(f'Refusing existing output: {destination}')
    temporary = destination.with_name(destination.name + '.partial')
    with temporary.open('xb') as output, source.open('rb') as input_file:
        shutil.copyfileobj(input_file, output)
    if digest(temporary) != digest(source):
        raise ValueError('Published bytes differ')
    temporary.rename(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--audit-directory', type=Path, required=True)
    parser.add_argument('--base', type=Path, default=Path('/eos/home-j/jniedzie/shift_cmssw/qcd'))
    args = parser.parse_args()
    audit_dir = args.audit_directory
    summary = json.loads((audit_dir / 'summary.json').read_text())
    stage = json.loads((audit_dir / 'four_stage_audit.json').read_text())
    bookkeeping_audit = json.loads((audit_dir / 'bookkeeping_audit.json').read_text())
    if not summary['complete'] or stage['status'] != 'validated' or bookkeeping_audit['status'] != 'validated':
        raise ValueError('Require successful complete audits')
    if {r['report'] for r in stage['rows']} != {r for g in summary['groups'] for r in g['reports']}:
        raise ValueError('Four-stage audit coverage differs')
    manifest = json.loads(args.manifest.read_text())
    groups = {g['campaign']: g for g in summary['groups']}
    if set(groups) != {e['campaign'] for e in manifest}:
        raise ValueError('Manifest and summary campaigns differ')
    bookkeeping = [json.loads(line) for line in (audit_dir / 'bookkeeping.jsonl').read_text().splitlines()]
    results = []
    for entry in manifest:
        group = groups[entry['campaign']]
        campaign = args.base / entry['campaign']
        ledger_path = Path(entry['ledger'])
        ledger = json.loads(ledger_path.read_text())
        validate(ledger)
        parent_path = Path(ledger['source_report'])
        parent = json.loads(parent_path.read_text())
        parent_log = parent_path.parent / 'step1.log'
        norm = normalization(parent, ledger, parent_log.read_text())
        directory = campaign / 'samples/step4_merged'
        output = directory / f'ntuple_sampling_complete_{ledger["selected_events"]}events.root'
        if output.exists() or output.with_suffix('.json').exists():
            raise ValueError(f'Refusing existing merge: {output}')
        with tempfile.TemporaryDirectory(prefix='shift_weighted_merge_') as scratch:
            scratch = Path(scratch)
            expected, inputs, staged, schema = {}, [], [], None
            for index, report_path in enumerate(group['reports']):
                report_path = Path(report_path)
                report = json.loads(report_path.read_text())
                if report['status'] != 'validated' or report['reconstructed_mass_read']:
                    raise ValueError('Invalid source report')
                if [r['sampling'] for r in report['rows']] != ledger['chunks'][index]:
                    raise ValueError('Replay choices differ from ledger')
                part = report_path.parent.name.removeprefix('part')
                source = report_path.parents[2] / 'samples/step4' / f'events_NanoAOD_part_{part}.root'
                local = scratch / f'input_{index:04d}.root'
                shutil.copy2(source, local)
                actual, current_schema = inspect(local)
                reference = {tuple(row['id']): row['reco'] for row in report['rows']}
                if actual != reference or expected.keys() & actual.keys():
                    raise ValueError('Input identities or topology counts differ')
                if schema is not None and current_schema != schema:
                    raise ValueError('Input schemas differ')
                schema = current_schema
                expected.update(actual)
                inputs.append(dict(path=str(source), sha256=digest(local), bytes=local.stat().st_size,
                                   report=str(report_path), report_sha256=digest(report_path)))
                staged.append(str(local))
            weights = selected_weights(ledger, expected.keys(), norm)
            records = [r for r in bookkeeping if r['campaign'] == entry['campaign']]
            if len(records) != norm['unique_parent_attempts'] or {tuple(r['id']) for r in records if r['state'] == 'processed'} != expected.keys():
                raise ValueError('Bookkeeping coverage differs')
            local_output = scratch / output.name
            with (scratch / 'hadd.log').open('w') as log:
                subprocess.run(['hadd', '-f', '-j', '2', str(local_output), *staged],
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            actual, merged_schema = inspect(local_output)
            if actual != expected or merged_schema != schema:
                raise ValueError('Merged union or schema differs')
            counts = Counter()
            for row in actual.values():
                counts.update(events=1, events_muon=row['muons'] > 0,
                              events_vertex=row['vertices'] > 0, events_both_both=row['both_both'] > 0)
            norm.update(parent_report=str(parent_path), parent_report_sha256=digest(parent_path),
                        parent_log=str(parent_log), parent_log_sha256=digest(parent_log),
                        physics_valid=False, normalization_ready=False, sampling_weights_required=True)
            write_json(scratch / 'cross_sections.json', norm)
            subprocess.run(['bash', str(Path(__file__).with_name('update_cross_section.sh')),
                            str(parent_log), str(scratch / 'cross_sections.txt'), 'qcd'], check=True)
            shutil.copy2(ledger_path, scratch / 'sampling_ledger.json')
            for name, rows in [('sampling_weights.jsonl', weights), ('sampling_bookkeeping.jsonl', records)]:
                (scratch / name).write_text(''.join(json.dumps(r, separators=(',', ':')) + '\n' for r in rows))
            (scratch / 'NORMALIZATION_README.txt').write_text(
                'Weighted QCD replay pilot; ATLAS proxy, no pileup/trigger. Not physics-ready.\n'
                'Join sampling_weights.jsonl to Events by (run, luminosityBlock, event).\n'
                'Fill cross-section plots with event_weight_pb; for yields multiply by luminosity in pb^-1.\n'
                'Equivalent formula: before_filter_pb / 5000 * sampling_weight for these full parents.\n'
                'Do NOT apply filter efficiency twice, use selected-event counts as denominator,\n'
                'or sum duplicated parent Runs counters. Native genWeight lacks sampling weights.\n'
                'The current histogrammer does not yet consume these sampling sidecars.\n'
                'Do not combine these files with their older 500-attempt replay subsets.\n')
            directory.mkdir(parents=True, exist_ok=True)
            sidecars = {}
            for name in ('cross_sections.json', 'cross_sections.txt', 'sampling_ledger.json',
                         'sampling_weights.jsonl', 'sampling_bookkeeping.jsonl', 'NORMALIZATION_README.txt'):
                target = campaign / name
                publish(scratch / name, target)
                sidecars[name] = dict(path=str(target), sha256=digest(target))
            publish(local_output, output)
            published, published_schema = inspect(output)
            if published != expected or published_schema != schema:
                raise ValueError('Reopened published output differs')
            info = dict(status='validated', output=str(output), campaign=entry['campaign'],
                        events=len(actual), counts=dict(counts), inputs=inputs, schema_sha256=schema,
                        sha256=digest(output), sidecars=sidecars, normalization=norm,
                        sampling_weights_required=True, native_genWeight_includes_sampling=False,
                        physics_valid=False, normalization_ready=False, reconstructed_mass_read=False)
            write_json(scratch / 'merge.json', info)
            publish(scratch / 'merge.json', output.with_suffix('.json'))
            results.append(info)
            write_json(audit_dir / 'merges.json', results)
            print(entry['campaign'], dict(counts), str(output), flush=True)
    (audit_dir / 'ready_inputs.txt').write_text(''.join(r['output'] + '\n' for r in results))


if __name__ == '__main__':
    main()
