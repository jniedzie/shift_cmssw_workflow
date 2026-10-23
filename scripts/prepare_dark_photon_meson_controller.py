#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable meson controller rollover helper.
"""Freeze scheduling/append-only reservation changes; never submit or edit sources."""
import argparse
import copy
import json
from pathlib import Path
import shlex
import shutil

import run_dark_photon_meson_production as production
from prepare_dark_photon_meson_production import source_execution_limits, detector_point_limit


REPLACEMENT_TOOLS = ('run_dark_photon_meson_production.py', 'run_dark_photon_statistics.py',
                     'prepare_dark_photon_production.py',
                     'prepare_dark_photon_meson_production.py', 'aggregate_dark_photon_samples.py')


def replacement_tool_names(original):
    # Legacy science keeps its qualified detector runner and AST telemetry guard.
    names = REPLACEMENT_TOOLS
    if original.get('detector_runtime') is not None:
        names += ('run_shift_gen_to_nano.py',)
    if original.get('automatic_bulk_admission') is not None:
        names += ('dark_photon_automatic_admission.py',)
    return names


def scheduling_configuration(original, previous, sources, detector_points):
    result = copy.deepcopy(previous)
    result.update(simultaneous_sources=sources, simultaneous_detector_points=detector_points)
    # Keep adopted production dependency names byte-identical to their original
    # preparation. Executing the replacement module does not rename physics.
    if result['tools'] != original['tools']:
        raise ValueError('Scheduling replacement requires the original adoption tools path')
    result['physics_tools'] = original['tools']
    return result


def prepare(directory, original_seal, template, template_seal, output, sources=7, detector_points=35,
            additional_meson_forecasts=(), prior_meson_controller_directory=None,
            meson_peer_controllers=(), reviewed_prior_meson_retirement=None, retired_meson_forecasts=(),
            close_source_admission=False, reviewed_coverage_recovery=None, closed_source_forecasts=(),
            operational_handoffs=False, reviewed_handoff_failures=(),
            regroup_complete_points=False, aggregation_storage_registration=None, historical_anchor_calibration=None):
    directory, template, output = [Path(path).resolve() for path in (directory, template, output)]
    if output.exists():
        raise ValueError('Replacement output already exists; preserve the previous attempt')
    original = production.verify(directory, original_seal)
    first_operation = template == directory
    if first_operation:
        if template_seal != original_seal:
            raise ValueError('First operational controller must retain the original science seal')
        previous = original
        descriptor = (template/'controller.sub').read_text()
        if 'arguments = '+original_seal not in descriptor:
            raise ValueError('Original controller arguments differ from the science seal')
        descriptor = descriptor.replace('arguments = '+original_seal,
                                        'arguments = OPERATIONS_SEAL_DIGEST')
    else:
        previous = production.operational_configuration(original, template/'config.json', template_seal)
        descriptor = (template/'controller.sub.in').read_text()
        if (template/'controller.sub').read_text() != descriptor.replace('OPERATIONS_SEAL_DIGEST', template_seal):
            raise ValueError('Template submit description differs from its sealed template')
    for required in ('+SendCredential = True', '+MaxRuntime = 604800',
                     'on_exit_hold =', 'periodic_release = False'):
        if required not in descriptor:
            raise ValueError('Template is missing its reviewed detached-worker policy: '+required)
    config = scheduling_configuration(original, previous, sources, detector_points)
    if regroup_complete_points:
        if aggregation_storage_registration is None:
            raise ValueError('Final regrouping requires its registered shared-storage reservation')
        path=Path(aggregation_storage_registration).resolve()
        config.update(regroup_complete_points=True,merge_source_histograms=False,aggregate_group_events=4000,
            aggregate_storage_reservation_bytes=22000000000,
            aggregation_storage_registration=dict(path=str(path),sha256=production.durable.sha(path)))
        production.aggregation_reservation(config)
    if close_source_admission:
        config['source_admission_closed']=True
    if reviewed_coverage_recovery is not None:
        path = Path(reviewed_coverage_recovery).resolve()
        config['reviewed_coverage_recovery'] = dict(path=str(path), sha256=production.durable.sha(path))
        production.reviewed_coverage_recovery(config)
    if reviewed_prior_meson_retirement is not None:
        path = Path(reviewed_prior_meson_retirement).resolve()
        config['reviewed_prior_meson_retirement'] = dict(path=str(path), sha256=production.durable.sha(path))
        production.reviewed_meson_retirement(config, config['reviewed_prior_meson_retirement'],
                                            config['prior_meson_publication'], check_queue=False)
    for name, manifest in retired_meson_forecasts:
        matches = [row for row in config.get('additional_reserved_meson_forecasts', []) if row['name'] == name]
        if len(matches) != 1 or matches[0].get('retirement_authority'):
            raise ValueError('Retirement needs one existing uncanceled meson forecast: '+name)
        path = Path(manifest).resolve()
        matches[0]['retirement_authority'] = dict(path=str(path), sha256=production.durable.sha(path))
    for name, manifest in closed_source_forecasts:
        matches = [row for row in config.get('additional_reserved_meson_forecasts', []) if row['name'] == name]
        if len(matches) != 1 or matches[0].get('source_closure_authority') or matches[0].get('retirement_authority'):
            raise ValueError('Source closure needs one existing live meson forecast: '+name)
        path = Path(manifest).resolve()
        matches[0]['source_closure_authority'] = dict(path=str(path), sha256=production.durable.sha(path))
    production.validate_meson_reservation_additions(previous, config)
    if prior_meson_controller_directory is not None:
        locator = str(Path(prior_meson_controller_directory).resolve())
        config.pop('prior_meson_controller_replacement', None)
        config['prior_meson_controller_directory'] = locator
        production.predecessor_controller_configuration(config, locator)
    if additional_meson_forecasts:
        peers = config.setdefault('additional_reserved_meson_forecasts', [])
        added_names = []
        for name, peer_config, forecast in additional_meson_forecasts:
            peer_config, forecast = Path(peer_config).resolve(), Path(forecast).resolve()
            peers.append(dict(name=name,
                peer_configuration=dict(path=str(peer_config), sha256=production.durable.sha(peer_config)),
                forecast=dict(path=str(forecast), sha256=production.durable.sha(forecast))))
            added_names.append(name)
        production.validate_meson_reservation_additions(previous, config)
        config['meson_reservation_registration'] = dict(
            previous_configuration=dict(path=str(template/'config.json'),
                sha256=production.durable.sha(template/'config.json')),
            previous_seal=dict(path=str(template/'seal.json'), sha256=template_seal),
            added_peer_names=added_names)
    if meson_peer_controllers:
        locators = config.setdefault('meson_peer_controller_directories', {})
        for name, owner in meson_peer_controllers:
            locators[name] = str(Path(owner).resolve())
        production.validate_meson_peer_locators(config,
            [row['name'] for row in config.get('additional_reserved_meson_forecasts', [])])
    # Validate scheduling bounds before creating anything, using a small private
    # candidate seal in the new output only after all source authorities passed.
    limits = {'simultaneous_sources': source_execution_limits(original)['maximum_simultaneous_sources'],
              'simultaneous_detector_points': detector_point_limit(original)}
    for key, maximum in limits.items():
        if type(config[key]) is not int or not 1 <= config[key] <= maximum:
            raise ValueError('Scheduling limit exceeds the reviewed grid: '+key)
    output.mkdir(parents=True)
    tools = output/'tools'
    tools.mkdir()
    seal_files = {}
    template_authority = production.read(template/'seal.json')['files']
    for name, digest in template_authority.items():
        path = Path(name)
        if path.parent != template/'tools':
            continue
        target = tools/path.name
        shutil.copy2(path, target)
        seal_files[str(target)] = production.durable.sha(target)
        if seal_files[str(target)] != digest:
            raise ValueError('Verified template tool changed during copying: '+name)
    changed_tools = list(replacement_tool_names(original))
    for name in changed_tools:
        maintained = Path(__file__).with_name(name)
        shutil.copy2(maintained, tools/name)
        seal_files[str(tools/name)] = production.durable.sha(tools/name)
    if operational_handoffs:
        config['operational_handoffs']=dict(directory=str(output/'handoffs'),reviewed_failures={})
        for batch,manifest in reviewed_handoff_failures:
            path=Path(manifest).resolve()
            config['operational_handoffs']['reviewed_failures'][batch]=dict(path=str(path),sha256=production.durable.sha(path))
            seal_files[str(path)]=production.durable.sha(path)
        for batch in config['batches']:
            if batch.get('gen_qualification_only'):
                continue
            paths=production.prepare_handoff_worker(config,batch,output,original_seal)
            for path in paths:
                seal_files[str(path)]=production.durable.sha(path)
    if historical_anchor_calibration is not None:
        source=Path(historical_anchor_calibration).resolve()
        calibration=production.read(source)
        if calibration.get('schema') != 'shift-meson-calibration-v1' or calibration.get('production_mechanism') not in production.MECHANISMS:
            raise ValueError('Historical LAB anchor requires its original meson calibration')
        target=output/'historical_anchor_calibration.json'
        shutil.copy2(source,target)
        if production.durable.sha(target) != production.durable.sha(source):
            raise ValueError('Historical anchor calibration changed while freezing')
        config['historical_anchor_calibration']=dict(path=str(target),sha256=production.durable.sha(target))
        seal_files[str(target)]=production.durable.sha(target)
    production.durable.new(output/'config.json', config)
    replacement = dict(schema='shift-meson-scheduling-rollover-v1',
        original_production=str(directory), original_seal_sha256=original_seal,
        previous_controller=str(template), previous_controller_seal_sha256=template_seal,
        state_and_lock_remain=str(directory), adoption_tools=original['tools'],
        physics_tools=original['tools'], source_jobs_unchanged=True,
        seeds_runs_M_N_conditions_unchanged=True, bulk_canary_gate_preserved=True,
        bulk_storage_gate_worklist_sha256=production.durable.sha(directory/'config.json'),
        simultaneous_sources=sources, simultaneous_detector_points=detector_points,
        detector_workers_per_point=original.get('detector_workers_per_point', 100),
        maximum_detector_workers=original.get('detector_workers_per_point', 100)*detector_points,
        changed_tools=changed_tools, only_configuration_changes=[
            key for key in config if config[key] != previous.get(key)])
    production.durable.new(output/'rollover.json', replacement)
    (output/'controller.sub.in').write_text(descriptor.replace(str(template), str(output)))
    if first_operation:
        quoted = {name:shlex.quote(str(value)) for name,value in dict(
            script=tools/'run_dark_photon_meson_production.py', science=directory,
            configuration=output/'config.json').items()}
        (output/'lcg_controller.sh').write_text(f'''#!/bin/bash
# Code style: AI-code. AI-generated immutable meson operational controller.
set -eo pipefail
cd {quoted['science']}
unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD ROOTSYS
export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
source /cvmfs/sft.cern.ch/lcg/views/LCG_108/x86_64-el9-gcc13-opt/setup.sh
exec python3 {quoted['script']} --directory {quoted['science']} --seal {original_seal} --action controller --controller-config {quoted['configuration']} --controller-seal "$1"
''')
    else:
        (output/'lcg_controller.sh').write_text((template/'lcg_controller.sh').read_text().replace(
            str(template), str(output)))
    (output/'lcg_controller.sh').chmod(0o755)
    for path in (directory/'config.json', directory/'seal.json', template/'seal.json',
                 output/'config.json', output/'rollover.json', output/'lcg_controller.sh', output/'controller.sub.in'):
        seal_files[str(path)] = production.durable.sha(path)
    retirement_pins = [row['retirement_authority'] for row in config.get('additional_reserved_meson_forecasts', [])
                       if row.get('retirement_authority')]
    retirement_pins.extend(row['source_closure_authority'] for row in config.get('additional_reserved_meson_forecasts', [])
                           if row.get('source_closure_authority'))
    if config.get('reviewed_prior_meson_retirement'):
        retirement_pins.append(config['reviewed_prior_meson_retirement'])
    if config.get('reviewed_coverage_recovery'):
        retirement_pins.append(config['reviewed_coverage_recovery'])
    if config.get('aggregation_storage_registration'):
        retirement_pins.append(config['aggregation_storage_registration'])
    for pin in retirement_pins:
        seal_files[str(production.pinned_path(pin))] = pin['sha256']
    production.durable.new(output/'seal.json', dict(schema='shift-meson-controller-seal-v1', files=seal_files))
    digest = production.durable.sha(output/'seal.json')
    (output/'controller.sub').write_text((output/'controller.sub.in').read_text().replace('OPERATIONS_SEAL_DIGEST', digest))
    if operational_handoffs:
        for batch in config['batches']:
            if batch.get('gen_qualification_only'):
                continue
            handoff=output/'handoffs'/batch['batch']
            (handoff/'worker.sub').write_text((handoff/'worker.sub.in').read_text().replace('OPERATIONS_SEAL_DIGEST',digest))
    production.operational_configuration(original, output/'config.json', digest)
    return dict(complete=True, directory=str(output), seal_sha256=digest,
                original_seal_sha256=original_seal, **{key: config[key] for key in limits})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--seal', required=True)
    parser.add_argument('--template', required=True, type=Path)
    parser.add_argument('--template-seal', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--simultaneous-sources', type=int, default=7)
    parser.add_argument('--simultaneous-detector-points', type=int, default=35)
    parser.add_argument('--close-source-admission', action='store_true',
        help='Finish submitted work without starting additional old-model GEN batches')
    parser.add_argument('--reviewed-coverage-recovery', type=Path,
        help='Adopt a separately executed, reviewed read-only coverage audit repair')
    parser.add_argument('--additional-meson-forecast', action='append', nargs=3,
        metavar=('NAME', 'PEER_CONFIG', 'FORECAST'), default=[],
        help='Append a complete pinned peer reservation; repeat for independent meson productions')
    parser.add_argument('--prior-meson-controller-directory', type=Path,
        help='Follow a sealed operational successor of the unchanged pinned predecessor science')
    parser.add_argument('--meson-peer-controller', action='append', nargs=2, default=[],
        metavar=('NAME', 'DIRECTORY'), help='Exact terminal owner of an existing protected meson peer')
    parser.add_argument('--reviewed-prior-meson-retirement', type=Path,
        help='Pinned review of stopped predecessor allocations; does not declare them complete')
    parser.add_argument('--retired-meson-forecast', action='append', nargs=2, default=[],
        metavar=('NAME', 'MANIFEST'), help='Cancel only the reviewed remaining future writes of an existing peer')
    parser.add_argument('--closed-source-forecast', action='append', nargs=2, default=[],
        metavar=('NAME', 'MANIFEST'), help='Cancel reviewed never-submitted sources; protect existing workers')
    parser.add_argument('--operational-handoffs',action='store_true')
    parser.add_argument('--reviewed-handoff-failure',action='append',nargs=2,default=[],metavar=('BATCH','MANIFEST'))
    parser.add_argument('--regroup-complete-points',action='store_true')
    parser.add_argument('--aggregation-storage-registration',type=Path)
    parser.add_argument('--historical-anchor-calibration',type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.directory, args.seal, args.template, args.template_seal,
                             args.output, args.simultaneous_sources, args.simultaneous_detector_points,
                             args.additional_meson_forecast, args.prior_meson_controller_directory,
                             args.meson_peer_controller, args.reviewed_prior_meson_retirement,
                             args.retired_meson_forecast, args.close_source_admission,
                             args.reviewed_coverage_recovery, args.closed_source_forecast,
                             args.operational_handoffs,args.reviewed_handoff_failure,
                             args.regroup_complete_points,args.aggregation_storage_registration,
                             args.historical_anchor_calibration), sort_keys=True))


if __name__ == '__main__':
    main()
