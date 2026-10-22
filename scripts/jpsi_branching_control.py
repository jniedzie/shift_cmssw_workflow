#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded J/psi decay-normalization diagnostic.
"""Compare forced and natural 443 decays without admitting a production source."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import time
import tarfile
from itertools import zip_longest

from audit_shift_weighted_gen import (ALGORITHM, DIRECT_JPSI_IDS, read_edm,
    validate_config, validate_ledger, validate_snapshot)
from run_shift_weighted_gen import publish

FORCING = ['443:onMode = off', '443:onIfMatch = 13 -13']
PAYLOADS = ('gen.root', 'audit.json', '443_events.jsonl.gz', 'source_slots.jsonl.gz', 'proposal_ledger.json',
            'gen_cfg.py', 'forced_validation_cfg.py', 'config_change_receipt.json',
            'native_decay_table.json', 'source_replay.json', 'cmsRun.log', 'worker_preflight.json')


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            result.update(block)
    return result.hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def channels(rows):
    """Count each connected 443 copy chain once, retaining unrelated parents."""
    particles = {row['barcode']: row for row in rows}
    require(len(particles) == len(rows), 'Duplicate particle barcode')
    ids = {key for key, row in particles.items() if row['pdg'] == 443}
    pending, parents = set(ids), []
    while pending:
        todo, chain = [pending.pop()], set()
        while todo:
            key = todo.pop()
            if key in chain:
                continue
            chain.add(key);pending.discard(key)
            row = particles[key]
            todo.extend(x for x in row['mothers']+row['daughters'] if x in ids and x not in chain)
        roots = [k for k in chain if not set(particles[k]['mothers']) & chain]
        ends = [k for k in chain if not set(particles[k]['daughters']) & chain]
        require(len(roots) == len(ends) == 1 and all(
            len(set(particles[k]['mothers']) & chain) <= 1 and
            len(set(particles[k]['daughters']) & chain) <= 1 for k in chain),
            'Ambiguous or forked 443 copies')
        terminal = particles[ends[0]]
        require(terminal['daughters'], '443 has no recorded decay')
        daughter_codes = sorted(particles[k]['pdg'] for k in terminal['daughters'])
        muonic = [pdg for pdg in daughter_codes if pdg != 22] == [-13, 13]
        muon_chains = []
        if muonic:
            for key in terminal['daughters']:
                if abs(particles[key]['pdg']) != 13:
                    continue
                copies = []
                while True:
                    require(key not in copies, 'Cycle in direct muon copies')
                    copies.append(key)
                    nexts = [x for x in particles[key]['daughters']
                             if particles[x]['pdg'] == particles[key]['pdg']]
                    require(len(nexts) <= 1, 'Ambiguous direct muon copies')
                    if not nexts:
                        break
                    key = nexts[0]
                muon_chains.append(copies)
        # Class ownership is decided before decay. A B-decay 443 is counted as
        # another parent, but never mistaken for a directly produced hard state.
        ancestry, todo = set(), list(chain)
        while todo:
            key = todo.pop()
            if key in ancestry:
                continue
            require(key in particles, 'Missing ancestor barcode')
            ancestry.add(key);todo.extend(particles[key]['mothers'])
        direct = any(abs(particles[k]['pdg']) in DIRECT_JPSI_IDS and
                     abs(particles[k]['status']) in (23, 33) for k in ancestry)
        parents.append(dict(root_barcode=roots[0], terminal_barcode=ends[0],
            copy_barcodes=sorted(chain), source_direct_ancestry=direct,
            direct_daughter_PDG_ids=daughter_codes, muonic=muonic,
            direct_muon_copy_barcodes=muon_chains, p4_GeV=terminal['p4_GeV']))
    parents.sort(key=lambda p: p['root_barcode'])
    require(any(p['source_direct_ancestry'] for p in parents),
            'Selected direct-J/psi source has no identifiable physical 443 descendant')
    direct = [p for p in parents if p['source_direct_ancestry']]
    return dict(parents=parents, terminal443=len(parents), muonic443=sum(p['muonic'] for p in parents),
        direct_terminal443=len(direct), direct_muonic443=sum(p['muonic'] for p in direct),
        any_muonic443=any(p['muonic'] for p in parents),
        any_direct_muonic443=any(p['muonic'] for p in direct))


def rate(values, trials, sigma_nd_pb):
    """Fixed-slot mean; omitted rejected slots contribute exactly zero."""
    require(type(trials) is int and trials > 1 and len(values) <= trials,
            'Invalid fixed source exposure')
    require(all(math.isfinite(v) and v >= 0 for v in values), 'Invalid weighted observable')
    total, squared = math.fsum(values), math.fsum(v*v for v in values)
    variance = max(0., squared-total*total/trials)
    return dict(rate_pb=sigma_nd_pb*total/trials,
        fixed_trial_standard_error_pb=sigma_nd_pb*math.sqrt(variance/(trials*(trials-1))),
        sum_weighted_observable=total, sum_squared_weighted_observable=squared,
        effective_nonzero_events=total*total/squared if squared else 0.,
        zero_observation_is_not_zero_physical_rate=not any(values))


def source_slots(rows, ledger):
    """Bind every fixed slot; absent selected events enter the estimator as zero."""
    saved = {tuple(row['event_id']): row for row in rows}
    require(len(saved) == len(rows), 'Duplicate accepted source slot')
    run, trials = ledger['run'], ledger['requested_trials']
    require(all(key[0] == run and key[1] == 1 and 1 <= key[2] <= trials for key in saved),
            'Accepted event outside its fixed source slots')
    return [dict(event_id=[run, 1, index], saved_selected_source=(run, 1, index) in saved,
                 selected_source_estimator_weight=saved[(run, 1, index)]['native_weight']
                     if (run, 1, index) in saved else 0.,
                 detailed_failure_status='aggregate native ledger only' if (run, 1, index) not in saved else None)
            for index in range(1, trials+1)]


def summaries(rows, ledger):
    names = ['source_class', 'terminal443', 'muonic443', 'direct_terminal443',
             'direct_muonic443', 'any_muonic443', 'any_direct_muonic443']
    return {name: rate([r['native_weight']*(1 if name == 'source_class' else r[name])
                        for r in rows], ledger['requested_trials'], ledger['sigma_nd_mb']*1e9)
            for name in names}


def make_config(plan_path, arm):
    import FWCore.ParameterSet.Config as cms
    plan = json.loads(plan_path.read_text());spec = next(x for x in plan['arms'] if x['name'] == arm)
    canonical = plan['canonical_replay']
    require(canonical['source_trials'] == 20000 and canonical['run'] == plan['canonical_run']
        and next(x for x in plan['arms'] if x['name'] == 'forced') ==
            dict(name='forced', run=canonical['run'], seed=canonical['generator_seed']),
        'Forced arm must replay the canonical source, reusing its exposure')
    native = plan_path.parent/'reference/native_cfg.py'
    require(sha(native) == plan['native_reference']['native_cfg.py']['sha256'], 'Native config changed')
    p = runpy.run_path(str(native))['process']
    require(p.generator.type_() == 'Pythia8GeneratorFilter', 'Native generator changed')
    settings = list(p.generator.PythiaParameters.processParameters)
    require([x for x in settings if x.startswith('443:')] == FORCING, 'Native443 forcing changed')
    other = p.generator.clone();other.PythiaParameters.processParameters = cms.vstring()
    other_before = other.dumpPython();path_before = p.generation_step.dumpPython()
    require(plan['fixed_trials_per_arm'] == 20000 and plan['bounds'] == [5, 10], 'Wrong bounded source')
    p.source.firstRun = cms.untracked.uint32(spec['run'])
    p.RandomNumberGeneratorService.generator.initialSeed = cms.untracked.uint32(spec['seed'])
    p.generator.UserCustomization[0].eventRun = cms.uint32(spec['run'])
    p.FEVTDEBUGoutput.fileName = cms.untracked.string('file:gen.root')
    if arm == 'natural':
        p.generator.PythiaParameters.processParameters = cms.vstring(*[x for x in settings if not x.startswith('443:')])
    check = p.generator.clone();check.PythiaParameters.processParameters = cms.vstring()
    require(check.dumpPython() == other_before.replace(str(plan['canonical_run']), str(spec['run']))
            and p.generation_step.dumpPython() == path_before, 'Nonchannel generator/path changed')
    Path('gen_cfg.py').write_text(p.dumpPython())
    actual_settings = list(p.generator.PythiaParameters.processParameters)
    # This view is never executed. It lets the unchanged production validator
    # check all other source/ledger constraints without admitting natural443.
    p.generator.PythiaParameters.processParameters = cms.vstring(*settings)
    Path('forced_validation_cfg.py').write_text(p.dumpPython())
    dump('config_change_receipt.json', dict(arm=arm, plan_sha256=sha(plan_path),
        original_cfg_sha256=sha(native), actual_cfg_sha256=sha('gen_cfg.py'),
        nonexecuted_forced_validation_view_sha256=sha('forced_validation_cfg.py'),
        validation_view_is_not_production_authority=True, only_physics_delta_is_443_forcing=True,
        process_settings_before=settings, process_settings_after=actual_settings,
        fixed_source_trials=20000, run=spec['run'], seed=spec['seed']))
    return p, settings


def native_decay_table(process, forcing, expected_xml_sha):
    """Read native particle data after exact settings; never call init/next."""
    import ROOT
    xml = Path(os.environ['PYTHIA8DATA'])/'ParticleData.xml'
    require(sha(xml) == expected_xml_sha, 'Native Pythia particle table changed')
    library = xml.parents[3]/'lib/libpythia8.so'
    require(ROOT.gSystem.Load(str(library)) >= 0, 'Cannot load native Pythia')
    ROOT.gInterpreter.AddIncludePath(str(xml.parents[3]/'include'))
    if not hasattr(ROOT, 'shiftJpsiDecayTable'):
        require(ROOT.gInterpreter.Declare(r'''
#include "Pythia8/Pythia.h"
#include <sstream>
#include <iomanip>
std::string shiftJpsiDecayTable(std::vector<std::string> settings) {
  Pythia8::Pythia p("",false);
  for (const auto& s:settings) if(!p.readString(s)) throw std::runtime_error("Pythia setting rejected");
  const auto particle=p.particleData.particleDataEntryPtr(443);
  std::ostringstream out;out<<std::setprecision(17)<<"[";
  for(int i=0;i<particle->sizeChannels();++i){
    const auto& c=particle->channel(i);if(i)out<<",";
    out<<"{\"onMode\":"<<c.onMode()<<",\"bRatio\":"<<c.bRatio()
       <<",\"meMode\":"<<c.meMode()<<",\"products\":[";
    for(int j=0;j<c.multiplicity();++j){if(j)out<<",";out<<c.product(j);}
    out<<"]}";
  }out<<"]";return out.str();
}
'''), 'Cannot compile read-only native table query')
    settings = []
    for name in process.generator.PythiaParameters.parameterSets:
        settings.extend(str(x) for x in getattr(process.generator.PythiaParameters, name))
    natural = [x for x in settings if not x.startswith('443:')]
    tables = {name: json.loads(str(ROOT.shiftJpsiDecayTable(values)))
              for name, values in [('natural', natural), ('forced', natural+forcing)]}
    dump('native_decay_table.json', dict(native_ParticleData_xml_sha256=sha(xml),
        native_Pythia_version='8.317', actual_config_sha256=sha('gen_cfg.py'), tables=tables,
        query_scope='post-readString pre-init native ParticleData; no init, next or GEN RNG draw',
        queried_all_native_source_settings=True, branching_factor_applied=False))


def audit_arm(plan_path, arm, directory):
    from DataFormats.FWLite import Events, Handle
    from audit_shift_material_scout import metadata, items
    plan=json.loads(plan_path.read_text());spec=next(x for x in plan['arms'] if x['name']==arm)
    changed=json.loads((directory/'config_change_receipt.json').read_text())
    require(changed['plan_sha256']==sha(plan_path) and changed['arm']==arm and
        changed['actual_cfg_sha256']==sha(directory/'gen_cfg.py') and
        changed['nonexecuted_forced_validation_view_sha256']==sha(directory/'forced_validation_cfg.py') and
        changed['only_physics_delta_is_443_forcing'] is True and
        changed['validation_view_is_not_production_authority'] is True, 'Config/view binding changed')
    ledger=json.loads((directory/'proposal_ledger.json').read_text());validate_ledger(ledger)
    require(ledger['sample']=='direct_jpsi' and ledger['requested_trials']==20000 and
        ledger['run']==spec['run'] and [ledger['lower'],ledger['upper']]==[5,10] and
        ledger['algorithm']==ALGORITHM, 'Wrong fixed-slot source authority')
    model=validate_config((directory/'forced_validation_cfg.py').read_text(),ledger)
    # Reconstruct the exact actual/view pair from its frozen reference before audit.
    with tempfile.TemporaryDirectory(prefix='jpsi_config_check_') as temporary:
        old=Path.cwd();os.chdir(temporary)
        try:
            process, forcing = make_config(plan_path.resolve(),arm)
            native_decay_table(process, forcing, plan['native_ParticleData_xml_sha256'])
            require(sha('gen_cfg.py')==changed['actual_cfg_sha256'] and
                    sha('forced_validation_cfg.py')==changed['nonexecuted_forced_validation_view_sha256'],
                    'Actual natural/forced config is not the exact planned443 delta')
            require(sha('native_decay_table.json') == sha(directory/'native_decay_table.json'),
                    'Native post-configuration443 channel table differs on independent readback')
        finally:os.chdir(old)
    records,embedded,weighted,native,beams=read_edm(directory/'gen.root',ledger['accepted'])
    snapshot=validate_snapshot(ledger,embedded,records,weighted)
    def product(event,label,kind):
        handle=Handle(kind);event.getByLabel(label,handle)
        require(handle.isValid(),'Missing native product '+str(label));return handle.product()
    rows=[]
    for event in Events(str(directory/'gen.root')):
        aux=event.eventAuxiliary();identity=[int(aux.run()),int(aux.luminosityBlock()),int(aux.event())]
        hep=product(event,('generator','unsmeared'),'edm::HepMCProduct').GetEvent()
        graph=[]
        for particle in items(hep.particles_begin(),hep.particles_end()):
            birth,end,p=particle.production_vertex(),particle.end_vertex(),particle.momentum()
            graph.append(dict(barcode=int(particle.barcode()),pdg=int(particle.pdg_id()),status=int(particle.status()),
                p4_GeV=[float(p.px()),float(p.py()),float(p.pz()),float(p.e())],
                mothers=[int(x.barcode()) for x in items(birth.particles_in_const_begin(),birth.particles_in_const_end())] if birth else [],
                daughters=[int(x.barcode()) for x in items(end.particles_out_const_begin(),end.particles_out_const_end())] if end else []))
        row=dict(event_id=identity,native_weight=float(product(event,'generator','GenEventInfoProduct').weight()),**channels(graph))
        if arm=='forced':require(row['terminal443']==row['muonic443'],'Forced443 has nonmuonic decay')
        rows.append(row)
    require([r['event_id'] for r in rows]==snapshot['event_ids'] and
            [r['native_weight'] for r in rows]==snapshot['weights'],'Channel/ledger original identities or W differ')
    table=json.loads((directory/'native_decay_table.json').read_text())
    require(table['native_ParticleData_xml_sha256']==plan['native_ParticleData_xml_sha256'] and
        table['actual_config_sha256']==changed['actual_cfg_sha256'] and
        table['branching_factor_applied'] is False,'Native particle table binding changed')
    raw=''.join(json.dumps(r,allow_nan=False)+'\n' for r in rows).encode()
    (directory/'443_events.jsonl.gz').write_bytes(gzip.compress(raw,mtime=0))
    slots=''.join(json.dumps(r,allow_nan=False)+'\n' for r in source_slots(rows,ledger)).encode()
    (directory/'source_slots.jsonl.gz').write_bytes(gzip.compress(slots,mtime=0))
    report=dict(schema='shift-jpsi-forced-natural-diagnostic-v1',complete=True,diagnostic_only=True,
        arm=arm,plan_sha256=sha(plan_path),GEN_sha256=sha(directory/'gen.root'),
        config_sha256=sha(directory/'gen_cfg.py'),validation_view_sha256=sha(directory/'forced_validation_cfg.py'),
        validation_view_is_not_production_authority=True,production_validator_unchanged=True,
        native_decay_table_sha256=sha(directory/'native_decay_table.json'),event_rows_sha256=sha(directory/'443_events.jsonl.gz'),
        full_source_slot_rows_sha256=sha(directory/'source_slots.jsonl.gz'),
        denominator_kind='fixed_source_trials',fixed_source_trials=20000,accepted_events=len(rows),
        ledger=ledger,source_model_sha256=model,weighted_run_info=weighted,native_run_info_not_authority=native,
        full_Run_Lumi=metadata(directory/'gen.root'),beams=beams,statistics=summaries(rows,ledger),
        terminal443_multiplicity=dict(Counter(str(r['terminal443']) for r in rows)),
        muonic443_multiplicity=dict(Counter(str(r['muonic443']) for r in rows)),
        native_sumW=snapshot['sumw'],native_sumW2=snapshot['sumw2'],
        no_decay_or_detector_selection=True,normalization_ready=False,correction_applied=False,
        caveat='Source sigmaND is fixed by the exact native model; named weighted-run error is the full-slot MC error. No branching or material factor is applied.')
    dump(directory/'audit.json',report)


def comparison(forced,natural,replay):
    require(replay.get('complete') is True and replay.get('full_HepMC_native_binary64_values_and_serialization_exact') is True
        and replay.get('GenEventInfo_IDs_native_weights_and_order_exact') is True
        and replay.get('full_Run_Lumi_native_ledger_exact') is True
        and replay.get('exposure_reused_not_additive') is True and replay.get('source_trials') == 20000,
        'Canonical forced replay must gate natural rate interpretation')
    require(forced['complete'] and natural['complete'] and forced['arm']=='forced' and
        natural['arm']=='natural' and forced['plan_sha256']==natural['plan_sha256'], 'Unpaired diagnostic arms')
    require(forced['fixed_source_trials']==natural['fixed_source_trials']==20000 and
        forced['source_model_sha256']==natural['source_model_sha256'] and
        forced['ledger']['sigma_nd_mb']==natural['ledger']['sigma_nd_mb'], 'Changed source/exposure')
    out={}
    for name,left in forced['statistics'].items():
        right=natural['statistics'][name];a,b=left['rate_pb'],right['rate_pb']
        err=math.hypot(left['fixed_trial_standard_error_pb'],right['fixed_trial_standard_error_pb'])
        out[name]=dict(forced=left,natural=right,natural_over_forced=b/a if a else None,
            difference_standard_errors=(b-a)/err if err else None,
            sparse_natural_effective_events=right['effective_nonzero_events']<25)
    return dict(complete=True,diagnostic_only=True,comparisons=out,full_source_trials_per_arm=20000,
        arms_are_independent_not_additive=True,normalization_ready=False,full_production_ready=False,
        correction_applied=False,interpretation='Review source-class closure and native channel table before any rate-convention decision; parent yields and event probabilities differ for multiple443.')


def load_request(path):
    request=json.loads(path.read_text());tools=Path(__file__).resolve().parents[1]
    plan_path=tools/'plan/plan.json';plan=json.loads(plan_path.read_text())
    require(request['stage'] in ('jpsi_branching_arm','jpsi_branching_comparison') and
        sha(plan_path)==request['plan_sha256'] and plan['fixed_trials_per_arm']==20000,
        'Unknown diagnostic request or changed plan')
    registration=tools/'storage_registration.json'
    require(sha(registration)==request['storage_registration_sha256'], 'Storage registration changed')
    budget=json.loads(registration.read_text())
    require(budget['science_workers']==2 and budget['science_publication_bound_per_worker_bytes']==67108864
        and budget['verification_publication_bound_bytes']==1048576 and
        budget['combined_component_bound_bytes']<=budget['registered_parent_bytes'], 'Missing bounded allocation')
    for name,item in plan['native_reference'].items():
        local=plan_path.parent/'reference'/name
        require(sha(local)==item['sha256'] and local.stat().st_size==item['bytes'], 'Native reference changed: '+name)
    require(plan['assets']['native_runtime_sha256'] ==
        '5d5a20a506fe8901916acd34b456c1c194148ae32aee3c62a2f6d5191374e807' and
        json.loads((tools/'runtimes.json').read_text())['cmssw']['sha256'] == plan['assets']['native_runtime_sha256'],
        'Diagnostic needs its exact original native runtime')
    require(plan['assets']['weighted_plugin_sha256'] ==
        '922c14f1016ff174256b928faaf64ac244a41604635c3fc3609e78eb44251ffe',
        'Canonical native weighted hook changed')
    reference=tools/'plugin/original_source_reference.tar.gz'
    require(sha(reference)==plan['assets']['source_reference_sha256'],'Original source reference changed')
    with tarfile.open(reference) as archive:
        for name,expected in [('MultipartonInteractions_tail.cc','0dea34d3b52d4459909c493dafc6cf1f64d91e532529046505879f8d0863ce76'),
                              ('ProcessContainer_weighted_stats.cc','9c7c3eb0dbe91cbbe617600099cde052238665068fead2353a408904d334af91')]:
            require(hashlib.sha256(archive.extractfile(name).read()).hexdigest()==expected,
                    'Canonical original source bytes changed: '+name)
    return request,tools,plan_path,plan


def install_plugin(tools,plan):
    import shutil
    scratch=Path(os.environ['_CONDOR_SCRATCH_DIR']).resolve()
    release=Path(os.environ['CMSSW_BASE']).resolve()
    require(scratch in release.parents, 'Plugin installation requires worker-local CMSSW')
    plugin=tools/'plugin/pluginShiftMpiWeightedProposalHook.so'
    overlay=tools/'plugin/libShiftMpiTailProposal.so'
    require(sha(plugin)==plan['assets']['weighted_plugin_sha256'] and
            sha(overlay)==plan['assets']['overlay_sha256'], 'Source plugin/overlay changed')
    library=release/'lib'/os.environ['SCRAM_ARCH']
    shutil.copyfile(plugin,library/plugin.name)
    subprocess.run(['edmPluginRefresh',str(library)],check=True,timeout=120,
                   stdout=Path('plugin_refresh.log').open('w'),stderr=subprocess.STDOUT)
    return overlay


def run_arm(path,prepare_only=False):
    started=time.monotonic();request,tools,plan_path,plan=load_request(path)
    arm=request['arm']
    require(arm in ('forced','natural') and request['publication_bound_bytes']==67108864,
            'Wrong diagnostic arm/allocation')
    overlay=install_plugin(tools,plan)
    process,forcing=make_config(plan_path,arm)
    native_decay_table(process,forcing,plan['native_ParticleData_xml_sha256'])
    from DataFormats.FWLite import Events, Handle
    dump('worker_preflight.json',dict(complete=True,GEN_or_cmsRun=False,
        config_sha256=sha('gen_cfg.py'),validation_view_sha256=sha('forced_validation_cfg.py'),
        native_particle_table_sha256=sha('native_decay_table.json'),source_plugin_installed=True,
        full_fixed_source_trials=20000,production_validator_unchanged=True))
    if prepare_only:
        dump('report.json',dict(complete=True,prepared_only=True,events=0,GEN_or_cmsRun=False));return
    env={k:v for k,v in os.environ.items() if not k.startswith(('SHIFT_MPI_','SHIFT_HEAVY_')) and k!='LD_PRELOAD'}
    env['LD_PRELOAD']=str(overlay)
    with Path('cmsRun.log').open('w') as log:
        subprocess.run(['cmsRun','gen_cfg.py'],env=env,stdout=log,stderr=subprocess.STDOUT,
                       check=True,timeout=request['stage_timeout_seconds'])
    audit_arm(plan_path,arm,Path.cwd())
    audit=json.loads(Path('audit.json').read_text())
    if arm == 'forced':
        with tempfile.TemporaryDirectory(prefix='canonical_jpsi_') as temporary:
            golden=Path(temporary)/'golden.root'
            fetch(plan['canonical_replay']['GEN_url'],golden,
                  dict(bytes=plan['canonical_replay']['GEN_bytes'],sha256=plan['canonical_replay']['GEN_sha256']))
            dump('source_replay.json',native_source_replay(golden,Path('gen.root'),plan))
    else:
        dump('source_replay.json',dict(canonical_replay_applies=False,independent_natural_control=True))
    payload=sum(Path(name).stat().st_size for name in PAYLOADS)
    require(payload+1048576<=request['publication_bound_bytes'],'Complete source/evidence exceeds registered arm bytes')
    report=dict(complete=True,diagnostic_only=True,arm=arm,events=audit['accepted_events'],
        full_fixed_source_trials=20000,denominator_kind='fixed_source_trials',
        native_sumW=audit['native_sumW'],native_sumW2=audit['native_sumW2'],
        request_sha256=sha(path),plan_sha256=sha(plan_path),helper_sha256=sha(Path(__file__)),
        storage_registration_sha256=request['storage_registration_sha256'],audit_sha256=sha('audit.json'),
        wall_seconds=time.monotonic()-started,publication_bound_bytes=request['publication_bound_bytes'],
        exposure_role='reused_canonical_source_replay' if arm == 'forced' else 'independent_natural_control',
        normalization_ready=False,full_production_ready=False,correction_applied=False)
    report['published']=publish([(Path(name),name) for name in PAYLOADS],request['publication_directory'])
    report['publication_sha256_readback_verified']=True;dump('report.json',report)
    require(Path('report.json').stat().st_size<=65536,'Oversized arm receipt')
    publish([(Path('report.json'),'complete.json')],request['publication_directory'])



def replay_frame(event):
    """Full native HepMC encoding and exact binary64 fields plus GenEventInfo, with no ROOT file identity."""
    import ROOT
    from DataFormats.FWLite import Handle
    def get(label,kind):
        h=Handle(kind);event.getByLabel(label,h)
        require(h.isValid(),'Missing canonical replay product '+str(label));return h,h.product()
    if not hasattr(ROOT,'shiftJpsiExactHepMC'):
        require(ROOT.gInterpreter.Declare(r"""
#include "HepMC/GenEvent.h"
#include "HepMC/IO_GenEvent.h"
#include <sstream>
std::string shiftJpsiExactHepMC(const HepMC::GenEvent& event) {
  std::ostringstream out;
  {HepMC::IO_GenEvent writer(static_cast<std::ostream&>(out));writer.precision(16);writer.write_event(&event);}
  // HepMC ASCII permits at most16 decimal digits. Supplement every floating
  // field with exact hexadecimal doubles, retaining native ASCII for all
  // identifiers, edges, flow codes, weight names and discrete metadata.
  out<<std::hexfloat<<"\nEXACT_BINARY64 "<<event.event_scale()<<' '<<event.alphaQCD()<<' '<<event.alphaQED();
  for(double x:event.weights())out<<' '<<x;
  for(auto v=event.vertices_begin();v!=event.vertices_end();++v){
    const auto& x=(*v)->position();out<<" V "<<(*v)->barcode()<<' '<<x.x()<<' '<<x.y()<<' '<<x.z()<<' '<<x.t();
    for(double w:(*v)->weights())out<<' '<<w;
  }
  for(auto i=event.particles_begin();i!=event.particles_end();++i){
    const auto& p=(*i)->momentum();const auto& s=(*i)->polarization();
    out<<" P "<<(*i)->barcode()<<' '<<p.px()<<' '<<p.py()<<' '<<p.pz()<<' '<<p.e()
       <<' '<<(*i)->generated_mass()<<' '<<s.theta()<<' '<<s.phi();
  }
  if(auto p=event.pdf_info())out<<" PDF "<<p->x1()<<' '<<p->x2()<<' '<<p->scalePDF()<<' '<<p->pdf1()<<' '<<p->pdf2();
  if(auto c=event.cross_section())out<<" XSEC "<<c->cross_section()<<' '<<c->cross_section_error();
  if(auto h=event.heavy_ion())out<<" HI "<<h->impact_parameter()<<' '<<h->event_plane_angle()<<' '<<h->eccentricity()<<' '<<h->sigma_inel_NN();
  return out.str();
}
"""),'Cannot encode complete native HepMC')
    graphs={}
    for name,label in [('generatorSmeared','generatorSmeared'),('generator:unsmeared',('generator','unsmeared'))]:
        handle,value=get(label,'edm::HepMCProduct')
        graphs[name]=hashlib.sha256(str(ROOT.shiftJpsiExactHepMC(value.GetEvent())).encode()).hexdigest()
    handle,info=get('generator','GenEventInfoProduct');pdf=info.pdf();a=event.eventAuxiliary()
    return dict(event_id=[int(a.run()),int(a.luminosityBlock()),int(a.event())],graphs=graphs,
        info=dict(weights=list(map(float,info.weights())),process=int(info.signalProcessID()),
            qScale=float(info.qScale()),alphaQCD=float(info.alphaQCD()),alphaQED=float(info.alphaQED()),
            binningValues=list(map(float,info.binningValues())),DJRValues=list(map(float,info.DJRValues())),
            nMEPartons=int(info.nMEPartons()),nMEPartonsFiltered=int(info.nMEPartonsFiltered()),
            pdf=dict(id=[int(pdf.id.first),int(pdf.id.second)],x=[float(pdf.x.first),float(pdf.x.second)],
                xPDF=[float(pdf.xPDF.first),float(pdf.xPDF.second)],scalePDF=float(pdf.scalePDF)) if pdf else None))


def exact_replay_frames(left,right):
    """Compare ordered frame streams; source slots and native W cannot change."""
    digest=hashlib.sha256();count=0;seen=set()
    for a,b in zip_longest(left,right):
        require(a is not None and b is not None,'Canonical replay event count differs')
        require(a==b,'Canonical replay graph/info/W differs at '+str(a['event_id']))
        identity=tuple(a['event_id']);require(identity not in seen,'Duplicate canonical replay event')
        seen.add(identity);count+=1
        digest.update(json.dumps(a,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n')
    return count,digest.hexdigest()


def native_source_replay(golden,replay,plan):
    from DataFormats.FWLite import Events
    from audit_shift_material_scout import metadata
    require(sha(golden)==plan['canonical_replay']['GEN_sha256'], 'Canonical golden file changed')
    old,new=metadata(golden),metadata(replay)
    require(old==new,'Canonical full Run/Lumi/native fixed-slot ledger differs')
    n,digest=exact_replay_frames((replay_frame(e) for e in Events(str(golden))),
                               (replay_frame(e) for e in Events(str(replay))))
    require(n==plan['canonical_replay']['saved_events'],'Canonical saved original count differs')
    return dict(complete=True,events=n,source_trials=20000,original_GEN_sha256=sha(golden),
        replay_GEN_sha256=sha(replay),native_all_event_graph_info_W_sha256=digest,
        full_Run_Lumi_sha256=hashlib.sha256(json.dumps(old,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
        ROOT_GUID_and_process_history_not_compared=True,full_HepMC_native_binary64_values_and_serialization_exact=True,
        GenEventInfo_IDs_native_weights_and_order_exact=True,full_Run_Lumi_native_ledger_exact=True,
        exposure_reused_not_additive=True,new_independent_source_exposure=0)

def fetch(url,path,expected=None):
    subprocess.run(['xrdcp','--silent','--cksum','adler32',url,str(path)],check=True,timeout=900)
    if expected is not None:
        require(sha(path)==expected['sha256'] and path.stat().st_size==expected['bytes'],
                'Independent readback differs: '+path.name)


def verify(path,prepare_only=False):
    request,tools,plan_path,plan=load_request(path)
    require(request['publication_bound_bytes']==1048576,'Wrong comparison allocation')
    arm_requests={arm:json.loads((tools/'requests'/(arm+'.json')).read_text()) for arm in ('forced','natural')}
    for arm,item in arm_requests.items():
        require(sha(tools/'requests'/(arm+'.json'))==request['arm_request_sha256'][arm] and
            item['arm']==arm and item['plan_sha256']==request['plan_sha256'] and
            item['storage_registration_sha256']==request['storage_registration_sha256'],'Unbound arm request')
    if prepare_only:
        from DataFormats.FWLite import Events, Handle
        dump('summary.json',dict(complete=True,prepared_only=True,GEN_or_cmsRun=False));return
    audits=[];completions=[];replay=None
    with tempfile.TemporaryDirectory(prefix='jpsi_native_readback_') as temporary:
        for arm in ('forced','natural'):
            directory=Path(temporary)/arm;directory.mkdir()
            arm_request=arm_requests[arm];base='root://eosuser.cern.ch/'+arm_request['publication_directory']
            fetch(base+'/complete.json',directory/'complete.json')
            require((directory/'complete.json').stat().st_size<=65536,'Oversized arm receipt')
            complete=json.loads((directory/'complete.json').read_text())
            require(complete['complete'] is True and complete['arm']==arm and
                complete['request_sha256']==request['arm_request_sha256'][arm] and
                complete['plan_sha256']==request['plan_sha256'] and complete['helper_sha256']==sha(Path(__file__)) and
                complete['publication_sha256_readback_verified'] is True,'Unbound actual arm completion')
            publications={item['name']:item for item in complete['published']}
            require(len(publications)==len(complete['published']) and set(publications)==set(PAYLOADS),
                    'Wrong published source/evidence inventory')
            require(sum(item['bytes'] for item in publications.values())+65536<=arm_request['publication_bound_bytes'],
                    'Arm exceeds registered bytes')
            for name,item in publications.items():
                require(item['path']==arm_request['publication_directory']+'/'+name and
                    item['independent_sha256_readback'] is True,'Unbound published source artifact')
                fetch(base+'/'+name,directory/name,item)
            if arm == 'forced':
                golden=directory/'golden.root'
                fetch(plan['canonical_replay']['GEN_url'],golden,
                      dict(bytes=plan['canonical_replay']['GEN_bytes'],sha256=plan['canonical_replay']['GEN_sha256']))
                replay=native_source_replay(golden,directory/'gen.root',plan)
                require(replay==json.loads((directory/'source_replay.json').read_text()),
                        'Canonical native replay differs on independent readback')
                golden.unlink()
            old=json.loads((directory/'audit.json').read_text());old_rows=(directory/'443_events.jsonl.gz').read_bytes()
            audit_arm(plan_path,arm,directory)
            fresh=json.loads((directory/'audit.json').read_text())
            require(fresh==old and old_rows==(directory/'443_events.jsonl.gz').read_bytes(),
                    'Repeated full native fixed-trial/channel audit differs')
            dump(arm+'_native_audit.json',fresh);audits.append(fresh)
            completions.append(dict(arm=arm,published_complete_sha256=sha(directory/'complete.json'),
                native_all_saved_originals_read_again=True,fixed_trials=fresh['fixed_source_trials'],
                accepted_events=fresh['accepted_events']))
    require(replay is not None and replay['complete'],'Canonical forced replay must gate natural rate interpretation')
    result=comparison(*audits,replay);result['canonical_forced_replay']=replay
    result['forced_exposure_is_reused_not_new_statistics']=True
    dump('comparison.json',result)
    names=['comparison.json','forced_native_audit.json','natural_native_audit.json']
    require(sum(Path(name).stat().st_size for name in names)+131072<=request['publication_bound_bytes'],
            'Comparison exceeds registered byte bound')
    report=dict(complete=True,diagnostic_only=True,arms=completions,
        request_sha256=sha(path),plan_sha256=sha(plan_path),helper_sha256=sha(Path(__file__)),
        storage_registration_sha256=request['storage_registration_sha256'],
        comparison=json.loads(Path('comparison.json').read_text()),normalization_ready=False,
        full_production_ready=False,correction_applied=False,native_all_source_readback=True)
    report['published']=publish([(Path(name),name) for name in names],request['publication_directory'])
    dump('summary.json',report);require(Path('summary.json').stat().st_size<=65536,'Oversized comparison receipt')
    publish([(Path('summary.json'),'complete.json')],request['publication_directory'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('arm','compare'));parser.add_argument('request',type=Path)
    parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
    try:
        (run_arm if args.mode=='arm' else verify)(args.request,args.prepare_only)
    except Exception as error:
        dump('report.json' if args.mode=='arm' else 'summary.json',
             dict(complete=False,error=repr(error),normalization_ready=False,full_production_ready=False))
        raise
