#!/usr/bin/env python3
"""Archive provenance for named superseded probes. Never delete their outputs."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    base = Path('/eos/home-j/jniedzie/shift_cmssw')
    names = [
        'jpsi/SamplingScan_jpsi_pThat_1to2_localprobe_20260921_v1',
        'qcd/SamplingScan_qcdmu_pThat_1to2_localprobe_20260921_v1',
        'jpsi/SamplingScan_jpsi_pThat_1to2_smoke_20260921_v1',
        'qcd/SamplingScan_qcdmu_pThat_1to2_smoke_20260921_v1',
        'qcd/WeightedReplay_qcdmu_pThat_1to2_smoke_20260921_v1',
        'jpsi/Charmonium_FixedTarget_pThat_1to5GeV_13p6TeV_sameSimHitDelayScan_condorSmoke_2023_v1',
        'jpsi/lssReco_control_targetCov_2023_v3',
    ]
    output = args.output.resolve()
    archive = output.with_suffix('.tar.gz')
    if archive.exists() or output.exists():
        raise ValueError('Refuse existing archive/manifest')
    campaigns, checksums = [], {}
    with tarfile.open(archive, 'x:gz') as tar:
        for name in names:
            campaign = base/name
            if not campaign.is_dir():
                raise ValueError(f'Missing candidate: {campaign}')
            row = dict(path=str(campaign), files=0, bytes=0, root_files=0, root_bytes=0,
                       archived_metadata_files=0, archived_metadata_bytes=0)
            for path in sorted(campaign.rglob('*')):
                if path.is_symlink():
                    raise ValueError('Unexpected symlink in retirement candidate')
                if not path.is_file():
                    continue
                size = path.stat().st_size
                row['files'] += 1
                row['bytes'] += size
                if path.suffix == '.root':
                    row['root_files'] += 1
                    row['root_bytes'] += size
                    continue
                relative = str(path.relative_to(base))
                checksums[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
                tar.add(path, arcname=relative, recursive=False)
                row['archived_metadata_files'] += 1
                row['archived_metadata_bytes'] += size
            campaigns.append(row)
    with tarfile.open(archive, 'r:gz') as tar:
        for relative, expected in checksums.items():
            if hashlib.sha256(tar.extractfile(relative).read()).hexdigest() != expected:
                raise ValueError('Archive checksum mismatch')
    result = dict(status='metadata_archive_validated', archive=str(archive),
        archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), campaigns=campaigns,
        metadata_sha256=checksums, deleted_files=0, root_payloads_archived=False,
        warning='Deletion candidates only. ROOT payloads are not backed up; deleting them discards these obsolete probe realizations. Keep this provenance archive.')
    output.write_text(json.dumps(result, indent=2)+'\n')
    print('Archived', len(checksums), 'metadata files from', len(campaigns), 'superseded probes;',
          sum(row['bytes'] for row in campaigns), 'candidate bytes. Nothing deleted.')


if __name__ == '__main__':
    main()
