import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import shift_condor_native as native


class NativeCondorTest(unittest.TestCase):
    def test_global_arrays_and_successful_empty_result(self):
        self.assertEqual(native.parse_ads(' []\n [{"ClusterId": 10}]\n[]\n'),
                         [{"ClusterId": 10}])
        self.assertEqual(native.parse_ads(" \n"), [])

    def test_partial_global_response_and_warnings_are_unknown(self):
        for output in ('[]\n[{"ClusterId":', '[{"ClusterId":10}]\nwarning',
                       '{"ClusterId":10}', '[1]'):
            with self.subTest(output=output), self.assertRaises(native.NativeCondorError):
                native.parse_ads(output)
        with self.assertRaises(native.NativeCondorError):
            native.parse_ads('[{"ClusterId":10}]', "Failed to contact another schedd")

    def test_scheduler_env_drops_cms_and_user_configuration(self):
        source = {"HOME": "/afs/user", "USER": "owner", "KRB5CCNAME": "FILE:/ticket",
                  "LD_LIBRARY_PATH": "/cms/lib", "PATH": "/eos/bin",
                  "PYTHONPATH": "/cms/python", "_CONDOR_CONDOR_HOST": "wrong-host"}
        env = native.native_env(source, local_schedd=True)
        self.assertEqual(env["SKIP_LOCAL_CONFIG_FILE"], "TRUE")
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertEqual(env["KRB5CCNAME"], "FILE:/ticket")
        self.assertEqual(env["_CONDOR_SCHEDD_ADDRESS_FILE"], native.SCHEDD_ADDRESS_FILE)
        self.assertNotIn("LD_LIBRARY_PATH", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("_CONDOR_CONDOR_HOST", env)
        self.assertNotIn("SKIP_LOCAL_CONFIG_FILE", native.native_env(source, local_schedd=False))

    def test_global_query_keeps_constraint_and_projection(self):
        with patch.object(native, "run_condor", return_value='[]\n[{"ClusterId":7}]') as run:
            self.assertEqual(native.query('Owner == "owner"', ["ClusterId"],
                                          global_query=True), [{"ClusterId": 7}])
        self.assertEqual(run.call_args.args[0],
                         ["/usr/bin/condor_q", "-global", "-constraint", 'Owner == "owner"',
                          "-json", "-attributes", "ClusterId"])

    def test_account_query_includes_group_submitters_and_every_schedd(self):
        discovery = json.dumps([
            {"Name": "group_u_CMS.users.owner@cern.ch", "ScheddName": "bigbird26.cern.ch"},
            {"Name": "owner@cern.ch", "ScheddName": "bigbird14.cern.ch"},
            {"Name": "owner@cern.ch", "ScheddName": "bigbird26.cern.ch"}])
        with patch.object(native, "run_condor", side_effect=[discovery, "[]", '[{"ClusterId":7}]', discovery]) as run:
            ads = native.query_account("owner", "ShiftProductionSuite == true", ["ClusterId"])
        self.assertEqual(ads, [{"ClusterId": 7, "_queried_schedd": "bigbird26.cern.ch"}])
        self.assertEqual(run.call_args_list[1].args[0][:3],
                         ["/usr/bin/condor_q", "-name", "bigbird14.cern.ch"])
        self.assertEqual(run.call_args_list[2].args[0][:3],
                         ["/usr/bin/condor_q", "-name", "bigbird26.cern.ch"])
        self.assertIn('Owner == "owner" && (ShiftProductionSuite == true)',
                      run.call_args_list[2].args[0])

    def test_account_discovery_empty_or_changed_is_unknown(self):
        discovery = '[{"Name":"owner@cern.ch","ScheddName":"bigbird26.cern.ch"}]'
        changed = '[{"Name":"owner@cern.ch","ScheddName":"bigbird14.cern.ch"}]'
        for responses in (["[]"], [discovery, "[]", changed]):
            with self.subTest(responses=responses), patch.object(native, "run_condor", side_effect=responses):
                with self.assertRaises(native.NativeCondorError):
                    native.query_account("owner", "ClusterId > 0")

    def test_worker_account_query_selects_submission_pool_everywhere(self):
        discovery = '[{"Name":"owner@cern.ch","ScheddName":"bigbird26.cern.ch"}]'
        with patch.object(native, "run_condor", side_effect=[discovery, "[]", discovery]) as run:
            self.assertEqual(native.query_account("owner", "ClusterId > 0", pool="tweetybird04.cern.ch"), [])
        for call in run.call_args_list:
            self.assertEqual(call.args[0][1:3], ["-pool", "tweetybird04.cern.ch"])

    def test_any_account_scheduler_failure_rejects_partial_coverage(self):
        discovery = json.dumps([
            {"Name": "group.users.owner@cern.ch", "ScheddName": "bigbird26.cern.ch"},
            {"Name": "owner@cern.ch", "ScheddName": "bigbird14.cern.ch"}])
        with patch.object(native, "run_condor", side_effect=[discovery, "[]", native.NativeCondorError("unreachable")]):
            with self.assertRaises(native.NativeCondorError):
                native.query_account("owner", "ShiftProductionSuite == true")

    def test_edits_restrict_one_job_quote_strings_and_confirm_match(self):
        output = 'Set attribute "DAGMan_MaxJobs" for 1 matching jobs.\n'
        output += 'Set attribute "Arguments" for 1 matching jobs.\n'
        argument = '-Dag /afs/campaign.dag -MaxJobs 75'
        with patch.object(native, "run_condor", return_value=output) as run:
            self.assertEqual(native.edit("12", {"DAGMan_MaxJobs": 75, "Arguments": argument}), output)
        self.assertEqual(run.call_args.args[0],
                         ["/usr/bin/condor_qedit", "12.0", "DAGMan_MaxJobs", "75",
                          "Arguments", json.dumps(argument)])
        with self.assertRaises(ValueError):
            native.edit("owner", {"DAGMan_MaxJobs": 75})
        with patch.object(native, "run_condor", return_value='Set attribute "DAGMan_MaxJobs" for 0 matching jobs.'):
            with self.assertRaises(native.NativeCondorError):
                native.edit("12.0", {"DAGMan_MaxJobs": 75})

    def test_command_errors_and_partial_warning_are_unknown(self):
        for returncode, stderr in ((1, "failure"), (0, "some schedds unreachable")):
            child = Mock(returncode=returncode)
            child.communicate.return_value = ('[{"ClusterId":7}]', stderr)
            with self.subTest(returncode=returncode), patch.object(native.subprocess, "Popen", return_value=child):
                with self.assertRaises(native.NativeCondorError):
                    native.run_condor(["/usr/bin/condor_q", "-global", "-json"])

    def test_timeout_kills_descendant_that_holds_output_pipe(self):
        with tempfile.TemporaryDirectory(prefix="shift_native_condor_test_") as folder:
            pid_file = Path(folder) / "descendant.pid"
            code = ("import subprocess,sys,time; from pathlib import Path; "
                    "child=subprocess.Popen([sys.executable,'-I','-S','-c','import time;time.sleep(30)']); "
                    "Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)")
            started = time.monotonic()
            try:
                with self.assertRaises(native.NativeCondorTimeout):
                    native.run_condor([str(Path(sys.executable).resolve()), "-I", "-S", "-c", code,
                                       str(pid_file)], timeout=0.7, local_schedd=False)
                self.assertLess(time.monotonic() - started, 3)
                self.assertTrue(pid_file.exists(), "The descendant must be created to exercise group termination")
                descendant = int(pid_file.read_text())
                stat = Path("/proc") / str(descendant) / "stat"
                state = None
                deadline = time.monotonic() + 0.25
                while time.monotonic() < deadline:
                    try:
                        state = stat.read_text().split(")", 1)[1].split()[0]
                    except (FileNotFoundError, ProcessLookupError):
                        state = None
                    if state in (None, "Z"):
                        break
                    time.sleep(0.01)
                self.assertIn(state, (None, "Z"))
            finally:
                # Preserve a bounded test even if its assertion exposes a bug.
                if pid_file.exists():
                    try:
                        os.kill(int(pid_file.read_text()), 9)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
