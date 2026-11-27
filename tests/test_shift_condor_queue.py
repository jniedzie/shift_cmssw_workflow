# Code style: AI-code.

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from shift_condor_queue import find_manager, is_controller, jobs_in_dag, show_manager, summarize


class CondorQueueTest(unittest.TestCase):
    def test_dag_steps_and_jobs_use_separate_counts(self):
        manager = {
            "ClusterId": 10, "ProcId": 0, "Cmd": "/usr/bin/condor_dagman",
            "JobBatchName": "controller_run", "JobStatus": 2,
            "DAG_NodesTotal": 2, "DAG_NodesQueued": 2,
        }
        nested = {
            "ClusterId": 11, "ProcId": 0, "DAGManJobId": 10,
            "Cmd": "/usr/bin/condor_dagman", "JobStatus": 2,
        }
        workers = [
            {"ClusterId": 12, "ProcId": 0, "DAGManJobId": 10, "JobStatus": 1},
            {"ClusterId": 12, "ProcId": 1, "DAGManJobId": 10, "JobStatus": 2},
            {"ClusterId": 13, "ProcId": 0, "DAGManJobId": 11, "JobStatus": 5},
        ]
        unrelated = {"ClusterId": 14, "ProcId": 0, "JobStatus": 2}
        ads = [manager, nested, *workers, unrelated]
        for target in ("10", "10.0", "controller_run"):
            self.assertEqual(find_manager(ads, target), manager)
        scoped = jobs_in_dag(ads, manager)
        output = StringIO()
        with redirect_stdout(output):
            show_manager(manager)
            summarize(scoped, by_batch=False)
        text = output.getvalue()
        self.assertIn("2 total  |  2 submitted", text)
        rows = [line.split() for line in text.splitlines()[-3:]]
        self.assertEqual([int(row[-1]) for row in rows], [3, 2, 5])

    def test_legacy_dispatcher_role_depends_on_native_node(self):
        dispatcher = {"JobBatchName": "SHIFT_DP_legacy", "Cmd": "/path/dispatch_v3.sh"}
        self.assertTrue(is_controller(dict(dispatcher, DAGNodeName="RESPONSE33")))
        self.assertFalse(is_controller(dict(dispatcher, DAGNodeName="PREP33")))
        self.assertFalse(is_controller(dict(dispatcher, DAGNodeName="RESPONSE33", Cmd="/bin/bash")))

    def test_ambiguous_manager_name_requires_job_id(self):
        manager = {"ClusterId": 10, "ProcId": 0, "Cmd": "/usr/bin/condor_dagman",
                   "JobBatchName": "controller_run"}
        with self.assertRaisesRegex(ValueError, "Use a job ID"):
            find_manager([manager, dict(manager, ClusterId=11)], "controller_run")


if __name__ == "__main__":
    unittest.main()
