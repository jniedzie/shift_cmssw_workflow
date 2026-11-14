# Code style: AI-code.
"""Exercise real admission with separate running/idle budgets and quota headroom."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_shift_smart_sm_controller as ctl


class WorkerCapacityTest(unittest.TestCase):
    def fixture(self, root, stale=False):
        cfg=dict(campaign='capacity-test',worker_template='initialdir = @DIRECTORY@\nqueue 1\n',
            production=dict(qcd_low=dict(chunks=100)),combined_canaries={},combined_clusters={},
            source_prefetch_per_bin=500,account_worker_ceiling=2000,maximum_idle_workers=1800,
            maximum_starts_per_poll=100,own_reservation_bytes=10**12,
            external_future_reservation_bytes=0,safety_floor_bytes=0)
        canary=root/'canary';canary.mkdir();ctl.save(canary/'status.json',dict(complete=True))
        (canary/'events.log').write_text('005 (7.000.000) terminal\nNormal termination (return value 0)\n...\n')
        cfg.update(combined_canaries=dict(qcd=str(canary)),combined_clusters=dict(qcd=7))
        state=dict(tasks={},next_detector_job=1,last_inventory_count=1)
        for index in range(100):
            name='source_'+str(index)
            state['tasks'][name]=ctl.make_task(root,cfg,name,{},'source','qcd_low')
        cfg['finite_source_worklist']=[dict(task_name=n,stratum=t['stratum'],request_sha256=t['request_sha256'])
            for n,t in state['tasks'].items()]
        nano=root/'old-nano';nano.mkdir()
        state['tasks']['old-nano']=dict(kind='detector',phase='done',stratum='qcd_low',directory=str(nano))
        for index in range(2 if stale else 1):
            name='quota_'+str(index)
            task=ctl.make_task(root,cfg,name,{},'quota','')
            if not stale:
                task['phase']='done'
                ctl.save(Path(task['directory'])/'result.json',dict(complete=True,checked_epoch=time.time(),
                    free_bytes=10**15,free_files=10**8))
            state['tasks'][name]=task
        return cfg,state

    def ads(self, running, idle):
        return [dict(ClusterId=100+i,ProcId=0,JobUniverse=5,JobStatus=2 if i<running else 1,
                     Iwd='/unrelated/'+str(i))for i in range(running+idle)]

    def test_1344_running_and_855_idle_admit_one_hundred_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cfg,state=self.fixture(root);clusters=iter(range(9000,9100))
            def submit(*args,**kwargs):
                c=next(clusters);return f'{c}.0 - {c}.0\n'
            with patch.object(ctl,'query',return_value=self.ads(1344,855)), \
                    patch.object(ctl,'run_condor',side_effect=submit) as native:
                record=ctl.step(root,cfg,state)
            self.assertEqual(native.call_count,100)
            self.assertEqual(record['submitted_this_poll'],100)
            self.assertEqual(record['account_running_workers'],1344)
            self.assertTrue(all(t['phase']=='submitted'for t in state['tasks'].values()if t['kind']=='source'))

    def test_full_running_and_idle_budgets_still_admit_only_one_quota(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cfg,state=self.fixture(root,stale=True)
            with patch.object(ctl,'query',return_value=self.ads(2000,1800)), \
                    patch.object(ctl,'run_condor',return_value='9000.0 - 9000.0\n') as native:
                record=ctl.step(root,cfg,state)
            native.assert_called_once()
            self.assertEqual(record['submitted_this_poll'],1)
            quotas=[t for t in state['tasks'].values()if t['kind']=='quota']
            self.assertEqual(sum(t['phase']=='submitted'for t in quotas),1)
            self.assertEqual(sum(t['phase']=='ready'for t in quotas),1)
            self.assertTrue(all(t['phase']=='ready'for t in state['tasks'].values()if t['kind']=='source'))


if __name__=='__main__':unittest.main()
