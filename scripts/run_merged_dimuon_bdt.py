#!/usr/bin/env python3
# Code style: AI-code. AI-generated frozen scoring and histogram production.
"""Score an immutable merged-MC inventory and histogram distinct copies.

The driver is used inside a frozen worker. It never submits jobs, filters
events/pairs, changes source files or overwrites incomplete publications.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zlib

import prepare_dimuon_score_production as io


def sha(path):
    return io.digest(path)


def adler(path):
    value = 1
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            value = zlib.adler32(block, value)
    return f'{value & 0xffffffff:08x}'


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, record):
    io.write_json(path, record)


def standard_environment(cache, analysis):
    runtime = Path(cache) / 'runtime'
    env = io.clean_transfer_environment()
    env.update(PATH=str(runtime / 'bin') + ':/usr/bin:/bin',
               PYTHONHOME=str(runtime), CONDA_PREFIX=str(runtime), ROOTSYS=str(runtime),
               CONDA_BUILD_SYSROOT=str(runtime / 'x86_64-conda-linux-gnu/sysroot'),
               PYTHONPATH=str(analysis / 'bin'), PYTHONDONTWRITEBYTECODE='1',
               LD_LIBRARY_PATH=':'.join(map(str, (analysis / 'bin', runtime / 'lib',
                   runtime / 'lib/python3.12/site-packages/correctionlib/lib'))))
    return env


def prepare_analysis(plan, scratch, cache):
    analysis = scratch / 'a'
    analysis.mkdir(exist_ok=False)
    archive = Path(plan['analysis_archive']['path'])
    if sha(archive) != plan['analysis_archive']['sha256']:
        raise ValueError('Frozen standard analysis archive changed')
    with tarfile.open(archive) as source:
        for entry in source.getmembers():
            if entry.name.startswith('/') or '..' in Path(entry.name).parts or not entry.isfile():
                raise ValueError('Unexpected member in frozen analysis archive')
        source.extractall(analysis)
    sys.path.insert(0, str(cache))
    from prefixes import update_prefix
    for target, old, destination in read_json(analysis / 'prefix_records.json'):
        new = str(Path(cache) / 'runtime' if destination == 'runtime' else analysis / 'bin')
        if len(new) > len(old):
            raise ValueError('Local analysis path exceeds archived binary relocation capacity')
        update_prefix(str(analysis / target), new, old, 'binary')
    return analysis


def check_standard_histograms(path, expected_events, expected_sumw):
    import ROOT
    root = ROOT.TFile.Open(str(path))
    if not root or root.IsZombie() or root.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unusable standard histogram output')
    try:
        raw, weighted = root.Get('rawEventsCutFlow'), root.Get('cutFlow')
        if not raw or not weighted or int(round(raw.GetBinContent(1))) != expected_events:
            raise ValueError('Standard histogram event count differs from scored source')
        if not math.isclose(float(weighted.GetBinContent(1)), expected_sumw, rel_tol=1e-12, abs_tol=1e-300):
            raise ValueError('Standard histogram weights differ from source weights')
        for category in ('', 'Good', 'Near-Both', 'Near-Far', 'Both-Both', 'Both-Far', 'Other'):
            name = 'dimuon/ShiftDimuonVertex' + category + '_logMass'
            if not root.Get(name):
                raise ValueError('Frozen histogrammer is missing required histogram: ' + name)
    finally:
        root.Close()


def legacy_weight_sum(path, branch):
    """Reproduce TEA's ordered Float32 event-weight/cutflow accumulation."""
    import numpy as np
    import uproot
    total = np.float32(0.)
    with uproot.open(path, handler=uproot.source.file.MultithreadedFileSource, num_workers=1) as root:
        for arrays in root['Events'].iterate([branch], library='np', how=dict, step_size=1000):
            for value in arrays[branch]:
                total = np.float32(total + np.float32(value))
                if not np.isfinite(total):
                    raise ValueError('Legacy Float cutflow overflows or is nonfinite')
    return float(total)


def standard_weight_sum(plan, path, branch):
    """Use the qualified runtime's precision; preserve old frozen-plan semantics."""
    precision = plan.get('standard_weight_precision', 'float32')
    if precision == 'float32':
        return legacy_weight_sum(path, branch)
    if precision != 'float64':
        raise ValueError('Unknown standard histogram weight precision: ' + str(precision))
    import uproot
    total = 0.0
    with uproot.open(path, handler=uproot.source.file.MultithreadedFileSource, num_workers=1) as source:
        for arrays in source['Events'].iterate([branch], library='np', how=dict, step_size=1000):
            for value in arrays[branch]:
                total += float(value)
                if not math.isfinite(total):
                    raise ValueError('Standard Double cutflow is nonfinite')
    return total


def validate_plan(path, expected_hash):
    if sha(path) != expected_hash:
        raise ValueError('Merged scoring plan changed')
    plan = read_json(path)
    if plan['schema'] != 'shift-merged-dimuon-bdt-plan-v1' or plan['selection_applied'] is not False:
        raise ValueError('Invalid score-only merged simulation plan')
    for row in plan['dependencies'].values():
        if sha(row['path']) != row['sha256']:
            raise ValueError('Frozen dependency changed: ' + row['path'])
    return plan


def run_file(plan, row, work, cache, analysis, plan_hash):
    work.mkdir(exist_ok=False)
    source = work / 'source.root'
    physical = row['source_transport_path']
    if physical.startswith('/eos/'):
        io.get_remote(physical.replace('/eos/home-j/jniedzie/', '/eos/user/j/jniedzie/', 1), source)
    else:
        shutil.copyfile(physical, source)
    source_hash = sha(source)
    if row.get('source_sha256') and source_hash != row['source_sha256']:
        raise ValueError('Merged source differs from its frozen checksum')
    if source.stat().st_size != row['source_bytes']:
        raise ValueError('Merged source byte count differs from frozen inventory')
    if row.get('source_adler32') and adler(source) != row['source_adler32'].lower().zfill(8):
        raise ValueError('Merged source Adler checksum differs from migration inventory')
    marker_path = row['complete_marker']
    if io.remote_exists(marker_path):
        io.get_remote(marker_path, work / 'existing_complete.json')
        marker = read_json(work / 'existing_complete.json')
        planned = {'nano': row['output_nano'], 'histograms': row['output_histograms'],
                   **{k: row[k] for k in ('score_receipt', 'bdt_hist_receipt', 'score_log', 'histogram_log')}}
        if (marker.get('schema') != 'shift-merged-dimuon-bdt-complete-v1'
                or marker.get('complete') is not True
                or marker.get('plan_sha256') not in {plan_hash, *plan.get('compatible_previous_plans', {})}
                or marker.get('source_sha256') != source_hash or marker.get('source') != row['source_logical_path']
                or marker.get('events') != row['expected_events'] or type(marker.get('pairs')) is not int
                or marker['pairs'] < 0 or marker.get('model_sha256') != plan['model_sha256']
                or marker.get('selection_applied') is not False
                or set(marker.get('published', {})) != set(planned)
                or any(marker['published'][k].get('path') != v for k, v in planned.items())):
            raise ValueError('Existing publication differs from this exact plan')
        for key, item in marker['published'].items():
            local = work / ('existing_' + key)
            io.get_remote(item['path'], local)
            if sha(local) != item['sha256']:
                raise ValueError('Existing publication failed checksum readback')
        score = read_json(work / 'existing_score_receipt')
        histogram = read_json(work / 'existing_bdt_hist_receipt')
        if (score.get('complete') is not True or score.get('source_sha256') != source_hash
                or score.get('output_sha256') != marker['published']['nano']['sha256']
                or score.get('model_sha256') != marker['model_sha256']
                or score.get('events') != marker['events'] or score.get('retained_pairs') != marker['pairs']
                or score.get('selection_applied') is not False
                or score.get('verification', {}).get('all_original_content_equal') is not True
                or score.get('verification', {}).get('all_scores_recomputed_equal') is not True
                or histogram.get('complete') is not True or histogram.get('pairs') != marker['pairs']
                or histogram.get('output_sha256') != marker['published']['histograms']['sha256']):
            raise ValueError('Existing receipts lack matching content and score verification')
        return dict(index=row['index'], complete=True, reused=True,
                    publication_plan_sha256=marker['plan_sha256'], marker=marker)
    if any(io.remote_exists(row[k]) for k in ('output_nano', 'output_histograms', 'score_receipt',
                                             'bdt_hist_receipt', 'score_log', 'histogram_log')):
        raise FileExistsError('Incomplete publication exists; preserve it without overwrite')
    classifier = Path(plan['classifier_directory'])
    scored, score_receipt = work / 'scored.root', work / 'score.json'
    with (work / 'score.log').open('x') as log:
        subprocess.run([sys.executable, str(classifier / 'add_bdt_score.py'), '--input', str(source),
            '--output', str(scored), '--model', plan['model'], '--sample-kind', 'simulation',
            '--receipt', str(score_receipt)], check=True, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    score = read_json(score_receipt)
    if (score.get('complete') is not True or score.get('source_sha256') != source_hash
            or score.get('model_sha256') != plan['model_sha256']
            or score['events'] != row['expected_events'] or score.get('selection_applied') is not False
            or score['verification'].get('all_original_content_equal') is not True
            or score['verification'].get('all_scores_recomputed_equal') is not True):
        raise ValueError('Scorer did not preserve the complete merged source')
    bdt_hist, bdt_receipt = work / 'bdt.root', work / 'bdt_hist.json'
    with (work / 'histogram.log').open('x') as log:
        subprocess.run([sys.executable, plan['bdt_histogrammer'], '--input', str(scored),
            '--output', str(bdt_hist), '--receipt', str(bdt_receipt),
            '--classifier-directory', str(classifier), '--weight-branch', row['weight_branch']],
            check=True, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        histogram = read_json(bdt_receipt)
        if histogram.get('complete') is not True or histogram['events'] != score['events'] or histogram['pairs'] != score['retained_pairs']:
            raise ValueError('BDT histograms changed the scored event/pair population')
        standard = work / 'standard.root'
        wrapper = work / 'standard_config.py'
        wrapper.write_text('import shift_sample_paths\n'
            'shift_sample_paths.latest_merged_sample = lambda *a, **k: (' + repr(str(scored)) + ', ' + repr(source_hash[:12]) + ')\n'
            'from shift_histogrammer_config import *\n'
            'nEvents = -1\nweightsBranchName = ' + repr(row['weight_branch']) + '\n')
        runtime = Path(cache) / 'runtime'
        subprocess.run([str(analysis / 'bin/shift_histogrammer'), '--config', str(wrapper),
            '--input_path', str(scored), '--output_hists_path', str(standard)],
            cwd=analysis / 'bin', env=standard_environment(cache, analysis), check=True,
            stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        expected_sum = standard_weight_sum(plan, scored, row['weight_branch'])
        check_standard_histograms(standard, score['events'], expected_sum)
        histogram['standard_cutflow_sumw'] = expected_sum
        histogram['standard_weight_precision'] = plan.get('standard_weight_precision', 'float32')
        if histogram['standard_weight_precision'] == 'float32':
            histogram['standard_cutflow_float32_sumw'] = expected_sum
        histogram['standard_cutflow_policy'] = 'Ordered accumulation at the qualified standard runtime precision'
        combined = work / 'histograms.root'
        subprocess.run(['hadd', str(combined), str(standard), str(bdt_hist)], check=True,
            stdout=log, stderr=subprocess.STDOUT, timeout=300)
    score['staged_paths'] = {k: score[k] for k in ('source', 'output')}
    score.update(source=row['source_logical_path'], source_transport=physical,
                 output=row['output_nano'], plan_sha256=plan_hash)
    score_receipt.write_text(json.dumps(score, indent=2) + '\n')
    histogram['staged_paths'] = {k: histogram[k] for k in ('input', 'output') if k in histogram}
    histogram.update(input=row['output_nano'], output=row['output_histograms'], output_sha256=sha(combined),
                     supplemental_histogram_sha256=sha(bdt_hist), plan_sha256=plan_hash)
    bdt_receipt.write_text(json.dumps(histogram, indent=2) + '\n')
    local_files = {'nano': scored, 'histograms': combined, 'score_receipt': score_receipt,
                  'bdt_hist_receipt': bdt_receipt, 'score_log': work / 'score.log',
                  'histogram_log': work / 'histogram.log'}
    destinations = {'nano': row['output_nano'], 'histograms': row['output_histograms'],
                    **{k: row[k] for k in ('score_receipt', 'bdt_hist_receipt', 'score_log', 'histogram_log')}}
    for directory in sorted({str(Path(v).parent) for v in destinations.values()} | {str(Path(marker_path).parent)}):
        io.transfer(['/usr/bin/xrdfs', 'root://eosuser.cern.ch', 'mkdir', '-p', directory])
    published = {}
    for key, local in local_files.items():
        remote = destinations[key]
        io.put_remote(local, remote)
        readback = work / ('readback_' + key)
        io.get_remote(remote, readback)
        if sha(readback) != sha(local):
            raise ValueError('Published file readback differs: ' + key)
        published[key] = dict(path=remote, sha256=sha(local), bytes=local.stat().st_size)
    marker = dict(schema='shift-merged-dimuon-bdt-complete-v1', complete=True,
        plan_sha256=plan_hash, source_sha256=source_hash, source=row['source_logical_path'],
        events=score['events'], pairs=score['retained_pairs'], model_sha256=score['model_sha256'],
        selection_applied=False, published=published)
    write_json(work / 'complete.json', marker)
    io.put_remote(work / 'complete.json', marker_path)
    io.get_remote(marker_path, work / 'readback_complete.json')
    if sha(work / 'complete.json') != sha(work / 'readback_complete.json'):
        raise ValueError('Completion marker readback differs')
    return dict(index=row['index'], complete=True, reused=False, marker=marker)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--batch', required=True, type=int)
    parser.add_argument('--pilot', action='store_true')
    parser.add_argument('--runtime-cache', required=True, type=Path)
    args = parser.parse_args()
    plan = validate_plan(args.plan, args.plan_sha256)
    scratch = Path(os.environ['_CONDOR_SCRATCH_DIR']).resolve()
    analysis = prepare_analysis(plan, scratch, args.runtime_cache)
    indices = plan['pilot'] if args.pilot else plan['batches'][args.batch]
    status = dict(plan_sha256=args.plan_sha256, batch=args.batch, pilot=args.pilot,
                  complete=False, files=[], expected_indices=indices, selection_applied=False)
    active = None
    try:
        for index in indices:
            active = scratch / f'f{index:04d}'
            status['files'].append(run_file(plan, plan['files'][index], active,
                args.runtime_cache, analysis, args.plan_sha256))
            shutil.rmtree(active)
            active = None
        status['complete'] = True
    except Exception as error:
        status['error'] = repr(error)
        raise
    finally:
        status['pending_indices'] = [i for i in indices if i not in {r['index'] for r in status['files']}]
        (scratch / 'batch_status.json').write_text(json.dumps(status, indent=2) + '\n')
        with tarfile.open(scratch / 'batch_failure.tar.gz', 'w:gz') as output:
            if active and active.exists():
                output.add(active, arcname=active.name)


if __name__ == '__main__':
    main()
