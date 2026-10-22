# Code style: AI-code. AI-generated strict full-exposure native-precision gates.
import copy
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from test_dark_photon_charged_rho_source import charged_parent
from dark_photon_meson_source import (FULL_SOURCE_NATIVE_PRECISION_POLICY,
    coefficient_bound,deferred_native_precision_measure,full_source_native_precision_summary,
    parent_branching_coefficient,resolved_parent_models,resolve_parent_kinematics,
    validate_native_precision_policy)
from export_dark_photon_cross_sections import meson_source_exposure,same_native_parent_precision_report

POLICY=FULL_SOURCE_NATIVE_PRECISION_POLICY
MASS=.22;BR=.4


def fixture():
    maintained=json.loads((ROOT/'config/campaigns/dark_photon_meson_extended_model.json').read_text())
    companion=next(r for r in maintained['parents'] if r['pdg_id']==213)
    card=dict(schema='shift-dark-photon-meson-model-v1',mechanism='meson-radiative',
        parents=[companion,charged_parent(213)],native_precision_policy=dict(POLICY))
    card['channels']=[r['channel'] for r in card['parents']]
    config=dict(check_event=True,mass_tolerance=.001,energy_tolerance=.01,ecm_gev=2*.77511,
        parents=[dict(pdg_id=213,mass_min_gev=.77511,mass_max_gev=.77511,mass_width_gev=0.)])
    models=resolved_parent_models(card,config,MASS)
    bad=dict(index=44,pdg_id=213,native_mass_gev=.77511,
        p4=[460.427513549071,.038970672533656964,.8268151790297367,-460.4261162975049])
    good=dict(index=44,pdg_id=213,native_mass_gev=.77511,p4=[.77511,0.,0.,0.])
    return card,config,models,bad,good


class FullSourcePrecisionTest(unittest.TestCase):
    def test_actual_failure_requires_explicit_policy_and_keeps_nominal_probability(self):
        card,config,models,bad,good=fixture()
        with self.assertRaisesRegex(ValueError,'0.1%'):
            resolve_parent_kinematics([bad],.001,MASS,models,BR)
        resolved,=resolve_parent_kinematics([bad],.001,MASS,models,BR,native_precision_policy=POLICY)
        self.assertEqual(resolved['p4'],bad['p4'])
        self.assertEqual(resolved['mass_gev'],bad['native_mass_gev'])
        report=resolved['native_parent_precision_report']
        self.assertEqual(report['scope'],'deferred-full-source')
        self.assertTrue(report['deferred_full_source_check'])
        self.assertGreater(report['parent_l1_relative_rate_shift'],.001)
        value,count=deferred_native_precision_measure([resolved],BR,POLICY)
        self.assertEqual(count,1)
        self.assertEqual(value,report['conservative_absolute_parent_measure_bound']*BR)
        nominal=parent_branching_coefficient(.77511,MASS,models[213],BR)
        self.assertFalse(full_source_native_precision_summary(POLICY,nominal,value,count)['complete'])
        self.assertTrue(full_source_native_precision_summary(POLICY,3001*nominal,value,count)['complete'])
        with self.assertRaises(ValueError):deferred_native_precision_measure([resolved],BR,None)
        for changed in (dict(POLICY,required_relative_bound=.01),dict(POLICY,kind='bypass'),{}):
            with self.assertRaises(ValueError):validate_native_precision_policy(changed)

    def test_legacy_same_parent_proofs_keep_all_original_checks(self):
        current=dict(schema='old',parent_total_relative_bound=.0005,deferred_full_source_check=False,
            scope='same-parent',nominal_parent_coefficient=1.,conservative_absolute_parent_measure_bound=0.)
        old={k:v for k,v in current.items() if k in ('schema','parent_total_relative_bound')}
        same_native_parent_precision_report(old,current)
        with self.assertRaises(ValueError):same_native_parent_precision_report(old,current,deferred_policy=True)
        with self.assertRaises(ValueError):same_native_parent_precision_report(dict(old,parent_total_relative_bound=.0004),current)

    def test_export_recomputes_every_parent_and_full_exposure_before_admission(self):
        card,config,models,bad,good=fixture()
        bad,=resolve_parent_kinematics([bad],.001,MASS,models,BR,native_precision_policy=POLICY)
        good,=resolve_parent_kinematics([good],.001,MASS,models,BR,native_precision_policy=POLICY)
        coefficient=parent_branching_coefficient(.77511,MASS,models[213],BR)
        bound=coefficient_bound(config['ecm_gev'],BR,models,MASS)
        measure,count=deferred_native_precision_measure([bad],BR,POLICY)
        total_count=3001;difference=bad['coefficient_difference_from_kinematic_mass']
        def row(index,parent):
            return dict(schema='shift-meson-source-trial-v1',trial=index,generator_success=True,native_weight=1.,
                parents=[parent],coefficient_sum=coefficient,physical_probability=1.e-6*coefficient,
                acceptance_probability=coefficient/bound,accepted=index==1,parent_index=44 if index==1 else None,
                accepted_slot=1 if index==1 else None,selected_channel=card['channels'][0] if index==1 else None,
                conservative_native_precision_measure_bound=measure if index==1 else 0.,
                exceptional_native_precision_parent_count=count if index==1 else 0)
        rows=[row(i,bad if i==1 else good) for i in range(1,total_count+1)]
        exposure=dict(schema='shift-meson-source-exposure-v1',fixed_source_collisions=total_count,
            source_calls=total_count,source_collisions=total_count,generator_failures=0,
            zero_eligible_parent_collisions=0,sum_native_weights=float(total_count),
            sum_native_weights_squared=float(total_count),sigma_source_mb=30.,sigma_source_error_mb=.3,
            coefficient_sum=total_count*coefficient,coefficient_sum_squared=total_count*coefficient**2,
            physical_probability_sum=total_count*1.e-6*coefficient,
            physical_probability_sum_squared=total_count*(1.e-6*coefficient)**2,
            accepted_signal_events=1,analytic_coefficient_bound=bound,coefficient_includes_br_mumu=True,
            coefficient_excludes_epsilon_squared=True,unit_output_gen_weights=True,
            coefficient_precision_difference_sum=difference,
            maximum_coefficient_relative_precision_shift=abs(bad['coefficient_relative_precision_shift']),
            relative_rate_precision_shift=difference/(total_count*coefficient),
            conservative_native_precision_measure_sum=measure,
            full_source_native_precision=full_source_native_precision_summary(POLICY,total_count*coefficient,measure,count))
        authority=dict(provider='synthetic pinned width source')
        width=dict(mass_gev=MASS,epsilon=.001,br_mumu=BR,ctau_mm=1.973269804e-3,
            total_width_gev=1.e-10,partial_widths_gev=dict(e_e=6.e-11,mu_mu=4.e-11),spectral_source=authority)
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory);reference=directory/'ErrorChecks.xml';reference.write_text('Synthetic precision fixture\n')
            precision=dict(config,reference=str(reference),reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
                prescription='native generated mass for branching and bound; unchanged p4 invariant for decay')
            contract=dict(production_mechanism='meson-radiative',exclusive_channels=card['channels'],
                epsilon=.001,mass_gev=MASS,physical_width_table=width,width_authority=authority,
                source_precision=precision,resolved_parent_models={str(k):v for k,v in models.items()},requested_events=1)
            def freeze():
                ledger=''.join(json.dumps(value)+'\n' for value in rows).encode()
                (directory/'meson_source_ledger.jsonl').write_bytes(ledger)
                contract['source_ledger_sha256']=exposure['source_ledger_sha256']=hashlib.sha256(ledger).hexdigest()
                contract['source_exposure']=copy.deepcopy(exposure);contract['meson_model_card']=copy.deepcopy(card)
                for name,value,key in [('source_exposure.json',exposure,'source_exposure_sha256'),
                        ('meson_model.json',card,'meson_model_card_sha256')]:
                    payload=(json.dumps(value)+'\n').encode();(directory/name).write_bytes(payload)
                    contract[key]=hashlib.sha256(payload).hexdigest()
            freeze();checked,_,_=meson_source_exposure(contract,directory)
            self.assertTrue(checked['full_source_native_precision']['complete'])
            rows[0]['conservative_native_precision_measure_bound']*=.5;freeze()
            with self.assertRaisesRegex(ValueError,'conservative native measure'):meson_source_exposure(contract,directory)
            rows[0]['conservative_native_precision_measure_bound']=measure
            exposure['full_source_native_precision']['exceptional_parent_count']=0;freeze()
            with self.assertRaisesRegex(ValueError,'completion proof'):meson_source_exposure(contract,directory)
            exposure['full_source_native_precision']['exceptional_parent_count']=1
            rows[:]=rows[:2];exposure.update(fixed_source_collisions=2,source_calls=2,source_collisions=2,
                sum_native_weights=2.,sum_native_weights_squared=2.,coefficient_sum=2*coefficient,
                coefficient_sum_squared=2*coefficient**2,physical_probability_sum=2.e-6*coefficient,
                physical_probability_sum_squared=2*(1.e-6*coefficient)**2,relative_rate_precision_shift=difference/(2*coefficient),
                full_source_native_precision=full_source_native_precision_summary(POLICY,2*coefficient,measure,1))
            freeze()
            with self.assertRaisesRegex(ValueError,'exceeds0.1%'):meson_source_exposure(contract,directory)


if __name__=='__main__':unittest.main()
