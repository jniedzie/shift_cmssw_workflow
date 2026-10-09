"""Access the workspace's canonical storage resolver without duplicating it.

Frozen deployments must explicitly include this facade and its canonical helper
before they can read relocated data. Existing frozen sources are never edited.
"""
import importlib.util
import os
from pathlib import Path


WORKSPACE = Path('/afs/cern.ch/work/j/jniedzie/private/shift_cmssw')
CANONICAL_HELPER_PATH = WORKSPACE / 'tea_shift_cmssw/configs/shift_storage_paths.py'
DEFAULT_MIGRATION_MANIFEST = WORKSPACE / 'validation/storage_reorganization_20261009/path_map.json'
_helper_module = None


def _canonical_helper(migration_manifest=None):
    global _helper_module
    if _helper_module is not None:
        return _helper_module
    if CANONICAL_HELPER_PATH.is_file():
        spec = importlib.util.spec_from_file_location('_tea_shift_storage_paths', CANONICAL_HELPER_PATH)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        _helper_module = helper
        return helper
    configured = migration_manifest or os.environ.get('SHIFT_STORAGE_MIGRATION_MANIFEST')
    if configured or DEFAULT_MIGRATION_MANIFEST.exists():
        raise RuntimeError('Storage migration resolver is unavailable in this deployment: '
                           + str(CANONICAL_HELPER_PATH))
    return None


def resolve_storage_path(path, migration_manifest=None):
    helper = _canonical_helper(migration_manifest)
    return str(path) if helper is None else helper.resolve_storage_path(path, migration_manifest)
