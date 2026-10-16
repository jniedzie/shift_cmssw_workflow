# Code style: AI-code. AI-generated fixed-source task preparation regressions.
import json
import io
from pathlib import Path
import sys
import tempfile
import tarfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import prepare_shift_stream_tasks as module
from run_shift_stream_controller import load_tasks, save, sha


class FactoryTests(unittest.TestCase):
    def setUp(self):
        parent = SCRIPTS.parent / 'tmp'
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='20261016-stream-factory-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('tasks', 'cohorts', 'frozen'):
            (self.root/name).mkdir()
        worker = self.root/'frozen/worker.sh';worker.write_text('#!/bin/bash\nexit 0\n')
        archive = self.root/'frozen/frozen_tools.tar.gz';archive.write_bytes(b'test-only-tools')
        self.policy = dict(tag='stream', output_campaign='20261016_mu_enriched_stream_v1',
            worker=dict(executable=str(worker), executable_sha256=sha(worker),
                        tools_archive=str(archive), tools_sha256=sha(archive)),
            tools=dict(selection='@TOOLS@/scripts/shift_streaming_qcd_inputs.py',
                reco='@TOOLS@/scripts/run_shift_gen_to_nano.py', templates='@TOOLS@/templates',
                histograms='@TOOLS@/tea/utils/shift_streaming_histograms.py',
                histogrammer='@SCRATCH@/runtime/bin/shift_histogrammer',
                merger='@SCRATCH@/runtime/scripts/merge.py'),
            snapshot_eos_base='/eos/user/j/jniedzie/shift_cmssw_support/stream/snapshots',
            plot_output='/unused/project/plots', canary_source_ids=['production_qcd_0to1_0000'])
        self.source = dict(source_id='production_qcd_0to1_0000', stratum='qcd_0to1',
            gen='/eos/user/j/jniedzie/shift_cmssw/qcd_pThat0to1GeV/20261015_mu_enriched_v1/GEN/source/gen.root',
            gen_sha256='a'*64, event_ids=[[42,1,1], [42,1,2]], weights=[1.,1.],
            normalization=dict(schema='shift-stream-source-exposure-v1', source_id='production_qcd_0to1_0000',
                stratum='qcd_0to1', full_source_trials=400, sigma_nd_pb=123., gen_sha256='a'*64))
        self.selected = self.source | dict(selected_events=[
            dict(raw_index=0,event_id=[42,1,1],native_weight=1.,sampling_probability=.5)],
            selected_indices=[0], sampling_probabilities=[.5], sampling={'plan_sha256':'b'*64})
        self.slot = dict(source_id=self.source['source_id'], receipt='/unused/receipt', manifest='/unused/manifest')

    def source_step(self, ledger):
        with mock.patch.object(module, 'source_descriptor', return_value=self.source), \
                mock.patch.object(module, 'select_events', return_value=self.selected):
            module.prepare_source(self.root,self.policy,{},self.slot,3,ledger)

    def task(self, suffix):
        return json.loads((self.root/'tasks'/(self.source['source_id']+suffix+'.json')).read_text())

    def test_scan_is_separate_and_never_reconstructs_before_audit(self):
        self.source_step({})
        self.assertEqual(len(list((self.root/'tasks').glob('*.json'))),1)
        task=self.task('_scan');request=module.read(Path(task['working_directory'])/'request.json')
        self.assertEqual(request['argv'][2],'scan')
        self.assertEqual(request['status_fields'],{})
        self.assertTrue(task['operational_canary'])
        description=Path(task['submit_file']).read_text()
        self.assertIn('transfer_output_files = status.json,result.json,return_payload.tar.gz',description)
        self.assertNotIn('transfer_output_remaps',description)
        self.assertIn('request_memory = 2048',description)

    def test_fixed_selection_creates_one_event_jobs_without_changing_native_weights(self):
        self.source_step({})
        task=self.task('_scan');save(task['result_path'],dict(complete=True,events=[]))
        self.source_step({self.source['source_id']+'_scan':{'phase':'complete'}})
        task=self.task('_reco_000');request=module.read(Path(task['working_directory'])/'request.json')
        self.assertEqual(request['argv'][request['argv'].index('--count')+1],'1')
        self.assertIn('--remove-consumed-intermediates',request['argv'])
        descriptor=module.read(self.root/'cohorts'/task['cohort_id']/'selected.json')
        self.assertEqual(descriptor['weights'],[1.,1.])
        self.assertEqual(descriptor['sampling_probabilities'],[.5])
        self.assertEqual(task['completion_checks']['selected_event_ids_sha256'],module.digest([[42,1,1]]))
        self.assertLess(230000000+(10000+1044*32+31)*100,900000000)

    def test_first_scan_must_return_before_other_sources_are_prepared(self):
        slots=[]
        for index,stratum in enumerate(module.STRATA):
            receipt=self.root/('receipt'+str(index)+'.json');receipt.write_text('{}')
            slots.append(dict(source_id=stratum+'_0',stratum=stratum,request={'chunk_index':0},receipt=str(receipt)))
        module.unchanged(self.root/'source_plan.json',{'source_slots':slots,'plan_sha256':'a'*64})
        policy=self.policy|{'maximum_open_sources':6,'maximum_open_sources_before_first_scan':1}
        module.unchanged(self.root/'factory_policy.json',policy)
        with mock.patch.object(module,'validate_plan'),mock.patch.object(module,'prepare_source') as prepare, \
                mock.patch.object(module,'prepare_snapshots'),mock.patch.object(module,'publish_returned_plots'), \
                mock.patch.object(module,'complete_operational_gate'):
            checkpoint=module.prepare(self.root)
            self.assertEqual(prepare.call_count,1)
            self.assertEqual(checkpoint['open_sources'],1)
            save(self.root/'ledger.json',{slots[0]['source_id']+'_scan':{'phase':'complete'}})
            prepare.reset_mock()
            checkpoint=module.prepare(self.root)
            self.assertEqual(prepare.call_count,6)
            self.assertEqual(checkpoint['open_sources'],6)

    def test_oversized_selection_cannot_overlap_next_source_job_ids(self):
        self.source_step({})
        scan = self.task('_scan')
        save(scan['result_path'], dict(complete=True, events=[]))
        self.selected['selected_events'] = [
            dict(raw_index=index, event_id=[42, 1, index + 1],
                 native_weight=1., sampling_probability=1.)
            for index in range(33)]
        with self.assertRaisesRegex(ValueError, 'at most 32 RECO job IDs'):
            self.source_step({self.source['source_id']+'_scan': {'phase': 'complete'}})
        self.assertEqual(list((self.root/'tasks').glob('*_reco_*.json')), [])

    def test_histogram_waits_for_every_selected_original_and_preserves_full_trials(self):
        self.source_step({});scan=self.task('_scan');save(scan['result_path'],dict(complete=True,events=[]))
        ledger={self.source['source_id']+'_scan':{'phase':'complete'}}
        self.source_step(ledger)
        self.assertFalse((self.root/'tasks'/(self.source['source_id']+'_histogram.json')).exists())
        reco=self.task('_reco_000')
        save(reco['status_path'],dict(nano_path='/eos/user/j/jniedzie/example/nano.root',nano_sha256='c'*64,events=1))
        ledger[self.source['source_id']+'_reco_000']={'phase':'complete'}
        self.source_step(ledger)
        histogram=self.task('_histogram');request=module.read(Path(histogram['working_directory'])/'request.json')
        self.assertIn('--retire-consumed-nano',request['argv'])
        cohort=module.read(self.root/'cohorts'/histogram['cohort_id']/'nano_cohort.json')
        self.assertEqual(cohort['normalization']['full_source_trials'],400)
        self.assertEqual(cohort['selected_events'][0]['sampling_probability'],.5)
        self.assertEqual(cohort['expected_reco_job_ids'],[10096])

    def test_snapshot_never_replaces_missing_first_source_with_finished_later_one(self):
        slots=[]
        for stratum in module.STRATA:
            for index in (0,1):
                slots.append(dict(source_id=stratum+'_'+str(index),stratum=stratum,request={'chunk_index':index}))
        ledger={slot['source_id']+'_histogram':{'phase':'complete'} for slot in slots
                if slot['request']['chunk_index']==1}
        module.prepare_snapshots(self.root,self.policy,{'source_slots':slots},ledger)
        self.assertEqual(list((self.root/'tasks').glob('snapshot*')),[])

    def test_immutable_prepared_values_cannot_be_overwritten(self):
        path=self.root/'input.json';module.unchanged(path,{'a':1})
        module.unchanged(path,{'a':1})
        with self.assertRaisesRegex(ValueError,'changed'):module.unchanged(path,{'a':2})

    def test_histogram_archive_override_keeps_scan_and_reco_archives_unchanged(self):
        overlay = self.root / 'corrected'
        overlay.mkdir()
        archive = overlay / 'frozen_tools.tar.gz'
        archive.write_bytes(b'corrected histogram runtime only')
        self.policy['histogram_worker'] = self.policy['worker'] | {
            'tools_archive': str(archive), 'tools_sha256': sha(archive)}
        for stage in ('prepare', 'reco', 'histogram', 'merge'):
            payload = module.request(stage, 'source0', 'tea', ['python3', 'tool.py'], 'receipt.json', self.policy)
            task = module.make_task(self.root, self.policy, 'source0_' + stage, stage, 'source0', payload, [])
            pins = task['input_sha256']
            expected = str(archive) if stage in ('histogram', 'merge') else self.policy['worker']['tools_archive']
            self.assertIn(expected, pins)
            self.assertEqual(Path(task['submit_file']).read_text().count('frozen_tools.tar.gz'), 1)

    def test_replacement_factory_policy_cannot_change_selection_or_reco(self):
        save(self.root / 'factory_policy.json', self.policy)
        save(self.root / 'source_plan.json', {})
        replacement = self.root / 'replacement_policy.json'
        for key, value in (('output_campaign', 'changed'), ('worker', {}), ('canary_source_ids', [])):
            with self.subTest(key=key):
                save(replacement, self.policy | {key: value})
                with self.assertRaisesRegex(ValueError, 'only the histogram worker'):
                    module.prepare(self.root, replacement)

    def plot_task(self, member_name='histograms/plots/raw/mass.pdf'):
        self.policy['plot_output']=str(self.root/'project/plots/sm_streaming')
        payload=module.request('histogram','source0','tea',['python3','tool.py'],'receipt.json',self.policy)
        payload['return_files']=['histograms/plots']
        task=module.make_task(self.root,self.policy,'source0_histogram','histogram','source0',payload,[])
        archive_path=Path(task['working_directory'])/'return_payload.tar.gz'
        data=b'%PDF-1.4\nsynthetic unit-test reference\n'
        with tarfile.open(archive_path,'w:gz') as archive:
            info=tarfile.TarInfo(member_name);info.size=len(data)
            archive.addfile(info,io.BytesIO(data))
        save(task['status_path'],{'return_payload_sha256':sha(archive_path)})
        return task,archive_path,{'source0_histogram':{'phase':'complete'}}

    def test_plot_publication_checks_hash_then_removes_duplicate_archive(self):
        task,archive_path,ledger=self.plot_task()
        module.publish_returned_plots(self.root,self.policy,ledger)
        self.assertTrue((self.root/'project/plots/sm_streaming/source0/raw/mass.pdf').exists())
        self.assertFalse(archive_path.exists())
        self.assertTrue((Path(task['working_directory'])/'plots_published.json').exists())
        module.publish_returned_plots(self.root,self.policy,ledger)

    def test_plot_archive_traversal_is_rejected(self):
        _,archive_path,ledger=self.plot_task('histograms/plots/../../outside.pdf')
        with self.assertRaisesRegex(ValueError,'unsafe entry'):
            module.publish_returned_plots(self.root,self.policy,ledger)
        self.assertTrue(archive_path.exists())

    def test_plot_archive_checksum_mismatch_is_rejected(self):
        _,archive_path,ledger=self.plot_task()
        with archive_path.open('ab') as stream:stream.write(b'changed')
        with self.assertRaisesRegex(ValueError,'differs'):
            module.publish_returned_plots(self.root,self.policy,ledger)


if __name__ == '__main__':
    unittest.main()
