# Code style: AI-code.
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_shift_qcd_production_source import read_source_authority, array_source_request
from run_shift_smart_sm_detector import quota_row


class ProductionSourceTest(unittest.TestCase):
    def test_array_expansion_is_fixed_and_keeps_all_physics_settings(self):
        request = dict(source_request=dict(family='qcd',bounds=[5.,10.],events_or_trials=1000),
            qcd_gen_filter_policy={'original':'unchanged'}, source_array=dict(count=2,index_start=50,
                id_prefix='qcd_full_',run_base=740300000,seed_base=826000000,
                publication_parent='/eos/cms/store/group/phys_exotica/xtracks/jeremi/shift_cmssw/support/full/sources'))
        result = array_source_request(request,1)
        self.assertEqual(result['source_request'],dict(family='qcd',bounds=[5.,10.],
            events_or_trials=1000,id='qcd_full_000051',run=740300051,seed=826002550))
        self.assertEqual(result['qcd_gen_filter_policy'],request['qcd_gen_filter_policy'])
        self.assertIn('source_array',request)
        with self.assertRaisesRegex(ValueError,'frozen range'):
            array_source_request(request,2)
    def test_authority_return_preserves_census_and_rejects_wrong_normalization(self):
        source = dict(source_id='qcd_source1', full_source_trials=1000)
        authority = dict(complete=True, primary_preflight=dict(complete=True), source=source,
            selected_source=dict(event_ids=[[1,1,2]], weights=[1.e-15], selected_indices=[0],
                sampling_probabilities=[1.e-5]))
        data = gzip.compress(json.dumps(authority).encode())
        receipt = dict(source=source, source_authority=dict(name='source_authority.json.gz',
            bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with tarfile.open(directory/'return_payload.tar.gz', 'w:gz') as archive:
                member = tarfile.TarInfo('source_authority.json.gz')
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            self.assertEqual(read_source_authority(directory, receipt), authority)
            receipt['source'] = dict(source, full_source_trials=10)
            with self.assertRaisesRegex(ValueError, 'matching'):
                read_source_authority(directory, receipt)

    def test_group_quota_uses_enforced_pool_and_never_unlimited_user_row(self):
        rows = [dict(gid='phys_exotica', space='/eos/cms/store/group/phys_exotica/',
                     maxlogicalbytes='0', maxfiles='0'),
                dict(gid='ALL', space='/eos/cms/store/group/phys_exotica/',
                     maxlogicalbytes='500000000000000', maxfiles='100000000')]
        self.assertEqual(quota_row(rows, rows[0]['space']+'xtracks/a', False)['gid'], 'ALL')
        with self.assertRaisesRegex(ValueError, 'finite enforced'):
            quota_row(rows[:1], rows[0]['space']+'xtracks/a', False)


if __name__ == '__main__':
    unittest.main()
