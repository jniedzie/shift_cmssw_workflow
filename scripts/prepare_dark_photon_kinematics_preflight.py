#!/usr/bin/env python3
# Code style: AI-code. AI-generated sealed full-GEN converter preflight preparation.
"""Run the actual replay and primary converter on the complete original GEN census."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def pin(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': sha(path)}


def read_pin(row):
    path = Path(row['path'])
    if sha(path) != row['sha256']:
        raise ValueError('Changed preflight dependency: ' + str(path))
    return json.loads(path.read_text())


def scoped_points(plan, point_scope=None):
    """Select whole physical points without changing their original GEN census."""
    points=[row['point'] for row in plan['points']]
    if len(points)!=len(set(points)):
        raise ValueError('Original plan has duplicate point names')
    if point_scope is None:
        return plan['points']
    if (not isinstance(point_scope,list) or not point_scope
            or any(not isinstance(name,str) for name in point_scope)
            or len(set(point_scope))!=len(point_scope) or not set(point_scope)<=set(points)):
        raise ValueError('Explicit preflight point scope must be unique original plan points')
    return [row for row in plan['points'] if row['point'] in point_scope]


def prepare(production, new_profile_pin, output_dir, *, point_scope=None):
    """Prepare a standalone worker; return the pinned config and submit paths."""
    production, output = Path(production).resolve(), Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError('Preserve existing preflight attempt: ' + str(output))
    profile = read_pin(new_profile_pin)
    plan = json.loads((production/'plan.json').read_text())
    inventory = json.loads((production/'gen_inventory.json').read_text())
    if inventory.get('complete') is not True:
        raise ValueError('Require complete original full-GEN inventory')
    if set(inventory['points']) != {p['point'] for p in plan['points']}:
        raise ValueError('Original GEN and plan point sets differ')
    scoped_points(plan,point_scope)
    output.mkdir(parents=True)
    helpers = output/'helpers'; helpers.mkdir()
    for name in (Path(__file__).name, 'dark_photon_detector_runtime.py'):
        shutil.copy2(Path(__file__).with_name(name), helpers/name)
    config = {'schema': 'shift-primary-kinematics-preflight-preparation-v1',
              'production': str(production), 'output': str(output),
              'original_plan': pin(production/'plan.json'),
              'original_seal': pin(production/'seal.json'),
              'inventory': pin(production/'gen_inventory.json'),
              'replacement_profile': new_profile_pin,
              'replacement_runtime_sha256': profile['runtime_sha256'],
              'helpers': [pin(p) for p in sorted(helpers.iterdir())]}
    if point_scope is not None:
        config['point_scope']=sorted(point_scope)
    cfg = output/'preflight.json'; cfg.write_text(json.dumps(config, indent=2)+'\n')
    command = ['/usr/bin/env', '-u', 'PYTHONHOME', '-u', 'PYTHONPATH', '/usr/bin/python3',
               str(helpers/'dark_photon_detector_runtime.py'), '--profile', new_profile_pin['path'],
               '--entry', str(helpers/Path(__file__).name), '--', '--scan', str(cfg)]
    script = output/'worker.sh'
    script.write_text('#!/bin/bash\n# Code style: AI-code. AI-generated frozen preflight entry.\nset -euo pipefail\ncd '+shlex.quote(str(output))+'\nexec '+shlex.join(command)+'\n')
    script.chmod(0o700); subprocess.run(['bash', '-n', str(script)], check=True)
    submit = output/'worker.sub'
    campaign=plan.get('campaign') or '_'.join(p['point'] for p in scoped_points(plan,point_scope))
    submit.write_text('universe = vanilla\nexecutable = '+str(script)+'\ninitialdir = '+str(output)+
        '\noutput = worker.out\nerror = worker.err\nlog = events.log\npriority = 1000000\nrequest_cpus = 1\nrequest_memory = 4000\nrequest_disk = 4000000\n+MaxRuntime = 7200\n+SendCredential = True\n+JobBatchName = '+json.dumps('SHIFT_DP_'+campaign+'_full_GEN_check')+'\n'
        '+ShiftDPStatisticsTask = '+json.dumps(str(output))+'\n'
        'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)\nperiodic_release = False\nqueue 1\n')
    result = {'complete': True, 'config': pin(cfg), 'worker': pin(script),
              'submit': pin(submit), 'receipt': str(output/'receipt.json')}
    (output/'preparation.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def resolve_point(config_path, name, local, scratch):
    """Resolve one point in a fresh CMS interpreter, as the original chain does."""
    config = json.loads(Path(config_path).read_text())
    plan = read_pin(config['original_plan'])
    inventory = read_pin(config['inventory'])
    point = next(p for p in scoped_points(plan,config.get('point_scope')) if p['point'] == name)
    parent = inventory['points'][name]
    production = Path(config['production']); output = Path(config['output'])
    sources = production/'frozen/sources'; sys.path.insert(0, str(sources))
    spec = importlib.util.spec_from_file_location('original_common', sources/'run_shift_gen_to_nano.py')
    common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)
    import FWCore.ParameterSet.Config as cms
    contract = {'schema':'shift-predefined-dark-photon-transport-v1',
                'mass_gev':point['mass_gev'], 'proper_ctau_mm':point['proper_ctau_mm']}
    # Read the exact production support; never guess a narrower interval.
    original_contract = Path(point['signal_contract'])
    if sha(original_contract) != point['signal_contract_sha256']:
        raise ValueError('Original signal contract changed')
    card = json.loads(original_contract.read_text())
    # run_dark_photon_detector uses the same explicit mass support.
    contract['generated_mass_support_gev'] = card['generated_mass_support_gev']
    selection = point['material_selection']['production_selection']
    process = common.build_config(production/'frozen/templates/step1.py', 1, local,
        Path(scratch)/'unused.root', 0, parent['events'], point['seed'], True,
        False, None, False, contract, selection)
    generator = process.g4SimHits.Generator.clone()
    if (hasattr(process,'MessageLogger') and hasattr(process.MessageLogger,'cerr')
            and hasattr(process.MessageLogger.cerr,'FwkReport')):
        process.MessageLogger.cerr.FwkReport.reportEvery=cms.untracked.int32(1000)
    process.kinematicsPreflight = cms.EDAnalyzer('ShiftPrimaryKinematicsPreflight',
        src=generator.HepMCProductLabel, generator=generator,
        massGeV=cms.double(point['mass_gev']), properCTauMm=cms.double(point['proper_ctau_mm']),
        output=cms.string(str(output/(name+'_converter.json'))))
    process.kinematics_step = cms.Path(process.generatorSmeared + process.shiftDarkPhotonHepMC + process.kinematicsPreflight)
    process.schedule = cms.Schedule(process.kinematics_step)
    cfg = output/(name+'_cfg.py'); cfg.write_text(process.dumpPython())


def point_config_command(config_path, name, local, scratch):
    # CMSSW customizations mutate imported ESProducers. Never reuse those
    # Python module objects across points, masses or model configurations.
    return [sys.executable, str(Path(__file__).resolve()), '--build-point',
            str(config_path), name, str(local), str(scratch)]


def native_point_environment(profile,manifest,release,gen_sha256):
    native_env=dict(os.environ)
    names=('SHIFT_RECORDED_PRIMARY_AUTHORITY','SHIFT_RECORDED_PRIMARY_AUTHORITY_SHA256',
           'SHIFT_RECORDED_PRIMARY_GEN_SHA256')
    for name in names:native_env.pop(name,None)
    if profile.get('recorded_primary_authority') is not None:
        helper=Path(release).parent/'workflow_helpers/dark_photon_stage_budget.py'
        if sha(helper)!=manifest['files']['workflow_helpers/dark_photon_stage_budget.py']['sha256']:
            raise ValueError('Capsule recorded-primary environment helper changed')
        spec=importlib.util.spec_from_file_location('_dp_capsule_primary_authority',helper)
        authority_helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(authority_helper)
        native_env.update(authority_helper.recorded_primary_environment(profile,gen_sha256))
    return native_env


def scan(config_path):
    config = json.loads(Path(config_path).read_text())
    for row in config['helpers']:
        if sha(row['path']) != row['sha256']:
            raise ValueError('Changed frozen preflight helper')
    plan = read_pin(config['original_plan']); read_pin(config['original_seal'])
    inventory = read_pin(config['inventory']); profile = read_pin(config['replacement_profile'])
    manifest = read_pin(profile['manifest'])
    release = Path(os.environ['CMSSW_BASE'])
    converter_names = ['SimG4Core/Generators/src/Generator.cc',
                       'SimG4Core/Generators/src/Generator3.cc',
                       'SimG4Core/Generators/interface/RecordedPrimaryMomentum.h']
    converter = {}
    for name in converter_names:
        converter[name] = sha(release/'src'/name)
        if converter[name] != manifest['files']['CMSSW_17_0_0_pre4/src/'+name]['sha256']:
            raise ValueError('Actual converter differs from candidate profile')
    output = Path(config['output'])
    report = {'schema': 'shift-production-primary-kinematics-preflight-v1', 'complete': False,
              'original_plan_sha256': config['original_plan']['sha256'],
              'original_seal_sha256': config['original_seal']['sha256'],
              'original_inventory_sha256': config['inventory']['sha256'],
              'replacement_runtime_sha256': profile['runtime_sha256'],
              'converter_source_sha256': hashlib.sha256(json.dumps(converter, sort_keys=True).encode()).hexdigest(),
              'points': {}}
    if config.get('point_scope') is not None:
        report['point_scope']=sorted(config['point_scope'])
    configurations = {}
    from DataFormats.FWLite import Events
    try:
        for point in scoped_points(plan,config.get('point_scope')):
            name = point['point']; parent = inventory['points'][name]
            with tempfile.TemporaryDirectory(prefix='dp-kinematics-') as scratch:
                local = Path(scratch)/'gen.root'
                subprocess.run(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32',
                                'root://eosuser.cern.ch/'+parent['gen_path'], str(local)], check=True, timeout=900)
                if sha(local) != parent['gen_sha256']:
                    raise ValueError('Original canonical GEN SHA changed: '+name)
                identities = []
                for event in Events(str(local)):
                    aux = event.eventAuxiliary()
                    identities.append([int(aux.run()), int(aux.luminosityBlock()), int(aux.event())])
                if identities != parent['event_ids'] or len(identities) != parent['events']:
                    raise ValueError('Full original GEN identity census changed: '+name)
                with (output/(name+'_config.log')).open('w') as config_log:
                    subprocess.run(point_config_command(config_path, name, local, scratch),
                                   check=True, stdout=config_log, stderr=subprocess.STDOUT, timeout=900)
                cfg = output/(name+'_cfg.py')
                configurations[name] = sha(cfg)
                native_env=native_point_environment(profile,manifest,release,parent['gen_sha256'])
                with (output/(name+'_cmsRun.log')).open('w') as log:
                    subprocess.run(['cmsRun', str(cfg)], cwd=scratch, stdout=log, stderr=subprocess.STDOUT,
                                   check=True, timeout=6000,env=native_env)
                native = json.loads((output/(name+'_converter.json')).read_text())
                if native.get('complete') is not True or native['events'] != parent['events']:
                    raise ValueError('Native converter did not cover full GEN census: '+name)
                report['points'][name] = {'gen_sha256': parent['gen_sha256'],
                    'event_ids_sha256': hashlib.sha256(json.dumps(identities,separators=(',',':')).encode()).hexdigest(),
                    'full_count':parent['events'], 'converted_count':native['events'],
                    'native_receipt':pin(output/(name+'_converter.json'))}
        report['complete'] = True
    except Exception as error:
        report['error'] = repr(error)
        raise
    finally:
        report['configuration_sha256'] = hashlib.sha256(json.dumps(configurations,sort_keys=True).encode()).hexdigest()
        (output/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan', type=Path)
    parser.add_argument('--build-point', nargs=4, metavar=('CONFIG','POINT','INPUT','SCRATCH'))
    parser.add_argument('--production', type=Path)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--point-scope', nargs='+', metavar='POINT')
    args = parser.parse_args()
    if args.build_point:
        cfg, name, local, scratch = args.build_point
        resolve_point(cfg, name, Path(local), Path(scratch))
    elif args.scan:
        scan(args.scan)
    else:
        print(json.dumps(prepare(args.production, pin(args.profile), args.output, point_scope=args.point_scope), indent=2))


if __name__ == '__main__':
    main()
