#!/usr/bin/env python3
"""Histogram a bounded signal replay with the exact frozen background runtime.

Uses native genWeight, with production-rate/BR correction retained in the sample
ledger. Never builds, submits, publishes, or overwrites existing histograms.
The frozen background's J/psi-specific truth-pair plots remain J/psi-specific;
dark-photon truth diagnostics are supplied by audit_dark_photon_dimuons.py.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time


WORKSPACE = Path(__file__).resolve().parents[2]


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def histogram_inventory(root, prefix=''):
    found = {}
    for key in root.GetListOfKeys():
        obj = key.ReadObj()
        name = prefix + key.GetName()
        if obj.InheritsFrom('TDirectory'):
            found.update(histogram_inventory(obj, name + '/'))
        elif obj.InheritsFrom('TH1'):
            values = [float(obj.GetBinContent(i)) for i in range(obj.GetNcells())]
            errors = [float(obj.GetBinError(i)) for i in range(obj.GetNcells())]
            if not all(math.isfinite(v) for v in values + errors):
                raise ValueError('Nonfinite histogram content: ' + name)
            found[name] = dict(entries=float(obj.GetEntries()), sum_all_cells=sum(values),
                               sum_error_squared_all_cells=sum(v*v for v in errors))
    return found


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('detector_directory', type=Path)
    parser.add_argument('--freeze', type=Path, default=WORKSPACE / 'validation/bsm_darkphoton_20261009/histogram_freeze')
    parser.add_argument('--runtime', type=Path, default=WORKSPACE / 'validation/histogram_hold_20261007/tea_runtime')
    parser.add_argument('--frozen-bin', type=Path, default=WORKSPACE / 'validation/histogram_hold_20261007/worker_payload/bin')
    parser.add_argument('--source-manifest', type=Path, default=WORKSPACE / 'validation/histogram_stall_20261007/local_runtime_source_checksums.json')
    parser.add_argument('--in-runtime', action='store_true', help=argparse.SUPPRESS)
    return parser.parse_args()


def main():
    args = parse_args()
    out, freeze, runtime, binary = [p.resolve() for p in
                                  (args.detector_directory, args.freeze, args.runtime, args.frozen_bin)]
    if not args.in_runtime:
        # Enter only the existing frozen user-local runtime, never CMSSW or a build.
        env = {k:v for k,v in os.environ.items() if k not in
               ('PYTHONPATH','PYTHONHOME','LD_LIBRARY_PATH','LD_PRELOAD','ROOTSYS')}
        env.update(PATH=str(runtime/'bin')+':/usr/bin:/bin', PYTHONHOME=str(runtime),
                   CONDA_PREFIX=str(runtime), ROOTSYS=str(runtime), PYTHONDONTWRITEBYTECODE='1',
                   CONDA_BUILD_SYSROOT=str(runtime/'x86_64-conda-linux-gnu/sysroot'),
                   PYTHONPATH=str(freeze/'frozen_configs')+':'+str(binary),
                   LD_LIBRARY_PATH=':'.join(map(str,(binary,runtime/'lib',
                     runtime/'lib/python3.12/site-packages/correctionlib/lib'))))
        executable = runtime/'bin/python'
        os.execve(executable, [str(executable),str(Path(__file__).resolve()),*sys.argv[1:],'--in-runtime'], env)
    if (out/'histograms.root').exists() or (out/'histogram_report.json').exists():
        raise FileExistsError('Refuse to overwrite previous histogram output or receipt')
    signal_path, chain_path = out/'signal_report.json', out/'report.json'
    signal, chain = [json.loads(p.read_text()) for p in (signal_path,chain_path)]
    if not signal.get('complete') or not chain.get('complete'):
        raise ValueError('Signal replay and common chain must both be complete')
    expected = signal['requested_event_ids']
    if not 1 <= len(expected) <= 20:
        raise ValueError('Only bounded 1..20-event signal replays are supported')
    nano_path = out/'nano.root'
    if digest(nano_path) != chain['nano_sha256']:
        raise ValueError('Nano differs from the completed chain receipt')
    frozen = json.loads((freeze/'digest_manifest.json').read_text())
    frozen_receipt = json.loads((freeze/'frozen_config_receipt.json').read_text())
    for row in frozen['source_to_persistent_copy'].values():
        p = Path(row['persistent_copy'])
        if p.stat().st_size != row['bytes'] or digest(p) != row['sha256']:
            raise ValueError('Persistent frozen configuration changed: ' + str(p))
    for name, row in frozen_receipt['config_files'].items():
        if digest(freeze/'frozen_configs'/Path(name).name) != row['sha256']:
            raise ValueError('Frozen background config changed: ' + name)
    source = json.loads(args.source_manifest.read_text())
    checked = {}
    for name in ('shift_histogrammer','libcore.so','libextensions.so','libhistogramming.so'):
        path = binary/name; key = 'bin/'+name
        checked[key] = digest(path)
        if checked[key] != source[key]:
            raise ValueError('Frozen binary/library changed: '+key)
    for name in ('bin/python3.12','lib/libpython3.12.so.1.0','lib/libCore.so.6.34',
                 'lib/libTree.so.6.34','lib/libCling.so'):
        path = (runtime/name).resolve(); key = 'runtime/'+str(path.relative_to(runtime))
        checked[key] = digest(path)
        if checked[key] != source[key]:
            raise ValueError('Frozen runtime component changed: '+key)
    import ROOT
    root = ROOT.TFile.Open(str(nano_path))
    if not root or root.IsZombie() or root.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unreadable/recovered signal Nano')
    tree = root.Get('Events')
    if not tree or int(tree.GetEntries()) != len(expected):
        raise ValueError('Nano count differs from signal exposure')
    identities, muons, dimuons, sumw = [], 0, 0, 0.
    for event in tree:
        weight = float(event.genWeight)
        if weight != 1.:
            raise ValueError('Native bounded signal pilot requires unit genWeight')
        identities.append([int(event.run),int(event.luminosityBlock),int(event.event)])
        muons += int(event.nShiftMuon); dimuons += int(event.nShiftDimuonVertex); sumw += weight
    root.Close()
    if identities != expected:
        raise ValueError('Nano identity mismatch')
    wrapper = out/'signal_histogram_config.py'
    if wrapper.exists():
        raise FileExistsError('Refuse to overwrite histogram configuration')
    wrapper.write_text('from shift_histogrammer_config import *\n\n'
                       f'nEvents = {len(expected)}\nweightsBranchName = "genWeight"\n')
    hist_path = out/'histograms.root'
    command = [str(binary/'shift_histogrammer'),'--config',str(wrapper),'--input_path',str(nano_path),
               '--output_hists_path',str(hist_path)]
    report = dict(schema='shift-dark-photon-frozen-histogram-replay-v2',complete=False,
                  physics_valid=False,normalization_ready=False,command=command,
                  input_event_ids=identities,input_sha256=digest(nano_path),native_event_weight_sum=sumw,
                  nano_shift_muons=muons,nano_shift_dimuons=dimuons,
                  source_signal_report_sha256=digest(signal_path),source_chain_report_sha256=digest(chain_path),
                  background_archive_sha256=frozen['background_archive_sha256'],
                  frozen_component_sha256=checked,freeze_manifest_sha256=digest(freeze/'digest_manifest.json'),
                  source_manifest_sha256=digest(args.source_manifest),wrapper_sha256=digest(wrapper),
                  runner_sha256=digest(__file__),root_version=ROOT.gROOT.GetVersion(),
                  config_overrides=dict(nEvents=len(expected),weightsBranchName='genWeight'),
                  weight_policy='native unit weights; production rate/BR corrections remain in sample ledger',
                  truth_pair_scope='frozen background J/psi definitions unchanged; use separate A-prime diagnostic')
    started = time.monotonic()
    try:
        with (out/'histogram.log').open('w') as log:
            subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
        root = ROOT.TFile.Open(str(hist_path))
        if not root or root.IsZombie() or root.TestBit(ROOT.TFile.kRecovered):
            raise ValueError('Unreadable/recovered histogram output')
        histograms = histogram_inventory(root)
        for name in ('cutFlow','rawEventsCutFlow','event/Event_nShiftMuon',
                     'event/Event_nShiftDimuonVertex','muon/ShiftMuon_pt','dimuon/ShiftDimuonVertex_mass'):
            if name not in histograms:
                raise ValueError('Missing required histogram: '+name)
        for name in ('cutFlow','rawEventsCutFlow'):
            if root.Get(name).GetBinContent(1) != len(expected):
                raise ValueError('Raw/native-weight event exposure changed')
        for name, expected_sum in (('event/Event_nShiftMuon',len(expected)),
                                   ('event/Event_nShiftDimuonVertex',len(expected)),
                                   ('muon/ShiftMuon_pt',muons),('dimuon/ShiftDimuonVertex_mass',dimuons)):
            if histograms[name]['sum_all_cells'] != expected_sum:
                raise ValueError('Histogram integral differs from Nano: '+name)
        root.Close()
        report.update(complete=True,histogram_sha256=digest(hist_path),histogram_bytes=hist_path.stat().st_size,
                      histogram_count=len(histograms),histograms=histograms)
    except Exception as error:
        report['error'] = repr(error)
        raise
    finally:
        report.update(wall_seconds=time.monotonic()-started,
                      histogram_log_sha256=digest(out/'histogram.log') if (out/'histogram.log').exists() else None)
        (out/'histogram_report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(complete=True,events=len(expected),muons=muons,dimuons=dimuons,
                         histogram_count=report['histogram_count'],output=str(out))))


if __name__ == '__main__':
    main()
