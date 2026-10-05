#!/usr/bin/env python3
"""Add a per-worker quota gate to the unchanged frozen GEN worker.

Transferred separately from the scientific runtime. The original manifest,
generator, worker, receipts and completeness audit remain authoritative.
"""
import json
import copy
import os
from pathlib import Path
import sys


def main():
    root = Path(os.environ['SHIFT_DAG_LOCAL_ROOT'])
    sys.path.insert(0, str(root/'workflow/scripts'))
    import run_shift_production_node as frozen
    if len(sys.argv) != 4:
        raise ValueError('Require manifest, mode and item')
    manifest = json.loads(Path(sys.argv[1]).read_text())
    frozen.validate_plan(manifest['plan'])
    policy_path = os.environ.get('SHIFT_DAG_SCHEDULING_POLICY')
    if policy_path:
        from generation_publication import sha256
        policy = json.loads(Path(policy_path).read_text())
        if policy.get('schema') != 'shift-gen-scheduling-policy-v1' or \
           policy.get('scientific_manifest_sha256') != sha256(sys.argv[1]):
            raise ValueError('Scheduling policy is not bound to the scientific manifest')
        reservation = policy.get('quota_reservation_workers')
        if not isinstance(reservation, int) or \
           not manifest['plan']['max_workers'] <= reservation <= 300 or \
           reservation != policy.get('scheduling_ceiling'):
            raise ValueError('Invalid scheduling quota reservation')
        # This copy is used only for storage reservation. The manifest passed
        # to the unchanged scientific worker keeps its original bytes/hash,
        # generator settings and receipt identity.
        capacity_manifest = copy.deepcopy(manifest)
        capacity_manifest['plan']['max_workers'] = reservation
        frozen.quota(capacity_manifest)
    elif sys.argv[2] == 'production':
        # Check before each job, including receipt revalidation on resumption.
        # quota() reserves the original concurrent workers plus fixed margin.
        frozen.quota(manifest)
    frozen.main()


if __name__ == '__main__':
    main()
