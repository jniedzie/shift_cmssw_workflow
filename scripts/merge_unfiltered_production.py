#!/usr/bin/env python3
"""Merge complete checkpointed unfiltered samples without reading mass branches."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from collect_generation_metadata import combine
from run_retiring_chain import sha, validate_provenance


def inspect_nano(path):
    import ROOT
    f = ROOT.TFile.Open(str(path))
    if not f or f.IsZombie() or f.TestBit(ROOT.TFile.kRecovered):
        raise ValueError(f'Unhealthy ROOT: {path}')
    try:
        tree = f.Get('Events')
        if not tree or tree.GetEntries() <= 0:
            raise ValueError('Missing or empty Events tree')
        schema = sorted((b.GetName(), b.GetTitle(), b.GetClassName()) for b in tree.GetListOfBranches())
        schema = hashlib.sha256(json.dumps(schema).encode()).hexdigest()
        tree.SetBranchStatus('*', 0)
        names = ('run','luminosityBlock','event','genWeight','nShiftMuon','nShiftDimuonVertex',
                 'ShiftDimuonVertex_topologyMin','ShiftDimuonVertex_topologyMax')
        for name in names:
            if not tree.GetBranch(name):
                raise ValueError(f'Missing branch {name}')
            tree.SetBranchStatus(name, 1)
        rows = {}
        for event in tree:
            identity = (int(event.run), int(event.luminosityBlock), int(event.event))
            if identity in rows or float(event.genWeight) != 1:
                raise ValueError('Duplicate identity or non-unit unfiltered weight')
            muons, vertices = int(event.nShiftMuon), int(event.nShiftDimuonVertex)
            if muons < 0 or vertices < 0:
                raise ValueError('Negative collection count')
            both = sum(int(event.ShiftDimuonVertex_topologyMin[i]) == 2 and
                       int(event.ShiftDimuonVertex_topologyMax[i]) == 2 for i in range(vertices))
            rows[identity] = (muons, vertices, both)
        if len(rows) != tree.GetEntries():
            raise ValueError('Unreadable Events entries')
        return rows, schema
    finally:
        f.Close()


def validate_rows(rows, metadata, seen):
    ids = [list(i) for i in rows]
    if (ids != metadata['event_ids'] or len(rows) != metadata['events']
            or metadata['events'] != metadata['attempted_events']
            or metadata['generated_filter_efficiency'] != 1
            or rows.keys() & seen.keys()):
        raise ValueError('Chunk IDs/counts/filter or disjoint-union mismatch')


def publish(source, destination):
    if destination.exists():
        raise ValueError(f'Refusing existing destination: {destination}')
    partial = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.partial')
    with source.open('rb') as src, partial.open('xb') as dst:
        shutil.copyfileobj(src, dst)
    if sha(partial) != sha(source):
        raise ValueError('Published checksum mismatch')
    if destination.exists():
        raise ValueError('Destination appeared during publication')
    partial.rename(destination)


def merge(campaign, chunks, report_dir, date):
    expected_chunks = set(range(chunks))
    sources = {int(p.stem.removeprefix('events_NanoAOD_part_')): p
               for p in (campaign/'samples/step4').glob('events_NanoAOD_part_*.root')}
    if set(sources) != expected_chunks:
        raise ValueError('Require the full expected chunk set; no silent partial merge')
    directory = campaign/'samples/step4_merged'
    if list(directory.glob('*.root')):
        raise ValueError('Merge directory already has ROOT files; refusing ambiguity')
    for name in ('normalization_complete.json', 'cross_sections_complete.txt'):
        if (campaign/name).exists():
            raise ValueError(f'Existing normalization export: {name}')
    required = 3 * sum(p.stat().st_size for p in sources.values()) + 1024**3
    if shutil.disk_usage(tempfile.gettempdir()).free < required:
        raise ValueError('Insufficient local merge scratch')
    report = dict(campaign=str(campaign), expected_chunks=chunks, complete=True,
                  missing_chunks=[], reconstructed_mass_read=False, physics_valid=False,
                  normalization_ready=False, inputs=[])
    with tempfile.TemporaryDirectory(prefix='shift_unfiltered_merge_') as temporary:
        temporary = Path(temporary)
        expected, schema, metadata, staged = {}, None, [], []
        for chunk in range(chunks):
            source = sources[chunk]
            checkpoint = campaign/'chain_metadata'/f'part{chunk:04d}'
            if (checkpoint/'active.lock').exists():
                raise ValueError(f'Active chunk lock: {chunk}')
            state_path = checkpoint/'state.json'
            state = json.loads(state_path.read_text())
            if set(state['stages']) != {'1','2','3','4'}:
                raise ValueError('Missing successful stage checkpoint')
            validate_provenance(state)
            genpath = campaign/'generation_metadata'/f'part{chunk:04d}.json'
            if sha(genpath) != state['generation_metadata_sha256']:
                raise ValueError('Generation metadata hash mismatch')
            gen = json.loads(genpath.read_text())
            if gen['chunk'] != chunk or gen['process'] != state['contract']['PROCESS']:
                raise ValueError('Chunk/process mismatch')
            local = temporary/f'input_{chunk:04d}.root'
            shutil.copy2(source, local)
            checksum = sha(local)
            saved = state['stages']['4']['output']
            if (checksum != saved['sha256'] or str(source) != saved['path']
                    or local.stat().st_size != saved['bytes']):
                raise ValueError('Output differs from checkpoint')
            rows, current_schema = inspect_nano(local)
            validate_rows(rows, gen, expected)
            if saved['event_ids'] != gen['event_ids'] or saved['events'] != len(rows):
                raise ValueError('Checkpoint event identities differ')
            if schema is not None and schema != current_schema:
                raise ValueError('Mixed Nano schemas')
            schema = current_schema
            for stage in (1,2,3):
                receipt = json.loads((checkpoint/f'retired_step{stage}.json').read_text())
                if receipt['status'] != 'deleted':
                    raise ValueError('Incomplete intermediate retirement')
            expected.update(rows)
            metadata.append(gen)
            staged.append(str(local))
            report['inputs'].append(dict(chunk=chunk, path=str(source), sha256=checksum,
                events=len(rows), metadata_sha256=sha(genpath), checkpoint_sha256=sha(state_path)))
            if (chunk+1) % 40 == 0:
                print(campaign.name, 'validated', chunk+1, 'chunks', len(expected), 'events', flush=True)
        norm = combine(metadata, chunks)
        if norm['events'] != len(expected):
            raise ValueError('Normalization denominator differs from merged event union')
        norm.update(complete=True, included_chunks=sorted(sources), missing_chunks=[],
                    recommended_uniform_event_weight_pb=norm['cross_section_pb']/norm['events'],
                    unfiltered_unit_genWeight=True)
        output = directory/f'ntuple_complete_{len(expected)}events_{date}.root'
        local_output = temporary/output.name
        logpath = report_dir/f'{campaign.name}_hadd.log'
        with logpath.open('x') as log:
            subprocess.run(['hadd','-fk','-j','2',str(local_output),*staged],
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        actual, current_schema = inspect_nano(local_output)
        if actual != expected or current_schema != schema:
            raise ValueError('Merged content/schema differs from exact input union')
        totals = Counter()
        for muons, vertices, both in actual.values():
            totals.update(events=1, muons=muons, vertices=vertices,
                          events_muon=muons>0, events_vertex=vertices>0, events_both_both=both>0)
        directory.mkdir(parents=True, exist_ok=True)
        publish(local_output, output)
        published, published_schema = inspect_nano(output)
        if published != expected or published_schema != schema or sha(output) != sha(local_output):
            raise ValueError('Reopened published output differs')
        with (temporary/'normalization_complete.json').open('x') as f:
            json.dump(norm, f, indent=2)
        sigma, error = norm['cross_section_pb'], norm['inclusive_cross_section_error_pb']
        with (temporary/'cross_sections_complete.txt').open('x') as f:
            f.write('# Combined complete unfiltered sample; use actual generated event count\n')
            f.write(f'{norm["process"]} before_filter={sigma:.12g} +- {error:.12g} pb '
                    f'after_filter={sigma:.12g} +- {error:.12g} pb\n')
        for name in ('normalization_complete.json','cross_sections_complete.txt'):
            publish(temporary/name, campaign/name)
        report.update(status='validated', output=str(output), events=len(actual),
                      counts=dict(totals), schema_sha256=schema, sha256=sha(local_output),
                      normalization=norm, output_bytes=output.stat().st_size)
        with (report_dir/f'{campaign.name}.json').open('x') as f:
            json.dump(report, f, indent=2)
        publish(report_dir/f'{campaign.name}.json', output.with_suffix('.json'))
        print('READY', campaign.name, len(actual), 'events;', sigma, 'pb', flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('campaign', type=Path)
    p.add_argument('--chunks', type=int, required=True)
    p.add_argument('--report-directory', type=Path, required=True)
    p.add_argument('--date', required=True)
    args = p.parse_args()
    if not args.campaign.is_absolute() or args.chunks <= 0 or not (len(args.date)==8 and args.date.isdigit()):
        raise ValueError('Invalid campaign/count/date')
    args.report_directory.mkdir(parents=True, exist_ok=True)
    merge(args.campaign, args.chunks, args.report_directory, args.date)

if __name__ == '__main__':
    main()
