#!/usr/bin/env python3
"""Freeze the existing GEN runtime and workflow without rebuilding or LSS data."""
import argparse
import json
import os
from pathlib import Path
import tarfile

from generation_publication import sha256

REPO = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {'.git', 'data', 'condor', '__pycache__', 'objs', 'tmp', 'plots', 'logs', '.pytest_cache'}
EXCLUDE_SUFFIXES = {'.gdml', '.inp', '.bnn', '.map', '.dat', '.root', '.log', '.f', '.F', '.tar', '.gz'}


def add_tree(archive, source, target):
    """Preserve CVMFS links; dereference custom AFS file links into the archive."""
    source = Path(source)
    if source.name in EXCLUDE_DIRS or source.suffix in EXCLUDE_SUFFIXES:
        return
    if source.is_symlink():
        resolved = source.resolve(strict=True)
        if str(resolved).startswith('/cvmfs/'):
            archive.add(str(source), arcname=target, recursive=False)
            return
        if resolved.is_dir():
            for child in sorted(resolved.iterdir()):
                add_tree(archive, child, target+'/'+child.name)
            return
        source = resolved
    if source.is_dir():
        archive.add(str(source), arcname=target, recursive=False)
        for child in sorted(source.iterdir()):
            add_tree(archive, child, target+'/'+child.name)
    elif source.is_file():
        archive.add(str(source), arcname=target, recursive=False)


def freeze(cmssw, output):
    cmssw = Path(cmssw).resolve()
    if cmssw.name != 'CMSSW_17_0_0_pre4':
        raise ValueError('This bootstrap is validated only for CMSSW_17_0_0_pre4')
    if Path(output).exists():
        raise ValueError('Refusing an existing runtime archive')
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'w:gz', format=tarfile.PAX_FORMAT) as archive:
        for name in ('.SCRAM', 'config', 'lib', 'python', 'cfipython', 'external', 'bin'):
            add_tree(archive, cmssw/name, cmssw.name+'/'+name)
        # Generated CMSSW Python package initializers point here. Include only
        # Python modules, never experiment data, decks, fields or geometry.
        for package in sorted((cmssw/'src').glob('*/*/python')):
            add_tree(archive, package, cmssw.name+'/src/'+str(package.relative_to(cmssw/'src')))
        for name in ('scripts', 'config', 'fragments'):
            add_tree(archive, REPO/name, 'workflow/'+name)
        add_tree(archive, REPO/'Configuration', 'workflow/Configuration')
        for script in REPO.glob('run_step*.sh'):
            add_tree(archive, script, 'workflow/'+script.name)
    with tarfile.open(output) as archive:
        for member in archive:
            if (member.name.startswith('/') or '..' in Path(member.name).parts or
                    (member.issym() and member.linkname.startswith('/afs/')) or
                    Path(member.name).suffix in EXCLUDE_SUFFIXES or 'data' in Path(member.name).parts):
                raise ValueError('Unsafe/private runtime member: '+member.name)
    return dict(archive=str(Path(output).resolve()), sha256=sha256(output),
                bytes=Path(output).stat().st_size, detector_payloads_included=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cmssw', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    print(json.dumps(freeze(args.cmssw, args.output), indent=2))


if __name__ == '__main__':
    main()
