from pathlib import Path
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from monitor_dark_photon_grid import successful_terminal
import monitor_dark_photon_grid as monitor

class TerminalPointOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.ad=dict(Owner='jniedzie',DAGManJobId=123,ShiftBSMGrid=True,
                     ShiftBSMPoint='m30_prompt',JobStatus=4,ExitBySignal=False,ExitCode=0)
    def test_exact_terminal_success(self):
        self.assertTrue(successful_terminal(self.ad,'m30_prompt',123,'jniedzie'))
    def test_foreign_active_removed_held_or_failed_jobs_are_rejected(self):
        for key,value in (('Owner','foreign'),('DAGManJobId',456),('ShiftBSMGrid',False),
                          ('ShiftBSMPoint','other'),('JobStatus',1),('JobStatus',2),('JobStatus',3),
                          ('JobStatus',5),('ExitBySignal',True),('ExitCode',1)):
            ad={**self.ad,key:value};self.assertFalse(successful_terminal(ad,'m30_prompt',123,'jniedzie'))
    def test_unknown_exit_state_is_rejected(self):
        for key in ('ExitBySignal','ExitCode'):
            ad=dict(self.ad);del ad[key];self.assertFalse(successful_terminal(ad,'m30_prompt',123,'jniedzie'))


class ControllerRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);(self.base/'preparation_v2').mkdir();(self.base/'retirement_tools').mkdir()
        (self.base/'retirement_tools/manifest.json').write_text('{}')
        self.points=[]
        for name,mass in (('m15_prompt',15),('m30_prompt',30)):
            folder=self.base/'runs'/name;det=folder/'detector';det.mkdir(parents=True)
            (det/'nano.root').write_bytes(b'validated nano fixture');(det/'histograms.root').write_bytes(b'validated hist fixture')
            self.points.append(dict(point=name,gen_directory=str(folder/'gen'),detector_directory=str(det),
                                    mass_gev=mass,epsilon=.001,events=20,target_mean_lab_flight_m=None))
        self.plan=self.base/'preparation_v2/plan.json';self.plan.write_text(json.dumps(dict(points=self.points)))
        for point in self.points:
            folder=Path(point['gen_directory']).parent;det=Path(point['detector_directory'])
            (folder/'point_report.json').write_text(json.dumps(dict(complete=True,point=point['point'],plan_sha256=monitor.sha(self.plan),
                physical_mass_gev=point['mass_gev'],physical_epsilon=.001,generated_events=20,detector_events=20,
                nano_sha256=monitor.sha(det/'nano.root'),histogram_sha256=monitor.sha(det/'histograms.root'))))
        (self.base/'local_canary_qualification.json').write_text('{"complete":true}')
        self.ad=dict(Owner='jniedzie',DAGManJobId=123,ShiftBSMGrid=True,ShiftBSMPoint='m30_prompt',
                     ClusterId=124,ProcId=0,JobStatus=4,ExitBySignal=False,ExitCode=0)

    def invoke(self,clock,fail_first_plot=False,active=False):
        calls=[]
        def query(command):
            if command[0]=='condor_history':return [self.ad]
            return [{**self.ad,'JobStatus':2}] if active else []
        def render(command,**kwargs):
            calls.append(command)
            if fail_first_plot and len(calls)==1:raise RuntimeError('deliberate first plot failure')
            selection=json.loads(Path(command[command.index('--plan')+1]).read_text())
            output=Path(command[command.index('--output-dir')+1]);output.mkdir(parents=True)
            (output/'kinematics_receipt.json').write_text(json.dumps(dict(complete=True,samples=selection['samples'])))
        args=['monitor','--base',str(self.base),'--workspace',str(self.base),'--dag-cluster','123',
              '--timeout-seconds','1','--poll-seconds','10']
        with patch.object(sys,'argv',args),patch.object(monitor,'native_query',side_effect=query),\
             patch.object(monitor.getpass,'getuser',return_value='jniedzie'),\
             patch.object(monitor.time,'monotonic',side_effect=clock),patch.object(monitor.time,'sleep'),\
             patch.object(monitor.subprocess,'run',side_effect=render),patch.object(sys,'stdout',io.StringIO()):
            monitor.main()
        return json.loads((self.base/'monitor_state.json').read_text()),calls

    def test_point_failure_does_not_block_other_complete_point(self):
        folder=Path(self.points[0]['gen_directory']).parent
        (folder/'point_report.json').write_text('{"complete":false}')
        state,calls=self.invoke([0,0,2])
        self.assertFalse(state['complete']);self.assertEqual(state['plotted_points'],['m30_prompt'])
        self.assertTrue(state['points']['m30_prompt']['terminal_validated']);self.assertEqual(len(calls),1)

    def test_failed_final_plot_is_retried_before_completion(self):
        state,calls=self.invoke([0,0,0],fail_first_plot=True)
        self.assertEqual(len(calls),2);self.assertTrue(state['complete'])
        self.assertEqual(state['plotted_points'],['m15_prompt','m30_prompt'])

    def test_active_ad_overrides_old_terminal_history(self):
        state,_=self.invoke([0,0,2],active=True)
        self.assertFalse(state['complete']);self.assertFalse(state['points']['m30_prompt']['terminal_validated'])

    def test_changed_retired_output_invalidates_cached_completion(self):
        detector=Path(self.points[0]['detector_directory'])
        retirement=detector/'retirement_plan.json'
        retirement.write_text(json.dumps(dict(protected=[dict(path=str(detector/'nano.root'),sha256=monitor.sha(detector/'nano.root'))])))
        digest=monitor.sha(retirement)
        (detector/'retirement_progress.jsonl').write_text(json.dumps(dict(action='complete',plan_sha256=digest))+'\n')
        state=dict(schema='shift-dark-photon-grid-monitor-v1',complete=True,dag_cluster=123,
                   plan_sha256=monitor.sha(self.plan),errors=[],
                   points={'m15_prompt':dict(retirement_complete=True,terminal_validated=True,retirement_plan_sha256=digest)})
        (self.base/'monitor_state.json').write_text(json.dumps(state))
        (detector/'nano.root').write_bytes(b'changed after retirement')
        state,_=self.invoke([0,0,2])
        self.assertFalse(state['complete']);self.assertFalse(state['points']['m15_prompt']['terminal_validated'])
        self.assertEqual(state['plotted_points'],['m30_prompt'])

if __name__=='__main__':unittest.main()
