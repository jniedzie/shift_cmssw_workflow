#!/usr/bin/env python3
# Code style: AI-code. AI-generated read-only material-SIM acceptance audit.
"""Compare complete GEN graphs with actual material transport and CSC SimHits.

This is a diagnostic reader. It does not apply a GEN-muon veto, select physics
events, manufacture survival weights, or measure reconstruction acceptance.
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path


DECAY_SUBTYPES = {201, 202, 203, 210, 211, 221, 231}
HADRONIC_SUBTYPES = {111, 116, 121, 131, 141, 151, 161, 310}
QUARKONIA = {441, 443, 445, 551, 553, 555, 100443, 100553, 200553}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def material_outcome(vertices):
    """Describe measured vertices; saved birth state is not a stopping record."""
    types = {vertex['process_subtype'] for vertex in vertices}
    if types.intersection(DECAY_SUBTYPES):
        return 'decay_observed'
    if 121 in types:
        return 'inelastic_interaction_observed'
    if types.intersection(HADRONIC_SUBTYPES):
        return 'hadronic_interaction_observed'
    return 'terminal_state_not_recorded'


def muon_source(ancestor_pdgs, matched_gen_muon, immediate_parent_pdgs=None):
    """Keep material-created muons visible even with no vacuum GEN muon."""
    if not matched_gen_muon:
        return 'material_or_other_G4_secondary'
    if immediate_parent_pdgs and any(abs(value) in (130, 211, 321) for value in immediate_parent_pdgs):
        return 'pion_kaon_decay'
    heavy = lambda value: ((abs(value) // 100) % 10 in (4, 5)
                           or (abs(value) // 1000) % 10 in (4, 5))
    if any(heavy(value) and abs(value) not in QUARKONIA for value in ancestor_pdgs):
        return 'heavy_flavor'
    if any(abs(value) in QUARKONIA for value in ancestor_pdgs):
        return 'prompt_quarkonium'
    if any(abs(value) in (130, 211, 321) for value in ancestor_pdgs):
        return 'pion_kaon_decay'
    return 'other_GEN_muon'


def supported_pairs(muons, minimum_stations):
    """Retain both charge combinations; true charge can differ from reco charge."""
    if type(minimum_stations) is not int or not 1 <= minimum_stations <= 4:
        raise ValueError('CSC station requirement must be between one and four')
    selected = [muon for muon in muons
                if all(len(muon['CSC_station_hits'].get(side, {})) >= minimum_stations
                       for side in ('plus', 'minus'))]
    if len({muon['track_id'] for muon in selected}) != len(selected):
        raise ValueError('Muon tracks must be distinct')
    pairs = []
    for index, first in enumerate(selected):
        for second in selected[index + 1:]:
            momentum = [a + b for a, b in zip(first['momentum_GeV'], second['momentum_GeV'])]
            energy = first['energy_GeV'] + second['energy_GeV']
            pairs.append(dict(
                track_ids=[first['track_id'], second['track_id']],
                opposite_charge=first['pdg'] * second['pdg'] < 0,
                mass_at_birth_GeV=math.sqrt(max(0., energy * energy - sum(p * p for p in momentum))),
                pt_at_birth_GeV=math.hypot(momentum[0], momentum[1]),
                source_types=[first['source_type'], second['source_type']]))
    return selected, pairs


def saved_ancestry(track, tracks):
    result, visited, missing = [], set(), None
    parent = track['vertex']['parent_G4_track_id'] if track['vertex'] else -1
    while parent > 0:
        if parent in visited:
            raise ValueError('Cycle in saved G4 ancestry')
        visited.add(parent)
        if parent not in tracks:
            missing = parent
            break
        current = tracks[parent]
        result.append(dict(track_id=parent, pdg=current['pdg'], GEN_barcode=current['GEN_barcode']))
        parent = current['vertex']['parent_G4_track_id'] if current['vertex'] else -1
    return result, missing


def items(begin, end):
    result = []
    while begin != end:
        result.append(begin.__deref__())
        begin.__preinc__()
    return result


def product(event, label, kind, instance=''):
    from DataFormats.FWLite import Handle
    handle = Handle(kind)
    event.getByLabel(label, instance, handle)
    if not handle.isValid():
        raise ValueError('Missing required product ' + label + ':' + instance)
    return handle, handle.product()


def identity(event):
    value = event.eventAuxiliary()
    return tuple(int(x) for x in (value.run(), value.luminosityBlock(), value.event()))


def graph(event):
    handle, value = product(event, 'shiftEventTime', 'edm::HepMCProduct')
    hep = value.GetEvent()
    rows, muons, parents = [], {}, []
    position = lambda vertex: ([float(vertex.position().x()), float(vertex.position().y()),
                                float(vertex.position().z()), float(vertex.position().t())]
                               if vertex else None)
    for particle in items(hep.particles_begin(), hep.particles_end()):
        momentum = particle.momentum()
        start, end = particle.production_vertex(), particle.end_vertex()
        row = dict(barcode=int(particle.barcode()), pdg=int(particle.pdg_id()), status=int(particle.status()),
                   momentum_GeV=[float(momentum.px()), float(momentum.py()), float(momentum.pz())],
                   energy_GeV=float(momentum.e()), birth_mm=position(start), end_mm=position(end),
                   daughter_barcodes=[int(p.barcode()) for p in items(end.particles_out_const_begin(),
                                                                     end.particles_out_const_end())] if end else [])
        rows.append(row)
        if abs(row['pdg']) > 100 and start and end:
            # This is a saved graph-parent inventory; actual transport and PDG
            # species remain explicit. Do not infer that every row was injected.
            parents.append(row)
        if abs(row['pdg']) != 13 or particle.status() != 1:
            continue
        ancestors, visited = [], set()
        pending = items(start.particles_in_const_begin(), start.particles_in_const_end()) if start else []
        immediate, immediate_seen, immediate_pending = [], set(), list(pending)
        while immediate_pending:
            parent = immediate_pending.pop()
            if parent.barcode() in immediate_seen:
                continue
            immediate_seen.add(parent.barcode())
            if abs(parent.pdg_id()) == 13 and parent.production_vertex():
                birth = parent.production_vertex()
                immediate_pending.extend(items(birth.particles_in_const_begin(), birth.particles_in_const_end()))
            else:
                immediate.append(int(parent.pdg_id()))
        while pending:
            ancestor = pending.pop()
            if ancestor.barcode() in visited:
                continue
            visited.add(ancestor.barcode())
            ancestors.append(int(ancestor.pdg_id()))
            if ancestor.production_vertex():
                birth = ancestor.production_vertex()
                pending.extend(items(birth.particles_in_const_begin(), birth.particles_in_const_end()))
        muons[row['barcode']] = dict(row, ancestor_pdgs=ancestors, first_non_muon_parent_pdgs=immediate)
    vertices = []
    for vertex in items(hep.vertices_begin(), hep.vertices_end()):
        vertices.append(dict(barcode=int(vertex.barcode()), position=position(vertex),
                             incoming=sorted(int(p.barcode()) for p in items(
                                 vertex.particles_in_const_begin(), vertex.particles_in_const_end())),
                             outgoing=sorted(int(p.barcode()) for p in items(
                                 vertex.particles_out_const_begin(), vertex.particles_out_const_end()))))
    body = dict(particles=sorted(rows, key=lambda row: row['barcode']),
                vertices=sorted(vertices, key=lambda row: row['barcode']),
                weights=list(map(float, hep.weights())), event_number=int(hep.event_number()),
                signal_process_id=int(hep.signal_process_id()),
                momentum_unit=str(hep.momentum_unit()), length_unit=str(hep.length_unit()))
    signature = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return signature, muons, parents


def metadata(path):
    from DataFormats.FWLite import Runs, Lumis, Handle
    runs, lumis = [], []
    for run in Runs(str(path)):
        row = dict(run=int(run.runAuxiliary().run()), products={})
        for label in ('generator', 'shiftWeightedNormalization'):
            handle = Handle('GenRunInfoProduct')
            run.getByLabel(label, handle)
            if handle.isValid():
                value = handle.product()
                row['products'][label] = dict(
                    internal_xsec_pb=float(value.internalXSec().value()),
                    internal_error_pb=float(value.internalXSec().error()),
                    filter_efficiency=float(value.filterEfficiency()))
        runs.append(row)
    for lumi in Lumis(str(path)):
        aux = lumi.luminosityBlockAuxiliary()
        row = dict(run=int(aux.run()), lumi=int(aux.luminosityBlock()))
        handle = Handle('GenFilterInfo')
        lumi.getByLabel('genFilterEfficiencyProducer', handle)
        if handle.isValid():
            value = handle.product()
            row['GenFilterInfo'] = dict(
                tried=int(value.numEventsTried()), passed=int(value.numEventsPassed()),
                total_positive=int(value.numTotalPositiveEvents()), total_negative=int(value.numTotalNegativeEvents()),
                passed_positive=int(value.numPassPositiveEvents()), passed_negative=int(value.numPassNegativeEvents()),
                sumw=float(value.sumWeights()), sumw2=float(value.sumWeights2()),
                passed_sumw=float(value.sumPassWeights()), passed_sumw2=float(value.sumPassWeights2()))
        handle = Handle('GenLumiInfoProduct')
        lumi.getByLabel('generator', handle)
        row['original_GenLumiInfoProduct_present'] = bool(handle.isValid())
        if handle.isValid():
            value = handle.product()
            processes = []
            for process in value.getProcessInfos():
                statistics = {}
                for name in ('tried', 'selected', 'killed', 'accepted', 'acceptedBr'):
                    stat = getattr(process, name)()
                    statistics[name] = dict(count=int(stat.n()), sumw=float(stat.sum()), sumw2=float(stat.sum2()))
                processes.append(dict(process=int(process.process()),
                                      xsec_pb=float(process.lheXSec().value()),
                                      error_pb=float(process.lheXSec().error()),
                                      passed_positive=int(process.nPassPos()), passed_negative=int(process.nPassNeg()),
                                      total_positive=int(process.nTotalPos()), total_negative=int(process.nTotalNeg()),
                                      statistics=statistics))
            row['GenLumiInfoProduct'] = dict(HEPIDWTUP=int(value.getHEPIDWTUP()), processes=processes)
        lumis.append(row)
    return dict(runs=runs, lumis=lumis)


def represented_metadata(value, event_ids):
    """Keep full source exposure for represented runs/lumis; never scale it."""
    runs = {event[0] for event in event_ids}
    lumis = {(event[0], event[1]) for event in event_ids}
    return dict(runs=[row for row in value['runs'] if row['run'] in runs],
                lumis=[row for row in value['lumis'] if (row['run'], row['lumi']) in lumis])


def audit(gen_path, sim_path, minimum_stations=2, control_manifest=None, expected_event_ids=None):
    import ROOT
    from DataFormats.FWLite import Events
    ROOT.gSystem.Load('libDataFormatsMuonDetId')
    originals = {}
    for event in Events(str(gen_path)):
        event_id = identity(event)
        if expected_event_ids is not None and event_id not in expected_event_ids:
            continue
        if event_id in originals:
            raise ValueError('Repeated original GEN identity')
        signature, muons, parents = graph(event)
        handle, weight = product(event, 'generator', 'GenEventInfoProduct')
        native_weight = float(weight.weight())
        if not math.isfinite(native_weight):
            raise ValueError('Non-finite native weight')
        originals[event_id] = dict(graph_sha256=signature, GEN_muons=muons, GEN_parents=parents,
                                    native_weight=native_weight)
    if not originals:
        raise ValueError('Complete GEN input has no events')
    if expected_event_ids is not None and set(originals) != set(expected_event_ids):
        raise ValueError('Requested original identity is absent from complete GEN')
    controls, manifest = {}, None
    if control_manifest:
        manifest = json.loads(Path(control_manifest).read_text())
        controls = {tuple(row['event_id']): row for row in manifest['selected_events']}
        if len(controls) != len(manifest['selected_events']) or not set(originals).issubset(controls):
            raise ValueError('Control manifest and GEN identities differ')
        if expected_event_ids is None and set(controls) != set(originals):
            raise ValueError('Control manifest and GEN identities differ')
    events, checks, seen = [], {}, set()
    for event in Events(str(sim_path)):
        event_id = identity(event)
        if event_id in seen or event_id not in originals:
            raise ValueError('Repeated or unrecognized SIM identity')
        seen.add(event_id)
        source = originals[event_id]
        signature, _, _ = graph(event)
        handle, weight = product(event, 'generator', 'GenEventInfoProduct')
        checks[str(event_id) + '_native_weight_exact'] = float(weight.weight()) == source['native_weight']
        checks[str(event_id) + '_complete_GEN_graph_exact'] = signature == source['graph_sha256']
        track_handle, saved_tracks = product(event, 'g4SimHits', 'std::vector<SimTrack>')
        vertex_handle, saved_vertices = product(event, 'g4SimHits', 'std::vector<SimVertex>')
        hit_handle, hits = product(event, 'g4SimHits', 'std::vector<PSimHit>', 'MuonCSCHits')
        vertices = [dict(index=index, parent_G4_track_id=int(vertex.parentIndex()),
                         process_subtype=int(vertex.processType()),
                         position_cm=[float(vertex.position().x()), float(vertex.position().y()),
                                      float(vertex.position().z())],
                         time_ns=float(vertex.position().t()) * 1e9)
                    for index, vertex in enumerate(saved_vertices)]
        tracks = {}
        for track in saved_tracks:
            number = int(track.trackId())
            if number in tracks:
                raise ValueError('Repeated saved G4 track ID')
            vertex = int(track.vertIndex())
            momentum = track.momentum()
            tracks[number] = dict(track_id=number, pdg=int(track.type()), GEN_barcode=int(track.genpartIndex()),
                                   vertex=vertices[vertex] if 0 <= vertex < len(vertices) else None,
                                   momentum_GeV=[float(momentum.x()), float(momentum.y()), float(momentum.z())],
                                   energy_GeV=float(momentum.E()), is_primary_or_preassigned=bool(track.isPrimary()))
        stations = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
        for hit in hits:
            detector = ROOT.CSCDetId(int(hit.detUnitId()))
            side = 'plus' if int(detector.endcap()) == 1 else 'minus'
            stations[int(hit.trackId())][side][int(detector.station())] += 1
        activity = []
        station_union = {side: collections.Counter() for side in ('plus', 'minus')}
        for number, sides in stations.items():
            for side in ('plus', 'minus'):
                station_union[side].update(sides[side])
            activity.append(dict(track_id=number, pdg=tracks[number]['pdg'] if number in tracks else None,
                                 CSC_station_hits={side: dict(sides[side]) for side in ('plus', 'minus')}))
        muons = []
        for track in tracks.values():
            if abs(track['pdg']) != 13:
                continue
            match = source['GEN_muons'].get(track['GEN_barcode'])
            if match and match['pdg'] != track['pdg']:
                match = None
            ancestry, missing = saved_ancestry(track, tracks)
            ancestor_pdgs = [value['pdg'] for value in ancestry]
            if match:
                ancestor_pdgs += match['ancestor_pdgs']
            muons.append(dict(track, matched_GEN_muon=match, saved_parent_chain=ancestry,
                               missing_saved_parent_track_id=missing,
                               source_type=muon_source(ancestor_pdgs, match is not None,
                                                       match['first_non_muon_parent_pdgs'] if match else None),
                               CSC_station_hits={side: dict(stations[track['track_id']][side])
                                                 for side in ('plus', 'minus')}))
        supported, pairs = supported_pairs(muons, minimum_stations)
        parents = []
        for parent in source['GEN_parents']:
            matched = [track for track in tracks.values()
                       if track['GEN_barcode'] == parent['barcode'] and track['pdg'] == parent['pdg']]
            ids = {track['track_id'] for track in matched}
            children = [vertex for vertex in vertices if vertex['parent_G4_track_id'] in ids]
            parents.append(dict(parent, saved_G4_tracks=matched, child_vertices=children,
                                measured_outcome=material_outcome(children),
                                terminal_alive_or_absorbed_status_recorded=False))
        events.append(dict(event_id=list(event_id), native_weight=source['native_weight'],
                           control_group=controls.get(event_id, {}).get('control_group'),
                           GEN_muons=len(source['GEN_muons']), actual_saved_muons=len(muons),
                           muons_with_any_CSC_hits=sum(any(muon['CSC_station_hits'][side]
                                                           for side in ('plus', 'minus')) for muon in muons),
                           muons_with_required_CSC_stations_each_endcap=len(supported),
                           all_charge_supported_pairs=len(pairs),
                           opposite_charge_supported_pairs=sum(pair['opposite_charge'] for pair in pairs),
                           pairs=pairs, muons=muons, GEN_parents=parents,
                           all_particle_CSC_activity=activity,
                           all_particle_CSC_station_hits={side: dict(station_union[side])
                                                          for side in ('plus', 'minus')},
                           csc_hit_track_ids_missing_from_saved_tracks=sorted(set(stations) - set(tracks)),
                           saved_creator_process_counts=dict(collections.Counter(v['process_subtype'] for v in vertices))))
    checks['all_original_identities_present_once'] = seen == set(originals)
    gen_metadata, sim_metadata = metadata(gen_path), metadata(sim_path)
    if expected_event_ids is not None:
        gen_metadata = represented_metadata(gen_metadata, expected_event_ids)
    checks['full_original_run_lumi_metadata_preserved'] = gen_metadata == sim_metadata
    return dict(schema='shift-material-scout-SIM-audit-v1', passed=all(checks.values()), checks=checks,
                 complete_GEN=dict(path=str(gen_path), sha256=digest(gen_path)),
                 SIM=dict(path=str(sim_path), sha256=digest(sim_path)),
                 control_manifest=dict(path=str(control_manifest), sha256=digest(control_manifest)) if control_manifest else None,
                 full_original_run_lumi_metadata=sim_metadata, events=events,
                 minimum_CSC_stations_each_endcap=minimum_stations, no_GEN_muon_count_veto=True,
                 requested_event_ids=[list(event) for event in sorted(expected_event_ids)] if expected_event_ids else None,
                 physics_normalization_ready=False, reconstruction_acceptance_measured=False,
                 limits=['CSC SimHits precede digitization, BX/readout, trigger and reconstruction.',
                         'Saved birth state is not a terminal alive/absorbed state.',
                         'Missing saved ancestry is not proof that a particle was never tracked.',
                         'Intentional control selection does not establish population efficiency or shape closure.',
                         'No material-survival correction or event-selection weight is inferred here.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gen', type=Path, required=True)
    parser.add_argument('--sim', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--control-manifest', type=Path)
    parser.add_argument('--minimum-csc-stations', type=int, default=2)
    parser.add_argument('--event-id', action='append', metavar='RUN:LUMI:EVENT',
                        help='Audit an explicit GEN subset; retain full exposure of each represented source lumi')
    options = parser.parse_args()
    expected = None
    if options.event_id:
        try:
            values = [tuple(int(part) for part in value.split(':')) for value in options.event_id]
        except ValueError:
            parser.error('Event IDs must be positive integer RUN:LUMI:EVENT values')
        if any(len(value) != 3 or min(value) < 1 for value in values) or len(set(values)) != len(values):
            parser.error('Event IDs must be unique positive RUN:LUMI:EVENT values')
        expected = set(values)
    result = audit(options.gen, options.sim, options.minimum_csc_stations, options.control_manifest, expected)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(passed=result['passed'], checks=len(result['checks']), events=len(result['events']),
                          supported_OS_pairs=sum(event['opposite_charge_supported_pairs'] for event in result['events']),
                          supported_all_charge_pairs=sum(event['all_charge_supported_pairs'] for event in result['events']))))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
