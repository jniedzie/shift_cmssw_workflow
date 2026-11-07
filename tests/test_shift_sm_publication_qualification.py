# Code style: AI-code.
"""A ready positive control must be submitted before its publication gate opens."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_shift_sm_postprocessing_controller as controller

class QualificationAdmissionTest(unittest.TestCase):
    def test_ready_canary_uses_quota_checked_submission_while_science_waits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);parent=root/'quota';parent.mkdir()
            producer=parent/'native';producer.mkdir()
            controller.save(producer/'result.json',dict(complete=True,checked_epoch=time.time(),free_bytes=10000000))
            quota=dict(kind='quota',phase='done',directory=str(producer),native_exit_code=0,
                result_sha256=controller.digest(producer/'result.json'),finished_epoch=time.time())
            controller.save(parent/'ledger.json',dict(tasks=dict(quota=quota)))
            controller.save(root/'parent.json',dict(own_reservation_bytes=1000000,reserved_science_bytes=0,
                external_future_reservation_bytes=0,safety_floor_bytes=0,account_worker_ceiling=100))
            tasks={}
            for key,events in [('science',1),('positive_control',2)]:
                d=root/key;d.mkdir();(d/'worker.sub').write_text('queue 1\n')
                tasks[key]=dict(phase='ready',directory=str(d),events=events,stratum=key,
                    publication_bound_bytes=1000,qualification_only=key=='positive_control')
            state=dict(tasks=tasks,latest={})
            config=dict(parent_state=str(parent),parent_config=str(root/'parent.json'),
                qualification_task='positive_control',maximum_publication_bytes=1000000,
                worker_publication_bound_bytes=1000)
            with patch.object(controller,'query',return_value=[]), \
                    patch.object(controller,'run_condor',return_value='123.0 - 123.0\n') as submit:
                controller.poll(root,config,state)
            self.assertEqual(state['tasks']['positive_control']['phase'],'submitted')
            self.assertEqual(state['tasks']['science']['phase'],'ready')
            self.assertEqual(submit.call_count,1)
            self.assertIn('positive_control',submit.call_args.args[0][-1])
