"""Guard physical-point and normalization contracts before runtime pilots."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dark_photon_generation import contract, production_settings, signal_settings


class SignalGenerationContractTest(unittest.TestCase):
    def setUp(self):
        self.width = dict(mass_gev=15., epsilon=1e-3, total_width_gev=2.64e-5,
                          ctau_mm=1.973269804e-13/2.64e-5, br_mumu=.145)

    def test_forced_decay_retains_total_lifetime_and_branch_convention(self):
        data = contract(15.,1e-3,self.width,'mumu',100,1234,1234,[],[])
        self.assertIn('32:onIfMatch = 13 -13', data['process_settings'])
        self.assertEqual(data['width_authority']['total_width_gev'],self.width['total_width_gev'])
        self.assertIn('no second BR factor',data['cross_section_convention'])
        self.assertFalse(data['physics_valid'])
        self.assertIn('32:tau0 = ', '\n'.join(data['process_settings']))
        self.assertFalse(any('doForceWidth = on' in x for x in data['process_settings']))

    def test_mismatched_or_partial_width_table_fails(self):
        for change in ({'epsilon':1e-6},{'mass_gev':20.},{'ctau_mm':1.},
                       {'br_mumu':0.},{'total_width_gev':float('nan')}):
            with self.assertRaises(ValueError):
                signal_settings(15.,1e-3,dict(self.width,**change))

    def test_unsupported_hadronic_and_degenerate_points_fail(self):
        for mass,eps in ((.22,1e-7),(5.,1e-7),(91.1876,1e-7),(15.,0.),(15.,.1)):
            with self.assertRaises(ValueError):
                production_settings(mass,eps)

    def test_inclusive_has_no_signal_forcing(self):
        commands = signal_settings(15.,1e-3,self.width,'inclusive')
        self.assertFalse(any('onIfMatch' in command for command in commands))

    def test_fixed_target_and_generator_scope(self):
        data = contract(15.,1e-3,self.width,'mumu',2,1234,1234,[],[])
        self.assertIn('Beams:eA = 0.',data['beam_settings'])
        self.assertIn('Beams:eB = 6800.0',data['beam_settings'])
        self.assertEqual(data['source_z_mm'],148000.)
        self.assertFalse(any(any(word in command for word in ('Detector','Readout','Trigger','G4'))
                             for command in data['process_settings']))

    def test_unresolved_native_pole_fails_closed(self):
        tiny=dict(self.width, epsilon=1e-7, total_width_gev=2.64e-15,
                  ctau_mm=1.973269804e-13/2.64e-15)
        with self.assertRaises(ValueError):
            contract(15.,1e-7,tiny,'mumu',2,1234,1234,[],[])
        data=contract(15.,1e-7,tiny,'mumu',2,1234,1234,[],[],self.width,1e5)
        self.assertEqual(data['production_rate_correction'],1e-10)
        self.assertEqual(data['width_authority']['ctau_mm'],tiny['ctau_mm'])


if __name__ == '__main__':
    unittest.main()
