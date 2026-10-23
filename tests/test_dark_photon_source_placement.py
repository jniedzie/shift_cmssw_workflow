# Code style: AI-code. AI-generated direct-DY jaw source preservation tests.
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_source_placement import place_contract, record_runtime, digest
from test_shift_jaw_source import profile


class DirectSourceTests(unittest.TestCase):
    def test_positions_cover_rejected_slots_and_leave_lifetime_rates_unchanged(self):
        contract=dict(seed=42,run_number=101,requested_events=10,maximum_framework_events=100,
            proper_ctau_mm=451.,epsilon=1.e-6,production_rate_correction=1.e-8)
        original=copy.deepcopy(contract)
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'jaw.json';path.write_text(json.dumps(profile()))
            placed=place_contract(contract,path)
            self.assertEqual(placed,place_contract(contract,path))
            self.assertEqual(contract,original)
            self.assertEqual(len(placed['source_placement']['event_positions']),100)
            self.assertEqual(placed['source_placement']['event_positions'][-1]['event_id'],[101,1,100])
            for key in ('proper_ctau_mm','epsilon','production_rate_correction'):
                self.assertEqual(placed[key],contract[key])
            self.assertFalse(placed['source_placement']['material_or_depth_weight_applied'])
            with self.assertRaisesRegex(ValueError,'twice'):
                place_contract(placed,path)

    def test_private_runtime_requires_actual_matching_archive_not_a_git_guess(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive=root/'runtime.tar';archive.write_bytes(b'coherent runtime')
            authority=root/'provenance.json'
            provenance=dict(schema='shift-bsm-gen-runtime-provenance-v1',
                archive_bytes=archive.stat().st_size,archive_sha256=digest(archive),scram_arch='test',
                cmssw_git_head='original-qualified-revision',cmssw_git_status='qualified')
            authority.write_text(json.dumps(provenance))
            output=root/'out';output.mkdir()
            manifest={}
            with patch.dict('os.environ',SCRAM_ARCH='test'):
                record_runtime(manifest,root,output,authority,archive)
                self.assertEqual(manifest['cmssw_git_head'],'original-qualified-revision')
                archive.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'differs'):
                    record_runtime({},root,output,authority,archive)


if __name__=='__main__':
    unittest.main()
