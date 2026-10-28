# Code style: AI-code. AI-generated focused diagnostic classifier/count tests.
import copy
import json
from pathlib import Path
import tempfile
import unittest

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dy_branching_control import primary_decay, mass_counts, compare, native_bounds


def event(channel=(-13,13)):
    return [dict(barcode=1,pdg=23,hard_process=True,p4_GeV=[0.,0.,0.,24.],mothers=[],daughters=[2]),
            dict(barcode=2,pdg=23,hard_process=False,p4_GeV=[0.,0.,0.,24.],mothers=[1],daughters=[3,4]),
            dict(barcode=3,pdg=channel[0],hard_process=True,p4_GeV=[0.,0.,12.,12.],mothers=[2],daughters=[]),
            dict(barcode=4,pdg=channel[1],hard_process=True,p4_GeV=[0.,0.,-12.,12.],mothers=[2],daughters=[])]


class ControlTests(unittest.TestCase):
    def test_threshold_floor_and_both_hard_mass_bounds_are_native_and_pinned(self):
        parameters=['23:mMin = 0.211317','PhaseSpace:mHatMin = 0.211317',
                    'PhaseSpace:mHatMax = 0.5','23:onMode = off','23:onIfMatch = 13 -13']
        self.assertEqual(native_bounds(parameters,[.211317,.5]),[.211317,.5])
        with self.assertRaisesRegex(ValueError,'Native mass boundary'):
            native_bounds(parameters,[.5,1.])
        with self.assertRaisesRegex(ValueError,'23:mMin'):
            native_bounds(parameters[1:],[.211317,.5])

    def test_copy_chain_is_one_primary_and_direct_muons_count(self):
        result=primary_decay(event())
        self.assertTrue(result['primary23_mumu'])
        self.assertEqual(result['mass_GeV'],24.)
        self.assertEqual(result['primary23_copy_barcodes'],[1,2])

    def test_tau_daughter_muons_do_not_become_a_primary_muon_decay(self):
        rows=event((-15,15))
        rows[2]['daughters']=[5]
        rows.append(dict(barcode=5,pdg=13,hard_process=False,p4_GeV=[0.,0.,1.,1.],mothers=[3],daughters=[]))
        self.assertFalse(primary_decay(rows)['primary23_mumu'])

    def test_radiated_photon_does_not_remove_the_direct_muon_channel(self):
        rows=event();rows[1]['daughters'].append(5)
        rows.append(dict(barcode=5,pdg=22,hard_process=True,p4_GeV=[1.,0.,0.,1.],mothers=[2],daughters=[]))
        self.assertTrue(primary_decay(rows)['primary23_mumu'])

    def test_direct_muon_FSR_copy_is_followed_and_a_copy_fork_fails(self):
        rows=event();rows[2]['daughters']=[5,6]
        rows.extend([dict(barcode=5,pdg=-13,hard_process=False,p4_GeV=[0.,0.,10.,10.],mothers=[3],daughters=[]),
                     dict(barcode=6,pdg=22,hard_process=False,p4_GeV=[0.,0.,2.,2.],mothers=[3],daughters=[])])
        result=primary_decay(rows)
        self.assertTrue(result['primary23_mumu'])
        self.assertIn([3,5],result['direct_muon_copy_barcodes'])
        rows[2]['daughters'].append(7)
        rows.append(dict(barcode=7,pdg=-13,hard_process=False,p4_GeV=[0.,0.,1.,1.],mothers=[3],daughters=[]))
        with self.assertRaisesRegex(ValueError,'muon copy fork'):primary_decay(rows)

    def test_secondary_nonhard_boson_is_ignored_but_two_hard_bosons_fail(self):
        rows=event();rows.append(dict(barcode=10,pdg=23,hard_process=False,p4_GeV=[0.,0.,0.,3.],mothers=[],daughters=[3,4]))
        self.assertTrue(primary_decay(rows)['primary23_mumu'])
        rows[-1]['hard_process']=True
        with self.assertRaisesRegex(ValueError,'exactly one'):primary_decay(rows)

    def test_ambiguous_copy_fork_or_missing_hard_primary_fails(self):
        rows=event();rows[0]['daughters'].append(7)
        rows.append(dict(barcode=7,pdg=23,hard_process=False,p4_GeV=[0.,0.,0.,24.],mothers=[1],daughters=[3,4]))
        with self.assertRaisesRegex(ValueError,'forked'):primary_decay(rows)
        rows=event();rows[0]['hard_process']=False
        with self.assertRaisesRegex(ValueError,'exactly one'):primary_decay(rows)

    def test_mass_slice_rates_use_full_arm_exposure_and_no_guessed_BR(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            forced=dict(complete=True,arm='forced',plan_sha256='a'*64,accepted_gen_events=5000,
                source_xsec_pb=1.3,source_xsec_error_pb=.01,muonic_events=5000,
                mass_slices=[dict(bounds_GeV=[20.,25.],parents=2500,muonic=2500),
                             dict(bounds_GeV=[25.,None],parents=2500,muonic=2500)])
            natural=copy.deepcopy(forced);natural.update(arm='natural',source_xsec_pb=13.,muonic_events=500)
            natural['mass_slices'][0]['muonic']=250;natural['mass_slices'][1]['muonic']=250
            for name,report in [('forced',forced),('natural',natural)]:
                (base/(name+'.json')).write_text(json.dumps(report))
            compare(base/'forced.json',base/'natural.json',base/'comparison.json')
            result=json.loads((base/'comparison.json').read_text())
            self.assertFalse(result['normalization_ready'])
            self.assertFalse(result['correction_applied'])
            self.assertEqual(result['comparisons'][0]['natural_over_forced'],1.)
            self.assertEqual(result['comparisons'][1]['forced']['rate_pb'],.65)
            self.assertEqual(result['comparisons'][1]['natural']['rate_pb'],.65)
            self.assertEqual(result['comparisons'][1]['natural']['accepted_exposure'],5000)
            self.assertGreater(result['comparisons'][1]['natural']['approximate_independent_standard_error_pb'],
                               result['comparisons'][1]['natural']['conditional_binomial_error_pb'])


if __name__=='__main__':unittest.main()
