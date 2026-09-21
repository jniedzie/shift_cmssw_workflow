#!/usr/bin/env python3
"""Independently compare replay topology counts with Nano and compact bookkeeping."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile

from validate_sampling_ledger import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('bookkeeping', type=Path)
    parser.add_argument('--base', type=Path, default=Path('/eos/home-j/jniedzie/shift_cmssw'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import ROOT
    records = [json.loads(line) for line in args.bookkeeping.read_text().splitlines()]
    indexed = {(row['campaign'], tuple(row['id'])): row for row in records}
    if len(indexed) != len(records):
        raise ValueError('Duplicate bookkeeping identity')
    result = dict(status='running', groups=[], reconstructed_mass_read=False)
    expected_keys = set()
    try:
        with tempfile.TemporaryDirectory(prefix='shift_replay_count_audit_') as temporary:
            local = Path(temporary) / 'nano.root'
            for entry in json.loads(args.manifest.read_text()):
                ledger = json.loads(Path(entry['ledger']).read_text())
                validate(ledger)
                expected = {tuple(row['id']): row for row in ledger['rows']}
                expected.update({tuple(i): None for i in ledger['upstream_not_saved_ids']})
                processed, counts, weighted, states = {}, Counter(), Counter(), Counter()
                for chunk in range(len(ledger['chunks'])):
                    campaign = entry.get('chunk_campaigns', {}).get(str(chunk), entry['campaign'])
                    root = args.base / 'qcd' / campaign
                    report = json.loads((root/'sampling_replay'/f'part{chunk:04d}'/'report.json').read_text())
                    if report['status'] != 'validated' or report['reconstructed_mass_read']:
                        raise ValueError('Require validated mass-free report')
                    shutil.copy2(root/'samples'/'step4'/f'events_NanoAOD_part_{chunk:04d}.root', local)
                    file = ROOT.TFile.Open(str(local))
                    if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
                        raise ValueError('Invalid ROOT output')
                    tree = file.Get('Events')
                    if not tree or tree.GetEntries() != len(report['rows']):
                        raise ValueError('Nano count mismatch')
                    tree.SetBranchStatus('*', 0)
                    for branch in ('run', 'luminosityBlock', 'event', 'nShiftMuon',
                                   'nShiftDimuonVertex', 'ShiftDimuonVertex_topologyMin',
                                   'ShiftDimuonVertex_topologyMax'):
                        if not tree.GetBranch(branch):
                            raise ValueError(f'Missing branch: {branch}')
                        tree.SetBranchStatus(branch, 1)
                    for event, reference in zip(tree, report['rows']):
                        identity = (int(event.run), int(event.luminosityBlock), int(event.event))
                        n = int(event.nShiftDimuonVertex)
                        reco = dict(muons=int(event.nShiftMuon), vertices=n,
                                    both_both=sum(int(event.ShiftDimuonVertex_topologyMin[i]) == 2
                                                  and int(event.ShiftDimuonVertex_topologyMax[i]) == 2
                                                  for i in range(n)))
                        if list(identity) != reference['id'] or reco != reference['reco']:
                            raise ValueError('Independent Nano/report mismatch')
                        if identity in processed or not expected[identity]['selected']:
                            raise ValueError('Repeated or unselected Nano identity')
                        processed[identity] = reco
                        weight = expected[identity]['inverse_probability']
                        flags = dict(events=1, events_muon=reco['muons'] > 0,
                                     events_vertex=n > 0, events_both_both=reco['both_both'] > 0)
                        for name, flag in flags.items():
                            counts[name] += int(flag)
                            weighted[name] += weight * flag
                    file.Close()
                for identity, choice in expected.items():
                    key = (entry['campaign'], identity)
                    expected_keys.add(key)
                    row = indexed[key]
                    state = ('upstream_not_saved' if choice is None else
                             'processed' if choice['selected'] else 'not_simulated')
                    weight = choice['inverse_probability'] if choice and choice['selected'] else 0.
                    probability = choice['probability'] if choice else None
                    if (row['state'] != state or row['reco'] != processed.get(identity)
                            or row['sampling_probability'] != probability
                            or row['sampling_weight_for_sum'] != weight
                            or row['physics_valid'] or row['normalization_ready']):
                        raise ValueError('Bookkeeping mismatch')
                    if state == 'processed' and identity not in processed:
                        raise ValueError('Missing selected identity')
                    states[state] += 1
                result['groups'].append(dict(campaign=entry['campaign'], counts=dict(counts),
                                             weighted=dict(weighted), states=dict(states)))
                print(entry['campaign'], dict(counts), dict(states), flush=True)
        if set(indexed) != expected_keys:
            raise ValueError('Extraneous bookkeeping identities')
        result['status'] = 'validated'
    except Exception as error:
        result['status'] = 'failed'
        result['error'] = repr(error)
        raise
    finally:
        args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
