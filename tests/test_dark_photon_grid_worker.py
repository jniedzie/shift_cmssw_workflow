import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_dark_photon_grid import sha,validate_plan


class FrozenGridWorkerTest(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        p=Path(self.directory.name)/'frozen.json';p.write_text('{}\n')
        record=dict(frozen_copy=str(p),sha256=sha(p))
        self.plan=dict(schema='shift-dark-photon-grid-plan-v1',prepared=True,
                       frozen_dependencies=dict(sources={'script':record},templates={'template':record},references=record),
                       points=[dict(point='one',events=20,detector_count=20,
                                    signal_contract=str(p),signal_contract_sha256=sha(p))])

    def test_complete_small_grid_point_is_accepted(self):
        self.assertEqual(validate_plan(self.plan,'one')['events'],20)

    def test_partial_replay_cannot_be_used_as_full_grid(self):
        p=copy.deepcopy(self.plan);p['points'][0]['detector_count']=2
        with self.assertRaises(ValueError):validate_plan(p,'one')

    def test_modified_frozen_dependency_is_rejected(self):
        Path(self.plan['points'][0]['signal_contract']).write_text('{"changed":true}\n')
        with self.assertRaises(ValueError):validate_plan(self.plan,'one')

    def test_unknown_duplicate_or_unprepared_points_are_rejected(self):
        with self.assertRaises(ValueError):validate_plan(self.plan,'other')
        p=copy.deepcopy(self.plan);p['points'].append(copy.deepcopy(p['points'][0]))
        with self.assertRaises(ValueError):validate_plan(p,'one')
        p=copy.deepcopy(self.plan);p['prepared']=False
        with self.assertRaises(ValueError):validate_plan(p,'one')


if __name__=='__main__':unittest.main()
