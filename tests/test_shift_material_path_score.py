# Code style: AI-code. AI-generated material-ranking boundary and unit tests.
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import shift_material_path_score as score


FRAME = dict(model_origin_cm=[10., 20., 30.], artifact_origin_in_model_cm=[1., 2., 3.],
             model_to_cms=[0., -1., 0., 1., 0., 0., 0., 0., 1.])


class FrameTests(unittest.TestCase):
    def test_angular_map_targets_plane_with_signed_eta(self):
        paths=score.angular_paths(dict(origin_cm=[0.,0.,100.], target_z_cm=10.,
                                      eta_edges=[-3.,-2.], phi_edges=[-math.pi,0.,math.pi]))
        self.assertEqual(len(paths),2)
        self.assertTrue(all(math.isclose(p['end_cm'][2],10.) for p in paths))
        self.assertAlmostEqual(math.dist(paths[0]['start_cm'],paths[0]['end_cm']),
                               90./abs(math.tanh(-2.5)))
        self.assertEqual([p['phi_index'] for p in paths],[0,1])

    def test_angular_map_rejects_outgoing_or_invalid_grid(self):
        inputs=dict(origin_cm=[0.,0.,100.], target_z_cm=10., eta_edges=[-3.,-2.],phi_edges=[0.,1.])
        for change in (dict(eta_edges=[2.,3.]),dict(phi_edges=[1.,0.]),dict(target_z_cm=100.)):
            with self.assertRaises(ValueError):score.angular_paths(inputs|change)

    def test_inverse_actual_placement_and_rotated_two_sided_placement(self):
        self.assertEqual(score.artifact_point([6., 23., 35.], FRAME), [2., 2., 2.])
        self.assertEqual(score.artifact_point([-6., 23., -35.], FRAME, mirrored=True), [2., 2., 2.])

    def test_bad_rotation_and_nonfinite_points_are_rejected(self):
        for rotation in ([2., 0., 0., 0., 1., 0., 0., 0., 1.], [-1., 0., 0., 0., 1., 0., 0., 0., 1.]):
            with self.assertRaises(ValueError):
                score.artifact_point([0., 0., 0.], FRAME | {'model_to_cms': rotation})
        with self.assertRaises(ValueError):
            score.external_chord([0., 0., math.nan], [0., 0., 0.], 10.)

    def test_clip_at_external_handoff_and_keep_unmodelled_length(self):
        start, end, external, unmodelled = score.external_chord([0., 0., 100.], [0., 0., -100.], 10.)
        self.assertEqual(end, [0., 0., 10.])
        self.assertAlmostEqual(external, 90.)
        self.assertAlmostEqual(unmodelled, 110.)


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import ROOT
        ROOT.TGeoManager.LockDefaultUnits(False)
        ROOT.TGeoManager.SetDefaultUnits(ROOT.TGeoManager.kRootUnits)
        ROOT.TGeoManager.LockDefaultUnits(True)
        cls.geometry = ROOT.TGeoManager('synthetic_parent_paths', '')
        ROOT.SetOwnership(cls.geometry, False)
        vacuum = ROOT.TGeoMaterial('synthetic_vacuum', 0., 0., 0.)
        dense = ROOT.TGeoMaterial('synthetic_dense', 56., 26., 2.)
        # ROOT recomputes positive supplied lengths; negative inputs explicitly
        # request their absolute centimetre values for this synthetic fixture.
        dense.SetRadLen(-10., -20.)
        assert dense.GetIntLen() == 20.
        background = ROOT.TGeoMedium('synthetic_background', 1, vacuum)
        slab_medium = ROOT.TGeoMedium('synthetic_slab', 2, dense)
        world = cls.geometry.MakeBox('synthetic_world', background, 100., 100., 100.)
        slab = cls.geometry.MakeBox('synthetic_slab', slab_medium, 50., 50., 5.)
        placement = ROOT.TGeoTranslation(0., 0., 0.)
        cls.objects = [vacuum, dense, background, slab_medium, world, slab, placement]
        for obj in cls.objects:
            ROOT.SetOwnership(obj, False)
        cls.geometry.SetTopVolume(world)
        world.AddNode(slab, 1, placement)
        cls.geometry.CloseGeometry()

    def test_known_slab_column_and_interaction_depth_in_cm(self):
        result = score.trace_chord(self.geometry, [0., 0., -20.], [0., 0., 20.])
        self.assertTrue(result['complete'], result)
        self.assertAlmostEqual(result['traced_length_cm'], 40., places=5)
        self.assertAlmostEqual(result['column_density_g_cm2'], 20., places=5)
        self.assertAlmostEqual(result['interaction_depth_proxy'], .5, places=5)
        self.assertAlmostEqual(result['radiation_depth_proxy'], 1., places=5)
        self.assertAlmostEqual(result['external_chord_attenuation_proxy'], math.exp(-.5), places=5)
        self.assertIsNone(result['first_depth_one_distance_cm'])

    def test_first_depth_one_includes_upstream_vacuum_and_interpolates_inside_material(self):
        result = score.trace_chord(self.geometry, [-80., 0., 0.], [80., 0., 0.])
        self.assertTrue(result['complete'])
        self.assertAlmostEqual(result['interaction_depth_proxy'],5.,places=5)
        self.assertAlmostEqual(result['first_depth_one_distance_cm'],50.,places=5)

    def test_first_depth_one_known_prefix_does_not_claim_full_unknown_path(self):
        result = score.trace_chord(self.geometry, [-80., 0., 0.], [220., 0., 0.])
        self.assertFalse(result['complete'])
        self.assertIsNone(result['external_chord_attenuation_proxy'])
        self.assertAlmostEqual(result['first_depth_one_distance_cm'],50.,places=5)

    def test_outside_and_missing_central_geometry_are_unknown(self):
        result = score.trace_chord(self.geometry, [200., 0., 0.], [220., 0., 0.])
        self.assertFalse(result['complete'])
        self.assertIsNone(result['external_chord_attenuation_proxy'])
        frame = dict(model_origin_cm=[0., 0., 0.], artifact_origin_in_model_cm=[0., 0., 0.],
                     model_to_cms=[1., 0., 0., 0., 1., 0., 0., 0., 1.], minimum_abs_z_cm=10.)
        report = score.score_paths(self.geometry, [dict(id='central', start_cm=[0., 0., 0.],
            end_cm=[0., 0., 5.])], frame)
        self.assertFalse(report['paths'][0]['complete'])
        self.assertFalse(report['paths'][0]['full_saved_chord_coverage'])
        self.assertFalse(report['usable_as_physics_weight'])


if __name__ == '__main__':
    unittest.main()
