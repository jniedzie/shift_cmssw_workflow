# Code style: AI-code. Exact control comparison must retain real differences.
from pathlib import Path
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from compare_shift_sm_precision import equal, digest, run


class PrecisionComparisonTest(unittest.TestCase):
    def test_native_weight_or_probability_change_is_visible(self):
        self.assertFalse(equal([1., .1], [1., .10000000000000002]))
        self.assertFalse(equal([1., .1], [2., .1]))

    def test_nan_positions_can_match_but_not_hide_real_changes(self):
        self.assertTrue(equal([[float('nan'), 2.]], [[float('nan'), 2.]]))
        self.assertFalse(equal([[float('nan'), 2.]], [[1., 2.]]))

    def test_changed_lengths_or_branches_do_not_match(self):
        self.assertFalse(equal([1], [1, 2]))
        self.assertFalse(equal({'a': [1]}, {'b': [1]}))

    def test_actual_nano_difference_remains_visible_and_cannot_qualify_migration(self):
        try:
            import awkward as ak
            import numpy as np
            import uproot
        except ImportError:
            self.skipTest('Integration test needs stock CMSSW Nano Python tools')
        from shift_sm_response_labels import PAIR_FIELDS, SAMPLING_BRANCHES
        original = dict(gen_sha256='a'*64, event_id=[1, 1, 1], native_weight=2.,
                        sampling_probability=.1)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            pins = {}
            for variant, sentinel in [('old', 1.), ('new', 2.)]:
                job = directory/variant
                job.mkdir()
                arrays = dict(run=np.array([1], dtype='uint32'),
                    luminosityBlock=np.array([1], dtype='uint32'),
                    event=np.array([1], dtype='uint64'), nShiftMuon=np.array([2], dtype='uint32'),
                    nShiftDimuonVertex=np.array([1], dtype='uint32'), sentinel=np.array([sentinel]))
                for name, value in zip(SAMPLING_BRANCHES, [2., .1, 10., 20.]):
                    arrays[name] = np.array([value], dtype='float64')
                pair = dict(mass=30., pt=15., vx=0., vy=0., vz=14800., dca=5.,
                    dcaValid=1, isOS=1, topologyMin=2, topologyMax=2, muonIdx1=0, muonIdx2=1)
                for name in PAIR_FIELDS:
                    arrays['ShiftDimuonVertex_'+name] = ak.Array([[pair[name]]])
                nano = job/'nano.root'
                with uproot.recreate(nano) as output:
                    output.mktree('Events', arrays)
                request = dict(detector_job=7, selected_skip=0, selected_count=1,
                    detector_source=dict(selected_indices=[0], event_ids=[[1, 1, 1]],
                        normalization_record={'gen_sha256': 'a'*64}, nano_base=variant))
                (job/'request.json').write_text(json.dumps(request))
                pin = dict(directory=str(job), request_sha256=digest(job/'request.json'))
                pins[variant] = pin
                result = dict(complete=True, exit_code=0, request_sha256=pin['request_sha256'],
                    nano_path=str(nano), nano_sha256=digest(nano), nano_readback_sha256=digest(nano))
                (job/'result.json').write_text(json.dumps(result))
                (job/'status.json').write_text(json.dumps(dict(complete=True, exit_code=0)))
            request_path = directory/'comparison.json'
            request_path.write_text(json.dumps(dict(cases=[dict(name='control', expected=original, **pins)])))
            previous = Path.cwd()
            try:
                os.chdir(directory)
                with patch('compare_shift_sm_precision.download', side_effect=lambda source, target, checksum: shutil.copyfile(source, target)):
                    run(request_path)
                result = json.loads((directory/'report.json').read_text())
            finally:
                os.chdir(previous)
        self.assertFalse(result['all_controls_equal'])
        self.assertFalse(result['runtime_migration_qualified'])
        self.assertTrue(result['reconstructed_BothBoth_control_seen'])
        self.assertEqual(result['cases'][0]['differences'][0]['branch'], 'sentinel')
        self.assertEqual(result['cases'][0]['new_native_weight'], 2.)
        self.assertEqual(result['cases'][0]['new_sampling_probability'], .1)


if __name__ == '__main__':
    unittest.main()
