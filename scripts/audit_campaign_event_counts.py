#!/usr/bin/env python3
"""Read exact campaign chunks and event identities, never reconstructed masses."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile


def main():
    import ROOT
    from DataFormats.FWLite import Events
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('campaign', type=Path)
    p.add_argument('--chunks', type=int, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--start', type=int, default=0)
    p.add_argument('--all-stages', action='store_true')
    args = p.parse_args()
    if args.chunks <= 0 or args.start < 0:
        p.error('Require a positive chunk count and non-negative start')
    counts, rows, identities = Counter(), [], set()
    result = dict(status='running', reconstructed_mass_read=False,
                  campaign=str(args.campaign), rows=rows, counts=counts)
    names = ('step1/events_step1_part{part}.root', 'step2/events_step2_part{part}.root',
             'step3/events_AOD_part{part}.root', 'step4/events_NanoAOD_part_{part}.root')
    try:
        with tempfile.TemporaryDirectory(prefix='shift_count_audit_') as temporary:
            local = Path(temporary) / 'input.root'
            for chunk in range(args.start, args.start+args.chunks):
                part = f'{chunk:04d}'
                metadata = json.loads((args.campaign/'generation_metadata'/f'part{part}.json').read_text())
                reference, row = None, dict(chunk=chunk, stages={})
                stages = range(4) if args.all_stages else (3,)
                for stage in stages:
                    source = args.campaign / 'samples' / names[stage].format(part=part)
                    shutil.copyfile(source, local)
                    root = ROOT.TFile.Open(str(local))
                    if not root or root.IsZombie() or root.TestBit(ROOT.TFile.kRecovered):
                        raise ValueError(f'Invalid ROOT file: {source}')
                    tree = root.Get('Events')
                    if not tree or tree.GetEntries() != metadata['events']:
                        raise ValueError(f'Wrong Events count: {source}')
                    ids = []
                    if stage < 3:
                        root.Close()
                        for event in Events(str(local)):
                            a = event.eventAuxiliary()
                            ids.append((int(a.run()), int(a.luminosityBlock()), int(a.event())))
                    else:
                        tree.SetBranchStatus('*', 0)
                        for name in ('run', 'luminosityBlock', 'event', 'nShiftMuon',
                                     'nShiftDimuonVertex', 'ShiftDimuonVertex_topologyMin',
                                     'ShiftDimuonVertex_topologyMax'):
                            if not tree.GetBranch(name):
                                raise ValueError(f'Missing branch {name}')
                            tree.SetBranchStatus(name, 1)
                        chunk_counts = Counter()
                        for event in tree:
                            identity = (int(event.run), int(event.luminosityBlock), int(event.event))
                            ids.append(identity)
                            if identity in identities:
                                raise ValueError(f'Duplicate identity: {identity}')
                            identities.add(identity)
                            vertices = int(event.nShiftDimuonVertex)
                            both = sum(int(event.ShiftDimuonVertex_topologyMin[i]) == 2 and
                                       int(event.ShiftDimuonVertex_topologyMax[i]) == 2 for i in range(vertices))
                            chunk_counts.update(events=1, events_muon=int(event.nShiftMuon)>0,
                                events_vertex=vertices>0, events_both_both=both>0,
                                muons=int(event.nShiftMuon), vertices=vertices, both_both_vertices=both)
                        counts.update(chunk_counts)
                        row['counts'] = dict(chunk_counts)
                        root.Close()
                    if len(set(ids)) != metadata['events'] or list(min(ids)) != metadata['identity_min'] or list(max(ids)) != metadata['identity_max']:
                        raise ValueError(f'Wrong event identities: {source}')
                    if reference is not None and ids != reference:
                        raise ValueError(f'Stage identities differ: {source}')
                    reference = ids
                    row['stages'][str(stage+1)] = dict(bytes=source.stat().st_size, events=len(ids))
                rows.append(row)
                if len(rows) % 25 == 0:
                    print(f'Validated {len(rows)} chunks, {counts["events"]} events', flush=True)
        result['status'] = 'validated'
    except Exception as error:
        result['status'] = 'failed'
        result['error'] = repr(error)
        raise
    finally:
        args.output.write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k != 'rows'}))


if __name__ == '__main__':
    main()
