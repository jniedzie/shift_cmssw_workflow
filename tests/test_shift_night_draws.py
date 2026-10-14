# Code style: AI-code. AI-generated full-support night-draw weighting regression tests.
import itertools
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_shift_night_draws import uniform_integer, union_probability


class NightDrawMathTest(unittest.TestCase):
    def test_exact_union_enumeration_preserves_signed_original_weights(self):
        for native, p0, p1 in [(2.5, .1, .0001), (-.25, .7, .2), (1e-100, .01, 1.)]:
            probabilities = [p0, p1, .01, .01]
            pi = union_probability(p0, p1, 2)
            expectation = 0.
            for selected in itertools.product((False, True), repeat=4):
                probability = math.prod(p if chosen else 1-p for p, chosen in zip(probabilities, selected))
                expectation += probability * (native/pi if any(selected) else 0.)
            self.assertTrue(math.isclose(expectation, native, rel_tol=2e-13, abs_tol=0))

    def test_certainty_tail_keeps_native_weight(self):
        for epoch in range(1, 15):
            self.assertEqual(union_probability(.00003, 1., epoch), 1.)

    def test_complete_epochs_change_every_retained_event_weight(self):
        values = [union_probability(.1, .0001, epoch) for epoch in range(1, 15)]
        self.assertTrue(all(first < second for first, second in zip(values, values[1:])))
        self.assertTrue(all(0 < value <= 1 for value in values))

    def test_hash_is_keyed_by_original_and_independent_seed(self):
        source = dict(stratum='qcd_0to1', original_gen_sha256='c'*64, index=3)
        first = uniform_integer('a'*64, source, 2, [9, 1, 3])
        self.assertEqual(first, uniform_integer('a'*64, source, 2, [9, 1, 3]))
        self.assertNotEqual(first, uniform_integer('b'*64, source, 2, [9, 1, 3]))
        self.assertNotEqual(first, uniform_integer('a'*64, source, 3, [9, 1, 4]))
        self.assertTrue(0 <= first < 1 << 256)


if __name__ == '__main__':
    unittest.main()
