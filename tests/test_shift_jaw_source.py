# Code style: AI-code. AI-generated first-interaction source-model tests.
import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from shift_jaw_source import first_interaction, source_position


def path():
    return dict(id="left", mixture_probability=.5, segments=[
        dict(start_cm=[0., 0., 30.], end_cm=[0., 0., 25.],
             interaction_length_cm=5., material="first"),
        dict(start_cm=[0., 0., 25.], end_cm=[0., 0., 15.],
             interaction_length_cm=20., material="second")])


def profile():
    left, right = path(), path()
    right["id"] = "right"
    for segment in right["segments"]:
        segment["start_cm"][0] = 4.
        segment["end_cm"][0] = 4.
    return dict(schema="shift-assumed-jaw-depth-profile-v1",
                conditional_on_jaw_collision=True, paths=[left, right])


class JawSourceTest(unittest.TestCase):
    def test_piecewise_material_depth_matches_analytic_first_collision(self):
        # Seven cm into the path means five cm of material1, then two of material2.
        optical_depth = 5./5.+2./20.
        probability = (1.-math.exp(-optical_depth))/(1.-math.exp(-1.5))
        result = first_interaction(path(), probability)
        self.assertAlmostEqual(result["incoming_path_distance_cm"], 7.)
        self.assertAlmostEqual(result["target_position_cm"][2], 23.)
        self.assertEqual(result["material"], "second")
        self.assertAlmostEqual(result["reference_collision_probability"], 1.-math.exp(-1.5))

    def test_front_heavy_quantiles_and_finite_jaw_conditioning(self):
        single = dict(segments=[dict(start_cm=[0., 0., 100.], end_cm=[0., 0., 0.],
                                   interaction_length_cm=10., material="test")])
        previous = 0.
        for quantile in (.1, .5, .9, .99):
            result = first_interaction(single, quantile)
            distance = result["incoming_path_distance_cm"]
            self.assertGreater(distance, previous)
            self.assertAlmostEqual((1.-math.exp(-distance/10.))/(1.-math.exp(-10.)), quantile)
            previous = distance
        self.assertLess(first_interaction(single, .5)["incoming_path_distance_cm"], 10.)
        self.assertLess(first_interaction(single, .9)["incoming_path_distance_cm"], 24.)

    def test_identity_determinism_independent_draws_and_no_material_weight(self):
        expected = source_position(profile(), "a"*64, [1, 2, 3], "seed")
        self.assertEqual(expected, source_position(profile(), "a"*64, [1, 2, 3], "seed"))
        for digest, identity, seed in (("b"*64, [1, 2, 3], "seed"),
                                       ("a"*64, [1, 2, 4], "seed"),
                                       ("a"*64, [1, 2, 3], "new")):
            self.assertNotEqual(expected["depth_uniform"],
                source_position(profile(), digest, identity, seed)["depth_uniform"])
        self.assertNotEqual(expected["jaw_uniform"], expected["depth_uniform"])
        self.assertFalse(expected["material_or_depth_weight_applied"])
        self.assertFalse(expected["normalization_ready"])

    def test_declared_two_jaw_mixture_and_source_support(self):
        counts = dict(left=0, right=0)
        for event in range(1, 1001):
            result = source_position(profile(), "a"*64, [1, 1, event], "fixed-panel")
            counts[result["jaw_id"]] += 1
            self.assertGreater(result["incoming_path_distance_cm"], 0.)
            self.assertLess(result["incoming_path_distance_cm"], 15.)
        self.assertGreater(counts["left"], 400)
        self.assertLess(counts["left"], 600)

    def test_unknown_invalid_or_discontinuous_material_is_rejected(self):
        for edit in (lambda p: p["segments"][0].update(interaction_length_cm=0.),
                     lambda p: p["segments"][0].update(interaction_length_cm=math.nan),
                     lambda p: p["segments"][0]["start_cm"].__setitem__(0, math.inf),
                     lambda p: p["segments"][1]["start_cm"].__setitem__(0, 1.),
                     lambda p: p["segments"][1]["end_cm"].__setitem__(2, 35.),
                     lambda p: p.update(segments=[])):
            bad = path()
            edit(bad)
            with self.assertRaises(ValueError):
                first_interaction(bad, .5)
        for uniform in (0., 1., -.1, math.nan, True):
            with self.assertRaises(ValueError):
                first_interaction(path(), uniform)

    def test_mixture_and_original_identity_cannot_be_ambiguous(self):
        for edit in (lambda p: p.update(conditional_on_jaw_collision=False),
                     lambda p: p["paths"][0].update(mixture_probability=1.),
                     lambda p: p["paths"][1].update(id="left")):
            bad = copy.deepcopy(profile())
            edit(bad)
            with self.assertRaises(ValueError):
                source_position(bad, "a"*64, [1, 1, 1], "seed")
        for digest, identity, seed in (("bad", [1, 1, 1], "seed"),
                                       ("a"*64, [True, 1, 1], "seed"),
                                       ("a"*64, [1., 1, 1], "seed"),
                                       ("a"*64, [1, 1, 1], "")):
            with self.assertRaises(ValueError):
                source_position(profile(), digest, identity, seed)


if __name__ == "__main__":
    unittest.main()
