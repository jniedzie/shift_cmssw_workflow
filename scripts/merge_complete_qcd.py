#!/usr/bin/env python3
"""Non-destructively replace a stale QCD aggregate after exact per-chunk checks."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from merge_sampling_nano import inspect


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('campaign', type=Path)
    p.add_argument('--chunks', type=int, required=True)
    p.add_argument('--expected-events', type=int, required=True)
    p.add_argument('--normalization', type=Path, required=True)
    p.add_argument('--output-report', type=Path, required=True)
    args = p.parse_args()
    output = args.campaign/'samples/step4_merged'/f'ntuple_complete_{args.expected_events}events_20260921.root'
    if output.exists() or output.with_suffix('.json').exists():
        raise ValueError('Refuse to overwrite an existing aggregate')
    result = dict(status='running', output=str(output), inputs=[], reconstructed_mass_read=False,
                  physics_valid=False, normalization_ready=False,
                  normalization_source=str(args.normalization.resolve()))
    try:
        with tempfile.TemporaryDirectory(prefix='shift_qcd_complete_merge_') as temporary:
            temporary = Path(temporary)
            expected, schema, attempts, staged = {}, None, 0, []
            for chunk in range(args.chunks):
                part = f'{chunk:04d}'
                metadata_path = args.campaign/'generation_metadata'/f'part{part}.json'
                metadata = json.loads(metadata_path.read_text())
                source = args.campaign/'samples/step4'/f'events_NanoAOD_part_{part}.root'
                local = temporary/f'input_{part}.root'
                shutil.copy2(source, local)
                rows, current_schema = inspect(local)
                if (len(rows) != metadata['events'] or list(min(rows)) != metadata['identity_min']
                        or list(max(rows)) != metadata['identity_max'] or expected.keys() & rows.keys()):
                    raise ValueError(f'Input identity mismatch: {source}')
                if schema is not None and schema != current_schema:
                    raise ValueError('Mixed schema in recovered production')
                schema = current_schema
                attempts += metadata['attempted_events']
                expected.update(rows)
                staged.append(str(local))
                result['inputs'].append(dict(path=str(source), events=len(rows),
                    sha256=hashlib.sha256(local.read_bytes()).hexdigest(),
                    metadata_sha256=hashlib.sha256(metadata_path.read_bytes()).hexdigest()))
                if (chunk+1) % 100 == 0:
                    print('Validated',chunk+1,'chunks',len(expected),'events',flush=True)
            if len(expected) != args.expected_events:
                raise ValueError('Wrong complete production event count')
            with (temporary/'hadd.log').open('w') as log:
                subprocess.run(['hadd', '-fk', '-j', '2', str(temporary/'merged.root'), *staged],
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            actual, current_schema = inspect(temporary/'merged.root')
            if actual != expected or current_schema != schema:
                raise ValueError('Merged contents differ from complete chunk set')
            counts = Counter()
            for row in actual.values():
                counts.update(events=1, events_muon=row['muons'] > 0,
                              events_vertex=row['vertices'] > 0, events_both_both=row['both_both'] > 0)
            result.update(status='validated', counts=dict(counts), attempted_events=attempts,
                          schema_sha256=schema,
                          normalization=json.loads(args.normalization.read_text()),
                          sha256=hashlib.sha256((temporary/'merged.root').read_bytes()).hexdigest())
            partial = output.with_suffix('.root.partial')
            with partial.open('xb') as target, (temporary/'merged.root').open('rb') as source:
                shutil.copyfileobj(source, target)
            if hashlib.sha256(partial.read_bytes()).hexdigest() != result['sha256']:
                raise ValueError('Published bytes differ')
            partial.rename(output)
            output.with_suffix('.json').write_text(json.dumps(result, indent=2)+'\n')
            print('Published',str(output),dict(counts),flush=True)
    except Exception as error:
        result['status']='failed'
        result['error']=repr(error)
        raise
    finally:
        args.output_report.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
