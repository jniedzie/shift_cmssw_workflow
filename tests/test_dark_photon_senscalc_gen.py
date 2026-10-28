# Code style: AI-code. Finite source-bank sampling and physical decay controls.
import math
import json
import gzip
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_senscalc_gen as adapter
from run_dark_photon_senscalc_gen import decay_momenta,unweight
from dark_photon_senscalc import source_summary,write_json,digest
from dark_photon_meson import MUON_MASS_GEV


class SourceToGen(unittest.TestCase):
    def test_source_invariant_rounding_does_not_move_muons_off_shell(self):
        cases=[(.7,[1.1997162141804003,-.3688090380961767,-.7582806668419327,-.4881693540979676]),
               (.25,[44.65566272043899,.17600257873885927,-.2035493638763745,-44.654152144570894])]
        for mass,parent in cases:
            for seed in range(50):
                muon,anti,flight=decay_momenta(parent,mass,300.,random.Random(seed))
                for child in (muon,anti):
                    on_shell=math.hypot(MUON_MASS_GEV,*child[1:])
                    bound=128*sys.float_info.epsilon*max(1.,parent[0])**2/(on_shell+child[0])
                    self.assertLessEqual(abs(on_shell-child[0]),bound)
                for i in range(4):self.assertAlmostEqual(muon[i]+anti[i],parent[i],delta=1.e-12*max(1.,parent[0]))
                rng=random.Random(seed);rng.random();rng.random();length=rng.expovariate(1/300.)
                self.assertEqual(flight,[length*x/mass for x in parent[1:]]+[length*parent[0]/mass])
        with self.assertRaisesRegex(ValueError,'narrow support'):
            decay_momenta([1.,0.,0.,0.],.7,300.,random.Random(1))

    def test_combined_bank_requires_ISR_off_and_preserves_the_original_collision_ledger(self):
        from dark_photon_bremsstrahlung import sampling_profile,TABLE_HASHES
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bank=root/'source_bank';model=self.bank(bank)
            original=json.loads((bank/'manifest.json').read_text());original['seed']=123
            original['coefficient_weights_over_epsilon_squared']={str(pdg):0. for pdg in adapter.CHANNELS}
            original['coefficient_weights_over_epsilon_squared'].update({'4900331':.4441015902010675,'4900113':4.})
            write_json(bank/'manifest.json',original)
            p=sampling_profile(.7);tables=Path(p['table_directory']);target=root/'bremsstrahlung_inputs';target.mkdir()
            for name in TABLE_HASHES:shutil.copy2(tables/name,target/name)
            p['table_directory']='bremsstrahlung_inputs';write_json(root/'bremsstrahlung.json',p)
            with self.assertRaisesRegex(ValueError,'explicit ISR off'):
                adapter.source_bank(bank,'pointlike',model)
            with (bank/'source_settings.cmnd').open('a') as stream:stream.write('PartonLevel:ISR=off\n')
            original['settings_sha256']=digest(bank/'source_settings.cmnd');write_json(bank/'manifest.json',original)
            _,rows,summary=adapter.source_bank(bank,'pointlike',model,write_bremsstrahlung_proposals=True)
            self.assertEqual(summary['source_collisions'],4)
            self.assertEqual(summary['bremsstrahlung_proposals'],4)
            self.assertEqual(summary['channels']['proton-bremsstrahlung']['candidates'],4)
            self.assertEqual(digest(bank/'source_ledger.jsonl'),original['ledger_sha256'])
            self.assertEqual(rows,adapter.source_bank(bank,'pointlike',model)[1])
            from dark_photon_bremsstrahlung import SourceSampler
            native=SourceSampler.candidate
            def other_math_runtime(sampler,*args):
                value=native(sampler,*args)
                value['p4'][0]=math.nextafter(value['p4'][0],math.inf)
                value['weight_over_epsilon_squared']=math.nextafter(value['weight_over_epsilon_squared'],math.inf)
                return value
            with patch.object(SourceSampler,'candidate',other_math_runtime):
                self.assertEqual(rows,adapter.source_bank(bank,'pointlike',model)[1])
            (target/next(iter(TABLE_HASHES))).write_text('changed authoritative input')
            with self.assertRaisesRegex(ValueError,'Changed pinned'):
                # Clear the table cache to emulate a fresh audit process.
                import dark_photon_bremsstrahlung as brem
                brem.form_factor_table.cache_clear();adapter.source_bank(bank,'pointlike',model)

    def test_grid_freezes_every_imported_reference_gen_helper(self):
        from prepare_dark_photon_grid import SOURCES
        self.assertFalse(set(adapter.SOURCES)-set(SOURCES))

    def bank(self,directory):
        directory.mkdir();mass=.7
        candidates=[dict(id=pdg,index=i,mother_id=331 if pdg==4900331 else 0,
            weight_over_epsilon_squared=weight,p4=[1.,0.,0.,-math.sqrt(1-mass**2)])
            for i,(pdg,weight) in enumerate([(4900331,.4441015902010675),(4900113,4.)])]
        rows=[dict(trial=i,generator_success=True,native_weight=1.,candidates=[candidates[i-1]] if i<=2 else []) for i in range(1,5)]
        (directory/'source_ledger.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        (directory/'source_settings.cmnd').write_text('Beams:frameType=2\nBeams:idA=2212\nBeams:idB=2212\nBeams:eA=0\nBeams:eB=6800\n')
        summary=source_summary(rows,26.,mass=mass)
        write_json(directory/'manifest.json',dict(schema='shift-senscalc-source-sample-v1',complete=True,mass_gev=mass,source_trials=4,
            ledger_sha256=digest(directory/'source_ledger.jsonl'),settings_sha256=digest(directory/'source_settings.cmnd'),
            native=dict(sigma_source_mb=26.,candidates=2,seed_probability=.01,sum_weights_over_epsilon_squared=summary['sum_weights_over_epsilon_squared'])))
        return json.loads((Path(__file__).resolve().parents[1]/'config/campaigns/dark_photon_meson_model.json').read_text())

    def test_form_factor_changes_only_radiative_channels_and_keeps_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'bank';model=self.bank(directory)
            _,reference,nominal=adapter.source_bank(directory,'pointlike',model)
            _,variation,varied=adapter.source_bank(directory,'measured-form-factor',model)
            self.assertEqual(reference[1],variation[1])
            factor=variation[0]['candidates'][0]['profile_factor']
            self.assertAlmostEqual(factor,13.9867,places=3)
            self.assertEqual(variation[0]['candidates'][0]['reference_weight_over_epsilon_squared'],reference[0]['candidates'][0]['weight_over_epsilon_squared'])
            self.assertLess(varied['sigma_over_epsilon_squared_pb']/nominal['sigma_over_epsilon_squared_pb'],factor)
            (directory/'source_ledger.jsonl').write_text('{}\n')
            with self.assertRaisesRegex(ValueError,'unchanged reference source bank'):
                adapter.source_bank(directory,'pointlike',model)

    def test_full_exposure_and_epsilon_squared_branching_are_applied_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);model=self.bank(directory/'source_bank')
            manifest,rows,summary=adapter.source_bank(directory/'source_bank','pointlike',model)
            for name in ('manifest.json','source_ledger.jsonl'):
                (directory/('source_manifest.json' if name=='manifest.json' else name)).write_bytes((directory/'source_bank'/name).read_bytes())
            write_json(directory/'radiative_model.json',model)
            selected,ledger=unweight(rows,711);write_json(directory/'selected_signals.json',selected)
            inputs=directory/'width_inputs';inputs.mkdir();write_json(inputs/'source_receipt.json',{})
            authority=dict(source_receipt_sha256=digest(inputs/'source_receipt.json'),files_sha256={})
            width=dict(br_mumu=.4,ctau_mm=300.,spectral_source=authority)
            exposure=dict(schema='shift-senscalc-source-exposure-v1',source_collisions=4,fixed_source_collisions=4,
                accepted_signal_events=len(selected),unit_output_gen_weights=True,raw_source_summary=summary)
            write_json(directory/'source_exposure.json',exposure)
            epsilon=1.e-6;factor=epsilon**2*.4
            contract=dict(production_mechanism=adapter.MECHANISM,mass_gev=.7,rate_profile='pointlike',seed=711,requested_events=len(selected),epsilon=epsilon,
                physical_width_table=width,width_authority=width,source_exposure=exposure,proposal_ledger=ledger,
                target_cross_section=dict(value_pb=summary['sigma_over_epsilon_squared_pb']*factor,
                    error_pb=summary['sigma_statistical_error_over_epsilon_squared_pb']*factor))
            for name,key in [('source_manifest.json','source_manifest_sha256'),('source_ledger.jsonl','source_ledger_sha256'),
                             ('source_exposure.json','source_exposure_sha256'),('radiative_model.json','radiative_model_sha256')]:
                contract[key]=digest(directory/name)
            with patch.dict(sys.modules,darkcast=SimpleNamespace(Model=lambda name:object())),patch.object(adapter,'physical_width_table',return_value=width):
                checked=adapter.accounting(directory,contract)
                self.assertEqual(checked['source_exposure']['source_collisions'],4)
                self.assertEqual(checked['target_cross_sections'],[contract['target_cross_section']])
                raw=(directory/'source_bank/source_ledger.jsonl').read_bytes()
                with gzip.open(directory/'source_bank/source_ledger.jsonl.gz','wb') as stream:stream.write(raw)
                (directory/'source_bank/source_ledger.jsonl').unlink()
                (directory/'source_ledger.jsonl').unlink()
                self.assertEqual(adapter.accounting(directory,contract),checked)
                contract['mass_gev']=.8
                with self.assertRaisesRegex(ValueError,'mass differs'):adapter.accounting(directory,contract)
                contract['mass_gev']=.7
                contract['target_cross_section']['value_pb']*=2
                with self.assertRaisesRegex(ValueError,'applied once'):adapter.accounting(directory,contract)

    def test_boost_reference_keeps_zero_candidate_collisions_and_shared_candidates(self):
        rows=[dict(candidates=[dict(weight_over_epsilon_squared=2.,p4=[5.,0.,0.,3.])]*2),
              dict(candidates=[dict(weight_over_epsilon_squared=1.,p4=[5.,0.,0.,4.])]),dict(candidates=[])]
        mean,error=adapter.boost_reference(rows,1.)
        self.assertAlmostEqual(mean,3.2)
        self.assertAlmostEqual(error,math.sqrt(1.5*(.8**2+.8**2))/5)

    def test_unweighting_preserves_identities_and_never_duplicates_exposure(self):
        rows=[dict(trial=i,candidates=[dict(index=11,weight_over_epsilon_squared=1. if i%2 else 4.)])
              for i in range(1,1001)]
        selected,ledger=unweight(rows,711)
        self.assertEqual((selected,ledger),unweight(rows,711))
        self.assertEqual(len({(x['source_trial'],x['index']) for x in selected}),len(selected))
        self.assertEqual([x['event_slot'] for x in selected],list(range(1,len(selected)+1)))
        self.assertTrue(all(x['sampling_probability']==x['weight_over_epsilon_squared']/4 for x in selected))
        self.assertTrue(all(i in {x['source_trial'] for x in selected} for i in range(2,1001,2)))
        self.assertLess(abs(len(selected)-625),40)
        self.assertEqual(ledger['n_accepted'],len(selected))
        self.assertEqual(ledger['n_tried'],1000)

    def test_a_declared_physics_envelope_does_not_follow_missing_rare_candidates(self):
        rows=[dict(trial=1,candidates=[dict(index=1,weight_over_epsilon_squared=1.)])]
        _,small=unweight(rows,711,envelope=4.)
        self.assertEqual(small['envelope'],4.)
        rows.append(dict(trial=2,candidates=[dict(index=2,weight_over_epsilon_squared=4.)]))
        _,larger=unweight(rows,711,envelope=4.)
        self.assertEqual(small['envelope'],larger['envelope'])
        with self.assertRaisesRegex(ValueError,'exceeds the frozen physical envelope'):
            unweight(rows,711,envelope=3.)

    def test_closed_radiative_channels_do_not_evaluate_an_unphysical_pole(self):
        model=json.loads((Path(__file__).resolve().parents[1]/'config/campaigns/dark_photon_meson_model.json').read_text())
        eta=next(parent for parent in model['parents'] if parent['pdg_id']==221)
        eta['transition_form_factor']=dict(kind='single-pole',lambda_gev=.7)
        manifest=dict(mass_gev=.7,proposal_probability=.01,
            coefficient_weights_over_epsilon_squared={str(key):0. for key in adapter.CHANNELS})
        manifest['coefficient_weights_over_epsilon_squared']['4900113']=4.
        self.assertTrue(math.isfinite(adapter.unweighting_envelope(manifest,'measured-form-factor',model)))

    def test_boost_conserves_momentum_and_mass_at_forward_signal_energies(self):
        for mass in (.22,.7):
            momentum=[.1,-.3,-6000.];energy=math.sqrt(mass*mass+sum(x*x for x in momentum))
            parent=[energy]+momentum
            muon,anti,flight=decay_momenta(parent,mass,300.,random.Random(25))
            for a,b,c in zip(muon,anti,parent):self.assertAlmostEqual(a+b,c,places=10)
            for p in (muon,anti):
                invariant=math.fsum([p[0]**2]+[-x*x for x in p[1:]])
                self.assertAlmostEqual(invariant,MUON_MASS_GEV**2,delta=2.e-8)
            proper=flight[3]*mass/energy
            for displacement,component in zip(flight,momentum+[energy]):
                self.assertAlmostEqual(displacement,proper*component/mass,places=8)

    def test_lifetime_is_not_truncated_to_the_detector(self):
        lengths=[];cosines=[];rng=random.Random(125)
        for _ in range(10000):
            muon,anti,flight=decay_momenta([.7,0.,0.,0.],.7,300.,rng)
            lengths.append(flight[3]);cosines.append(muon[3]/math.sqrt(sum(x*x for x in muon[1:])))
        self.assertLess(abs(sum(lengths)/len(lengths)/300.-1.),.04)
        self.assertGreater(max(lengths),2000.)
        self.assertLess(abs(sum(cosines)/len(cosines)),.03)


if __name__=='__main__':unittest.main()
