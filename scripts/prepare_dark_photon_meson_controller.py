#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable meson controller rollover helper.
"""Freeze a scheduling-only controller replacement; do not submit or edit sources."""
import argparse
import copy
import json
from pathlib import Path
import shutil

import run_dark_photon_meson_production as production


def scheduling_configuration(original, previous, sources, detector_points):
    result = copy.deepcopy(previous)
    result.update(simultaneous_sources=sources, simultaneous_detector_points=detector_points)
    # Keep adopted production dependency names byte-identical to their original
    # preparation. Executing the replacement module does not rename physics.
    if result['tools'] != original['tools']:
        raise ValueError('Scheduling replacement requires the original adoption tools path')
    result['physics_tools'] = original['tools']
    return result


def prepare(directory, original_seal, template, template_seal, output, sources=7, detector_points=35):
    directory, template, output = [Path(path).resolve() for path in (directory, template, output)]
    if output.exists():
        raise ValueError('Replacement output already exists; preserve the previous attempt')
    original = production.verify(directory, original_seal)
    previous = production.operational_configuration(original, template/'config.json', template_seal)
    descriptor = (template/'controller.sub.in').read_text()
    if (template/'controller.sub').read_text() != descriptor.replace('OPERATIONS_SEAL_DIGEST', template_seal):
        raise ValueError('Template submit description differs from its sealed template')
    for required in ('+SendCredential = True', '+MaxRuntime = 604800',
                     'on_exit_hold =', 'periodic_release = False'):
        if required not in descriptor:
            raise ValueError('Template is missing its reviewed detached-worker policy: '+required)
    config = scheduling_configuration(original, previous, sources, detector_points)
    # Validate scheduling bounds before creating anything, using a small private
    # candidate seal in the new output only after all source authorities passed.
    limits = {'simultaneous_sources': 7, 'simultaneous_detector_points': 35}
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
    maintained = Path(__file__).with_name('run_dark_photon_meson_production.py')
    shutil.copy2(maintained, tools/maintained.name)
    seal_files[str(tools/maintained.name)] = production.durable.sha(tools/maintained.name)
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
        changed_tools=[maintained.name], only_configuration_changes=[
            key for key in limits if config[key] != previous[key]])
    production.durable.new(output/'rollover.json', replacement)
    for name in ('lcg_controller.sh', 'controller.sub.in'):
        text = (template/name).read_text().replace(str(template), str(output))
        (output/name).write_text(text)
    (output/'lcg_controller.sh').chmod(0o755)
    for path in (directory/'config.json', directory/'seal.json', template/'seal.json',
                 output/'config.json', output/'rollover.json', output/'lcg_controller.sh', output/'controller.sub.in'):
        seal_files[str(path)] = production.durable.sha(path)
    production.durable.new(output/'seal.json', dict(schema='shift-meson-controller-seal-v1', files=seal_files))
    digest = production.durable.sha(output/'seal.json')
    (output/'controller.sub').write_text((output/'controller.sub.in').read_text().replace('OPERATIONS_SEAL_DIGEST', digest))
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
    args = parser.parse_args()
    print(json.dumps(prepare(args.directory, args.seal, args.template, args.template_seal,
                             args.output, args.simultaneous_sources, args.simultaneous_detector_points), sort_keys=True))


if __name__ == '__main__':
    main()
