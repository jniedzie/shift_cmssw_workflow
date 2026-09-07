import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_collection_sensitivity import union_bounds, calculate


class BoundsTest(unittest.TestCase):
    def test_extreme_correlations(self):
        # Same events reconstructed in both readouts versus disjoint successes.
        self.assertEqual(union_bounds([.1, .2]), (.2, .30000000000000004))
        self.assertEqual(union_bounds([]), (0, 0))
        self.assertEqual(union_bounds([.7, .8]), (.8, 1))
        with self.assertRaises(ValueError):
            union_bounds([float('nan')])

    def test_orbit_wrap_and_timing_sign(self):
        counts = dict(format='shift-delay-efficiencies-v1', points=[
            dict(delay_ns=d, sources=['same'], muon=dict(inclusive=int(d == -25), denominator=1),
                 dimuon=dict(inclusive=int(d == -25), denominator=1)) for d in range(-175, 176, 25)])
        mask = dict(schema='cms-lpc-ip5-bunch-mask', orbit_slots=3564,
                    beam2_filled_bx_slots=[1, 3564], beam1_filled_bx_slots=[1], colliding_ip5_bx_slots=[1])
        rows, slots = calculate(counts, mask, phases=(0,), qs=(.1,))
        # Parent 3564 sees collision 1 one BX later: response must be -25 ns.
        self.assertEqual(rows[0]['lower'], .05)
        self.assertEqual(rows[0]['upper'], .05)
        counts['points'].pop()
        with self.assertRaisesRegex(ValueError, 'missing exact'):
            calculate(counts, mask, phases=(0,), qs=(.1,))
