#!/usr/bin/env python3
# Code style: AI-code. AI-generated ordinary mu-enriched source accounting.
"""Audit ordinary CP5/MPI source trials separately from the saved muon subset."""
import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import re

from audit_shift_weighted_gen import (_keyword, _strings, _value, close,
                                      CP5_SETTINGS, read_edm, SOURCE_MODEL_SHA256)
from generation_publication import sha256
from qcd_mu_enrichment import ordinary_source_config, PROFILE
from soft_mpi_model import process_statistics


def validate_config(source, request):
    """Check the declared profile, then the unchanged ordinary source and hook."""
    ordinary = ordinary_source_config(source, {'sample': 'qcd'})
    modules = {}
    for node in ast.parse(ordinary).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'process':
                    if target.attr in modules:
                        raise ValueError('Duplicate resolved module: '+target.attr)
                    modules[target.attr] = node.value
    generator = modules['generator']
    if _value(generator.args[0]) != 'Pythia8GeneratorFilter':
        raise ValueError('Unexpected generator')
    pythia = _keyword(generator, 'PythiaParameters')
    names = _strings(_keyword(pythia, 'parameterSets'))
    if len(names) != len(set(names)) or 'pythia8CP5Settings' not in names:
        raise ValueError('Missing CP5 or duplicated settings')
    parameters, settings = [], {}
    for name in names:
        for text in _strings(_keyword(pythia, name)):
            key, value = re.split(r'\s*=\s*|\s+', text.strip(), maxsplit=1)
            parameters.append([key, value])
            settings[key] = value
    digest = hashlib.sha256(json.dumps(parameters, separators=(',', ':')).encode()).hexdigest()
    if digest != SOURCE_MODEL_SHA256:
        raise ValueError('Ordered ordinary CP5/MPI source changed')
    for key, expected in CP5_SETTINGS.items():
        actual = settings.get(key)
        try:
            equal = actual is not None and float(actual) == float(expected)
        except ValueError:
            equal = actual == expected
        if not equal:
            raise ValueError('Changed CP5 setting: '+key)
    hooks = _keyword(generator, 'UserCustomization')
    if len(hooks.args) != 1:
        raise ValueError('Expected one ordinary event-class hook')
    hook = hooks.args[0]
    wanted = dict(pluginName='ShiftMpiEventClassHook', eventClass='qcd',
                  pTHatMin=request['bounds'][0], pTHatMax=request['bounds'][1])
    if {key.arg for key in hook.keywords} != set(wanted):
        raise ValueError('Unexpected ordinary hook parameters')
    if any(_value(_keyword(hook, key)) != value for key, value in wanted.items()):
        raise ValueError('Ordinary hook/bin changed')
    for key, value in [('firstRun', request['run']), ('firstEvent', 1), ('firstLuminosityBlock', 1)]:
        if _value(_keyword(modules['source'], key)) != value:
            raise ValueError('Changed event namespace')
    for key in ('numberEventsInRun', 'numberEventsInLuminosityBlock'):
        if _value(_keyword(modules['source'], key)) <= request['trials']:
            raise ValueError('Source can split requested framework slots')
    if _value(_keyword(modules['maxEvents'], 'input')) != request['trials']:
        raise ValueError('Wrong framework request')
    for key in ('numberOfThreads', 'numberOfStreams'):
        if _value(_keyword(modules['options'], key)) != 1:
            raise ValueError('Expected one thread and stream')
    seed = _keyword(_keyword(modules['RandomNumberGeneratorService'], 'generator'), 'initialSeed')
    if _value(seed) != request['seed']:
        raise ValueError('Changed generator seed')
    return digest


def audit(input_path, config_path, log_path, request):
    from DataFormats.FWLite import Handle, Lumis
    digest = validate_config(Path(config_path).read_text(), request)
    filters = []
    for lumi in Lumis(str(input_path)):
        handle = Handle('GenFilterInfo')
        lumi.getByLabel('genFilterEfficiencyProducer', handle)
        if not handle.isValid():
            raise ValueError('Missing external muon-filter accounting')
        info = handle.product()
        filters.append(dict(total_positive=int(info.numTotalPositiveEvents()),
            total_negative=int(info.numTotalNegativeEvents()),
            pass_positive=int(info.numPassPositiveEvents()), pass_negative=int(info.numPassNegativeEvents()),
            sum_weights=float(info.sumWeights()), sum_weights2=float(info.sumWeights2()),
            sum_pass_weights=float(info.sumPassWeights()), sum_pass_weights2=float(info.sumPassWeights2())))
    if not filters or any(row['total_negative'] or row['pass_negative'] for row in filters):
        raise ValueError('Missing or negative filter exposure')
    before = sum(row['total_positive'] for row in filters)
    saved = sum(row['pass_positive'] for row in filters)
    if before != request['trials'] or not 0 <= saved <= before:
        raise ValueError('Incomplete framework/source exposure')
    close(math.fsum(row['sum_weights'] for row in filters), before, 'Unit filter input weights')
    close(math.fsum(row['sum_weights2'] for row in filters), before, 'Squared unit filter input weights')
    close(math.fsum(row['sum_pass_weights'] for row in filters), saved, 'Saved filter weights')
    close(math.fsum(row['sum_pass_weights2'] for row in filters), saved, 'Saved squared filter weights')
    records, _, _, native, beams = read_edm(input_path, saved, require_muon=True, weighted=False)
    ids = set()
    lower, upper = request['bounds']
    for row in records:
        identity = tuple(row['event_id'])
        if (identity in ids or identity[0] != request['run'] or identity[1] != 1
                or not 1 <= identity[2] <= request['trials']):
            raise ValueError('Duplicate or out-of-budget original identity')
        ids.add(identity)
        if row['weight'] != 1. or row['hepmc_weight'] != 1. or row['process'] != 101 or row['direct_scales']:
            raise ValueError('Ordinary source weight or complementary ownership changed')
        if row['pthat'] < lower or (upper != -1 and row['pthat'] >= upper):
            raise ValueError('Original outside half-open hardest-MPI bin')
    statistics = process_statistics(log_path)
    if statistics['accepted'] != before or not math.isclose(statistics['sigma_pb'], native['internal_xsec_pb'], rel_tol=.001):
        raise ValueError('Pythia source count/cross section differs from native run')
    tried = statistics['tried']
    sigma_nd_mb = native['internal_xsec_pb']/1.e9*tried/before
    selected_sigma = sigma_nd_mb*saved/tried
    return dict(schema='shift-mu-enriched-ordinary-semantic-v1', complete=True, healthy=True,
        generator_profile=PROFILE, sample='qcd', bounds=request['bounds'],
        requested_framework_slots=request['trials'], source_accepted=before,
        full_source_trial_denominator=tried, events=saved, event_ids=[r['event_id'] for r in records],
        weights=[r['weight'] for r in records], scale=[r['pthat'] for r in records],
        charged=[r['charged'] for r in records], sumw=saved, sumw2=saved,
        sigma_nd_mb=sigma_nd_mb, sigma_mb=selected_sigma,
        error_poisson_mb=sigma_nd_mb*math.sqrt(saved)/tried,
        native_generator_run_info=native, pythia_process_statistics=statistics,
        filter_records=filters, raw_filter_efficiency=saved/before,
        no_second_filter_efficiency=True, source_model_settings_sha256=digest,
        normalization='native source sigma * saved/source accepted; pool source Pythia trials across chunks',
        beams=beams, input_sha256=sha256(input_path), config_sha256=sha256(config_path),
        physics_valid=False, normalization_ready=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'config', 'log', 'request', 'output'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    result = audit(args.input, args.config, args.log, request)
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n')
