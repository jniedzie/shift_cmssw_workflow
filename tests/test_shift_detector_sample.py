import gzip
import hashlib
import importlib.util
import itertools
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'prepare_shift_detector_sample.py'
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location('detector_sample', SCRIPT)
sample = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sample)


class DetectorSampleTest(unittest.TestCase):
    def test_scale_saturation_and_tiny_weight_full_support(self):
        weights = [8.0, 1.0, .5, 1e-56]
        scale, target = sample.fit_scale(weights, 2)
        probabilities = [sample.inclusion_probability(w, scale)[1] for w in weights]
        self.assertAlmostEqual(math.fsum(probabilities), target)
        self.assertEqual(probabilities[0], 1)
        threshold, actual = sample.inclusion_probability(1e-56, scale)
        self.assertGreater(threshold, 0)
        self.assertGreater(actual, 0)
        self.assertLess(actual, 2**-64)
        with self.assertRaises(ValueError):
            sample.probability_threshold(1e-100)

    def test_inverse_probability_expectation_and_variance_all_outcomes(self):
        weights = [1., 3., 5.]
        observables = [2., -1., .4]
        probabilities = [.2, .5, 1.]
        expected, second, estimated_variance = 0., 0., 0.
        for selected in itertools.product((False, True), repeat=3):
            chance = math.prod(p if retain else 1-p for p, retain in zip(probabilities, selected))
            total = math.fsum(w*f/p for w, f, p, retain in zip(weights, observables, probabilities, selected) if retain)
            variance = math.fsum((w*f/p)**2*(1-p) for w, f, p, retain in zip(weights, observables, probabilities, selected) if retain)
            expected += chance * total
            second += chance * total * total
            estimated_variance += chance * variance
        truth = math.fsum(w*f for w, f in zip(weights, observables))
        design_variance = math.fsum(w*w*f*f*(1-p)/p for w, f, p in zip(weights, observables, probabilities))
        self.assertAlmostEqual(expected, truth)
        self.assertAlmostEqual(second - expected*expected, design_variance)
        self.assertAlmostEqual(estimated_variance, design_variance)

    def test_saturated_dominant_weight_preserves_small_active_tail(self):
        for weights in ([1e20, 1., 1., 1.], [1e20, 1e-20, 1e-20, 1e-20]):
            with self.subTest(weights=weights):
                scale, target = sample.fit_scale(weights, 2)
                probabilities = [sample.inclusion_probability(w, scale)[1] for w in weights]
                self.assertEqual(probabilities[0], 1)
                for probability in probabilities[1:]:
                    self.assertAlmostEqual(probability, 1/3)
                self.assertAlmostEqual(math.fsum(probabilities), target)

    def test_deterministic_independent_keys(self):
        threshold, _ = sample.probability_threshold(.5)
        first = [sample.select_event('frozen-seed', 'bin', 'a'*64, [1, 1, i+1], i, threshold) for i in range(50)]
        second = [sample.select_event('frozen-seed', 'bin', 'a'*64, [1, 1, i+1], i, threshold) for i in range(50)]
        other = [sample.select_event('other-seed', 'bin', 'a'*64, [1, 1, i+1], i, threshold) for i in range(50)]
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(any(first))
        self.assertFalse(all(first))

    def test_gapped_raw_indices_partition_selected_offsets_exactly(self):
        raw = [0, 4, 5, 18, 90, 101, 200]
        jobs = sample.partition_jobs([dict(index=23, events=len(raw))], 3)
        replayed = [raw[offset+i] for _, offset, count, _ in jobs for i in range(count)]
        self.assertEqual(replayed, raw)
        self.assertEqual([count for _, _, count, _ in jobs], [3, 3, 1])
        self.assertEqual([job for _, _, _, job in jobs], [0, 1, 2])

    def test_preparation_preserves_gen_and_freezes_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); parent = root/'full'; parent.mkdir()
            (parent/'sources').mkdir(); (parent/'templates').mkdir()
            templates = {}
            for stage in range(1,5):
                path=parent/'templates'/f'step{stage}.py'; path.write_text(f'# synthetic stage {stage}\n')
                templates[str(stage)] = dict(sha256=sample.sha256(path), source='synthetic')
            descriptors = []
            for index, stratum in [(4,'ordinary'),(9,'weighted')]:
                descriptor=dict(receipt=f'/synthetic/receipt{index}', receipt_sha256='b'*64,
                    stratum=stratum, gen=f'/synthetic/gen{index}', gen_sha256='a'*64,
                    gen_bytes=100, event_ids=[[index+1,1,i+1] for i in range(20)],
                    output_base='/eos/synthetic/full/'+stratum, normalization_record=None)
                if stratum=='weighted':descriptor['weights']=[1+i/10 for i in range(20)]
                path=parent/'sources'/f'source{index:05d}.json';path.write_text(json.dumps(descriptor))
                descriptors.append(descriptor)
            manifest=dict(schema='shift-gen-to-nano-plan-v1', templates=templates,
                sources=[dict(index=i, gen=d['gen'], stratum=d['stratum'], events=20) for i,d in zip((4,9),descriptors)],
                strata=dict(ordinary=20, weighted=20), physics_valid=False, normalization_ready=False,
                approval_basis='Synthetic parent authorization evidence')
            manifest_path=parent/'manifest.json';manifest_path.write_text(json.dumps(manifest))
            originals={p: p.read_bytes() for p in parent.rglob('*') if p.is_file()}
            out=root/'sample'; plan=sample.prepare(parent,out,'/eos/synthetic/sample',10,3,'fixed')
            self.assertEqual(plan['expected_events'],20)
            self.assertEqual(plan['parent_manifest_sha256'], hashlib.sha256(originals[manifest_path]).hexdigest())
            child_manifest=json.loads((out/'manifest.json').read_text())
            self.assertNotIn('approval_basis',child_manifest)
            self.assertEqual(child_manifest['parent_approval_basis'],manifest['approval_basis'])
            self.assertIn('preparation_basis',child_manifest)
            with gzip.open(out/'sampling_events.jsonl.gz','rt') as ledger:
                rows=[json.loads(line) for line in ledger]
            self.assertEqual(len(rows),plan['selected_events'])
            self.assertEqual(len({tuple(r['event_id']) for r in rows}),len(rows))
            for row in rows:
                self.assertAlmostEqual(row['corrected_weight']*row['sampling_probability'],row['native_weight'])
            for index, original in zip((4,9),descriptors):
                produced=json.loads((out/'sources'/f'source{index:05d}.json').read_text())
                for key in ('gen','gen_sha256','event_ids','receipt','receipt_sha256','weights'):
                    self.assertEqual(produced.get(key),original.get(key))
                self.assertEqual(produced['selected_indices'],sorted(set(produced['selected_indices'])))
            for path, original in originals.items():self.assertEqual(path.read_bytes(),original)
            repeat=root/'repeat'; sample.prepare(parent,repeat,'/eos/synthetic/sample',10,3,'fixed')
            self.assertEqual((out/'sampling_events.jsonl.gz').read_bytes(),(repeat/'sampling_events.jsonl.gz').read_bytes())
            with self.assertRaises(ValueError):sample.prepare(parent,out,'/eos/synthetic/sample',10,3,'fixed')

    def test_qcd_subset_uses_process_bin_storage_and_complete_decays(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); parent = root / 'full'; parent.mkdir()
            (parent / 'sources').mkdir(); (parent / 'templates').mkdir()
            templates = {}
            for stage in range(1, 5):
                path = parent / 'templates' / f'step{stage}.py'
                path.write_text(f'# stage {stage}\n')
                templates[str(stage)] = dict(sha256=sample.sha256(path), source='synthetic')
            sources = []
            for index, stratum in enumerate(('qcd_0to1', 'dy_2to5')):
                descriptor = dict(receipt=f'/synthetic/receipt{index}', receipt_sha256='b' * 64,
                    stratum=stratum, gen=f'/synthetic/gen{index}', gen_sha256=('a' if index == 0 else 'c') * 64,
                    gen_bytes=100, event_ids=[[index + 1, 1, event + 1] for event in range(20)],
                    output_base='/eos/synthetic/full/' + stratum, normalization_record=None)
                (parent / 'sources' / f'source{index:05d}.json').write_text(json.dumps(descriptor))
                sources.append(dict(index=index, gen=descriptor['gen'], stratum=stratum, events=20))
            (parent / 'manifest.json').write_text(json.dumps(dict(
                schema='shift-gen-to-nano-plan-v1', templates=templates, sources=sources,
                strata={'qcd_0to1': 20, 'dy_2to5': 20}, physics_valid=False,
                normalization_ready=False)))
            output = root / 'qcd'
            campaign = '/eos/user/j/test/shift_cmssw/qcd_pThat0to1GeV/20261012-qcd-tail-v1'
            locations = {'/synthetic/gen0': '/eos/user/j/test/GEN/part0.root',
                         '/synthetic/receipt0': '/eos/user/j/test/metadata/receipt0.json'}
            originals = {p: p.read_bytes() for p in parent.rglob('*') if p.is_file()}
            with patch.object(sample.shift_storage_paths, 'campaign_directory',
                              return_value=Path(campaign)) as storage, \
                 patch.object(sample.shift_storage_paths, 'resolve_storage_path',
                              side_effect=locations.__getitem__):
                plan = sample.prepare(parent, output, '/eos/user/j/test/control', 7, 3, 'qcd-subset',
                    include_strata={'qcd_0to1'}, target_events_by_stratum={'qcd_0to1': 9},
                    process_bin_campaign='20261012-qcd-tail-v1',
                    eos_data_root='/eos/user/j/test/shift_cmssw', complete_muon_decays=True,
                    resolve_input_paths=True)
            storage.assert_called_once_with('/eos/user/j/test/shift_cmssw', 'qcd', '0to1',
                                            '20261012-qcd-tail-v1')
            manifest = json.loads((output / 'manifest.json').read_text())
            descriptor = json.loads((output / 'sources/source00000.json').read_text())
            self.assertEqual(plan['included_strata'], ['qcd_0to1'])
            self.assertEqual(plan['target_expected_events_by_stratum'], {'qcd_0to1': 9})
            self.assertAlmostEqual(plan['expected_events'], 9)
            self.assertEqual(set(manifest['strata']), {'qcd_0to1'})
            self.assertEqual(manifest['storage_contract'], 'shift-process-bin-detector-v1')
            self.assertTrue(manifest['complete_muon_decays'])
            self.assertEqual(descriptor['nano_base'], campaign + '/nanoAOD')
            self.assertEqual(descriptor['metadata_base'], campaign + '/metadata/detector_jobs')
            self.assertTrue(descriptor['complete_muon_decays'])
            self.assertEqual(descriptor['logical_gen'], '/synthetic/gen0')
            self.assertEqual(descriptor['logical_receipt'], '/synthetic/receipt0')
            self.assertEqual(descriptor['gen'], locations[descriptor['logical_gen']])
            self.assertEqual(descriptor['receipt'], locations[descriptor['logical_receipt']])
            self.assertTrue(descriptor['gen_path_resolved'])
            self.assertTrue(descriptor['receipt_path_resolved'])
            self.assertEqual(manifest['sources'][0]['gen'], descriptor['gen'])
            self.assertEqual({p: p.read_bytes() for p in parent.rglob('*') if p.is_file()}, originals)
            self.assertNotIn('source00001.json', {path.name for path in (output / 'sources').iterdir()})

    def test_invalid_sample_options_fail_before_writing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / 'full'
            parent.mkdir()
            (parent / 'manifest.json').write_text(json.dumps(
                dict(strata={'qcd_0to1': 20}, sources=[])))
            invalid = [{'target_events_by_stratum': value} for value in
                       ([], [('qcd_0to1', 5)], {'dy_2to5': 5}, {'qcd_0to1': True})]
            invalid.extend(dict(process_bin_campaign='20261012-test', eos_data_root=value)
                           for value in ('/tmp/data', '/eos/home-j/test', '/eos/user/j/test/../data'))
            for options in invalid:
                with self.subTest(options=options):
                    output = Path(directory) / 'output'
                    with self.assertRaises(ValueError):
                        sample.prepare(parent, output, '/eos/user/j/test/control', 7, 3,
                                       'sample', **options)
                    self.assertFalse(output.exists())


if __name__=='__main__':
    unittest.main()
