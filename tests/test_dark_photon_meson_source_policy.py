# Code style: AI-code. AI-generated frozen source-sharding policy controls.
from pathlib import Path
import shutil
import json
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import prepare_dark_photon_meson_production as preparation


def policy():
    return dict(schema='shift-meson-source-execution-policy-v1',
                maximum_simultaneous_sources=256, maximum_batches=5000,
                maximum_source_collisions_per_batch=50000,
                maximum_canary_source_collisions=10000,
                detector_job_capacity_per_batch=64)


def rows(count=1002):
    result = [dict(mass_gev=.3, epsilon=.001, prompt=True,
                   source_collisions=10000, operational_canary=True),
              dict(mass_gev=.3, epsilon=1.e-6, target_mean_lab_flight_m=30,
                   source_collisions=10000, operational_canary=True)]
    for index in range(count - 2):
        result.append(dict(mass_gev=.3, epsilon=.001, prompt=True, source_collisions=50000))
    return result


class SourcePolicyTest(unittest.TestCase):
    def test_old_and_explicit_new_detector_and_storage_limits(self):
        self.assertEqual(preparation.detector_point_limit({}), 35)
        self.assertEqual(preparation.meson_storage_limit({}), 30000000000)
        self.assertEqual(preparation.detector_point_limit(dict(maximum_active_detector_workers=2400)), 2400)
        self.assertEqual(preparation.meson_storage_limit(dict(maximum_meson_forecast_bytes=100000000000)), 100000000000)
        for key, value in [('maximum_active_detector_workers', True),
                           ('maximum_active_detector_workers', 2401),
                           ('maximum_meson_forecast_bytes', 100000000001),
                           ('maximum_meson_forecast_bytes', False)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                if key == 'maximum_active_detector_workers':
                    preparation.detector_point_limit({key: value})
                else:
                    preparation.meson_storage_limit({key: value})

    def test_replacement_changes_detector_runner_only_for_explicit_runtime(self):
        import prepare_dark_photon_meson_controller as controller
        self.assertNotIn('run_shift_gen_to_nano.py', controller.replacement_tool_names({}))
        self.assertIn('run_shift_gen_to_nano.py', controller.replacement_tool_names({'detector_runtime': {'profile': 'pinned'}}))
        self.assertIn('prepare_dark_photon_meson_production.py', controller.replacement_tool_names({}))

    def test_parallel_handoff_flag_rejects_integer_truth_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'request.json'
            config.write_text(json.dumps(dict(schema='shift-dp-meson-production-request-v1', parallel_handoffs=1)))
            with self.assertRaisesRegex(ValueError, 'explicit boolean'):
                preparation.prepare(config, Path(temporary) / 'output')

    def test_balanced_fixed_exposure_preserves_even_the_tiny_remainder(self):
        parts = preparation.split_source_exposure(100001, 50000)
        self.assertEqual(sum(parts), 100001)
        self.assertTrue(all(2 <= value <= 50000 for value in parts))
        self.assertLessEqual(max(parts) - min(parts), 1)
        with self.assertRaises(ValueError):
            preparation.split_source_exposure(1, 50000)

    def test_legacy_defaults_and_namespace_are_unchanged(self):
        limits = preparation.source_execution_limits({})
        self.assertEqual(limits['maximum_simultaneous_sources'], 7)
        self.assertEqual(limits['maximum_batches'], 100)
        legacy = preparation.validate_batches(rows(2), [], mechanism='meson-radiative')
        self.assertEqual(legacy[0]['detector_job_capacity'], 1000)
        self.assertEqual(legacy[1]['detector_job_base'] - legacy[0]['detector_job_base'], 1000)
        with self.assertRaisesRegex(ValueError, 'bounded fixed-source'):
            preparation.validate_batches(rows(), [], mechanism='meson-radiative')

    def test_thousand_independent_shards_fit_frozen_narrow_namespace(self):
        limits = preparation.source_execution_limits(dict(source_execution_policy=policy()))
        result = preparation.validate_batches(rows(), [], seed_base=861100001,
            run_base=1761700001, detector_job_base=6600000,
            mechanism='meson-radiative', execution_limits=limits)
        self.assertEqual(len(result), 1002)
        self.assertEqual(len({r['seed'] for r in result}), 1002)
        self.assertEqual(len({r['run_number'] for r in result}), 1002)
        self.assertEqual(result[1]['detector_job_base'] - result[0]['detector_job_base'], 64)
        self.assertLessEqual(230000000 + (result[-1]['detector_job_base'] + 63) * 100 + 40, 899999900)

    def test_bulk_and_canary_limits_are_independent(self):
        limits = preparation.source_execution_limits(dict(source_execution_policy=policy()))
        for index, exposure in ((0, 10001), (2, 50001)):
            candidate = rows(3)
            candidate[index]['source_collisions'] = exposure
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'per-batch limit'):
                preparation.validate_batches(candidate, [], mechanism='meson-radiative', execution_limits=limits)

    def test_declared_policy_rejects_ambiguity_and_invalid_types(self):
        for change in (dict(maximum_simultaneous_sources=True), dict(maximum_batches=0),
                       dict(detector_job_capacity_per_batch=0), dict(maximum_canary_source_collisions=50001),
                       dict(unreviewed_limit=999)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                preparation.source_execution_limits(dict(source_execution_policy=dict(policy(), **change)))

    def test_actual_copied_controller_tools_import_the_new_policy_helper(self):
        # Use the real frozen predecessor inventory, then exactly the maintained
        # replacement list. No frozen deployment or scheduler state is changed.
        template = SCRIPTS.parents[1] / 'validation/bsm_rho_radiative_20261021/charged_rho/full35_controller_v1/tools'
        if not template.is_dir():
            self.skipTest('The local frozen controller import fixture is unavailable')
        import prepare_dark_photon_meson_controller as controller
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary) / 'tools'
            shutil.copytree(template, tools)
            original_runner = (tools / 'run_shift_gen_to_nano.py').read_bytes()
            for name in (*controller.replacement_tool_names({}), 'prepare_dark_photon_meson_controller.py'):
                shutil.copy2(SCRIPTS / name, tools / name)
            self.assertEqual((tools / 'run_shift_gen_to_nano.py').read_bytes(), original_runner)
            program = ("import sys; sys.path.insert(0, " + repr(str(tools)) + "); "
                       "import prepare_dark_photon_meson_controller as c; "
                       "import prepare_dark_photon_meson_production as p; "
                       "import run_dark_photon_meson_production as r; "
                       "assert p.source_execution_limits({})['maximum_simultaneous_sources']==7; "
                       "assert c.source_execution_limits({})['detector_job_capacity_per_batch']==1000")
            result = subprocess.run([sys.executable, '-I', '-c', program], cwd=temporary,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
