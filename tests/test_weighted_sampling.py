import sys
from pathlib import Path
import unittest
import copy
import hashlib
import json
import tempfile
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_weighted_sampling import sampling_probability, uniform
from validate_sampling_ledger import validate
from run_sampling_replay import mkdir_shared


class WeightedSamplingTest(unittest.TestCase):
    def test_shared_parent_directory_visibility_retry(self):
        path = Mock()
        path.mkdir.side_effect = [FileExistsError(), FileNotFoundError(), None]
        with patch('run_sampling_replay.time.sleep') as sleep:
            mkdir_shared(path)
        self.assertEqual(path.mkdir.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        path.mkdir.assert_called_with(parents=True, exist_ok=True)

    def test_shared_parent_directory_retry_is_bounded(self):
        path = Mock()
        path.mkdir.side_effect = FileNotFoundError()
        with patch('run_sampling_replay.time.sleep') as sleep:
            with self.assertRaises(FileNotFoundError):
                mkdir_shared(path)
        self.assertEqual(path.mkdir.call_count, 10)
        self.assertEqual(sleep.call_count, 9)

    def test_shared_parent_permission_failure_is_not_retried(self):
        path = Mock()
        path.mkdir.side_effect = PermissionError()
        with patch('run_sampling_replay.time.sleep') as sleep:
            with self.assertRaises(PermissionError):
                mkdir_shared(path)
        self.assertEqual(path.mkdir.call_count, 1)
        sleep.assert_not_called()

    def test_positive_support_even_without_gen_muons(self):
        self.assertEqual(sampling_probability({'muons': []}), .1)

    def test_two_forward_energetic_muons_kept(self):
        self.assertEqual(sampling_probability({'muons': [dict(eta=-3,p=21),dict(eta=-2,p=40)]}),1.)

    def test_threshold_strict_and_direction_required(self):
        for muons in ([dict(eta=-3,p=20),dict(eta=-2,p=40)],
                      [dict(eta=3,p=30),dict(eta=-2,p=40)]):
            self.assertEqual(sampling_probability({'muons':muons},threshold=20),.1)

    def test_invalid_floor_and_threshold(self):
        for floor in (0., -1., 1.1, float('nan')):
            with self.assertRaises(ValueError):
                sampling_probability({'muons':[]}, floor=floor)
        with self.assertRaises(ValueError):
            sampling_probability({'muons':[]}, threshold=float('nan'))

    def test_hash_reproducible_and_strictly_bounded(self):
        values=[uniform((123,1,i),9) for i in range(10000)]
        self.assertTrue(all(0 <= x < 1 for x in values))
        self.assertEqual(values[0],uniform((123,1,0),9))
        self.assertNotEqual(values[0],uniform((123,1,0),10))
        self.assertTrue(850 < sum(x<.1 for x in values) < 1150)

    def test_inverse_probability_expectation(self):
        for q in (.1,.5,1.):
            self.assertAlmostEqual(q*(1/q)+(1-q)*0,1.)

    def test_ledger_partition_and_weights_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'parent.json'
            parent=dict(status='validated',contract=dict(mode='gen',seed=100,attempted_events=3),
                        generation=dict(rows=[dict(id=[100,1,i],muons=[]) for i in (1,3)]))
            path.write_text(json.dumps(parent))
            rows=[dict(id=[100,1,i],probability=1.,inverse_probability=1.,selected=True,reason='two_forward_muons') for i in (1,3)]
            ledger=dict(source_report=str(path),source_report_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                framework_attempts=3,threshold_GeV=10.,retention_floor=1.,salt=7,rows=rows,chunks=[rows],
                selected_events=2,upstream_saved_events=2,upstream_not_saved_events=1,
                upstream_not_saved_ids=[[100,1,2]],sum_inverse_probability=2.,sum_squared_inverse_probability=2.)
            self.assertEqual(validate(ledger)['selected'],2)
            for kind in ('weight','selected','duplicate','denominator'):
                broken=copy.deepcopy(ledger)
                if kind=='weight':broken['rows'][0]['inverse_probability']=2.
                elif kind=='selected':broken['rows'][0]['selected']=False
                elif kind=='duplicate':broken['upstream_not_saved_ids']=[[100,1,1]]
                else:broken['framework_attempts']=4
                with self.assertRaises(ValueError):validate(broken)


if __name__ == '__main__':
    unittest.main()
