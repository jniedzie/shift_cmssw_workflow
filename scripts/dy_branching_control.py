#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded DY branching-convention diagnostic.
"""Native DY forced/natural channel controls, preserving complete GEN exposure.

The classifier and rate estimator originate in the qualified DY20+ diagnostic.
Mass bounds come from a pinned native source config; this module changes no
shared runtime and does not admit a production or apply a branching factor.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import runpy


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def require(value, message):
    if not value:
        raise ValueError(message)


def native_bounds(settings, expected):
    """Check the actual hard-source limits, including the low-mass23 floor."""
    require(len(expected)==2 and 0.<expected[0]<expected[1], 'Invalid finite source bounds')
    parsed={}
    for item in settings:
        if '=' in item:
            key,value=item.split('=',1);parsed[key.strip()]=value.strip()
    for key,value in [('23:mMin',expected[0]),('PhaseSpace:mHatMin',expected[0]),
                      ('PhaseSpace:mHatMax',expected[1])]:
        require(key in parsed and float(parsed[key])==value, 'Native mass boundary differs: '+key)
    return expected


def make_config(plan_path, arm):
    import FWCore.ParameterSet.Config as cms
    plan = json.loads(plan_path.read_text())
    spec = next(row for row in plan['arms'] if row['name'] == arm)
    original = plan_path.parent/'reference/native_cfg.py'
    require(sha(original) == plan['native_source_reference']['native_cfg.py']['sha256'], 'Native config changed')
    p = runpy.run_path(str(original))['process']
    require(p.generator.type_() == 'Pythia8GeneratorFilter', 'Native generator changed')
    before = list(p.generator.PythiaParameters.processParameters)
    native_bounds(before,plan['bounds_GeV'])
    require(before[-2:] == ['23:onMode = off', '23:onIfMatch = 13 -13'], 'Native23 forcing changed')
    require(not any(x.startswith('23:') for x in before[:-2] if not x.startswith('23:mMin')), 'Unexpected23 decay setting')
    other = p.generator.clone()
    other.PythiaParameters.processParameters = cms.vstring()
    generator_other = other.dumpPython()
    path_before = p.gen.dumpPython()
    if arm == 'natural':
        p.generator.PythiaParameters.processParameters = cms.vstring(*before[:-2])
    p.maxEvents.input = cms.untracked.int32(plan['accepted_events_per_arm'])
    p.source.firstRun = cms.untracked.uint32(spec['run'])
    p.RandomNumberGeneratorService.generator.initialSeed = cms.untracked.uint32(spec['seed'])
    p.RandomNumberGeneratorService.VtxSmeared.initialSeed = cms.untracked.uint32(spec['vertex_seed'])
    p.output.fileName = cms.untracked.string('file:gen.root')
    check = p.generator.clone()
    check.PythiaParameters.processParameters = cms.vstring()
    require(check.dumpPython() == generator_other and p.gen.dumpPython() == path_before,
            'Nonchannel generator settings or native GEN path changed')
    Path('gen_cfg.py').write_text(p.dumpPython())
    dump('config_change_receipt.json', dict(arm=arm, plan_sha256=sha(plan_path),
        original_cfg_sha256=sha(original), resolved_cfg_sha256=sha('gen_cfg.py'),
        process_settings_before=before, process_settings_after=list(p.generator.PythiaParameters.processParameters),
        nonchannel_generator_parameters_exact=True, native_GEN_path_exact=True,
        accepted_events=plan['accepted_events_per_arm'], run=spec['run'], seed=spec['seed'],
        vertex_seed=spec['vertex_seed'], SIM_or_detector_run=False, normalization_ready=False))


def primary_decay(rows):
    """Count one hard23 copy chain; daughter muons from taus/hadrons do not count."""
    particles = {row['barcode']: row for row in rows}
    bosons = {key for key, row in particles.items() if row['pdg'] == 23}
    pending, components = set(bosons), []
    while pending:
        component, todo = set(), [pending.pop()]
        while todo:
            key = todo.pop()
            if key in component:
                continue
            component.add(key);pending.discard(key)
            row = particles[key]
            todo.extend(x for x in row['mothers']+row['daughters'] if x in bosons and x not in component)
        if any(particles[key]['hard_process'] for key in component):
            components.append(component)
    require(len(components) == 1, 'Expected exactly one primary hard23 copy component')
    chain = components[0]
    roots = [key for key in chain if not any(x in chain for x in particles[key]['mothers'])]
    ends = [key for key in chain if not any(x in chain for x in particles[key]['daughters'])]
    require(len(roots) == len(ends) == 1 and all(
        sum(x in chain for x in particles[key]['mothers']) <= 1
        and sum(x in chain for x in particles[key]['daughters']) <= 1 for key in chain),
        'Ambiguous/forked hard23 copy chain')
    root, terminal = particles[roots[0]], particles[ends[0]]
    require(terminal['daughters'], 'Primary23 has no recorded decay')
    daughters = sorted(particles[key]['pdg'] for key in terminal['daughters'])
    nonphotons = [pdg for pdg in daughters if pdg != 22]
    px, py, pz, energy = root['p4_GeV']
    mass2 = energy*energy-px*px-py*py-pz*pz
    require(math.isfinite(mass2) and mass2 >= 0., 'Invalid primary23 mass')
    muon_copies = []
    if nonphotons == [-13, 13]:
        for key in terminal['daughters']:
            if abs(particles[key]['pdg']) != 13:
                continue
            copies = []
            while True:
                require(key not in copies, 'Cycle in direct primary muon copies')
                copies.append(key)
                nexts = [x for x in particles[key]['daughters'] if particles[x]['pdg'] == particles[key]['pdg']]
                require(len(nexts) <= 1, 'Ambiguous direct primary muon copy fork')
                if not nexts:
                    break
                key = nexts[0]
            muon_copies.append(copies)
    return dict(primary23_root_barcode=roots[0], primary23_terminal_barcode=ends[0],
        primary23_copy_barcodes=sorted(chain), mass_GeV=math.sqrt(mass2),
        direct_daughter_PDG_ids=daughters, direct_muon_copy_barcodes=muon_copies,
        primary23_mumu=nonphotons == [-13, 13])


def mass_counts(rows, slices):
    bins = [dict(bounds_GeV=bounds, parents=0, muonic=0) for bounds in slices]
    for row in rows:
        matches = [b for b in bins if row['mass_GeV'] >= b['bounds_GeV'][0]
                   and (b['bounds_GeV'][1] is None or row['mass_GeV'] < b['bounds_GeV'][1])]
        require(len(matches) == 1, 'Primary mass outside or ambiguously inside diagnostic slices')
        matches[0]['parents'] += 1;matches[0]['muonic'] += int(row['primary23_mumu'])
    return bins


def audit_arm(plan_path, arm, directory):
    from DataFormats.FWLite import Events, Handle
    from audit_shift_material_scout import metadata, items
    from shift_gen_material_features import hepmc_unit_names
    momentum_units, _ = hepmc_unit_names()
    plan = json.loads(plan_path.read_text());spec = next(x for x in plan['arms'] if x['name'] == arm)
    changed = json.loads((directory/'config_change_receipt.json').read_text())
    require(changed['plan_sha256'] == sha(plan_path) and changed['arm'] == arm
            and changed['resolved_cfg_sha256'] == sha(directory/'gen_cfg.py'), 'Resolved arm config changed')
    import ROOT
    f=ROOT.TFile.Open(str(directory/'gen.root'))
    require(bool(f) and not f.IsZombie() and not f.TestBit(ROOT.TFile.kRecovered), 'Invalid/recovered native GEN file')
    require(int(f.Get('Events').GetEntries()) == plan['accepted_events_per_arm'], 'Incomplete native event tree')
    f.Close()
    def product(event, label, typename):
        h = Handle(typename);event.getByLabel(label, h)
        require(h.isValid(), 'Missing native product '+str(label));return h.product()
    rows = [];identities = set();channels = Counter()
    for event in Events(str(directory/'gen.root')):
        aux = event.eventAuxiliary();eid = [int(aux.run()), int(aux.luminosityBlock()), int(aux.event())]
        require(eid[0] == spec['run'] and tuple(eid) not in identities, 'Wrong/duplicate native event identity')
        identities.add(tuple(eid))
        info = product(event, 'generator', 'GenEventInfoProduct')
        require(int(info.signalProcessID()) == 221 and float(info.weight()) == 1., 'Changed LO DY process or weight')
        hep = product(event, ('generator', 'unsmeared'), 'edm::HepMCProduct').GetEvent()
        require(momentum_units[int(hep.momentum_unit())] == 'GEV', 'Native generator momentum unit changed')
        truth = product(event, 'genParticles', 'std::vector<reco::GenParticle>')
        codes = list(map(int, product(event, 'genParticles', 'std::vector<int>')))
        require(len(codes) == len(set(codes)) == len(truth), 'Truth barcode inventory differs')
        hard = {code: bool(p.statusFlags().isHardProcess()) for code, p in zip(codes, truth)}
        graph = []
        for p in items(hep.particles_begin(), hep.particles_end()):
            v, end, momentum = p.production_vertex(), p.end_vertex(), p.momentum()
            graph.append(dict(barcode=int(p.barcode()), pdg=int(p.pdg_id()), hard_process=hard[int(p.barcode())],
                p4_GeV=[float(momentum.px()),float(momentum.py()),float(momentum.pz()),float(momentum.e())],
                mothers=[int(x.barcode()) for x in items(v.particles_in_const_begin(),v.particles_in_const_end())] if v else [],
                daughters=[int(x.barcode()) for x in items(end.particles_out_const_begin(),end.particles_out_const_end())] if end else []))
        row = dict(event_id=eid, native_weight=1., **primary_decay(graph))
        lower,upper=plan['bounds_GeV']
        require(lower-1.e-7<=row['mass_GeV']<upper+1.e-7, 'Primary mass outside native source bounds')
        rows.append(row);channels[str(row['direct_daughter_PDG_ids'])] += 1
    count = len(rows)
    require(count == plan['accepted_events_per_arm'] and {x['event_id'][2] for x in rows} == set(range(1,count+1)), 'Incomplete accepted GEN exposure')
    full = metadata(directory/'gen.root')
    require(len(full['runs']) == 1 and full['lumis'] and all(x['original_GenLumiInfoProduct_present'] for x in full['lumis']), 'Missing native Run/Lumi accounting')
    require(sum(p['passed_positive']+p['passed_negative'] for lumi in full['lumis']
                for p in lumi['GenLumiInfoProduct']['processes']) == count,
            'Native luminosity accepted count differs from complete saved exposure')
    run = full['runs'][0]['products']['generator'];sigma, error = run['internal_xsec_pb'], run['internal_error_pb']
    require(math.isfinite(sigma) and sigma > 0. and math.isfinite(error) and error >= 0., 'Invalid native cross section')
    log = (directory/'cmsRun.log').read_text()
    values = re.findall(r'(?:Before Filter: total|After filter: final) cross section =\s*(\S+)\s*\+-\s*(\S+) pb',log)
    require(len(values) == 2 and all(math.isclose(float(x[0]),sigma,rel_tol=.001) for x in values), 'Native analyzer cross section mismatch/filter loss')
    if arm == 'forced':
        require(all(x['primary23_mumu'] for x in rows), 'Forced23 has a nonmuonic primary decay')
    raw=''.join(json.dumps(row,allow_nan=False)+'\n' for row in rows).encode()
    (directory/'primary23_events.jsonl.gz').write_bytes(gzip.compress(raw,mtime=0))
    report = dict(complete=True, diagnostic_only=True, arm=arm, accepted_gen_events=count,
        denominator_kind='accepted_gen_events', sumW=float(count), sumW2=float(count),
        source_xsec_pb=sigma, source_xsec_error_pb=error, muonic_events=sum(x['primary23_mumu'] for x in rows),
        mass_slices=mass_counts(rows,plan['mass_slices_GeV']), channels=channels, full_Run_Lumi=full,
        plan_sha256=sha(plan_path), config_sha256=sha(directory/'gen_cfg.py'), GEN_sha256=sha(directory/'gen.root'),
        event_rows_sha256=sha(directory/'primary23_events.jsonl.gz'), cmsRun_log_sha256=sha(directory/'cmsRun.log'),
        source_bounds_GeV=plan['bounds_GeV'], normalization_ready=False,
        no_event_selection=True, detector_or_material_run=False)
    dump(directory/'audit.json',report)


def compare(forced_path, natural_path, output):
    forced, natural = [json.loads(p.read_text()) for p in (forced_path,natural_path)]
    require(forced['complete'] and natural['complete'] and forced['arm']=='forced' and natural['arm']=='natural'
            and forced['plan_sha256']==natural['plan_sha256'], 'Controls do not share one complete plan')
    require(len(forced['mass_slices'])==len(natural['mass_slices']) and all(
        a['bounds_GeV']==b['bounds_GeV'] for a,b in zip(forced['mass_slices'],natural['mass_slices'])),
        'Forced/natural mass slices differ')
    comparisons=[]
    for label, left, right in [('all',forced['muonic_events'],natural['muonic_events'])]+[
            (str(a['bounds_GeV']),a['muonic'],b['muonic']) for a,b in zip(forced['mass_slices'],natural['mass_slices'])]:
        rates=[]
        for report,count in ((forced,left),(natural,right)):
            n=report['accepted_gen_events'];f=count/n;sigma=report['source_xsec_pb']
            channel_error=sigma*math.sqrt(f*(1.-f)/n)
            sigma_error=report['source_xsec_error_pb']*f
            rates.append(dict(count=count,accepted_exposure=n,rate_pb=sigma*f,
                conditional_binomial_error_pb=channel_error,
                native_xsec_error_component_pb=sigma_error,
                approximate_independent_standard_error_pb=math.hypot(channel_error,sigma_error),
                conservative_error_component_sum_pb=channel_error+sigma_error,
                zero_count_95pct_upper_rate_pb=sigma*(1.-.05**(1./n)) if count==0 else None))
        comparisons.append(dict(mass_slice=label,forced=rates[0],natural=rates[1],
            sparse_muonic_count=(left < 25 or right < 25),
            natural_over_forced=(rates[1]['rate_pb']/rates[0]['rate_pb'] if rates[0]['rate_pb'] else None)))
    dump(output,dict(complete=True, diagnostic_only=True, comparisons=comparisons,
        forced_audit_sha256=sha(forced_path), natural_audit_sha256=sha(natural_path),
        caveat='Inspect native sigma and measured primary muonic fractions; error components are separate and no within-arm covariance is assumed. Sparse slices remain inconclusive.',
        normalization_ready=False, full_production_ready=False, correction_applied=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['make-config','audit','compare']);parser.add_argument('args',nargs='+');args=parser.parse_args()
    if args.mode=='make-config':make_config(Path(args.args[0]),args.args[1])
    elif args.mode=='audit':audit_arm(Path(args.args[0]),args.args[1],Path(args.args[2]))
    else:compare(*(Path(x) for x in args.args))
