#!/usr/bin/env python3
# Code style: AI-code. AI-generated reader for sealed external storage budgets.
"""Read current external reservations through their unchanged sealed readers.

Call this program with a process timeout on an authenticated audit worker. Each
listed authority is an independent complete budget; do not list a nested budget
again when its parent's external reader already includes it. Its fixed reserve
is preserved even when some corresponding files already occupy storage.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import sys
import time

# Importing an authoritative reader must not write caches into its deployment.
sys.dont_write_bytecode = True


MAX_JSON_BYTES = 16000000


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1048576), b''):
            checksum.update(block)
    return checksum.hexdigest()


def read_json(path):
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError('External authority JSON exceeds the bounded size')
    return json.loads(path.read_text())


def pinned_path(directory, relative, seal):
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('External reader must be a relative sealed path')
    path = directory / relative
    expected = seal['files'].get(str(path))
    if not isinstance(expected, str) or digest(path) != expected:
        raise ValueError('External sealed dependency changed: ' + str(path))
    return path


def load_module(path):
    specification = importlib.util.spec_from_file_location('_shift_storage_' + digest(path)[:16], path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def positive_integer(value, name, allow_zero=False):
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ValueError('Invalid external storage reservation: ' + name)
    return value


def pinned_authority_file(authority):
    path = Path(authority['path'])
    expected = authority.get('sha256')
    if (not path.is_absolute() or path != path.resolve() or '..' in path.parts
            or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected)
            or digest(path) != expected):
        raise ValueError('Pinned peer storage authority changed: ' + str(path))
    return path


def unchanged_peer_budget(previous, replacement):
    """Allow terminal evidence while preserving every declared future allocation."""
    old = copy.deepcopy(read_json(pinned_authority_file(previous)))
    new = copy.deepcopy(read_json(pinned_authority_file(replacement)))
    old_rows, new_rows = old.pop('campaigns'), new.pop('campaigns')
    if old != new or len(old_rows) != len(new_rows):
        raise ValueError('Peer supervision cannot change shared floors or campaign membership')
    for before, after in zip(old_rows, new_rows):
        before, after = dict(before), dict(after)
        prior_terminal = before.pop('terminal_completion', None)
        terminal = after.pop('terminal_completion', None)
        if before != after or (prior_terminal is not None and prior_terminal != terminal):
            raise ValueError('Peer supervision cannot change a campaign allocation or terminal proof')
        if terminal is not None:
            pinned_authority_file(terminal)


def reserved_worklist_populations(config):
    """Separate fixed populations from their replaceable completion observer."""
    result = []
    for entry in config.get('additional_reserved_worklists', []):
        row = dict(entry)
        authority = row.pop('completion_authority', None)
        if authority is not None:
            controller = read_json(pinned_authority_file(authority['controller_config']))
            if controller.get('worklist') != row['worklist']['path']:
                raise ValueError('Meson completion observer changed its reserved source worklist')
            for key in ('current_controller_receipt', 'current_controller_submit'):
                if key in authority:
                    pinned_authority_file(authority[key])
        result.append(row)
    return result


def reviewed_peer_authority_replacement(previous, replacement):
    """Adopt pinned accounting/control code without changing science or storage budgets.

    The old authority remains the baseline. A new policy may add only terminal
    completion evidence, which its pinned reader still has to verify against
    native scheduler closure and every published output. Controller launch
    identities may move to a new persistent deployment; their science config
    and the protected allocations remain unchanged.
    """
    if (previous.get('schema') != 'shift-statistics-peer-storage-authority-v1'
            or replacement.get('schema') != previous['schema']):
        raise ValueError('Peer supervision replacement needs the complete statistics authority')
    outer_changes = {'policy', 'reader', 'constrained_stream'}
    if ({k: v for k, v in previous.items() if k not in outer_changes} !=
            {k: v for k, v in replacement.items() if k not in outer_changes}):
        raise ValueError('Peer supervision cannot change its storage contract')
    unchanged_peer_budget(previous['policy'], replacement['policy'])
    pinned_authority_file(replacement['reader'])
    old = previous['constrained_stream']
    new = replacement['constrained_stream']
    launch_changes = {'controller_bindings', 'supplement_policies', 'protected_qcd_guards'}
    if ({k: v for k, v in old.items() if k not in launch_changes} !=
            {k: v for k, v in new.items() if k not in launch_changes}):
        raise ValueError('Peer supervision cannot change constrained or additional campaign coverage')
    for name in launch_changes:
        if set(old[name]) != set(new[name]):
            raise ValueError('Peer supervision cannot omit a declared controller or guard')
    for name, pin in new['supplement_policies'].items():
        unchanged_peer_budget(old['supplement_policies'][name], pin)
    for name, guard in new['protected_qcd_guards'].items():
        before = old['protected_qcd_guards'][name]
        if guard['field'] != before['field']:
            raise ValueError('Peer supervision cannot change the protected-QCD field')
        values = []
        for pin in (before, guard):
            value = read_json(pinned_authority_file(pin))
            for part in pin['field'].split('.'):
                value = value[part]
            values.append(value)
        if values[0] != values[1]:
            raise ValueError('Peer supervision cannot change the protected-QCD amount')
    operational_fields = {'tools', 'lcg_wrapper', 'dependencies', 'supplement_admission',
        'reviewed_terminal_native_jobs', 'storage_admission_review', 'operational_authorities',
        'adaptive_terminal_completion', 'handoff_operations', 'physics_tools',
        'additional_reserved_worklists'}
    terminal_pins = [row.get('terminal_completion') for row in
                     read_json(pinned_authority_file(replacement['policy']))['campaigns']]
    for name, records in new['controller_bindings'].items():
        records = records if isinstance(records, list) else [records]
        originals = old['controller_bindings'][name]
        originals = originals if isinstance(originals, list) else [originals]
        old_configs = [read_json(pinned_authority_file(row['config'])) for row in originals]
        for row in records:
            config = read_json(pinned_authority_file(row['config']))
            science = {k: v for k, v in config.items() if k not in operational_fields}
            compatible = [prior for prior in old_configs if
                science == {k: v for k, v in prior.items() if k not in operational_fields}
                and reserved_worklist_populations(config) == reserved_worklist_populations(prior)]
            if (not compatible or ('physics_tools' in config and not any(
                    config['physics_tools'] == prior.get('physics_tools', prior.get('tools'))
                    for prior in compatible))):
                raise ValueError('Peer controller replacement changed its science configuration: ' + name)
            if (config.get('adaptive_terminal_completion') is not None
                    and config['adaptive_terminal_completion'] not in terminal_pins):
                raise ValueError('Peer controller uses another adaptive terminal completion proof')
            peer_controller_binding(name, row, new['protected_qcd_guards'].get(name),
                                    new['supplement_policies'].get(name))
    return copy.deepcopy(replacement)


def peer_controller_binding(name, binding, guard, supplement, directory_config=False):
    """Bind a live controller's native launch identity to its frozen guard."""
    config_path = pinned_authority_file(binding['config'])
    config = read_json(config_path)
    submit_path = pinned_authority_file(binding['submit'])
    launches = [pinned_authority_file(pin) for pin in binding['launch_files']]
    if not launches:
        raise ValueError('A peer controller needs pinned launch code: ' + name)
    if supplement is not None:
        if config.get('supplement_admission') != supplement:
            raise ValueError('Deferred controller does not use its pinned whole-future gate: ' + name)
    elif not directory_config and config_path != Path(guard['path']):
        field = binding.get('guard_reference_field')
        if not field or config.get(field) != guard['path']:
            raise ValueError('Active controller does not use its pinned QCD guard: ' + name)
    fields = {}
    for line in submit_path.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            fields[key.strip().lower()] = value.strip()
    expected = dict(Iwd=fields.get('initialdir'), Cmd=fields.get('executable'),
                    Args=fields.get('arguments'))
    marker = fields.get('+shiftdpstatisticstask', '')
    expected['ShiftDPStatisticsTask'] = json.loads(marker) if marker.startswith('"') else marker
    if binding.get('controller_seal'):
        operational_meson_launch(binding, config_path, config, expected, launches, fields)
    elif directory_config:
        seal_path = pinned_authority_file(binding['seal'])
        seal = read_json(seal_path)
        program = Path(config['tools']) / 'run_dark_photon_meson_production.py'
        wrapper = Path(expected['Cmd'])
        command = ['exec', 'python3', str(program), '--directory', str(config_path.parent),
                   '--seal', '$1', '--action', 'controller']
        invocations = [shlex.split(line, comments=True) for line in wrapper.read_text().splitlines()
                       if line.lstrip().startswith('exec ')]
        if (config_path != config_path.parent/'config.json'
                or seal_path != config_path.parent/'seal.json'
                or expected['Args'] != binding['seal']['sha256']
                or fields.get('universe', '').lower() != 'vanilla'
                or invocations != [command] or wrapper not in launches or program not in launches
                or any(seal['files'].get(str(path)) != digest(path)
                       for path in (config_path, wrapper, program))):
            raise ValueError('Additional meson launch does not derive its exact sealed directory configuration')
    if (any(not isinstance(value, str) or not value for value in expected.values())
            or expected['Iwd'] != str(config_path.parent)
            or expected['ShiftDPStatisticsTask'] != expected['Iwd']
            or (not directory_config and not any(str(config_path) in path.read_text()
                                                for path in [submit_path]+launches))):
        raise ValueError('Peer controller launch is not bound to its pinned config: ' + name)
    return dict(name=name, expected_native=expected)


def operational_meson_launch(binding, config_path, config, expected, launches, fields):
    """Check the exact sealed control handoff while keeping its source deployment."""
    production_path = pinned_authority_file(binding['production_config'])
    source_seal_path = pinned_authority_file(binding['seal'])
    controller_seal_path = pinned_authority_file(binding['controller_seal'])
    original = read_json(production_path)
    source_seal = read_json(source_seal_path)
    controller_seal = read_json(controller_seal_path)
    program = config_path.parent/'tools/run_dark_photon_meson_production.py'
    wrapper = Path(expected['Cmd'])
    command = ['exec', 'python3', str(program), '--directory', str(production_path.parent),
        '--seal', binding['seal']['sha256'], '--action', 'controller',
        '--controller-config', str(config_path), '--controller-seal', '$1']
    invocations = [shlex.split(line, comments=True) for line in wrapper.read_text().splitlines()
                   if line.lstrip().startswith('exec ')]
    operational = {'tools', 'physics_tools', 'simultaneous_sources', 'simultaneous_detector_points',
        'reviewed_detector_recoveries', 'additional_reserved_worklists',
        'reviewed_terminal_native_jobs', 'adaptive_terminal_completion'}
    if (production_path != production_path.parent/'config.json'
            or source_seal_path != production_path.parent/'seal.json'
            or config.get('directory') != str(production_path.parent)
            or config.get('physics_tools') != original.get('tools')
            or config_path != config_path.parent/'config.json'
            or controller_seal_path != config_path.parent/'seal.json'
            or expected['Args'] != binding['controller_seal']['sha256']
            or fields.get('universe', '').lower() != 'vanilla'
            or invocations != [command] or wrapper not in launches or program not in launches
            or source_seal['files'].get(str(production_path)) != digest(production_path)
            or any(row not in reserved_worklist_populations(config)
                   for row in reserved_worklist_populations(original))
            or any(config.get(key) != value for key, value in original.items() if key not in operational)
            or any(controller_seal['files'].get(str(path)) != digest(path)
                   for path in (config_path, wrapper, program, production_path, source_seal_path))
            or any(digest(Path(path)) != checksum for path, checksum in controller_seal['files'].items())):
        raise ValueError('Operational meson controller is not bound to its sealed original source')


def additional_meson_peers(constrained, policy, reader, observed, maximum_age):
    """Add a bounded future meson allocation without changing the older peers."""
    additions = constrained.get('additional_meson_campaigns', [])
    if not isinstance(additions, list) or len(additions) > 1:
        raise ValueError('At most one explicit additional meson allocation is supported')
    names = {row['name'] for row in policy['campaigns']}
    directories = [row['directory'] for row in policy['campaigns']]
    claims, controllers, roots = [], [], []
    for entry in additions:
        campaign = entry['campaign']
        name, directory = campaign['name'], campaign['directory']
        config_path = pinned_authority_file(campaign['worklist'])
        config = read_json(config_path)
        forecast = read_json(pinned_authority_file(campaign['forecast']))
        total = forecast.get('total_meson_forecast_bytes')
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name) or name in names
                or campaign.get('kind') != 'meson' or directory != str(config_path.parent)
                or config.get('directory') != directory
                or config.get('schema') != 'shift-dp-meson-production-v1'
                or config.get('production_mechanism') != 'meson-radiative'
                or config.get('minimum_eos_free_bytes', 0) < policy['minimum_free_bytes']
                or type(total) is not int or not 0 < total <= 30000000000
                or forecast.get('complete') is not True
                or forecast.get('worklist_sha256') != campaign['worklist']['sha256']
                or any(directory == root or directory.startswith(root.rstrip('/') + '/')
                       or root.startswith(directory.rstrip('/') + '/') for root in directories)):
            raise ValueError('Additional meson campaign must be distinct, pinned and fully reserved')
        # The new source must still protect every older declared production.
        # No completed status or proposed cleanup releases these conservative claims.
        for old in policy['campaigns']:
            if old['kind'] == 'meson':
                if not any(row.get('forecast') == old['forecast'] for row in
                           config.get('additional_reserved_meson_forecasts', [])):
                    raise ValueError('New meson source omits an older meson allocation')
            elif old['directory'] != config.get('adaptive_directory'):
                if not any(row.get('worklist') == old['worklist']
                           and row.get('bytes_per_event', 0) >= old['bytes_per_event']
                           for row in config.get('additional_reserved_worklists', [])):
                    raise ValueError('New meson source omits an older statistics allocation')
        if not any(row['directory'] == config.get('adaptive_directory')
                   and row['kind'] == 'statistics' for row in policy['campaigns']):
            raise ValueError('New meson source lost the protected adaptive production')
        binding = entry['controller_binding']
        if (binding.get('config') != campaign['worklist']
                and not (binding.get('controller_seal')
                         and binding.get('production_config') == campaign['worklist'])):
            raise ValueError('Additional meson controller differs from its reserved source worklist')
        controllers.append(peer_controller_binding(name, binding, campaign['worklist'], None, directory_config=True))
        claims.append(reader.supplement_campaign_claim(campaign, observed, maximum_age))
        roots.append(directory)
        names.add(name)
        directories.append(directory)
    return claims, controllers, roots


def statistics_peer_commitment(authority):
    """Use the peer campaign's frozen whole-allocation accounting once.

    The reader keeps full claims for missing/stale peers and credits only
    sealed publication predating this observation. It never credits planned
    retirement. Only the old QCD allowance is replaced by our own PRE claims.
    """
    policy_path = pinned_authority_file(authority['policy'])
    reader_path = pinned_authority_file(authority['reader'])
    policy = read_json(policy_path)
    if policy.get('schema') != 'shift-dp-supplement-admission-v1':
        raise ValueError('Unsupported statistics peer storage policy')
    capacity_path = pinned_authority_file(policy['shared_capacity_review'])
    capacity = read_json(capacity_path)
    if capacity.get('schema') != 'shift-reviewed-shared-weekend-capacity-v1':
        raise ValueError('Unsupported shared peer capacity review')
    floor = positive_integer(policy['minimum_free_bytes'], 'peer safety floor')
    floor_files = positive_integer(policy['minimum_free_files'], 'peer file floor')
    reviewed_floor = positive_integer(capacity['shared_free_floor_bytes'], 'reviewed peer floor')
    reviewed_total = positive_integer(capacity['total_extra_reservation_bytes'], 'shared transient reservation')
    if floor < reviewed_floor or reviewed_floor < reviewed_total:
        raise ValueError('Peer floor omits its reviewed shared transient reservations')
    replace = authority.get('replace_qcd_reservation_with_stream_PREs', False)
    if type(replace) is not bool:
        raise ValueError('Replacing the old QCD allowance needs an explicit boolean')
    replaced = positive_integer(capacity['qcd_reserve_bytes'], 'old QCD reserve') if replace else 0
    if replaced >= floor:
        raise ValueError('Old QCD allowance cannot consume the shared peer floor')
    campaigns = policy.get('campaigns')
    maximum_age = positive_integer(policy['maximum_peer_age_seconds'], 'peer state age')
    if (not isinstance(campaigns, list) or not 1 <= len(campaigns) <= 8
            or len({row['directory'] for row in campaigns}) != len(campaigns)):
        raise ValueError('Require distinct bounded peer campaigns')
    reader = load_module(reader_path)
    observed = time.time()
    claims = [reader.supplement_campaign_claim(row, observed, maximum_age) for row in campaigns]
    if len({row['name'] for row in claims}) != len(claims):
        raise ValueError('Repeated statistics peer commitment')
    remaining = sum(positive_integer(row['remaining_bytes'], 'remaining canonical bytes', allow_zero=True)
                    for row in claims)
    result = dict(authority_kind=authority['schema'], policy_path=str(policy_path),
        reader_path=str(reader_path), observed_at_epoch=observed, claims=claims,
        full_peer_floor_bytes=floor, replaced_old_qcd_bytes=replaced,
        shared_transient_floor_bytes=floor-replaced, remaining_canonical_bytes=remaining,
        required_free_bytes=floor-replaced+remaining, required_free_files=floor_files,
        fixed_reserve_bytes=floor-replaced, retirement_credit_bytes=0)
    constrained = authority.get('constrained_stream')
    if constrained is not None:
        active = constrained.get('active_campaign_names')
        names = {row['name'] for row in campaigns}
        if (not replace or not isinstance(active, list) or not active
                or len(set(active)) != len(active) or set(active) - names):
            raise ValueError('Constrained stream needs explicit distinct active peer campaigns')
        guards = constrained.get('protected_qcd_guards', {})
        if set(guards) != set(active):
            raise ValueError('Each active peer needs its pinned protected-QCD guard')
        for name, guard in guards.items():
            record = read_json(pinned_authority_file(guard))
            value = record
            for part in guard['field'].split('.'):
                value = value[part]
            if type(value) is not int or value != replaced:
                raise ValueError('Active peer does not protect the declared QCD allowance: ' + name)
        supplements = constrained.get('supplement_policies', {})
        if set(supplements) != names-set(active):
            raise ValueError('Every deferred supplement needs its pinned whole-future gate')
        allowed = {row['directory'] for row in campaigns}
        for name, pin in supplements.items():
            gate = read_json(pinned_authority_file(pin))
            if (gate.get('schema') != policy['schema'] or gate.get('campaigns') != campaigns
                    or gate.get('minimum_free_bytes', 0) < floor
                    or gate.get('shared_capacity_review') != policy['shared_capacity_review']):
                raise ValueError('Supplement no longer gates admission on the full shared future: ' + name)
            allowed.update(row['directory'] for row in gate.get('required_cohorts', []))
        bindings = constrained.get('controller_bindings', {})
        if not isinstance(bindings, dict) or set(bindings) != names:
            raise ValueError('Every peer controller needs its pinned native launch binding')
        controllers, seen_controllers, seen_configs = [], set(), set()
        for name in sorted(names):
            records = bindings[name]
            if isinstance(records, dict):
                records = [records]
            if (not isinstance(records, list) or not 1 <= len(records) <= 2
                    or any(not isinstance(record, dict) for record in records)):
                raise ValueError('A peer needs one or two explicit controller bindings: ' + name)
            for record in records:
                controller = peer_controller_binding(name, record, guards.get(name), supplements.get(name))
                identity = json.dumps(controller['expected_native'], sort_keys=True)
                config = record['config']['path']
                if identity in seen_controllers or config in seen_configs:
                    raise ValueError('Repeated peer controller binding: ' + name)
                seen_controllers.add(identity)
                seen_configs.add(config)
                controllers.append(controller)
        allowed.update(row['expected_native']['Iwd'] for row in controllers)
        additional_claims, additional_controllers, additional_roots = additional_meson_peers(
            constrained, policy, reader, observed, maximum_age)
        if additional_claims:
            extra = sum(positive_integer(row['remaining_bytes'], 'additional remaining meson bytes', allow_zero=True)
                        for row in additional_claims)
            result['claims'].extend(additional_claims)
            result['remaining_canonical_bytes'] += extra
            result['required_free_bytes'] += extra
            remaining += extra
            controllers.extend(additional_controllers)
            allowed.update(additional_roots)
            allowed.update(row['expected_native']['Iwd'] for row in additional_controllers)
        absent = constrained.get('required_absent_stream_campaigns')
        if not isinstance(absent, list) or not absent or any(not isinstance(tag, str) or not tag for tag in absent):
            raise ValueError('Constrained stream needs the exact old-SM drain identities')
        active_remaining = sum(row['remaining_bytes'] for row in claims if row['name'] in active)
        result['stream_capacity'] = dict(protected_stream_bytes=replaced,
            constrained_required_free_bytes=floor-replaced+active_remaining,
            active_campaign_names=active, deferred_campaign_names=sorted((names-set(active)) | {
                row['name'] for row in additional_claims}),
            deferred_remaining_canonical_bytes=remaining-active_remaining,
            deferred_bsm_directories=sorted([row['directory'] for row in campaigns if row['name'] not in active]
                                            + additional_roots),
            allowed_bsm_directories=sorted(allowed), required_absent_stream_campaigns=absent,
            controller_bindings=controllers,
            full_future_remains_reported=True)
    return result


def read_commitments(authorities):
    if not isinstance(authorities, list) or len(authorities) > 8:
        raise ValueError('Require a bounded list of at most eight external budgets')
    rows, seen = [], set()
    for authority in authorities:
        if authority.get('schema') == 'shift-statistics-peer-storage-authority-v1':
            identity = 'peer-policy:' + str(pinned_authority_file(authority['policy']))
            if identity in seen:
                raise ValueError('Repeated peer storage budget')
            seen.add(identity)
            rows.append(statistics_peer_commitment(authority))
            continue
        directory = Path(authority['production_directory'])
        if (not directory.is_absolute() or '..' in directory.parts
                or directory != directory.resolve() or str(directory) in seen):
            raise ValueError('Require distinct persistent absolute external deployments')
        seen.add(str(directory))
        for name, key in [('seal.json', 'seal_sha256'), ('plan.json', 'plan_sha256'),
                          ('gen_inventory.json', 'gen_inventory_sha256'),
                          ('storage_budget.json', 'storage_budget_sha256')]:
            expected = authority.get(key)
            if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected) or digest(directory / name) != expected:
                raise ValueError('External budget authority changed: ' + name)
        seal = read_json(directory / 'seal.json')
        for name in ('plan.json', 'gen_inventory.json', 'storage_budget.json'):
            pinned_path(directory, name, seal)
        worker = load_module(pinned_path(directory, authority['completion_reader'], seal))
        plan, inventory = read_json(directory / 'plan.json'), read_json(directory / 'gen_inventory.json')
        budget = read_json(directory / 'storage_budget.json')
        total = positive_integer(budget['detector_chunks'], 'total chunks')
        if total > 100000:
            raise ValueError('External chunk partition exceeds bounded audit size')
        count = worker.completed_chunk_count(plan, inventory, authority['seal_sha256'],
            authority['plan_sha256'], directory / 'statuses', budget['bytes_per_detector_chunk'])
        if type(count) is not int or not 0 <= count <= total:
            raise ValueError('External completion count is outside its fixed partition')
        required_bytes, required_files = worker.quota_requirements(budget, count)
        external_bytes, external_files, nested = 0, 0, []
        if authority.get('external_reader'):
            reader = load_module(pinned_path(directory, authority['external_reader'], seal))
            external_bytes, external_files, nested = reader.external_chunk_reservations(budget)
        for value, name in [(required_bytes, 'main bytes'), (required_files, 'main files'),
                            (external_bytes, 'nested bytes'), (external_files, 'nested files')]:
            positive_integer(value, name, allow_zero=True)
        rows.append(dict(production_directory=str(directory), verified_completed_chunks=count,
            unfinished_chunks=total-count, required_free_bytes=required_bytes+external_bytes,
            required_free_files=required_files+external_files,
            fixed_reserve_bytes=budget['fixed_reservation_bytes'], nested=nested))
    result = dict(complete=True, checked_at_epoch=time.time(), authorities=rows,
        authority_contracts_sha256=hashlib.sha256(json.dumps(authorities, sort_keys=True).encode()).hexdigest(),
        required_free_bytes=sum(row['required_free_bytes'] for row in rows),
        required_free_files=sum(row['required_free_files'] for row in rows))
    constrained = [row for row in rows if row.get('stream_capacity') is not None]
    if constrained:
        if len(rows) != 1:
            raise ValueError('Constrained peer accounting must be one complete shared authority')
        result['stream_capacity'] = constrained[0]['stream_capacity']
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = read_commitments(read_json(args.request)['external_storage_reservations'])
    args.output.write_text(json.dumps(result, indent=2) + '\n')
