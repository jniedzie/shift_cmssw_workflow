# Code style: AI-code.
"""AI-generated checks for partial-data luminosity and exact event ownership."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import audit_shift_data_luminosity as audit


class DataLuminosityTest(unittest.TestCase):
    def fixture(self, complete=False):
        datasets = ['/Muon0/Run2023D-v1/RAW', '/Tau/Run2023D-v1/RAW']
        sources = [dict(dataset=dataset, source_url='root://example/source' + str(index),
                        headers_read=2 if index == 0 else 1, events_tree_entries=2)
                   for index, dataset in enumerate(datasets)]
        events = [dict(globalcohort_entry=index, source_index=0 if index < 2 else 1,
                       source_dataset=datasets[0 if index < 2 else 1],
                       source_url=sources[0 if index < 2 else 1]['source_url'],
                       run=369943, luminosityBlock=10 if index == 0 else 11,
                       event=2**63 + index + 1) for index in range(3)]
        census = dict(schema='shift-data-input-header-census-v2', header_only=True,
                      candidate_branches_read=False, all_headers_is_real_data=True,
                      unique_originals=3, era='Run2023D', sources=sources, events=events,
                      ordering='Fixed disk-file order, then original EDM entry')
        selected = [0, 1, 2] if complete else [0, 2]
        ready = []
        for offset in selected:
            source = sources[0 if offset < 2 else 1]
            ready.append(dict(path='/eos/example/canonical' + str(offset) + '.root',
                              sha256='a' * 64, root_uuid='synthetic-' + str(offset),
                              bytes=100, expected_event_count=1,
                              origin_uri=source['source_url'],
                              producer_receipt=dict(path='/afs/example/result' + str(offset) + '.json',
                                                    sha256='b' * 64),
                              native_exit_verified=True, dataset=source['dataset'],
                              source_offset=offset, era='Run2023D'))
        rows = [(events[index]['run'], events[index]['luminosityBlock'], events[index]['event'])
                for index in selected]
        digest = hashlib.sha256(b''.join(struct.pack('>IIQ', *row) for row in sorted(rows))).hexdigest()
        inventory = dict(schema='shift-data-staged-publication-inputs-v2', era='Run2023D',
                         datasets=datasets, runs=[369943], expected_unique_events=len(selected),
                         expected_identity_sha256=digest,
                         inputs=[{key: row[key] for key in audit.MATCHED_FIELDS} for row in ready])
        staged = dict(schema='shift-data-verified-staged-outputs-v1',
                      candidate_branches_read=False, completed_originals=len(selected), ready=ready)
        certification = {'369943': [[10, 11]]}
        return inventory, staged, census, certification

    def test_partial_frame_exports_exact_unique_mask_and_separate_denominators(self):
        result, mask, selected = audit.audit_headers(*self.fixture())
        self.assertEqual(mask, {'369943': [[10, 11]]})
        self.assertEqual(selected, {(369943, 10), (369943, 11)})
        self.assertEqual(result['assigned_frame']['original_events'], 3)
        self.assertEqual(result['assigned_frame']['partially_scanned_source_files'], 1)
        self.assertEqual(result['published']['original_events'], 2)
        self.assertEqual(result['published']['pending_assigned_originals'], 1)
        self.assertFalse(result['published']['complete_for_assigned_frame'])
        self.assertEqual(result['published']['absolute_lumisection_completeness'], 'unknown')
        self.assertTrue(result['published_lumisections'][0]['complete_for_assigned_frame'])
        self.assertFalse(result['published_lumisections'][1]['complete_for_assigned_frame'])

    def test_all_assigned_originals_returned_does_not_qualify_mc_normalization(self):
        result, _, _ = audit.audit_headers(*self.fixture(complete=True))
        self.assertTrue(result['published']['complete_for_assigned_frame'])
        self.assertEqual(result['published']['absolute_lumisection_completeness'], 'unknown')
        normalization = audit.mc_normalization()
        self.assertIsNone(normalization['value_pb_inverse'])
        self.assertFalse(normalization['qualified_for_mc_scaling'])

    def test_overlapping_source_ranges_cannot_double_count_exposure(self):
        inventory, staged, census, certification = self.fixture()
        staged['ready'][1]['source_offset'] = 0
        with self.assertRaisesRegex(ValueError, 'Duplicate published original ownership'):
            audit.audit_headers(inventory, staged, census, certification)

    def test_repeated_published_paths_are_rejected(self):
        inventory, staged, census, certification = self.fixture()
        inventory['inputs'].append(copy.deepcopy(inventory['inputs'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate published path'):
            audit.audit_headers(inventory, staged, census, certification)

    def test_matching_published_path_cannot_relabel_root_metadata(self):
        for key, value in [('sha256', 'c' * 64), ('root_uuid', 'changed'), ('bytes', 101),
                           ('expected_event_count', 2)]:
            inventory, staged, census, certification = self.fixture()
            inventory['inputs'][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'ROOT metadata differ'):
                audit.audit_headers(inventory, staged, census, certification)

    def test_wrong_identity_and_uncertified_headers_fail(self):
        inventory, staged, census, certification = self.fixture()
        census['events'][2]['event'] += 1
        with self.assertRaisesRegex(ValueError, 'identity digest differs'):
            audit.audit_headers(inventory, staged, census, certification)
        inventory, staged, census, certification = self.fixture()
        certification['369943'] = [[10, 10]]
        with self.assertRaisesRegex(ValueError, 'outside certified'):
            audit.audit_headers(inventory, staged, census, certification)

    def test_duplicate_original_identity_is_not_repaired_by_distinct_source_offsets(self):
        inventory, staged, census, certification = self.fixture()
        for key in ('run', 'luminosityBlock', 'event'):
            census['events'][2][key] = census['events'][0][key]
        with self.assertRaisesRegex(ValueError, 'Duplicate assigned original identity'):
            audit.audit_headers(inventory, staged, census, certification)

    def csv(self, path, rows):
        path.write_text('#Data tag: synthetic, Norm tag: synthetic\n'
                        '#run:fill,ls,time,beamstatus,E(GeV),delivered(/pb),recorded(/pb),avgpu,source\n'
                        + ''.join('369943:1,' + row + ',time,STABLE BEAMS,6800,1.0,0.8,50,synthetic\n'
                                  for row in rows)
                        + '#Summary:\n#nfill,nrun,nls,totls,totbeamson,totdelivered(/pb),totrecorded(/pb)\n'
                        '1,1,2,2,2,2.0,1.6\n')

    def test_bril_unique_lumisection_sum_is_coverage_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bril.csv'
            self.csv(path, ['10:10', '11:11'])
            result = audit.bril_coverage(path, {(369943, 10), (369943, 11)})
            self.assertAlmostEqual(result['recorded_pb_inverse'], 1.6)
            self.assertEqual(result['status'], 'coverage_only')
            self.assertFalse(result['use_for_mc_scaling'])

    def test_bril_uses_cms_lumisection_not_luminometer_counter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bril.csv'
            self.csv(path, ['1:10', '2:11'])
            result = audit.bril_coverage(path, {(369943, 10), (369943, 11)})
            self.assertEqual(result['lumisections'], 2)
            with self.assertRaisesRegex(ValueError, 'exact published lumisection mask'):
                audit.bril_coverage(path, {(369943, 1), (369943, 2)})

    def test_duplicate_missing_extra_and_bad_bril_rows_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bril.csv'
            for rows in (['10:10', '10:10'], ['10:10'], ['10:10', '11:11', '12:12']):
                self.csv(path, rows)
                with self.subTest(rows=rows), self.assertRaises(ValueError):
                    audit.bril_coverage(path, {(369943, 10), (369943, 11)})
            self.csv(path, ['10:10', '11:11'])
            path.write_text(path.read_text().replace('1.0,0.8', 'nan,0.8', 1))
            with self.assertRaisesRegex(ValueError, 'Nonfinite'):
                audit.bril_coverage(path, {(369943, 10), (369943, 11)})
            self.csv(path, ['10:10', '11:11'])
            path.write_text(path.read_text().replace('recorded(/pb)', 'recorded(/ub)'))
            with self.assertRaisesRegex(ValueError, 'in /pb'):
                audit.bril_coverage(path, {(369943, 10), (369943, 11)})

    def test_cli_binds_frozen_headers_and_writes_only_coverage_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            inventory, staged, census, certification = self.fixture()
            for name, value in zip(('inventory', 'staged', 'census', 'certification'),
                                   (inventory, staged, census, certification)):
                (task / (name + '.json')).write_text(json.dumps(value))
            census['certification_sha256'] = audit.sha256(task / 'certification.json')
            (task / 'census.json').write_text(json.dumps(census))
            (task / 'normtag.json').write_text('{"synthetic": []}')
            self.csv(task / 'bril.csv', ['10:10', '11:11'])
            script = Path(__file__).parents[1] / 'scripts/audit_shift_data_luminosity.py'
            command = [sys.executable, str(script)]
            for name in ('inventory', 'staged', 'census', 'certification'):
                command += ['--' + name, str(task / (name + '.json'))]
            command += ['--census-sha256', audit.sha256(task / 'census.json'),
                        '--output', str(task / 'audit.json'), '--lumimask-output', str(task / 'mask.json'),
                        '--bril-csv', str(task / 'bril.csv'), '--normtag', str(task / 'normtag.json')]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((task / 'audit.json').read_text())
            self.assertIsNone(output['luminosity']['mc_normalization']['value_pb_inverse'])
            self.assertEqual(output['luminosity']['covered_lumisections']['recorded_pb_inverse'], 1.6)
            self.assertEqual(output['inputs']['normtag']['sha256'], audit.sha256(task / 'normtag.json'))
            self.assertFalse(output['ROOT_files_read'])
            self.assertEqual((task / 'audit.json').stat().st_mode & 0o777, 0o600)
            before = (task / 'audit.json').read_bytes()
            (task / 'certification.json').write_text('{"369943": [[1, 999]]}')
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Frozen certification SHA-256 differs', result.stderr)
            self.assertEqual((task / 'audit.json').read_bytes(), before)
            (task / 'census.json').write_text('{}')
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Frozen census SHA-256 differs', result.stderr)
            self.assertEqual((task / 'audit.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
