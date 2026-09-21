#!/usr/bin/env python3
"""Replay persisted GEN into unchanged simulation, with explicit MC sampling ledger."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import time
from run_sampling_pilot import audit_nano


def mkdir_shared(path):
    """Tolerate transient visibility races only for shared parent directories."""
    for attempt in range(10):
        try:
            path.mkdir(parents=True, exist_ok=True)
            return
        except (FileExistsError, FileNotFoundError):
            if attempt == 9:
                raise
            time.sleep(1)


def signatures(path, wanted, collect_hits=False):
    from DataFormats.FWLite import Events, Handle
    result = {}
    for e in Events(str(path)):
        a = e.eventAuxiliary()
        identity = (int(a.run()), int(a.luminosityBlock()), int(a.event()))
        if identity not in wanted:
            continue
        h = Handle('edm::HepMCProduct')
        e.getByLabel('shiftEventTime', h)
        if not h.isValid():
            raise ValueError('Missing already-shifted source HepMC')
        hep = h.product().GetEvent()
        values = []
        it, end = hep.particles_begin(), hep.particles_end()
        while it != end:
            p = it.__deref__()
            it.__preinc__()
            m, v = p.momentum(), p.production_vertex()
            pos = v.position() if v else None
            values.append([int(p.barcode()), int(p.pdg_id()), int(p.status()),
                m.px(), m.py(), m.pz(), m.e(),
                None if pos is None else [pos.x(), pos.y(), pos.z(), pos.t()]])
        item = dict(hepmc_sha256=hashlib.sha256(json.dumps(values).encode()).hexdigest())
        if collect_hits:
            item['simhits'] = {}
            for name in ('MuonCSCHits', 'MuonDTHits', 'MuonRPCHits', 'MuonGEMHits'):
                hit = Handle('std::vector<PSimHit>')
                e.getByLabel(('g4SimHits', name, 'SHIFTSIM'), hit)
                if not hit.isValid():
                    raise ValueError('Missing replay SimHits')
                item['simhits'][name] = len(hit.product())
        if identity in result:
            raise ValueError('Duplicate identity')
        result[identity] = item
    if set(result) != wanted:
        raise ValueError('Event identity set does not match sampling ledger')
    return result


def main():
    import FWCore.ParameterSet.Config as cms
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('ledger', type=Path)
    p.add_argument('--chunk', type=int, required=True)
    p.add_argument('--template', type=Path, required=True)
    p.add_argument('--sim-seed', type=int, required=True)
    p.add_argument('--sim-only', action='store_true')
    p.add_argument('--stage-timeout', type=int,
                   default=int(os.environ.get('SAMPLING_STAGE_TIMEOUT_SECONDS', '7200')))
    args = p.parse_args()
    if not 60 <= args.stage_timeout <= 21600:
        p.error('External stage allowance must be between 60 and 21600 seconds')
    ledger = json.loads(args.ledger.read_text())
    source = Path(ledger['source_report'])
    if hashlib.sha256(source.read_bytes()).hexdigest() != ledger['source_report_sha256']:
        raise ValueError('Source report changed')
    selected = ledger['chunks'][args.chunk]
    wanted = {tuple(row['id']) for row in selected}
    if not selected or len(wanted) != len(selected):
        raise ValueError('Empty or duplicate selected input')
    reference = json.loads(source.read_text())
    by_id = {tuple(row['id']): row for row in reference['generation']['rows']}
    rows = [copy.deepcopy(by_id[tuple(s['id'])]) for s in selected]
    for row, choice in zip(rows, selected):
        row['sampling'] = choice
    campaign = Path(os.environ['SAMPLE_DIR'])
    part = f'{args.chunk:04d}'
    out = campaign/'sampling_replay'/f'part{part}'
    # Distributed filesystems can briefly report a concurrently created
    # parent as existing but not yet stat-able. Never reuse the chunk itself.
    mkdir_shared(out.parent)
    out.mkdir(exist_ok=False)
    workflow = Path(__file__).resolve().parents[1]
    report = dict(status='running', physics_valid=False, normalization_ready=False,
        stage_timeout_seconds=args.stage_timeout,
        reconstructed_mass_read=False, ledger=str(args.ledger.resolve()),
        ledger_sha256=hashlib.sha256(args.ledger.read_bytes()).hexdigest(),
        sim_seed=args.sim_seed, pps_seed=args.sim_seed+500000, rows=rows, source_report=str(source),
        source_framework_attempts=ledger['framework_attempts'],
        weighting_warning='Nano genWeight and parent GEN run/lumi counters are not replay normalization. Use the unique parent ledger and inverse-probability weights.')
    try:
        with tempfile.TemporaryDirectory(prefix='shift_gen_replay_') as temporary:
            work = Path(temporary)
            input_file = work/'gen.root'
            shutil.copy2(source.with_name('gen.root'), input_file)
            report['input_gen_sha256'] = hashlib.sha256(input_file.read_bytes()).hexdigest()
            before = signatures(input_file, wanted)
            process = runpy.run_path(str(args.template))['process']
            process.setName_('SHIFTSIM')
            process.source = cms.Source('PoolSource', fileNames=cms.untracked.vstring('file:'+str(input_file)),
                eventsToProcess=cms.untracked.VEventRange(*['%d:%d:%d-%d:%d:%d' % (*identity,*identity) for identity in sorted(wanted)]))
            process.maxEvents.input = -1
            process.RandomNumberGeneratorService.g4SimHits.initialSeed = args.sim_seed
            process.RandomNumberGeneratorService.LHCTransport.initialSeed = args.sim_seed+500000
            process.options.wantSummary = cms.untracked.bool(True)
            process.MessageLogger.cerr.FwkReport.reportEvery = 1
            process.simulation_step = cms.Path(process.generatorSmeared + process.psim)
            # CMS's forward transport is a task on pgen in the combined chain.
            # GEN-only output did not persist it; regenerate it unchanged.
            process.simulation_step.associate(process.PPSTransportTask)
            process.schedule = cms.Schedule(process.simulation_step, process.endjob_step, process.FEVTDEBUGoutput_step)
            # Existing source and timing products must be read, not regenerated.
            for name in ('generation_step', 'genfiltersummary_step', 'generator',
                         'mugenfilter', 'VtxSmeared', 'shiftEventTime', 'genParticles',
                         'genFilterEfficiencyProducer', 'genFilterSummary'):
                if hasattr(process, name):
                    delattr(process, name)
            output_file = work/'step1.root'
            process.FEVTDEBUGoutput.fileName = 'file:'+str(output_file)
            process.FEVTDEBUGoutput.SelectEvents = cms.untracked.PSet(SelectEvents=cms.vstring('simulation_step'))
            process.FEVTDEBUGoutput.outputCommands.extend(['drop *_g4SimHits_*_*', 'keep *_g4SimHits_*_SHIFTSIM'])
            cfg = out/'step1_cfg.py'
            cfg.write_text(process.dumpPython())
            report['config_sha256'] = hashlib.sha256(cfg.read_bytes()).hexdigest()
            def run(command, name):
                start = time.monotonic()
                with (out/(name+'.log')).open('w') as log:
                    subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT,
                                   check=True, timeout=args.stage_timeout)
                report[name+'_wall_seconds'] = time.monotonic()-start
            run(['cmsRun', str(cfg)], 'step1')
            after = signatures(output_file, wanted, True)
            for row in rows:
                identity = tuple(row['id'])
                if before[identity]['hepmc_sha256'] != after[identity]['hepmc_sha256']:
                    raise ValueError('GEN four-momenta, vertices or physical timing changed')
                row.update(after[identity])
            dest = campaign/'samples/step1'/f'events_step1_part{part}.root'
            mkdir_shared(dest.parent)
            shutil.copy2(output_file, dest)
            configs = campaign/'configs/step1'
            mkdir_shared(configs)
            shutil.copy2(cfg, configs/f'events_step1_part{part}_seed{os.environ["GENERATOR_SEED"]}_cfg.py')
            if not args.sim_only:
                for stage, script in ((2,'run_step2_digi_raw.sh'), (3,'run_step3_aod.sh'), (4,'run_step4_exonanoAOD.sh')):
                    run(['bash', str(workflow/script), str(args.chunk), str(len(rows))], f'step{stage}')
                nano = work/'nano.root'
                shutil.copy2(campaign/'samples/step4'/f'events_NanoAOD_part_{part}.root', nano)
                report['reconstruction'] = audit_nano(nano, rows)
            report['status'] = 'validated'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = repr(error)
        raise
    finally:
        (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k != 'rows'}))


if __name__ == '__main__':
    main()
