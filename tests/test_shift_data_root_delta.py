# Code style: AI-code.
import hashlib
from pathlib import Path
import random
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import shift_data_root_delta as codec


def pin(data):
    return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


@unittest.skipUnless(Path(codec.ZSTD).is_file(), 'Requires the deployed zstd CLI')
class RootDeltaTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)
        self.reference = path / 'reference.root'
        self.original = path / 'native.root'
        self.encoded = path / 'native.root.zst'
        self.decoded = path / 'decoded.root'
        self.reference.write_bytes(b'root reference\x00' * 8192)
        self.payload = self.reference.read_bytes() + b'event row\x00\xff'
        self.original.write_bytes(self.payload)

    def encode(self):
        return codec.encode(self.original, self.reference, self.encoded,
                            reference_pin=pin(self.reference.read_bytes()), root_pin=pin(self.payload))

    def test_exact_roundtrip_and_existing_decoded_file(self):
        receipt = self.encode()
        self.assertEqual(receipt['encoded'], pin(self.encoded.read_bytes()))
        self.assertEqual(codec.decode(self.encoded, self.reference, self.decoded, receipt), pin(self.payload))
        self.assertEqual(self.decoded.read_bytes(), self.payload)
        self.assertEqual(self.decoded.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            codec.decode(self.encoded, self.reference, self.decoded, receipt)
        self.assertEqual(self.decoded.read_bytes(), self.payload)

    def test_wrong_reference_or_packed_bytes_leave_no_output(self):
        receipt = self.encode()
        reference = self.reference.read_bytes()
        self.reference.write_bytes(b'changed' + reference[7:])
        with self.assertRaisesRegex(ValueError, 'reference ROOT differs'):
            codec.decode(self.encoded, self.reference, self.decoded, receipt)
        self.assertFalse(self.decoded.exists())
        self.reference.write_bytes(reference)
        self.encoded.write_bytes(self.encoded.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'packed ROOT differs'):
            codec.decode(self.encoded, self.reference, self.decoded, receipt)
        self.assertFalse(self.decoded.exists())

    def test_encoded_limit_is_enforced_even_for_incompressible_bytes(self):
        self.payload = random.Random(47).randbytes(codec.ENCODED_LIMIT + 4096)
        self.original.write_bytes(self.payload)
        with self.assertRaisesRegex(ValueError, 'output byte limit'):
            self.encode()
        self.assertFalse(self.encoded.exists())
        self.assertEqual(self.original.read_bytes(), self.payload)

    def test_decode_is_bounded_by_the_receipt(self):
        receipt = self.encode()
        receipt['root']['bytes'] -= 1
        with self.assertRaisesRegex(ValueError, 'output byte limit'):
            codec.decode(self.encoded, self.reference, self.decoded, receipt)
        self.assertFalse(self.decoded.exists())


if __name__ == '__main__':
    unittest.main()
