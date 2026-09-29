from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from generation_publication import finalize, sha256, verify


class GenerationPublicationTest(unittest.TestCase):
    def test_audited_root_and_metadata_are_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'events.root'
            path.write_bytes(b'audited GEN output')
            metadata = Path(directory)/'audit.json'
            metadata.write_text('{"schema":"shift-production-gen-v1",'
                                '"input":"/tmp/original.root",'
                                '"input_sha256":"'+sha256(path)+'"}')
            record = finalize(metadata, path)
            verify(record, path)
            self.assertEqual(record['audit_input'], '/tmp/original.root')
            self.assertEqual(record['input'], str(path))
            path.write_bytes(b'corrupt output')
            with self.assertRaisesRegex(ValueError, 'validated pair'):
                verify(record, path)

    def test_wrong_published_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'events.root'
            path.write_bytes(b'different')
            metadata = Path(directory)/'audit.json'
            metadata.write_text('{"schema":"shift-production-gen-v1",'
                                '"input":"/tmp/original.root",'
                                '"input_sha256":"'+'a'*64+'"}')
            with self.assertRaisesRegex(ValueError, 'differs'):
                finalize(metadata, path)


if __name__ == '__main__':
    unittest.main()
