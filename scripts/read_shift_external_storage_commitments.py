#!/usr/bin/env python3
# Code style: AI-code. AI-generated reader for sealed external storage budgets.
"""Read current external reservations through their unchanged sealed readers.

Call this program with a process timeout on an authenticated audit worker. Each
listed authority is an independent complete budget; do not list a nested budget
again when its parent's external reader already includes it. Its fixed reserve
is preserved even when some corresponding files already occupy storage.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
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


def read_commitments(authorities):
    if not isinstance(authorities, list) or len(authorities) > 8:
        raise ValueError('Require a bounded list of at most eight external budgets')
    rows, seen = [], set()
    for authority in authorities:
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
    return dict(complete=True, checked_at_epoch=time.time(), authorities=rows,
        authority_contracts_sha256=hashlib.sha256(json.dumps(authorities, sort_keys=True).encode()).hexdigest(),
        required_free_bytes=sum(row['required_free_bytes'] for row in rows),
        required_free_files=sum(row['required_free_files'] for row in rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = read_commitments(read_json(args.request)['external_storage_reservations'])
    args.output.write_text(json.dumps(result, indent=2) + '\n')
