# Code style: AI-code. AI-generated tests for bounded native response DAG preparation.
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_shift_sm_response_jobs as jobs


def json_file(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    jobs.write_json(path, record)
    return dict(path=str(path), sha256=jobs.sha(path))


def fixture(root):
    base=root/'old';(base/'scripts').mkdir(parents=True,exist_ok=True)
    json_file(base/'runtimes.json',dict(cmssw=dict(sha256='r'*64,url='root://example/runtime')))
    (base/'scripts/run_shift_smart_sm_detector.py').write_text('old detector bytes\n')
    archive=root/'old.tar.gz'
    with tarfile.open(archive,'w:gz') as stream:
        for path in base.rglob('*'):
            if path.is_file():stream.add(path,arcname=str(path.relative_to(base)))
    bootstrap=root/'old_worker.sh';bootstrap.write_text('#!/bin/bash\nexit 0\n')
    plugin=root/jobs.PLUGIN;plugin.write_bytes(b'qualified-plugin')
    build=json_file(root/'build.json',dict(plugin_sha256=jobs.sha(plugin),runtime_sha256='r'*64))
    helpers=[]
    for name in jobs.REQUIRED_HELPERS:
        path=root/'helpers'/name;path.parent.mkdir(exist_ok=True);path.write_text('# new helper '+name+'\n')
        helpers.append(dict(path=str(path),sha256=jobs.sha(path)))
    selected=[];sources=[];exposure=[]
    for number,region in enumerate(('credible_shallow_pointing','no_both_sides_pointing_pair')):
        raw=str(number+1)*64;prepared=str(number+3)*64;source_id='qcd_source'+str(number)
        norm=dict(gen_sha256=raw,saved_events=3,full_source_trials=100)
        descriptor=dict(gen='root://example/'+source_id,gen_sha256=prepared,
            event_ids=[[number+1,1,10],[number+1,1,20],[number+1,1,30]],weights=[1.,2.,3.],
            selected_indices=[2,0],sampling_probabilities=[.25,.5],stratum='qcd_20toinf',
            normalization_record=norm,nano_base='/old/nano',metadata_base='/old/meta',
            maximum_publication_bytes=42,production_selection=dict(frozen=True))
        authority=json_file(root/(source_id+'.json'),dict(complete=True,exit_code=0,
            source=norm,selected_source=descriptor))
        sources.append(dict(source_id=source_id,original_selected_source=descriptor,
            original_result_path=authority['path'],original_result_sha256=authority['sha256'],
            full_original_exposure=norm,selected_descriptor_canonical_json_sha256=hashlib.sha256(
                json.dumps(descriptor,sort_keys=True,separators=(',',':')).encode()).hexdigest()))
        selected.append(dict(source_id=source_id,gen_sha256=raw,prepared_gen_sha256=prepared,
            stratum='qcd_20toinf',event_id=[number+1,1,30],original_index=2,selected_position=0,
            native_weight=3.,existing_sampling_probability=.25,benchmark_inclusion_probability=.1,
            benchmark_region=region,draw_hash=str(number)))
        exposure.append(dict(original_raw_gen_sha256=raw,old_selected_events=2,original_saved_events=3))
    extra=copy.deepcopy(selected[0]);extra.update(event_id=[1,1,10],original_index=0,
        selected_position=1,native_weight=1.,existing_sampling_probability=.5,draw_hash='2')
    selected.append(extra)
    plan=json_file(root/'plan.json',dict(draw_frozen=True,population_events=4,selected=selected))
    return dict(base_tools=dict(path=str(archive),sha256=jobs.sha(archive)),
        bootstrap=dict(path=str(bootstrap),sha256=jobs.sha(bootstrap)),
        addon=dict(path=str(plugin),sha256=jobs.sha(plugin)),build_receipt=build,helper_files=helpers,
        benchmark_plan=plan,selected_source_pins=json_file(root/'sources.json',dict(
            frozen_benchmark_sha256=plan['sha256'],sources=sources)),
        full_population_exposure=json_file(root/'exposure.json',dict(sources=exposure,
            includes_sources_with_no_benchmark_event_drawn=True,original_saved_GEN_events=6)),
        positive_reference=json_file(root/'positive.json',dict(nano_path='root://reference')),
        output_eos_directory='/eos/user/j/jniedzie/shift_cmssw_support/response_test')


class ResponseJobsTest(unittest.TestCase):
    def test_native_dependencies_census_weights_and_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=fixture(root);output=root/'deployment'
            with patch.object(jobs,'persistent_directory',side_effect=Path):
                result=jobs.prepare(config,output)
            self.assertEqual(len(result['preflight_jobs']),2)
            self.assertEqual(len(result['detector_jobs']),3)
            self.assertEqual(len(result['jobs']),7)
            self.assertEqual(result['publication_reservation_bytes'],(3*16+2+128)*jobs.MIB)
            self.assertFalse(result['jobs_submitted'])
            dag=(output/'response.dag').read_text()
            self.assertIn('PARENT DET_0001 DET_0002 CHILD CANARY_VALIDATED',dag)
            self.assertIn('PARENT CANARY_VALIDATED CHILD DET_0003',dag)
            self.assertIn('PARENT PREF_0001 CHILD DET_0003',dag)
            self.assertNotIn('PARENT CANARY_VALIDATED CHILD DET_0001',dag)
            for record in result['jobs']:
                directory=Path(record['job_directory']);submit=(directory/'worker.sub').read_text()
                self.assertIn('initialdir = '+str(directory),submit)
                self.assertIn('+SendCredential = True',submit)
                self.assertIn('periodic_release = False',submit)
                self.assertIn('on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)',submit)
            request=jobs.read(output/'jobs/det_0003/request.json')
            self.assertEqual(request['selected_skip'],1)
            self.assertEqual(request['detector_source']['sampling_probabilities'],[.25,.5])
            self.assertEqual(request['benchmark_inclusion_probability'],.1)
            self.assertEqual(request['detector_source']['weights'],[1.,2.,3.])
            pref=jobs.read(output/'jobs/pref_0001/request.json')
            self.assertEqual(len(pref['sources'][0]['full_event_ids']),3)
            self.assertEqual(pref['schema'],'shift-stream-payload-v1')
            self.assertEqual(pref['sources'][0]['source_descriptor']['nano_base'],'/old/nano')
            self.assertIn('-no_submit',result['native_prepare_command'])
            for name,checksum in result['frozen_tools']['old_tools_members_unchanged'].items():
                self.assertEqual(jobs.sha(output/'frozen/tools'/name),checksum)
            with patch.object(jobs,'persistent_directory',side_effect=Path):
                with self.assertRaisesRegex(ValueError,'overwrite'):jobs.prepare(config,output)

    def test_changed_source_authority_is_rejected_before_freeze(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=fixture(root)
            source=jobs.read(config['selected_source_pins']['path'])['sources'][0]
            Path(source['original_result_path']).write_text('{}')
            with patch.object(jobs,'persistent_directory',side_effect=Path):
                with self.assertRaisesRegex(ValueError,'input changed'):jobs.prepare(config,root/'deployment')
            self.assertFalse((root/'deployment').exists())

    def test_support_storage_and_persistent_iwd_required(self):
        for path in ('relative','/tmp/test','/pool/condor/dir_42','/afs/path with space'):
            with self.assertRaises(ValueError):jobs.persistent_directory(path)
        self.assertEqual(jobs.persistent_directory('/afs/persistent'),Path('/afs/persistent'))
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=fixture(root);config['output_eos_directory']='/eos/user/j/jniedzie/shift_cmssw/qcd'
            with patch.object(jobs,'persistent_directory',side_effect=Path):
                with self.assertRaisesRegex(ValueError,'support storage'):jobs.prepare(config,root/'deployment')

    def test_capsule_input_hash_and_runtime_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=fixture(root);config['addon']['sha256']='f'*64
            with self.assertRaisesRegex(ValueError,'capsule input changed'):
                jobs.freeze_tools(config,root/'frozen')
            config=fixture(root);receipt=jobs.read(config['build_receipt']['path']);receipt['runtime_sha256']='x'*64
            config['build_receipt']=json_file(root/'build.json',receipt)
            with self.assertRaisesRegex(ValueError,'different old runtime'):
                jobs.freeze_tools(config,root/'frozen')

    def test_failure_adapter_preserves_exit_report_and_bounds_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);wrapper=root/'run_shift_sm_response_worker.py';wrapper.write_text(jobs.WRAPPER)
            helper=root/'run_shift_smart_sm_detector.py'
            helper.write_text("from pathlib import Path\nPath('step1.log').write_bytes(b'x'*300000)\nPath('report.json').write_text('original report')\nraise SystemExit(7)\n")
            json_file(root/'request.json',dict(stage='smart_sm_detector'))
            result=subprocess.run([sys.executable,str(wrapper),'request.json'],cwd=root,capture_output=True)
            self.assertEqual(result.returncode,7,result.stderr)
            self.assertEqual((root/'report.json').read_text(),'original report')
            self.assertEqual((root/'failure_logs/step1.log.tail').stat().st_size,128*1024)
            self.assertEqual(len(list((root/'failure_logs').iterdir())),1)
            helper.write_text("from pathlib import Path\nPath('step2.log').write_text('success')\n")
            result=subprocess.run([sys.executable,str(wrapper),'request.json'],cwd=root,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((root/'failure_logs/step2.log.tail').exists())


if __name__=='__main__':unittest.main()
