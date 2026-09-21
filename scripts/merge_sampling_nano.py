#!/usr/bin/env python3
"""Publish complete per-bin Nano merges, validating only mass-free observables."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def inspect(path):
    import ROOT
    file = ROOT.TFile.Open(str(path))
    if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
        raise ValueError(f'Invalid ROOT: {path}')
    tree = file.Get('Events')
    if not tree:
        raise ValueError('Missing Events tree')
    schema = sorted((b.GetName(), b.GetTitle(), b.GetClassName()) for b in tree.GetListOfBranches())
    schema_hash = hashlib.sha256(json.dumps(schema).encode()).hexdigest()
    tree.SetBranchStatus('*', 0)
    for name in ('run', 'luminosityBlock', 'event', 'nShiftMuon', 'nShiftDimuonVertex',
                 'ShiftDimuonVertex_topologyMin', 'ShiftDimuonVertex_topologyMax'):
        if not tree.GetBranch(name):
            raise ValueError(f'Missing branch {name}')
        tree.SetBranchStatus(name, 1)
    rows = {}
    for event in tree:
        identity = (int(event.run), int(event.luminosityBlock), int(event.event))
        if identity in rows:
            raise ValueError('Duplicate event identity')
        vertices = int(event.nShiftDimuonVertex)
        both = sum(int(event.ShiftDimuonVertex_topologyMin[i]) == 2 and
                   int(event.ShiftDimuonVertex_topologyMax[i]) == 2 for i in range(vertices))
        rows[identity] = dict(muons=int(event.nShiftMuon), vertices=vertices, both_both=both)
    file.Close()
    return rows, schema_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary', type=Path)
    parser.add_argument('--sample', choices=('jpsi', 'qcdmu'), default='jpsi')
    parser.add_argument('--campaign-tag', default='scan')
    parser.add_argument('--base', type=Path, default=Path('/eos/home-j/jniedzie/shift_cmssw'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text())
    results = dict(status='validated_complete_groups', groups=[], skipped_incomplete=[],
                   reconstructed_mass_read=False, physics_valid=False, normalization_ready=False)
    for key, group in summary['groups'].items():
        sample, mode, bounds = key.split('/')
        if sample != args.sample or mode != 'full':
            continue
        if group['missing'] or group['failed'] or group['validated_chunks'] != group['expected_chunks']:
            results['skipped_incomplete'].append(key)
            continue
        lo, hi = bounds.split(':')
        campaign = args.base/('jpsi' if sample == 'jpsi' else 'qcd')/f'SamplingScan_{sample}_pThat_{lo}to{hi}_{args.campaign_tag}_20260921_v1'
        directory = campaign/'samples/step4_merged'
        output = directory/f'ntuple_sampling_complete_{group["accepted_events"]}events.root'
        metadata = output.with_suffix('.json')
        expected = {}
        inputs = []
        with tempfile.TemporaryDirectory(prefix='shift_sampling_merge_') as temporary:
            temporary = Path(temporary)
            schema = None
            staged = []
            for index, path in enumerate(group['reports']):
                path = Path(path)
                report = json.loads(path.read_text())
                if report['status'] != 'validated' or report['reconstructed_mass_read']:
                    raise ValueError('Require a validated mass-free source report')
                part = path.parent.name.removeprefix('part')
                source = path.parents[3]/'samples/step4'/f'events_NanoAOD_part_{part}.root'
                local = temporary/f'input_{index:04d}.root'
                shutil.copy2(source, local)
                actual, current_schema = inspect(local)
                reference = {tuple(row['id']): row['reco'] for row in report['generation']['rows']}
                if actual != reference or expected.keys() & actual.keys():
                    raise ValueError('Input identities or topology counts differ')
                if schema is not None and current_schema != schema:
                    raise ValueError('Input schemas differ')
                schema = current_schema
                expected.update(actual)
                inputs.append(dict(path=str(source), bytes=source.stat().st_size,
                                   sha256=hashlib.sha256(local.read_bytes()).hexdigest(), report=str(path)))
                staged.append(str(local))
            if len(expected) != group['accepted_events']:
                raise ValueError('Merged denominator mismatch')
            if output.exists() or metadata.exists():
                if not output.exists() or not metadata.exists():
                    raise ValueError('Incomplete existing merge; do not overwrite automatically')
                prior = json.loads(metadata.read_text())
                if prior['inputs'] != inputs:
                    raise ValueError('Existing merge input provenance differs')
                shutil.copy2(output, temporary/'merged.root')
            else:
                with (temporary/'hadd.log').open('w') as log:
                    subprocess.run(['hadd', '-fk', '-j', '2', str(temporary/'merged.root'), *staged],
                                   stdout=log, stderr=subprocess.STDOUT, check=True)
            actual, current_schema = inspect(temporary/'merged.root')
            if actual != expected or current_schema != schema:
                raise ValueError('Merged identity, topology or schema mismatch')
            counts = Counter()
            for reco in actual.values():
                counts.update(events=1, events_muon=reco['muons'] > 0,
                              events_vertex=reco['vertices'] > 0, events_both_both=reco['both_both'] > 0)
            info = dict(status='validated', bin=key, output=str(output), inputs=inputs,
                events=len(actual), counts=dict(counts), schema_sha256=schema,
                sha256=hashlib.sha256((temporary/'merged.root').read_bytes()).hexdigest(),
                framework_attempts=group['framework_attempts'], generator_attempts=group['generator_attempts'],
                internal_cross_sections_pb=group['internal_cross_sections_pb'],
                physics_valid=False, normalization_ready=False, reconstructed_mass_read=False,
                warning='ATLAS proxy, no pileup/trigger. Born pThat bins, not final J/psi pT. Forced-decay and final normalization conventions remain provisional.')
            if not output.exists():
                directory.mkdir(parents=True, exist_ok=True)
                partial = output.with_suffix('.root.partial')
                with partial.open('xb') as destination, (temporary/'merged.root').open('rb') as source:
                    shutil.copyfileobj(source, destination)
                if hashlib.sha256(partial.read_bytes()).hexdigest() != info['sha256']:
                    raise ValueError('Published bytes differ')
                partial.rename(output)
                metadata.write_text(json.dumps(info, indent=2)+'\n')
            results['groups'].append(info)
            print(key, dict(counts), str(output), flush=True)
    if not results['groups']:
        raise ValueError('No complete groups to merge')
    args.output.write_text(json.dumps(results, indent=2)+'\n')


if __name__ == '__main__':
    main()
