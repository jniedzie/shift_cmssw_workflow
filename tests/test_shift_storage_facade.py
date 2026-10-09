import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


FACADE = Path(__file__).resolve().parents[1] / 'scripts/shift_storage_paths.py'


def load_facade():
    spec = importlib.util.spec_from_file_location('storage_facade_test', FACADE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StorageFacadeTest(unittest.TestCase):
    def test_canonical_resolver_reads_exact_completed_map(self):
        facade = load_facade()
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'migration.json'
            manifest.write_text(json.dumps(dict(schema='shift-storage-path-map-v1',
                complete=True, paths={'/tmp/original.root': '/tmp/relocated.root'})))
            self.assertEqual(facade.resolve_storage_path('/tmp/original.root', manifest), '/tmp/relocated.root')

    def test_missing_helper_keeps_identity_only_before_migration(self):
        facade = load_facade()
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {}, clear=True):
            root = Path(directory)
            facade.CANONICAL_HELPER_PATH = root / 'missing_helper.py'
            facade.DEFAULT_MIGRATION_MANIFEST = root / 'map.json'
            self.assertEqual(facade.resolve_storage_path('/tmp/original.root'), '/tmp/original.root')
            facade.DEFAULT_MIGRATION_MANIFEST.write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'resolver is unavailable'):
                facade.resolve_storage_path('/tmp/original.root')

    def test_missing_helper_refuses_explicit_migration(self):
        facade = load_facade()
        with tempfile.TemporaryDirectory() as directory:
            facade.CANONICAL_HELPER_PATH = Path(directory) / 'missing_helper.py'
            facade.DEFAULT_MIGRATION_MANIFEST = Path(directory) / 'missing_map.json'
            with self.assertRaisesRegex(RuntimeError, 'resolver is unavailable'):
                facade.resolve_storage_path('/tmp/original.root', Path(directory) / 'requested_map.json')


if __name__ == '__main__':
    unittest.main()
