# Code style: AI-code.
"""AI-generated tests for complete-lumisection ownership and exposure gates."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import prepare_shift_data_lumisections as full


class FullLumisectionTest(unittest.TestCase):
    def fixtures(self):
        datasets = ['/Muon0/Run2023D-v1/RAW', '/Muon1/Run2023D-v1/RAW']
        files = [dict(lfn=f'/store/data/Run2023D/Muon{i}/RAW/v1/{i}.root',
                      source_url=f'root://eoscms.cern.ch//store/data/Run2023D/Muon{i}/RAW/v1/{i}.root')
                 for i in range(2)]
        mask = {'369943': [[10, 12]]}
        catalog = dict(schema='shift-data-full-lumisection-catalog-v1',
                       lumimask=mask, datasets=datasets, records=[])
        for lumi in (10, 11, 12):
            for index, dataset in enumerate(datasets):
                members = [files[index]] if lumi == 10 or (lumi == 11 and index == 0) else []
                catalog['records'].append(dict(run=369943, luminosityBlock=lumi,
                                               dataset=dataset, query_complete=True, files=members))
        sources = [dict(files[0], dataset=datasets[0], events_tree_entries=3, headers_read=3,
                        selected_headers=3, duplicate_selected_headers=0, accepted_originals=3),
                   dict(files[1], dataset=datasets[1], events_tree_entries=2, headers_read=2,
                        selected_headers=2, duplicate_selected_headers=1, accepted_originals=1)]
        events = []
        for source_index, source_entry, lumi, number in [(0, 0, 10, 7), (0, 1, 10, 8),
                                                        (0, 2, 11, 9), (1, 1, 10, 10)]:
            events.append(dict(globalcohort_entry=len(events), source_index=source_index,
                               source_entry=source_entry, source_dataset=datasets[source_index],
                               source_url=files[source_index]['source_url'],
                               run=369943, luminosityBlock=lumi, event=number))
        census = dict(schema='shift-data-input-header-census-v2', header_only=True,
                      all_headers_is_real_data=True, candidate_branches_read=False,
                      all_required_files_scanned_to_eof=True, event_limit_applied=False,
                      prior_events_excluded=False, selected_lumis=mask,
                      full_lumisection_catalog_sha256='a'*64,
                      sources=sources, events=events, unique_originals=len(events))
        return catalog, census, mask

    def contract(self, plan):
        pins = {key: dict(path='/afs/example/' + key + '.json', sha256='b'*64)
                for key in ('catalog', 'census', 'certification')}
        science = dict(lumimask=plan['lumimask'], datasets=plan['datasets'],
                       required_sources=plan['required_sources'], lumisections=plan['lumisections'], **pins)
        jobs = []
        for job in plan['jobs']:
            jobs.append({key: job[key] for key in ('name', 'run', 'luminosityBlock',
                                                  'expected_event_count', 'identity_sha256')}
                        | dict(request_path='/afs/example/' + job['name'] + '/request.json',
                               request_sha256=full.digest_value(job['name'])))
        return dict(schema='shift-data-full-lumisection-contract-v1', preparation_only=True,
                    cohort_sha256=full.digest_value(science), jobs=jobs, **science)

    def test_large_lumisection_splits_into_bounded_single_lumi_single_source_jobs(self):
        catalog, census, certification = self.fixtures()
        plan = full.build_full_lumi_plan(catalog, census, certification, max_events_per_job=1)
        self.assertEqual(len(plan['jobs']), 4)
        self.assertEqual([row['luminosityBlock'] for row in plan['normalized_census']['events']],
                         [10, 10, 10, 11])
        self.assertEqual(census['events'][2]['luminosityBlock'], 11)
        self.assertEqual([row['expected_events'] for row in plan['lumisections']], [3, 1, 0])
        for job in plan['jobs']:
            self.assertEqual(len({(row['run'], row['luminosityBlock'], row['source_url'])
                                  for row in job['events']}), 1)

    def test_one_missing_response_holds_whole_lumi_but_successful_empty_lumi_closes(self):
        plan = full.build_full_lumi_plan(*self.fixtures(), max_events_per_job=1)
        contract = self.contract(plan)
        verified = {job['request_sha256'] for job in contract['jobs']
                    if job['luminosityBlock'] == 11}
        result = full.completed_lumisections(contract, verified)
        self.assertEqual(result['closed_lumimask'], {'369943': [[11, 12]]})
        self.assertEqual(result['pending_lumimask'], {'369943': [[10, 10]]})
        self.assertEqual(result['closed_original_events'], 1)
        verified.update(job['request_sha256'] for job in contract['jobs'])
        self.assertEqual(full.completed_lumisections(contract, verified)['closed_original_events'], 4)

    def test_missing_catalog_query_is_unknown_even_if_other_stream_has_data(self):
        catalog, census, certification = self.fixtures()
        catalog['records'].pop()
        with self.assertRaisesRegex(ValueError, 'catalogue matrix'):
            full.build_full_lumi_plan(catalog, census, certification)

    def test_unfinished_query_and_missing_required_lfn_are_rejected(self):
        catalog, census, certification = self.fixtures()
        catalog['records'][0]['query_complete'] = False
        with self.assertRaisesRegex(ValueError, 'Unknown catalogue'):
            full.build_full_lumi_plan(catalog, census, certification)
        catalog['records'][0]['query_complete'] = True
        census['sources'].pop()
        with self.assertRaisesRegex(ValueError, 'Not every catalogue'):
            full.build_full_lumi_plan(catalog, census, certification)

    def test_prefix_and_partial_raw_scan_are_rejected(self):
        for change in (dict(event_limit_applied=True), dict(prior_events_excluded=True),
                       dict(all_required_files_scanned_to_eof=False)):
            catalog, census, certification = self.fixtures()
            census.update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'prefix, partial scan'):
                full.build_full_lumi_plan(catalog, census, certification)
        catalog, census, certification = self.fixtures()
        census['sources'][0]['headers_read'] = 2
        with self.assertRaisesRegex(ValueError, 'truncated'):
            full.build_full_lumi_plan(catalog, census, certification)

    def test_duplicates_and_unaccounted_selected_headers_are_rejected(self):
        catalog, census, certification = self.fixtures()
        census['events'][1]['event'] = 7
        with self.assertRaisesRegex(ValueError, 'Duplicate identity'):
            full.build_full_lumi_plan(catalog, census, certification)
        catalog, census, certification = self.fixtures()
        census['sources'][1]['duplicate_selected_headers'] = 0
        with self.assertRaisesRegex(ValueError, 'deduplication is unaccounted'):
            full.build_full_lumi_plan(catalog, census, certification)

    def test_uncertified_lumi_and_missing_file_membership_are_rejected(self):
        catalog, census, certification = self.fixtures()
        with self.assertRaisesRegex(ValueError, 'uncertified'):
            full.build_full_lumi_plan(catalog, census, {'369943': [[10, 11]]})
        catalog['records'][2]['files'] = []
        with self.assertRaisesRegex(ValueError, 'catalogue file scope'):
            full.build_full_lumi_plan(catalog, census, certification)

    def test_unknown_verified_job_and_tampered_lumi_count_are_rejected(self):
        contract = self.contract(full.build_full_lumi_plan(*self.fixtures()))
        with self.assertRaisesRegex(ValueError, 'unknown worker'):
            full.completed_lumisections(contract, {'f'*64})
        contract['lumisections'][0]['expected_events'] = 999
        contract['cohort_sha256'] = full.digest_value({key: contract[key] for key in (
            'lumimask', 'datasets', 'required_sources', 'lumisections', 'catalog', 'census', 'certification')})
        with self.assertRaisesRegex(ValueError, 'counts differ'):
            full.validate_contract(contract)

    def test_load_contract_checks_pins_and_rejects_alias_duplicate_raw_inputs(self):
        catalog, census, certification = self.fixtures()
        plan = full.build_full_lumi_plan(catalog, census, certification)
        contract = self.contract(plan)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for key, value in [('catalog', catalog), ('census', census), ('certification', certification)]:
                path = root / (key + '.json')
                path.write_text(json.dumps(value))
                contract[key] = full.pin(path)
            contract['cohort_sha256'] = full.digest_value({key: contract[key] for key in (
                'lumimask', 'datasets', 'required_sources', 'lumisections', 'catalog', 'census', 'certification')})
            path = root/'contract.json';path.write_text(json.dumps(contract));digest = full.sha256(path)
            source = catalog['records'][0]['files'][0]['source_url']
            self.assertEqual(full.load_contract(path, digest, catalog['datasets'][0], [source]), certification)
            with self.assertRaisesRegex(ValueError, 'outside the frozen'):
                full.load_contract(path, digest, catalog['datasets'][0],
                                   [source, source.replace('eoscms.cern.ch', 'cms-xrd-global.cern.ch')])
            (root/'certification.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'metadata changed'):
                full.load_contract(path, digest, catalog['datasets'][0], [source])


if __name__ == '__main__':
    unittest.main()
