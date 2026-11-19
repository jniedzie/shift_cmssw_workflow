# Code style: AI-code.
"""Complete-LS halo selection keeps unknowns and exact original ownership."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('halo_scanner', SCRIPTS / 'scan_shift_data_beam_halo.py')
SCANNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCANNER)

MUON0 = '/Muon0/Run2023D-PromptReco-v1/AOD'
MUON1 = '/Muon1/Run2023D-PromptReco-v1/AOD'
LFN0 = '/store/data/Run2023D/Muon0/AOD/PromptReco-v1/000/369/943/a.root'
LFN1 = '/store/data/Run2023D/Muon1/AOD/PromptReco-v1/000/369/943/b.root'


def event(lumi, number, decision='selected'):
    return dict(run=369943, luminosityBlock=lumi, event=number, is_real_data=True,
                halo=dict(decision=decision))


class HaloCensusTest(unittest.TestCase):
    def catalog(self):
        rows = []
        for dataset, lfn in [(MUON0, LFN0), (MUON1, LFN1)]:
            for lumi in (356, 357):
                rows.append(dict(run=369943, luminosityBlock=lumi, dataset=dataset,
                                 query_complete=True, files=[dict(lfn=lfn, source_url='root://cms/' + lfn)]))
        return dict(schema='shift-data-full-lumisection-catalog-v1', data_tier='AOD',
                    datasets=[MUON0, MUON1], lumimask={'369943': [[356, 357]]}, records=rows)

    def reader(self, rows=None, counts=None):
        rows = rows or {LFN0: [event(355, 1), event(356, 2), event(356, 3, 'rejected'), event(357, 4)],
                        LFN1: [event(356, 2), event(356, 5)]}
        consumed = []

        def read(source, selected, policy):
            lfn = source['lfn']
            metadata = dict(root_uuid='uuid_' + Path(lfn).stem,
                            events_tree_entries=(counts or {}).get(lfn, len(rows[lfn])))

            def headers():
                for row in rows[lfn]:
                    consumed.append(row)
                    yield row

            return metadata, headers()

        return read, consumed

    def scan(self, catalog=None, reader=None, **kwargs):
        return SCANNER.scan_catalog(catalog or self.catalog(), {'369943': [[350, 400]]},
                                    reader or self.reader()[0], catalog_sha256='a'*64,
                                    certification_sha256='b'*64, **kwargs)

    def test_all_headers_and_originals_survive_selection_accounting(self):
        reader, consumed = self.reader()
        result = self.scan(reader=reader)
        self.assertEqual(len(consumed), 6)
        self.assertEqual((result['unique_originals'], result['selected'], result['rejected'], result['unknown']),
                         (4, 3, 1, 0))
        self.assertEqual(result['selected_identities'], [[369943, 356, 2], [369943, 356, 5], [369943, 357, 4]])
        self.assertEqual(result['duplicate_observations'], 1)
        self.assertTrue(result['flag_census_closed'])
        self.assertFalse(result['candidate_branches_read'])
        self.assertEqual(result['product_handles_requested'], ['reco::BeamHaloSummary'])
        self.assertEqual(result['events'][0]['occurrences'][0]['source_index'], 0)
        self.assertEqual(len(result['events'][0]['occurrences']), 2)

    def test_unknown_product_and_conflicting_duplicates_never_become_rejections(self):
        rows = {LFN0: [event(356, 2), event(356, 3, 'unknown')], LFN1: [event(356, 2, 'rejected')]}
        result = self.scan(reader=self.reader(rows)[0])
        self.assertEqual((result['selected'], result['rejected'], result['unknown']), (0, 0, 2))
        self.assertFalse(result['flag_census_closed'])
        self.assertEqual(result['events'][0]['duplicate_conflict'], 'conflicting_duplicate_flag')
        self.assertFalse(result['lumisections'][0]['flag_census_closed'])
        self.assertTrue(result['lumisections'][1]['flag_census_closed'])

    def test_nondata_outside_selected_lumi_fails(self):
        row = event(355, 1)
        row['is_real_data'] = False
        with self.assertRaisesRegex(ValueError, 'non-data'):
            self.scan(reader=self.reader({LFN0: [row], LFN1: []})[0])

    def test_early_eof_and_extra_headers_cannot_close(self):
        for count in (3, 5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.scan(reader=self.reader(counts={LFN0: count})[0])

    def test_unknown_or_missing_catalog_query_prevents_read(self):
        for change in ('missing', 'unknown'):
            catalog = self.catalog()
            if change == 'missing':
                catalog['records'].pop()
            else:
                catalog['records'][0]['query_complete'] = False
            reader, consumed = self.reader()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.scan(catalog, reader)
            self.assertFalse(consumed)

    def test_no_event_cap_even_above_one_thousand(self):
        reader, consumed = self.reader({LFN0: [event(356, number+1) for number in range(1105)], LFN1: []})
        result = self.scan(reader=reader)
        self.assertEqual(len(consumed), 1105)
        self.assertEqual(result['selected'], 1105)
        self.assertFalse(result['event_limit_applied'])

    def test_all_explicitly_empty_catalog_lumis_close_without_reader(self):
        catalog = self.catalog()
        for row in catalog['records']:
            row['files'] = []
        reader, consumed = self.reader()
        result = self.scan(catalog, reader)
        self.assertTrue(result['flag_census_closed'])
        self.assertEqual(result['unique_originals'], 0)
        self.assertFalse(consumed)

    def merge(self, shards):
        return SCANNER.merge_shards(self.catalog(), {'369943': [[350, 400]]}, shards,
                                    catalog_sha256='a'*64, certification_sha256='b'*64)

    def test_partial_shards_only_close_after_full_union_and_match_serial(self):
        first = self.scan(source_indices=[0])
        second = self.scan(source_indices=[1])
        self.assertFalse(first['flag_census_closed'])
        self.assertFalse(second['flag_census_closed'])
        self.assertEqual(self.merge([second, first]), self.scan())

    def test_merge_refuses_repeated_source_changed_policy_and_missing_event_rows(self):
        first, second = self.scan(source_indices=[0]), self.scan(source_indices=[1])
        with self.assertRaisesRegex(ValueError, 'Repeated'):
            self.merge([first, first])
        changed = copy.deepcopy(second)
        changed['policy']['required_CSC_report_entries'] = 0
        with self.assertRaisesRegex(ValueError, 'pins differ'):
            self.merge([first, changed])
        changed = copy.deepcopy(second)
        changed['events'].pop()
        with self.assertRaisesRegex(ValueError, 'accounting differ'):
            self.merge([first, changed])

    def raw(self):
        rows = [dict(run=369943, luminosityBlock=lumi, event=number,
                     source_dataset='/Muon0/Run2023D-v1/RAW', source_index=0,
                     source_entry=index, source_url='root://cms//store/a.root', globalcohort_entry=index)
                for index, (lumi, number) in enumerate([(356, 2), (356, 3), (356, 5), (357, 4)])]
        return dict(schema='shift-data-input-header-census-v2', all_required_files_scanned_to_eof=True,
                    header_only=True, all_headers_is_real_data=True, event_limit_applied=False,
                    prior_events_excluded=False, selected_lumis={'369943': [[356, 357]]},
                    datasets=['/Muon0/Run2023D-v1/RAW', '/Muon1/Run2023D-v1/RAW'],
                    unique_originals=4, events=rows)

    def test_raw_mapping_uses_exact_original_raw_owner_for_selected_events(self):
        result = SCANNER.validate_raw_mapping(self.scan(), self.raw())
        self.assertEqual(result['selected'], 3)
        self.assertEqual([row['source_entry'] for row in result['events']], [0, 2, 3])
        self.assertEqual([row['globalcohort_entry'] for row in result['events']], [0, 2, 3])
        self.assertTrue(result['complete'])

    def test_raw_mapping_refuses_missing_rejected_original_extra_original_and_partial_flags(self):
        for change in ('missing_rejected', 'extra', 'partial_flags', 'partial_raw'):
            census, raw = self.scan(), self.raw()
            if change == 'missing_rejected':
                raw['events'].pop(1)
                raw['unique_originals'] -= 1
            elif change == 'extra':
                raw['events'].append(dict(raw['events'][0], event=999))
                raw['unique_originals'] += 1
            elif change == 'partial_flags':
                census['flag_census_closed'] = False
            else:
                raw['all_required_files_scanned_to_eof'] = False
            with self.subTest(change=change), self.assertRaises(ValueError):
                SCANNER.validate_raw_mapping(census, raw)

    def test_missing_and_short_reports_are_unknown(self):
        class Handle:
            def __init__(self, valid, size):
                self.valid, self.size = valid, size
            def isValid(self):
                return self.valid
            def product(self):
                return self
            def GetCSCHaloReport(self):
                return [0]*self.size
            def GetGlobalHaloReport(self):
                return [0]*4
            def GetEcalHaloReport(self):
                return [0]*2
            def GetHcalHaloReport(self):
                return [0]*2
            def CSCLooseHaloId(self):
                return True
            def CSCTightHaloId(self):
                return False
        class Event:
            def getByLabel(self, label, handle):
                pass
        for valid, size, expected in [(False, 6, 'unknown'), (True, 0, 'unknown'),
                                      (True, 5, 'unknown'), (True, 6, 'selected')]:
            with self.subTest(valid=valid, size=size):
                result = SCANNER.read_halo(Event(), Handle(valid, size), SCANNER.halo_policy())
                self.assertEqual(result['decision'], expected)


if __name__ == '__main__':
    unittest.main()
