# Code style: AI-code. AI-generated tests of nested frozen runtime integrity.
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_shift_stream_tools import prepare, read_archive, validate


class StreamToolsTest(unittest.TestCase):
    def test_stale_nested_manifest_and_duplicate_config_are_resealed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / 'original.tar.gz'
            files = {'tea/configs/paths.py': b'correct config',
                     'tea/bin/paths.py': b'old config', 'tea/bin/program': b'compiled bytes',
                     'runtimes.json': b'frozen runtime pins',
                     'tea/source_manifest.json': json.dumps({'relocation_prefixes': {'runtime': 'old'}}).encode(),
                     'source_manifest.json': b'{}'}
            with tarfile.open(original, 'w:gz') as archive:
                for name, data in files.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            before = original.read_bytes()
            replacement = root / 'helper.py'
            replacement.write_bytes(b'validated helper')
            output = root / 'new.tar.gz'
            prepare(original, output, {'tea/utils/helper.py': str(replacement)})
            result, _ = read_archive(output)
            validate(result)
            self.assertEqual(original.read_bytes(), before)
            self.assertEqual(result['tea/bin/program'], files['tea/bin/program'])
            self.assertEqual(result['runtimes.json'], files['runtimes.json'])
            self.assertEqual(result['tea/bin/paths.py'], files['tea/configs/paths.py'])
            inner = json.loads(result['tea/source_manifest.json'])
            self.assertEqual(inner['utils/helper.py']['sha256'], hashlib.sha256(replacement.read_bytes()).hexdigest())
            result['tea/utils/helper.py'] = b'tampered'
            with self.assertRaisesRegex(ValueError, 'checksum differs'):
                validate(result)
            with self.assertRaisesRegex(ValueError, 'new tools archive'):
                prepare(original, output, {})

    def test_unsafe_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'unsafe.tar.gz'
            with tarfile.open(archive, 'w:gz') as target:
                member = tarfile.TarInfo('../outside')
                member.size = 0
                target.addfile(member, io.BytesIO())
            with self.assertRaisesRegex(ValueError, 'Unsafe tools archive'):
                read_archive(archive)


if __name__ == '__main__':
    unittest.main()
