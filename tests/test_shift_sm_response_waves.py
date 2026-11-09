# Code style: AI-code.
"""Worker capacity stays bounded as early waves drain and later ones become ready."""
import sys
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_shift_sm_response_waves import limits,native_launch_failure,rebalance,supervise

class WaveBudgetTest(unittest.TestCase):
    def test_one_ready_wave_receives_the_available_budget(self):
        self.assertEqual(limits({'first':4},2000),{'first':2000})

    def test_new_waves_share_capacity_without_counting_live_workers_twice(self):
        current={'first':1500,'second':10,'third':10,'fourth':10,'waiting':1}
        result=limits(current,2000)
        self.assertEqual(result['waiting'],1)
        self.assertLess(result['first'],current['first'])
        self.assertLessEqual(sum(max(current[k],result[k]) for k in current),2000)
        self.assertGreater(result['second'],10)

    def test_temporary_overlap_drains_without_new_admission(self):
        result=limits({'first':1500,'second':1000},2000)
        self.assertEqual(result,{'first':1,'second':1})

    def test_only_the_reviewed_native_lock_failure_is_recoverable(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary)
            (p/'events.log').write_text('Normal termination (return value 1)')
            (p/'worker.err').write_text('prepare_wave\nfcntl.flock\nBlockingIOError: [Errno 11]')
            self.assertTrue(native_launch_failure(p))
            (p/'worker.err').write_text('ValueError: wrong physical event weights')
            self.assertFalse(native_launch_failure(p))

    def test_held_preflight_does_not_reserve_the_whole_worker_pool(self):
        calls=[]
        class Client:
            def __init__(self,jid): self.jid=jid
            def throttle(self,**kwargs):
                calls.append((self.jid,kwargs))
                return dict(DAGMan_MaxJobs=kwargs['max_nodes'])
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            waves={}
            for wid,jid in [('blocked',10),('ready',20)]:
                d=root/wid;d.mkdir();(d/'dagman.config').write_text('DAGMAN_MAX_JOBS_SUBMITTED = 2000\n')
                waves[wid]=dict(cluster=jid,directory=str(d))
            ads=[dict(ClusterId=10,JobUniverse=7,JobStatus=2,DAG_NodesReady=0),
                 dict(ClusterId=20,JobUniverse=7,JobStatus=2,DAG_NodesReady=1000),
                 dict(ClusterId=30,JobUniverse=5,JobStatus=5,DAGManJobId=10)]
            binding=SimpleNamespace(DAGMan=Client,param={})
            with patch.dict(sys.modules,{'htcondor2':binding}), \
                    patch('run_shift_sm_response_waves.query',return_value=ads) as query:
                result=rebalance(dict(worker_budget=2000),waves)
            self.assertIn('DAG_NodesReady',query.call_args.args[1])
            self.assertEqual(result['limits'],{'blocked':1,'ready':1999})
            self.assertEqual(result['held_physics_workers_released'],0)

    def test_real_native_abort_is_preserved_then_next_poll_can_admit(self):
        import json
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            command=[sys.executable,'-c',"import os,sys,resource;from pathlib import Path;resource.setrlimit(resource.RLIMIT_CORE,(0,0));p=Path(sys.argv[1]);first=not p.exists();p.write_text('admitted' if not first else 'first');sys.stderr.write('none_dealloc: deallocating None\\n') if first else None;sys.stderr.flush();os.abort() if first else None",str(root/'admission')]
            config=dict(working_directory=str(root),failure_path=str(root/'failure.json'),
                        poll_seconds=0,recovery_poll_seconds=0)
            supervise(root/'config.json',config,command,maximum_polls=2)
            self.assertEqual((root/'admission').read_text(),'admitted')
            failure=json.loads((root/'native_abort_01.json').read_text())
            self.assertEqual(failure['returncode'],-6)
            self.assertEqual(json.loads((root/'admission_supervisor_heartbeat.json').read_text())['reviewed_native_abort_restarts'],1)

    def test_unknown_controller_failure_is_not_blindly_replayed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            config=dict(working_directory=str(root),failure_path=str(root/'failure.json'))
            with self.assertRaisesRegex(RuntimeError,'Admission poll failed'):
                supervise(root/'config.json',config,[sys.executable,'-c','raise ValueError("unknown scientific input")'],maximum_polls=2)
            self.assertTrue((root/'failure.json').is_file())
            self.assertFalse((root/'native_abort_02.json').exists())
