#!/usr/bin/env python3
"""Audit signal graph, spacetime, weights and native cross-section bookkeeping."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
from pythia_generation_ledger import read_pythia_generation_ledger


def objects(begin, end):
    while begin != end:
        yield begin.__deref__()
        begin.__preinc__()


def read_product(event, label, typename):
    from DataFormats.FWLite import Handle
    value = Handle(typename)
    event.getByLabel(label, value)
    if not value.isValid():
        raise ValueError('Missing product: ' + str(label))
    return value.product()


def graph_record(hep):
    """Hash ancestry/end vertices and generator metadata, not only momenta."""
    vertices, particles = [], []
    for vertex in objects(hep.vertices_begin(), hep.vertices_end()):
        pos = vertex.position()
        incoming = list(objects(vertex.particles_in_const_begin(), vertex.particles_in_const_end()))
        outgoing = list(objects(vertex.particles_out_const_begin(), vertex.particles_out_const_end()))
        vertices.append([int(vertex.barcode()), [float(pos.x()), float(pos.y()), float(pos.z()), float(pos.t())],
                         sorted(int(p.barcode()) for p in incoming), sorted(int(p.barcode()) for p in outgoing)])
    for particle in objects(hep.particles_begin(), hep.particles_end()):
        mom = particle.momentum()
        start, end = particle.production_vertex(), particle.end_vertex()
        polar = particle.polarization()
        particles.append([int(particle.barcode()), int(particle.pdg_id()), int(particle.status()),
            [float(mom.px()), float(mom.py()), float(mom.pz()), float(mom.e())],
            float(particle.generated_mass()), int(start.barcode()) if start else None,
            int(end.barcode()) if end else None, [float(polar.theta()), float(polar.phi())]])
    return dict(vertices=sorted(vertices), particles=sorted(particles),
                momentum_unit=int(hep.momentum_unit()), length_unit=int(hep.length_unit()),
                event_number=int(hep.event_number()), process_id=int(hep.signal_process_id()),
                weights=[float(w) for w in hep.weights()])


def record_hash(record):
    def canonical(value):
        # HepMC copies can flip the sign of an undefined polarization zero.
        # Preserve every nonzero value exactly; +/-0 encode the same graph.
        if isinstance(value,float) and value==0:
            return 0.0
        if isinstance(value,list):
            return [canonical(item) for item in value]
        if isinstance(value,dict):
            return {key:canonical(item) for key,item in value.items()}
        return value
    return hashlib.sha256(json.dumps(canonical(record), sort_keys=True, allow_nan=False).encode()).hexdigest()


def graph_hash(hep):
    return record_hash(graph_record(hep))


def genparticle_hash(event):
    particles=read_product(event,'genParticles','std::vector<reco::GenParticle>')
    rows=[]
    for p in particles:
        # HepMC/GenParticle rebuilding can permute the same parent list.
        # Compare graph adjacency multisets, retaining every duplicate edge.
        mothers=sorted(int(p.motherRef(i).key()) for i in range(p.numberOfMothers()))
        daughters=sorted(int(p.daughterRef(i).key()) for i in range(p.numberOfDaughters()))
        rows.append([int(p.pdgId()),int(p.status()),float(p.charge()),
                     [float(p.px()),float(p.py()),float(p.pz()),float(p.energy()),float(p.mass())],
                     [float(p.vx()),float(p.vy()),float(p.vz())],mothers,daughters])
    return record_hash(rows)


def validate_lifetime_sample(lengths, expected_mean_mm, rejection_probability_bound=1e-12):
    """Finite-N exponential mean gate, including the small-pilot lower tail.

    For N independent exponential draws and mean ratio r, the Chernoff bound
    on the corresponding tail is exp[-N*(r-1-log(r))]. This is a conservative
    finite-sample bound, not a Gaussian approximation or a physical acceptance.
    Exact zero lengths cannot represent a continuous exponential realization.
    """
    lengths = list(lengths)
    if not lengths or not math.isfinite(expected_mean_mm) or expected_mean_mm <= 0:
        raise ValueError('Lifetime sample requires draws and a positive physical mean')
    if not math.isfinite(rejection_probability_bound) or not 0 < rejection_probability_bound < 1:
        raise ValueError('Lifetime rejection bound must lie strictly between zero and one')
    if any(not math.isfinite(value) or value <= 0 for value in lengths):
        raise ValueError('Proper lengths must be finite and strictly positive')
    ratio = math.fsum(value/expected_mean_mm for value in lengths)/len(lengths)
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError('Invalid physical lifetime mean ratio')
    distance = ratio-1.
    rate = max(0., distance-math.log1p(distance)) if ratio > .5 else ratio-1.-math.log(ratio)
    log_bound = -len(lengths)*rate
    if log_bound < math.log(rejection_probability_bound):
        raise ValueError('Generated lifetime disagrees with the physical target width')
    return dict(passed=True, method='finite-N exponential Chernoff mean-tail bound',
                terminal_decays=len(lengths), mean_ratio=ratio,
                mean_lifetime_pull=distance*math.sqrt(len(lengths)),
                log_tail_probability_upper_bound=log_bound,
                rejection_probability_bound=rejection_probability_bound)


def audit(directory):
    import ROOT
    from DataFormats.FWLite import Events, Runs
    out = Path(directory)
    contract = json.loads((out / 'contract.json').read_text())
    if contract['schema'] != 'shift-dark-photon-gen-v1':
        raise ValueError('Wrong signal audit contract')
    proposal_ledger = read_pythia_generation_ledger(
        out / 'cmsRun.log', expected_accepted=contract['requested_events'])
    root = ROOT.TFile.Open(str(out / 'gen.root'))
    if not root or root.IsZombie() or root.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unreadable/recovered signal GEN')
    if int(root.Get('Events').GetEntries()) != contract['requested_events']:
        raise ValueError('Signal GEN count differs from frozen request')
    root.Close()
    identities, event_rows, lengths, counts = set(), [], [], Counter()
    max_flight_error, max_source_error, max_conservation = 0., 0., 0.
    beam_summary = None
    for index, event in enumerate(Events(str(out / 'gen.root'))):
        aux = event.eventAuxiliary()
        identity = (int(aux.run()), int(aux.luminosityBlock()), int(aux.event()))
        if identity in identities or identity != (contract['run_number'], 1, index + 1):
            raise ValueError('Missing/duplicated/out-of-order signal identity')
        identities.add(identity)
        info = read_product(event, 'generator', 'GenEventInfoProduct')
        weight = float(info.weight())
        if not math.isfinite(weight) or weight != 1.:
            raise ValueError('Native LO signal pilot requires unit GEN weights')
        original = read_product(event, ('generator', 'unsmeared'), 'edm::HepMCProduct').GetEvent()
        beams = original.beam_particles()
        target, projectile = beams.first.momentum(), beams.second.momentum()
        if abs(target.pz()) > 1e-6 or abs(target.e() - 0.938272) > 1e-4 or (
                abs(projectile.e() - 6800.) > 1e-4 or projectile.pz() >= 0):
            raise ValueError('Signal beams differ from the fixed-target Beam-B convention')
        beam_summary = dict(target_energy_gev=float(target.e()), beam_energy_gev=float(projectile.e()),
            beam_pz_gev=float(projectile.pz()),
            sqrt_s_gev=math.sqrt((target.e()+projectile.e())**2-(target.pz()+projectile.pz())**2))
        source_z = float(read_product(event, ('shiftEventTime', 'sourceZmm'), 'double')[0])
        shift = float(read_product(event, ('shiftEventTime', 'appliedShiftCtMm'), 'double')[0])
        max_source_error = max(max_source_error, abs(shift + source_z))
        if not math.isfinite(source_z) or source_z <= 0:
            raise ValueError('Invalid signal source vertex')
        hep = read_product(event, 'shiftEventTime', 'edm::HepMCProduct').GetEvent()
        mirrored = read_product(event, 'generatorSmeared', 'edm::HepMCProduct').GetEvent()
        signature = graph_hash(hep)
        if signature != graph_hash(mirrored):
            raise ValueError('generatorSmeared changed the completed physical graph')
        signal_decays = []
        for parent in objects(hep.particles_begin(), hep.particles_end()):
            if abs(parent.pdg_id()) != 32:
                if parent.status() == 1 and abs(parent.pdg_id()) == 13:
                    counts['stable_muons'] += 1
                continue
            start, end = parent.production_vertex(), parent.end_vertex()
            if parent.status() != 3 or not start or not end:
                raise ValueError('Signal parent is not a completed generator-only decay')
            children = list(objects(end.particles_out_const_begin(), end.particles_out_const_end()))
            if any(abs(p.pdg_id()) == 32 for p in children):
                continue  # A shower/history copy, not the physical terminal decay.
            if contract['decay_mode'] == 'mumu' and sorted(p.pdg_id() for p in children) != [-13, 13]:
                raise ValueError('Wrong terminal signal decay channel')
            mom, prod, decay = parent.momentum(), start.position(), end.position()
            mass = float(parent.generated_mass())
            low,high=contract['generated_mass_support_gev']
            if not math.isfinite(mass) or not low<=mass<=high:
                raise ValueError('Signal mass outside its frozen hard-process support')
            delta = [float(decay.x()-prod.x()), float(decay.y()-prod.y()),
                     float(decay.z()-prod.z()), float(decay.t()-prod.t())]
            components = [float(mom.px()), float(mom.py()), float(mom.pz()), float(mom.e())]
            length = delta[3] * mass / components[3]
            if not math.isfinite(length) or length < 0:
                raise ValueError('Invalid proper decay length')
            for displacement, momentum in zip(delta, components):
                error = abs(displacement - momentum / mass * length)
                max_flight_error = max(max_flight_error, error)
                if error > 1e-6 * max(1., abs(displacement)):
                    raise ValueError('Signal space/time flight differs from its proper lifetime')
            outgoing = [sum(float(getattr(child.momentum(), component)()) for child in children)
                        for component in ('px', 'py', 'pz', 'e')]
            residual = max(abs(a-b) for a,b in zip(components,outgoing))
            max_conservation = max(max_conservation, residual)
            if residual > 1e-6 * max(1., components[3]):
                raise ValueError('Signal decay violates four-momentum conservation')
            inside = math.hypot(decay.x(),decay.y()) <= 8000. and abs(decay.z()) <= 151000.
            counts['signal_decays'] += 1
            counts['inside_reference_cylinder' if inside else 'outside_reference_cylinder'] += 1
            lengths.append(length)
            signal_decays.append(dict(parent_barcode=int(parent.barcode()), mass_gev=mass,
                proper_length_mm=length, position_mm=[float(decay.x()),float(decay.y()),float(decay.z()),float(decay.t())],
                inside_reference_cylinder=inside))
        if not signal_decays:
            raise ValueError('No physical terminal signal decay')
        decayed = int(read_product(event, ('shiftMuonDecays', 'decayedParents'), 'unsigned int')[0])
        muons = int(read_product(event, ('shiftMuonDecays', 'addedMuons'), 'unsigned int')[0])
        counts['completed_hadron_parents'] += decayed
        counts['residual_added_muons'] += muons
        event_rows.append(dict(id=list(identity), weight=weight, full_graph_sha256=signature,
                               signal_decays=signal_decays))
    if max_source_error > 1e-7:
        raise ValueError('Common physical source timing changed')
    native_xsecs = []
    for run in Runs(str(out / 'gen.root')):
        run_info = read_product(run, 'generator', 'GenRunInfoProduct')
        cross = run_info.internalXSec()
        if not math.isfinite(cross.value()) or cross.value() <= 0:
            raise ValueError('No positive native signal cross section')
        native_xsecs.append(dict(value_pb=float(cross.value()), error_pb=float(cross.error())))
    if not native_xsecs:
        raise ValueError('Missing native signal run information')
    generated_mean = sum(lengths) / len(lengths)
    requested_mean = contract['width_authority']['ctau_mm']
    lifetime_audit=validate_lifetime_sample(lengths,requested_mean)
    lifetime_pull=lifetime_audit['mean_lifetime_pull']
    report = dict(schema='shift-dark-photon-gen-audit-v1', runtime_validated=True,
        physics_valid=False, normalization_ready=False, events=len(event_rows), counts=dict(counts),
        event_ids=[row['id'] for row in event_rows], event_rows=event_rows,
        sum_weights=sum(row['weight'] for row in event_rows),
        sum_weights_squared=sum(row['weight']**2 for row in event_rows),
        proposal_ledger=proposal_ledger,
        full_gen_denominator=dict(accepted_events=len(event_rows),
            selection='none; outside-envelope decays retained',
            interpretation='Technical Pythia trial counts are not proton collisions or luminosity'),
        beams=beam_summary, native_cross_sections=native_xsecs,
        target_cross_sections=[dict(value_pb=x['value_pb']*contract['production_rate_correction'],
                                    error_pb=x['error_pb']*contract['production_rate_correction'])
                               for x in native_xsecs],
        production_rate_correction=contract['production_rate_correction'],
        rate_scope='pure DY narrow-width pilot; physical closure and normalization not yet approved',
        cross_section_convention=contract['cross_section_convention'],
        maximum_source_timing_error_mm=max_source_error, maximum_flight_error_mm=max_flight_error,
        maximum_decay_momentum_residual_gev=max_conservation,
        mean_proper_length_mm=generated_mean, expected_proper_length_mm=requested_mean,
        mean_lifetime_pull=lifetime_pull,
        lifetime_audit=lifetime_audit)
    (out / 'validation.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='event_rows'},indent=2))
    return report


if __name__ == '__main__':
    audit(sys.argv[1])
