#!/usr/bin/env python3
# Code style: AI-code. AI-generated tracking-free full-GEN gate for frozen SM workers.
"""Exercise the original production converter before spending detector CPU.

The addon is built against hash-bound headers and the exact old SM runtime.
It is installed only in a worker-local runtime. Original GEN identities,
weights, selection masks and the production Generator configuration stay intact.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


LIBRARIES = ('libSimG4CoreGenerators.so', 'libSimG4CoreApplication.so',
             'pluginSimG4CoreApplicationPlugins.so')
PLUGIN = 'pluginConfigurationShiftSMKinematicsValidationPlugins.so'
CACHE_LINES = [PLUGIN+' ShiftSMPrimaryKinematicsPreflight CMS%EDM%Framework%Module',
               PLUGIN+' ShiftSMPrimaryKinematicsPreflight CMS%EDM%Framework%ParameterSet%Description']


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    with Path(path).open('rb') as stream:
        return json.loads(b''.join(iter(lambda: stream.read(1048576), b'')))


def save(path, value):
    temporary = Path(str(path)+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def validate_request(request):
    direct = request.get('schema') == 'shift-SM-primary-preflight-request-v1'
    streamed = (request.get('schema') == 'shift-stream-payload-v1'
                and request.get('stage') == 'sm_primary_preflight'
                and request.get('environment') == 'cmssw')
    if not (direct or streamed):
        raise ValueError('Unsupported SM preflight request or streaming stage/environment')
    return request


def tools_file(tools, relative):
    candidate = Path(relative)
    root = Path(tools).resolve()
    if candidate.is_absolute() or '..' in candidate.parts:
        raise ValueError('Addon paths must stay inside frozen tools')
    result = (root/candidate).resolve()
    if not result.is_relative_to(root) or not result.is_file():
        raise ValueError('Missing or external addon file')
    return result


def validate_source(source):
    descriptor = source['source_descriptor']
    identities = source['full_event_ids']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', source['source_id']):
        raise ValueError('Invalid source identifier')
    if (not identities or identities != descriptor['event_ids']
            or len(set(map(tuple, identities))) != len(identities)
            or any(len(row) != 3 or any(type(x) is not int or x <= 0 for x in row) for row in identities)):
        raise ValueError('Preflight requires the complete ordered original GEN census, not selected offsets')
    if (source['gen_path'] != descriptor['gen'] or source['gen_sha256'] != descriptor['gen_sha256']
            or not re.fullmatch(r'[0-9a-f]{64}', source['gen_sha256'])):
        raise ValueError('Prepared GEN differs from its frozen source descriptor')
    weights = descriptor['weights']
    if len(weights) != len(identities) or any(not math.isfinite(x) for x in weights):
        raise ValueError('Native weights must cover all original GEN events')
    if 'production_selection' not in descriptor:
        raise ValueError('Require the unchanged production material-transport contract')
    return descriptor


def check_libraries(release, receipt):
    if set(receipt['old_libraries']) != set(LIBRARIES):
        raise ValueError('Require the complete original three-library transport build')
    directory = Path(release)/'lib'/os.environ.get('SCRAM_ARCH', 'el9_amd64_gcc13')
    for name in LIBRARIES:
        expected = receipt['old_libraries'][name]
        path = directory/name
        if path.stat().st_size != expected['bytes'] or sha(path) != expected['sha256']:
            raise ValueError('Frozen runtime library differs: '+name)
    return directory


def install_addon(request, tools):
    scratch = os.environ.get('_CONDOR_SCRATCH_DIR')
    release = os.environ.get('CMSSW_BASE')
    if not scratch or not release:
        raise ValueError('An explicit worker-local scratch runtime is required')
    release, scratch = Path(release).resolve(), Path(scratch).resolve()
    local_roots = (Path('/tmp'), Path('/var/tmp'), Path('/pool/condor'))
    if (not any(scratch.is_relative_to(root) for root in local_roots)
            or release == scratch or not release.is_relative_to(scratch)):
        raise ValueError('Refuse to alter a nonlocal or shared CMSSW runtime')
    addon = request['preflight_addon']
    receipt_path = tools_file(tools, addon['build_receipt']['path'])
    if sha(receipt_path) != addon['build_receipt']['sha256']:
        raise ValueError('Preflight build receipt changed')
    receipt = read(receipt_path)
    if receipt['runtime_sha256'] != request['runtime_sha256']:
        raise ValueError('Addon was built for a different original runtime')
    if read(Path(tools)/'runtimes.json')['cmssw']['sha256'] != request['runtime_sha256']:
        raise ValueError('Requested runtime differs from the frozen worker runtime')
    if receipt.get('addon_plugin_cache_lines') != CACHE_LINES:
        raise ValueError('Unexpected addon plugin registrations')
    plugin = tools_file(tools, addon['path'])
    if plugin.name != PLUGIN or sha(plugin) != addon['sha256'] or sha(plugin) != receipt['plugin_sha256']:
        raise ValueError('Preflight addon binary changed')
    library = check_libraries(release, receipt)
    if (library/PLUGIN).exists() and sha(library/PLUGIN) != addon['sha256']:
        raise ValueError('A different addon already exists in this worker runtime')
    shutil.copyfile(plugin, library/PLUGIN)
    if sha(library/PLUGIN) != addon['sha256']:
        raise ValueError('Worker-local addon copy differs')
    # edmPluginRefresh did not discover this addon in the old capsule. Reuse
    # only its two qualified SCRAM-produced lines and keep all old registrations.
    cache = library/'.edmplugincache'
    original = cache.read_text()
    addition = [line for line in CACHE_LINES if line not in original.splitlines()]
    if addition:
        cache.write_text(original+('' if original.endswith('\n') else '\n')+'\n'.join(addition)+'\n')
    check_libraries(release, receipt)
    return receipt


def build_config(request_path, source_id, local, output, tools):
    """Run in a fresh CMS interpreter, using the frozen production builder."""
    request = read(request_path)
    source = next(row for row in request['sources'] if row['source_id'] == source_id)
    descriptor = validate_source(source)
    sys.path.insert(0, str(Path(tools)/'scripts'))
    from run_shift_gen_to_nano import build_config as production_config
    import FWCore.ParameterSet.Config as cms
    process = production_config(Path(tools)/'templates/step1.py', 1, Path(local),
        Path(output).with_suffix('.unused.root'), 0, len(source['full_event_ids']),
        391713, True, False, None, False, None, descriptor['production_selection'])
    generator = process.g4SimHits.Generator.clone()
    if not generator.TrackAllHadronDecays.value() or generator.MaxUntrackedHadronFlightMm.value() != 1.e-5:
        raise ValueError('Production primary-converter material settings differ')
    process.kinematicsPreflight = cms.EDAnalyzer('ShiftSMPrimaryKinematicsPreflight',
        src=generator.HepMCProductLabel, generator=generator,
        output=cms.string(str(Path(output).with_suffix('.json'))))
    process.preflight_step = cms.Path(process.generatorSmeared+process.kinematicsPreflight)
    process.schedule = cms.Schedule(process.preflight_step)
    process.MessageLogger.cerr.FwkReport.reportEvery = cms.untracked.int32(1000)
    Path(output).write_text(process.dumpPython())
    Path(output).with_suffix('.generator.py').write_text(generator.dumpPython())


def verify_originals(path, source):
    from DataFormats.FWLite import Events, Handle
    expected = validate_source(source)
    identities, weights = [], []
    for event in Events(str(path)):
        auxiliary = event.eventAuxiliary()
        identities.append([int(auxiliary.run()), int(auxiliary.luminosityBlock()), int(auxiliary.event())])
        handle = Handle('GenEventInfoProduct')
        event.getByLabel('generator', handle)
        if not handle.isValid():
            raise ValueError('Original GEN has no native event weight')
        weights.append(float(handle.product().weight()))
    references = expected['weights']
    if identities != source['full_event_ids'] or any(not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-300)
            for a, b in zip(weights, references)):
        raise ValueError('Full original GEN identities or native weights changed')


def run(request_path, tools):
    request_path = Path(request_path).resolve()
    request = validate_request(read(request_path))
    names = [row['source_id'] for row in request['sources']]
    if not names or len(names) != len(set(names)):
        raise ValueError('Require unique nonempty full source cohorts')
    report = dict(schema='shift-SM-full-GEN-primary-preflight-v1', complete=False,
        request_sha256=sha(request_path), runtime_sha256=request['runtime_sha256'],
        detector_tracking_performed=False, sources={}, helper_sha256=sha(Path(__file__)),
        physics_valid=False, normalization_ready=False)
    save('report.json', report)
    try:
        receipt = install_addon(request, tools)
        report['analyzer_sha256'] = receipt['plugin_sha256']
        report['analyzer_source_sha256'] = receipt['plugin_source_sha256']
        report['original_library_fingerprints'] = receipt['old_libraries']
        for number, source in enumerate(request['sources']):
            validate_source(source)
            local = Path.cwd()/f'original_{number:04d}.root'
            url = source['gen_path']
            if url.startswith('/eos/'):
                url = 'root://eosuser.cern.ch/'+url
            subprocess.run(['xrdcp', '--silent', '--force', '--cksum', 'adler32', url, str(local)],
                           check=True, timeout=900)
            if sha(local) != source['gen_sha256']:
                raise ValueError('Prepared original GEN SHA changed')
            verify_originals(local, source)
            cfg = Path.cwd()/f'preflight_{number:04d}.py'
            command = [sys.executable, str(Path(__file__).resolve()), str(request_path),
                       '--build-config', source['source_id'], str(local), str(cfg)]
            with cfg.with_suffix('.configuration.log').open('w') as log:
                subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=600)
            with cfg.with_suffix('.cmsRun.log').open('w') as log:
                subprocess.run(['cmsRun', str(cfg)], check=True, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            native = read(cfg.with_suffix('.json'))
            if native.get('complete') is not True or native.get('event_ids') != source['full_event_ids']:
                raise ValueError('Actual primary converter did not cover the full original ordered census')
            if native.get('events') != len(source['full_event_ids']) or native.get('detector_tracking_performed') is not False:
                raise ValueError('Unexpected primary preflight count or tracking')
            if sha(local) != source['gen_sha256']:
                raise ValueError('Preflight changed its original GEN input')
            check_libraries(Path(os.environ['CMSSW_BASE']), receipt)
            report['sources'][source['source_id']] = dict(complete=True, full_original_events=native['events'],
                full_ordered_event_ids=native['event_ids'], prepared_GEN_sha256=source['gen_sha256'],
                full_ordered_event_ids_sha256=hashlib.sha256(json.dumps(native['event_ids'], separators=(',', ':')).encode()).hexdigest(),
                source_descriptor_sha256=hashlib.sha256(json.dumps(source['source_descriptor'], sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                original_production_selection=source['source_descriptor']['production_selection'],
                converted_particles=native['converted_particles'], native_result_sha256=sha(cfg.with_suffix('.json')),
                resolved_config_sha256=sha(cfg), generator_PSet_sha256=sha(cfg.with_suffix('.generator.py')))
            save('report.json', report)
            local.unlink()
        report['complete'] = True
        save('report.json', report)
    except Exception as error:
        report['error'] = repr(error)
        report['failed_source_id'] = source['source_id'] if 'source' in locals() else None
        save('report.json', report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('--build-config', nargs=3, metavar=('SOURCE', 'GEN', 'OUTPUT'))
    args = parser.parse_args()
    tools = Path(__file__).resolve().parents[1]
    if args.build_config:
        build_config(args.request, *args.build_config, tools)
    else:
        run(args.request, tools)


if __name__ == '__main__':
    main()
