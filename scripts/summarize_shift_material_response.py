#!/usr/bin/env python3
# Code style: AI-code. AI-generated strict material-response completion check.
"""Close declared response cohorts and compare the frozen candidate selection."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

from shift_material_response import (bootstrap_comparison, shape_comparison, response_reporting_scope,
                                     source_exposures, source_rate_weight, source_origin_sha256)


def collect_paired(directory, fetch=None):
    """Collect completed pairs using their frozen request inventory.

    Older comparison receipts omit request_sha256. Their submitted request
    remains bound by the reviewed inventory and native submit/execute/exit log;
    do not add a guessed hash to those original receipts.
    """
    directory = Path(directory).resolve()
    read = lambda path: json.loads(path.read_text())
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    binding_path = directory/'preparation/request_binding.json'
    binding = read(binding_path)
    review = read(directory/'preparation/root_review.json')
    plan_path = directory/'plan.json'
    if (sha(binding_path) != review['request_binding_sha256']
            or sha(plan_path) != binding['plan_sha256']
            or sha(directory/'frozen/frozen_tools.tar.gz') != binding['archive_sha256']
            or binding['archive_sha256'] != review['archive_sha256']):
        raise ValueError('Reviewed pair inventory or plan changed')
    plan, pairs, request_hashes = read(plan_path), [], {}
    dag = (directory/'production.dag').read_text().splitlines()
    for pair in plan['pairs']:
        job = directory/'verifiers'/pair['id']
        request_path = job/'request.json'
        pin = binding['requests'][pair['id']]
        if str(request_path) != pin['path'] or sha(request_path) != pin['sha256']:
            raise ValueError('Pair request changed: '+pair['id'])
        expected_vars = ('VARS '+pair['id']+' RequestSHA="'+pin['sha256']
                         +'" ToolsSHA="'+binding['archive_sha256']+'"')
        if dag.count(expected_vars) != 1:
            raise ValueError('Pair submitted request/runtime binding differs')
        request, result, status = [read(job/name) for name in
                                    ('request.json', 'result.json', 'status.json')]
        if (request['argv'][-2:] != [str(directory), pair['id']]
                or request['cohort_id'] != plan['cohort_id']
                or not all(x.get('complete') is True and x.get('exit_code') == 0
                           for x in (result, status))):
            raise ValueError('Pair request or completion differs: '+pair['id'])
        if result.get('request_sha256', pin['sha256']) != pin['sha256']:
            raise ValueError('Pair receipt request hash differs: '+pair['id'])
        log = (job/'events.log').read_text()
        submitted = re.findall(r'000 \((\d+)\.0+\.0+\)', log)
        if len(submitted) != 1:
            raise ValueError('Pair requires one preserved native attempt')
        cluster = submitted[0]
        terminal = [block for block in log.split('...')
                    if re.search(r'005 \(0*'+cluster+r'\.0+\.0+\)', block)]
        if (not re.search(r'001 \(0*'+cluster+r'\.0+\.0+\)', log)
                or not terminal or 'Normal termination (return value 0)' not in terminal[-1]):
            raise ValueError('Pair lacks native successful execution')
        if sha(job/'return_payload.tar.gz') != status['return_payload_sha256']:
            raise ValueError('Pair returned archive changed')
        summary_path = directory/'pair_summaries'/(pair['id']+'.json')
        item = read(summary_path)
        if (item.get('complete') is not True or item['pair'] != pair
                or result['selector'] != pair['id']
                or sha(summary_path) != result['summary_sha256']):
            raise ValueError('Pair summary changed: '+pair['id'])
        expected_url = 'root://eosuser.cern.ch/'+plan['publication_directory']+'/pairs/'+pair['id']+'/comparison.json'
        publications = result['published']
        if (len(publications) != 1 or publications[0] != dict(url=expected_url,
                bytes=summary_path.stat().st_size, sha256=sha(summary_path))):
            raise ValueError('Pair publication differs')
        if fetch is not None:
            fetch(publications[0], summary_path)
        old, new = item['units']['legacy'], item['units']['complete']
        for key in ('source_id', 'original_source_sha256', 'prepared_input_GEN_sha256',
                    'timed_GEN_graph_sha256', 'full_source_metadata_sha256', 'weight_audit'):
            if old[key] != new[key]:
                raise ValueError('Paired source, graph, weight or exposure differs: '+key)
        pairs.append(item)
        request_hashes[pair['id']] = pin['sha256']
    return dict(schema='shift-paired-hadron-transport-comparison-v1', complete=True,
        originals=len(pairs), workers=len(plan['samples']), pairs=pairs, new_GEN_exposure=0,
        modes_not_additive=True, source_exposure_counted_once_per_source_per_mode=plan['full_source_denominators'],
        representative_efficiency_or_rate=False, tail_gain_qualified=False,
        physics_normalization_ready=False, request_hashes=request_hashes,
        plan_sha256=sha(plan_path), binding_sha256=sha(binding_path),
        published_pairs_independently_read_back=fetch is not None)


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
