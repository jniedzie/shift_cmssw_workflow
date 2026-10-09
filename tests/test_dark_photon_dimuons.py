from pathlib import Path
import sys
import unittest
import math

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_dark_photon_dimuons import direct_pairs, diagnose_event, summarize, momentum


def particle(pdg, mother=-1, status=2, vz=0.):
    return dict(pdgId=pdg, genPartIdxMother=mother, status=status, pt=2., eta=1., phi=0.,
                mass=15. if pdg == 32 else .1056583755, vx=0., vy=0., vz=vz)


def fixture():
    result = [particle(32), particle(32, 0), particle(13, 1, 2, 10.),
            particle(13, 2, 1, 10.), particle(-13, 1, 1, 10.)]
    for child in result[2:]:
        child['vx'] = 10 / math.sinh(1.)
    return result


class DarkPhotonDimuonTest(unittest.TestCase):
    def test_history_and_muon_copies_count_one_physical_pair(self):
        pairs, coverage = direct_pairs(fixture())
        self.assertEqual(coverage['retained_parent_histories'], 1)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]['history_indices'], [0, 1])
        self.assertEqual(pairs[0]['minus']['muon_copy_indices'], [3, 2])
        self.assertEqual(pairs[0]['decay_vertex_cm'][2], 10.)
        self.assertGreater(pairs[0]['proper_length_spatial_proxy_mm'], 0)

    def test_secondary_tau_muons_are_not_direct_signal(self):
        particles = [particle(32), particle(15, 0), particle(13, 1, 1), particle(-13, 1, 1)]
        pairs, coverage = direct_pairs(particles)
        self.assertEqual(pairs, [])
        self.assertEqual(coverage['histories_without_direct_pair'], [0])

    def test_multiple_stable_daughters_fail_unique_pair_denominator(self):
        particles = fixture() + [particle(13, 1, 1, 10.)]
        pairs, coverage = direct_pairs(particles)
        self.assertEqual(pairs, [])
        self.assertEqual(len(coverage['ambiguous_histories']), 1)

    def test_different_decay_copies_are_not_combined(self):
        particles = fixture()
        particles[4]['genPartIdxMother'] = 0
        pairs, coverage = direct_pairs(particles)
        self.assertEqual(pairs, [])
        self.assertEqual(len(coverage['ambiguous_histories']), 1)

    def test_cycles_and_invalid_parent_references_fail(self):
        for reference in [0, 99, -2]:
            particles = fixture()
            particles[0]['genPartIdxMother'] = reference
            with self.assertRaises(ValueError):
                direct_pairs(particles)

    def test_daughter_vertex_disagreement_fails(self):
        particles = fixture()
        particles[4]['vz'] = 11.
        with self.assertRaisesRegex(ValueError, 'birth vertices'):
            direct_pairs(particles)

    def test_no_silent_angular_fallback(self):
        muons = [dict(hitGenPartIdx=-1, genPartIdx=3, charge=-1),
                 dict(hitGenPartIdx=-1, genPartIdx=4, charge=1)]
        vertices = [dict(muonIdx1=0, muonIdx2=1, mass=15., vx=0., vy=0., vz=10., isOS=1)]
        event = diagnose_event(fixture(), muons, vertices, [1, 1, 1])
        summary = summarize([event])
        self.assertEqual(summary['direct_pair_denominator'], 1)
        self.assertEqual(summary['hit_matched_direct_legs'], 0)
        self.assertEqual(summary['direct_pairs_with_reco_vertex'], 0)
        self.assertEqual(event['direct_pairs'][0]['minus']['angular_matched_reco_indices'], [0])

    def test_duplicate_reco_matches_do_not_inflate_truth_denominators(self):
        muons = [dict(hitGenPartIdx=3, genPartIdx=3, charge=-1),
                 dict(hitGenPartIdx=4, genPartIdx=4, charge=1),
                 dict(hitGenPartIdx=3, genPartIdx=3, charge=-1)]
        vertices = [dict(muonIdx1=0, muonIdx2=1, mass=15., vx=0., vy=0., vz=10., isOS=1),
                    dict(muonIdx1=2, muonIdx2=1, mass=15., vx=0., vy=0., vz=10., isOS=1)]
        event = diagnose_event(fixture(), muons, vertices, [1, 1, 1])
        summary = summarize([event])
        self.assertEqual(summary['hit_matched_direct_legs'], 2)
        self.assertEqual(summary['direct_pairs_with_reco_vertex'], 1)
        self.assertEqual(len(event['direct_pairs'][0]['hit_matched_vertices']), 2)

    def test_jpsi_is_not_a_dark_photon(self):
        particles = fixture()
        particles[0]['pdgId'] = particles[1]['pdgId'] = 443
        self.assertEqual(direct_pairs(particles)[0], [])

    def test_sub_float_vertex_resolution_does_not_claim_a_lifetime(self):
        particles = fixture()
        for p in particles:
            p['vz'] = 14800.
            p['vx'] = 0.
        self.assertIsNone(direct_pairs(particles)[0][0]['proper_length_spatial_proxy_mm'])

    def test_invalid_vertex_indices_fail_even_without_signal(self):
        vertices = [dict(muonIdx1=0, muonIdx2=0, mass=15., vx=0., vy=0., vz=0., isOS=1)]
        with self.assertRaises(ValueError):
            diagnose_event([], [], vertices, [1, 1, 1])

    def test_native_weights_are_applied_once_to_separate_weighted_counts(self):
        events = [diagnose_event(fixture(), [], [], [1, 1, i], weight)
                  for i, weight in enumerate((2., -1., 0.))]
        summary = summarize(events)
        self.assertEqual(summary['direct_pair_denominator'], 3)
        self.assertEqual(summary['weighted_direct_pair_denominator'], 1.)
        self.assertEqual(summary['weighted_direct_muon_leg_denominator'], 2.)

    def test_longitudinal_parent_uses_persisted_pz_without_eta_overflow(self):
        parent = particle(32)
        parent.update(pt=0., eta=1000., pz=100.)
        self.assertEqual(momentum(parent)[2], 100.)

    def test_non_collinear_flight_does_not_claim_a_proper_length(self):
        particles = fixture()
        for child in particles[2:]:
            child['vx'] = 0.
        pair = direct_pairs(particles)[0][0]
        self.assertGreater(pair['transverse_flight_residual_cm'], 1.)
        self.assertIsNone(pair['proper_length_spatial_proxy_mm'])


if __name__ == '__main__':
    unittest.main()
