# Code style: AI-code. AI-generated shared SM/BSM admission regression tests.
import copy
import gzip
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from test_shift_material_preselection import event, inputs
import shift_production_selection as common
import shift_material_preselection as admission
import prepare_shift_detector_sample as preparer
import shift_gen_material_features as features
from test_shift_gen_material_features import particle


def bundle(rows):
    census, authority = inputs(rows)
    return dict(schema='shift-production-material-inputs-v1', cohort_id='fixed-full-census',
                selection_seed='independent-material-draw', features=census, authority=authority)


def descriptor(rows, stratum='qcd_20toinf'):
    return dict(gen='/synthetic/completed.root', gen_sha256='b'*64, gen_bytes=12,
                receipt='/synthetic/receipt.json', receipt_sha256='d'*64,
                stratum=stratum, event_ids=[row['event_id'] for row in rows],
                weights=[row['native_weight'] for row in rows],
                normalization_record=dict(full_trials=2000), output_base='/eos/original')


class ProductionSelectionTests(unittest.TestCase):
    def test_default_without_override_is_shared_and_reproducible(self):
        census, authority = inputs([event()])
        first = admission.prepare_selection(census, authority)
        self.assertEqual(first, admission.prepare_selection(census, authority))
        self.assertTrue(first['policy']['require_opposite_sign'])
        self.assertEqual(first['policy']['unknown_probability'], 1.)
        self.assertEqual(first['policy']['two_endcap_pointing']['schema'], 'shift-two-endcap-pointing-v3')
        self.assertEqual(first['policy'], common.default_policy(
            first['policy']['cohort_id'], first['policy']['selection_seed']))

    def test_unknown_model_is_not_a_hadron_or_given_guessed_absorption(self):
        for pdg in (32, 1000022, 4900111, 9900015):
            self.assertFalse(features.hadron(pdg))
        graph = [particle(1, 9900015), particle(2, 13, [1], status=1),
                 particle(3, -13, [1], status=1)]
        row = features.event_features(graph, [1, 1, 1], 1., [1.])
        row.update(source_file_sha256='b'*64, original_source_sha256='a'*64,
                   source_id='hard-source-1')
        self.assertEqual(row['parent_flights'], [])
        self.assertEqual(row['unknown_transport_ancestor_barcodes'], [1])
        ledger = common.prepare_default_selection(bundle([row]))
        self.assertEqual(ledger['events'][0]['sampling_probability'], 1.)

    def test_same_signed_bad_direction_controls_keep_support_and_native_weights(self):
        rows = [event(i+1, native=(-2. if i % 2 else 1.)) for i in range(100)]
        # Same-sign muons get the control probability, not zero probability.
        ledger = common.prepare_default_selection(bundle(rows))
        for family in ('qcd_20toinf', 'dy_10to20', 'jpsi_5to10', 'darkphoton_new_model'):
            source = descriptor(rows, family)
            before = copy.deepcopy(source)
            selected = common.bind_source(source, ledger)
            self.assertEqual(source, before)
            self.assertEqual(selected['event_ids'], source['event_ids'])
            self.assertEqual(selected['weights'], source['weights'])
            self.assertGreater(len(selected['selected_indices']), 0)
            self.assertLess(len(selected['selected_indices']), len(rows))
            for row in ledger['events']:
                self.assertGreaterEqual(row['sampling_probability'], .1)
                self.assertEqual(row['sampling_gen_weight'] * row['sampling_probability'], row['native_weight'])
            with self.assertRaisesRegex(ValueError, 'reopen'):
                common.bind_source(selected, ledger)

    def test_priority_uses_total_momentum_pointing_and_material_without_pt_cut(self):
        row = event()
        for number, muon in enumerate(row['muons']):
            muon.update(pdg=13 if number == 0 else -13, birth_cm=[500., 0., 14800.],
                        momentum_GeV=[0., 0., -40.], pt_GeV=0.,
                        material_to_CMS_entry=dict(complete=True, column_density_g_cm2=100.))
        def probability(value):
            return common.prepare_default_selection(bundle([value]))['events'][0]['sampling_probability']
        reference = probability(row)
        changed_pt = copy.deepcopy(row)
        for muon in changed_pt['muons']:
            muon['pt_GeV'] = 1.e9
        self.assertEqual(probability(changed_pt), reference)
        low_momentum = copy.deepcopy(row)
        for muon in low_momentum['muons']:
            muon.update(p_GeV=1., momentum_GeV=[0., 0., -1.])
        thick_material = copy.deepcopy(row)
        thick_material['parent_flights'][0]['material']['interaction_depth_proxy'] = 20.
        misses_cms = copy.deepcopy(row)
        for muon in misses_cms['muons']:
            muon['birth_cm'][0] = 10000.
        for less_promising in (low_momentum, thick_material, misses_cms):
            self.assertLess(probability(less_promising), reference)
            self.assertGreaterEqual(probability(less_promising), .1)

    def test_missing_graphs_changed_weights_and_separate_proposal_factor_fail(self):
        rows = [event()]
        ledger = common.prepare_default_selection(bundle(rows))
        for source in (dict(descriptor(rows), event_ids=[]),
                       dict(descriptor(rows), gen_sha256='e'*64),
                       dict(descriptor(rows), weights=[99.])):
            with self.assertRaises(ValueError):
                common.bind_source(source, ledger)
        changed = copy.deepcopy(ledger)
        changed['events'][0]['proposal_correction'] = 2.
        with self.assertRaisesRegex(ValueError, 'native proposal weights'):
            common.bind_source(descriptor(rows), changed)

    def test_new_inventory_defaults_to_material_for_sm_and_bsm_and_preserves_exposure(self):
        for family in ('qcd_20toinf', 'darkphoton_new_model'):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                parent = root/'GEN'; parent.mkdir()
                (parent/'sources').mkdir(); (parent/'templates').mkdir()
                rows = [event(i+1) for i in range(200)]
                source = descriptor(rows, family)
                (parent/'sources/source00000.json').write_text(json.dumps(source))
                templates = {}
                for stage in range(1, 5):
                    path = parent/'templates'/f'step{stage}.py'
                    path.write_text(f'# unchanged reconstruction stage {stage}\n')
                    templates[str(stage)] = dict(sha256=preparer.sha256(path))
                manifest = dict(sources=[dict(index=0, gen=source['gen'], stratum=family, events=len(rows))],
                    strata={family: len(rows)}, templates=templates, full_GEN_exposure=dict(attempted=2000),
                    physics_valid=False, normalization_ready=False)
                (parent/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'defaults to material'):
                    preparer.prepare(parent, root/'missing', '/eos/synthetic/child', 1, 2, 'unused')
                self.assertFalse((root/'missing').exists())
                (parent/'material_selection_inputs.json').write_text(json.dumps(bundle(rows)))
                frozen = {path: path.read_bytes() for path in parent.rglob('*') if path.is_file()}
                first = preparer.prepare(parent, root/'first', '/eos/synthetic/child', 1, 2, 'batch-a')
                second = preparer.prepare(parent, root/'second', '/eos/synthetic/child', 100, 7, 'batch-b')
                a = json.loads((root/'first/sources/source00000.json').read_text())
                b = json.loads((root/'second/sources/source00000.json').read_text())
                self.assertEqual(a['selected_indices'], b['selected_indices'])
                self.assertEqual(a['sampling_probabilities'], b['sampling_probabilities'])
                self.assertEqual(a['event_ids'], source['event_ids'])
                self.assertEqual(a['normalization_record'], source['normalization_record'])
                self.assertEqual(first['selected_events'], second['selected_events'])
                output_manifest = json.loads((root/'first/manifest.json').read_text())
                self.assertEqual(output_manifest['full_GEN_exposure'], manifest['full_GEN_exposure'])
                with gzip.open(root/'first/sampling_events.jsonl.gz', 'rt') as stream:
                    for line in stream:
                        row = json.loads(line)
                        self.assertEqual(row['corrected_weight'] * row['sampling_probability'], row['native_weight'])
                for path, original in frozen.items():
                    self.assertEqual(path.read_bytes(), original)
                for stage in range(1, 5):
                    self.assertEqual((root/'first/templates'/f'step{stage}.py').read_bytes(),
                                     (parent/'templates'/f'step{stage}.py').read_bytes())

    def test_sim_settings_preserve_bsm_physics_list_and_reco_modules(self):
        ledger = common.prepare_default_selection(bundle([event()]))
        contract = common.bind_source(descriptor([event()]), ledger)['production_selection']
        generator = SimpleNamespace(AlwaysTrackDecaysTo=[32])
        physics = SimpleNamespace(type='model-specific-physics')
        process = SimpleNamespace(g4SimHits=SimpleNamespace(Generator=generator, Physics=physics),
                                  reconstruction=object())
        before = process.reconstruction
        cms = SimpleNamespace(untracked=SimpleNamespace(bool=bool, double=float))
        parameter_set = SimpleNamespace(Config=cms)
        with patch.dict(sys.modules, {'FWCore': SimpleNamespace(ParameterSet=parameter_set), 'FWCore.ParameterSet': parameter_set,
                                      'FWCore.ParameterSet.Config': cms}):
            common.configure_transport(process, contract)
        self.assertTrue(generator.TrackAllHadronDecays)
        self.assertEqual(generator.MaxUntrackedHadronFlightMm, 1.e-5)
        self.assertEqual(generator.AlwaysTrackDecaysTo, [32])
        self.assertEqual(physics.type, 'model-specific-physics')
        self.assertIs(process.reconstruction, before)

    def test_bsm_freeze_inventory_includes_shared_dependencies(self):
        from prepare_dark_photon_grid import SOURCES
        from prepare_dark_photon_meson_production import TOOLS
        for name in common.DEPENDENCIES:
            self.assertIn(name, SOURCES)
            self.assertIn(name, TOOLS)

    def test_legacy_bsm_publisher_cannot_silently_drop_sampling_weights(self):
        rows = [event(i+1) for i in range(30)]
        ledger = common.prepare_default_selection(bundle(rows))
        source = common.bind_source(descriptor(rows), ledger)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = dict(points=[dict(point='signal', gen_directory=str(directory))])
            inventory = dict(points=dict(signal=dict(gen_sha256='b'*64,
                event_ids=descriptor(rows)['event_ids'])))
            with self.assertRaisesRegex(ValueError, 'requires the shared prepared'):
                common.guard_legacy_bsm_publication(plan, inventory)
            path = directory/'material_selected_source.json'
            path.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, 'legacy BSM publisher assumes'):
                common.guard_legacy_bsm_publication(plan, inventory)
            # Even a lucky draw retaining every original cannot hide p<1.
            source['selected_indices'] = list(range(30))
            source['sampling_probabilities'] = [.1] * 30
            path.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, 'legacy BSM publisher assumes'):
                common.guard_legacy_bsm_publication(plan, inventory)
            source['sampling_probabilities'] = [1.] * 30
            path.write_text(json.dumps(source))
            common.guard_legacy_bsm_publication(plan, inventory)


if __name__ == '__main__':
    unittest.main()
