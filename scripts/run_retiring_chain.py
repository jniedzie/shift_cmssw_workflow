#!/usr/bin/env python3
"""Full-chain, exact-chunk retirement with durable provenance and safe restart.

No mass branch is read. A predecessor is unlinked only after its successor is
published, fully identity-checked, and checkpointed. This intentionally cannot
be used for filtered, grouped, or externally shared inputs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import tempfile
import time

from unfiltered_qcd_contract import check

NAMES = {1: 'events_step1_part{part}.root', 2: 'events_step2_part{part}.root',
         3: 'events_AOD_part{part}.root', 4: 'events_NanoAOD_part_{part}.root'}
SCRIPTS = {1: 'run_step1_generation.sh', 2: 'run_step2_digi_raw.sh',
           3: 'run_step3_aod.sh', 4: 'run_step4_exonanoAOD.sh'}


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def save(path, value):
    # Same-directory unique temporary; retries do not truncate a valid checkpoint.
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name + '.', delete=False) as f:
        json.dump(value, f, indent=2)
        f.write('\n')
        temporary = Path(f.name)
    temporary.replace(path)


def canonical_paths(campaign, chunk, env):
    campaign = Path(campaign)
    if not campaign.is_absolute() or '..' in campaign.parts or len(campaign.parts) < 4:
        raise ValueError('Require an explicit, narrow, absolute campaign path')
    campaign = campaign.resolve()
    part = f'{chunk:04d}'
    paths = {}
    for stage, name in NAMES.items():
        directory = campaign / 'samples' / f'step{stage}'
        if Path(env[f'STEP{stage}_DIR']).resolve() != directory or directory.resolve() != directory:
            raise ValueError('Retirement requires canonical, non-symlinked campaign stage directories')
        path = directory / name.format(part=part)
        if path.is_symlink():
            raise ValueError('Refusing a symlinked chunk')
        paths[stage] = path
    return paths


def inspect(path, stage):
    """Read back published bytes, all event IDs, and required stage payload handles."""
    import ROOT
    from DataFormats.FWLite import Events, Handle
    with tempfile.TemporaryDirectory(prefix='shift_retirement_audit_') as tmp:
        local = Path(tmp) / 'input.root'
        shutil.copy2(path, local)
        checksum = sha(local)
        if checksum != sha(path):
            raise ValueError('Published bytes changed during audit')
        file = ROOT.TFile.Open(str(local))
        if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
            raise ValueError('Invalid or recovered ROOT file')
        tree = file.Get('Events')
        if not tree or tree.GetEntries() <= 0:
            raise ValueError('Missing or empty Events tree')
        entries = int(tree.GetEntries())
        ids = []
        if stage == 4:
            tree.SetBranchStatus('*', 0)
            for name in ('run', 'luminosityBlock', 'event', 'genWeight', 'nShiftMuon', 'nShiftDimuonVertex'):
                if not tree.GetBranch(name):
                    raise ValueError(f'Missing Nano branch {name}')
                tree.SetBranchStatus(name, 1)
            for event in tree:
                ids.append([int(event.run), int(event.luminosityBlock), int(event.event)])
                if float(event.genWeight) != 1 or int(event.nShiftMuon) < 0 or int(event.nShiftDimuonVertex) < 0:
                    raise ValueError('Invalid unfiltered Nano weights or counts')
            file.Close()
        else:
            file.Close()
            for event in Events(str(local)):
                aux = event.eventAuxiliary()
                ids.append([int(aux.run()), int(aux.luminosityBlock()), int(aux.event())])
                required = [('generator', 'GenEventInfoProduct')]
                if stage == 1:
                    required.append((('g4SimHits', 'MuonCSCHits'), 'std::vector<PSimHit>'))
                elif stage == 2:
                    required.append(('rawDataCollector', 'FEDRawDataCollection'))
                elif stage == 3:
                    required.append(('displacedStandAloneMuons', 'std::vector<reco::Track>'))
                for label, kind in required:
                    handle = Handle(kind)
                    event.getByLabel(label, handle)
                    if not handle.isValid():
                        raise ValueError(f'Missing stage-{stage} product {label}')
                    if kind == 'GenEventInfoProduct' and handle.product().weight() != 1.:
                        raise ValueError('Unexpected generator weight')
        if len(ids) != entries or len({tuple(i) for i in ids}) != entries:
            raise ValueError('Duplicate or unreadable event identities')
        return dict(path=str(path), sha256=checksum, bytes=local.stat().st_size,
                    event_ids=ids, events=entries, reconstructed_mass_read=False)


def retire(previous, successor, record, directory):
    """Exact two-file operation; no recursive deletion, globs, or external inputs."""
    if not previous.exists():
        return
    if previous.is_symlink() or successor.is_symlink() or not successor.is_file():
        raise ValueError('Invalid retirement targets')
    if sha(previous) != record['previous_sha256'] or sha(successor) != record['successor_sha256']:
        raise ValueError('Retirement file digest changed')
    audit = directory / f'retired_step{record["previous_stage"]}.json'
    save(audit, dict(record, status='validated_before_deletion'))
    previous.unlink()
    save(audit, dict(record, status='deleted', deleted_time=time.time()))
    print(f'Retired {previous}; regeneration requires retained seeds/configs', flush=True)


def validate_provenance(state):
    for stage in state['stages'].values():
        for path, checksum in stage['provenance'].items():
            if not Path(path).is_file() or sha(path) != checksum:
                raise ValueError(f'Retained config/log provenance changed or is missing: {path}')


def stage_provenance(campaign, stage, chunk):
    # Match the delimited chunk field, never digits in a generator seed.
    pattern = re.compile(rf'_part_?{chunk:04d}(?:_|\.)')
    configs = sorted(p for p in (campaign / 'configs' / f'step{stage}').glob('*_cfg.py')
                     if pattern.search(p.name))
    logs = sorted(p for p in (campaign / 'logs').glob(f'step{stage}_*.log')
                  if pattern.search(p.name))
    if len(configs) != 1 or len(logs) != 1 or not logs[0].stat().st_size:
        raise ValueError(f'Missing or ambiguous stage {stage} chunk {chunk} config/log provenance')
    return configs + logs


def run_command(command, **kwargs):
    """Own a child process group; do not release the lock with cmsRun alive."""
    with subprocess.Popen(command, start_new_session=True, **kwargs) as child:
        try:
            code = child.wait()
            if code:
                raise subprocess.CalledProcessError(code, command)
        except BaseException:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            raise


def interrupted(signum, frame):
    raise InterruptedError(f'Worker received signal {signum}; stopping child before releasing lock')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chunk', type=int, required=True)
    p.add_argument('--events', type=int, required=True)
    args = p.parse_args()
    if args.chunk < 0 or args.events <= 0:
        raise ValueError('Invalid chunk/events')
    check(os.environ)
    if os.environ['CLEANUP_PREVIOUS_STEP'] != '1':
        raise ValueError('Retirement must be explicitly enabled')
    root = Path(os.environ['WORKFLOW_ROOT'])
    campaign = Path(os.environ['SAMPLE_DIR']).resolve()
    paths = canonical_paths(campaign, args.chunk, os.environ)
    directory = campaign / 'chain_metadata' / f'part{args.chunk:04d}'
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / 'active.lock'
    lock.mkdir()  # Atomic cross-node exclusion. A hard-kill leaves a fail-closed lock.
    previous_handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        (lock / 'owner.json').write_text(json.dumps(dict(pid=os.getpid(), host=os.uname().nodename,
                                                        cluster=os.environ.get('ClusterId'), time=time.time())))
        keys = ('PROCESS', 'GEN_PTHAT_MIN', 'GEN_PTHAT_MAX', 'GEN_EVENT_CLASS', 'GENERATOR_SEED', 'SIMULATION_SEED',
                'COLLISION_YEAR', 'ERA', 'GEOMETRY', 'CONDITIONS', 'BEAMSPOT', 'CMSSW_RUNTIME_FINGERPRINT')
        contract = {k: os.environ.get(k, '') for k in keys}
        contract.update({k: v for k, v in os.environ.items() if k.startswith('SHIFT_')})
        contract.update(events=args.events, chunk=args.chunk,
                        fragment_sha256=sha(root / 'fragments' / (os.environ['PROCESS'] + '_pythia8_cff.py')))
        state_path = directory / 'state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else dict(contract=contract, stages={})
        if state['contract'] != contract:
            raise ValueError('Chunk configuration changed; use a new campaign')
        validate_provenance(state)
        metadata_path = campaign / 'generation_metadata' / f'part{args.chunk:04d}.json'
        highest = max(map(int, state['stages']), default=0)
        if highest:
            metadata = json.loads(metadata_path.read_text())
            current = inspect(paths[highest], highest)
            if current != state['stages'][str(highest)]['output']:
                raise ValueError('Latest checkpoint is missing, corrupt, or changed; do not regenerate automatically')
            if sha(metadata_path) != state['generation_metadata_sha256']:
                raise ValueError('Generator normalization metadata changed')
        for stage in range(max(1, highest), 5):
            if stage > highest:
                started = time.monotonic()
                metrics = directory / f'step{stage}_resources.txt'
                transcript = directory / f'step{stage}_attempt_{time.time_ns()}.log'
                print(f'Chunk {args.chunk}: starting Step {stage}; full log {transcript}', flush=True)
                # CMSSW output is already archived on EOS. Keep the attempt transcript
                # there too, rather than duplicating verbose jobs into limited AFS logs.
                with transcript.open('x') as log:
                    run_command(['/usr/bin/time', '-v', '-o', str(metrics), 'bash', str(root / SCRIPTS[stage]),
                                 str(args.chunk), str(args.events)], stdout=log, stderr=subprocess.STDOUT)
                elapsed = time.monotonic() - started
                current = inspect(paths[stage], stage)
                metadata = json.loads(metadata_path.read_text())
                expected = metadata['event_ids']
                if (metadata.get('framework_requested_events', metadata['events']) != args.events or
                        metadata['attempted_events'] != metadata['events'] or
                        metadata['generated_filter_efficiency'] != 1 or current['event_ids'] != expected):
                    raise ValueError('Unfiltered count/identity/normalization contract failed')
                # Every stage must have durable config and log snapshots before retirement.
                provenance = stage_provenance(campaign, stage, args.chunk)
                if stage > 1 and current['event_ids'] != state['stages'][str(stage-1)]['output']['event_ids']:
                    raise ValueError('Downstream identities differ from the predecessor')
                state['generation_metadata_sha256'] = sha(metadata_path)
                state['stages'][str(stage)] = dict(output=current, wall_seconds=elapsed,
                    provenance={str(path): sha(path) for path in provenance + [transcript]})
                save(state_path, state)
            if stage > 1:
                validate_provenance(state)
                previous_record = state['stages'][str(stage-1)]['output']
                successor_record = state['stages'][str(stage)]['output']
                retire(paths[stage-1], paths[stage], dict(previous_stage=stage-1,
                    previous=str(paths[stage-1]), successor=str(paths[stage]),
                    previous_sha256=previous_record['sha256'], successor_sha256=successor_record['sha256'],
                    events=successor_record['events'], reconstructed_mass_read=False), directory)
        print(f'Validated complete unfiltered chunk {args.chunk}: {metadata["events"]} events '
              f'from {args.events} requested slots', flush=True)
    finally:
        (lock / 'owner.json').unlink(missing_ok=True)
        lock.rmdir()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    main()
