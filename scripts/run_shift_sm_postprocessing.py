#!/usr/bin/env python3
# Code style: AI-code. AI-generated shared merge and histogram worker.
"""Publish closed SM or conditional-signal sources through the main histogrammer.

The original small Nano files and all production settings remain unchanged.
Only complete, predetermined source selections contribute exposure. Zero-
admission sources contribute their full exposure without invented events.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time


DIMUON_CATEGORIES = ('', 'Good', 'Near-Both', 'Near-Far', 'Both-Both', 'Both-Far', 'Other')


def validate_dimuon_histograms(root):
    """Require main-config mass, pT and logMass in every dimuon category.

    Individual production files may have zero pairs. Runtime qualification
    separately requires an actual reconstructed positive-mass control pair.
    """
    required = ['dimuon/ShiftDimuonVertex'+category+'_'+variable
                for category in DIMUON_CATEGORIES for variable in ('mass', 'pt', 'logMass')]
    missing = [name for name in required if not root.Get(name)]
    if missing:
        raise ValueError('Missing histogram: '+', '.join(missing))
    return required


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 << 20), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    while True:
        try:
            with Path(path).open('rb') as stream:
                data = b''.join(iter(lambda: stream.read(1 << 20), b''))
            return json.loads(data)
        except OSError as error:
            if error.errno not in (5, 116, 110):
                raise
            time.sleep(10)


def save(path, value):
    path = Path(path)
    data = json.dumps(value, indent=2, allow_nan=False) + '\n'
    while True:
        try:
            temporary = path.with_suffix(path.suffix + '.tmp')
            temporary.write_text(data)
            temporary.replace(path)
            return
        except OSError as error:
            if error.errno not in (5, 116, 110):
                raise
            time.sleep(10)


def transfer_env():
    env = dict(os.environ)
    for key in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME', 'ROOTSYS', 'CONDA_PREFIX'):
        env.pop(key, None)
    env['PATH'] = '/usr/bin:/bin'
    return env


def download(source, target, checksum=None):
    url = str(source)
    if url.startswith('/eos/'):
        url = 'root://eosuser.cern.ch/' + url
    subprocess.run(['/usr/bin/xrdcp', '--silent', '--force', '--cksum', 'adler32', url, str(target)],
                   check=True, timeout=900, env=transfer_env())
    if checksum and digest(target) != checksum:
        raise ValueError('Input SHA-256 changed: ' + str(source))


def native_class_rate(sources):
    """Pool the same accepted MPI class with native Pythia trial weights.

    The accepted originals remain the histogram denominator. The internal
    trials determine the class cross-section estimate, as in the established
    SoftQCD/MPI partition audit; they never replace accepted-event exposure.
    """
    common, rates, errors, trials = None, [], [], []
    for source in sources:
        authority = source.get('native_class_normalization')
        if not isinstance(authority, dict) or authority.get('schema') != 'shift-native-MPI-class-rate-v1':
            raise ValueError('Exact native Pythia trial/model authority required for accepted-class pooling')
        count, rate = source['full_source_events'], source['native_cross_section_pb']
        tried, selected, accepted = (authority.get(key) for key in
                                     ('pythia_tried', 'pythia_selected', 'pythia_accepted'))
        if (any(type(value) is not int for value in (tried, selected, accepted))
                or not tried >= selected >= accepted > 0 or accepted != count):
            raise ValueError('Native accepted-class trial counters differ from full source exposure')
        for key in ('mpi_model_settings_sha256', 'audit_sha256'):
            value = authority.get(key)
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Missing accepted-class native model/audit digest')
        event_class = 'direct_jpsi' if source['stratum'].startswith('jpsi_') else 'qcd'
        bounds = authority.get('bounds')
        if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in bounds)
                or bounds[0] < 0 or (bounds[1] != -1 and bounds[1] <= bounds[0])):
            raise ValueError('Missing accepted-class native bin authority')
        if (authority.get('event_class') != event_class or authority.get('bounds') != source.get('bounds')
                or authority.get('mpi_model_contract') != 'pythia8-softqcd-nd-cp5-processlevel3-v1'
                or authority.get('partition_contract') != 'direct-hard-jpsi-status23or33-v1'):
            raise ValueError('Accepted-class native model/class/bin authority differs')
        sigma_nd = authority.get('sigma_nd_pb')
        if (not isinstance(sigma_nd, (int, float)) or isinstance(sigma_nd, bool)
                or not math.isfinite(sigma_nd) or sigma_nd <= 0
                or not math.isclose(rate * tried / count, sigma_nd, rel_tol=2e-10)):
            raise ValueError('Native class rate does not close against its ND cross section and trials')
        model = tuple(authority[key] for key in ('mpi_model_settings_sha256',
                      'mpi_model_contract', 'partition_contract', 'event_class')) + (tuple(authority['bounds']),)
        if common is None:
            common = model, sigma_nd
        elif model != common[0] or not math.isclose(sigma_nd, common[1], rel_tol=2e-10):
            raise ValueError('Accepted-class sources have incompatible native model or ND cross section')
        error = source.get('native_cross_section_error_pb')
        if (not isinstance(error, (int, float)) or isinstance(error, bool)
                or not math.isfinite(error) or error < 0):
            raise ValueError('Missing accepted-class native cross-section uncertainty')
        trials.append(tried)
        rates.append(tried * rate)
        errors.append((tried * error) ** 2)
    total = sum(trials)
    return dict(cross_section_pb=math.fsum(rates) / total,
                cross_section_error_pb=math.sqrt(math.fsum(errors)) / total,
                native_pythia_trials=total,
                native_class_pooling='native-Pythia-trial-weighted-class-rate-v1')


def exposure(sources, stratum):
    """Preserve the declared native estimator; never add a branching factor."""
    seen = set()
    kinds = set()
    denominator = 0
    rates = []
    for source in sources:
        if source['stratum'] != stratum or source['gen_sha256'] in seen:
            raise ValueError('Wrong stratum or duplicate original GEN exposure')
        seen.add(source['gen_sha256'])
        kind = source['denominator_kind']
        kinds.add(kind)
        if kind == 'fixed_source_trials':
            count, rate = source['full_source_trials'], source['sigma_nd_pb']
        elif kind == 'accepted_gen_events':
            count, rate = source['full_source_events'], source['native_cross_section_pb']
        elif kind == 'conditional_signal_events':
            count, rate = source['full_source_events'], source['physical_dimuon_cross_section_pb']
            record = source['signal_rate_record']
            if (record['full_gen']['accepted_events'] != count or
                    record['full_gen']['sum_weights'] != count or
                    record['physical_selected_cross_sections_pb'][0]['value_pb'] != rate):
                raise ValueError('Conditional signal normalization differs from its full source-rate record')
        else:
            raise ValueError('Unknown original denominator convention')
        if type(count) is not int or count <= 0 or not math.isfinite(rate) or rate <= 0:
            raise ValueError('Invalid original GEN exposure')
        denominator += count
        rates.append((rate, count))
    if len(kinds) != 1 or not rates:
        raise ValueError('Incompatible source estimators cannot be pooled')
    if kinds == {'conditional_signal_events'} and len(rates) != 1:
        raise ValueError('Combine independent signal source banks with their source-aware aggregator before postprocessing')
    class_sources = [s for s in sources if s['stratum'].startswith(('qcd_', 'jpsi_'))]
    class_rate = {}
    if kinds == {'fixed_source_trials'}:
        if any(not math.isclose(r, rates[0][0], rel_tol=1e-12) for r, n in rates):
            raise ValueError('Fixed-trial sources have different ND cross sections')
        rate = rates[0][0]
    elif kinds == {'accepted_gen_events'} and class_sources and (
            len(sources) > 1 or sources[0].get('native_class_normalization') is not None):
        class_rate = native_class_rate(sources)
        rate = class_rate['cross_section_pb']
    else:
        rate = math.fsum(r * n for r, n in rates) / denominator
    return dict(schema='shift-preserved-full-GEN-histogram-v1', stratum=stratum,
                full_source_sumw=denominator, cross_section_pb=rate,
                denominator_kind=next(iter(kinds)), original_sources=sources,
                histogram_weight='native_gen_weight/reco_inclusion_probability',
                no_second_filter_efficiency=True, partial_production=True,
                physics_normalization_ready=all(s.get('physics_normalization_ready', False) for s in sources),
                forced_decay_branching_convention_ready=all(
                    s.get('forced_decay_branching_convention_ready', False) for s in sources),
                **{key: value for key, value in class_rate.items() if key != 'cross_section_pb'})


def selected_rows(source):
    indices, probabilities = source['selected_indices'], source['sampling_probabilities']
    if len(indices) != len(probabilities) or len(set(indices)) != len(indices):
        raise ValueError('Invalid frozen selected indices')
    rows = []
    for index, probability in zip(indices, probabilities):
        if not 0 < probability <= 1:
            raise ValueError('Invalid frozen sampling probability')
        rows.append(dict(event_id=source['event_ids'][index], native_weight=source['weights'][index],
                         sampling_probability=probability))
    return rows


def inspect_nano(path, expected):
    import ROOT
    file = ROOT.TFile.Open(str(path))
    if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unreadable Nano ' + str(path))
    seen, weights = [], []
    try:
        tree = file.Get('Events')
        for event in tree:
            identity = (int(event.run), int(event.luminosityBlock), int(event.event))
            if identity not in expected:
                raise ValueError('Nano event outside the frozen source selection')
            row = expected[identity]
            native, probability = row['native_weight'], row['sampling_probability']
            for field, reference in [('shiftOriginalGenWeight', native),
                    ('shiftSamplingProbability', probability), ('shiftSamplingWeight', 1 / probability),
                    ('shiftSamplingGenWeight', native / probability)]:
                if tree.GetLeaf(field).GetTypeName() != 'Double_t':
                    raise ValueError('Sampling fields must remain Double')
                if not math.isclose(float(getattr(event, field)), reference, rel_tol=1e-10, abs_tol=1e-300):
                    raise ValueError('Frozen W/p changed: ' + field)
            seen.append(identity)
            weights.append(native / probability)
        if len(seen) != len(set(seen)):
            raise ValueError('Duplicate original event')
        return seen, math.fsum(weights), math.fsum(w * w for w in weights)
    finally:
        file.Close()


def publish(path, directory):
    """Successful-close transfer followed by an independent SHA-256 readback."""
    path = Path(path)
    destination = directory.rstrip('/') + '/' + path.name
    env = transfer_env()
    subprocess.run(['/usr/bin/xrdfs', 'root://eosuser.cern.ch', 'mkdir', '-p', directory],
                   check=True, env=env, timeout=60)
    existing = subprocess.run(['/usr/bin/xrdfs', 'root://eosuser.cern.ch', 'stat', destination],
                              capture_output=True, env=env, timeout=60)
    if existing.returncode == 0:
        raise ValueError('Refuse to overwrite an existing publication: ' + destination)
    subprocess.run(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32', str(path),
                    'root://eosuser.cern.ch/' + destination], check=True, env=env, timeout=900)
    readback = path.with_suffix(path.suffix + '.readback')
    download(destination, readback, digest(path))
    if readback.stat().st_size != path.stat().st_size:
        raise ValueError('Publication size mismatch')
    readback.unlink()
    return dict(path=destination, sha256=digest(path), bytes=path.stat().st_size)


def hadd_command(output, inputs, maximum_open_files=50):
    if type(maximum_open_files) is not int or not 2 <= maximum_open_files <= 1000:
        raise ValueError('Invalid merger open-file limit')
    return ['hadd', '-fk', '-n', str(maximum_open_files), str(output), *map(str, inputs)]


def run(request_path, maximum_open_files=50, merge_timeout=3600):
    request = read(request_path)
    assert request['schema'] in ('shift-smart-SM-postprocessing-v1','shift-production-postprocessing-v1')
    assert request['complete_source_selections_only'] is True
    expected = {}
    for source in request['selected_sources']:
        for row in selected_rows(source):
            identity = tuple(row['event_id'])
            if identity in expected:
                raise ValueError('Repeated original event between sources')
            expected[identity] = row
    if not expected:
        raise ValueError('A zero-selected bin needs no invented Nano file')
    metadata = exposure(request['normalization_sources'], request['stratum'])
    work = Path.cwd()
    capsule = work / 'histogram_runtime.tar.gz'
    download(request['histogram_runtime']['path'], capsule, request['histogram_runtime']['sha256'])
    with tarfile.open(capsule) as archive:
        for member in archive.getmembers():
            if member.name.startswith('/') or '..' in Path(member.name).parts:
                raise ValueError('Unsafe runtime member')
        archive.extractall(work)
    capsule.unlink()
    payload = work / 'payload'
    for name, record in read(payload / 'manifest.json').items():
        if digest(payload / name) != record['sha256']:
            raise ValueError('Frozen histogram runtime member changed')
    inputs, seen = [], set()
    for i, row in enumerate(request['nano_inputs']):
        path = work / f'input_{i:05d}.root'
        download(row['path'], path, row['sha256'])
        identities, _, _ = inspect_nano(path, expected)
        if len(identities) != row['events'] or seen.intersection(identities):
            raise ValueError('Missing or repeated Nano original')
        seen.update(identities)
        inputs.append(path)
    if seen != set(expected):
        raise ValueError('Source selection is not completely reconstructed')
    merged = work / 'nano_merged.root'
    with (work / 'merge.log').open('w') as log:
        subprocess.run(hadd_command(merged, inputs, maximum_open_files),
                       check=True, stdout=log, stderr=subprocess.STDOUT, timeout=merge_timeout)
    from shift_nano_integrity import compare_all_branches
    integrity = compare_all_branches(inputs, merged)
    _, sumw, sumw2 = inspect_nano(merged, expected)
    environment = transfer_env()
    environment.update(PYTHONHOME=str(payload / 'runtime'), ROOTSYS=str(payload / 'runtime'),
        PYTHONPATH=str(payload / 'configs') + ':' + str(payload / 'bin'),
        LD_LIBRARY_PATH=str(payload / 'runtime/lib64') + ':' + str(payload / 'runtime/lib64/root'),
        PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    overlay = work / 'histogram_config.py'
    overlay.write_text("from shift_histogrammer_config import *\n"
                       "enableBdtDiagnostics = False\nweightsBranchName = 'shiftSamplingGenWeight'\n")
    hist = work / 'histograms.root'
    with (work / 'histogram.log').open('w') as log:
        subprocess.run([str(payload / 'bin/shift_histogrammer'), '--config', str(overlay),
                        '--input_path', str(merged), '--output_hists_path', str(hist)],
                       env=environment, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=3600)
    import ROOT
    root = ROOT.TFile.Open(str(hist), 'UPDATE')
    if not root or root.IsZombie():
        raise ValueError('Missing standard histogram output')
    try:
        raw = root.Get('rawEventsCutFlow')
        event = root.Get('event/Event_nShiftMuon')
        if not raw or int(round(raw.GetBinContent(1))) != len(expected) or not event:
            raise ValueError('Histogram event exposure changed')
        values = math.fsum(float(event.GetBinContent(i)) for i in range(event.GetNcells()))
        if not math.isclose(values, sumw, rel_tol=1e-9, abs_tol=1e-300):
            raise ValueError('Histogram W/p sum changed')
        validate_dimuon_histograms(root)
        ROOT.TObjString(json.dumps(metadata, sort_keys=True)).Write('ShiftFullGenNormalization')
    finally:
        root.Close()
    bound = request.get('maximum_publication_bytes', 256000000)
    if merged.stat().st_size + 2 * hist.stat().st_size + 1048576 > bound:
        raise ValueError('Merged outputs exceed the declared publication reservation')
    # One independently validated per-bin snapshot is already the bin's merged
    # histogram. Publish both standard stages, with the plotter input last.
    published = {}
    name = request['snapshot_name'] + '.root'
    merged.rename(work / name)
    published['merged_nano'] = publish(work / name, request['sample_directory'] + '/nanoAOD_merged')
    (work / name).unlink()
    hist.rename(work / name)
    published['histogram'] = publish(work / name, request['sample_directory'] + '/histograms')
    published['merged_histogram'] = publish(work / name, request['sample_directory'] + '/histograms_merged')
    schema = ('shift-production-postprocessing-complete-v1' if request['schema']=='shift-production-postprocessing-v1'
              else 'shift-smart-SM-postprocessing-complete-v1')
    receipt = dict(schema=schema, complete=True,
        campaign=request['campaign'], stratum=request['stratum'],
        request_sha256=digest(request_path), events=len(expected), sumw=sumw, sumw2=sumw2,
        source_ids=[s['source_id'] for s in request['normalization_sources']],
        full_original_exposure=metadata, original_nano_inputs=request['nano_inputs'],
        merged_nano_integrity=integrity, published=published,
        original_nano_retired=False, checked_epoch=time.time())
    save(work / 'receipt.json', receipt)
    publish(work / 'receipt.json', request['sample_directory'] + '/metadata/postprocessing/' + request['snapshot_name'])
    save(work / 'result.json', receipt)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('--hadd-open-files', type=int, default=50)
    parser.add_argument('--merge-timeout', type=int, default=3600)
    args = parser.parse_args()
    try:
        run(args.request, args.hadd_open_files, args.merge_timeout)
    except BaseException as error:
        save('result.json', dict(complete=False, error=repr(error), checked_epoch=time.time()))
        raise
