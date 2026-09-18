#!/usr/bin/env python3
"""Check all four smoke outputs and event identities; read no reconstructed masses."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def main():
    import ROOT
    from DataFormats.FWLite import Events
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('--events', required=True, type=int)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    names = ['step1/events_step1_part0000.root', 'step2/events_step2_part0000.root',
             'step3/events_AOD_part0000.root', 'step4/events_NanoAOD_part_0000.root']
    report, reference = {}, None
    with tempfile.TemporaryDirectory(prefix='shift_qcd_audit_') as temporary:
        for index, name in enumerate(names, 1):
            source = args.campaign / 'samples' / name
            local = Path(temporary) / source.name
            shutil.copyfile(source, local)  # avoid the EOS/FUSE ROOT-open failure
            counts, ids = {}, []
            if index < 4:
                for event in Events(str(local)):
                    aux = event.eventAuxiliary()
                    ids.append((int(aux.run()), int(aux.luminosityBlock()), int(aux.event())))
            else:
                root = ROOT.TFile.Open(str(local))
                if not root or root.IsZombie() or root.TestBit(ROOT.TFile.kRecovered):
                    raise ValueError('Invalid NanoAOD file')
                tree = root.Get('Events')
                if not tree:
                    raise ValueError('Missing NanoAOD Events tree')
                tree.SetBranchStatus('*', 0)
                for branch in ('run', 'luminosityBlock', 'event', 'nShiftMuon', 'nShiftDimuonVertex'):
                    if not tree.GetBranch(branch):
                        raise ValueError(f'Missing NanoAOD branch {branch}')
                    tree.SetBranchStatus(branch, 1)
                counts = dict(muons=0, dimuon_vertices=0)
                for event in tree:
                    ids.append((int(event.run), int(event.luminosityBlock), int(event.event)))
                    counts['muons'] += int(event.nShiftMuon)
                    counts['dimuon_vertices'] += int(event.nShiftDimuonVertex)
                root.Close()
            if len(ids) != args.events or len(set(ids)) != args.events:
                raise ValueError(f'Incorrect or duplicate event count in step {index}')
            if reference is not None and ids != reference:
                raise ValueError(f'Event identities changed in step {index}')
            reference = ids
            digest = hashlib.sha256()
            with local.open('rb') as stream:
                for block in iter(lambda: stream.read(1024*1024), b''):
                    digest.update(block)
            report[f'step{index}'] = dict(path=str(source), events=len(ids),
                bytes=local.stat().st_size, sha256=digest.hexdigest(), **counts)
    report['physics_valid'] = False
    report['reconstructed_mass_read'] = False
    with args.output.open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    print(f'Four-stage ROOT/event identity audit passed for {args.events} events')


if __name__ == '__main__':
    main()
