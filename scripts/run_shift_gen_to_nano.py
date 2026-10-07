#!/usr/bin/env python3
"""Replay an exact, contiguous GEN slice through the established CMS IR5 chain."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import runpy
import re
import os
import signal
import struct
import shutil
import subprocess
import sys
import tarfile
import time


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def command(args, **kwargs):
    timeout = kwargs.pop('timeout', 900)
    on_poll = kwargs.pop('on_poll', None)
    if Path(args[0]).name in ('xrdcp', 'xrdfs'):
        # CMSSW's library path is incompatible with the native CERN XRootD
        # clients on some nodes. Keep delegated credentials, isolate loaders.
        args = ['/usr/bin/' + Path(args[0]).name, *args[1:]]
        env = dict(kwargs.pop('env', os.environ))
        for name in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'ROOTSYS'):
            env.pop(name, None)
        env['PATH'] = '/usr/bin:/bin'
        kwargs['env'] = env
    child = subprocess.Popen(args, start_new_session=True, **kwargs)
    try:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                code = child.wait(timeout=min(15, remaining) if on_poll else remaining)
                break
            except subprocess.TimeoutExpired:
                if on_poll:
                    on_poll()
                else:
                    raise
    except BaseException:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        raise
    if code:
        raise subprocess.CalledProcessError(code, args)


def event_progress(log_path, report, tier, validated, stall_seconds, expected_records=None):
    """Observe actual framework records, rather than refreshing a timer blindly."""
    offset, records, remainder = 0, 0, ''
    last_record = time.monotonic()
    pattern = re.compile(r'Begin processing the ([\d,]+)(?:st|nd|rd|th) record')

    def poll():
        nonlocal offset, records, last_record, remainder
        with log_path.open() as stream:
            stream.seek(offset)
            text = remainder + stream.read()
            offset = stream.tell()
        if '\n' in text:
            text, remainder = text.rsplit('\n', 1)
        else:
            remainder = text
            text = ''
        observed = [int(value.replace(',', '')) for value in pattern.findall(text)]
        if observed and max(observed) > records:
            records = max(observed)
            last_record = time.monotonic()
            report['stage_records_started'] = records
            # The final record has started, but it and output closure may
            # still take time. Keep scheduler telemetry consistent with the
            # worker's absolute stage guard instead of a per-record timer.
            if expected_records is not None and records >= expected_records:
                deadline = report.get('stage_deadline_epoch', 0)
                started = report.get('stage_started_epoch', time.time())
                report['stage_timeout_seconds'] = max(stall_seconds, math.ceil(deadline-started))
            progress(report, tier, validated)
        # The independent command deadline still bounds the last record and
        # file finalisation, even though no further record is expected.
        if expected_records is not None and records >= expected_records:
            return
        if time.monotonic() - last_record > stall_seconds:
            raise TimeoutError(f'{tier} made no event progress for {stall_seconds} seconds; records started={records}')
    return poll


def archive_evidence(work, source):
    with tarfile.open(work / 'evidence.tar.gz', 'w:gz') as archive:
        for path in sorted(work.glob('step[1-4]*')):
            if path.suffix in ('.py', '.log'):
                archive.add(path, arcname=path.name)
        archive.add(source, arcname='source.json')


def signatures(path, skip, count, indices=None):
    from DataFormats.FWLite import Events, Handle
    events = Events(str(path))
    rows = []
    for index in (range(skip, skip + count) if indices is None else indices):
        events.to(index)
        aux = events.eventAuxiliary()
        identity = [int(aux.run()), int(aux.luminosityBlock()), int(aux.event())]
        handle = Handle('edm::HepMCProduct')
        events.getByLabel('shiftEventTime', handle)
        timing_persisted = handle.isValid()
        if not timing_persisted:
            handle = Handle('edm::HepMCProduct')
            events.getByLabel('generatorSmeared', handle)
            if not handle.isValid():
                raise ValueError('Missing persisted smeared source HepMC')
        hep = handle.product().GetEvent()
        source_vertex = hep.signal_process_vertex()
        if not source_vertex:
            source_vertex = hep.vertices_begin().__deref__()
        nominal_shift = 0. if timing_persisted else -float(source_vertex.position().z())
        values = []
        it, end = hep.particles_begin(), hep.particles_end()
        while it != end:
            particle = it.__deref__()
            it.__preinc__()
            momentum, vertex = particle.momentum(), particle.production_vertex()
            position = vertex.position() if vertex else None
            values.append([int(particle.barcode()), int(particle.pdg_id()), int(particle.status()),
                           momentum.px(), momentum.py(), momentum.pz(), momentum.e(),
                           None if position is None else [position.x(), position.y(), position.z(), position.t() + nominal_shift]])
        info = Handle('GenEventInfoProduct')
        events.getByLabel('generator', info)
        if not info.isValid():
            raise ValueError('Missing source event weight')
        rows.append({'id': identity, 'weight': float(info.product().weight()),
                     'hepmc_sha256': hashlib.sha256(json.dumps(values).encode()).hexdigest(),
                     'timing_persisted_in_input': timing_persisted})
    return rows


def build_config(template, stage, input_file, output_file, skip, count, seed, timing_persisted=True, missing_filter_lumi=False, event_ids=None):
    import FWCore.ParameterSet.Config as cms
    process = runpy.run_path(str(template))['process']
    process.source = cms.Source('PoolSource', fileNames=cms.untracked.vstring('file:' + str(input_file)),
                                skipEvents=cms.untracked.uint32(skip))
    if stage == 1 and event_ids is not None:
        if len(event_ids) != count or len(set(map(tuple, event_ids))) != count:
            raise ValueError('Selected event ranges must be exact and unique')
        process.source.skipEvents = cms.untracked.uint32(0)
        process.source.eventsToProcess = cms.untracked.VEventRange([
            ':'.join(map(str, event)) + '-' + ':'.join(map(str, event)) for event in event_ids])
    process.maxEvents.input = count
    process.options.numberOfThreads = cms.untracked.uint32(1)
    process.options.numberOfStreams = cms.untracked.uint32(1)
    if hasattr(process, 'RandomNumberGeneratorService'):
        for offset, name in enumerate(sorted(process.RandomNumberGeneratorService.parameterNames_())):
            value = getattr(process.RandomNumberGeneratorService, name)
            if hasattr(value, 'initialSeed'):
                value.initialSeed = seed + offset
    if stage == 1:
        process.setName_('SHIFTSIM')
        sequence = process.generatorSmeared + process.psim
        if not timing_persisted:
            process.shiftEventTime.src = cms.InputTag('generatorSmeared', '', '@skipCurrentProcess')
            sequence = process.shiftEventTime + process.generatorSmeared + process.genParticles + process.psim
        process.simulation_step = cms.Path(sequence)
        process.simulation_step.associate(process.PPSTransportTask)
        process.schedule = cms.Schedule(process.simulation_step, process.endjob_step, process.FEVTDEBUGoutput_step)
        for name in ('generation_step', 'genfiltersummary_step', 'generator', 'mugenfilter',
                     'VtxSmeared', 'genFilterEfficiencyProducer', 'genFilterSummary'):
            if hasattr(process, name):
                delattr(process, name)
        if timing_persisted:
            delattr(process, 'shiftEventTime')
            delattr(process, 'genParticles')
        process.FEVTDEBUGoutput.SelectEvents = cms.untracked.PSet(SelectEvents=cms.vstring('simulation_step'))
        process.FEVTDEBUGoutput.outputCommands.extend(['drop *_g4SimHits_*_*', 'keep *_g4SimHits_*_SHIFTSIM'])
    outputs = process.outputModules_()
    if stage == 4 and missing_filter_lumi:
        # LumiSingletonSimpleFlatTableProducer dereferences its input even
        # with skipNonExistingSrc=True. DY has no GenFilterInfo: remove this
        # optional table from its tasks/paths instead of fabricating counters.
        process.globalTablesMCTask.remove(process.genFilterTable)
        if 'genFilterTable' in process.nanoAOD_step.moduleNames():
            raise ValueError('Optional DY filter table is still scheduled')
    if len(outputs) != 1:
        raise ValueError('Expected one output module in archived production recipe')
    output = next(iter(outputs.values()))
    output.fileName = 'file:' + str(output_file)
    if stage < 4:
        output.outputCommands.append('keep *_shiftWeightedNormalization_*_*')
    return process


def audit_edm(path, source_rows):
    """Check each detector tier against the exact parent IDs and weights."""
    import ROOT
    from DataFormats.FWLite import Events, Handle
    file = ROOT.TFile.Open(str(path))
    if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unreadable or recovered detector-tier ROOT file')
    tree = file.Get('Events')
    if not tree or tree.GetEntries() != len(source_rows):
        raise ValueError('Detector-tier event count differs from exact GEN slice')
    file.Close()
    ids, weights = [], []
    for event, source in zip(Events(str(path)), source_rows):
        aux = event.eventAuxiliary()
        identity = [int(aux.run()), int(aux.luminosityBlock()), int(aux.event())]
        handle = Handle('GenEventInfoProduct')
        event.getByLabel('generator', handle)
        if not handle.isValid():
            raise ValueError('Detector tier lost the generator weight')
        weight = float(handle.product().weight())
        if identity != source['id'] or not math.isclose(weight, source['weight'], rel_tol=1e-12, abs_tol=0):
            raise ValueError('Detector-tier identity or weight differs from source GEN')
        ids.append(identity)
        weights.append(weight)
    return {'events': len(ids), 'event_ids': ids, 'sumw': math.fsum(weights)}


def progress(report, tier, validated_events):
    """Keep a durable final record and lightweight live Condor telemetry."""
    report['current_tier'] = tier
    report['progress_epoch'] = time.time()
    report['validated_tier_events'] = dict(validated_events)
    Path('report.json').write_text(json.dumps(report, indent=2) + '\n')
    chirp = shutil.which('condor_chirp')
    if not chirp and Path('/usr/libexec/condor/condor_chirp').is_file():
        chirp = '/usr/libexec/condor/condor_chirp'
    if os.environ.get('_CONDOR_SCRATCH_DIR') and not chirp:
        report['telemetry_error'] = 'condor_chirp is unavailable'
        Path('report.json').write_text(json.dumps(report, indent=2) + '\n')
        return
    if not os.environ.get('_CONDOR_SCRATCH_DIR'):
        chirp = None
    if chirp:
        try:
            # Chirp writes are separate scheduler updates. Refresh progress
            # before shortening the timeout when entering a new stage.
            for name, value in [('ShiftNtupleProgressEpoch', int(report['progress_epoch'])),
                                ('ShiftNtupleStageTimeout', report.get('stage_timeout_seconds', 900)),
                                ('ShiftNtupleStageDeadlineEpoch', int(report.get('stage_deadline_epoch', 0))),
                                ('ShiftNtupleStratum', report['source_stratum']),
                                ('ShiftNtupleStageRecordsStarted', report.get('stage_records_started', 0)),
                                ('ShiftNtupleTier', tier),
                                *[('ShiftNtuple'+key+'Events', count) for key, count in validated_events.items()]]:
                subprocess.run([chirp, 'set_job_attr', name, json.dumps(value)],
                               check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (subprocess.SubprocessError, OSError) as error:
            # Chirp only supplies scheduler visibility.  It must not turn a
            # successful detector job into a failure: worker-side stage and
            # absolute deadlines remain independently enforced.
            report['telemetry_error'] = str(error)
            report['telemetry_error_epoch'] = time.time()
            Path('report.json').write_text(json.dumps(report, indent=2) + '\n')


def audit_nano(path, source_rows, probabilities=None):
    import ROOT
    ROOT.gROOT.SetBatch(True)
    file = ROOT.TFile.Open(str(path))
    if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unreadable or recovered NanoAOD')
    tree = file.Get('Events')
    if not tree or tree.GetEntries() != len(source_rows):
        raise ValueError('NanoAOD event count differs from exact GEN slice')
    if not tree.GetBranch('nShiftMuon') or not tree.GetBranch('genWeight'):
        raise ValueError('Missing SHIFT collection or generator weight')
    if probabilities is not None and (len(probabilities) != len(source_rows) or any(
            not math.isfinite(p) or p <= 0 or p > 1 for p in probabilities)):
        raise ValueError('Invalid detector sampling probabilities')
    ids, weights, corrected_weights, thinning_variances, muons = [], [], [], [], 0
    for index, source in enumerate(source_rows):
        if tree.GetEntry(index) <= 0:
            raise ValueError('Unreadable NanoAOD event')
        identity = [int(tree.run), int(tree.luminosityBlock), int(tree.event)]
        weight = float(tree.genWeight)
        native_expected = struct.unpack('f', struct.pack('f', source['weight']))[0]
        if identity != source['id'] or not math.isclose(weight, native_expected, rel_tol=2e-6, abs_tol=0):
            raise ValueError('NanoAOD identity or weight differs from source GEN')
        if probabilities is not None:
            probability = probabilities[index]
            expected_sampling = {'shiftSamplingProbability': probability,
                'shiftSamplingWeight': 1 / probability,
                'shiftSamplingGenWeight': source['weight'] / probability,
                'shiftOriginalGenWeight': source['weight']}
            if not all(math.isfinite(value) for value in expected_sampling.values()):
                raise ValueError('Nonfinite detector sampling correction')
            for branch, expected_value in expected_sampling.items():
                stored = tree.GetBranch(branch)
                if (not stored or len(stored.GetListOfLeaves()) != 1 or
                        stored.GetListOfLeaves().At(0).GetTypeName() != 'Double_t' or
                        not math.isclose(float(getattr(tree, branch)),
                        expected_value, rel_tol=2e-12, abs_tol=0)):
                    raise ValueError('Missing or incorrect double-precision sampling field: ' + branch)
            corrected_weights.append(expected_sampling['shiftSamplingGenWeight'])
            thinning_variances.append((source['weight'] / probability) ** 2 * (1 - probability))
        ids.append(identity)
        weights.append(weight)
        muons += int(tree.nShiftMuon)
    result = {'events': len(ids), 'event_ids': ids, 'sumw': math.fsum(weights),
              'shift_muons': muons, 'branches': len(tree.GetListOfBranches()),
              'compressed_event_bytes': int(tree.GetZipBytes())}
    if probabilities is not None:
        result.update(sampling_corrected_sumw=math.fsum(corrected_weights),
                      sampling_thinning_variance_sumw=math.fsum(thinning_variances))
    file.Close()
    return result


def add_sampling_fields(path, source_rows, probabilities):
    """Keep native generator weights; publish the explicit unbiased correction."""
    import ROOT
    from array import array
    if len(probabilities) != len(source_rows) or any(not math.isfinite(p) or not 0 < p <= 1 for p in probabilities):
        raise ValueError('Invalid detector sampling probabilities')
    file = ROOT.TFile.Open(str(path), 'UPDATE')
    if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Cannot add sampling fields to invalid NanoAOD')
    try:
        tree = file.Get('Events')
        if not tree or tree.GetEntries() != len(source_rows):
            raise ValueError('Sampling annotation count differs from NanoAOD')
        names = ('shiftSamplingProbability', 'shiftSamplingWeight', 'shiftSamplingGenWeight', 'shiftOriginalGenWeight')
        if any(tree.GetBranch(name) for name in names):
            raise ValueError('Sampling fields already exist; refusing a second correction')
        values = {name: array('d', [0]) for name in names}
        branches = {name: tree.Branch(name, values[name], name + '/D') for name in names}
        for index, (source, probability) in enumerate(zip(source_rows, probabilities)):
            if tree.GetEntry(index) <= 0 or [int(tree.run), int(tree.luminosityBlock), int(tree.event)] != source['id']:
                raise ValueError('Sampling annotation does not match the exact event identity')
            expected = (probability, 1 / probability, source['weight'] / probability, source['weight'])
            if not all(math.isfinite(value) for value in expected):
                raise ValueError('Nonfinite detector sampling correction')
            for name, value in zip(names, expected):
                values[name][0] = value
                if branches[name].Fill() < 0:
                    raise ValueError('Could not write sampling field: ' + name)
        file.cd()
        if tree.Write('', ROOT.TObject.kOverwrite) <= 0:
            raise ValueError('Could not persist sampled NanoAOD tree')
    finally:
        file.Close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--skip', type=int, required=True)
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--job', type=int, required=True)
    parser.add_argument('--templates', type=Path, default=Path('templates'))
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--canary', action='store_true')
    parser.add_argument('--stage-timeouts', default='10800,1800,5400,14400',
                        help='SIM,DIGIHLT,RECO,NANO wall limits in seconds')
    parser.add_argument('--config-timeout', type=int, default=600)
    parser.add_argument('--event-stall-timeout', type=int, default=1800)
    args = parser.parse_args()
    def terminate(signum, frame):
        raise TimeoutError('Worker stopped by signal ' + str(signum))
    signal.signal(signal.SIGTERM, terminate)
    limits = [int(value) for value in args.stage_timeouts.split(',')]
    if len(limits) != 4 or min(limits) <= 0 or min(args.config_timeout, args.event_stall_timeout) <= 0:
        raise ValueError('Four positive stage timeouts required')
    descriptor = json.loads(args.source.read_text())
    population_indices = descriptor.get('selected_indices')
    population_size = len(population_indices) if population_indices is not None else len(descriptor['event_ids'])
    if 'weights' in descriptor and len(descriptor['weights']) != len(descriptor['event_ids']):
        raise ValueError('Native source weights do not cover every GEN event')
    if population_indices is not None:
        all_probabilities = descriptor.get('sampling_probabilities', [])
        if len(all_probabilities) != population_size or any(
                not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 < p <= 1
                for p in all_probabilities):
            raise ValueError('Sampling probabilities do not cover the exact selected GEN events')
        limits = [max(floor, math.ceil(limit * args.count / 100))
                  for limit, floor in zip(limits, (1800, 900, 1800, 5400))]
    # Measured high-bin shower transport has long, finite event times. Keep
    # the event-stall guard independent of the absolute slice time budget.
    if descriptor['stratum'] == 'qcd_20toinf':
        limits[0] = max(limits[0], math.ceil(72000 * args.count / 100))
        limits[3] = max(limits[3], math.ceil(21600 * args.count / 100))
    elif descriptor['stratum'] == 'jpsi_20toinf':
        limits[0] = max(limits[0], math.ceil(36000 * args.count / 100))
        limits[3] = max(limits[3], math.ceil(21600 * args.count / 100))
    if args.skip < 0 or args.count < 1 or args.skip + args.count > population_size:
        raise ValueError('Invalid source slice')
    indices = (list(range(args.skip, args.skip + args.count)) if population_indices is None
               else population_indices[args.skip:args.skip + args.count])
    if len(set(indices)) != len(indices) or indices != sorted(indices) or any(
            not isinstance(i, int) or i < 0 or i >= len(descriptor['event_ids']) for i in indices):
        raise ValueError('Invalid selected source indices')
    probabilities = (None if population_indices is None else
                     descriptor['sampling_probabilities'][args.skip:args.skip + args.count])
    work = Path.cwd()
    report = {'schema': 'shift-gen-to-nano-receipt-v1', 'complete': False,
              'job': args.job, 'source_descriptor_sha256': sha(args.source),
              'source_receipt': descriptor['receipt'], 'source_receipt_sha256': descriptor['receipt_sha256'],
              'source_gen': descriptor['gen'], 'source_gen_sha256': descriptor['gen_sha256'],
              'source_stratum': descriptor['stratum'], 'source_skip': args.skip, 'events': args.count,
              'seed': 230000000 + args.job * 100, 'stages': {},
              'normalization_ready': False, 'physics_valid': False,
              'canary': args.canary,
              'normalization_record': descriptor.get('normalization_record'),
              'recipe': 'Frozen archived Steps 1-4; persisted GEN replay; resolved configuration hashes recorded per stage'}
    if probabilities is not None:
        report.update(source_indices=indices, sampling_probabilities=probabilities,
                      sampling_plan_sha256=descriptor['sampling']['plan_sha256'],
                      sampling_weight_convention='Native genWeight unchanged; use shiftSamplingGenWeight=W/p with original full-GEN normalization.')
    started = time.monotonic()
    published_receipt = False
    try:
        validated = {'GEN': 0, 'SIM': 0, 'DIGIHLT': 0, 'RECO': 0, 'NANO': 0}
        report['stage_timeout_seconds'] = 1800
        report['stage_deadline_epoch'] = time.time() + 1800
        progress(report, 'SETUP', validated)
        gen = work / 'gen.root'
        command(['xrdcp', '--silent', '--cksum', 'adler32', 'root://eosuser.cern.ch/' + descriptor['gen'], str(gen)], timeout=900)
        if sha(gen) != descriptor['gen_sha256']:
            raise ValueError('Frozen GEN payload changed')
        before = signatures(gen, args.skip, args.count, indices=indices)
        timing_modes = {row['timing_persisted_in_input'] for row in before}
        if len(timing_modes) != 1:
            raise ValueError('Mixed physical-time conventions in one input slice')
        report['timing_persisted_in_gen'] = before[0]['timing_persisted_in_input']
        report['timing_handoff'] = ('Read persisted shiftEventTime unchanged' if report['timing_persisted_in_gen']
                                    else 'Apply the archived nominal beamDirectionZ=-1 source-time producer once to persisted generatorSmeared; rebuild derived genParticles')
        expected = [descriptor['event_ids'][i] for i in indices]
        if [row['id'] for row in before] != expected:
            raise ValueError('GEN event order differs from source receipt')
        if 'weights' in descriptor:
            for row, weight in zip(before, (descriptor['weights'][i] for i in indices)):
                if not math.isclose(row['weight'], weight, rel_tol=1e-12, abs_tol=0):
                    raise ValueError('GEN event weight differs from frozen weighted audit')
        input_file = gen
        selection = work / 'selected_event_ids.json'
        if probabilities is not None:
            selection.write_text(json.dumps(expected) + '\n')
        validated = {'GEN': args.count, 'SIM': 0, 'DIGIHLT': 0, 'RECO': 0, 'NANO': 0}
        tiers = {1: 'SIM', 2: 'DIGIHLT', 3: 'RECO', 4: 'NANO'}
        for stage in range(1, 5):
            report['stage_timeout_seconds'] = args.config_timeout
            report['stage_deadline_epoch'] = time.time() + args.config_timeout
            report['stage_records_started'] = 0
            progress(report, tiers[stage]+'_CONFIG', validated)
            output = work / (f'step{stage}.root' if stage < 4 else 'nano.root')
            cfg = work / f'step{stage}_cfg.py'
            # CMSSW customisations mutate imported ESProducer objects. Resolve
            # each stage in a fresh interpreter, just like the original chain.
            config_start = time.monotonic()
            with (work / f'step{stage}_config.log').open('w') as config_log:
                config_arguments = [sys.executable, str(Path(__file__).resolve()), 'make-config',
                     str(args.templates / f'step{stage}.py'), str(stage), str(input_file), str(output),
                     str(args.skip if stage == 1 else 0), str(args.count), str(report['seed'] + stage * 10),
                     str(int(report['timing_persisted_in_gen'])), str(int(descriptor['stratum'].startswith('dy_'))), str(cfg)]
                if stage == 1 and probabilities is not None:
                    config_arguments.append(str(selection))
                command(config_arguments,
                     timeout=args.config_timeout, stdout=config_log, stderr=subprocess.STDOUT)
            config_seconds = time.monotonic() - config_start
            stage_start = time.monotonic()
            report['stage_started_epoch'] = time.time()
            report['stage_timeout_seconds'] = args.event_stall_timeout
            report['stage_deadline_epoch'] = time.time() + limits[stage - 1]
            progress(report, tiers[stage], validated)
            log_path = work / f'step{stage}.log'
            with log_path.open('w') as log:
                command(['cmsRun', str(cfg)], timeout=limits[stage - 1], stdout=log, stderr=subprocess.STDOUT,
                        on_poll=event_progress(log_path, report, tiers[stage], validated,
                                               args.event_stall_timeout, expected_records=args.count))
            report['stages'][str(stage)] = {'seconds': time.monotonic() - stage_start,
                                           'config_seconds': config_seconds,
                                           'config_sha256': sha(cfg), 'bytes': output.stat().st_size}
            report['stage_timeout_seconds'] = 900
            report['stage_deadline_epoch'] = time.time() + 900
            progress(report, tiers[stage]+'_AUDIT', validated)
            if stage < 4:
                report['stages'][str(stage)]['audit'] = audit_edm(output, before)
            else:
                report['stages'][str(stage)]['audit'] = audit_nano(output, before)
            if stage == 1:
                after = signatures(output, 0, args.count)
                if [{k:v for k,v in row.items() if k != 'timing_persisted_in_input'} for row in after] != [
                        {k:v for k,v in row.items() if k != 'timing_persisted_in_input'} for row in before]:
                    raise ValueError('Simulation changed GEN kinematics, vertices, nominal physical timing or weights')
            validated[tiers[stage]] = args.count
            progress(report, tiers[stage]+'_VALIDATED', validated)
            input_file = output
        if probabilities is not None:
            add_sampling_fields(input_file, before, probabilities)
        report['nano'] = audit_nano(input_file, before, probabilities)
        report['source_events'] = before
        report['complete'] = True
        report['wall_seconds'] = time.monotonic() - started
        report['nano_sha256'] = sha(input_file)
        report['nano_bytes'] = input_file.stat().st_size
        report['stage_timeout_seconds'] = 3600
        report['stage_deadline_epoch'] = time.time() + 3600
        progress(report, 'PUBLISHING' if args.publish else 'COMPLETE', validated)
        if args.publish:
            destination = descriptor['output_base'] + ('/canaries' if args.canary else '') + f'/job{args.job:07d}'
            command(['xrdfs', 'root://eosuser.cern.ch', 'mkdir', '-p', destination], timeout=120)
            report['nano_path'] = destination + '/nano.root'
            command(['xrdcp', '--silent', '--cksum', 'adler32', str(input_file), 'root://eosuser.cern.ch/' + report['nano_path']], timeout=900)
            readback = work / 'nano_readback.root'
            command(['xrdcp', '--silent', '--cksum', 'adler32', 'root://eosuser.cern.ch/' + report['nano_path'], str(readback)], timeout=900)
            if sha(readback) != report['nano_sha256'] or audit_nano(readback, before, probabilities) != report['nano']:
                raise ValueError('Published NanoAOD does not match validated local output')
            archive_evidence(work, args.source)
            command(['xrdcp', '--silent', '--cksum', 'adler32', str(work / 'evidence.tar.gz'),
                     'root://eosuser.cern.ch/' + destination + '/evidence.tar.gz'], timeout=900)
            report['evidence_sha256'] = sha(work / 'evidence.tar.gz')
            progress(report, 'COMPLETE', validated)
            command(['xrdcp', '--silent', '--cksum', 'adler32', str(work / 'report.json'),
                     'root://eosuser.cern.ch/' + destination + '/complete.json'], timeout=120)
            published_receipt = True
        else:
            progress(report, 'COMPLETE', validated)
    except Exception as error:
        report['complete'] = False
        report['error'] = repr(error)
        raise
    finally:
        # Publication already hashed and uploaded this archive. Recreating it
        # changes gzip metadata and breaks its receipt even if logs match.
        if not published_receipt:
            archive_evidence(work, args.source)
            report['evidence_sha256'] = sha(work / 'evidence.tar.gz')
        (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({k: v for k, v in report.items() if k not in ('source_events', 'nano')}))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'make-config':
        template, stage, source, target, skip, count, seed, timed, missing_filter, cfg = sys.argv[2:12]
        event_ids = json.loads(Path(sys.argv[12]).read_text()) if len(sys.argv) > 12 else None
        process = build_config(Path(template), int(stage), Path(source), Path(target),
                               int(skip), int(count), int(seed), bool(int(timed)), bool(int(missing_filter)), event_ids)
        Path(cfg).write_text(process.dumpPython())
    else:
        main()
