# Code style: AI-code. AI-generated exact decay-mixture closure checks.
import copy
import itertools
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_decay_channel_proposal import (accepted_event_proposal, natural_event_fraction,
                                         natural_trial_fraction, whole_event_proposal)


def parents(probabilities, channels):
    return [dict(parent_barcode=index+1, pdg_id=443,
                 forced_channel_probability=probability, in_forced_channel=channel)
            for index, (probability, channel) in enumerate(zip(probabilities, channels))]


class FullSupportDecayMixture(unittest.TestCase):
    def test_mixture_uses_original_trials_and_both_components_share_the_same_density(self):
        self.assertEqual(natural_trial_fraction(20000, 20000), .5)
        self.assertNotEqual(natural_trial_fraction(20000, 20000), 1392/(1360+1392))
        b = .0593
        muonic = whole_event_proposal(parents([b], [True]),
                                     natural_trials=20000, forced_trials=20000)
        self.assertAlmostEqual(muonic['proposal_density_ratio'], .5+.5/b)
        self.assertAlmostEqual(muonic['proposal_correction'], 1./(.5+.5/b))
        self.assertEqual(muonic['proposal_correction'], 1./muonic['proposal_density_ratio'])
        nonmuonic = whole_event_proposal(parents([b], [False]),
                                        natural_trials=20000, forced_trials=20000)
        self.assertAlmostEqual(nonmuonic['proposal_correction'], 2.)
        # No selected-component flag exists: the complete mixture law applies
        # even when a muonic state happened to come from the natural component.
        self.assertAlmostEqual(muonic['proposal_density_ratio']*muonic['proposal_correction'], 1.)

    def test_exact_multiple_parent_closure_keeps_cross_parent_and_unrelated_muon_pairs(self):
        branching = [.0593, .2]
        unrelated_probability = .4
        alpha = natural_trial_fraction(7, 13)
        native = [0., 0., 0., 0.]
        corrected = [0., 0., 0., 0.]
        total_proposal = 0.
        for channels in itertools.product([False, True], repeat=2):
            f_channels = math.prod(b if on else 1.-b for b, on in zip(branching, channels))
            result = whole_event_proposal(parents(branching, channels), natural_trials=7, forced_trials=13)
            for unrelated in [False, True]:
                f_unrelated = unrelated_probability if unrelated else 1.-unrelated_probability
                f = f_channels*f_unrelated
                q = alpha*f + ((1.-alpha)*f_unrelated if all(channels) else 0.)
                total_proposal += q
                self.assertAlmostEqual(q/f, result['proposal_density_ratio'])
                self.assertGreater(result['proposal_correction'], 0.)
                self.assertLessEqual(result['proposal_correction'], 1./alpha*(1.+1.e-14))
                # Every muonic 443 and the unrelated source supply an OS pair.
                # Count all OS combinations, including cross-parent pairs.
                charges_per_sign = sum(channels) + int(unrelated)
                observables = [1., sum(channels), int(unrelated), charges_per_sign**2]
                for index, value in enumerate(observables):
                    native[index] += f*value
                    corrected[index] += q*result['proposal_correction']*value
        self.assertAlmostEqual(total_proposal, 1.)
        for expected, actual in zip(native, corrected):
            self.assertAlmostEqual(actual, expected, places=14)
        self.assertAlmostEqual(corrected[2], unrelated_probability)

    def test_forced_only_cannot_restore_the_inclusive_unrelated_muon_observable(self):
        b = .0593
        # B times a forced event correctly gives only the muonic-J/psi
        # component, even when the plotted pair comes from somewhere else.
        unrelated_native_rate = .4
        restricted_rate = b*unrelated_native_rate
        self.assertNotEqual(restricted_rate, unrelated_native_rate)
        with self.assertRaisesRegex(ValueError, 'forced-only'):
            whole_event_proposal(parents([b], [True]), natural_trials=0, forced_trials=20)

    def test_source_weight_and_admission_are_independent_of_the_decay_correction(self):
        rows = parents([.0593, .0593], [True, True])
        before = copy.deepcopy(rows)
        result = whole_event_proposal(rows, natural_trials=20, forced_trials=20)
        self.assertEqual(rows, before)
        native_weight, admission = 3.e-9, .125
        final = native_weight/admission*result['proposal_correction']
        self.assertAlmostEqual(final/(native_weight/admission), result['proposal_correction'])
        self.assertAlmostEqual(result['native_all_forced_channel_probability'], .0593**2)
        self.assertFalse(any('weight' in key or 'admission' in key for key in result))

    def test_natural_only_and_unit_branching_are_identity_proposals(self):
        natural = whole_event_proposal(parents([.0593], [False]), natural_trials=20, forced_trials=0)
        self.assertEqual(natural['proposal_correction'], 1.)
        unit = whole_event_proposal(parents([1.], [True]), natural_trials=20, forced_trials=20)
        self.assertEqual(unit['proposal_correction'], 1.)
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            whole_event_proposal(parents([1.], [False]), natural_trials=20, forced_trials=20)

    def test_unknown_models_channels_and_ambiguous_copy_chains_are_rejected(self):
        for change in [dict(pdg_id=511), dict(pdg_id=None),
                       dict(forced_channel_probability=None), dict(forced_channel_probability=0.),
                       dict(forced_channel_probability=float('nan')),
                       dict(forced_channel_probability=float('inf')),
                       dict(forced_channel_probability=True), dict(in_forced_channel=None),
                       dict(in_forced_channel=1)]:
            row = parents([.0593], [True])[0]
            row.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                whole_event_proposal([row], natural_trials=20, forced_trials=20)
        row = parents([.0593], [True])[0]
        for inventory in [[], [row, row]]:
            with self.assertRaises(ValueError):
                whole_event_proposal(inventory, natural_trials=20, forced_trials=20)
        for counts in [(0, 20), (-1, 20), (20, -1), (True, 20), (20., 20)]:
            with self.assertRaises(ValueError):
                natural_trial_fraction(*counts)

    def test_unrepresentable_likelihood_is_not_silently_clipped_to_zero(self):
        with self.assertRaisesRegex(ValueError, 'double precision'):
            whole_event_proposal(parents([1.e-200, 1.e-200], [True, True]),
                                 natural_trials=20, forced_trials=20)

    def test_accepted_source_allocation_is_explicit_and_keeps_its_measure(self):
        result = accepted_event_proposal(parents([.0593], [True]),
                                         natural_events=7, forced_events=13)
        self.assertEqual(natural_event_fraction(7, 13), .35)
        self.assertEqual(result['denominator_kind'], 'accepted_gen_events')
        self.assertEqual(result['natural_source_events'], 7)
        self.assertEqual(result['forced_source_events'], 13)
        self.assertFalse(any('trial' in key for key in result))
        self.assertAlmostEqual(result['proposal_density_ratio'], .35+.65/.0593)
        # Internal rejection counts (e.g.142017 attempts for5 accepted class
        # originals) estimate sigma_class, never the decay-mixture allocation.
        self.assertNotEqual(natural_event_fraction(7, 13), 142017/(142017+13))

    def test_accepted_class_corrected_observables_close_with_accepted_denominator(self):
        alpha = natural_event_fraction(7, 13)
        branching = [.0593, .2]
        native = corrected = 0.
        for channels in itertools.product([False, True], repeat=2):
            f = math.prod(b if on else 1.-b for b, on in zip(branching, channels))
            q = alpha*f+((1.-alpha) if all(channels) else 0.)
            result = accepted_event_proposal(parents(branching, channels),
                                             natural_events=7, forced_events=13)
            # Count OS pairs from both J/psi parents and one unrelated pair.
            observable = (sum(channels)+1)**2
            native += f*observable
            corrected += q*result['proposal_correction']*observable
        self.assertAlmostEqual(native, corrected, places=14)
        sigma_class, accepted_originals = 17., 20
        self.assertAlmostEqual(sigma_class*accepted_originals*corrected/accepted_originals,
                               sigma_class*native)
        self.assertEqual(accepted_event_proposal(parents([.0593], [False]),
                         natural_events=20, forced_events=0)['proposal_correction'], 1.)
        for counts in [(0, 20), (True, 20), (20., 20), (20, -1)]:
            with self.assertRaises(ValueError):
                natural_event_fraction(*counts)


if __name__ == '__main__':
    unittest.main()
