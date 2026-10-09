#!/usr/bin/env python3
"""Additive PDG-32 Nano diagnostics; never changes TEA or reco selections.

Denominators count uniquely identifiable direct dimuon decays in retained Nano
truth, not full GEN exposure. Hit-based truth matching is primary; angular
matches are reported separately. Counts from small pilots are not physical
efficiency, transport acceptance, or final recording acceptance measurements.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

MUON_MASS_GEV = 0.1056583755
GEN_FIELDS = ('pdgId', 'status', 'genPartIdxMother', 'pt', 'eta', 'phi', 'mass', 'vx', 'vy', 'vz')
MUON_FIELDS = ('hitGenPartIdx', 'genPartIdx', 'charge')
PAIR_FIELDS = ('muonIdx1', 'muonIdx2', 'mass', 'vx', 'vy', 'vz', 'isOS')


def ancestor_path(index, particles):
    """Validate one stored mother chain; cycles and invalid references fail."""
    path, seen = [], set()
    while index != -1:
        if index < 0 or index >= len(particles) or index in seen:
            raise ValueError('Invalid or cyclic GenPart mother history')
        seen.add(index)
        path.append(index)
        index = particles[index]['genPartIdxMother']
    return path


def momentum(particle, mass=None):
    pt, eta, phi = (particle[k] for k in ('pt', 'eta', 'phi'))
    px, py = pt * math.cos(phi), pt * math.sin(phi)
    pz = particle['pz'] if 'pz' in particle else pt * math.sinh(eta)
    mass = particle['mass'] if mass is None else mass
    return [px, py, pz, math.sqrt(px * px + py * py + pz * pz + mass * mass)]


def direct_pairs(particles):
    """Group A' copies, keeping direct and cascade muons distinct.

    A retained history with multiple terminal muons of either charge, or
    daughters attached to different A' copies, is ambiguous and excluded.
    Single-mother Nano ancestry cannot replace the full generator graph.
    """
    histories, root_by_parent = {}, {}
    for index, particle in enumerate(particles):
        ancestor_path(index, particles)
        if abs(particle['pdgId']) != 32:
            continue
        current = index
        while particles[current]['genPartIdxMother'] >= 0:
            parent = particles[current]['genPartIdxMother']
            if abs(particles[parent]['pdgId']) != 32:
                break
            current = parent
        root_by_parent[index] = current
        histories.setdefault(current, {'history_indices': [], 'minus': [], 'plus': []})['history_indices'].append(index)
    for index, particle in enumerate(particles):
        if abs(particle['pdgId']) != 13 or particle['status'] != 1:
            continue
        copies, current = [index], index
        parent = particle['genPartIdxMother']
        while parent >= 0 and particles[parent]['pdgId'] == particle['pdgId']:
            copies.append(parent)
            current = parent
            parent = particles[parent]['genPartIdxMother']
        if parent not in root_by_parent:
            continue
        leg = {'stable_index': index, 'muon_copy_indices': copies,
               'direct_child_index': current, 'decay_parent_index': parent}
        histories[root_by_parent[parent]]['minus' if particle['pdgId'] == 13 else 'plus'].append(leg)
    pairs, ambiguous, without_pair = [], [], []
    for root, history in sorted(histories.items()):
        if not history['minus'] and not history['plus']:
            without_pair.append(root)
            continue
        if (len(history['minus']) != 1 or len(history['plus']) != 1
                or history['minus'][0]['decay_parent_index'] != history['plus'][0]['decay_parent_index']):
            ambiguous.append({'root_index': root, **history})
            continue
        minus, plus = history['minus'][0], history['plus'][0]
        parent_index = minus['decay_parent_index']
        vertices = [[particles[leg['direct_child_index']][k] for k in ('vx', 'vy', 'vz')]
                    for leg in (minus, plus)]
        separation = math.dist(*vertices)
        if separation > 1e-3:
            raise ValueError('Direct opposite-charge daughters have inconsistent birth vertices')
        decay = [(a + b) / 2 for a, b in zip(*vertices)]
        parent = particles[parent_index]
        production = [parent[k] for k in ('vx', 'vy', 'vz')]
        p = momentum(parent)
        norm = math.sqrt(sum(v * v for v in p[:3]))
        displacement = [a - b for a, b in zip(decay, production)]
        projected = sum(a * b for a, b in zip(displacement, p[:3])) / norm if norm else None
        transverse_residual = (math.sqrt(sum((a - projected * b / norm)**2
                                            for a, b in zip(displacement, p[:3]))) if norm else None)
        # Vertices are Float Nano columns in cm. A zero/sub-ulp displacement
        # does not measure a prompt lifetime; timed HepMC remains authoritative.
        resolution = max(1e-7, max(abs(v) for v in production + decay) * 2**-23)
        spatial_length = (10 * projected * parent['mass'] / norm
                          if norm and projected is not None and projected > resolution
                          and transverse_residual <= 5 * resolution else None)
        muons = [momentum(particles[leg['stable_index']], MUON_MASS_GEV) for leg in (minus, plus)]
        summed = [a + b for a, b in zip(*muons)]
        mass2 = summed[3]**2 - sum(v * v for v in summed[:3])
        if mass2 < -1e-6:
            raise ValueError('Unphysical direct dimuon four-vector')
        pairs.append({'history_root_index': root, 'history_indices': history['history_indices'],
                      'decay_parent_index': parent_index, 'minus': minus, 'plus': plus,
                      'parent_mass_gev': parent['mass'], 'final_muon_pair_mass_gev': math.sqrt(max(0., mass2)),
                      'production_vertex_cm': production, 'decay_vertex_cm': decay,
                      'daughter_vertex_separation_cm': separation,
                      'proper_length_spatial_proxy_mm': spatial_length,
                      'transverse_flight_residual_cm': transverse_residual,
                      'spatial_proxy_resolution_cm': resolution,
                      'proper_lifetime_from_Nano': None, 'decay_time_from_Nano': None})
    return pairs, {'retained_parent_histories': len(histories), 'ambiguous_histories': ambiguous,
                   'histories_without_direct_pair': without_pair}


def diagnose_event(particles, reco_muons, reco_pairs, event_id, weight=1.):
    for collection in (particles, reco_muons, reco_pairs):
        if any(not math.isfinite(value) for item in collection for value in item.values()):
            raise ValueError('Non-finite persisted Nano value')
    for vertex in reco_pairs:
        a, b = vertex['muonIdx1'], vertex['muonIdx2']
        if a == b or min(a, b) < 0 or max(a, b) >= len(reco_muons):
            raise ValueError('Invalid ShiftDimuonVertex muon indices')
    pairs, coverage = direct_pairs(particles)
    for pair in pairs:
        for name in ('minus', 'plus'):
            leg = pair[name]
            # Both producer match fields address status-1 final GenPart rows.
            leg['hit_matched_reco_indices'] = [i for i, muon in enumerate(reco_muons)
                                              if muon['hitGenPartIdx'] == leg['stable_index']]
            leg['angular_matched_reco_indices'] = [i for i, muon in enumerate(reco_muons)
                                                  if muon['genPartIdx'] == leg['stable_index']]
        matched_vertices = []
        for index, vertex in enumerate(reco_pairs):
            a, b = vertex['muonIdx1'], vertex['muonIdx2']
            truth = {reco_muons[a]['hitGenPartIdx'], reco_muons[b]['hitGenPartIdx']}
            if truth != {pair['minus']['stable_index'], pair['plus']['stable_index']}:
                continue
            matched_vertices.append({'vertex_index': index, **vertex,
                                     'reco_charge_opposite': reco_muons[a]['charge'] * reco_muons[b]['charge'] == -1,
                                     'mass_residual_gev': vertex['mass'] - pair['final_muon_pair_mass_gev'],
                                     'vertex_residual_cm': [vertex[k] - pair['decay_vertex_cm'][j]
                                                            for j, k in enumerate(('vx', 'vy', 'vz'))]})
        pair['hit_matched_vertices'] = matched_vertices
        pair['both_legs_hit_matched'] = all(pair[name]['hit_matched_reco_indices'] for name in ('minus', 'plus'))
    for muon in reco_muons:
        for field in ('hitGenPartIdx', 'genPartIdx'):
            index = muon[field]
            if index < -1 or index >= len(particles):
                raise ValueError('Reco truth index outside GenPart')
            if index >= 0 and (abs(particles[index]['pdgId']) != 13 or particles[index]['status'] != 1):
                raise ValueError('Reco truth match does not address a stable muon')
    return {'event_id': event_id, 'native_weight': weight, 'coverage': coverage, 'direct_pairs': pairs,
            'reco_muons': len(reco_muons), 'reco_vertices': len(reco_pairs),
            'hit_angular_match_disagreements': sum(muon['hitGenPartIdx'] != muon['genPartIdx'] for muon in reco_muons),
            'reco_muons_without_hit_truth': sum(muon['hitGenPartIdx'] < 0 for muon in reco_muons)}


def summarize(events):
    pairs = [pair for event in events for pair in event['direct_pairs']]
    weighted_pairs = [(event['native_weight'], pair) for event in events for pair in event['direct_pairs']]
    return {'events': len(events), 'sum_native_weights': math.fsum(e['native_weight'] for e in events),
            'retained_parent_histories': sum(e['coverage']['retained_parent_histories'] for e in events),
            'ambiguous_histories': sum(len(e['coverage']['ambiguous_histories']) for e in events),
            'direct_pair_denominator': len(pairs),
            'direct_muon_leg_denominator': 2 * len(pairs),
            'weighted_direct_pair_denominator': math.fsum(weight for weight, _ in weighted_pairs),
            'weighted_direct_muon_leg_denominator': math.fsum(2 * weight for weight, _ in weighted_pairs),
            'weighted_hit_matched_direct_legs': math.fsum(weight * bool(pair[name]['hit_matched_reco_indices'])
                                                        for weight, pair in weighted_pairs for name in ('minus', 'plus')),
            'weighted_direct_pairs_with_reco_vertex': math.fsum(weight * bool(pair['hit_matched_vertices'])
                                                              for weight, pair in weighted_pairs),
            'hit_matched_direct_legs': sum(bool(p[n]['hit_matched_reco_indices']) for p in pairs for n in ('minus', 'plus')),
            'direct_pairs_with_both_reco_legs': sum(p['both_legs_hit_matched'] for p in pairs),
            'direct_pairs_with_reco_vertex': sum(bool(p['hit_matched_vertices']) for p in pairs),
            'direct_pairs_with_os_reco_vertex': sum(any(v['isOS'] == 1 for v in p['hit_matched_vertices']) for p in pairs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-events', type=int, default=1000)
    args = parser.parse_args()
    if args.max_events < 1 or args.output.exists():
        parser.error('Require positive max-events and a new output path')
    source = args.input.resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    adjacent_receipts = {}
    for name in ('signal_report.json', 'report.json'):
        path = source.parent / name
        if not path.exists():
            continue
        receipt = json.loads(path.read_text())
        if not receipt.get('complete'):
            raise ValueError('Adjacent detector receipt is incomplete: ' + name)
        if name == 'report.json' and receipt.get('nano_sha256') != digest:
            raise ValueError('Nano digest differs from its detector receipt')
        adjacent_receipts[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    import ROOT
    file = ROOT.TFile.Open(str(source))
    if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unreadable/recovered Nano ROOT file')
    tree = file.Get('Events')
    if not tree:
        raise ValueError('Missing Events tree')
    names = {branch.GetName() for branch in tree.GetListOfBranches()}
    required = ['run', 'luminosityBlock', 'event', 'genWeight']
    required += [f'{prefix}_{field}' for prefix, fields in
                 [('GenPart', GEN_FIELDS), ('ShiftMuon', MUON_FIELDS), ('ShiftDimuonVertex', PAIR_FIELDS)] for field in fields]
    if set(required) - names:
        raise ValueError('Missing required Nano branches: ' + ', '.join(sorted(set(required) - names)))
    events = []
    for row in tree:
        if len(events) >= args.max_events:
            break
        collections = []
        for prefix, fields in [('GenPart', GEN_FIELDS), ('ShiftMuon', MUON_FIELDS), ('ShiftDimuonVertex', PAIR_FIELDS)]:
            fields = fields + (('pz',) if prefix == 'GenPart' and 'GenPart_pz' in names else ())
            collections.append([{field: getattr(row, f'{prefix}_{field}')[i] for field in fields}
                                for i in range(getattr(row, f'n{prefix}'))])
        weight = float(row.genWeight)
        if not math.isfinite(weight):
            raise ValueError('Non-finite native event weight')
        events.append(diagnose_event(*collections, [int(row.run), int(row.luminosityBlock), int(row.event)], weight))
    result = {'schema': 'shift-dark-photon-mother-aware-nano-diagnostics-v1', 'complete': True,
              'physics_efficiency_validated': False, 'recording_acceptance_validated': False,
              'input': str(source), 'input_sha256': digest,
              'command': [sys.executable, *sys.argv], 'adjacent_receipt_sha256': adjacent_receipts,
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'root_version': ROOT.gROOT.GetVersion(), 'input_events': int(tree.GetEntries()),
              'requested_max_events': args.max_events,
              'denominator_scope': 'unique retained PDG32 histories with exactly one direct stable opposite-charge muon pair; incomplete/ambiguous Nano ancestry is excluded and reported',
              'matching_scope': 'hitGenPartIdx only; angular genPartIdx reported separately with no fallback or reco selection change',
              'lifetime_scope': 'Nano has no persisted proper lifetime or time; spatial proxy uses Float cm vertices and reduced-precision parent mass, requires collinear resolvable flight; timed GEN/HepMC is authoritative',
              'normalization_scope': 'native genWeight retained; proposal correction stays in the sample cross section',
              'summary': summarize(events), 'events': events}
    file.Close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    main()
