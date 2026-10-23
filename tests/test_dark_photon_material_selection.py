# Code style: AI-code. AI-generated selected DP accounting regression tests.
"""Selected detector rows retain their original full GEN denominator and W/p."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dark_photon_material_selection import (bind, population, chunk_spec, validate_nano, validate_plan,
                                            rate_only_report, validate_rate_only_report)
from shift_production_selection import default_policy
from shift_material_preselection import checksum


class MaterialPopulation(unittest.TestCase):
    def setUp(self):
        policy = default_policy('dp-regression', 'independent-before-reco')
        self.parent = dict(events=10, gen_sha256='a'*64,
            event_ids=[[100,1,event] for event in range(1,11)])
        self.descriptor = dict(gen_sha256='a'*64, event_ids=self.parent['event_ids'],
            weights=[1.]*10, selected_indices=[1,5,8], sampling_probabilities=[.1,.25,1.],
            production_selection=dict(schema='shift-production-material-selection-v1',
                policy=policy, policy_sha256=checksum(policy), ledger_sha256='b'*64,
                track_all_hadron_decays=True, maximum_untracked_hadron_flight_mm=1.e-5))
        self.row = bind(dict(point='m2_prompt',events=10), self.parent, self.descriptor)

    def test_selected_offsets_keep_full_parent(self):
        original = copy.deepcopy(self.parent)
        ids, probabilities = population(self.row, self.parent)
        self.assertEqual(ids, [[100,1,2],[100,1,6],[100,1,9]])
        self.assertEqual(probabilities,[.1,.25,1.])
        self.assertEqual(self.row['full_gen_events'],10)
        self.assertEqual(self.row['events'],3)
        self.row['chunks']=[dict(chunk=0,skip=1,count=2)]
        self.assertEqual(chunk_spec(dict(points=[self.row]),dict(points={'m2_prompt':self.parent}),
                                    'm2_prompt',0)[2],ids[1:])
        self.assertEqual(self.parent,original)

    def test_changed_full_parent_mask_and_probabilities_fail(self):
        for key,value in [('selected_indices',[1,1,8]),('selected_indices',[8,5,1]),
                          ('selected_indices',[1,5,10]),('sampling_probabilities',[.1,0,1.]),
                          ('sampling_probabilities',[.1,float('nan'),1.]),('gen_sha256','c'*64)]:
            row=copy.deepcopy(self.row); row['material_selection'][key]=value
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                population(row,self.parent)

    def test_zero_draw_keeps_full_exposure(self):
        descriptor=copy.deepcopy(self.descriptor)
        descriptor.update(selected_indices=[],sampling_probabilities=[])
        row=bind(dict(point='m2_prompt',events=10),self.parent,descriptor)
        self.assertEqual(population(row,self.parent),([],[]))
        self.assertEqual(row['full_gen_events'],10)
        row['final_output_base']='/eos/user/t/test/darkphoton_m2_prompt/20261023-test'
        report=rate_only_report(row,self.parent,'c'*64,'d'*64)
        validate_rate_only_report(report,row,self.parent,'c'*64,'d'*64)
        self.assertEqual(report['union']['parent_gen'],self.parent)
        self.assertNotIn('score_histograms',report)
        report['full_gen_events']=0
        with self.assertRaisesRegex(ValueError,'full GEN exposure'):
            validate_rate_only_report(report,row,self.parent,'c'*64,'d'*64)

    def test_descriptor_file_is_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'material_selected_source.json'
            path.write_text(json.dumps(self.descriptor))
            row=dict(self.row,gen_directory=directory,
                material_selected_source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            validate_plan(dict(points=[row]),dict(points={'m2_prompt':self.parent}))
            path.write_text(path.read_text()+' ')
            with self.assertRaisesRegex(ValueError,'checksum'):
                validate_plan(dict(points=[row]),dict(points={'m2_prompt':self.parent}))

    def test_real_extended_worker_stages_the_pinned_descriptor(self):
        from prepare_dark_photon_production import extend_worker
        template=Path(__file__).resolve().parents[2]/'validation/bsm_stage2_20261013/production/chunk_worker.py'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);gen=root/'gen';gen.mkdir()
            for name in ('manifest.json','contract.json','validation.json'):
                (gen/name).write_text('{}')
            descriptor=gen/'material_selected_source.json'
            descriptor.write_text(json.dumps(self.descriptor))
            worker_path=root/'chunk_worker.py'
            worker_path.write_text(extend_worker(template.read_text(),'nano-metadata-v1'))
            spec=importlib.util.spec_from_file_location('material_staging_worker',worker_path)
            worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
            remote='/eos/user/t/test/darkphoton_m2_prompt/20261023-test/GEN/gen.root'
            row=dict(self.row,gen_directory=str(gen),gen_remote_path=remote,
                final_output_base=remote[:-len('/GEN/gen.root')],
                material_selected_source_sha256=hashlib.sha256(descriptor.read_bytes()).hexdigest())
            parent=dict(self.parent,gen_path=remote,gen_transport='xrootd',
                        gen_sha256=hashlib.sha256(b'GEN').hexdigest())
            with patch.object(worker,'xrd',side_effect=lambda argv:Path(argv[-1]).write_bytes(b'GEN')):
                staged=worker.stage_gen_input(row,parent,root/'staged')
            self.assertEqual((staged/descriptor.name).read_bytes(),descriptor.read_bytes())
            descriptor.write_text(descriptor.read_text()+' ')
            with self.assertRaisesRegex(ValueError,'descriptor changed'):
                worker.stage_gen_input(row,parent,root/'changed')

    def test_native_identity_and_double_sampling_fields_survive(self):
        import numpy as np
        import uproot
        ids,probabilities=population(self.row,self.parent)
        arrays=dict(run=np.array([100]*3,dtype=np.uint32),
            luminosityBlock=np.array([1]*3,dtype=np.uint32),event=np.array([2,6,9],dtype=np.uint64),
            genWeight=np.ones(3,dtype=np.float32), shiftSamplingProbability=np.array(probabilities),
            shiftSamplingWeight=np.array([10.,4.,1.]), shiftSamplingGenWeight=np.array([10.,4.,1.]),
            shiftOriginalGenWeight=np.ones(3,dtype=np.float64))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'nano.root'
            with uproot.recreate(path) as output:
                output.mktree('Events',{name:value.dtype for name,value in arrays.items()})
                output['Events'].extend(arrays)
            self.assertEqual(validate_nano(path,ids,probabilities)['sumw'],15.)
            arrays['shiftSamplingGenWeight'][1]=5.
            with uproot.recreate(path) as output:
                output.mktree('Events',{name:value.dtype for name,value in arrays.items()})
                output['Events'].extend(arrays)
            with self.assertRaisesRegex(ValueError,'sampling field'):
                validate_nano(path,ids,probabilities)


if __name__ == '__main__':
    unittest.main()
