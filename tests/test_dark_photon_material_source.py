# Code style: AI-code. AI-generated immutable DP admission/exposure tests.
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_dark_photon_material_source as preparation
from shift_production_selection import prepare_default_selection, bind_source
from test_shift_material_preselection import event


class MaterialSourceTests(unittest.TestCase):
    def fixture(self):
        rows=[event(index) for index in range(1,101)]
        parent=dict(gen_sha256='b'*64,events=100,
                    event_ids=[row['event_id'] for row in rows],
                    full_gen_denominator=dict(sumw=100.,source_collisions=10000))
        contract=dict(requested_events=100,
            source_exposure=dict(fixed_source_collisions=10000,accepted_signal_events=100),
            source_placement=dict(schema='shift-dp-assumed-jaw-source-v1'))
        return rows,parent,contract

    def test_complete_census_is_drawn_once_without_changing_exposure(self):
        rows,parent,contract=self.fixture()
        original=copy.deepcopy((parent,contract))
        inputs=preparation.selection_inputs(rows,parent,contract,'c'*64,'cohort','seed')
        ledger=prepare_default_selection(inputs)
        self.assertEqual(ledger,prepare_default_selection(inputs))
        descriptor=bind_source(dict(gen_sha256=parent['gen_sha256'],
            event_ids=parent['event_ids'],weights=[1.]*100),ledger)
        self.assertEqual((parent,contract),original)
        self.assertEqual(descriptor['event_ids'],parent['event_ids'])
        self.assertEqual(descriptor['weights'],[1.]*100)
        self.assertGreater(len(descriptor['selected_indices']),0)
        self.assertLess(len(descriptor['selected_indices']),100)
        exposure=inputs['authority']['sources'][0]['full_gen_exposure']
        self.assertEqual(exposure['source_exposure']['fixed_source_collisions'],10000)
        self.assertEqual(exposure['conditional_signal_GEN_events'],100)
        self.assertEqual(exposure['parent_GEN_inventory'],parent)
        for row in ledger['events']:
            self.assertGreaterEqual(row['sampling_probability'],.1)
            self.assertAlmostEqual(row['sampling_gen_weight']*row['sampling_probability'],1.)

    def test_direct_dy_keeps_accepted_denominator_and_rate_correction(self):
        rows,parent,contract=self.fixture()
        contract.pop('source_exposure')
        contract.update(mechanism='pure-on-shell-drell-yan',
            production_rate_correction=1.e-10,cross_section_convention='native BR once',
            event_budget_mode='outputAccepted')
        parent['full_gen_denominator']=dict(accepted_events=100,framework_attempt_cap=1000)
        parent['proposal_ledger']=dict(n_tried=789,n_accepted=100)
        inputs=preparation.selection_inputs(rows,parent,contract,'c'*64,'dy','seed')
        exposure=inputs['authority']['sources'][0]['full_gen_exposure']['source_exposure']
        self.assertEqual(exposure['full_gen_denominator'],parent['full_gen_denominator'])
        self.assertEqual(exposure['proposal_ledger'],parent['proposal_ledger'])
        self.assertEqual(exposure['production_rate_correction'],1.e-10)
        self.assertNotIn('fixed_source_collisions',exposure)
        self.assertEqual(prepare_default_selection(inputs)['policy']['control_probability'],.1)

    def test_missing_placement_partial_census_and_changed_weights_fail(self):
        for defect in ('placement','prefix','order','native'):
            rows,parent,contract=self.fixture()
            if defect=='placement': contract.pop('source_placement')
            if defect=='prefix': rows.pop()
            if defect=='order': rows.reverse()
            if defect=='native': rows[0]['native_weight']=2.
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                preparation.selection_inputs(rows,parent,contract,'c'*64,'cohort','seed')

    def test_frozen_evidence_cannot_be_replaced_by_another_draw(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'evidence.json'
            preparation.immutable(path,dict(seed='first'))
            preparation.immutable(path,dict(seed='first'))
            with self.assertRaisesRegex(ValueError,'changed'):
                preparation.immutable(path,dict(seed='replacement'))
            self.assertEqual(json.loads(path.read_text()),dict(seed='first'))


if __name__=='__main__':
    unittest.main()
