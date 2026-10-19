# Code style: AI-code. AI-generated GEN lifetime and identity regression tests.
import math
import copy
import hashlib
import gzip
import json
import random
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_dark_photon_gen import validate_lifetime_sample, validate_event_ids, proper_length_from_native_time, generation_accounting, meson_accepted_slots, validate_meson_radiative_record


class MesonRadiativeGraphTest(unittest.TestCase):
    def setUp(self):
        self.selected = dict(slot=1,trial=12,
            parent=dict(index=38,pdg_id=221,native_mass_gev=.548,p4=[.548,0.,0.,0.]),
            graph=dict(barcode=38,pdg_id=221,native_mass_gev=.548,
                production_position_mm=[0.,0.,0.,0.],decay_position_mm=[0.,0.,0.,.001]),
            sampled_decay=dict(dark_photon_p4=[.35,0.,0.,.198],photon_p4=[.198,0.,0.,-.198]))
        self.record = dict(mother_barcode=38,mother_pdg_id=221,mother_generated_mass_gev=.548,
            mother_p4=[.548,0.,0.,0.],mother_production_position_mm=[0.,0.,0.,0.],
            decay_position_mm=[0.,0.,0.,.001],outgoing=[
                dict(pdg_id=32,status=3,p4=[.35,0.,0.,.198]),
                dict(pdg_id=22,status=1,p4=[.198,0.,0.,-.198])])

    def test_native_parent_recoil_and_spacetime_match_accepted_slot(self):
        result = validate_meson_radiative_record(self.record,self.selected)
        self.assertEqual(result['source_trial'],12)
        self.assertEqual(result['selected_parent_barcode'],38)
        self.assertLess(result['maximum_momentum_residual_gev'],1.e-15)
        mutations = [('mother_barcode',39),('mother_pdg_id',331),
                     ('mother_generated_mass_gev',.547),('mother_p4',[.55,0.,0.,0.]),
                     ('decay_position_mm',[0.,0.,0.,0.])]
        for key,value in mutations:
            changed = dict(self.record,**{key:value})
            with self.subTest(key=key),self.assertRaises(ValueError):
                validate_meson_radiative_record(changed,self.selected)
        changed=copy.deepcopy(self.record)
        changed['outgoing'][1]['p4'][0] += .001
        with self.assertRaises(ValueError):
            validate_meson_radiative_record(changed,self.selected)
        changed=copy.deepcopy(self.record)
        changed['outgoing'].append(dict(pdg_id=22,status=1,p4=[0.,0.,0.,0.]))
        with self.assertRaises(ValueError):
            validate_meson_radiative_record(changed,self.selected)

    def test_full_source_stream_caches_only_selected_slots_and_pins_hash(self):
        row=dict(accepted=True,trial=12,accepted_slot=1,parent_index=38,
            parents=[self.selected['parent']],selected_parent_graph=self.selected['graph'],
            sampled_decay=self.selected['sampled_decay'])
        payload=''.join(json.dumps(value)+'\n' for value in [dict(accepted=False),row])
        contract=dict(requested_events=1,source_ledger_sha256=hashlib.sha256(payload.encode()).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.jsonl'
            path.write_text(payload)
            self.assertEqual(meson_accepted_slots(path,contract),{1:self.selected})
            compressed=Path(directory)/'meson_source_ledger.jsonl.gz'
            with gzip.open(compressed,'wt') as output:
                output.write(payload)
            self.assertEqual(meson_accepted_slots(compressed,contract),{1:self.selected})
            path.write_text(payload+json.dumps(dict(accepted=False))+'\n')
            with self.assertRaises(ValueError):
                meson_accepted_slots(path,contract)

    def test_vector_recoil_mass_and_spin_are_pinned_to_the_model(self):
        from dark_photon_vector_meson import sample_decay
        sampled = sample_decay((.78266,0.,0.,0.),.1349768,.3,10.,random.Random(5),
                               polarization='unpolarized')
        sampled = {key: list(value) if isinstance(value,tuple) else value for key,value in sampled.items()}
        sampled['recoil_pdg_id'] = 111
        selected = copy.deepcopy(self.selected)
        selected['parent'].update(pdg_id=223,native_mass_gev=.78266,p4=[.78266,0.,0.,0.])
        selected['graph'].update(pdg_id=223,native_mass_gev=.78266)
        selected['sampled_decay'] = sampled
        selected['resolved_model'] = dict(decay_type='vector-pseudoscalar',recoil_pdg_id=111,
            recoil_mass_gev=.1349768,parent_polarization='unpolarized')
        record = copy.deepcopy(self.record)
        record.update(mother_pdg_id=223,mother_generated_mass_gev=.78266,mother_p4=[.78266,0.,0.,0.],
            outgoing=[dict(pdg_id=32,status=3,p4=sampled['dark_photon_p4']),
                      dict(pdg_id=111,status=2,p4=sampled['recoil_p4'])])
        result = validate_meson_radiative_record(record,selected)
        self.assertEqual(result['parent_pdg_id'],223)
        for key,value in [('recoil_mass_gev',.14),('parent_polarization','unknown'),
                          ('recoil_natural_decay_required',False)]:
            changed = copy.deepcopy(selected)
            changed['sampled_decay'][key] = value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'recoil/spin'):
                validate_meson_radiative_record(record,changed)
        record['outgoing'][1]['status'] = 1
        with self.assertRaisesRegex(ValueError,'status'):
            validate_meson_radiative_record(record,selected)


class SignalAndSourceAccounting(unittest.TestCase):
    def test_native_dy_keeps_its_original_output_denominator(self):
        contract = dict(requested_events=100, width_authority=dict(ctau_mm=5.))
        with patch('audit_dark_photon_gen.read_pythia_generation_ledger', return_value={'n_accepted': 100}) as parser:
            result = generation_accounting('/frozen/native', contract)
        parser.assert_called_once_with(Path('/frozen/native/cmsRun.log'), expected_accepted=100)
        self.assertNotIn('source_exposure', result)
        self.assertEqual(result['physical_width'], contract['width_authority'])

    def test_meson_retains_more_source_collisions_than_conditional_signals(self):
        exposure = dict(source_collisions=103, native_n_tried=110, native_n_accepted=103)
        contract = dict(production_mechanism='eta-etaprime-radiative', requested_events=4,
                        physical_width_table=dict(ctau_mm=50.))
        rate = dict(value_pb=1.e-5, error_pb=2.e-6)
        ledger = dict(n_accepted=103, n_tried=110)
        with patch('export_dark_photon_cross_sections.meson_source_exposure', return_value=(exposure, rate, {})), \
             patch('audit_dark_photon_gen.read_pythia_generation_ledger', return_value=ledger) as parser:
            result = generation_accounting('/frozen/mesons', contract)
        parser.assert_called_once_with(Path('/frozen/mesons/cmsRun.log'), expected_accepted=103)
        self.assertEqual(result['proposal_ledger']['n_accepted'], 103)
        self.assertEqual(result['target_cross_sections'], [rate])
        self.assertEqual(result['physical_width'], contract['physical_width_table'])

    def test_changed_native_source_counters_are_rejected(self):
        exposure = dict(source_collisions=103, native_n_tried=110, native_n_accepted=103)
        contract = dict(production_mechanism='eta-etaprime-radiative')
        with patch('export_dark_photon_cross_sections.meson_source_exposure', return_value=(exposure, {}, {})), \
             patch('audit_dark_photon_gen.read_pythia_generation_ledger', return_value=dict(n_accepted=103, n_tried=111)):
            with self.assertRaisesRegex(ValueError, 'source counters'):
                generation_accounting('/frozen/mesons', contract)


class AcceptedEventIdentityTest(unittest.TestCase):
    def setUp(self):
        self.legacy=dict(run_number=101,requested_events=3)
        self.accepted=dict(self.legacy,event_budget_mode='outputAccepted',maximum_framework_events=30)

    def test_new_mode_retains_generator_rejection_gaps(self):
        ids=[[101,1,2],[101,1,4],[101,1,30]]
        validate_event_ids(ids,self.accepted)
        with self.assertRaises(ValueError):
            validate_event_ids(ids,self.legacy)

    def test_duplicate_reversed_or_invalid_identity_fails(self):
        for ids in ([[101,1,2],[101,1,2],[101,1,3]],
                    [[101,1,2],[101,1,4],[101,1,3]],
                    [[101,1,0],[101,1,2],[101,1,3]],
                    [[102,1,1],[101,1,2],[101,1,3]],
                    [[101,2,1],[101,1,2],[101,1,3]],
                    [[101,1,1],[101,1,2],[101,1,31]],
                    [[101,1,True],[101,1,2],[101,1,3]]):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                validate_event_ids(ids,self.accepted)

    def test_exact_accepted_count_and_input_cap_are_required(self):
        with self.assertRaises(ValueError):
            validate_event_ids([[101,1,1],[101,1,2]],self.accepted)
        with self.assertRaises(ValueError):
            validate_event_ids([[101,1,1],[101,1,2],[101,1,3]],
                               dict(self.accepted,maximum_framework_events=31))
        with self.assertRaises(ValueError):
            validate_event_ids([[101,1,1],[101,1,2],[101,1,3]],
                               dict(self.accepted,event_budget_mode='unknown'))

    def test_legacy_exact_ids_still_pass(self):
        validate_event_ids([[101,1,1],[101,1,2],[101,1,3]],self.legacy)


class FiniteLifetimeAuditTest(unittest.TestCase):
    def test_prompt_lifetime_survives_sub_ulp_source_translation(self):
        native_delta_ct=1e-12
        shifted_start=-148000.
        shifted_end=shifted_start+native_delta_ct
        self.assertEqual(shifted_end-shifted_start,0.)
        length=proper_length_from_native_time(native_delta_ct,90.,900.)
        self.assertAlmostEqual(length,1e-13,places=25)
        self.assertTrue(validate_lifetime_sample([length]*100,length)['passed'])

    def test_native_lifetime_guard_remains_strict(self):
        for delta,mass,energy in [(0.,90.,900.),(-1e-12,90.,900.),(math.nan,90.,900.),
                                  (1.,0.,900.),(1.,90.,0.),(1.,90.,math.inf)]:
            with self.subTest(delta=delta,mass=mass,energy=energy),self.assertRaises(ValueError):
                proper_length_from_native_time(delta,mass,energy)

    def test_small_pilot_rejects_zero_and_proposal_scale_lifetimes(self):
        # Both cases passed the previous |mean_pull|<8 condition at N=20.
        for values in ([0.]*20,[1e-10]*20):
            with self.assertRaises(ValueError):
                validate_lifetime_sample(values,1.)

    def test_finite_n_gate_accepts_plausible_skewed_sample(self):
        values=[-math.log((i+.5)/20) for i in range(20)]
        result=validate_lifetime_sample(values,1.)
        self.assertTrue(result['passed'])
        self.assertLess(abs(result['mean_ratio']-1),.02)

    def test_rejects_wrong_long_lifetime(self):
        with self.assertRaises(ValueError):
            validate_lifetime_sample([10.]*20,1.)

    def test_invalid_draws_means_and_bounds(self):
        for values,mean,bound in (([],1.,1e-12),([-1.],1.,1e-12),([math.nan],1.,1e-12),
                                   ([math.inf],1.,1e-12),([1.],0.,1e-12),([1.],math.nan,1e-12),
                                   ([1.],1.,0.),([1.],1.,1.)):
            with self.assertRaises(ValueError):
                validate_lifetime_sample(values,mean,bound)

    def test_scale_invariance_and_exact_mean(self):
        for scale in (1e-9,1.,1e100):
            result=validate_lifetime_sample([scale]*20,scale)
            self.assertEqual(result['mean_ratio'],1.)
            self.assertEqual(result['log_tail_probability_upper_bound'],0.)


if __name__=='__main__':
    unittest.main()
