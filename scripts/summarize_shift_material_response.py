#!/usr/bin/env python3
# Code style: AI-code. AI-generated strict material-response completion check.
"""Close declared response cohorts and compare the frozen candidate selection."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

from shift_material_response import (bootstrap_comparison, shape_comparison, response_reporting_scope,
                                     source_exposures, source_rate_weight, source_origin_sha256)


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
        source = plan['sources'][sample['source_id']]
        if (response['source_GEN_sha256'] != source_origin_sha256(source)
                or response.get('prepared_input_GEN_sha256', response['source_GEN_sha256']) != source['gen_sha256']):
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
        exposure, denominator_kinds = source_exposures(plan['sources'].values())
        for row in rows:
            source = plan['sources'][row['source_id']]
            row['rate_weight'] = source_rate_weight(source, row['native_weight'], exposure[source['stratum']])
        out = dict(schema='shift-material-response-comparison-v1', complete=True,
                   conditional_saved_GEN_study=True,
                   full_source_trials_by_bin={key: count for key, count in exposure.items()
                                              if denominator_kinds[key] == 'fixed_source_trials'},
                   native_source_exposure_by_bin=exposure, denominator_kind_by_bin=denominator_kinds,
                   all_bins=bootstrap_comparison(rows), bins={}, publications=publications,
                   shapes=shape_comparison(rows, plan['policy']),
                   **response_reporting_scope(plan),
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
