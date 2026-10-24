# Code style: AI-code. AI-generated controller outage and scheduler-action regression tests.
import errno
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_dark_photon_statistics as production


class ControllerPersistence(unittest.TestCase):
    def test_transient_rename_keeps_old_snapshot_until_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'state.json'
            path.write_text('{"previous": true}\n')
            replace = production.os.replace
            failures = []

            def interrupted(source, destination):
                if len(failures) < 2:
                    self.assertEqual(json.loads(path.read_text()), {'previous': True})
                    failures.append(True)
                    raise OSError(errno.ETIMEDOUT, 'AFS unavailable')
                replace(source, destination)

            with patch.object(production.os, 'replace', side_effect=interrupted), \
                    patch.object(production.time, 'sleep'):
                production.save(path, {'new': True})
            self.assertEqual(json.loads(path.read_text()), {'new': True})
            self.assertFalse(path.with_suffix('.json.next').exists())

    def test_outage_after_scheduler_action_does_not_repeat_the_step(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'state.json'
            path.write_text('{"submitted": []}\n')
            saved = production.save
            attempts = []

            def interrupted(destination, state):
                attempts.append(json.loads(json.dumps(state)))
                if len(attempts) <= 5:
                    raise OSError(errno.ESTALE, 'long AFS outage')
                saved(destination, state)

            def native_step(config, config_path, state):
                state['submitted'].append(123)
                return state

            with patch.object(sys, 'argv', ['controller', '--config', 'unused.json', '--once']), \
                    patch.object(production, 'configuration', return_value={'directory': root}), \
                    patch.object(production, 'step', side_effect=native_step) as step, \
                    patch.object(production, 'save', side_effect=interrupted), \
                    patch.object(production.time, 'sleep'), \
                    patch.object(production.sys, 'stderr'):
                production.main()
            step.assert_called_once()
            self.assertEqual(json.loads(path.read_text())['submitted'], [123])
            self.assertEqual(len(attempts), 6)
            self.assertTrue(all(item['submitted'] == [123] for item in attempts))

    def test_space_permission_and_semantic_errors_are_not_retried(self):
        for error in (OSError(errno.ENOSPC, 'quota'), PermissionError(errno.EACCES, 'denied'),
                      ValueError('changed scientific identity')):
            with self.subTest(error=error), \
                    patch.object(production, 'save', side_effect=error) as save, \
                    patch.object(production.time, 'sleep') as sleep:
                with self.assertRaises(type(error)):
                    production.persist_controller_state('/unused/state.json', {})
                save.assert_called_once()
                sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
