# Code style: AI-code. AI-generated tests of actual SM dimuon response labels.
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_sm_response_labels import (PAIR_FIELDS, SAMPLING_BRANCHES, event_labels,
    expected_index, pair_categories, pair_histograms, read_nano)


def pair(**updates):
    value = dict(mass=30., pt=15., vx=0., vy=0., vz=14800., dca=5.,
                 dcaValid=1, isOS=1, topologyMin=2, topologyMax=2,
                 muonIdx1=0, muonIdx2=1)
    value.update(updates)
    return value


class ResponseLabelTest(unittest.TestCase):
    def test_both_both_is_topology_only(self):
        actual = pair_categories(pair(isOS=0, dcaValid=0, dca=1000.))
        self.assertTrue(actual['both_both'])
        self.assertFalse(actual['both_both_opposite_sign'])
        self.assertFalse(actual['good'])

    def test_good_needs_charge_valid_dca_and_inclusive_boundaries(self):
        for rejected in [pair(isOS=0), pair(dcaValid=0), pair(dca=-1.),
                         pair(dca=50.001), pair(dca=math.nan), pair(dca=None)]:
            self.assertFalse(pair_categories(rejected)['good'])
        for distance in (0., 50.):
            self.assertTrue(pair_categories(pair(dca=distance))['good'])
        self.assertTrue(pair_categories(pair(topologyMin=0))['good'])
        self.assertFalse(pair_categories(pair(topologyMin=0))['both_both_good'])

    def test_empty_event_is_a_measured_negative(self):
        actual = event_labels([])
        self.assertFalse(any(actual['event_pass'].values()))
        self.assertEqual(sum(actual['pair_counts'].values()), 0)

    def test_multiple_pairs_are_one_positive_original(self):
        actual = event_labels([pair(), pair(mass=70., pt=30.)])
        self.assertEqual(actual['pair_counts']['both_both'], 2)
        self.assertIs(actual['event_pass']['both_both'], True)
        self.assertEqual(actual['tail_counts']['both_both']['mass_gt_20'], 2)
        self.assertEqual(actual['tail_counts']['both_both']['mass_gt_50'], 1)

    def test_tail_thresholds_are_strict_and_nonfinite_values_remain_diagnostic(self):
        actual = event_labels([pair(mass=20., pt=10.), pair(mass=None, pt=math.nan)])
        self.assertEqual(actual['tail_counts']['both_both']['mass_gt_20'], 0)
        self.assertEqual(actual['tail_counts']['both_both']['pt_gt_10'], 0)
        self.assertEqual(actual['pair_counts']['both_both'], 2)

    def test_histograms_keep_flow_bins_nonfinite_and_event_pair_contributions(self):
        pairs = [pair(mass=-1.), pair(mass=0.), pair(mass=10.), pair(mass=20.), pair(mass=None)]
        actual = pair_histograms(pairs, {'mass': [0., 10., 20.]})['both_both']['mass']
        self.assertEqual(actual['counts'], [1, 1, 1, 1])
        self.assertEqual(actual['nonfinite'], 1)

    def test_ambiguous_identity_across_gen_parents_is_rejected(self):
        one = dict(gen_sha256='a'*64, event_id=[1, 1, 1], native_weight=2., sampling_probability=.1)
        two = dict(one, gen_sha256='b'*64)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            expected_index([one, two])
        with self.assertRaisesRegex(ValueError, 'positive frozen'):
            expected_index([dict(one, sampling_probability=0.)])


class NanoReaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import uproot, awkward, numpy
        except ImportError:
            raise unittest.SkipTest('Nano integration tests require stock CMSSW uproot/awkward/numpy')

    def nano(self, path, weight_dtype='float64'):
        import awkward as ak
        import numpy as np
        import uproot
        pairs = [pair(), pair(isOS=0, dcaValid=0), pair(mass=70., pt=30.)]
        arrays = dict(run=np.array([1, 1], dtype=np.uint32),
                      luminosityBlock=np.array([1, 1], dtype=np.uint32),
                      event=np.array([1, 2], dtype=np.uint64),
                      nShiftMuon=np.array([3, 0], dtype=np.uint32),
                      nShiftDimuonVertex=np.array([3, 0], dtype=np.uint32))
        for name, values in zip(SAMPLING_BRANCHES, ([2., -3.], [.1, 1.], [10., 1.], [20., -3.])):
            arrays[name] = np.array(values, dtype=weight_dtype)
        for name in PAIR_FIELDS:
            arrays['ShiftDimuonVertex_'+name] = ak.Array([[p[name] for p in pairs], []])
        with uproot.recreate(path) as root:
            root.mktree('Events', arrays)

    def expected(self):
        return [dict(gen_sha256='a'*64, event_id=[1, 1, 1], native_weight=2.,
                     sampling_probability=.1, source_id='one'),
                dict(gen_sha256='b'*64, event_id=[1, 1, 2], native_weight=-3.,
                     sampling_probability=1., source_id='two')]

    def test_native_nano_reader_preserves_weights_negative_events_and_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'nano.root'
            self.nano(path)
            rows = read_nano(path, self.expected())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['pair_counts']['both_both'], 3)
        self.assertEqual(rows[0]['pair_counts']['both_both_good'], 2)
        self.assertEqual(rows[0]['sampling_gen_weight'], 20.)
        self.assertEqual(rows[1]['sampling_gen_weight'], -3.)
        self.assertFalse(rows[1]['event_pass']['inclusive'])

    def test_reader_rejects_changed_weights_and_missing_expected_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'nano.root'
            self.nano(path)
            changed = self.expected()
            changed[0]['native_weight'] = 99.
            with self.assertRaisesRegex(ValueError, 'weight/probability changed'):
                read_nano(path, changed)
            extra = self.expected()+[dict(self.expected()[0], event_id=[1, 1, 3])]
            with self.assertRaisesRegex(ValueError, 'missing results'):
                read_nano(path, extra)

    def test_reader_rejects_float_sampling_fields_and_wrong_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'nano.root'
            self.nano(path, 'float32')
            with self.assertRaisesRegex(ValueError, 'must remain Double'):
                read_nano(path, self.expected())
            with self.assertRaisesRegex(ValueError, 'SHA-256 differs'):
                read_nano(path, self.expected(), '0'*64)


if __name__ == '__main__':
    unittest.main()
