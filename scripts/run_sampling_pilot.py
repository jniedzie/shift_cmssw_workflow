#!/usr/bin/env python3
"""Bounded pThat sampling study with the existing, frozen detector configuration.

No event rejection based on reconstruction or SimHits. Bin excursions are
diagnostics, not a selection; these exploratory outputs are not normalized
production. All reconstructed reads use an explicit mass-free allowlist.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
import re
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_config(args, work, workflow):
    import FWCore.ParameterSet.Config as cms
    from fixed_target_generation import process_settings
    process = runpy.run_path(str(args.template))['process']
    fragment = workflow / 'fragments' / (os.environ['PROCESS'] + '_pythia8_cff.py')
    generator = runpy.run_path(str(fragment))['generator']
    process.generator.PythiaParameters = generator.PythiaParameters.clone()
    params = list(process.generator.PythiaParameters.processParameters)
    if args.sample == 'jpsi':
        # Use the CMS common lifetime boundary, not the legacy J/psi overrides.
        source = [p for p in params if p.startswith('Beams:')]
        params = process_settings('jpsi', args.lower, args.upper if args.upper > 0 else 100.) + source
    params = [p for p in params if not p.startswith(('PhaseSpace:pTHatMin =', 'PhaseSpace:pTHatMax ='))]
    params += [f'PhaseSpace:pTHatMin = {args.lower}', f'PhaseSpace:pTHatMax = {args.upper}']
    process.generator.PythiaParameters.processParameters = cms.vstring(params)
    process.generator.maxEventsToPrint = 0
    if args.sample != 'qcdmu':
        for path in process.paths_().values():
            path.remove(process.mugenfilter)
    process.maxEvents.input = args.events
    process.source.firstRun = cms.untracked.uint32(args.seed)
    process.RandomNumberGeneratorService.generator.initialSeed = args.seed
    process.RandomNumberGeneratorService.g4SimHits.initialSeed = args.seed + 1000000
    process.RandomNumberGeneratorService.VtxSmeared.initialSeed = args.seed + 2000000
    process.options.wantSummary = cms.untracked.bool(True)
    process.MessageLogger.cerr.FwkReport.reportEvery = 100 if args.mode == 'gen' else 1
    process.FEVTDEBUGoutput.fileName = 'file:' + str(work / 'step1.root')
    if args.mode == 'gen':
        process.schedule.remove(process.simulation_step)
        process.FEVTDEBUGoutput.outputCommands = cms.untracked.vstring(
            'drop *', 'keep *_generator_*_*', 'keep *_genParticles_*_*',
            'keep *_shiftEventTime_*_*', 'keep *_genFilterEfficiencyProducer_*_*',
            'keep edmTriggerResults_*_*_*')
    cfg = work / 'step1_cfg.py'
    cfg.write_text(process.dumpPython())
    return cfg, dict(sample=args.sample, lower=args.lower, upper=args.upper,
        mode=args.mode, attempted_events=args.events, seed=args.seed,
        fragment_sha256=digest(fragment), template_sha256=digest(args.template),
        config_sha256=digest(cfg), process_settings=params,
        cms_common=list(process.generator.PythiaParameters.pythia8CommonSettings),
        cms_cp5=list(process.generator.PythiaParameters.pythia8CP5Settings),
        physics_valid=False, normalization_ready=False,
        reason='Exploratory bin/acceptance scan; pThat assignment and low-pT coverage under investigation')


def audit_gen(path, args):
    from DataFormats.FWLite import Events, Handle, Runs, Lumis
    from pythia_pthat import from_hepmc
    def get(e, label, kind):
        h = Handle(kind)
        e.getByLabel(label, h)
        if not h.isValid():
            raise ValueError(f'Missing product {label}')
        return h.product()
    counts, codes, excursions = Counter(), Counter(), Counter()
    rows, ids = [], set()
    for event in Events(str(path)):
        aux = event.eventAuxiliary()
        identity = (int(aux.run()), int(aux.luminosityBlock()), int(aux.event()))
        if identity in ids:
            raise ValueError('Duplicate identity')
        ids.add(identity)
        info = get(event, 'generator', 'GenEventInfoProduct')
        code, weight, pthat = int(info.signalProcessID()), float(info.weight()), float(info.binningValues()[0])
        expected = (set(range(401, 411)) | {441}) if args.sample == 'jpsi' else (set(range(111, 117)) | set(range(121, 125)))
        if code not in expected or weight != 1. or not math.isfinite(pthat) or pthat < 0:
            raise ValueError('Invalid process, weight or pThat')
        hep = get(event, ('generator', 'unsmeared'), 'edm::HepMCProduct')
        born_pthat = from_hepmc(hep.GetEvent(), code, pthat)
        if born_pthat < args.lower-1.e-8 or (args.upper > 0 and born_pthat > args.upper+1.e-8):
            raise ValueError('Born pThat outside the configured bin')
        codes[str(code)] += 1
        if pthat < args.lower or (args.upper > 0 and pthat > args.upper):
            excursions[str(code)] += 1
        particles = get(event, 'genParticles', 'std::vector<reco::GenParticle>')
        muons = [dict(pt=float(p.pt()), p=float(p.p()), eta=float(p.eta()),
                      phi=float(p.phi()), vx=float(p.vx()), vy=float(p.vy()),
                      vz=float(p.vz()), pdg=int(p.pdgId())) for p in particles
                 if abs(p.pdgId()) == 13 and p.status() == 1]
        jpsi_pt = [float(p.pt()) for p in particles if p.pdgId() == 443 and
                   not any(p.daughter(i).pdgId() == 443 for i in range(p.numberOfDaughters()))]
        counts['events'] += 1
        counts['muons'] += len(muons)
        forward = [m for m in muons if -10 < m['eta'] < 0]
        counts['at_least_one_forward_muon'] += len(forward) >= 1
        counts['at_least_two_forward_muons'] += len(forward) >= 2
        for threshold in (5, 10, 20, 40, 80):
            counts[f'two_forward_muons_p_gt_{threshold}'] += sum(m['p'] > threshold for m in forward) >= 2
        z = float(get(event, ('shiftEventTime', 'sourceZmm'), 'double')[0])
        shift = float(get(event, ('shiftEventTime', 'appliedShiftCtMm'), 'double')[0])
        if abs(z + shift) > 1.e-7:
            raise ValueError('Invalid physical timing')
        row = dict(id=identity, code=code, pthat=pthat, born_pthat=born_pthat,
                   muons=muons, jpsi_pt=jpsi_pt)
        if args.mode == 'full':
            hits = {}
            for name in ('MuonCSCHits', 'MuonDTHits', 'MuonRPCHits', 'MuonGEMHits'):
                hs = get(event, ('g4SimHits', name), 'std::vector<PSimHit>')
                hits[name] = len(hs)
            row['simhits'] = hits
            counts['events_any_muon_system_simhit'] += any(hits.values())
        rows.append(row)
    tries = passed = 0
    for lumi in Lumis(str(path)):
        f = get(lumi, 'genFilterEfficiencyProducer', 'GenFilterInfo')
        tries += int(f.numTotalPositiveEvents())
        passed += int(f.numPassPositiveEvents())
        if f.numTotalNegativeEvents():
            raise ValueError('Negative filter weights')
    text = path.with_name('framework.log').read_text() if path.with_name('framework.log').exists() else ''
    framework_counts = re.findall(r'TrigReport Events total =\s*(\d+) passed =\s*(\d+) failed =\s*(\d+)', text)
    if not framework_counts or int(framework_counts[-1][0]) != args.events or int(framework_counts[-1][1]) != passed:
        raise ValueError('Missing or inconsistent framework attempt/pass summary')
    if not 0 < tries <= args.events or passed != len(rows):
        raise ValueError(f'Filter bookkeeping mismatch: {tries}/{passed}/{len(rows)}')
    runs = []
    for run in Runs(str(path)):
        x = get(run, 'generator', 'GenRunInfoProduct').internalXSec()
        if not math.isfinite(x.value()) or x.value() <= 0:
            raise ValueError('Invalid cross section')
        runs.append(dict(internal_xsec_pb=x.value(), error_pb=x.error()))
    return dict(counts=dict(counts), framework_attempts=args.events,
        generator_failures=args.events-tries, attempted_events=tries, accepted_events=passed,
        runs=runs, process_codes=dict(codes), pthat_excursions_by_code=dict(excursions),
        pthat_range=[min((r['pthat'] for r in rows), default=None), max((r['pthat'] for r in rows), default=None)],
        rows=rows)


def audit_nano(path, rows):
    import ROOT
    f = ROOT.TFile.Open(str(path))
    if not f or f.IsZombie() or f.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Invalid NanoAOD')
    tree = f.Get('Events')
    tree.SetBranchStatus('*', 0)
    for name in ('run', 'luminosityBlock', 'event', 'nShiftMuon', 'nShiftDimuonVertex',
                 'ShiftDimuonVertex_topologyMin', 'ShiftDimuonVertex_topologyMax'):
        if not tree.GetBranch(name):
            raise ValueError(f'Missing {name}')
        tree.SetBranchStatus(name, 1)
    result, ids = Counter(), []
    row_by_id = {tuple(row['id']): row for row in rows}
    for e in tree:
        identity = (int(e.run), int(e.luminosityBlock), int(e.event))
        ids.append(identity)
        n = int(e.nShiftDimuonVertex)
        nb = sum(int(e.ShiftDimuonVertex_topologyMin[i]) == 2 and int(e.ShiftDimuonVertex_topologyMax[i]) == 2 for i in range(n))
        result['events'] += 1
        result['events_muon'] += int(e.nShiftMuon) > 0
        result['events_vertex'] += n > 0
        result['events_both_both'] += nb > 0
        result['vertices'] += n
        result['both_both_vertices'] += nb
        if identity not in row_by_id:
            raise ValueError('Unexpected NanoAOD identity')
        row_by_id[identity]['reco'] = dict(muons=int(e.nShiftMuon), vertices=n, both_both=nb)
    if ids != [tuple(r['id']) for r in rows]:
        raise ValueError('GEN/NanoAOD identity mismatch')
    f.Close()
    return dict(result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sample', choices=('qcdmu', 'qcd', 'jpsi'), required=True)
    p.add_argument('--mode', choices=('gen', 'full'), required=True)
    p.add_argument('--lower', type=float, required=True)
    p.add_argument('--upper', type=float, required=True)
    p.add_argument('--events', type=int, required=True)
    p.add_argument('--seed', type=int, required=True)
    p.add_argument('--chunk', type=int, required=True)
    p.add_argument('--template', type=Path, required=True)
    p.add_argument('--stage-timeout', type=int,
                   default=int(os.environ.get('SAMPLING_STAGE_TIMEOUT_SECONDS', '2700')),
                   help='External wall-time limit only; does not change transport settings')
    args = p.parse_args()
    if not (0 <= args.lower and (args.upper == -1 or args.upper > args.lower) and 0 < args.events <= 10000):
        p.error('Invalid pilot bounds or count')
    if args.sample != 'jpsi' and args.lower <= 0:
        p.error('HardQCD requires a positive lower bound')
    if not 60 <= args.stage_timeout <= 21600:
        p.error('Stage wall-time allowance must be between 60 and 21600 seconds')
    workflow = Path(__file__).resolve().parents[1]
    campaign = Path(os.environ['SAMPLE_DIR'])
    out = campaign / 'sampling_pilot' / args.mode / f'part{args.chunk:04d}'
    out.mkdir(parents=True, exist_ok=False)
    work = Path(tempfile.mkdtemp(prefix='shift_sampling_'))
    os.environ['LD_LIBRARY_PATH'] = ':'.join(x for x in os.environ.get('LD_LIBRARY_PATH', '').split(':') if '/biglib/' not in x)
    report = dict(status='running', physics_valid=False, normalization_ready=False,
                  reconstructed_mass_read=False, stage_timeout_seconds=args.stage_timeout)
    def run(command, name, timeout=None):
        start = time.monotonic()
        with (out / (name + '.log')).open('w') as log:
            subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=timeout or args.stage_timeout)
        report[name + '_wall_seconds'] = time.monotonic() - start
    try:
        cfg, contract = make_config(args, work, workflow)
        report['contract'] = contract
        shutil.copy2(cfg, out / 'step1_cfg.py')
        run(['cmsRun', str(cfg)], 'step1', 1800 if args.mode == 'gen' else args.stage_timeout)
        shutil.copy2(out / 'step1.log', work / 'framework.log')
        if args.mode == 'gen':
            # Retain a successful cmsRun payload even if an independent audit fails.
            shutil.copy2(work / 'step1.root', out / 'gen.root')
        report['generation'] = audit_gen(work / 'step1.root', args)
        # This runner bypasses run_step1_generation.sh, including its export.
        # Keep the usual discoverable file as well as the full per-run JSON.
        result = subprocess.run(['bash', str(workflow / 'scripts/update_cross_section.sh'),
                                 str(out / 'step1.log'), str(campaign / 'cross_sections.txt'),
                                 campaign.name], check=False)
        if result.returncode:
            print('WARNING: cross_sections.txt export failed; cross sections remain in report.json',
                  file=sys.stderr)
        if args.mode == 'full':
            part = f'{args.chunk:04d}'
            dest = campaign / 'samples/step1' / f'events_step1_part{part}.root'
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work / 'step1.root', dest)
            configdir = campaign / 'configs/step1'
            configdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cfg, configdir / f'events_step1_part{part}_seed{os.environ["GENERATOR_SEED"]}_cfg.py')
            for stage, script in ((2, 'run_step2_digi_raw.sh'), (3, 'run_step3_aod.sh'), (4, 'run_step4_exonanoAOD.sh')):
                run(['bash', str(workflow / script), str(args.chunk), str(args.events)], f'step{stage}')
            nano = campaign / 'samples/step4' / f'events_NanoAOD_part_{part}.root'
            shutil.copy2(nano, work / 'nano.root')
            report['reconstruction'] = audit_nano(work / 'nano.root', report['generation']['rows'])
        report['status'] = 'validated'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = repr(error)
        raise
    finally:
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({k: v for k, v in report.items() if k not in ('generation', 'contract')}))


if __name__ == '__main__':
    main()
