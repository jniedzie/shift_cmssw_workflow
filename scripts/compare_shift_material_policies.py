#!/usr/bin/env python3
# Code style: AI-code. AI-generated predeclared material-policy qualification.
"""Compare positive control floors on the same independent response benchmark.

This reads completed response artifacts. It never modifies GEN, selected
originals, native weights, Nanos, jobs, reservations or production controllers.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time

from shift_material_preselection import admission_probability
from shift_material_response import (bootstrap_comparison, shape_comparison, TARGETS,
                                     source_exposures, source_rate_weight, source_origin_sha256)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preflight(directory, authority):
    """Exercise actual standalone credentials, plotting and project publication."""
    env = dict(os.environ, PATH='/usr/bin:/bin')
    for key in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME', 'ROOTSYS'):
        env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix='shift-material-preflight-') as temp:
        path = Path(temp)/'probe.tar.gz'
        probe = authority['credential_probe']
        subprocess.run(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32', probe['url'], str(path)],
                       env=env, timeout=600, check=True)
        if sha(path) != probe['sha256']:
            raise ValueError('Standalone EOS credential/readback preflight failed')
    rows = [dict(original_source_sha256='a'*64, event_id=[1, 1, i], benchmark_probability=1.,
                 candidate_probability=1., rate_weight=1., detector_cpu_seconds=1.,
                 feature_cpu_seconds=.01, stratum='synthetic', tail_counts=dict.fromkeys(TARGETS, 1),
                 retained_pairs=[dict(isOS=True, topologyMin=2, topologyMax=2, mass=51., pt=21.)])
            for i in range(1, 41)]
    summary = dict(all_bins=bootstrap_comparison(rows, replicates=100),
                   shapes=shape_comparison(rows, dict(selection_domain='synthetic', selection_seed='one')))
    from plot_shift_material_response import plot
    pdf = Path(authority['plot_directory'])/'.qualification_preflight.pdf'
    plot(summary, pdf)
    if pdf.read_bytes()[:5] != b'%PDF-':
        raise ValueError('Standalone PDF preflight failed')
    receipt = dict(complete=True, checked_at_epoch=time.time(), EOS_probe=probe,
                   synthetic_pdf_sha256=sha(pdf), synthetic_pdf_bytes=pdf.stat().st_size,
                   synthetic_figure_removed=True, real_response_analyzed=False)
    pdf.unlink()
    (directory/'qualification_preflight.json').write_text(json.dumps(receipt, indent=2)+'\n')


def run(directory, authority):
    plan = json.loads((directory/'plan.json').read_text())
    if sha(directory/'plan.json') != authority['response_plan_sha256']:
        raise ValueError('Response worklist changed')
    rows, priorities, evidence = [], {}, []
    native = dict(os.environ, PATH='/usr/bin:/bin')
    for key in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME', 'ROOTSYS'):
        native.pop(key, None)
    for sample in plan['samples']:
        job = directory/'jobs'/sample['id']
        result, status = (json.loads((job/name).read_text()) for name in ('result.json', 'status.json'))
        if not result['complete'] or not status['complete'] or result['exit_code'] != 0:
            raise ValueError('Incomplete benchmark: '+sample['id'])
        if result['request_sha256'] != sha(job/'request.json'):
            raise ValueError('Response request mismatch')
        if status['return_payload_sha256'] != sha(job/'return_payload.tar.gz'):
            raise ValueError('Returned response archive changed')
        with tarfile.open(job/'return_payload.tar.gz', 'r:gz') as archive:
            response = json.load(archive.extractfile('response.json'))
        if [r['event_id'] for r in response['events']] != sample['event_ids']:
            raise ValueError('Response original identities differ')
        source = plan['sources'][sample['source_id']]
        if (response['source_GEN_sha256'] != source_origin_sha256(source)
                or response.get('prepared_input_GEN_sha256', response['source_GEN_sha256']) != source['gen_sha256']
                or not response['saved_decays_unchanged'] or not response['no_second_filter_efficiency']
                or response['candidate_gate_applied_to_detector_sample']):
            raise ValueError('Invalid independent saved-decay response sample')
        publication = next(p for p in result['published'] if p['url'].endswith('/evidence.tar.gz'))
        with tempfile.TemporaryDirectory(prefix='shift-material-evidence-') as temp:
            payload = Path(temp)/'evidence.tar.gz'
            subprocess.run(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32', publication['url'], str(payload)],
                           env=native, timeout=600, check=True)
            if sha(payload) != publication['sha256']:
                raise ValueError('Published feature evidence changed')
            with tarfile.open(payload, 'r:gz') as archive:
                features = json.loads(gzip.decompress(archive.extractfile('features.json.gz').read()))
        if [f['event_id'] for f in features['events']] != sample['event_ids']:
            raise ValueError('Feature original identities differ')
        for feature, row in zip(features['events'], response['events']):
            key = (row['original_source_sha256'], tuple(row['event_id']))
            if key in priorities or feature['native_weight'] != row['native_weight']:
                raise ValueError('Repeated original or inconsistent native weight')
            expected = admission_probability(feature['ranking'], row['native_weight'],
                                               source['mean_abs_native_weight'], plan['policy'])
            if expected != row['candidate_probability']:
                raise ValueError('Frozen candidate probability does not reproduce')
            priorities[key] = feature['ranking']
            rows.append(row)
        evidence.append(publication)
    exposures, denominator_kinds = source_exposures(plan['sources'].values())
    for row in rows:
        source = plan['sources'][row['source_id']]
        row['rate_weight'] = source_rate_weight(source, row['native_weight'], exposures[row['stratum']])
    comparisons = {}
    for floor in authority['control_probabilities']:
        policy = dict(plan['policy'], control_probability=floor, unknown_probability=floor)
        for row in rows:
            source = plan['sources'][row['source_id']]
            key = (row['original_source_sha256'], tuple(row['event_id']))
            row['candidate_probability'] = admission_probability(priorities[key], row['native_weight'],
                source['mean_abs_native_weight'], policy)
        result = dict(complete=True, conditional_saved_GEN_study=True, policy=policy,
            all_bins=bootstrap_comparison(rows, replicates=authority['bootstrap_replicates'],
                                          confidence=authority['confidence']),
            shapes=shape_comparison(rows, policy), physics_normalization_ready=False,
            full_source_trials_by_bin={key: count for key, count in exposures.items()
                                       if denominator_kinds[key] == 'fixed_source_trials'},
            native_source_exposure_by_bin=exposures, denominator_kind_by_bin=denominator_kinds,
            inclusive_transport_complete=False)
        label = 'control_'+str(floor).replace('.', 'p')
        path = directory/(label+'_qualification.json')
        path.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
        pdf = Path(authority['plot_directory'])/(label+'_comparison.pdf')
        subprocess.run([sys.executable, str(Path(__file__).with_name('plot_shift_material_response.py')),
                        '--summary', str(path), '--output', str(pdf)], check=True)
        comparisons[str(floor)] = dict(summary_path=str(path), summary_sha256=sha(path),
            pdf=str(pdf), pdf_sha256=sha(pdf),
            primary_tails_supported=result['all_bins']['tail_improvement_proven'],
            targets=result['all_bins']['targets'])
    output = dict(schema='shift-material-control-floor-qualification-v1', complete=True,
        response_plan_sha256=authority['response_plan_sha256'],
        predeclared_authority_sha256=sha(directory/'tail_policy_comparison_plan.json'),
        original_events=len(rows), comparisons=comparisons, published_feature_evidence=evidence,
        physics_normalization_ready=False, confidence=authority['confidence'],
        observed_benchmark_effective_count_used=True)
    (directory/'qualification.json').write_text(json.dumps(output, indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    authority = json.loads((directory/'tail_policy_comparison_plan.json').read_text())
    try:
        preflight(directory, authority)
        while args.wait and not (directory/'summary.json').exists():
            (directory/'qualification_heartbeat.json').write_text(json.dumps(dict(checked_at_epoch=time.time(),
                phase='waiting_for_complete_response_benchmark', science_actions=False))+'\n')
            if (directory/'attention_required.json').exists():
                raise RuntimeError('Response watchdog reports a failure; preserve it for review')
            time.sleep(60)
        run(directory, authority)
        if args.result:
            args.result.write_text(json.dumps(dict(complete=True,
                qualification_sha256=sha(directory/'qualification.json')))+'\n')
    except BaseException as error:
        if args.result:
            args.result.write_text(json.dumps(dict(complete=False, error=repr(error)))+'\n')
        raise
