# Code style: AI-code. AI-generated exact CSC polygon-ray regression checks.
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_csc_active_geometry import local_to_global,ray_intersections


class ActiveGeometryTest(unittest.TestCase):
    def layer(self,number=1,center=(0.,0.,0.),endcap=1,station=1):
        return dict(raw_id=number,center_cm=list(center),axes=dict(x=[1.,0.,0.],y=[0.,1.,0.],z=[0.,0.,1.]),
            bottom_half_width_cm=1.,top_half_width_cm=2.,half_length_cm=2.,half_thickness_cm=.1,
            endcap=endcap,station=station,ring=1,chamber=1,layer=1)

    def test_sloped_polygon_edge_is_used_without_a_radial_or_rectangle_proxy(self):
        geometry=dict(layers=[self.layer()])
        hit=ray_intersections(geometry,[1.75,1.,10.],[0.,0.,-1.])
        self.assertEqual(len(hit['hits']),1)
        self.assertAlmostEqual(hit['hits'][0]['signed_inside_clearance_cm'],0.)
        miss=ray_intersections(geometry,[1.750001,1.,10.],[0.,0.,-1.])
        self.assertEqual(miss['hits'],[])
        self.assertLess(miss['best_layer_clearance_by_station'][0]['signed_inside_clearance_cm'],0.)

    def test_internal_gap_is_not_filled_by_a_broad_station_envelope(self):
        layers=[]
        for endcap,zsign in [(1,1.),(2,-1.)]:
            for station in [1,2]:
                for x in [-4.,4.]:
                    layers.append(self.layer(len(layers)+1,(x,0.,zsign*(5.+station)),endcap,station))
        geometry=dict(layers=layers)
        gap=ray_intersections(geometry,[0.,0.,20.],[0.,0.,-1.])
        self.assertFalse(gap['two_stations_each_endcap'])
        self.assertEqual(gap['hits'],[])
        active=ray_intersections(geometry,[4.,0.,20.],[0.,0.,-1.])
        self.assertTrue(active['two_stations_each_endcap'])
        self.assertEqual(active['by_endcap']['plus']['station_count'],2)
        self.assertEqual(active['by_endcap']['minus']['station_count'],2)

    def test_rotated_layer_uses_global_plane_and_vector_transform(self):
        layer=self.layer(center=(5.,0.,0.))
        layer['axes']=dict(x=[0.,1.,0.],y=[0.,0.,1.],z=[1.,0.,0.])
        self.assertEqual(local_to_global(layer,[1.,2.,3.]),[8.,1.,2.])
        self.assertEqual(local_to_global(layer,[1.,2.,3.],vector=True),[3.,1.,2.])
        result=ray_intersections(dict(layers=[layer]),[10.,0.,0.],[-1.,0.,0.])
        self.assertEqual(result['hits'][0]['global_cm'],[5.,0.,0.])

    def test_layers_do_not_inflate_station_count_and_backward_rays_do_not_hit(self):
        first=self.layer();second=self.layer(number=2,center=(0.,0.,.1));second['layer']=2
        result=ray_intersections(dict(layers=[first,second]),[0.,0.,10.],[0.,0.,-1.])
        self.assertEqual(result['by_endcap']['plus']['layer_count'],2)
        self.assertEqual(result['by_endcap']['plus']['station_count'],1)
        self.assertEqual(ray_intersections(dict(layers=[first]),[0.,0.,10.],[0.,0.,1.])['hits'],[])
        self.assertEqual(ray_intersections(dict(layers=[first]),[0.,0.,10.],[1.,0.,0.])['hits'],[])
        with self.assertRaisesRegex(ValueError,'Zero momentum'):
            ray_intersections(dict(layers=[first]),[0.,0.,10.],[0.,0.,0.])


if __name__=='__main__':unittest.main()
