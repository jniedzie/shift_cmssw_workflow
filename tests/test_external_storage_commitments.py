# Code style: AI-code. AI-generated sealed external reservation audit tests.
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/read_shift_external_storage_commitments.py'
spec = importlib.util.spec_from_file_location('external_storage', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ExternalStorageTests(unittest.TestCase):
    def fixture(self, folder, complete=3):
        root = Path(folder)
        (root / 'plan.json').write_text(json.dumps({'complete': complete}))
        (root / 'gen_inventory.json').write_text('{}')
        (root / 'storage_budget.json').write_text(json.dumps(dict(detector_chunks=10,
            bytes_per_detector_chunk=10, fixed_reservation_bytes=100)))
        (root / 'chunk_worker.py').write_text('''# Synthetic test fixture.
def completed_chunk_count(plan, inventory, seal, plan_hash, statuses, maximum):
    return plan['complete']
def quota_requirements(budget, complete):
    return budget['fixed_reservation_bytes'] + (10-complete)*10, 20+(10-complete)*2
''')
        (root / 'external.py').write_text('''# Synthetic test fixture.
def external_chunk_reservations(budget):
    return 30, 4, [{'unfinished_chunks': 3}]
''')
        files = {str(path):module.digest(path) for path in root.iterdir() if path.is_file()}
        (root / 'seal.json').write_text(json.dumps({'files': files}))
        return dict(production_directory=str(root), completion_reader='chunk_worker.py',
            external_reader='external.py', seal_sha256=module.digest(root/'seal.json'),
            plan_sha256=module.digest(root/'plan.json'),
            gen_inventory_sha256=module.digest(root/'gen_inventory.json'),
            storage_budget_sha256=module.digest(root/'storage_budget.json'))

    def test_sealed_authoritative_remaining_and_nested_reserves(self):
        with tempfile.TemporaryDirectory() as folder:
            result = module.read_commitments([self.fixture(folder)])
            self.assertEqual(result['required_free_bytes'], 200)
            self.assertEqual(result['required_free_files'], 38)
            self.assertEqual(result['authorities'][0]['fixed_reserve_bytes'], 100)

    def test_full_detector_completion_retains_the_fixed_future_publication_floor(self):
        with tempfile.TemporaryDirectory() as folder:
            result = module.read_commitments([self.fixture(folder, complete=10)])
            self.assertEqual(result['required_free_bytes'], 130)
            self.assertEqual(result['required_free_files'], 24)

    def test_changed_budget_or_worker_is_rejected(self):
        for name in ['storage_budget.json', 'chunk_worker.py']:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                authority = self.fixture(folder)
                with (Path(folder)/name).open('a') as stream:
                    stream.write('\n# changed')
                with self.assertRaises(ValueError):
                    module.read_commitments([authority])

    def test_missing_authority_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            authority = self.fixture(folder)
            (Path(folder)/'gen_inventory.json').unlink()
            with self.assertRaises(FileNotFoundError):
                module.read_commitments([authority])

    def test_duplicate_budget_is_not_counted_or_credited_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            authority = self.fixture(folder)
            with self.assertRaises(ValueError):
                module.read_commitments([authority, authority])

    def test_impossible_completion_count_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            authority = self.fixture(folder, complete=11)
            with self.assertRaises(ValueError):
                module.read_commitments([authority])

    def test_no_external_campaigns_requires_no_external_space(self):
        result = module.read_commitments([])
        self.assertEqual(result['required_free_bytes'], 0)
        self.assertEqual(result['required_free_files'], 0)


if __name__ == '__main__':
    unittest.main()
