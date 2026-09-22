import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from merge_sampling_replay import normalization, selected_weights


class NormalizationTest(unittest.TestCase):
    def setUp(self):
        self.parent = dict(contract=dict(sample='qcdmu', attempted_events=5000),
                           generation=dict(attempted_events=5000, accepted_events=4800,
                                           runs=[dict(internal_xsec_pb=100000., error_pb=100.)]))
        self.ledger = dict(framework_attempts=5000, upstream_saved_events=4800, selected_events=2,
                           rows=[dict(id=[1, 1, 1], selected=True, probability=1., inverse_probability=1.),
                                 dict(id=[1, 1, 2], selected=True, probability=.1, inverse_probability=10.)])
        self.log = '\n'.join(['Before Filter: total cross section = 1.000e+05 +- 100 pb',
                              'After filter: final cross section = 9.600e+04 +- 100 pb',
                              'Filter efficiency (taking into account weights)= (4800) / (5000)',
                              'Filter efficiency (event-level)= (4800) / (5000)'])

    def test_parent_denominator_and_inverse_probability(self):
        norm = normalization(self.parent, self.ledger, self.log)
        rows = selected_weights(self.ledger, {(1, 1, 1), (1, 1, 2)}, norm)
        self.assertEqual(norm['after_filter_pb'], 96000.)
        self.assertEqual([r['event_weight_pb'] for r in rows], [20., 200.])

    def test_reject_prefix(self):
        self.ledger['framework_attempts'] = 500
        with self.assertRaises(ValueError):
            normalization(self.parent, self.ledger, self.log)

    def test_reject_wrong_log_cross_section_or_filter(self):
        for bad_log in [self.log.replace('9.600e+04', '9.500e+04'), self.log.replace('(4800)', '(4700)')]:
            with self.assertRaises(ValueError):
                normalization(self.parent, self.ledger, bad_log)

    def test_reject_bad_sigma(self):
        for bad in [0., -1., float('nan'), float('inf')]:
            self.parent['generation']['runs'][0]['internal_xsec_pb'] = bad
            with self.assertRaises(ValueError):
                normalization(self.parent, self.ledger, self.log)

    def test_reject_missing_or_extra_weight_identity(self):
        norm = normalization(self.parent, self.ledger, self.log)
        for identities in [{(1, 1, 1)}, {(1, 1, 1), (1, 1, 2), (1, 1, 3)}]:
            with self.assertRaises(ValueError):
                selected_weights(self.ledger, identities, norm)

    def test_reject_bad_sampling_weight(self):
        norm = normalization(self.parent, self.ledger, self.log)
        for probability, inverse in [(0., 10.), (.1, 1.), (float('nan'), 1.), (1.1, 1.)]:
            ledger = copy.deepcopy(self.ledger)
            ledger['rows'][1].update(probability=probability, inverse_probability=inverse)
            with self.assertRaises(ValueError):
                selected_weights(ledger, {(1, 1, 1), (1, 1, 2)}, norm)


if __name__ == '__main__':
    unittest.main()
