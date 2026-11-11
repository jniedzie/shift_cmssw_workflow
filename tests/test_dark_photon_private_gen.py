# Code style: AI-code. AI-generated failed private-GEN retention tests.
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_dark_photon_private_gen import preserve_failed_gen


class FailedGenRetention(unittest.TestCase):
    def test_archive_is_verified_before_the_only_root_payload_can_be_removed(self):
        self.check_verified_retention('/eos/user/j/jniedzie/shift_cmssw_support/test','root://eosuser.cern.ch')

    def test_group_archive_uses_the_group_instance_and_preserves_exposure(self):
        self.check_verified_retention('/eos/cms/store/group/phys_exotica/xtracks/jeremi/shift_cmssw/support/test',
                                      'root://eoscms.cern.ch')

    def check_verified_retention(self,destination,host):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);gen=root/'gen';gen.mkdir()
            (gen/'gen.root').write_bytes(b'sole source payload')
            (gen/'contract.json').write_text('{"seed":42}')
            remote={}
            def command(argv,timeout):
                if argv[0].endswith('xrdfs'):
                    self.assertEqual(argv[1],host)
                    return
                source,destination=argv[-2:]
                if source.startswith('root://'):
                    shutil.copy2(remote[source],destination)
                else:
                    saved=root/'remote.tar.gz';shutil.copy2(source,saved);remote[destination]=saved
            def write(path,value):path.write_text(json.dumps(value))
            stage=SimpleNamespace(HOST='root://test',command=command,
                sha256=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest(),write_new=write)
            row=dict(point='m2_prompt',run_number=101,seed=42,gen_directory=str(root/'canonical/gen'))
            Path(row['gen_directory']).parent.mkdir()
            result=preserve_failed_gen(gen,root,dict(private_gen_failure_base=
                destination),row,stage)
            self.assertEqual(result['sha256'],result['readback_sha256'])
            self.assertFalse(result['exposure_credited'])
            self.assertTrue(all(value.startswith(host+'/') for value in remote))
            with tarfile.open(root/'remote.tar.gz') as archive:
                self.assertEqual(archive.extractfile('gen/gen.root').read(),b'sole source payload')
            self.assertTrue((gen/'gen.root').exists())

    def test_non_support_destination_cannot_delete_or_archive_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);gen=root/'gen';gen.mkdir();(gen/'gen.root').write_bytes(b'keep')
            with self.assertRaises(ValueError):
                preserve_failed_gen(gen,root,dict(private_gen_failure_base='/eos/user/j/jniedzie/shift_cmssw'),
                                    dict(run_number=1,seed=2),None)
            self.assertEqual((gen/'gen.root').read_bytes(),b'keep')


if __name__=='__main__':
    unittest.main()
