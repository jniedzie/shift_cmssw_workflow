# Code style: AI-code.
"""AI-generated checks for complete input scanning and original-event ownership."""
import importlib.util
import hashlib
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('full_lumi_scanner',
                                             SCRIPTS / 'scan_shift_data_lumisections.py')
SCANNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCANNER)

MUON0 = '/Muon0/Run2023D-v1/RAW'
MUON1 = '/Muon1/Run2023D-v1/RAW'
LFN0 = '/store/data/Run2023D/Muon0/RAW/v1/000/369/943/00000/a.root'
LFN1 = '/store/data/Run2023D/Muon1/RAW/v1/000/369/943/00000/b.root'


def file(lfn):
    return dict(lfn=lfn, source_url='root://eoscms.cern.ch/' + lfn)


def event(lumi, number, real=True):
    return dict(run=369943, luminosityBlock=lumi, event=number, is_real_data=real)


class FullLumiHeaderScannerTest(unittest.TestCase):
    def catalog(self):
        return dict(schema='shift-data-full-lumisection-catalog-v1',
                    datasets=[MUON0, MUON1], lumimask={'369943': [[356, 357]]},
                    records=[
                        dict(run=369943, luminosityBlock=356, dataset=MUON0,
                             query_complete=True, files=[file(LFN0)]),
                        dict(run=369943, luminosityBlock=357, dataset=MUON0,
                             query_complete=True, files=[file(LFN0)]),
                        dict(run=369943, luminosityBlock=356, dataset=MUON1,
                             query_complete=True, files=[file(LFN1)]),
                        dict(run=369943, luminosityBlock=357, dataset=MUON1,
                             query_complete=True, files=[]),
                    ])

    def reader(self, rows=None, entry_counts=None):
        rows = rows or {LFN0: [event(355, 1), event(356, 2), event(356, 3), event(357, 4)],
                        LFN1: [event(356, 3), event(356, 5)]}
        opened, consumed = [], []

        def read(source):
            lfn = source['lfn']
            opened.append(lfn)
            metadata = dict(events_tree_entries=(entry_counts or {}).get(lfn, len(rows[lfn])),
                            root_uuid='original_uuid_' + Path(lfn).stem,
                            raw_collection_branches=['FEDRawDataCollection_rawDataCollector__HLT'])

            def headers():
                for row in rows[lfn]:
                    consumed.append((lfn, row['event']))
                    yield row

            return metadata, headers()

        return read, opened, consumed

    def scan(self, catalog=None, reader=None):
        if reader is None:
            reader = self.reader()[0]
        return SCANNER.scan_catalog(catalog or self.catalog(), {'369943': [[350, 400]]}, reader,
                                    catalog_sha256='a' * 64, certification_sha256='b' * 64)

    def test_every_file_is_read_to_eof_once_and_stream_overlap_is_owned_once(self):
        reader, opened, consumed = self.reader()
        result = self.scan(reader=reader)
        self.assertEqual(opened, [LFN0, LFN1])
        self.assertEqual(len(consumed), 6)
        self.assertEqual(result['unique_originals'], 4)
        self.assertEqual([row['event'] for row in result['events']], [2, 3, 5, 4])
        self.assertEqual(result['processed_identity_sha256'], hashlib.sha256(
            b'[[369943,356,2],[369943,356,3],[369943,356,5],[369943,357,4]]').hexdigest())
        self.assertEqual([row['globalcohort_entry'] for row in result['events']], list(range(4)))
        self.assertEqual(result['sources'][0]['accepted_originals'], 3)
        self.assertEqual(result['sources'][1]['accepted_originals'], 1)
        self.assertEqual(result['sources'][1]['duplicate_selected_headers'], 1)
        self.assertTrue(result['all_required_files_scanned_to_eof'])
        self.assertFalse(result['event_limit_applied'])
        self.assertFalse(result['prior_events_excluded'])
        self.assertFalse(result['product_handles_requested'])
        self.assertFalse(result['candidate_branches_read'])

    def test_more_than_one_thousand_selected_originals_are_never_truncated(self):
        catalog = self.catalog()
        catalog['datasets'] = [MUON0]
        catalog['records'] = catalog['records'][:2]
        reader, _, consumed = self.reader({LFN0: [event(356, i + 1) for i in range(1105)]})
        result = self.scan(catalog, reader)
        self.assertEqual(len(consumed), 1105)
        self.assertEqual(result['unique_originals'], 1105)
        self.assertEqual(result['sources'][0]['headers_read'], 1105)

    def test_early_eof_is_not_a_complete_source(self):
        reader, _, _ = self.reader(entry_counts={LFN0: 5})
        with self.assertRaisesRegex(ValueError, 'before all original headers'):
            self.scan(reader=reader)

    def test_iteration_beyond_metadata_count_fails(self):
        reader, _, _ = self.reader(entry_counts={LFN0: 3})
        with self.assertRaisesRegex(ValueError, 'exceeds its original entry count'):
            self.scan(reader=reader)

    def test_nondata_header_outside_selected_lumis_still_fails(self):
        reader, _, _ = self.reader({LFN0: [event(355, 1, real=False)], LFN1: []})
        with self.assertRaisesRegex(ValueError, 'non-collision-data'):
            self.scan(reader=reader)

    def test_missing_required_source_does_not_become_an_empty_file(self):
        def unavailable(source):
            raise OSError('Required source unavailable')
        with self.assertRaises(OSError):
            self.scan(reader=unavailable)

    def test_selected_lumi_discovered_in_an_uncatalogued_file_fails(self):
        catalog = self.catalog()
        catalog['records'][1]['files'] = []
        with self.assertRaisesRegex(ValueError, 'absent from its file catalog'):
            self.scan(catalog)

    def test_unknown_or_missing_catalog_query_prevents_any_source_read(self):
        for change in ('unknown', 'missing'):
            catalog = self.catalog()
            if change == 'unknown':
                catalog['records'][0]['query_complete'] = False
            else:
                catalog['records'].pop()
            reader, opened, _ = self.reader()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.scan(catalog, reader)
            self.assertEqual(opened, [])

    def test_successfully_empty_catalog_lumi_remains_in_declared_mask(self):
        catalog = self.catalog()
        for row in catalog['records']:
            row['files'] = []
        reader, opened, consumed = self.reader()
        result = self.scan(catalog, reader)
        self.assertEqual(result['selected_lumis'], {'369943': [[356, 357]]})
        self.assertEqual(result['selected_lumisection_count'], 2)
        self.assertEqual(result['unique_originals'], 0)
        self.assertEqual(result['sources'], [])
        self.assertEqual(opened, [])
        self.assertEqual(consumed, [])
        self.assertTrue(result['all_required_files_scanned_to_eof'])


if __name__ == '__main__':
    unittest.main()
