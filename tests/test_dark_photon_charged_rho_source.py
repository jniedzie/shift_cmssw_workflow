# Code style: AI-code. AI-generated charged-rho source and audit integration tests.
import copy
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_dark_photon_gen import validate_meson_radiative_record
import dark_photon_charged_rho_radiative as rho
from dark_photon_meson_source import (choose_collision, coefficient_bound,
    charged_rho_parent_rate_model,
    dipion_kernel, exclusive_channels, parent_branching_coefficient,
    recoil_threshold, resolved_parent_models, resolve_parent_kinematics,
    source_cross_section_pb)
from run_dark_photon_meson_gen import SOURCES
from export_dark_photon_cross_sections import meson_source_exposure


def charged_parent(pdg):
    inputs = dict(kind='gauged-proca-pion-g2-pole', alpha=1/137.035999084,
        charged_pion_mass_gev=.13957039, neutral_pion_mass_gev=.1349768,
        reference_parent_mass_gev=.77511, valid_mass_range_gev=[.211316751,.50056281],
        parent_polarization='unpolarized', parent_mass_continuation='zero-width-pole',
        pole_mass_numerical_tolerance_gev=1.e-6, electromagnetic_gyromagnetic_ratio=2,
        normalization=dict(channel='charged-pion-neutral-pion', branching_fraction=1.,
                           reference='synthetic normalization fixture'),
        reference='synthetic benchmark fixture', assumption='local g=2 benchmark',
        signal_rate_validated=False)
    return dict(pdg_id=pdg, channel=f'{pdg}->charged-pion_pi0_Aprime', mass_gev=.77511,
        pdg_reference='synthetic fixture', decay_type='charged-vector-dipion',
        charged_pion_mass_gev=.13957039, neutral_pion_mass_gev=.1349768,
        br_pipi=1., recoil_pdg_ids=[211 if pdg>0 else -211,111],
        parent_polarization='unpolarized', parent_mass_model='zero-width-pole',
        charged_rho_radiative_model=inputs)


def resolve(parents, mass=.3, width=0.):
    card = dict(mechanism='meson-radiative', parents=parents,
                channels=[row['channel'] for row in parents])
    native = {row['pdg_id']: dict(pdg_id=row['pdg_id'], mass_min_gev=row['mass_gev'],
        mass_max_gev=row['mass_gev'], mass_width_gev=width) for row in parents}
    return resolved_parent_models(card, dict(parents=list(native.values())), mass)


class ChargedRhoSourceTest(unittest.TestCase):
    def endpoint_fixture(self):
        maintained = json.loads((Path(__file__).resolve().parents[1]/
            'config/campaigns/dark_photon_meson_extended_model.json').read_text())
        companion = next(row for row in maintained['parents'] if row['pdg_id']==213)
        parents = [companion,charged_parent(213)]
        card = dict(schema='shift-dark-photon-meson-model-v1',mechanism='meson-radiative',
                    parents=parents,channels=[row['channel'] for row in parents])
        configuration = dict(check_event=True,mass_tolerance=.001,energy_tolerance=.01,
            ecm_gev=2*.77511,parents=[dict(pdg_id=213,mass_min_gev=.77511,
                                       mass_max_gev=.77511,mass_width_gev=0.)])
        models = resolved_parent_models(card,configuration,.5)
        native = dict(index=52,pdg_id=213,native_mass_gev=.77511,
            p4=[455.44498130766743,-.34545302901765806,.09521061883127688,-455.4441807650014])
        return card,configuration,models,native

    def test_observed_native_endpoint_uses_audited_same_parent_bound(self):
        card,configuration,models,native = self.endpoint_fixture()
        parent, = resolve_parent_kinematics([native],.001,.5,models,.4)
        self.assertEqual(parent['p4'],native['p4'])
        self.assertEqual(parent['mass_gev'],native['native_mass_gev'])
        self.assertEqual(parent['kinematic_mass_gev'],math.sqrt(math.fsum(
            (native['p4'][0]**2,*(-x*x for x in native['p4'][1:])))))
        report = parent['native_parent_precision_report']
        self.assertGreater(report['channel_relative_rate_shift'],.001)
        self.assertLess(report['parent_total_relative_bound'],.001)
        self.assertAlmostEqual(report['channel_nominal_parent_fraction'],2.7725762200625056e-6,delta=1.e-16)
        self.assertEqual(report['conditional_distribution_fraction_bound'],report['channel_nominal_parent_fraction'])
        model = next(x for x in exclusive_channels(models[213])
                     if x['decay_type']=='charged-vector-dipion')
        isolated = {213:model}
        with self.assertRaisesRegex(ValueError,'0.1%'):
            resolve_parent_kinematics([native],.001,.5,isolated,.4)
        nominal = parent_branching_coefficient(.77511,.5,models[213],.4)
        bound = coefficient_bound(configuration['ecm_gev'],.4,models,.5)
        decision = choose_collision([parent],.5,.4,models,bound,random.Random(1))
        self.assertEqual(decision['coefficient_sum'],nominal)
        self.assertNotIn('native_parent_precision_report',resolve_parent_kinematics(
            [dict(native,p4=[.77511,0.,0.,0.])],.001,.5,models,.4)[0])
        context = dict(native_mass_gev=.77511,native_mass_tolerance=.001,
            native_parent_rate_model=charged_rho_parent_rate_model(213,models[213]))
        self.assertTrue(all(row['pdg_id']==213
            for row in context['native_parent_rate_model']['exclusive_channels']))
        self.assertTrue(all('pdg_id' not in row for row in exclusive_channels(models[213])))
        self.assertGreater(rho.mean_lab_beta_gamma(parent['p4'],.5,model,**context),0.)

    def test_exporter_recomputes_endpoint_report_from_pinned_channels(self):
        card,configuration,models,native = self.endpoint_fixture()
        parent, = resolve_parent_kinematics([native],.001,.5,models,.4)
        coefficient = parent_branching_coefficient(.77511,.5,models[213],.4)
        probability = 1.e-6*coefficient
        bound = coefficient_bound(configuration['ecm_gev'],.4,models,.5)
        rows = [dict(schema='shift-meson-source-trial-v1',trial=1,generator_success=True,
            native_weight=1.,parents=[parent],coefficient_sum=coefficient,
            physical_probability=probability,acceptance_probability=coefficient/bound,
            accepted=True,parent_index=52,accepted_slot=1,selected_channel=card['channels'][0]),
            dict(schema='shift-meson-source-trial-v1',trial=2,generator_success=True,
            native_weight=1.,parents=[],coefficient_sum=0.,physical_probability=0.,
            acceptance_probability=0.,accepted=False,parent_index=None,accepted_slot=None)]
        difference = parent['coefficient_difference_from_kinematic_mass']
        exposure = dict(schema='shift-meson-source-exposure-v1',fixed_source_collisions=2,
            source_calls=2,source_collisions=2,generator_failures=0,zero_eligible_parent_collisions=1,
            sum_native_weights=2.,sum_native_weights_squared=2.,sigma_source_mb=30.,sigma_source_error_mb=.3,
            coefficient_sum=coefficient,coefficient_sum_squared=coefficient**2,
            physical_probability_sum=probability,physical_probability_sum_squared=probability**2,
            accepted_signal_events=1,analytic_coefficient_bound=bound,coefficient_includes_br_mumu=True,
            coefficient_excludes_epsilon_squared=True,unit_output_gen_weights=True,
            coefficient_precision_difference_sum=difference,
            maximum_coefficient_relative_precision_shift=abs(parent['coefficient_relative_precision_shift']),
            relative_rate_precision_shift=difference/coefficient)
        authority=dict(provider='synthetic pinned width source')
        width=dict(mass_gev=.5,epsilon=.001,br_mumu=.4,ctau_mm=1.973269804e-3,
            total_width_gev=1.e-10,partial_widths_gev=dict(e_e=6.e-11,mu_mu=4.e-11),spectral_source=authority)
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory)
            reference=directory/'ErrorChecks.xml';reference.write_text('Synthetic Check:mTolErr fixture\n')
            precision=dict(configuration,reference=str(reference),reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
                prescription='native generated mass for branching and bound; unchanged p4 invariant for decay')
            contract=dict(production_mechanism='meson-radiative',exclusive_channels=card['channels'],
                epsilon=.001,mass_gev=.5,physical_width_table=width,width_authority=authority,
                source_precision=precision,resolved_parent_models={str(k):v for k,v in models.items()},requested_events=1)
            def freeze():
                ledger=''.join(json.dumps(row)+'\n' for row in rows).encode()
                (directory/'meson_source_ledger.jsonl').write_bytes(ledger)
                digest=hashlib.sha256(ledger).hexdigest()
                contract['source_ledger_sha256']=exposure['source_ledger_sha256']=digest
                contract['source_exposure']=copy.deepcopy(exposure)
                contract['meson_model_card']=copy.deepcopy(card)
                for filename,value,key in [('source_exposure.json',exposure,'source_exposure_sha256'),
                                           ('meson_model.json',card,'meson_model_card_sha256')]:
                    payload=json.dumps(value).encode();(directory/filename).write_bytes(payload)
                    contract[key]=hashlib.sha256(payload).hexdigest()
            freeze()
            audited,rate,_=meson_source_exposure(contract,directory)
            self.assertEqual(audited['source_collisions'],2)
            self.assertAlmostEqual(rate['value_pb'],30.e9*probability/2)
            original=copy.deepcopy(parent['native_parent_precision_report'])
            for replacement in (None,dict(original,parent_total_relative_bound=0.),
                                dict(original,context_sha256='0'*64)):
                if replacement is None:
                    parent.pop('native_parent_precision_report',None)
                else:
                    parent['native_parent_precision_report']=replacement
                freeze()
                with self.assertRaisesRegex(ValueError,'precision report'):
                    meson_source_exposure(contract,directory)
            parent['native_parent_precision_report']=original

    def test_source_resolves_charge_conjugates_unequal_threshold_and_endpoints(self):
        for mass in (.22,.25,.3,.4,.5,.7,.9):
            models = resolve([charged_parent(213),charged_parent(-213)],mass)
            values = [parent_branching_coefficient(.77511,mass,models[pdg],.2)
                      for pdg in (213,-213)]
            self.assertEqual(values[0],values[1])
            self.assertEqual(values[0]>0,mass<.50056281)
            self.assertEqual(dipion_kernel(models[213]),rho)
            self.assertEqual(recoil_threshold(models[213]),.13957039+.1349768)

    def test_shared_physical_parent_selects_both_exclusive_channels_once(self):
        dipion = resolve([charged_parent(213)])[213]
        photon = dict(mass_min_gev=.77511,mass_max_gev=.77511,br_p_gamma=.0004,
            form_factor_abs2=1.,decay_type='vector-pseudoscalar',recoil_pdg_id=211,
            recoil_mass_gev=.13957039,parent_polarization='unpolarized',
            parent_mass_model='zero-width-pole',provenance='synthetic fixture',
            channel='213->charged-pion_Aprime')
        models = {213:dict(mass_min_gev=.77511,mass_max_gev=.77511,
                           exclusive_channels=[photon,dipion])}
        parent = dict(index=7,pdg_id=213,mass_gev=.77511)
        values = [parent_branching_coefficient(.77511,.3,row,.2)
                  for row in exclusive_channels(models[213])]
        bound = coefficient_bound(2*.77511,.2,models,.3)
        self.assertAlmostEqual(bound,2*sum(values))
        for fraction,name in ((0.,photon['channel']),(.999999,dipion['channel'])):
            rng = Mock()
            rng.random.side_effect = [0.,fraction]
            row = choose_collision([parent],.3,.2,models,bound,rng)
            self.assertEqual(row['parent_count'],1)
            self.assertEqual(row['parent_index'],7)
            self.assertEqual(row['selected_channel'],name)
            self.assertAlmostEqual(row['coefficient_sum'],sum(values))
        empty = choose_collision([],.3,.2,models,bound,random.Random(1))
        self.assertEqual(empty['parent_count'],0)
        self.assertEqual(empty['coefficient_sum'],0.)
        # Two source collisions, one empty, retain the full source denominator.
        self.assertEqual(source_cross_section_pb(1.,.001,sum(values),2),500*sum(values))
        with self.assertRaisesRegex(ValueError,'repeated'):
            choose_collision([parent,parent],.3,.2,models,bound,random.Random(1))

    def test_invalid_charge_recoil_masses_spin_or_native_width_fail_closed(self):
        mutations = [dict(recoil_pdg_ids=[-211,111]),
            dict(charged_pion_mass_gev=.14),dict(neutral_pion_mass_gev=.14),
            dict(parent_polarization='unknown'),dict(parent_mass_model='broad-rho')]
        for changes in mutations:
            parent = charged_parent(213)
            parent.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                resolve([parent])
        with self.assertRaisesRegex(ValueError,'zero-width'):
            resolve([charged_parent(213)],width=.1491)
        self.assertIn('dark_photon_charged_rho_radiative.py',SOURCES)

    def test_explicit_native_context_preserves_momenta_and_pole_probability(self):
        models = resolve([charged_parent(213)])
        parent = dict(index=12,pdg_id=213,native_mass_gev=.77511,
                      p4=[.7751102,0.,0.,0.])
        resolved, = resolve_parent_kinematics([parent],1.e-3,.3,models,.2)
        self.assertEqual(resolved['p4'],parent['p4'])
        self.assertEqual(resolved['mass_gev'],parent['native_mass_gev'])
        self.assertGreater(resolved['kinematic_mass_gev'],resolved['mass_gev'])
        self.assertGreater(resolved['coefficient_relative_precision_shift'],0.)
        self.assertLess(resolved['coefficient_relative_precision_shift'],.001)
        self.assertEqual(parent_branching_coefficient(resolved['mass_gev'],.3,models[213],.2),
                         parent_branching_coefficient(.77511,.3,models[213],.2))
        with self.assertRaisesRegex(ValueError,'Pythia mass tolerance'):
            resolve_parent_kinematics([parent],1.e-10,.3,models,.2)


class ChargedRhoGraphAuditTest(unittest.TestCase):
    def fixture(self,pdg):
        model = resolve([charged_parent(pdg)])[pdg]
        sample = rho.sample_decay((.77511,0.,0.,0.),.3,10.,random.Random(34),
                                  model=model,charge=1 if pdg>0 else -1)
        sample = {key:list(value) if isinstance(value,tuple) else value
                  for key,value in sample.items()}
        sample['recoil_p4s'] = [list(value) for value in sample['recoil_p4s']]
        selected = dict(slot=1,trial=31,parent=dict(index=12,pdg_id=pdg,
            native_mass_gev=.77511,p4=[.77511,0.,0.,0.]),
            graph=dict(barcode=12,pdg_id=pdg,native_mass_gev=.77511,
                production_position_mm=[0.,0.,0.,0.],decay_position_mm=[0.,0.,0.,.001]),
            sampled_decay=sample,resolved_model=model)
        record = dict(mother_barcode=12,mother_pdg_id=pdg,mother_generated_mass_gev=.77511,
            mother_p4=[.77511,0.,0.,0.],mother_production_position_mm=[0.,0.,0.,0.],
            decay_position_mm=[0.,0.,0.,.001],outgoing=[
                dict(pdg_id=32,status=3,p4=sample['dark_photon_p4'])]+[
                dict(pdg_id=pid,status=2,p4=p4)
                for pid,p4 in zip(sample['recoil_pdg_ids'],sample['recoil_p4s'])])
        return record,selected

    def test_both_charges_preserve_distinct_pion_masses_and_natural_recoil_graph(self):
        for pdg in (213,-213):
            record,selected = self.fixture(pdg)
            result = validate_meson_radiative_record(record,selected)
            self.assertEqual(result['parent_pdg_id'],pdg)
            self.assertLess(result['maximum_momentum_residual_gev'],1.e-12)
            for key,value in [('recoil_pdg_ids',[-211 if pdg>0 else 211,111]),
                              ('recoil_masses_gev',[.13957039,.13957039]),
                              ('parent_polarization','unknown'),
                              ('recoil_natural_decay_required',False)]:
                changed = copy.deepcopy(selected)
                changed['sampled_decay'][key] = value
                with self.subTest(pdg=pdg,key=key),self.assertRaises(ValueError):
                    validate_meson_radiative_record(record,changed)
            changed = copy.deepcopy(record)
            changed['outgoing'][-1]['status'] = 1
            with self.assertRaisesRegex(ValueError,'status'):
                validate_meson_radiative_record(changed,selected)
            changed = copy.deepcopy(record)
            changed['outgoing'].append(dict(pdg_id=22,status=1,p4=[0.,0.,0.,0.]))
            with self.assertRaisesRegex(ValueError,'exactly'):
                validate_meson_radiative_record(changed,selected)

    def test_charged_decay_cannot_be_attached_to_neutral_rho(self):
        record,selected = self.fixture(213)
        record['mother_pdg_id'] = selected['parent']['pdg_id'] = selected['graph']['pdg_id'] = 113
        with self.assertRaisesRegex(ValueError,'physical parent'):
            validate_meson_radiative_record(record,selected)


if __name__ == '__main__':
    unittest.main()
