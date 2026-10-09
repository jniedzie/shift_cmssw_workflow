import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from plot_dark_photon_kinematics import append_object, binned_counts, collect_event, common_edges, color_slot, plan_samples


def particle(pdg, mother=-1, status=2):
    return dict(pdgId=pdg, genPartIdxMother=mother, status=status, pt=2., eta=-2.,
                phi=0., pz=-2 * math.sinh(2.), mass=15. if pdg == 32 else .1056583755,
                vx=0., vy=0., vz=14800.)


class DarkPhotonKinematicsTest(unittest.TestCase):
    def test_zero_reconstruction_events_remain_in_full_gen_denominator(self):
        counts, receipt = binned_counts([2., 3.], [0., 5., 10.], 20)
        self.assertEqual(counts, [.1, 0.])
        self.assertEqual(receipt['sum_per_generated_event_including_flow'], .1)

    def test_histogram_tails_are_counted_and_not_renormalized_away(self):
        counts, receipt = binned_counts([-1., 2., 10.], [0., 5., 10.], 20)
        self.assertEqual(counts, [.05, 0.])
        self.assertEqual((receipt['underflow'], receipt['overflow']), (1, 1))
        self.assertEqual(receipt['sum_per_generated_event_including_flow'], .15)

    def test_centimetres_to_metres_and_longitudinal_momentum(self):
        data = {}
        append_object(data, 'track', dict(pt=3., pz=-4., vx=2., vy=3., vz=14800.))
        self.assertEqual(data['track']['z'], [148.])
        self.assertEqual(data['track']['p'], [5.])
        self.assertEqual(data['track']['x'], [2.])

    def test_parent_history_collapses_and_signal_reco_uses_hit_truth_only(self):
        particles = [particle(32), particle(32, 0), particle(13, 1, 1), particle(-13, 1, 1)]
        muons = [dict(hitGenPartIdx=2, genPartIdx=2, charge=-1, pt=2., eta=-2., pz=-5., vx=0., vy=0., vz=14800.),
                 dict(hitGenPartIdx=-1, genPartIdx=3, charge=1, pt=2., eta=-2., pz=-5., vx=0., vy=0., vz=14800.)]
        data = {}
        collect_event(data, particles, muons, [], [1, 1, 1])
        self.assertEqual(len(data['gen_parent']['pt']), 1)
        self.assertEqual(len(data['gen_muon']['pt']), 2)
        self.assertEqual(len(data['reco_muon_all']['pt']), 2)
        self.assertEqual(len(data['reco_muon_signal']['pt']), 1)

    def test_failed_dca_does_not_become_a_valid_vertex_plot(self):
        particles = [particle(32), particle(13, 0, 1), particle(-13, 0, 1)]
        muons = [dict(hitGenPartIdx=1, genPartIdx=1, charge=-1, pt=2., eta=-2., pz=-5., vx=0., vy=0., vz=14800.),
                 dict(hitGenPartIdx=2, genPartIdx=2, charge=1, pt=2., eta=-2., pz=-5., vx=0., vy=0., vz=14800.)]
        pair = dict(muonIdx1=0, muonIdx2=1, mass=15., pt=4., eta=-2., pz=-10., vx=-999., vy=-999., vz=-999., isOS=1, dcaValid=0)
        data = {}
        collect_event(data, particles, muons, [pair], [1, 1, 1])
        self.assertIn('reco_pair_signal', data)
        self.assertNotIn('reco_vertex_signal', data)

    def test_common_edges_preserve_finite_far_displaced_tail(self):
        samples = [{'data': {'decay': {'z': [-1500., 148.]}}}]
        edges = common_edges(samples, 'decay', 'z')
        self.assertLess(edges[0], -1500.)
        self.assertGreater(edges[-1], 148.)

    def test_mass_colors_do_not_depend_on_which_points_have_completed(self):
        self.assertEqual([color_slot(m) for m in [15, 30, 50]], [0, 1, 2])
        self.assertEqual(color_slot(50), 2)

    def test_prepared_grid_plan_retains_full_gen_count_and_nominal_lifetime_label(self):
        samples = plan_samples({'points':[dict(point='m30_5m',mass_gev=30.,target_mean_lab_flight_m=5.,events=20,
                                              epsilon=1e-7,detector_directory='/tmp/detector')]})
        self.assertEqual(samples[0]['generated_events'],20)
        self.assertEqual(samples[0]['lifetime_label'],'mean_lab_5m')
        self.assertEqual(samples[0]['nano_path'],'/tmp/detector/nano.root')


if __name__ == '__main__':
    unittest.main()
