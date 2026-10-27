# Code style: AI-code. AI-generated full-source sampling and normalization tests.
import math
import copy
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_meson_source import (coefficient_bound, choose_collision, source_cross_section_pb,
    resolve_parent_kinematics, parent_branching_coefficient, resolved_parent_models, production_mechanism,
    radiative_cascade_owner)
from dark_photon_vector_meson import two_body_momentum


class CorrelatedCurrentSourceTest(unittest.TestCase):
    def double(self, pdg=221, recoil=111, *, scalar=False):
        from test_dark_photon_double_radiative import reference_model,scalar_model,MASSES
        return dict(pdg_id=pdg,channel=('eta' if pdg==221 else 'eta_prime')+
            ('->pi0_gamma_Aprime' if recoil==111 else '->eta_gamma_Aprime'),
            mass_gev=MASSES[pdg],decay_type='pseudoscalar-double-radiative',
            parent_mass_model='zero-width-pole',recoil_pdg_id=recoil,recoil_mass_gev=MASSES[recoil],
            total_parent_width_gev=1.31e-6 if pdg==221 else .000196,
            relative_numerical_mass_precision_budget=1.e-3,
            coherent_replaced_vector_pdgs=[113,223,333],pdg_reference='synthetic source wrapper; kernel inputs attributed',
            double_radiative_model=(scalar_model if scalar else reference_model)(pdg,recoil))

    def quark(self, channel='eta_c->gamma_Aprime'):
        from test_dark_photon_quarkonium import channel_model, MASSES
        row=channel_model(channel);name=channel.split('->')[0]
        pdg=dict(eta_c=441,chi_c0=10441,chi_c1=20443,chi_c2=445,h_c=10443,psi_2S=100443)[name]
        mass=MASSES[name]
        row.update(pdg_id=pdg,mass_gev=mass,parent_mass_model='zero-width-pole',
            relative_numerical_mass_precision_budget=1.e-3,
            pdg_reference='synthetic source wrapper; kernel inputs attributed')
        row['quarkonium_model'].update(reference_parent_mass_gev=mass,
            reference_recoil_mass_gev=row['recoil_mass_gev'])
        return row

    @staticmethod
    def resolve(parents, mass=.22, epsilon=1.e-6):
        card=dict(mechanism='meson-radiative',parents=parents,channels=[p['channel'] for p in parents])
        native={p['pdg_id']:dict(pdg_id=p['pdg_id'],mass_min_gev=p['mass_gev'],
            mass_max_gev=p['mass_gev'],mass_width_gev=0.) for p in parents}
        return resolved_parent_models(card,dict(parents=list(native.values())),mass,epsilon=epsilon)

    def test_rate_uses_physical_em_coupling_and_dimuon_branching_once(self):
        from dark_photon_meson_source import current_kernel
        from dark_photon_model import mixing_parameters
        for parent in [self.double(),self.double(scalar=True),self.quark(),self.quark('chi_c1->Jpsi_Aprime')]:
            with self.subTest(channel=parent['channel']):
                model=self.resolve([parent])[parent['pdg_id']]
                bare=current_kernel(model).branching_coefficient(parent['mass_gev'],.22,model)
                factor=(mixing_parameters(.22,1.e-6).em_coefficient/1.e-6)**2
                self.assertAlmostEqual(parent_branching_coefficient(parent['mass_gev'],.22,model,.17),bare*factor*.17)
                self.assertEqual(parent_branching_coefficient(parent['mass_gev'],.22,model,0.),0.)
                with self.assertRaisesRegex(ValueError,'another signal mass'):
                    parent_branching_coefficient(parent['mass_gev'],.25,model,.17)

    def test_fixed_pole_count_envelope_and_closed_heavy_channel(self):
        parent=self.quark('chi_c0->Jpsi_Aprime')
        model=self.resolve([parent])[parent['pdg_id']]
        rate=parent_branching_coefficient(parent['mass_gev'],.22,model,.2)
        bound=coefficient_bound(113.,.2,{parent['pdg_id']:model},.22)
        self.assertEqual(bound,math.floor(113./parent['mass_gev'])*rate)
        closed=self.resolve([parent],mass=.4)[parent['pdg_id']]
        self.assertFalse(closed['kinematically_open'])
        self.assertEqual(coefficient_bound(113.,.2,{parent['pdg_id']:closed},.4),0.)
        with self.assertRaisesRegex(ValueError,'fixed signal mass'):
            coefficient_bound(113.,.2,{parent['pdg_id']:model})

    def test_scalar_current_must_match_its_source_parent_and_recoil(self):
        for key,value in [('parent_pdg_id',331),('recoil_pdg_id',221),
                          ('parent_mass_gev',.6),('recoil_mass_gev',.14)]:
            parent=self.double(scalar=True)
            parent['double_radiative_model']['scalar_current'][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'species and pole masses'):
                self.resolve([parent])

    def test_source_rejects_wrong_species_pole_or_undeclared_ownership(self):
        for change in ['species','current_pole','native_pole','budget','overlap']:
            parent=self.double() if change=='overlap' else self.quark('chi_c1->Jpsi_Aprime')
            if change=='species':parent['pdg_id']=441
            if change=='current_pole':parent['quarkonium_model']['reference_parent_mass_gev']+=.01
            if change=='native_pole':parent['parent_mass_model']='broad-unreviewed'
            if change=='budget':parent['relative_numerical_mass_precision_budget']=.1
            if change=='overlap':parent.pop('coherent_replaced_vector_pdgs')
            with self.subTest(change=change),self.assertRaises(ValueError):self.resolve([parent])
        parent=self.quark()
        with self.assertRaisesRegex(ValueError,'physical epsilon'):
            self.resolve([parent],epsilon=None)

    def test_coherent_replacement_removes_only_the_owned_channel_from_collision_rate(self):
        import dark_photon_meson_source as source
        owner=self.double(331)
        owner_model=self.resolve([owner])[331]
        vector=dict(mass_min_gev=.77526,mass_max_gev=.77526,decay_type='vector-pseudoscalar',
            channel='rho0->pi0_Aprime',recoil_pdg_id=111)
        models={331:owner_model,113:dict(mass_min_gev=.77526,mass_max_gev=.77526,
            exclusive_channels=[vector,dict(vector,channel='rho0->eta_Aprime',recoil_pdg_id=221)])}
        parents=[dict(index=5,pdg_id=331,mass_gev=.95778),dict(index=12,pdg_id=113,mass_gev=.77526,
            radiative_ancestor=dict(index=5,pdg_id=331,vector_pdg_id=113,sibling_pdg_id=22))]
        def rate(parent_mass,mass,channel,br):
            return 7. if channel['channel']==owner['channel'] else 3.
        with patch.object(source,'validate_models'),patch.object(source,'parent_branching_coefficient',side_effect=rate):
            result=choose_collision(parents,.22,.2,models,100.,random.Random(2))
        self.assertEqual(result['coefficient_sum'],10.)
        self.assertEqual(result['coherently_replaced_cascades'],
            [dict(parent_index=12,channel='rho0->pi0_Aprime',owner=owner['channel'])])

    def test_native_precision_preserves_the_original_record_and_checks_the_same_current(self):
        model=self.resolve([self.quark()])[441]
        parent=dict(index=1,pdg_id=441,native_mass_gev=model['mass_min_gev'],
            p4=[model['mass_min_gev'],0.,0.,0.])
        original=copy.deepcopy(parent)
        checked,=resolve_parent_kinematics([parent],.001,.22,{441:model},.2)
        self.assertEqual(parent,original)
        self.assertEqual(checked['p4'],original['p4'])
        self.assertEqual(checked['coefficient_relative_precision_shift'],0.)

    def test_exporter_reconstructs_coherent_ownership_and_rejects_double_counting(self):
        from export_dark_photon_cross_sections import meson_source_exposure
        owner=self.double(331)
        path=Path(__file__).resolve().parents[1]/'config/campaigns/dark_photon_meson_extended_model.json'
        vector=next(row for row in json.loads(path.read_text())['parents'] if row['channel']=='rho0->pi0_Aprime')
        card=dict(schema='shift-dark-photon-meson-model-v1',mechanism='meson-radiative',
            parents=[owner,vector],channels=[owner['channel'],vector['channel']])
        configuration=dict(ecm_gev=113.,check_event=True,mass_tolerance=.001,energy_tolerance=.01,
            parents=[dict(pdg_id=p['pdg_id'],mass_min_gev=p['mass_gev'],mass_max_gev=p['mass_gev'],mass_width_gev=0.)
                     for p in card['parents']])
        models=resolved_parent_models(card,configuration,.22,epsilon=.001)
        momentum=two_body_momentum(owner['mass_gev'],vector['mass_gev'],0.)
        raw=[dict(index=5,pdg_id=331,native_mass_gev=owner['mass_gev'],p4=[owner['mass_gev'],0.,0.,0.]),
             dict(index=12,pdg_id=113,native_mass_gev=vector['mass_gev'],
                p4=[math.hypot(vector['mass_gev'],momentum),0.,0.,momentum],
                radiative_ancestor=dict(index=5,pdg_id=331,vector_pdg_id=113,sibling_pdg_id=22))]
        parents=resolve_parent_kinematics(raw,.001,.22,models,.4)
        bound=coefficient_bound(113.,.4,models,.22)
        class AcceptFirst:
            def random(self):return 0.
        decision=choose_collision(parents,.22,.4,models,bound,AcceptFirst())
        coefficient=decision['coefficient_sum'];probability=1.e-6*coefficient
        rows=[dict(decision,schema='shift-meson-source-trial-v1',trial=1,generator_success=True,native_weight=1.,
            parents=parents,physical_probability=probability,accepted_slot=1),
            dict(schema='shift-meson-source-trial-v1',trial=2,generator_success=True,native_weight=1.,
                parents=[],coefficient_sum=0.,physical_probability=0.,acceptance_probability=0.,accepted=False,parent_index=None)]
        difference=math.fsum(p['coefficient_difference_from_kinematic_mass'] for p in parents)
        exposure=dict(schema='shift-meson-source-exposure-v1',fixed_source_collisions=2,source_calls=2,
            source_collisions=2,generator_failures=0,zero_eligible_parent_collisions=1,
            sum_native_weights=2.,sum_native_weights_squared=2.,sigma_source_mb=30.,sigma_source_error_mb=.3,
            coefficient_sum=coefficient,coefficient_sum_squared=coefficient**2,
            physical_probability_sum=probability,physical_probability_sum_squared=probability**2,
            accepted_signal_events=1,analytic_coefficient_bound=bound,coefficient_includes_br_mumu=True,
            coefficient_excludes_epsilon_squared=True,unit_output_gen_weights=True,
            coefficient_precision_difference_sum=difference,
            maximum_coefficient_relative_precision_shift=max(abs(p['coefficient_relative_precision_shift']) for p in parents),
            relative_rate_precision_shift=difference/coefficient)
        authority=dict(provider='synthetic pinned width source')
        width=dict(mass_gev=.22,epsilon=.001,br_mumu=.4,ctau_mm=1.973269804e-3,total_width_gev=1.e-10,
            partial_widths_gev=dict(e_e=6.e-11,mu_mu=4.e-11),spectral_source=authority)
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);reference=directory/'ErrorChecks.xml';reference.write_text('Synthetic source precision authority\n')
            precision=dict(configuration,reference=str(reference),reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
                prescription='native generated mass for branching and bound; unchanged p4 invariant for decay')
            contract=dict(production_mechanism='meson-radiative',exclusive_channels=card['channels'],epsilon=.001,mass_gev=.22,
                physical_width_table=width,width_authority=authority,source_precision=precision,
                resolved_parent_models={str(k):v for k,v in models.items()},requested_events=1)
            def freeze():
                ledger=''.join(json.dumps(row)+'\n' for row in rows).encode();(directory/'meson_source_ledger.jsonl').write_bytes(ledger)
                contract['source_ledger_sha256']=exposure['source_ledger_sha256']=hashlib.sha256(ledger).hexdigest()
                contract['source_exposure']=copy.deepcopy(exposure);contract['meson_model_card']=copy.deepcopy(card)
                for filename,value,key in [('source_exposure.json',exposure,'source_exposure_sha256'),('meson_model.json',card,'meson_model_card_sha256')]:
                    payload=json.dumps(value).encode();(directory/filename).write_bytes(payload);contract[key]=hashlib.sha256(payload).hexdigest()
            freeze();_,rate,_=meson_source_exposure(contract,directory)
            self.assertAlmostEqual(rate['value_pb'],30.e9*probability/2)
            replacement=rows[0]['coherently_replaced_cascades'];rows[0]['coherently_replaced_cascades']=[];freeze()
            with self.assertRaisesRegex(ValueError,'ownership'):meson_source_exposure(contract,directory)
            rows[0]['coherently_replaced_cascades']=replacement
            rows[0]['coefficient_sum']+=parent_branching_coefficient(vector['mass_gev'],.22,models[113],.4);freeze()
            with self.assertRaisesRegex(ValueError,'parent branching sum'):meson_source_exposure(contract,directory)


class MesonSourceTest(unittest.TestCase):
    def test_jpsi_channels_share_source_and_apply_exact_em_mixing_once(self):
        from dark_photon_model import mixing_parameters
        from dark_photon_vector_meson import radiative_branching
        parents=[dict(pdg_id=443,mass_gev=3.0969,pdg_reference='synthetic fixture',
            channel='Jpsi_to_'+str(recoil),br_p_gamma=.001,br_p_gamma_uncertainty=.0001,
            decay_type='vector-pseudoscalar',parent_mass_model='zero-width-pole',
            parent_polarization='unpolarized',recoil_pdg_id=recoil,recoil_mass_gev=recoil_mass,
            transition_form_factor=dict(kind='single-pole-slope',slope_gev_minus2=.1,
                slope_stat_gev_minus2=.01,slope_syst_gev_minus2=.01,
                valid_mass_range_gev=[.211316751,2.9],reference='synthetic fixture'))
            for recoil,recoil_mass in ((111,.1349768),(221,.547862),(331,.95778))]
        card=dict(mechanism='meson-radiative',parents=parents,channels=[p['channel'] for p in parents])
        configuration=dict(parents=[dict(pdg_id=443,mass_min_gev=3.0969,mass_max_gev=3.0969,mass_width_gev=0.)])
        mass,epsilon,br=.3,.01,.4
        models=resolved_parent_models(card,configuration,mass,epsilon=epsilon)
        channels=models[443]['exclusive_channels']
        expected=[]
        for row in channels:
            value=radiative_branching(3.0969,row['recoil_mass_gev'],mass,
                mixing_parameters(mass,epsilon).em_coefficient,row['br_p_gamma'],row['form_factor_abs2'])
            expected.append(value/epsilon**2*br)
            self.assertAlmostEqual(parent_branching_coefficient(3.0969,mass,row,br),expected[-1],places=15)
            with self.assertRaisesRegex(ValueError,'another signal mass'):
                parent_branching_coefficient(3.0969,.4,row,br)
        bound=coefficient_bound(113.,br,models,mass)
        self.assertAlmostEqual(bound,math.floor(113./3.0969)*sum(expected),places=14)
        selected=choose_collision([dict(index=7,pdg_id=443,mass_gev=3.0969)],mass,br,models,bound,random.Random(1))
        self.assertEqual(selected['parent_count'],1)
        self.assertAlmostEqual(selected['coefficient_sum'],sum(expected),places=15)
        with self.assertRaisesRegex(ValueError,'physical source epsilon'):
            resolved_parent_models(card,configuration,mass)
        malformed=copy.deepcopy(models);malformed[443]['exclusive_channels'][0]['em_mixing_over_epsilon_abs2']=1.
        with self.assertRaisesRegex(ValueError,'physical source epsilon'):
            coefficient_bound(113.,br,malformed,mass)

    def test_coherent_parent_owns_only_its_matching_radiative_vector_cascade(self):
        owner=dict(decay_type='pseudoscalar-double-radiative',channel='eta_prime->pi0_gamma_Aprime',
            recoil_pdg_id=111,coherent_replaced_vector_pdgs=[113,223,333])
        models={331:owner}
        vector=dict(index=12,pdg_id=113,radiative_ancestor=dict(index=5,pdg_id=331,
            vector_pdg_id=113,sibling_pdg_id=22))
        source=[dict(index=5,pdg_id=331),vector]
        channel=dict(decay_type='vector-pseudoscalar',channel='rho0->pi0_Aprime',recoil_pdg_id=111)
        self.assertEqual(radiative_cascade_owner(vector,channel,models,source),owner['channel'])
        self.assertIsNone(radiative_cascade_owner(vector,dict(channel,recoil_pdg_id=221),models,source))
        self.assertIsNone(radiative_cascade_owner(dict(vector,radiative_ancestor=None),channel,models,source))
        self.assertIsNone(radiative_cascade_owner(vector,dict(decay_type='vector-dipion'),models,source))
        with self.assertRaisesRegex(ValueError,'absent'):
            radiative_cascade_owner(vector,channel,models,[vector])
        missing=dict(vector);missing.pop('radiative_ancestor')
        with self.assertRaisesRegex(ValueError,'requires native ancestry'):
            radiative_cascade_owner(missing,channel,models,source)
        malformed=dict(vector,radiative_ancestor=dict(vector['radiative_ancestor'],vector_pdg_id=223))
        with self.assertRaisesRegex(ValueError,'Invalid coherent'):
            radiative_cascade_owner(malformed,channel,models,source)

    def test_mechanism_cannot_mislabel_new_exclusive_channels(self):
        legacy = dict(parents=[dict(pdg_id=221)])
        self.assertEqual(production_mechanism(legacy), 'eta-etaprime-radiative')
        with self.assertRaisesRegex(ValueError, 'Unknown meson'):
            production_mechanism(dict(legacy, mechanism='misspelled'))
        for decay in ('vector-pseudoscalar', 'pseudoscalar-dipion'):
            parent = dict(pdg_id=331, decay_type=decay)
            for inventory in ([parent], [dict(exclusive_channels=[parent])]):
                with self.subTest(decay=decay, inventory=inventory):
                    card = dict(parents=inventory)
                    with self.assertRaisesRegex(ValueError, 'meson-radiative'):
                        production_mechanism(card)
                    card['mechanism'] = 'meson-radiative'
                    with self.assertRaisesRegex(ValueError, 'channel inventory'):
                        production_mechanism(card)
                    card['channels'] = ['synthetic-test-channel']
                    self.assertEqual(production_mechanism(card), 'meson-radiative')

    def setUp(self):
        self.models = {
            221: dict(mass_min_gev=.547, br_gamma_gamma=.39, form_factor_abs2=1., provenance='synthetic fixture'),
            331: dict(mass_min_gev=.957, br_gamma_gamma=.023, form_factor_abs2=2., provenance='synthetic fixture')}
        self.parents = [dict(index=17, pdg_id=221, mass_gev=.548),
                        dict(index=28, pdg_id=331, mass_gev=.958)]

    def test_energy_bound_covers_every_physically_allowed_parent_count(self):
        bound = coefficient_bound(10., .2, self.models)
        maximum = sum(math.floor(10./row['mass_min_gev'])*2*row['br_gamma_gamma']*
                      row['form_factor_abs2']*.2 for row in self.models.values())
        self.assertEqual(bound, maximum)
        result = choose_collision(self.parents, .3, .2, self.models, bound, random.Random(1))
        self.assertLessEqual(result['coefficient_sum'], bound)

    def test_zero_parent_and_closed_channels_stay_in_the_source_exposure(self):
        bound = coefficient_bound(10., .2, self.models)
        for parents, mass in (([], .3), (self.parents, 1.)):
            row = choose_collision(parents, mass, .2, self.models, bound, random.Random(1))
            self.assertFalse(row['accepted'])
            self.assertEqual(row['coefficient_sum'], 0.)
            self.assertEqual(row['acceptance_probability'], 0.)
        # Including the zero-parent collision halves the rate, rather than
        # renormalizing to the subset of interactions containing mesons.
        self.assertEqual(source_cross_section_pb(1., .001, 4., 2), 2000.)
        self.assertEqual(source_cross_section_pb(1., .002, 4., 2), 8000.)

    def test_collision_unweighting_and_one_parent_selection_close(self):
        rng = random.Random(761016017)
        bound = coefficient_bound(2., .2, self.models)
        accepted, selected, sums = 0, {17: 0, 28: 0}, []
        for _ in range(80000):
            row = choose_collision(self.parents, .3, .2, self.models, bound, rng)
            sums.append(row['coefficient_sum'])
            if row['accepted']:
                self.assertEqual(row['gen_weight'], 1.)
                accepted += 1
                selected[row['parent_index']] += 1
        expected = sums[0]/bound
        self.assertLess(abs(accepted/len(sums)-expected), 5*math.sqrt(expected*(1-expected)/len(sums)))
        eta = choose_collision(self.parents[:1], .3, .2, self.models, bound, random.Random(2))['coefficient_sum']
        fraction = eta/sums[0]
        self.assertLess(abs(selected[17]/accepted-fraction), 5*math.sqrt(fraction*(1-fraction)/accepted))

    def test_weights_bad_parent_inventory_and_unproven_bounds_fail_closed(self):
        bound = coefficient_bound(10., .2, self.models)
        for kwargs in (dict(native_weight=.5), dict(native_weight=True), dict(bound=1.e-6),
                       dict(parents=self.parents+self.parents), dict(parents=[], mass_gev=float('nan')),
                       dict(parents=[dict(index=-1,pdg_id=221,mass_gev=.548)]),
                       dict(parents=[dict(index=1,pdg_id=111,mass_gev=.135)]),
                       dict(parents=[dict(index=1,pdg_id=221,mass_gev=.5)])):
            arguments = dict(parents=self.parents, mass_gev=.3, br_mumu=.2, models=self.models,
                             bound=bound, rng=random.Random(1))
            arguments.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                choose_collision(**arguments)
        self.models[221]['provenance'] = ''
        with self.assertRaises(ValueError):
            coefficient_bound(10., .2, self.models)

    def test_native_mass_precision_is_retained_bounded_and_never_repaired(self):
        self.models[221]['relative_model_uncertainty'] = .01
        original = dict(index=38,pdg_id=221,native_mass_gev=.547862,
            p4=[570.5010019649538,-.7004529990853869,.7446772589972893,-570.4998228797658])
        row, = resolve_parent_kinematics([original],1.e-3,.3,self.models,.2)
        self.assertEqual(row['p4'],original['p4'])
        self.assertEqual(row['mass_gev'],original['native_mass_gev'])
        self.assertGreater(row['kinematic_mass_gev'],row['native_mass_gev'])
        self.assertGreater(row['coefficient_relative_precision_shift'],0.)
        with self.assertRaisesRegex(ValueError,'Pythia mass tolerance'):
            resolve_parent_kinematics([original],1.e-10,.3,self.models,.2)
        self.models[221]['relative_model_uncertainty'] = 1.e-10
        with self.assertRaisesRegex(ValueError,'BR/TFF precision budget'):
            resolve_parent_kinematics([original],1.e-3,.3,self.models,.2)
        crossing = dict(index=1,pdg_id=221,native_mass_gev=.300001,p4=[.299999,0.,0.,0.])
        with self.assertRaisesRegex(ValueError,'whether the rare decay is open'):
            resolve_parent_kinematics([crossing],1.e-3,.3,self.models,.2)

    def test_unified_vector_and_pseudoscalar_weights_share_one_collision_bound(self):
        omega = dict(mass_min_gev=.78266, mass_max_gev=.78266, br_p_gamma=.0833,
            form_factor_abs2=1.7, decay_type='vector-pseudoscalar', recoil_pdg_id=111,
            recoil_mass_gev=.1349768, parent_polarization='unpolarized',
            parent_mass_model='zero-width-pole', provenance='synthetic fixture')
        self.models[223] = omega
        parents = self.parents + [dict(index=36,pdg_id=223,mass_gev=.78266)]
        br = .2
        value = parent_branching_coefficient(.78266,.3,omega,br)
        expected = .0833*1.7*br*(two_body_momentum(.78266,.1349768,.3)/
                                               two_body_momentum(.78266,.1349768,0.))**3
        self.assertAlmostEqual(value,expected,places=14)
        # A common bound selects the actual mixture. Pooling equal numbers
        # from independent component proposals would fail this closure.
        bound = coefficient_bound(2.,br,self.models,.3)
        rng = random.Random(761017102)
        accepted = 0
        omega_selected = 0
        count = 60000
        total = choose_collision(parents,.3,br,self.models,bound,random.Random(1))['coefficient_sum']
        for _ in range(count):
            decision = choose_collision(parents,.3,br,self.models,bound,rng)
            if decision['accepted']:
                accepted += 1
                omega_selected += decision['parent_index'] == 36
        probability = total/bound
        self.assertLess(abs(accepted/count-probability),5*math.sqrt(probability*(1-probability)/count))
        fraction = value/total
        self.assertLess(abs(omega_selected/accepted-fraction),5*math.sqrt(fraction*(1-fraction)/accepted))
        self.assertEqual(parent_branching_coefficient(.78266,.7,omega,br),0.)
        with self.assertRaisesRegex(ValueError,'zero-width'):
            coefficient_bound(2.,br,{223:dict(omega,mass_max_gev=.85)},.3)

    def test_vector_parent_card_requires_qualified_native_pole_and_form_factor_domain(self):
        parent = dict(pdg_id=223,mass_gev=.78266,pdg_reference='synthetic fixture',
            br_p_gamma=.0833,br_p_gamma_uncertainty=.0025,decay_type='vector-pseudoscalar',
            recoil_pdg_id=111,recoil_mass_gev=.1349768,parent_polarization='unpolarized',
            parent_mass_model='zero-width-pole',transition_form_factor=dict(kind='single-pole-slope',
                slope_gev_minus2=2.223,slope_stat_gev_minus2=.026,slope_syst_gev_minus2=.037,
                valid_mass_range_gev=[.211316751,.64],reference='synthetic fixture'))
        configuration = dict(parents=[dict(pdg_id=223,mass_min_gev=.78266,mass_max_gev=.78266,mass_width_gev=0.)])
        model = resolved_parent_models(dict(parents=[parent]),configuration,.3)[223]
        self.assertTrue(model['kinematically_open'])
        self.assertGreater(model['relative_model_uncertainty'],0.)
        with self.assertRaisesRegex(ValueError,'domain'):
            resolved_parent_models(dict(parents=[parent]),configuration,.645)
        self.assertFalse(resolved_parent_models(dict(parents=[parent]),configuration,.7)[223]['kinematically_open'])
        configuration['parents'][0]['mass_width_gev'] = .00849
        with self.assertRaisesRegex(ValueError,'zero-width'):
            resolved_parent_models(dict(parents=[parent]),configuration,.3)

    def test_one_parent_selects_exclusive_channels_without_duplicate_source_entries(self):
        first = dict(mass_min_gev=1.02, mass_max_gev=1.02, br_p_gamma=.013,
            form_factor_abs2=1.2, decay_type='vector-pseudoscalar', recoil_pdg_id=221,
            recoil_mass_gev=.548, parent_polarization='unpolarized',
            parent_mass_model='zero-width-pole', provenance='synthetic fixture', channel='phi_eta')
        second = dict(first, br_p_gamma=.0013, recoil_pdg_id=111,
                      recoil_mass_gev=.135, channel='phi_pi0')
        models = {333: dict(mass_min_gev=1.02, mass_max_gev=1.02,
                            exclusive_channels=[first,second])}
        parent = dict(index=5,pdg_id=333,mass_gev=1.02)
        values = [parent_branching_coefficient(1.02,.3,row,.3) for row in (first,second)]
        bound = coefficient_bound(2.04,.3,models,.3)
        self.assertAlmostEqual(bound,2*sum(values))
        rng = random.Random(761017202)
        selected = {'phi_eta':0,'phi_pi0':0}
        for _ in range(30000):
            row = choose_collision([parent],.3,.3,models,bound,rng)
            self.assertEqual(row['parent_count'],1)
            self.assertAlmostEqual(row['coefficient_sum'],sum(values))
            if row['accepted']:
                self.assertEqual(row['parent_index'],5)
                selected[row['selected_channel']] += 1
        probability = values[1]/sum(values)
        count = sum(selected.values())
        self.assertLess(abs(selected['phi_pi0']/count-probability),
                        5*math.sqrt(probability*(1-probability)/count))
        with self.assertRaisesRegex(ValueError,'repeated'):
            choose_collision([parent,parent],.3,.3,models,bound,rng)

    def test_charged_transition_requires_the_matching_recoil_charge(self):
        model = dict(mass_min_gev=.77511,mass_max_gev=.77511,br_p_gamma=.0004,
            form_factor_abs2=1.,decay_type='vector-pseudoscalar',recoil_pdg_id=211,
            recoil_mass_gev=.13957,parent_polarization='unpolarized',
            parent_mass_model='zero-width-pole',provenance='synthetic fixture')
        self.assertGreater(coefficient_bound(2.,.3,{213:model},.3),0.)
        with self.assertRaisesRegex(ValueError,'charge-conjugate'):
            coefficient_bound(2.,.3,{-213:model},.3)
        self.assertGreater(coefficient_bound(2.,.3,{-213:dict(model,recoil_pdg_id=-211)},.3),0.)


if __name__ == '__main__':
    unittest.main()
