#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable statistics-controller rollover helper.
"""Freeze an append-only meson-peer policy rollover; never submit or edit science state."""
import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import run_dark_photon_meson_production as meson
import run_dark_photon_statistics as statistics


def validate_policy(previous, candidate):
    """Keep existing claims and all admission gates, appending only bound meson peers."""
    if previous.get('schema') != 'shift-dp-supplement-admission-v1':
        raise ValueError('Unsupported statistics admission policy')
    before, after = previous.get('campaigns'), candidate.get('campaigns')
    if (not isinstance(before, list) or not isinstance(after, list)
            or after[:len(before)] != before
            or {key:value for key,value in candidate.items() if key != 'campaigns'} !=
               {key:value for key,value in previous.items() if key != 'campaigns'}):
        raise ValueError('Policy rollover must preserve every existing peer, cohort and admission gate')
    names = [row.get('name') for row in after]
    directories = [row.get('directory') for row in after]
    if (any(not isinstance(name, str) or not name for name in names)
            or len(names) != len(set(names)) or len(directories) != len(set(directories))):
        raise ValueError('Admission policy requires unique peer names and production directories')
    added = after[len(before):]
    for row in added:
        if set(row) != {'kind', 'name', 'directory', 'worklist', 'forecast'} or row['kind'] != 'meson':
            raise ValueError('Policy rollover may only append complete pinned meson peers')
        authority = dict(name=row['name'], peer_configuration=row['worklist'], forecast=row['forecast'])
        directory = meson.validate_meson_forecast_peer(authority)
        if row['directory'] != directory:
            raise ValueError('Admission peer directory differs from its frozen worklist')
        meson.additional_reservations(dict(additional_reserved_meson_forecasts=[authority]))
    return added


def relocated(path, previous, output):
    value = Path(path)
    if not value.is_absolute() or not value.is_relative_to(previous):
        raise ValueError('Operational path is outside the previous frozen deployment: '+str(path))
    return output/value.relative_to(previous)


def prepare(template, template_freeze, policy, output, meson_peer_controllers=()):
    template, policy, output = [Path(path).resolve() for path in (template, policy, output)]
    if output.exists():
        raise ValueError('Replacement output already exists; preserve the previous attempt')
    freeze = template/'freeze.json'
    if statistics.sha(freeze) != template_freeze:
        raise ValueError('Statistics template freeze changed')
    frozen = statistics.read(freeze)
    for name, expected in frozen['files'].items():
        if statistics.sha(name) != expected:
            raise ValueError('Statistics template dependency changed: '+name)
    previous = statistics.configuration(template/'config.json')
    old_policy_pin = previous['supplement_admission']
    old_policy = statistics.pinned_json(old_policy_pin)
    candidate_policy = statistics.read(policy)
    added = validate_policy(old_policy, candidate_policy)
    old_tools = Path(previous['tools'])
    old_wrapper = Path(previous['lcg_wrapper'])
    if old_tools != template/'tools' or old_wrapper != template/'lcg_controller.sh':
        raise ValueError('Require the coherent previous operational tools and wrapper')
    descriptor = (template/'controller.sub').read_text()
    required = ('universe = vanilla', 'initialdir = '+str(template), '+MaxRuntime = 604800',
                '+SendCredential = True', 'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)',
                'periodic_release = False', 'should_transfer_files = NO')
    if any(line not in descriptor for line in required):
        raise ValueError('Template lacks the required persistent credential and failure-hold policy')
    sources = {}
    for source in sorted(old_tools.iterdir()):
        if source.name == '__pycache__':
            continue
        expected = frozen['files'].get(str(source))
        if not source.is_file() or expected is None or statistics.sha(source) != expected:
            raise ValueError('Statistics tools contain an unpinned or changed helper: '+str(source))
        sources[source] = relocated(source, template, output)
    for name in previous['dependencies']:
        source = Path(name)
        if source.is_relative_to(template) and source not in (old_wrapper, Path(old_policy_pin['path'])):
            sources[source] = relocated(source, template, output)
    if sum(source.stat().st_size for source in sources) > 64000000:
        raise ValueError('Operational rollover exceeds the bounded 64 MB helper deployment')
    output.mkdir(parents=True)
    for source, target in sources.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # Freeze the two control modules together. GEN and detector source helpers
    # remain the exact previous frozen copies.
    for name in ('run_dark_photon_statistics.py', 'run_dark_photon_meson_production.py'):
        shutil.copy2(Path(__file__).with_name(name), output/'tools'/name)
    new_policy = output/'admission_policy.json'
    shutil.copy2(policy, new_policy)
    new_wrapper = output/'lcg_controller.sh'
    new_wrapper.write_text(old_wrapper.read_text().replace(str(template)+'/', str(output)+'/'))
    new_wrapper.chmod(0o750)
    new_descriptor = descriptor.replace(str(template)+'/', str(output)+'/')
    new_descriptor = new_descriptor.replace('initialdir = '+str(template)+'\n',
                                            'initialdir = '+str(output)+'\n')
    new_descriptor = new_descriptor.replace('"'+str(template)+'"', '"'+str(output)+'"')
    (output/'controller.sub').write_text(new_descriptor)
    current = copy.deepcopy(previous)
    current['tools'] = str(output/'tools')
    current['lcg_wrapper'] = str(new_wrapper)
    current['handoff_operations'] = str(relocated(previous['handoff_operations'], template, output))
    current['supplement_admission'] = dict(path=str(new_policy), sha256=statistics.sha(new_policy))
    if meson_peer_controllers:
        locators = current.setdefault('meson_peer_controller_directories', {})
        for name, owner in meson_peer_controllers:
            locators[name] = str(Path(owner).resolve())
        meson.validate_meson_peer_locators(current,
            [row['name'] for row in candidate_policy['campaigns'] if row['kind'] == 'meson'])
    dependencies = {}
    for name, expected in previous['dependencies'].items():
        source = Path(name)
        if source.is_relative_to(template):
            target = new_policy if name == old_policy_pin['path'] else relocated(source, template, output)
            dependencies[str(target)] = statistics.sha(target)
        else:
            dependencies[name] = expected
    dependencies[str(new_policy)] = statistics.sha(new_policy)
    dependencies[str(new_wrapper)] = statistics.sha(new_wrapper)
    for name in ('run_dark_photon_statistics.py', 'run_dark_photon_meson_production.py'):
        target = output/'tools'/name
        dependencies[str(target)] = statistics.sha(target)
    for row in added:
        for key in ('worklist', 'forecast'):
            authority = row[key]
            dependencies[authority['path']] = authority['sha256']
        peer = statistics.pinned_json(row['worklist'])
        dependencies[peer['model_card']] = statistics.sha(peer['model_card'])
    current['dependencies'] = dependencies
    allowed = {'tools', 'lcg_wrapper', 'handoff_operations', 'supplement_admission', 'dependencies',
               'meson_peer_controller_directories'}
    if set(previous.get('meson_peer_controller_directories', {}))-set(current.get('meson_peer_controller_directories', {})):
        raise ValueError('Statistics rollover removed a protected meson completion owner')
    if any(current[key] != previous[key] for key in previous if key not in allowed):
        raise ValueError('Statistics rollover changed frozen scientific or accounting settings')
    statistics.new(output/'config.json', current)
    # Import the actual clone, so a maintained-module check cannot hide a broken
    # frozen import or a missing helper required by the detached controller.
    probe = ('import json,sys; sys.path.insert(0,sys.argv[1]); '
             'import run_dark_photon_statistics as frozen; '
             'config=frozen.configuration(sys.argv[2]); '
             'print(json.dumps(dict(directory=config["directory"],tools=config["tools"])))')
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    checked = subprocess.run([sys.executable, '-B', '-c', probe, current['tools'], str(output/'config.json')],
        cwd=output, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True, timeout=120)
    proof = json.loads(checked.stdout)
    if proof != dict(directory=current['directory'], tools=current['tools']):
        raise ValueError('Frozen statistics configuration validation returned different settings')
    statistics.new(output/'configuration_validation.json', dict(complete=True,
        module=str(output/'tools/run_dark_photon_statistics.py'),
        config_sha256=statistics.sha(output/'config.json'), result=proof))
    files = {str(path):statistics.sha(path) for path in output.rglob('*') if path.is_file()}
    record = dict(schema='shift-reviewed-bsm-supervisor-rollover-v1',
        created_utc=datetime.now(timezone.utc).isoformat(), previous_directory=str(template),
        template_freeze=dict(path=str(freeze), sha256=template_freeze),
        old_config=dict(path=str(template/'config.json'), sha256=statistics.sha(template/'config.json')),
        new_config=dict(path=str(output/'config.json'), sha256=statistics.sha(output/'config.json')),
        canonical_science_directory=current['directory'], persistent_initialdir=str(output),
        seeds_runs_exposures_conditions_unchanged=True, source_jobs_unchanged=True,
        changed_configuration_keys=sorted(key for key in allowed if previous.get(key) != current.get(key)),
        added_meson_peers=[row['name'] for row in added], files=files, no_submission_performed=True)
    statistics.new(output/'freeze.json', record)
    return dict(complete=True, directory=str(output), freeze_sha256=statistics.sha(output/'freeze.json'),
                added_meson_peers=record['added_meson_peers'], configuration_validation=proof)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', required=True, type=Path)
    parser.add_argument('--template-freeze', required=True)
    parser.add_argument('--policy', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--meson-peer-controller', action='append', nargs=2, default=[],
        metavar=('NAME', 'DIRECTORY'), help='Exact terminal owner of a protected meson peer')
    args = parser.parse_args()
    print(json.dumps(prepare(args.template, args.template_freeze, args.policy, args.output,
                             args.meson_peer_controller), sort_keys=True))


if __name__ == '__main__':
    main()
