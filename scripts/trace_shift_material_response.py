#!/usr/bin/env python3
# Code style: AI-code. AI-generated original-event GEN-to-detector loss study.
"""Trace saved GEN tail pairs through the independent material response sample.

No GEN, transport, selection, weights or production state is modified. Missing
saved tracks are explicitly unknown losses, never evidence of absorption.
CSC SimHits precede digitization, electronics, pattern recognition and vertexing.
"""
import argparse
import collections
import concurrent.futures
import gzip
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

from shift_material_response import TARGETS, retained_tail_counts
from shift_material_preselection import uniform


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pair_kinematics(first, second):
    momenta = [first['momentum_GeV'], second['momentum_GeV']]
    energies = [first['energy_GeV'], second['energy_GeV']]
    if any(not math.isfinite(x) for vector in momenta for x in vector):
        raise ValueError('Nonfinite muon momentum')
    if any(not math.isfinite(x) or x < 0 for x in energies):
        raise ValueError('Invalid muon energy')
    p = [math.fsum(values) for values in zip(*momenta)]
    mass2 = math.fsum([math.fsum(energies)**2] + [-x*x for x in p])
    tolerance = 1e-9*max(1., math.fsum(energies)**2)
    if mass2 < -tolerance:
        raise ValueError('Spacelike pair: check momentum/energy units')
    return dict(mass=math.sqrt(max(0., mass2)), pt=math.hypot(*p[:2]))


def tail_flags(kinematics):
    return {name: kinematics[field] > value for name, (field, value) in TARGETS.items()}


def stations(muon, number):
    return all(len(muon['CSC_station_hits'].get(side, {})) >= number
               for side in ('plus', 'minus'))


def trace_event(feature, audit, response):
    if not (feature['event_id'] == audit['event_id'] == response['event_id']):
        raise ValueError('GEN/SIM/RECO identities differ')
    if not (feature['native_weight'] == audit['native_weight'] == response['native_weight']):
        raise ValueError('GEN/SIM/RECO native weights differ')
    matches = collections.defaultdict(list)
    for muon in audit['muons']:
        match = muon['matched_GEN_muon']
        if match:
            matches[match['barcode']].append(muon)
    flights = {parent['barcode']: parent for parent in feature['parent_flights']}
    parents = {parent['barcode']: parent for parent in audit['GEN_parents']}
    pairs = []
    for first, second in itertools.combinations(feature['muons'], 2):
        if first['pdg']*second['pdg'] >= 0:
            continue
        kinematics = pair_kinematics(first, second)
        flags = tail_flags(kinematics)
        tracks = [(a, b) for a in matches[first['barcode']] for b in matches[second['barcode']]
                  if a['track_id'] != b['track_id']]
        parent_ids = sorted(set(first['transport_parent_barcodes']) |
                            set(second['transport_parent_barcodes']))
        parent_material = [flights[number]['material'] for number in parent_ids]
        depths = [material.get('interaction_depth_proxy') for material in parent_material]
        known_depth = math.fsum(x for x in depths if x is not None)
        stages = dict(
            GEN_OS=True,
            GEN_incoming=all(muon['incoming_towards_CMS_z0'] for muon in (first, second)),
            GEN_approaches_entry=all(muon['straight_line_towards_CMS_entry']
                                     for muon in (first, second)),
            matched_saved_G4_muons=bool(tracks),
            both_muons_any_CSC=any(all(any(muon['CSC_station_hits'].get(side, {})
                                          for side in ('plus', 'minus')) for muon in pair)
                                   for pair in tracks),
            both_muons_both_endcaps_1station=any(all(stations(muon, 1) for muon in pair)
                                                for pair in tracks),
            both_muons_both_endcaps_2stations=any(all(stations(muon, 2) for muon in pair)
                                                 for pair in tracks))
        pairs.append(dict(barcodes=[first['barcode'], second['barcode']],
            **kinematics, targets=flags, stages=stages,
            muons=[dict(barcode=muon['barcode'], pdg=muon['pdg'], p_GeV=muon['p_GeV'],
                        energy_GeV=muon['energy_GeV'], birth_cm=muon['birth_cm'],
                        momentum_GeV=muon['momentum_GeV'],
                        projected_CMS_entry_cm=muon['projected_CMS_entry_cm'],
                        material_to_CMS_entry=muon['material_to_CMS_entry'],
                        matched_saved_G4=matches[muon['barcode']]) for muon in (first, second)],
            transport_parent_barcodes=parent_ids,
            known_parent_interaction_depth=known_depth,
            parent_material_complete=all(x.get('full_saved_chord_coverage', False)
                                         for x in parent_material),
            parent_outcomes=[dict(barcode=number, pdg=parents[number]['pdg'],
                                  measured_outcome=parents[number]['measured_outcome'],
                                  saved_G4_track_ids=[track['track_id'] for track in parents[number]['saved_G4_tracks']],
                                  child_vertices=parents[number]['child_vertices'])
                             for number in parent_ids if number in parents]))
    return dict(event_id=feature['event_id'], source_id=response['source_id'],
                original_source_sha256=response['original_source_sha256'],
                stratum=response['stratum'], native_weight=response['native_weight'],
                benchmark_probability=response['benchmark_probability'],
                candidate_probability=response['candidate_probability'],
                GEN_muons=feature['GEN_muons'], ranking=feature['ranking'], GEN_OS_pairs=pairs,
                material_summary={name: audit[name] for name in (
                    'actual_saved_muons', 'muons_with_any_CSC_hits',
                    'muons_with_required_CSC_stations_each_endcap',
                    'opposite_charge_supported_pairs')},
                all_saved_G4_muons=audit['muons'], retained_pairs=response['retained_pairs'],
                retained_tail_counts=retained_tail_counts(response['retained_pairs']))


def fetch_case(args):
    directory, sample, scratch = args
    job = directory/'jobs'/sample['id']
    result = json.loads((job/'result.json').read_text())
    if not result['complete'] or result['exit_code'] != 0:
        raise ValueError('Incomplete response: '+sample['id'])
    if sha(job/'request.json') != result['request_sha256']:
        raise ValueError('Response request changed')
    publication = next(x for x in result['published'] if x['url'].endswith('/evidence.tar.gz'))
    env = dict(os.environ, PATH='/usr/bin:/bin')
    for name in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME', 'ROOTSYS'):
        env.pop(name, None)
    with tempfile.TemporaryDirectory(prefix=sample['id']+'-', dir=scratch) as temp:
        payload = Path(temp)/'evidence.tar.gz'
        subprocess.run(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32', publication['url'], str(payload)],
                       env=env, timeout=600, check=True)
        if sha(payload) != publication['sha256'] or payload.stat().st_size != publication['bytes']:
            raise ValueError('Evidence readback differs: '+sample['id'])
        with tarfile.open(payload) as archive:
            features = json.loads(gzip.decompress(archive.extractfile('features.json.gz').read()))
            audit = json.loads(gzip.decompress(archive.extractfile('material_audit.json.gz').read()))
            response = json.load(archive.extractfile('response.json'))
        if not audit['passed'] or response['candidate_gate_applied_to_detector_sample']:
            raise ValueError('Invalid independent detector response')
        collections_to_check = [features['events'], audit['events'], response['events']]
        if any([row['event_id'] for row in group] != sample['event_ids'] for group in collections_to_check):
            raise ValueError('Evidence population differs from frozen sample')
        rows = [trace_event(*values) for values in zip(*collections_to_check)]
        return rows, publication


def summarize(rows):
    totals = collections.Counter()
    targets = {name: collections.Counter() for name in TARGETS}
    for row in rows:
        totals['originals'] += 1
        pairs = row['GEN_OS_pairs']
        totals['GEN_OS_pair_events'] += bool(pairs)
        totals['GEN_OS_pairs'] += len(pairs)
        totals['saved_G4_muon_events'] += row['material_summary']['actual_saved_muons'] > 0
        totals['any_CSC_muon_events'] += row['material_summary']['muons_with_any_CSC_hits'] > 0
        totals['any_SIM_OS_supported_pair_events'] += row['material_summary']['opposite_charge_supported_pairs'] > 0
        totals['retained_pair_events'] += bool(row['retained_pairs'])
        totals['retained_BothBoth_OS_events'] += any(pair['isOS'] and pair['topologyMin'] == pair['topologyMax'] == 2
                                                    for pair in row['retained_pairs'])
        for name in TARGETS:
            tail = [pair for pair in pairs if pair['targets'][name]]
            targets[name]['GEN_pairs'] += len(tail)
            for stage in ('GEN_OS', 'GEN_incoming', 'GEN_approaches_entry', 'matched_saved_G4_muons',
                          'both_muons_any_CSC', 'both_muons_both_endcaps_1station',
                          'both_muons_both_endcaps_2stations'):
                targets[name][stage+'_events'] += any(pair['stages'][stage] for pair in tail)
            targets[name]['reco_OS_BothBoth_tail_events'] += row['retained_tail_counts'][name] > 0
    return dict(totals=dict(totals), targets={name: dict(value) for name, value in targets.items()})


def scan_gen(path, receipt_path, output):
    """Select all saved OS GEN-tail originals plus a frozen 1% remainder control.

    This is an intentional diagnostic sample. Every tail original has p=1;
    other originals have p=.01. No material correction, second filter factor,
    repeated lifetime draw or inference from an old response is involved.
    """
    from DataFormats.FWLite import Events
    from audit_shift_material_scout import identity
    from shift_gen_material_features import read_hepmc
    receipt = json.loads(receipt_path.read_text())
    audit = receipt['semantic_audit']
    if not receipt['complete'] or receipt['generator_profile'] != 'shift-qcd-mu-enriched-cylinder-v1':
        raise ValueError('Needs a completed mu-enriched source')
    if sha(path) != receipt['artifacts']['gen.root']['sha256']:
        raise ValueError('Source GEN differs from its native audit')
    expected = {tuple(eid): (weight, scale) for eid, weight, scale in
                zip(audit['event_ids'], audit['weights'], audit['scale'])}
    if len(expected) != audit['events']:
        raise ValueError('Repeated source identities or incomplete source audit')
    selected, seen, counts = [], set(), collections.Counter()
    target_counts = {name: collections.Counter() for name in TARGETS}
    for index, event in enumerate(Events(str(path))):
        eid = identity(event)
        rows, weight, weights, units = read_hepmc(event, 'generatorSmeared')
        if eid in seen or eid not in expected or weight != expected[eid][0]:
            raise ValueError('GEN scan changed source identities or native weights')
        seen.add(eid)
        muons = [row for row in rows if abs(row['pdg']) == 13 and row['status'] == 1]
        pairs = []
        for first, second in itertools.combinations(muons, 2):
            if first['pdg']*second['pdg'] >= 0:
                continue
            kin = pair_kinematics(first, second)
            flags = tail_flags(kin)
            if any(flags.values()):
                incoming = all(muon['birth_cm'] is not None and
                               muon['birth_cm'][2]*muon['momentum_GeV'][2] < 0
                               for muon in (first, second))
                pairs.append(dict(barcodes=[first['barcode'], second['barcode']],
                                  **kin, targets=flags, incoming=incoming))
        for name in TARGETS:
            target_counts[name]['tail_events'] += any(pair['targets'][name] for pair in pairs)
            target_counts[name]['incoming_tail_events'] += any(pair['targets'][name] and pair['incoming']
                                                              for pair in pairs)
        counts['originals'] += 1
        counts['tail_originals'] += bool(pairs)
        control = uniform(['GEN-tail-trace-control-20261021-v1',
                           receipt['artifacts']['gen.root']['sha256'], list(eid)]) < .01
        if pairs or control:
            selected.append(dict(event_id=list(eid), raw_index=index, native_weight=weight,
                pthat_GeV=expected[eid][1], sampling_probability=1. if pairs else .01,
                group='GEN_tail' if pairs else 'control', GEN_tail_pairs=pairs,
                stable_GEN_muons=muons))
    if seen != set(expected):
        raise ValueError('GEN scan missed audited source originals')
    report = dict(schema='shift-GEN-tail-diagnostic-selection-v1', complete=True,
        generator_profile=receipt['generator_profile'], source_GEN_sha256=receipt['artifacts']['gen.root']['sha256'],
        source_receipt_sha256=sha(receipt_path), source_request=receipt['request'],
        full_source_trials=audit['full_source_trial_denominator'], sigma_nd_pb=receipt['ledger']['sigma_nd_mb']*1e9,
        counts=dict(counts), targets={key: dict(value) for key, value in target_counts.items()},
        selected_events=selected, control_seed='GEN-tail-trace-control-20261021-v1',
        sampling_weight='native W/p; pool full source trials once within each disjoint pThat bin',
        saved_decays_unchanged=True, no_second_filter_efficiency=True,
        physics_normalization_ready=False)
    output.mkdir(parents=True, exist_ok=False)
    with gzip.open(output/'GEN_tail_selection.json.gz', 'wt') as stream:
        json.dump(report, stream, allow_nan=False)
    brief = {key: value for key, value in report.items() if key != 'selected_events'}
    brief['selected_events'] = len(selected)
    (output/'summary.json').write_text(json.dumps(brief, indent=2)+'\n')
    print(json.dumps(brief, indent=2), flush=True)


def run(directory, output, scratch, workers):
    plan = json.loads((directory/'plan.json').read_text())
    output.mkdir(parents=True, exist_ok=False)
    all_rows, publications, seen = [], [], set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        work = [(directory, sample, scratch) for sample in plan['samples']]
        for rows, publication in pool.map(fetch_case, work):
            for row in rows:
                identity = (row['original_source_sha256'], tuple(row['event_id']))
                if identity in seen:
                    raise ValueError('Repeated original; never count it twice')
                seen.add(identity)
            all_rows.extend(rows)
            publications.append(publication)
            if len(publications) % 20 == 0:
                print(json.dumps(dict(archives=len(publications), originals=len(all_rows))), flush=True)
    # Pool full source exposure exactly once per pThat stratum, not per job.
    exposure = collections.Counter()
    for source in plan['sources'].values():
        exposure[source['stratum']] += source['full_source_trials']
    for row in all_rows:
        source = plan['sources'][row['source_id']]
        row['rate_weight_pb'] = row['native_weight']*source['sigma_nd_pb']/exposure[row['stratum']]
    trace = output/'event_traces.jsonl.gz'
    with gzip.open(trace, 'wt') as stream:
        for row in all_rows:
            stream.write(json.dumps(row, allow_nan=False, separators=(',', ':'))+'\n')
    by_bin = {name: summarize([row for row in all_rows if row['stratum'] == name])
              for name in sorted(exposure)}
    report = dict(schema='shift-material-GEN-tail-trace-v1', complete=True,
        response_plan=str(directory/'plan.json'), response_plan_sha256=sha(directory/'plan.json'),
        all_bins=summarize(all_rows), by_bin=by_bin, full_source_trials_by_bin=dict(exposure),
        source_saved_events={name: source['saved_events'] for name, source in plan['sources'].items()},
        source_inclusion_scope='Nine sources; uniform within each saved source, not the full production.',
        event_traces_sha256=sha(trace), evidence_publications=publications,
        interpretation=['Counts are unique benchmark originals, not independent pair counts.',
                        'GEN and matched-track columns refer to the same original GEN pair.',
                        'Reco tail column includes any OS Both-Both pair; it is not a truth-matched efficiency.',
                        'Missing saved G4 muons or parents do not prove absorption.',
                        'Straight direction is diagnostic, not measured detector acceptance.',
                        'Finite-cylinder GEN and bounded incoming overlay do not establish inclusive normalization.'],
        physics_normalization_ready=False)
    (output/'summary.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(json.dumps(report['all_bins'], indent=2), flush=True)


def collect_scans(directory, output):
    """Collect a declared existing-source census, keeping full source exposure."""
    plan = json.loads((directory/'plan.json').read_text())
    targets = {name: dict(raw_events=0, incoming_events=0, weights=[], incoming_weights=[])
               for name in TARGETS}
    cases, trials, originals, cross_sections = [], 0, 0, set()
    source_evidence, seen = [], set()
    for source in plan['sources']:
        job = directory/'jobs'/source['id']
        result, status = (json.loads((job/name).read_text()) for name in ('result.json', 'status.json'))
        if not result['complete'] or not status['complete']:
            raise ValueError('Incomplete GEN census worker: '+source['id'])
        if sha(job/'request.json') != source['worker_request_sha256']:
            raise ValueError('GEN census request changed')
        if sha(job/'return_payload.tar.gz') != status['return_payload_sha256']:
            raise ValueError('GEN census returned evidence changed')
        with tarfile.open(job/'return_payload.tar.gz') as archive:
            selection = json.loads(gzip.decompress(archive.extractfile('tail_scan/GEN_tail_selection.json.gz').read()))
        if (selection['source_GEN_sha256'] != source['GEN_sha256']
                or selection['source_receipt_sha256'] != source['source_receipt_sha256']
                or selection['full_source_trials'] != source['full_source_trials']
                or selection['counts']['originals'] != source['saved_events']):
            raise ValueError('GEN census original source/exposure differs')
        trials += selection['full_source_trials']
        originals += selection['counts']['originals']
        cross_sections.add(selection['sigma_nd_pb'])
        source_evidence.append(dict(source_id=source['id'], source_GEN_sha256=source['GEN_sha256'],
                                    return_payload_sha256=status['return_payload_sha256']))
        observed = collections.Counter()
        incoming_counts = collections.Counter()
        for event in selection['selected_events']:
            identity = (source['GEN_sha256'], tuple(event['event_id']))
            if identity in seen:
                raise ValueError('Repeated GEN census original')
            seen.add(identity)
            for name in TARGETS:
                pairs = [pair for pair in event['GEN_tail_pairs'] if pair['targets'][name]]
                if pairs:
                    if event['sampling_probability'] != 1.:
                        raise ValueError('Census must retain every GEN tail original')
                    observed[name] += 1
                    targets[name]['weights'].append(event['native_weight'])
                    if any(pair['incoming'] for pair in pairs):
                        incoming_counts[name] += 1
                        targets[name]['incoming_weights'].append(event['native_weight'])
            if any(pair['targets']['mass_gt20'] for pair in event['GEN_tail_pairs']):
                cases.append(dict(event, source_id=source['id'], source_GEN_sha256=source['GEN_sha256'],
                                  source_receipt_path=source['source_receipt_path']))
        for name in TARGETS:
            if (observed[name] != result['targets'][name]['tail_events'] or
                    incoming_counts[name] != result['targets'][name]['incoming_tail_events']):
                raise ValueError('Selected GEN tail population differs from complete source scan')
            targets[name]['raw_events'] += observed[name]
            targets[name]['incoming_events'] += incoming_counts[name]
    if trials != plan['full_source_trials'] or originals != plan['saved_events'] or len(cross_sections) != 1:
        raise ValueError('Census exposure or common ND cross section differs')
    sigma = cross_sections.pop()
    for record in targets.values():
        for field in ('weights', 'incoming_weights'):
            values = record.pop(field)
            first, second = math.fsum(values), math.fsum(x*x for x in values)
            prefix = 'incoming_' if field == 'incoming_weights' else ''
            record[prefix+'GEN_filtered_cross_section_pb'] = sigma*first/trials
            record[prefix+'effective_originals'] = first*first/second if second else 0.
    output.mkdir(parents=True, exist_ok=False)
    with gzip.open(output/'GEN_mass_tail_cases.json.gz', 'wt') as stream:
        json.dump(dict(events=cases, source_plan_sha256=sha(directory/'plan.json'),
                       diagnostic_only=True, saved_decays_unchanged=True), stream, allow_nan=False)
    report = dict(complete=True, saved_GEN_originals=originals, full_source_trials=trials,
        targets=targets, source_evidence=source_evidence, existing_GEN_reused=True,
        physics_normalization_ready=False,
        scope='Conditional finite-cylinder saved-GEN census. GEN rates are not material/reconstruction yields.')
    (output/'summary.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'source_evidence'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('response_directory', type=Path, nargs='?')
    parser.add_argument('--gen-input', type=Path)
    parser.add_argument('--source-receipt', type=Path)
    parser.add_argument('--collect-scans', action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--scratch', type=Path)
    parser.add_argument('--workers', type=int, default=4)
    options = parser.parse_args()
    if not 1 <= options.workers <= 8:
        parser.error('Use one to eight concurrent evidence readers')
    if options.collect_scans:
        if not options.response_directory or options.gen_input:
            parser.error('Census collection needs the declared scan directory')
        collect_scans(options.response_directory.resolve(), options.output.resolve())
    elif options.gen_input:
        if options.response_directory or not options.source_receipt:
            parser.error('GEN scan needs --source-receipt and no response directory')
        scan_gen(options.gen_input.resolve(), options.source_receipt.resolve(), options.output.resolve())
    else:
        if not options.response_directory or not options.scratch:
            parser.error('Response trace needs a response directory and --scratch')
        options.scratch.mkdir(parents=True, exist_ok=True)
        run(options.response_directory.resolve(), options.output.resolve(), options.scratch.resolve(), options.workers)
