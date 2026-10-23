# Code style: AI-code. AI-generated automatic-admission safety checks.
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/dark_photon_automatic_admission.py'
SPEC = importlib.util.spec_from_file_location('automatic_admission', SCRIPT)
admission = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(admission)


class AutomaticAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        (self.directory / 'config.json').write_text('{"frozen": true}')
        (self.directory / 'model.json').write_text('{"model": "fixed"}')
        masses = [.22, .25, .3, .4, .5, .7, .9]
        batches = [dict(batch='gen'+str(i), mass_gev=mass,
                        gen_qualification_only=True, operational_canary=True)
                   for i, mass in enumerate(masses)]
        batches.extend([dict(batch='prompt', operational_canary=True, prompt=True),
                        dict(batch='displaced', operational_canary=True, prompt=False),
                        dict(batch='bulk', operational_canary=False)])
        self.config = dict(directory=str(self.directory),
            model_card=str(self.directory / 'model.json'),
            bulk_storage_gate=str(self.directory / 'gate.json'),
            maximum_meson_forecast_bytes=1000, batches=batches,
            coverage_audit=dict(masses_gev=masses),
            automatic_bulk_admission=dict(schema='shift-dp-automatic-bulk-admission-v1',
                per_batch_forecast_bytes={b['batch']: 10 for b in batches}))
        self.state = dict(batches={b['batch']: dict(gen_qualification=dict(complete=True))
                                  for b in batches})

    def test_no_tests_no_automatic_release(self):
        self.assertIsNone(admission.maybe_create_bulk_gate(self.config, {}))
        self.assertFalse(Path(self.config['bulk_storage_gate']).exists())

    def test_missing_mass_cannot_be_substituted_by_more_other_tests(self):
        self.config['coverage_audit']['masses_gev'].append(.8)
        with self.assertRaisesRegex(ValueError, 'every requested mass'):
            admission.maybe_create_bulk_gate(self.config, self.state)

    def test_incomplete_downstream_keeps_bulk_waiting(self):
        with patch.object(admission, 'source_evidence', return_value={'GEN': 'valid'}), \
             patch.object(admission, 'fullchain_evidence', return_value=None):
            self.assertIsNone(admission.maybe_create_bulk_gate(self.config, self.state))
        self.assertFalse(Path(self.config['bulk_storage_gate']).exists())

    def test_failed_evidence_does_not_create_gate(self):
        with patch.object(admission, 'source_evidence', side_effect=ValueError('changed source')):
            with self.assertRaisesRegex(ValueError, 'changed source'):
                admission.maybe_create_bulk_gate(self.config, self.state)
        self.assertFalse(Path(self.config['bulk_storage_gate']).exists())

    def test_frozen_reservation_cannot_expand(self):
        self.config['automatic_bulk_admission']['per_batch_forecast_bytes']['bulk'] = 1001
        with self.assertRaisesRegex(ValueError, 'storage allocation'):
            admission.maybe_create_bulk_gate(self.config, self.state)

    def test_all_prerequisites_release_exact_frozen_request_once(self):
        with patch.object(admission, 'source_evidence', return_value={'GEN': 'verified'}), \
             patch.object(admission, 'fullchain_evidence', return_value={'ROOT': 'verified'}):
            gate = admission.maybe_create_bulk_gate(self.config, self.state)
        self.assertTrue(gate['complete'])
        self.assertEqual(len(gate['source_qualification']), 7)
        self.assertEqual(len(gate['fullchain_qualification']), 2)
        self.assertEqual(gate['worklist_sha256'], admission.sha(self.directory / 'config.json'))
        before = Path(self.config['bulk_storage_gate']).read_bytes()
        self.assertEqual(admission.maybe_create_bulk_gate(self.config, {}), gate)
        self.assertEqual(Path(self.config['bulk_storage_gate']).read_bytes(), before)

    def test_existing_gate_from_different_request_is_rejected(self):
        Path(self.config['bulk_storage_gate']).write_text(json.dumps(dict(
            automatic_canary_admission=True, worklist_sha256='wrong')))
        with self.assertRaisesRegex(ValueError, 'another frozen request'):
            admission.maybe_create_bulk_gate(self.config, self.state)


if __name__ == '__main__':
    unittest.main()
