import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/summarize_sampling_scan.py'
spec = importlib.util.spec_from_file_location('sampling_summary', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GateDiagnosticsTest(unittest.TestCase):
    def test_transport_produced_muons_are_not_assumed_absent(self):
        row = dict(muons=[], simhits={'csc': 5}, reco={'muons': 2})
        result = module.gates(row)
        self.assertTrue(result['fewer_than_two_gen_muons'])
        self.assertFalse(result['no_muon_system_simhit'])
        self.assertFalse(result['fewer_than_two_reco_muons'])

    def test_threshold_is_strict_and_distinct_from_direction(self):
        row = dict(muons=[{'eta': -3., 'p': 40.}, {'eta': -4., 'p': 80.}],
                   simhits={'csc': 0}, reco={'muons': 1})
        result = module.gates(row)
        self.assertTrue(result['no_muon_system_simhit'])
        self.assertFalse(result['fewer_than_two_forward_gen_muons'])
        self.assertFalse(result['fewer_than_two_forward_gen_muons_p_gt_20'])
        self.assertTrue(result['fewer_than_two_forward_gen_muons_p_gt_40'])

    def test_backward_muon_does_not_pass_forward_diagnostic(self):
        row = dict(muons=[{'eta': -3., 'p': 100.}, {'eta': 3., 'p': 100.}],
                   simhits={'csc': 1}, reco={'muons': 2})
        result = module.gates(row)
        self.assertFalse(result['fewer_than_two_gen_muons'])
        self.assertTrue(result['fewer_than_two_forward_gen_muons'])


if __name__ == '__main__':
    unittest.main()
