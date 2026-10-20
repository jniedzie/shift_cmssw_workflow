#!/usr/bin/env python3
# Code style: AI-code. AI-generated strict material-response completion check.
"""Close declared response cohorts and compare the frozen candidate selection."""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

from shift_material_response import bootstrap_comparison, shape_comparison


def summarize(directory, canary=False):
    directory = Path(directory).resolve()
    plan = json.loads((directory/'plan.json').read_text())
    samples = [x for x in plan['samples'] if not canary or x['canary']]
    rows, publications = [], []
    for sample in samples:
        job = directory/'jobs'/sample['id']
        result = json.loads((job/'result.json').read_text())
        status = json.loads((job/'status.json').read_text())
        if not result['complete'] or not status['complete'] or result['exit_code'] != 0:
            raise ValueError('Incomplete response job: '+sample['id'])
        expected = hashlib.sha256((job/'request.json').read_bytes()).hexdigest()
        if result['request_sha256'] != expected or result['events'] != len(sample['indices']):
            raise ValueError('Response request/count mismatch')
        import tarfile
        with tarfile.open(job/'return_payload.tar.gz', 'r:gz') as archive:
            response = json.load(archive.extractfile('response.json'))
        if [r['event_id'] for r in response['events']] != sample['event_ids']:
            raise ValueError('Returned original identities differ')
        if response['source_GEN_sha256'] != plan['sources'][sample['source_id']]['gen_sha256']:
            raise ValueError('Source hash mismatch')
        if not response['saved_decays_unchanged'] or not response['no_second_filter_efficiency']:
            raise ValueError('Unsupported source/decay normalization')
        if response['candidate_gate_applied_to_detector_sample']:
            raise ValueError('Benchmark must not apply the candidate detector gate')
        rows.extend(response['events'])
        publications.extend(result['published'])
    if canary:
        out = dict(schema='shift-material-response-canary-v1', complete=True,
                   originals=len(rows), publications=publications,
                   downstream_completion_check_executed=True,
                   physics_improvement_proven=False)
    else:
        # Full source exposure belongs once to the union of all chunks, not to
        # every Nano file or to each independent source's already scaled rate.
        exposure = collections.defaultdict(int)
        for source in plan['sources'].values():
            exposure[source['stratum']] += source['full_source_trials']
        for row in rows:
            source = plan['sources'][row['source_id']]
            row['rate_weight'] = row['native_weight']*source['sigma_nd_pb']/exposure[source['stratum']]
        out = dict(schema='shift-material-response-comparison-v1', complete=True,
                   conditional_saved_GEN_study=True, full_source_trials_by_bin=dict(exposure),
                   all_bins=bootstrap_comparison(rows), bins={}, publications=publications,
                   shapes=shape_comparison(rows, plan['policy']),
                   source_population_scope='Saved finite-cylinder mu-enriched GEN; no lifetime redraws.',
                   physics_normalization_ready=False, inclusive_transport_complete=False)
        for stratum in sorted(exposure):
            out['bins'][stratum] = bootstrap_comparison([r for r in rows if r['stratum'] == stratum])
    name = 'canary_summary.json' if canary else 'summary.json'
    (directory/name).write_text(json.dumps(out, indent=2, allow_nan=False)+'\n')
    if not canary and plan.get('final_pdf'):
        subprocess.run([sys.executable, str(Path(__file__).with_name('plot_shift_material_response.py')),
                        '--summary', str(directory/name), '--output', plan['final_pdf']], check=True)
        output = Path(plan['final_pdf'])
        (directory/'plot_receipt.json').write_text(json.dumps(dict(path=str(output), bytes=output.stat().st_size,
            sha256=hashlib.sha256(output.read_bytes()).hexdigest()), indent=2)+'\n')
    print(json.dumps({k:v for k,v in out.items() if k not in ('publications', 'bins')}, allow_nan=False))
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--canary', action='store_true')
    args = parser.parse_args()
    summarize(args.directory, args.canary)
