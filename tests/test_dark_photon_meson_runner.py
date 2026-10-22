# Code style: AI-code. AI-generated source-card and fixed-exposure guard tests.
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_dark_photon_meson_gen import audit_generated_source, remaining_timeout, resolved_parent_models, run
from dark_photon_meson_source import coefficient_bound


class MesonRunnerTest(unittest.TestCase):
    def test_closed_eta_does_not_evaluate_its_unphysical_pole(self):
        card = json.loads((Path(__file__).resolve().parents[1] /
            'config/campaigns/dark_photon_meson_model.json').read_text())
        configuration = dict(parents=[dict(pdg_id=221,mass_min_gev=.547862,mass_max_gev=.547862),
                                      dict(pdg_id=331,mass_min_gev=.95578,mass_max_gev=.95978)])
        models = resolved_parent_models(card, configuration, .749)
        self.assertFalse(models[221]['kinematically_open'])
        self.assertTrue(models[331]['kinematically_open'])
        self.assertGreater(coefficient_bound(112.97, .2, models, .749), 0.)
        # The closed eta contributes zero even though its form-factor pole
        # is singular outside the physical decay interval.
        self.assertEqual(coefficient_bound(112.97, .2, {221: models[221]}, .749), 0.)

    def test_single_collision_exposure_is_rejected_before_creating_output(self):
        arguments = SimpleNamespace(mass=.3, epsilon=1.e-6, source_collisions=1,
                                    seed=761016999, run_number=1761016999, timeout=60)
        with self.assertRaisesRegex(ValueError, 'fixed source exposure'):
            run(arguments)


class MesonAuditDeadlineTest(unittest.TestCase):
    def test_audit_uses_remaining_large_source_budget_and_respects_short_requests(self):
        with patch('run_dark_photon_meson_gen.time.monotonic', return_value=4100):
            self.assertEqual(remaining_timeout(started=100, timeout=43200), 39200)
            self.assertEqual(remaining_timeout(started=4000, timeout=120), 20)
            for timeout in (100, 50):
                with self.subTest(timeout=timeout), self.assertRaises(TimeoutError):
                    remaining_timeout(started=4000, timeout=timeout)

    def test_real_audit_receives_the_directory_and_checks_every_fixture_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output/'meson_source_ledger.jsonl').write_text('first\nlast\n')
            (output/'audit_dark_photon_gen.py').write_text(
                'from pathlib import Path\nimport sys\n'
                'out=Path(sys.argv[1])\n'
                'assert out == Path.cwd()\n'
                'assert (out/"meson_source_ledger.jsonl").read_text().splitlines() == ["first","last"]\n'
                '(out/"validation.json").write_text("audited complete fixture")\n'
                'print("complete audit")\n')
            audit_generated_source(output, remaining_timeout(time.monotonic(), 43200))
            self.assertEqual((output/'validation.json').read_text(), 'audited complete fixture')
            self.assertIn('complete audit', (output/'audit.log').read_text())

    def test_real_audit_failure_or_timeout_cannot_be_reported_as_success(self):
        for source, failure, timeout in (
                ('raise RuntimeError("invalid ledger")\n', subprocess.CalledProcessError, 10),
                ('import time\ntime.sleep(10)\n', subprocess.TimeoutExpired, .01)):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                (output/'audit_dark_photon_gen.py').write_text(source)
                with self.assertRaises(failure):
                    audit_generated_source(output, timeout)
                self.assertFalse((output/'validation.json').exists())


if __name__ == '__main__':
    unittest.main()
