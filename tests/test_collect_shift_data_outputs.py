# Code style: AI-code.
"""Checks that staged data bind the original request and returned evidence."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import collect_shift_data_outputs as collector


class DataCollectorTest(unittest.TestCase):
    def fixture(self, directory, *, mismatch=False):
        request = dict(schema='shift-data-reconstruction-job-v1', directory='/afs/example/job',
                       events=[dict(run=369943, luminosityBlock=356, event=2**63+1)],
                       source_offset=20, source_url='root://example/input.root',
                       batch_name='DATA_Run2023D_ZeroBias_SHIFTReco_20261118_test')
        path = directory / 'request.json'
        path.write_text(json.dumps(request))
        digest = collector.sha256(path)
        payload = b'synthetic ROOT bytes'
        identity = dict(root_uuid='fixture', events=1, unique_events=1,
                        ordered_original_ids_match=True, candidate_branches_read=False)
        result = dict(schema='shift-data-worker-result-v1', request_sha256=digest,
                      input_origin='collision-data', complete=True, exit_code=0,
                      native=dict(exit_code=0), original_events=1, candidate_branches_read=False,
                      output=dict(sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload),
                                  root_uuid='fixture'), identity_audit=identity)
        (directory / 'result.json').write_text(json.dumps(result))
        with tarfile.open(directory / 'return_payload.tar.gz', 'w:gz') as archive:
            for name, data in [('result.json', b'{}' if mismatch else json.dumps(result).encode()),
                               ('restricted_nano.root', payload)]:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return digest, identity

    def test_completed_exact_response_is_staged_once(self):
        with tempfile.TemporaryDirectory() as task:
            directory = Path(task)
            digest, identity = self.fixture(directory)
            first = collector.stage_output(directory, digest, audit=lambda path, ids: identity)
            second = collector.stage_output(directory, digest, audit=lambda path, ids: identity)
            self.assertEqual(first, second)
            self.assertEqual(first['expected_event_count'], 1)

    def test_archive_cannot_relabel_another_producer(self):
        with tempfile.TemporaryDirectory() as task:
            directory = Path(task)
            digest, identity = self.fixture(directory, mismatch=True)
            with self.assertRaisesRegex(ValueError, 'receipts disagree'):
                collector.stage_output(directory, digest, audit=lambda path, ids: identity)
            self.assertFalse((directory / 'restricted_nano.root').exists())

    def test_existing_canonical_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as task:
            directory = Path(task)
            digest, identity = self.fixture(directory)
            (directory / 'restricted_nano.root').write_bytes(b'existing changed bytes')
            with self.assertRaisesRegex(ValueError, 'canonical ROOT changed'):
                collector.stage_output(directory, digest, audit=lambda path, ids: identity)
            self.assertEqual((directory / 'restricted_nano.root').read_bytes(), b'existing changed bytes')

    def test_existing_valid_root_does_not_hide_a_corrupt_archive(self):
        with tempfile.TemporaryDirectory() as task:
            directory = Path(task)
            digest, identity = self.fixture(directory)
            collector.stage_output(directory, digest, audit=lambda path, ids: identity)
            archive_path = directory / 'return_payload.tar.gz'
            with tarfile.open(archive_path) as archive:
                files = [(member.name, archive.extractfile(member).read())
                         for member in archive.getmembers()]
            with tarfile.open(archive_path, 'w:gz') as archive:
                for name, data in files:
                    if name == 'restricted_nano.root':
                        data = b'x' * len(data)
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            with self.assertRaisesRegex(ValueError, 'Archived ROOT checksum mismatch'):
                collector.stage_output(directory, digest, audit=lambda path, ids: identity)

    def test_terminal_success_must_belong_to_exact_original_producer(self):
        with tempfile.TemporaryDirectory() as task:
            directory = Path(task)
            (directory / 'events.log').write_text('005 (123.004.000) 01/01 00:00:00 Job terminated.\n'
                                                '    (1) Normal termination (return value 0)\n...\n')
            self.assertTrue(collector.native_success(directory, 123, 4))
            self.assertFalse(collector.native_success(directory, 123, 5))


if __name__ == '__main__':
    unittest.main()
