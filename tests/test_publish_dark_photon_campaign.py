# Code style: AI-code. AI-generated cumulative DP publication failure tests.
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch,MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import publish_dark_photon_campaign as publisher
import aggregate_dark_photon_samples as aggregate


def source(name='a'):
    return dict(plan_path='/persistent/'+name+'/plan.json',point='m5_prompt',
        plan_sha256=name*64,seal_sha256='b'*64,completed_point_sha256='c'*64)


class PublicationOwnershipTests(unittest.TestCase):
    def fixture(self,directory):
        config=dict(campaign='DP_20261111',output_base='/eos/user/t/test',retained_signals=[],
            state_path=str(directory/'state.json'),catalog_path=str(directory/'catalog.json'),
            configuration_path=str(directory/'config.json'),configuration_sha256='d'*64)
        return config,dict(published={},attempts={},quarantined={})

    def test_frozen_aggregate_closure_follows_non_dark_local_lazy_imports(self):
        from run_dark_photon_statistics import aggregate_helper_names
        with tempfile.TemporaryDirectory() as temporary:
            tools=Path(temporary)
            for name in ['aggregate_dark_photon_samples.py','dark_photon_production_support.py',
                         'export_dark_photon_cross_sections.py','pythia_generation_ledger.py']:
                (tools/name).write_text('# synthetic closure fixture\n')
            (tools/'aggregate_dark_photon_samples.py').write_text(
                'def publish():\n from shift_eos_storage import endpoint\n')
            (tools/'shift_eos_storage.py').write_text('from shift_condor_native import command\n')
            (tools/'shift_condor_native.py').write_text('import aggregate_dark_photon_samples\n')
            names=aggregate_helper_names(dict(tools=str(tools)))
            self.assertIn('shift_eos_storage.py',names)
            self.assertIn('shift_condor_native.py',names)
            self.assertEqual(len(names),len(set(names)))

    def test_named_campaign_normalizes_frontend_home_paths_before_native_RPC(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);config,state=self.fixture(directory)
            home='/eos/home-j/jniedzie/shift_cmssw/darkphoton_m5_prompt/campaign'
            native='/eos/user/j/jniedzie/shift_cmssw/darkphoton_m5_prompt/campaign'
            target='/eos/cms/store/group/phys_exotica/xtracks/jeremi/shift_cmssw/publication/m5'
            config['plot_aliases']={'m5_prompt':home}
            entry=dict(name='m5_prompt',histogram_path=target+'/histograms_merged/ntuple_0.root')
            transport=MagicMock();transport.metadata.side_effect=[None,{'target':target}]
            # Exercise the real resolver: it intentionally preserves frontend
            # spelling for analysis, so merely calling it is insufficient.
            self.assertEqual(publisher.native_eos_path(home),native)
            with patch('watch_dark_photon_production.EosRpc',return_value=transport),patch('run_dark_photon_gen_stage.command') as command:
                publisher.publish_plot_aliases(config,state,dict(ready_signals=[entry]))
            self.assertEqual(state['aliases']['m5_prompt']['path'],native)
            self.assertFalse(state.get('alias_errors'))
            self.assertTrue(all('/eos/home-' not in str(call) for call in command.call_args_list))

    def test_signature_ignores_discovery_order_but_keeps_completion_identity(self):
        a,b=source('a'),source('b')
        self.assertEqual(publisher.input_signature([a,b]),publisher.input_signature([b,a]))
        before=publisher.input_signature([a,b]);b['completed_point_sha256']='e'*64
        self.assertNotEqual(before,publisher.input_signature([a,b]))

    def test_composite_reference_is_not_a_common_full_GEN_exposure(self):
        rate=dict(schema='shift-dark-photon-senscalc-dy-component-rates-v1',
            histogram_normalization=dict(reference_gen_weight_sum=2734.),
            components=[dict(full_gen_weight_sum=2634.),dict(full_gen_weight_sum=100.)])
        self.assertEqual(publisher.reference_denominator(rate),2734.)
        rate['histogram_normalization']['reference_gen_weight_sum']=2634.
        with self.assertRaisesRegex(ValueError,'preserved GEN records'):
            publisher.reference_denominator(rate)

    def test_previous_compatible_exposure_cannot_be_dropped(self):
        old=dict(original_GEN_sha256=['a','b'])
        publisher.keep_all_originals(old,['a','b','c'])
        with self.assertRaisesRegex(ValueError,'drop compatible original GEN'):
            publisher.keep_all_originals(old,['b','c'])

    def test_old_DY_or_unsupported_epsilon_never_enters_new_catalog(self):
        old=dict(rate=dict(mechanism='pure-on-shell-drell-yan',epsilon=.001,mass_gev=5))
        with self.assertRaisesRegex(ValueError,'Legacy DY'):
            publisher.components_for([old])
        old['rate'].update(dy_prescription={'synthetic':True},epsilon=.002)
        with patch.object(aggregate,'validated_dy_normalization'):
            with self.assertRaisesRegex(ValueError,'epsilon exceeds'):
                publisher.components_for([old])

    def test_extended_DY_requires_exact_versioned_authority(self):
        from dark_photon_dy_prescription import prescription_metadata
        rate = dict(mechanism='pure-on-shell-drell-yan', mass_gev=40., epsilon=.03,
            dy_prescription=prescription_metadata('msht20-nnlo-v2', 40., .03))
        publisher.validated_epsilon_domain(rate)
        rate['dy_prescription']['normalization']['factor'] *= 2
        with self.assertRaisesRegex(ValueError, 'pinned publication authority'):
            publisher.validated_epsilon_domain(rate)
        rate['dy_prescription'] = prescription_metadata('msht20-nnlo', 40., .001)
        with self.assertRaisesRegex(ValueError, 'epsilon exceeds'):
            publisher.validated_epsilon_domain(rate)

    def test_photon_extension_never_implicitly_changes_the_benchmark(self):
        from run_dark_photon_spectral_gen import photon_benchmark_epsilon
        rate = dict(mechanism='senscalc-meson-fragmentation-bremsstrahlung',
            mass_gev=.5, epsilon=.03, physical_width_authority=dict(neutral_current_width_included=False))
        with self.assertRaisesRegex(ValueError, 'Photon-benchmark epsilon'):
            publisher.validated_epsilon_domain(rate)
        rate.update(experimental_photon_benchmark=True,
            photon_benchmark_approximation=photon_benchmark_epsilon(.03, True))
        publisher.validated_epsilon_domain(rate)
        rate['physical_width_authority']['neutral_current_width_included'] = True
        with self.assertRaisesRegex(ValueError, 'pinned photon-like width'):
            publisher.validated_epsilon_domain(rate)
        rate['physical_width_authority']['neutral_current_width_included'] = False
        rate['photon_benchmark_approximation']['neutral_current_width_included'] = True
        with self.assertRaisesRegex(ValueError, 'Changed photon'):
            publisher.validated_epsilon_domain(rate)

    def test_overlap_needs_both_components_and_keeps_each_population(self):
        dy=dict(rate=dict(mechanism='pure-on-shell-drell-yan',dy_prescription={},epsilon=.001,mass_gev=1.5))
        sc=dict(rate=dict(mechanism='senscalc-meson-fragmentation-bremsstrahlung',epsilon=.001,mass_gev=1.5))
        with patch.object(aggregate,'validated_dy_normalization'),patch.object(publisher,'validated_epsilon_domain'),patch.object(aggregate,'unique_populations',side_effect=lambda rows:rows):
            self.assertIsNone(publisher.components_for([dy]))
            self.assertIsNone(publisher.components_for([sc]))
            groups=publisher.components_for([dy,sc])
        self.assertEqual(sum(map(len,groups.values())),2)

    def test_daily_session_returns_after_a_completed_poll_for_normal_DAG_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);config,state=self.fixture(directory)
            config.update(deadline_utc='2030-01-01T00:00:00+00:00',maximum_session_seconds=1)
            path=directory/'config.json';path.write_text('{}')
            result=dict(ready_signals=[],pending_signals=[],quarantined_points={})
            with patch.object(sys,'argv',['publisher','--config',str(path),'--config-sha256',publisher.sha(path)]),patch.object(publisher,'configuration',return_value=config),patch.object(publisher,'poll',return_value=result) as poll,patch.object(publisher.time,'monotonic',side_effect=[0,2]),patch.object(publisher.time,'sleep') as sleep:
                publisher.main()
            poll.assert_called_once()
            sleep.assert_not_called()

    def test_unchanged_completed_bundle_is_not_republished(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,state=self.fixture(Path(temporary));inputs=[source()]
            signature=publisher.input_signature(inputs)
            state['published']['m5_prompt']=dict(name='m5_prompt',input_signature=signature)
            with patch.object(publisher,'discovered_sources',return_value=({'m5_prompt':inputs},[])),patch.object(publisher,'build_publication') as build:
                publisher.poll(config,state)
            build.assert_not_called()

    def test_known_publication_failure_gets_one_new_preserved_attempt_then_quarantines(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,state=self.fixture(Path(temporary));inputs=[source()]
            with patch.object(publisher,'discovered_sources',return_value=({'m5_prompt':inputs},[])),patch.object(publisher,'adopt_completed',return_value=None),patch.object(publisher,'build_publication',side_effect=subprocess.CalledProcessError(51,['xrdcp'])) as build:
                publisher.poll(config,state);publisher.poll(config,state);result=publisher.poll(config,state)
            self.assertEqual(build.call_count,2)
            self.assertEqual(len(state['preserved_incomplete_attempts']),1)
            self.assertIn('m5_prompt',state['quarantined'])
            self.assertFalse(result['all_ready_compatible_increments_included'])

    def test_interrupted_complete_publication_is_adopted_without_building_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,state=self.fixture(Path(temporary));inputs=[source()]
            signature=publisher.input_signature(inputs)
            entry=dict(name='m5_prompt',input_signature=signature,original_GEN_sha256=['x'])
            state['attempts']['m5_prompt']=dict(number=0,input_signature=signature,status='started')
            with patch.object(publisher,'discovered_sources',return_value=({'m5_prompt':inputs},[])),patch.object(publisher,'adopt_completed',return_value=entry),patch.object(publisher,'build_publication') as build:
                publisher.poll(config,state)
            build.assert_not_called()
            self.assertEqual(state['published']['m5_prompt'],entry)
            self.assertIn('adopted_after_interruption_utc',state['attempts']['m5_prompt'])

    def test_scientific_failure_is_preserved_and_does_not_block_independent_point(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,state=self.fixture(Path(temporary));a,b=source('a'),source('b')
            b['point']='m10_prompt'
            ready=dict(name='m10_prompt',input_signature=publisher.input_signature([b]),original_GEN_sha256=['b'])
            def build(unused,point,*args):
                if point=='m5_prompt':raise ValueError('Original source identities overlap')
                return ready
            with patch.object(publisher,'discovered_sources',return_value=({'m5_prompt':[a],'m10_prompt':[b]},[])),patch.object(publisher,'build_publication',side_effect=build):
                publisher.poll(config,state)
            self.assertIn('m5_prompt',state['quarantined'])
            self.assertEqual(state['published']['m10_prompt'],ready)


if __name__=='__main__':unittest.main()
