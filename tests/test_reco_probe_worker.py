# Code style: AI-code. AI-generated event-free worker receipt and relocation checks.
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import reco_probe_worker as worker


class ProbeWorker(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.iwd = self.root / 'persistent'
        self.iwd.mkdir()
        self.sandbox = self.root / 'scratch'
        self.sandbox.mkdir()
        self.previous_cwd = Path.cwd()
        self.addCleanup(self.restore_directory)
        self.job = {
            'id': 'synthetic', 'events': [[7, 1, 9]],
            'input': '/original/input.root', 'template': '/original/template.py',
            'output': '/original/nano.root', 'base_uri': 'root://synthetic/runtime',
            'transfer_input': 'input.root', 'transfer_template': 'template.py',
            'transfer_config': 'config.py', 'required_libraries': {},
        }
        for kind, content in [
                ('input', b'synthetic input'), ('template', b'synthetic template'),
                ('config', b'/original/template.py /original/input.root /original/nano.root')]:
            path = self.sandbox / self.job['transfer_' + kind]
            path.write_bytes(content)
            self.job[kind + '_sha256'] = worker.sha256(path)
        self.base = self.root / 'base.tar.gz'
        self.make_archive(self.base, 'CMSSW_17_0_0_pre4/src/placeholder', b'fixture')
        self.make_archive(self.sandbox / 'native_overlay.tar.gz', 'overlay/cache', b'fixture')
        self.job['base_sha256'] = worker.sha256(self.base)
        self.job['overlay_sha256'] = worker.sha256(self.sandbox / 'native_overlay.tar.gz')
        self.save_job()

    def restore_directory(self):
        import os
        os.chdir(self.previous_cwd)
        self.temporary.cleanup()

    def make_archive(self, path, name, payload):
        with tarfile.open(path, 'w:gz') as archive:
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    def save_job(self):
        (self.sandbox / 'job.json').write_text(json.dumps(self.job))

    def download(self, command, check):
        self.assertEqual(command[:3], ['xrdcp', '--nopbar', self.job['base_uri']])
        self.assertTrue(check)
        shutil.copyfile(self.base, command[3])

    def prepare(self):
        with patch.object(worker.subprocess, 'run', side_effect=self.download):
            return worker.main(['prepare', str(self.iwd), str(self.sandbox)])

    def test_prepare_verifies_hashes_and_relocates_only_frozen_tokens(self):
        self.assertEqual(self.prepare(), 0)
        scratch = self.sandbox / 'native_runtime'
        self.assertEqual((scratch / 'nano_cfg.py').read_text(),
                         ' '.join(str(path) for path in [
                             self.sandbox / 'template.py', self.sandbox / 'input.root',
                             scratch / 'nano.root']))
        bootstrap = json.loads((self.iwd / 'bootstrap.json').read_text())
        self.assertEqual(bootstrap['phase'], 'prepared')
        self.assertEqual(bootstrap['worker_cfg_sha256'], worker.sha256(scratch / 'nano_cfg.py'))
        self.assertFalse((self.iwd / 'run.json').exists())

    def test_prepare_failure_has_terminal_receipt_and_never_launches_native(self):
        self.job['input_sha256'] = 'wrong'
        self.save_job()
        with patch.object(worker.subprocess, 'Popen') as launch:
            self.assertEqual(self.prepare(), 1)
        launch.assert_not_called()
        receipt = json.loads((self.iwd / 'run.json').read_text())
        self.assertEqual(receipt['phase'], 'prepare')
        self.assertFalse(receipt['native_started'])
        self.assertFalse(receipt['private_mapping_pass'])
        self.assertFalse(receipt['output_exists'])
        self.assertIn('input SHA256 mismatch', receipt['error'])
        self.assertEqual(receipt['events'], [[7, 1, 9]])

    def test_setup_failure_is_recorded_and_does_not_overwrite_native_receipt(self):
        self.assertEqual(worker.main(['setup-failed', str(self.iwd), str(self.sandbox),
                                      '17', 'scram b ProjectRename']), 17)
        path = self.iwd / 'run.json'
        receipt = json.loads(path.read_text())
        self.assertEqual(receipt['phase'], 'setup')
        self.assertEqual(receipt['exit_code'], 17)
        self.assertFalse(receipt['native_started'])
        native = {'phase': 'native', 'exit_code': 42, 'error': 'preserved failure'}
        path.write_text(json.dumps(native))
        worker.main(['setup-failed', str(self.iwd), str(self.sandbox), '42', 'native command'])
        self.assertEqual(json.loads(path.read_text()), native)

    def test_shell_setup_trap_reports_failed_command_without_loading_cmssw(self):
        # Replace only the transferred helper and Bash source builtin. No
        # runtime download, CMSSW setup or reconstruction process is invoked.
        helper = self.sandbox / 'reco_probe_worker.py'
        helper.write_text(
            'import json, pathlib, sys\n'
            'if sys.argv[1] == "setup-failed":\n'
            ' pathlib.Path(sys.argv[2], "synthetic_shell_failure.json").write_text(json.dumps(sys.argv[1:]))\n')
        shell = Path(worker.__file__).with_name('run_reco_probe_worker.sh')
        environment = dict(os.environ, _CONDOR_SCRATCH_DIR=str(self.sandbox))
        completed = subprocess.run([
            'bash', '-c', 'source() { return 17; }; export -f source; exec bash "$1" "$2"',
            '--', str(shell), str(self.iwd)], env=environment, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 17, completed.stderr)
        recorded = json.loads((self.iwd / 'synthetic_shell_failure.json').read_text())
        self.assertEqual(recorded[:4], ['setup-failed', str(self.iwd), str(self.sandbox), '17'])
        # BASH_COMMAND identifies the failing statement inside our injected
        # source function, rather than the outer call of that function.
        self.assertEqual(recorded[4], 'return 17')

    def test_shell_preserves_scram_runtime_failure_instead_of_evaluating_empty_output(self):
        helper = self.sandbox / 'reco_probe_worker.py'
        helper.write_text(
            'import json, pathlib, sys\n'
            'if sys.argv[1] == "prepare":\n'
            ' pathlib.Path(sys.argv[3], "native_runtime/CMSSW_17_0_0_pre4/src").mkdir(parents=True)\n'
            'elif sys.argv[1] == "setup-failed":\n'
            ' pathlib.Path(sys.argv[2], "synthetic_shell_failure.json").write_text(json.dumps(sys.argv[1:]))\n')
        shell = Path(worker.__file__).with_name('run_reco_probe_worker.sh')
        environment = dict(os.environ, _CONDOR_SCRATCH_DIR=str(self.sandbox))
        commands = ('source() { return 0; }; '
                    'scram() { if [ "$1" = runtime ]; then return 23; fi; return 0; }; '
                    'export -f source scram; exec bash "$1" "$2"')
        completed = subprocess.run(['bash', '-c', commands, '--', str(shell), str(self.iwd)],
                                   env=environment, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 23, completed.stderr)
        recorded = json.loads((self.iwd / 'synthetic_shell_failure.json').read_text())
        self.assertEqual(recorded[3], '23')
        self.assertIn('native_runtime_environment=', recorded[4])

    def native_result(self, expected_hash):
        self.assertEqual(self.prepare(), 0)
        library = self.sandbox / 'native_runtime/overlay/libSynthetic.so'
        library.write_bytes(b'synthetic mapped library')
        self.job['required_libraries'] = {'libSynthetic.so': expected_hash or worker.sha256(library)}
        self.save_job()
        original_read_text = Path.read_text
        def read_text(path, *args, **kwargs):
            if str(path) == '/proc/123/maps':
                return '0-1 r-xp 0 0:0 1 ' + str(library) + '\n'
            if str(path) == '/proc/123/status':
                return 'VmRSS: 4096 kB\n'
            return original_read_text(path, *args, **kwargs)
        class Process:
            pid = 123
            returncode = 0
            def __init__(self, *args, **kwargs):
                self.polls = 0
                kwargs['stdout'].write(b'synthetic native log\n')
                (self_sandbox / 'native_runtime/nano.root').write_bytes(b'synthetic output')
            def poll(self):
                self.polls += 1
                return None if self.polls == 1 else 0
        self_sandbox = self.sandbox
        with patch.object(worker.subprocess, 'Popen', Process), \
                patch.object(worker.time, 'sleep'), patch.object(Path, 'read_text', read_text):
            code = worker.main(['run', str(self.iwd), str(self.sandbox)])
        return code, json.loads((self.iwd / 'run.json').read_text())

    def test_native_success_preserves_original_identity_and_mapping_digest(self):
        code, receipt = self.native_result(None)
        self.assertEqual(code, 0)
        self.assertTrue(receipt['native_started'])
        self.assertTrue(receipt['private_mapping_pass'])
        self.assertEqual(receipt['config_sha256'], self.job['config_sha256'])
        self.assertEqual(receipt['output_sha256'], worker.sha256(self.iwd / 'nano.root'))
        self.assertEqual(receipt['max_rss_kb'], 4096)

    def test_wrong_actual_library_fails_even_with_successful_native_output(self):
        code, receipt = self.native_result('wrong')
        self.assertEqual(code, 1)
        self.assertEqual(receipt['exit_code'], 0)
        self.assertTrue(receipt['output_exists'])
        self.assertFalse(receipt['private_mapping_pass'])

    def test_finalize_error_retains_unknown_native_accounting_and_worker_evidence(self):
        self.assertEqual(self.prepare(), 0)
        (self.sandbox / 'native_runtime/nano.root').write_bytes(b'existing native output')
        process = Mock(returncode=0)
        process.poll.return_value = 0
        with patch.object(worker.subprocess, 'Popen', return_value=process), \
                patch.object(worker.shutil, 'copyfile', side_effect=OSError('copy failed')):
            self.assertEqual(worker.main(['run', str(self.iwd), str(self.sandbox)]), 1)
        receipt = json.loads((self.iwd / 'run.json').read_text())
        self.assertEqual(receipt['phase'], 'run')
        self.assertIsNone(receipt['native_started'])
        self.assertTrue(receipt['worker_log_exists'])
        self.assertTrue(receipt['worker_output_exists'])
        self.assertIn('copy failed', receipt['error'])

    def test_timeout_kills_and_reaps_process_after_term_is_ignored(self):
        self.assertEqual(self.prepare(), 0)
        process = Mock(pid=123, returncode=-9)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('cmsRun', 30), -9]
        with patch.object(worker.subprocess, 'Popen', return_value=process), \
                patch.object(worker.os, 'killpg') as kill, \
                patch.object(worker.time, 'time', side_effect=[0., 1201., 1202.]):
            self.assertEqual(worker.main(['run', str(self.iwd), str(self.sandbox)]), 1)
        self.assertEqual(kill.call_args_list,
                         [call(123, worker.signal.SIGTERM), call(123, worker.signal.SIGKILL)])
        self.assertEqual(process.wait.call_args_list, [call(timeout=30), call()])
        receipt = json.loads((self.iwd / 'run.json').read_text())
        self.assertTrue(receipt['native_started'])
        self.assertIn('1200s whole-job guard', receipt['error'])


if __name__ == '__main__':
    unittest.main()
