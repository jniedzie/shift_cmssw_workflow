#!/usr/bin/env python3
"""Fail-closed GEN ownership and unfiltered-weight audit before publication.

Only the two explicitly defined 1--5 GeV LO samples are supported. This is MC
bookkeeping, never an analysis selection. No reconstructed content is read.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

QCD_CODES = set(range(111, 117)) | set(range(121, 125))
JPSI_CODES = set(range(401, 411)) | {441}


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
    args = parser.parse_args()
    expected = {'QCD_FixedTarget_pThat_1to5GeV_13p6TeV': QCD_CODES,
                'Charmonium_FixedTarget_pThat_1to5GeV_13p6TeV': JPSI_CODES}[args.process]

    def get(event, label, kind):
        handle = Handle(kind)
        event.getByLabel(label, handle)
        if not handle.isValid():
            raise RuntimeError(f'Missing {label} ({kind})')
        return handle.product()

    codes, particles = Counter(), Counter()
    identities, weights, pthats = set(), [], []
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
        if not bins or not math.isfinite(bins[0]) or not 1.-1.e-8 <= bins[0] <= 5.+1.e-8:
            raise ValueError(f'Unexpected generated pThat: {bins}')
        codes[code] += 1
        weights.append(weight)
        pthats.append(bins[0])
        for p in get(event, 'genParticles', 'std::vector<reco::GenParticle>'):
            if abs(p.pdgId()) == 443:
                particles['jpsi_record_entries'] += 1
    if len(weights) != args.events:
        raise ValueError(f'Expected {args.events} generated events, got {len(weights)}')
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
    if not lumis or sum(p['nPassPos'] for p in lumis) != args.events:
        raise ValueError('Lumi generator denominator does not match output count')
    if any(p['nTotalPos'] != p['nPassPos'] or p['nTotalNeg'] or p['nPassNeg'] for p in lumis):
        raise ValueError('Unexpected generator filtering or negative weights')
    report = dict(schema='shift-production-gen-v1', process=args.process, chunk=args.chunk,
        input=args.input, events=len(weights), sum_weights=sum(weights),
        sum_weights_squared=sum(w*w for w in weights), hard_process_codes=dict(codes),
        pthat_min=min(pthats), pthat_max=max(pthats), particle_counts=dict(particles),
        runs=runs, lumi_processes=lumis, generated_filter_efficiency=1.,
        normalization_scope='unfiltered LO primary-process definition; no luminosity assumed',
        forced_decay=('none' if expected == QCD_CODES else '443 -> 13 -13; convention must be audited'),
        physics_valid=False,
        identity_min=list(min(identities)), identity_max=list(max(identities)),
        fragment_sha256=hashlib.sha256(Path(args.fragment).read_bytes()).hexdigest(),
        config_sha256=hashlib.sha256(Path(args.config).read_bytes()).hexdigest())
    Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    print(f'Validated {len(weights)} GEN events, ownership, pThat and normalization inputs')


if __name__ == '__main__':
    main()
