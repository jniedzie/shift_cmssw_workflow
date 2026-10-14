#!/usr/bin/env python3
# Code style: AI-code. AI-generated shared BSM publication reservation accounting.
"""Reserve unfinished detector chunks from other immutable BSM deployments."""
import hashlib
import importlib.util
import json
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def external_chunk_reservations(budget):
    """Count shared safety floors once; reserve each campaign's unfinished work.

    Only complete receipts accepted by the original sealed worker release a
    chunk reservation. A missing or in-progress receipt earns no credit.
    Unknown or changed authorities fail closed before publication.
    """
    total_bytes, total_files, details, seen = 0, 0, [], set()
    for authority in budget.get('external_reservations', []):
        directory = Path(authority['production_directory'])
        if not directory.is_absolute() or '..' in directory.parts or str(directory) in seen:
            raise ValueError('External BSM reservations require distinct absolute deployments')
        seen.add(str(directory))
        for name, key in [('seal.json', 'seal_sha256'), ('plan.json', 'plan_sha256'),
                          ('gen_inventory.json', 'gen_inventory_sha256')]:
            if sha256(directory / name) != authority[key]:
                raise ValueError('External BSM reservation authority changed: ' + str(directory / name))
        seal = json.loads((directory / 'seal.json').read_text())
        for name in ('chunk_worker.py', 'plan.json', 'gen_inventory.json', 'storage_budget.json'):
            path = directory / name
            if seal['files'].get(str(path)) != sha256(path):
                raise ValueError('External BSM deployment dependency changed: ' + str(path))
        specification = importlib.util.spec_from_file_location(
            '_shift_reserved_' + authority['seal_sha256'][:16], directory / 'chunk_worker.py')
        worker = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(worker)
        plan = json.loads((directory / 'plan.json').read_text())
        inventory = json.loads((directory / 'gen_inventory.json').read_text())
        other_budget = json.loads((directory / 'storage_budget.json').read_text())
        complete = worker.completed_chunk_count(plan, inventory, authority['seal_sha256'],
            authority['plan_sha256'], directory / 'statuses', other_budget['bytes_per_detector_chunk'])
        remaining = other_budget['detector_chunks'] - complete
        if remaining < 0:
            raise ValueError('External BSM completion count exceeds its complete partition')
        reserved_bytes = remaining * other_budget['bytes_per_detector_chunk']
        reserved_files = remaining * other_budget['files_per_detector_chunk']
        total_bytes += reserved_bytes
        total_files += reserved_files
        details.append(dict(production_directory=str(directory), complete_chunks=complete,
            unfinished_chunks=remaining, reserved_bytes=reserved_bytes, reserved_files=reserved_files))
    return total_bytes, total_files, details
