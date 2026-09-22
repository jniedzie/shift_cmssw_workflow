#!/usr/bin/env python3
"""Fail-closed GEN ownership and normalization audit before publication.

Only the explicitly defined 1--5 GeV LO samples are supported. This is MC
bookkeeping, never an analysis selection. No reconstructed content is read.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from pythia_pthat import from_hepmc

QCD_CODES = set(range(111, 117)) | set(range(121, 125))
JPSI_CODES = set(range(401, 411)) | {441}
QCD_PROCESS = 'QCD_FixedTarget_pThat_1to5GeV_13p6TeV'
QCD_MU_PROCESS = 'QCD_MuEnriched_FixedTarget_pThat_1to5GeV_13p6TeV'
QCD_UNFILTERED_PROCESS = 'QCD_UnfilteredDecays_FixedTarget_pThat_1to5GeV_13p6TeV'
JPSI_PROCESS = 'Charmonium_FixedTarget_pThat_1to5GeV_13p6TeV'
JPSI_UNFILTERED_PROCESS = 'Charmonium_Unfiltered_FixedTarget_pThat_1to5GeV_13p6TeV'


def main():
    from DataFormats.FWLite import Events, Handle, Runs, Lumis
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('--process', required=True)
    parser.add_argument('--events', type=int, required=True)
    parser.add_argument('--chunk', type=int, required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--fragment', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--lower', type=float, default=1.)
    parser.add_argument('--upper', type=float, default=5.)
    args = parser.parse_args()
    if not math.isfinite(args.lower) or args.lower < 1 or not math.isfinite(args.upper) or not (args.upper == -1 or args.upper > args.lower):
        raise ValueError('Invalid or unvalidated Born pThat range')
    expected = {QCD_PROCESS: QCD_CODES, QCD_MU_PROCESS: QCD_CODES, QCD_UNFILTERED_PROCESS: QCD_CODES,
                JPSI_PROCESS: JPSI_CODES, JPSI_UNFILTERED_PROCESS: JPSI_CODES}[args.process]
    filtered = args.process == QCD_MU_PROCESS

    def get(event, label, kind):
        handle = Handle(kind)
        event.getByLabel(label, handle)
        if not handle.isValid():
            raise RuntimeError(f'Missing {label} ({kind})')
        return handle.product()

    codes, particles = Counter(), Counter()
    identities, weights, pthats, born_pthats = set(), [], [], []
    selected_muons = []
    timing_residuals, source_shift_residuals = [], []
    for event in Events(args.input):
        aux = event.eventAuxiliary()
        identity = (int(aux.run()), int(aux.luminosityBlock()), int(aux.event()))
        if identity in identities:
            raise ValueError('Duplicate event identity within chunk')
        identities.add(identity)
        info = get(event, 'generator', 'GenEventInfoProduct')
        code, weight = int(info.signalProcessID()), float(info.weight())
        if code not in expected:
            raise ValueError(f'Hard-process ownership violation: {code}')
        if not math.isfinite(weight) or weight != 1.:
            raise ValueError('This unfiltered LO normalization contract requires unit weights')
        bins = list(info.binningValues())
        if not bins or not math.isfinite(bins[0]) or bins[0] < 0:
            raise ValueError(f'Invalid generated pThat: {bins}')
        hepmc = get(event, ('generator', 'unsmeared'), 'edm::HepMCProduct').GetEvent()
        born = from_hepmc(hepmc, code, bins[0])
        if born < args.lower-1.e-8 or (args.upper != -1 and born > args.upper+1.e-8):
            raise ValueError(f'Unexpected Born sampling pThat: {born}; stored={bins[0]}')
        born_pthats.append(born)
        codes[code] += 1
        weights.append(weight)
        pthats.append(bins[0])
        for p in get(event, 'genParticles', 'std::vector<reco::GenParticle>'):
            if abs(p.pdgId()) == 443:
                particles['jpsi_record_entries'] += 1
        if filtered:
            hepmc = get(event, ('generator', 'unsmeared'), 'edm::HepMCProduct').GetEvent()
            shifted_hepmc = get(event, 'shiftEventTime', 'edm::HepMCProduct').GetEvent()
            applied_shift = float(get(event, ('shiftEventTime', 'appliedShiftCtMm'), 'double')[0])
            source_x = float(get(event, ('shiftEventTime', 'sourceXmm'), 'double')[0])
            source_y = float(get(event, ('shiftEventTime', 'sourceYmm'), 'double')[0])
            source_z = float(get(event, ('shiftEventTime', 'sourceZmm'), 'double')[0])
            source_ct = float(get(event, ('shiftEventTime', 'sourceCtBeforeMm'), 'double')[0])
            raw_source_vertex = hepmc.signal_process_vertex()
            if not raw_source_vertex:
                raw_source_vertex = hepmc.vertices_begin().__deref__()
            raw_source = raw_source_vertex.position()
            vtx_smear = (source_x - raw_source.x(), source_y - raw_source.y(),
                         source_z - raw_source.z(), source_ct - raw_source.t())
            source_shift_residuals.append(applied_shift + source_z)
            event_muons = []
            particle = hepmc.particles_begin()
            particles_end = hepmc.particles_end()
            while particle != particles_end:
                p = particle.__deref__()
                particle.__preinc__()
                vertex = p.production_vertex()
                if abs(p.pdg_id()) != 13 or p.status() != 1 or vertex is None:
                    continue
                momentum, position = p.momentum(), vertex.position()
                rho = math.hypot(position.x(), position.y())
                if (momentum.eta() < 0 and momentum.eta() > -10 and
                        0 <= position.z() <= 151000 and rho <= 8000):
                    shifted_particle = shifted_hepmc.barcode_to_particle(p.barcode())
                    if shifted_particle is None or shifted_particle.production_vertex() is None:
                        raise ValueError('Selected muon is missing after the SHIFT time transformation')
                    shifted_position = shifted_particle.production_vertex().position()
                    spatial_residual = max(abs(shifted_position.x() - position.x() - vtx_smear[0]),
                                           abs(shifted_position.y() - position.y() - vtx_smear[1]),
                                           abs(shifted_position.z() - position.z() - vtx_smear[2]))
                    if spatial_residual > 1.e-8:
                        raise ValueError('SHIFT time transformation changed a selected muon vertex position')
                    timing_residuals.append(shifted_position.t() - position.t() - vtx_smear[3] - applied_shift)
                    event_muons.append(dict(pdg_id=p.pdg_id(), p_GeV=momentum.rho(),
                        pt_GeV=momentum.perp(), eta=momentum.eta(),
                        production_rho_mm=rho, production_z_mm=position.z(),
                        production_ct_mm=position.t()))
            if not event_muons:
                raise ValueError('Saved mu-enriched event does not satisfy the declared filter')
            selected_muons.extend(event_muons)
            particles['filter_eligible_muons'] += len(event_muons)
    if filtered and not weights:
        raise ValueError(f'No events passed the muon filter in {args.events} attempts')
    if not filtered and not 0 < len(weights) <= args.events:
        raise ValueError(f'Invalid generated count {len(weights)} for {args.events} requested slots')
    if any(run != args.chunk+1 or lumi != 1 or not 1 <= event <= args.events
           for run, lumi, event in identities):
        raise ValueError('Event identities outside the requested chunk/source slots')
    runs = []
    for run in Runs(args.input):
        info = get(run, 'generator', 'GenRunInfoProduct')
        xs = info.internalXSec()
        if not math.isfinite(xs.value()) or xs.value() <= 0:
            raise ValueError('Invalid internal cross section')
        runs.append({'internal_xsec_pb': xs.value(), 'error_pb': xs.error()})
    if len(runs) != 1:
        raise ValueError('Expected exactly one generated run per chunk')
    lumis = []
    for lumi in Lumis(args.input):
        info = get(lumi, 'generator', 'GenLumiInfoProduct')
        for p in info.getProcessInfos():
            record = {key: int(getattr(p, key)()) for key in
                      ('process', 'nPassPos', 'nPassNeg', 'nTotalPos', 'nTotalNeg')}
            for key in ('tried', 'selected', 'killed', 'accepted', 'acceptedBr'):
                stat = getattr(p, key)()
                record[key] = dict(n=int(stat.n()), sum=stat.sum(), sum2=stat.sum2())
            lumis.append(record)
    expected_generated = args.events if filtered else len(weights)
    if not lumis or sum(p['nPassPos'] for p in lumis) != expected_generated:
        raise ValueError('Lumi generator denominator does not match attempted count')
    if any(p['nTotalPos'] != p['nPassPos'] or p['nTotalNeg'] or p['nPassNeg'] for p in lumis):
        raise ValueError('Unexpected internal generator filtering or negative weights')
    filter_records = []
    for lumi in Lumis(args.input):
        info = get(lumi, 'genFilterEfficiencyProducer', 'GenFilterInfo')
        filter_records.append(dict(
            pass_positive=int(info.numPassPositiveEvents()),
            pass_negative=int(info.numPassNegativeEvents()),
            total_positive=int(info.numTotalPositiveEvents()),
            total_negative=int(info.numTotalNegativeEvents()),
            sum_pass_weights=info.sumPassWeights(), sum_pass_weights2=info.sumPassWeights2(),
            sum_weights=info.sumWeights(), sum_weights2=info.sumWeights2()))
    attempted = sum(x['total_positive'] + x['total_negative'] for x in filter_records)
    accepted = sum(x['pass_positive'] + x['pass_negative'] for x in filter_records)
    if attempted != expected_generated or accepted != len(weights):
        raise ValueError('External-filter bookkeeping does not match attempted/accepted counts')
    if any(x['pass_negative'] or x['total_negative'] for x in filter_records):
        raise ValueError('Unexpected negative weights in external-filter bookkeeping')
    efficiency = accepted / attempted
    efficiency_error = math.sqrt(efficiency * (1. - efficiency) / attempted)
    if filtered and (max(map(abs, timing_residuals)) > 1.e-8 or
                     max(map(abs, source_shift_residuals)) > 1.e-8):
        raise ValueError('SHIFT time transformation is inconsistent with nominal source timing')
    report = dict(schema='shift-production-gen-v1', process=args.process, chunk=args.chunk,
        framework_requested_events=args.events,
        generator_failed_framework_slots=args.events-expected_generated,
        input=args.input, events=len(weights), attempted_events=attempted,
        accepted_events=accepted, sum_weights=sum(weights),
        sum_weights_squared=sum(w*w for w in weights), hard_process_codes=dict(codes),
        pthat_min=min(pthats), pthat_max=max(pthats), particle_counts=dict(particles),
        born_pthat_min=min(born_pthats), born_pthat_max=max(born_pthats),
        pthat_bin_definition='Born phase-space pThat before final constituent-mass assignment',
        runs=runs, lumi_processes=lumis, external_filter_records=filter_records,
        generated_filter_efficiency=efficiency, filter_efficiency_error=efficiency_error,
        normalization_scope=('filtered LO primary-process definition; no luminosity assumed'
                             if filtered else 'unfiltered LO primary-process definition; no luminosity assumed'),
        forced_decay=('443 -> 13 -13; convention must be audited' if expected == JPSI_CODES else 'none'),
        decay_policy=('Pythia pi/K/KL decays inside rho<8000 mm, |z|<151000 mm'
                      if filtered or args.process == QCD_UNFILTERED_PROCESS else 'CMS lifetime cutoff'),
        generator_filter=('status-1 muon, -10<eta<0, '
                          '0<=production z<=151000 mm, rho<=8000 mm'
                          if filtered else 'none'),
        selected_muon_ranges=({key: [min(x[key] for x in selected_muons),
                                      max(x[key] for x in selected_muons)]
                               for key in selected_muons[0]} if selected_muons else {}),
        max_selected_muon_timing_residual_mm=(max(map(abs, timing_residuals))
                                                if timing_residuals else None),
        max_nominal_source_shift_residual_mm=(max(map(abs, source_shift_residuals))
                                               if source_shift_residuals else None),
        physics_valid=False, normalization_ready=False,
        configured_pthat_bounds=[args.lower, args.upper],
        event_ids=[list(identity) for identity in sorted(identities)],
        identity_min=list(min(identities)), identity_max=list(max(identities)),
        fragment_sha256=hashlib.sha256(Path(args.fragment).read_bytes()).hexdigest(),
        config_sha256=hashlib.sha256(Path(args.config).read_bytes()).hexdigest())
    Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    print(f'Validated {len(weights)} GEN events, ownership, pThat and normalization inputs')


if __name__ == '__main__':
    main()
