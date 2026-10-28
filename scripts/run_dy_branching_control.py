#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded native DY channel-control worker.
"""Run scratch-only GEN, reread it independently, and return compact evidence.

Successful controls make no EOS writes. A separately registered failure-only
allowance protects a produced GEN file if validation fails. This entry never
changes a production weight or marks a production ready.
"""
import argparse
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time

import dy_branching_control as control


TOOLS = Path(__file__).resolve().parents[1]


def plan_for(request):
    relative = Path(request['plan_relative_path'])
    control.require(not relative.is_absolute() and '..' not in relative.parts,
                    'Control plan must be inside its frozen capsule')
    plan = TOOLS/relative
    control.require(control.sha(plan) == request['plan_sha256'], 'Frozen DY plan changed')
    return plan


def retain_failure(request):
    """Preserve a produced, possibly bad GEN before worker scratch disappears."""
    gen = Path('gen.root')
    if not gen.exists():
        return None
    bound = request['failure_publication_bound_bytes']
    control.require(bound > 0, 'Produced failing GEN needs a registered retention allowance')
    control.require(request['failure_publication_directory'].startswith(
        '/eos/user/j/jniedzie/shift_cmssw_support/'), 'Failed GEN must stay in registered support storage')
    from run_shift_weighted_gen import publish
    names = [name for name in ['gen.root', 'gen_cfg.py', 'config_change_receipt.json',
        'audit.json', 'native_second_readback.json', 'cmsRun.tail.log', 'report.json']
        if Path(name).is_file()]
    control.require(sum(Path(name).stat().st_size for name in names) < bound,
                    'Failing GEN exceeds its declared retention allowance')
    return publish([(Path(name), name) for name in names], request['failure_publication_directory'])


def worker(request_path, prepare_only=False):
    started = time.monotonic()
    request = json.loads(request_path.read_text())
    report = dict(complete=False, diagnostic_only=True, detector_or_material_run=False,
                  production_ready=False, correction_applied=False)
    try:
        control.require(request['normal_EOS_write_bound_bytes'] == 0,
                        'Successful DY control must return compact evidence only')
        plan_path = plan_for(request)
        plan = json.loads(plan_path.read_text())
        control.require(type(plan['accepted_events_per_arm']) is int
            and 1 <= plan['accepted_events_per_arm'] <= 5000, 'Unbounded DY GEN control')
        control.require(request['failure_publication_bound_bytes']
            == plan['budget']['failure_publication_bound_bytes_per_arm']
            and request['returned_evidence_bound_bytes']
            == plan['budget']['returned_evidence_bound_bytes_per_arm'],
            'Worker retention or evidence allowance differs from its frozen plan')
        control.make_config(plan_path, request['arm'])
        from audit_shift_material_scout import metadata
        from shift_gen_material_features import hepmc_unit_names
        from DataFormats.FWLite import Events, Handle
        import ROOT
        ROOT.gSystem.Load('libFWCoreFWLite')
        ROOT.FWLiteEnabler.enable()
        report['native_imports'] = [metadata.__module__, Events.__module__, Handle.__module__]
        report['native_hepmc_unit_names'] = hepmc_unit_names()
        if prepare_only:
            report.update(complete=True, prepared_only=True, GEN_or_cmsRun_run=False)
        else:
            with Path('cmsRun.log').open('w') as log:
                subprocess.run(['cmsRun', 'gen_cfg.py'], stdout=log, stderr=subprocess.STDOUT,
                               timeout=request['stage_timeout_seconds'], check=True)
            control.audit_arm(plan_path, request['arm'], Path.cwd())
            before = Path('audit.json').read_bytes()
            # A separate native interpreter reopens the actual ROOT file and
            # recounts all accepted IDs, channels, Run/Lumi and sigma values.
            subprocess.run([sys.executable, str(TOOLS/'scripts/dy_branching_control.py'),
                'audit', str(plan_path), request['arm'], str(Path.cwd())],
                timeout=request['readback_timeout_seconds'], check=True)
            control.require(Path('audit.json').read_bytes() == before,
                            'Fresh-process native GEN readback changed the audit')
            second = dict(complete=True, full_native_GEN_reopened=True,
                identical_audit_sha256=control.sha('audit.json'),
                every_accepted_identity_channel_weight_and_Run_Lumi_reread=True)
            control.dump('native_second_readback.json', second)
            audit = json.loads(before)
            report.update(complete=True, arm=request['arm'], stratum=plan['stratum'],
                events=audit['accepted_gen_events'], source_xsec_pb=audit['source_xsec_pb'],
                source_xsec_error_pb=audit['source_xsec_error_pb'],
                muonic_events=audit['muonic_events'], native_audit=audit,
                full_native_second_readback=second, normal_EOS_writes=0,
                generated_ROOT_bytes=Path('gen.root').stat().st_size,
                generated_ROOT_retirement='Successful worker-local scratch only; exact seed/config/GEN hash, all native event rows and Run/Lumi retained')
        report.update(wall_seconds=time.monotonic()-started,
            plan_sha256=control.sha(plan_path), request_sha256=control.sha(request_path))
        control.dump('report.json', report)
        if Path('cmsRun.log').exists():
            Path('cmsRun.tail.log').write_bytes(Path('cmsRun.log').read_bytes()[-131072:])
        returned = [Path(name) for name in request['return_files'] if Path(name).is_file()]
        control.require(sum(path.stat().st_size for path in returned)+Path('report.json').stat().st_size
                        < request['returned_evidence_bound_bytes'], 'Compact DY evidence exceeds its AFS allowance')
    except Exception as error:
        report.update(complete=False, error=repr(error), wall_seconds=time.monotonic()-started)
        control.dump('report.json', report)
        if Path('cmsRun.log').exists():
            Path('cmsRun.tail.log').write_bytes(Path('cmsRun.log').read_bytes()[-131072:])
        try:
            report['failure_publications'] = retain_failure(request)
        except Exception as retention_error:
            report['failure_retention_error'] = repr(retention_error)
        control.dump('report.json', report)
        raise


def collect(request_path):
    request = json.loads(request_path.read_text())
    control.require(control.sha(request['plan_path']) == request['plan_sha256'],
                    'Comparison source plan changed')
    audits = {}
    rows_read = {}
    for arm in ['forced', 'natural']:
        pin = request['arms'][arm]
        path = Path(pin['request_path'])
        control.require(control.sha(path) == pin['request_sha256'], 'Parent DY request changed')
        result = json.loads(path.with_name('result.json').read_text())
        control.require(result['complete'] and result['exit_code'] == 0
            and result['request_sha256'] == pin['request_sha256'], 'Missing or failed parent DY result')
        payload = path.with_name('return_payload.tar.gz')
        status = json.loads(path.with_name('status.json').read_text())
        control.require(status['complete'] and status['exit_code'] == 0
            and control.sha(payload) == status['return_payload_sha256'], 'Returned DY evidence changed')
        with tarfile.open(payload) as archive:
            audit = json.load(archive.extractfile('audit.json'))
            compressed = archive.extractfile('primary23_events.jsonl.gz').read()
            import hashlib
            control.require(hashlib.sha256(compressed).hexdigest() == audit['event_rows_sha256'],
                            'Native DY row authority changed')
            rows = [json.loads(line) for line in gzip.decompress(compressed).splitlines()]
        control.require(audit == result['native_audit']
            and audit['plan_sha256'] == request['plan_sha256']
            and audit['accepted_gen_events'] == len(rows)
            and len({tuple(r['event_id']) for r in rows}) == len(rows)
            and all(r['native_weight'] == 1. for r in rows)
            and audit['muonic_events'] == sum(r['primary23_mumu'] for r in rows),
            'Returned native rows differ from the worker audit')
        control.require(control.mass_counts(rows,[r['bounds_GeV'] for r in audit['mass_slices']])
                        == audit['mass_slices'], 'Native DY mass-slice counts changed')
        rows_read[arm] = len(rows)
        audits[arm] = Path(arm+'_audit.json')
        control.dump(audits[arm], audit)
    control.compare(audits['forced'], audits['natural'], Path('comparison.json'))
    result = json.loads(Path('comparison.json').read_text())
    target = request['minimum_natural_muonic_count']
    result.update(request_sha256=control.sha(request_path), exit_code=0,
        plan_sha256=request['plan_sha256'],
        accepted_rows_reread=rows_read, minimum_natural_muonic_count=target,
        positive_count_target_met=result['comparisons'][0]['natural']['count'] >= target,
        slice_count_target_met=all(not row['sparse_muonic_count'] for row in result['comparisons'][1:]),
        normal_EOS_writes=0)
    control.dump('result.json', result)


def finalize(request_path):
    """Terminal accounting for the existing read-only DAG watchdog."""
    request = json.loads(request_path.read_text())
    control.dump('result.json', dict(complete=False, exit_code=1,
        benchmark_plan_sha256=request['plan_sha256'], complete_frozen_draw_collected=False))
    plan_path = Path(request['plan_path'])
    control.require(control.sha(plan_path) == request['plan_sha256'], 'Final DY plan changed')
    plan = json.loads(plan_path.read_text())
    control.require(len(plan['cases']) == plan['science_workers']
        and len({(row['stratum'],row['arm']) for row in plan['cases']}) == plan['science_workers'],
        'Final DY native case census is incomplete or repeated')
    for case in plan['cases']:
        path = Path(case['request_path'])
        control.require(control.sha(path) == case['request_sha256'], 'Final DY case request changed')
        result = json.loads(path.with_name('result.json').read_text())
        control.require(result['complete'] and result['exit_code'] == 0
            and result['request_sha256'] == case['request_sha256']
            and result['native_audit']['complete']
            and result['native_audit']['arm'] == case['arm']
            and result['native_audit']['accepted_gen_events'] == plan['accepted_GEN_events_per_worker']
            and result['full_native_second_readback']['complete']
            and result['full_native_second_readback']['every_accepted_identity_channel_weight_and_Run_Lumi_reread'],
            'Final DY case lacks successful complete native readback')
    expected = {Path(pin['path']).parent.name:pin for pin in plan['all_native_subplans']}
    names = [row['stratum'] for row in request['comparisons']]
    control.require(len(names) == len(set(names)) and set(names) == set(expected),
                    'Final DY comparison census is incomplete or duplicated')
    results = {}
    for row in request['comparisons']:
        source = expected[row['stratum']]
        control.require(control.sha(source['path']) == source['sha256'], 'Final DY source plan changed')
        path = Path(row['request_path'])
        control.require(control.sha(path) == row['request_sha256'], 'Final DY comparison request changed')
        result_path = path.with_name('result.json')
        result = json.loads(result_path.read_text())
        control.require(result['complete'] and result['exit_code'] == 0
            and result['request_sha256'] == row['request_sha256']
            and result['plan_sha256'] == source['sha256'], 'Final DY comparison is not native-complete')
        results[row['stratum']] = dict(result=result,
            result_sha256=control.sha(result_path), request_sha256=row['request_sha256'])
    control.dump('result.json', dict(complete=True, exit_code=0,
        benchmark_plan_sha256=request['plan_sha256'], complete_frozen_draw_collected=True,
        request_sha256=control.sha(request_path), diagnostic_only=True,
        accepted_GEN_events=plan['total_accepted_GEN_budget'], results=results,
        all_positive_count_targets_met=all(row['result']['positive_count_target_met'] for row in results.values()),
        all_slice_count_targets_met=all(row['result']['slice_count_target_met'] for row in results.values()),
        normalization_ready=False, full_production_ready=False, correction_applied=False,
        complete_means_accounted_test_not_a_passed_physics_convention=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['worker', 'collect', 'finalize'])
    parser.add_argument('request', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if args.mode == 'collect':
        collect(args.request)
    elif args.mode == 'finalize':
        finalize(args.request)
    else:
        worker(args.request, args.prepare_only)
