#!/usr/bin/env python3
# Code style: AI-code. AI-generated full-HepMC material-ranking feature reader.
"""Read GEN ancestry and material features without selecting or reweighting events.

The geometry calculation is a straight-chord ranking proxy. It does not model
magnetic bending, transport losses, showers or detector acceptance. Unknown
coverage and events with no saved GEN muons are retained. Original source
identities and exposure must come from the production ledger: copied run/lumi
products in different decay variants are not additional generated exposure.
"""
import argparse
from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path
import time

from audit_shift_material_scout import digest, identity, items, metadata, product
from shift_material_path_score import score_paths


def hadron(pdg):
    value = abs(int(pdg))
    # Diquarks use q1 q2 0 spin codes; they are not transported hadrons.
    return (100 < value < 1000000000
            and not (1000 <= value < 10000 and (value//10) % 10 == 0))


def kinematics(momentum):
    if len(momentum) != 3 or any(not math.isfinite(float(x)) for x in momentum):
        raise ValueError('Momentum needs three finite GeV components')
    momentum = list(map(float, momentum))
    magnitude = math.sqrt(sum(x*x for x in momentum))
    return dict(momentum_GeV=momentum, p_GeV=magnitude,
                pt_GeV=math.hypot(*momentum[:2]),
                direction=[x/magnitude for x in momentum] if magnitude else None)


def ancestry(barcode, particles):
    """Follow every actual HepMC incoming edge, including muon-copy chains."""
    visited, visiting = set(), set()

    def visit(number):
        if number in visiting:
            raise ValueError('Cycle in HepMC ancestry')
        if number in visited:
            return
        if number not in particles:
            raise ValueError('Missing referenced HepMC parent barcode')
        visiting.add(number)
        for parent in particles[number]['parents']:
            visit(parent)
        visiting.remove(number)
        visited.add(number)

    for parent in particles[barcode]['parents']:
        visit(parent)
    if barcode in visited:
        raise ValueError('Particle is its own HepMC ancestor')
    return sorted(visited)


def first_non_muon_parents(barcode, particles):
    result, visited = set(), set()
    pending = list(particles[barcode]['parents'])
    while pending:
        number = pending.pop()
        if number in visited:
            continue
        visited.add(number)
        parent = particles[number]
        if abs(parent['pdg']) == 13:
            pending.extend(parent['parents'])
        else:
            result.add(number)
    return sorted(result)


def ray_to_cms_entry(start, momentum, minimum_abs_z_cm):
    """Extrapolate to the external/CMS boundary; this is not hit acceptance."""
    if start is None:
        return None, 'missing muon production vertex'
    if abs(start[2]) <= minimum_abs_z_cm:
        return None, 'muon born inside unmodelled central CMS region'
    pz = momentum[2]
    if pz == 0 or start[2]*pz >= 0:
        return None, 'straight direction does not approach CMS entry plane'
    target_z = math.copysign(minimum_abs_z_cm, start[2])
    scale = (target_z-start[2])/pz
    return [start[i]+scale*momentum[i] for i in range(3)], None


def path_feature(geometry, frame, event_id, number, kind, start, end):
    if start is None or end is None:
        return dict(complete=False, full_saved_chord_coverage=False,
                    interaction_depth_proxy=None, column_density_g_cm2=None,
                    unknown_reason='missing saved production or decay endpoint')
    if geometry is None:
        return dict(complete=False, full_saved_chord_coverage=False,
                    interaction_depth_proxy=None, column_density_g_cm2=None,
                    unknown_reason='geometry scoring not requested')
    path = dict(id=':'.join(map(str, event_id))+f':{kind}:{number}',
                event_id=list(event_id), start_cm=start, end_cm=end)
    result = score_paths(geometry, [path], frame)['paths'][0]
    # Even a dimensionless attenuation proxy would be easy to misuse downstream.
    # Export the raw material features and coverage, never a survival correction.
    result.pop('external_chord_attenuation_proxy', None)
    return result


def event_features(rows, event_id, native_weight, hepmc_weights, geometry=None, frame=None):
    """Pure graph features, with optional read-only ROOT geometry navigation."""
    if len(event_id) != 3 or any(type(x) is not int or x <= 0 for x in event_id):
        raise ValueError('GEN identity needs positive run, lumi and event')
    if not math.isfinite(native_weight) or any(not math.isfinite(x) for x in hepmc_weights):
        raise ValueError('GEN weights must be finite')
    particles = {row['barcode']: row for row in rows}
    if len(particles) != len(rows):
        raise ValueError('Duplicate HepMC particle barcode')
    for row in rows:
        kinematics(row['momentum_GeV'])
        if len(set(row['parents'])) != len(row['parents']):
            raise ValueError('Repeated immediate HepMC parent')
        if any(number not in particles for number in row['parents']):
            raise ValueError('Missing immediate HepMC parent')
    muons, parent_flights, needed_ancestors = [], {}, set()
    for row in rows:
        if abs(row['pdg']) != 13 or row['status'] != 1:
            continue
        parents = ancestry(row['barcode'], particles)
        needed_ancestors.update(parents)
        flight_numbers = [number for number in parents if hadron(particles[number]['pdg'])
                          and particles[number]['status'] != 4]
        for number in flight_numbers:
            if number in parent_flights:
                continue
            parent = particles[number]
            parent_flights[number] = dict(
                barcode=number, pdg=parent['pdg'], status=parent['status'],
                immediate_parent_barcodes=parent['parents'],
                birth_cm=parent['birth_cm'], end_cm=parent['end_cm'],
                **kinematics(parent['momentum_GeV']),
                incoming_towards_CMS_z0=(parent['birth_cm'] is not None
                                        and parent['birth_cm'][2]*parent['momentum_GeV'][2] < 0),
                material=path_feature(geometry, frame, event_id, number, 'parent',
                                      parent['birth_cm'], parent['end_cm']))
        motion = kinematics(row['momentum_GeV'])
        end, reason = ray_to_cms_entry(row['birth_cm'], row['momentum_GeV'],
                                       frame['minimum_abs_z_cm'] if frame else 0.)
        material = path_feature(geometry, frame, event_id, row['barcode'], 'muon',
                                row['birth_cm'], end)
        if reason:
            material['unknown_reason'] = reason
        # ROOT nuclear interaction lengths describe a hadron-ranking feature.
        # A muon's relevant geometric feature is column density, not that depth.
        material.pop('interaction_depth_proxy', None)
        flights = [parent_flights[number]['material'] for number in flight_numbers]
        all_known = all(item['full_saved_chord_coverage'] for item in flights)
        known_depth = sum(item['interaction_depth_proxy'] or 0. for item in flights)
        muons.append(dict(
            barcode=row['barcode'], pdg=row['pdg'], birth_cm=row['birth_cm'],
            **motion, immediate_parent_barcodes=row['parents'],
            incoming_towards_CMS_z0=(row['birth_cm'] is not None
                                    and row['birth_cm'][2]*row['momentum_GeV'][2] < 0),
            first_non_muon_parent_barcodes=first_non_muon_parents(row['barcode'], particles),
            ancestor_barcodes=parents, transport_parent_barcodes=flight_numbers,
            all_parent_flights_scored=all_known,
            parent_depth_proxy_sum=known_depth if all_known else None,
            known_parent_depth_proxy_sum=known_depth,
            straight_line_towards_CMS_entry=end is not None,
            projected_CMS_entry_cm=end, material_to_CMS_entry=material))
    stable_hadrons = [row for row in rows if row['status'] == 1 and hadron(row['pdg'])]
    stable_incoming = [row for row in stable_hadrons if row['birth_cm'] is not None
                       and row['birth_cm'][2]*row['momentum_GeV'][2] < 0]
    body = dict(particles=sorted(rows, key=lambda row: row['barcode']),
                hepmc_weights=hepmc_weights)
    return dict(
        event_id=list(map(int, event_id)), native_weight=float(native_weight),
        hepmc_weights=list(map(float, hepmc_weights)),
        graph_sha256=hashlib.sha256(json.dumps(body, sort_keys=True,
                                              separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
        GEN_muons=len(muons), zero_GEN_muon_control=not muons, muons=muons,
        stable_hadrons_without_saved_decay=len(stable_hadrons),
        incoming_stable_hadrons_without_saved_decay=len(stable_incoming),
        ancestor_nodes=[dict(barcode=number, pdg=particles[number]['pdg'],
                             status=particles[number]['status'],
                             immediate_parent_barcodes=particles[number]['parents'])
                        for number in sorted(needed_ancestors)],
        parent_flights=[parent_flights[number] for number in sorted(parent_flights)])


@lru_cache(maxsize=1)
def hepmc_unit_names():
    import ROOT
    # FWLite's product dictionary need not expose namespace enum constants.
    if not ROOT.gInterpreter.Declare('#include <HepMC/Units.h>'):
        raise ValueError('Cannot load authoritative HepMC unit declarations')
    # A previously loaded FWLite dictionary can cache Units as a PyROOT class.
    # Ask the C++ interpreter for the actual enum constants instead of relying
    # on that stale Python namespace binding.
    code = lambda name: int(ROOT.gInterpreter.ProcessLine(
        'static_cast<int>(HepMC::Units::'+name+')'))
    return ({code('GEV'): 'GEV', code('MEV'): 'MEV'},
            {code('MM'): 'MM', code('CM'): 'CM'})


def read_hepmc(event, label):
    handle, value = product(event, label, 'edm::HepMCProduct')
    hep = value.GetEvent()
    # Current PyROOT stringifies these enums as '1'/'0', not their C++ names.
    momenta, lengths = hepmc_unit_names()
    momentum_unit = momenta.get(int(hep.momentum_unit()))
    length_unit = lengths.get(int(hep.length_unit()))
    if momentum_unit is None or length_unit is None:
        raise ValueError('Unsupported HepMC momentum or length unit enum')
    pscale = 1. if momentum_unit == 'GEV' else .001
    xscale = .1 if length_unit == 'MM' else 1.
    position = lambda vertex: ([float(vertex.position().x())*xscale,
                                float(vertex.position().y())*xscale,
                                float(vertex.position().z())*xscale] if vertex else None)
    rows = []
    for particle in items(hep.particles_begin(), hep.particles_end()):
        start, end = particle.production_vertex(), particle.end_vertex()
        p = particle.momentum()
        rows.append(dict(barcode=int(particle.barcode()), pdg=int(particle.pdg_id()),
                         status=int(particle.status()),
                         momentum_GeV=[float(p.px())*pscale, float(p.py())*pscale, float(p.pz())*pscale],
                         energy_GeV=float(p.e())*pscale, birth_cm=position(start), end_cm=position(end),
                         birth_ct_cm=float(start.position().t())*xscale if start else None,
                         end_ct_cm=float(end.position().t())*xscale if end else None,
                         parents=sorted(int(parent.barcode()) for parent in items(
                             start.particles_in_const_begin(), start.particles_in_const_end())) if start else []))
    weight_handle, weight = product(event, 'generator', 'GenEventInfoProduct')
    return rows, float(weight.weight()), list(map(float, hep.weights())), (momentum_unit, length_unit)


def scan_source(path, hepmc_label, geometry=None, frame=None, maximum_events=None,
                original_sources=None):
    from DataFormats.FWLite import Events
    seen = set()
    for event in Events(str(path)):
        if maximum_events is not None and len(seen) >= maximum_events:
            break
        event_id = identity(event)
        if event_id in seen:
            raise ValueError('Repeated identity within one GEN source')
        seen.add(event_id)
        rows, weight, hepmc_weights, units = read_hepmc(event, hepmc_label)
        record = event_features(rows, event_id, weight, hepmc_weights, geometry, frame)
        record['original_hepmc_units'] = dict(momentum=units[0], length=units[1])
        original = (original_sources or {}).get(str(event_id[0]), {})
        record.update(source_id=original.get('source_id'),
                      original_source_sha256=original.get('original_source_sha256'),
                      original_source_identity_known=bool(original))
        yield record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gen', type=Path, action='append', required=True)
    parser.add_argument('--hepmc-label', default='shiftEventTime')
    parser.add_argument('--geometry', type=Path)
    parser.add_argument('--frame', type=Path, help='JSON object with the existing material scorer frame')
    parser.add_argument('--original-source-map', type=Path,
                        help='JSON object mapping original run number to source_id and original_source_sha256')
    parser.add_argument('--maximum-events-per-source', type=int)
    parser.add_argument('--output', type=Path, required=True, help='JSON or .json.gz; must not exist')
    args = parser.parse_args()
    if bool(args.geometry) != bool(args.frame):
        parser.error('--geometry and --frame must be supplied together')
    if args.maximum_events_per_source is not None and args.maximum_events_per_source < 1:
        parser.error('--maximum-events-per-source must be positive')
    started = time.monotonic()
    hepmc_unit_names()
    geometry, frame = None, None
    original_sources = json.loads(args.original_source_map.read_text()) if args.original_source_map else {}
    for run, value in original_sources.items():
        if not str(run).isdigit() or not value.get('source_id'):
            parser.error('Original source map needs run numbers and nonempty source_id values')
        checksum = value.get('original_source_sha256', '')
        if len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum):
            parser.error('Original source map needs lowercase SHA-256 values')
    if args.geometry:
        import ROOT
        frame = json.loads(args.frame.read_text())
        if 'minimum_abs_z_cm' not in frame or frame['minimum_abs_z_cm'] < 0:
            parser.error('Frame must explicitly specify minimum_abs_z_cm')
        ROOT.TGeoManager.LockDefaultUnits(False)
        ROOT.TGeoManager.SetDefaultUnits(ROOT.TGeoManager.kRootUnits)
        ROOT.TGeoManager.LockDefaultUnits(True)
        geometry = ROOT.TGeoManager.Import(str(args.geometry))
        if not geometry:
            raise ValueError('Cannot import supplied private geometry')
    from DataFormats.FWLite import Events
    sources = [dict(path=str(path.resolve()), sha256=digest(path),
                    source_file_event_count=int(Events(str(path)).size()),
                    full_original_run_lumi_metadata=metadata(path)) for path in args.gen]
    header = dict(schema='shift-gen-material-features-v1', hepmc_label=args.hepmc_label,
                  sources=sources, frame=frame, diagnostic_only=True,
                  original_source_map=original_sources,
                  maximum_events_per_source=args.maximum_events_per_source,
                  prefix_requested=args.maximum_events_per_source is not None,
                  original_population_census_proven=False,
                  usable_as_physics_weight=False, zero_GEN_muon_records_retained=True,
                  full_source_exposure_is_not_selected_event_count=True,
                  geometry_path=str(args.geometry.resolve()) if args.geometry else None,
                  geometry_sha256=digest(args.geometry) if args.geometry else None,
                  frame_definition_sha256=digest(args.frame) if args.frame else None,
                  material_score_schema='shift-gen-material-path-score-v1',
                  feature_reader_sha256=digest(Path(__file__)),
                  material_scorer_sha256=digest(Path(__file__).with_name('shift_material_path_score.py')),
                  limits=['Straight chords omit magnetic bending, scattering, losses and showers.',
                          'Material depth ranks only; no survival correction is exported.',
                          'Column ends at external/CMS entry; central CMS transport remains unmodelled.',
                          'Keep original ledger source identity when grouping decay variants.',
                          'Repeated original full-source Run/Lumi exposure must be counted once.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if args.output.suffix == '.gz' else open
    counts = dict(events=0, zero_GEN_muon_events=0, GEN_muons=0,
                  muons_with_complete_parent_flights=0, muons_with_known_external_column=0)
    with opener(args.output, 'xt') as target:
        body = json.dumps(header, allow_nan=False)
        target.write(body[:-1]+',"events":[')
        source_read_counts = []
        for path, source in zip(args.gen, sources):
            read_count = 0
            for row in scan_source(path, args.hepmc_label, geometry, frame,
                                   args.maximum_events_per_source, original_sources):
                row['source_file_sha256'] = source['sha256']
                if counts['events']:
                    target.write(',\n')
                json.dump(row, target, allow_nan=False, separators=(',', ':'))
                counts['events'] += 1
                read_count += 1
                counts['zero_GEN_muon_events'] += row['zero_GEN_muon_control']
                counts['GEN_muons'] += row['GEN_muons']
                counts['muons_with_complete_parent_flights'] += sum(
                    muon['all_parent_flights_scored'] for muon in row['muons'])
                counts['muons_with_known_external_column'] += sum(
                    muon['material_to_CMS_entry']['complete'] for muon in row['muons'])
            source_read_counts.append(dict(source_file_sha256=source['sha256'],
                                           source_file_event_count=source['source_file_event_count'],
                                           events_read=read_count,
                                           entire_input_file_read=read_count == source['source_file_event_count']))
        counts['source_read_counts'] = source_read_counts
        counts['seconds'] = time.monotonic()-started
        target.write('],"summary":'+json.dumps(counts, allow_nan=False)+'}\n')
    print(json.dumps(counts))


if __name__ == '__main__':
    main()
