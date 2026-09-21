#!/usr/bin/env python3
"""Read-only health/count checks for merged files; never read mass branches."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base', type=Path, default=Path('/eos/home-j/jniedzie/shift_cmssw'))
    args = parser.parse_args()
    import ROOT
    rows = []
    result = dict(status='running', rows=rows, reconstructed_mass_read=False,
                  scope='Merged file health and mass-free counts only; does not prove complete source coverage.')
    try:
        with tempfile.TemporaryDirectory(prefix='shift_merge_retention_audit_') as temporary:
            local = Path(temporary)/'merged.root'
            files = sorted(args.base.glob('*/lssPaired_*/samples/step4_merged/*.root'))
            files += sorted(args.base.glob('qcd/QCD_*/samples/step4_merged/*.root'))
            files += sorted(args.base.glob('jpsi/Charmonium_*_2023*/samples/step4_merged/*.root'))
            for source in files:
                shutil.copy2(source, local)
                file = ROOT.TFile.Open(str(local))
                if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
                    raise ValueError(f'Invalid merged file: {source}')
                tree = file.Get('Events')
                if not tree:
                    raise ValueError(f'Missing Events: {source}')
                schema = sorted((b.GetName(), b.GetTitle(), b.GetClassName()) for b in tree.GetListOfBranches())
                branches = {b.GetName() for b in tree.GetListOfBranches()}
                allowed = ['run', 'luminosityBlock', 'event', 'nShiftMuon', 'nShiftDimuonVertex']
                enabled = [b for b in allowed if b in branches]
                tree.SetBranchStatus('*', 0)
                for branch in enabled:
                    tree.SetBranchStatus(branch, 1)
                totals = {b: 0 for b in ('nShiftMuon', 'nShiftDimuonVertex') if b in enabled}
                identities = set()
                for event in tree:
                    for branch in totals:
                        totals[branch] += int(getattr(event, branch))
                    if all(b in enabled for b in ('run', 'luminosityBlock', 'event')):
                        identities.add((int(event.run), int(event.luminosityBlock), int(event.event)))
                row = dict(path=str(source), events=int(tree.GetEntries()), bytes=source.stat().st_size,
                    columns=len(schema), schema_sha256=hashlib.sha256(json.dumps(schema).encode()).hexdigest(),
                    unique_run_lumi_event=len(identities), totals=totals, enabled_branches=enabled,
                    root_health='readable_not_recovered')
                file.Close()
                rows.append(row)
                print(source.parent.parent.parent.name, source.name, row['events'], totals, flush=True)
        result['status'] = 'validated_merged_files_only'
    except Exception as error:
        result['status'] = 'failed'
        result['error'] = repr(error)
        raise
    finally:
        args.output.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
