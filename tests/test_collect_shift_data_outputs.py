# Code style: AI-code.
"""Checks that staged data bind the original request and returned evidence."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import struct
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import collect_shift_data_outputs as collector


class DataCollectorTest(unittest.TestCase):
    def fixture(self, directory, *, mismatch=False, version2=False):
        request = dict(schema='shift-data-reconstruction-job-v1', directory='/afs/example/job',
                       events=[dict(run=369943, luminosityBlock=356, event=2**63+1)],
                       source_offset=20, source_url='root://example/input.root',
                       batch_name='DATA_Run2023D_ZeroBias_SHIFTReco_20261118_test')
        if version2:
            request.update(schema='shift-data-reconstruction-job-v2', dataset='/Muon0/Run2023D-v1/RAW',
                           era='Run2023D', batch_name='DATA_Run2023D_Muon0_SHIFTReco_20261118_test',
                           source_url='root://example//store/data/Run2023D/Muon0/RAW/v1/fixture.root')
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
        if version2:
            result.update(dataset=request['dataset'], era=request['era'], source_url=request['source_url'],
                          original_runs=[369943])
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

    def test_new_dataset_provenance_binds_to_native_request(self):
        with tempfile.TemporaryDirectory() as task:
            directory = Path(task)
            digest, identity = self.fixture(directory, version2=True)
            staged = collector.stage_output(directory, digest, audit=lambda path, ids: identity)
            self.assertEqual(staged['dataset'], '/Muon0/Run2023D-v1/RAW')
            self.assertEqual(staged['original_runs'], [369943])
            result = json.loads((directory / 'result.json').read_text())
            result['dataset'] = '/Muon1/Run2023D-v1/RAW'
            (directory / 'result.json').write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, 'source provenance'):
                collector.stage_output(directory, digest, audit=lambda path, ids: identity)

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

    def publication_fixture(self):
        census = dict(header_only=True, all_headers_is_real_data=True,
                      events=[dict(source_entry=i, run=369943, luminosityBlock=356,
                                   event=2**63+i) for i in range(40)])
        digest = hashlib.sha256(b''.join(struct.pack('>IIQ', 369943, 356, 2**63+i)
                                        for i in range(20))).hexdigest()
        base = dict(schema='shift-data-staged-publication-inputs-v1',
                    dataset='/ZeroBias/Run2023D-v1/RAW', era='Run2023D', run=369943,
                    expected_unique_events=20, expected_identity_sha256=digest,
                    inputs=[dict(expected_event_count=20)])
        row = dict(path='fixture.root', origin_uri='condor:123.4', root_uuid='fixture',
                   sha256='fixture-sha', bytes=1, producer_receipt=dict(path='result.json', sha256='x'),
                   expected_event_count=10, source_offset=20, native_exit_verified=True,
                   archive_sha256='private-collector-evidence')
        return base, dict(ready=[row]), census

    def test_publication_inventory_credits_only_owned_unique_originals(self):
        base, staged, census = self.publication_fixture()
        inventory = collector.build_publication_inventory(base, staged, census)
        expected = hashlib.sha256(b''.join(struct.pack('>IIQ', 369943, 356, 2**63+i)
                                          for i in range(30))).hexdigest()
        self.assertEqual(inventory['expected_unique_events'], 30)
        self.assertEqual(inventory['expected_identity_sha256'], expected)
        self.assertNotIn('archive_sha256', inventory['inputs'][-1])

    def test_publication_inventory_rejects_overlap_and_relabelled_prior_events(self):
        for offsets in ([20, 20], [20, 25], [10]):
            with self.subTest(offsets=offsets):
                base, staged, census = self.publication_fixture()
                staged['ready'] = [dict(staged['ready'][0], source_offset=offset) for offset in offsets]
                with self.assertRaisesRegex(ValueError, 'ownership'):
                    collector.build_publication_inventory(base, staged, census)

    def cohort_fixture(self):
        dataset = '/Muon0/Run2023D-v1/RAW'
        url = 'root://example//store/data/Run2023D/Muon0/RAW/v1/fixture.root'
        census = dict(schema='shift-data-input-header-census-v2', header_only=True,
                      all_headers_is_real_data=True, era='Run2023D', unique_originals=100,
                      events=[dict(globalcohort_entry=i, run=369943, luminosityBlock=99,
                                   event=2**63+i, source_dataset=dataset, source_url=url) for i in range(100)])
        row = dict(path='fixture.root', origin_uri=url, root_uuid='fixture', sha256='fixture-sha', bytes=1,
                   producer_receipt=dict(path='result.json', sha256='x'), expected_event_count=10,
                   source_offset=20, native_exit_verified=True, dataset=dataset, era='Run2023D',
                   original_runs=[369943])
        return dict(ready=[row]), census

    def test_new_cohort_inventory_uses_exact_completed_header_subset(self):
        staged, census = self.cohort_fixture()
        inventory = collector.build_cohort_publication_inventory(staged, census, 10000)
        expected = hashlib.sha256(b''.join(struct.pack('>IIQ', 369943, 99, 2**63+i)
                                          for i in range(20, 30))).hexdigest()
        self.assertEqual(inventory['expected_identity_sha256'], expected)
        self.assertEqual(inventory['expected_unique_events'], 10)
        self.assertEqual(inventory['event_budget'], 10000)
        self.assertEqual(inventory['datasets'], ['/Muon0/Run2023D-v1/RAW'])

    def test_new_cohort_rejects_overlapping_or_relabelled_responses(self):
        staged, census = self.cohort_fixture()
        staged['ready'].append(dict(staged['ready'][0], path='second.root'))
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            collector.build_cohort_publication_inventory(staged, census, 10000)
        staged, census = self.cohort_fixture()
        staged['ready'][0]['dataset'] = '/Muon1/Run2023D-v1/RAW'
        with self.assertRaisesRegex(ValueError, 'provenance'):
            collector.build_cohort_publication_inventory(staged, census, 10000)


if __name__ == '__main__':
    unittest.main()
