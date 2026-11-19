# Code style: AI-code.
"""Halo responses and complete original exposure stay separate through collection."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import prepare_shift_data_beam_halo as halo
import scan_shift_data_beam_halo as aod_scan
import scan_shift_data_lumisections as raw_scan
from collect_shift_data_outputs import build_cohort_publication_inventory


class HaloPreparationTest(unittest.TestCase):
    def setUp(self):
        (ROOT/'tmp').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='halo-plan-test-', dir=ROOT/'tmp')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.mask = {'369943': [[10, 12]]}
        self.rows = [[(10, 30, True), (10, 10, False), (10, 20, True),
                      (11, 40, False), (11, 50, True), (12, 60, False)], [(10, 5, True)]]
        self.raw_catalog = self.catalog('RAW')
        self.aod_catalog = self.catalog('AOD')
        for name, value in [('raw_catalog', self.raw_catalog), ('aod_catalog', self.aod_catalog), ('certification', self.mask)]:
            self.write(name+'.json', value)
        self.scan_inputs()
        self.template = dict(schema='shift-data-reconstruction-job-v2', payload_files={'bootstrap.sh': 'c'*64},
            runtime_url='root://example//runtime.tar.gz', runtime_sha256='d'*64, table_sha256='e'*64,
            native_timeout_seconds=900, job_max_runtime_seconds=3600, source_file_adler32='oldvalue',
            operational_retry_limit=1, afs_allocation={'path':'/afs/old/allocation.json','sha256':'a'*64},
            remote_output_root='/eos/old.root', remote_output_url='root://old//old.root',
            remote_storage_prefix='/old', transport_qualification_pause_seconds=999,
            operational_canary=True, paired_group='old', recovery_policy={'old':True}, storage_claim='old')
        self.write('template.json', self.template)

    def scan_inputs(self):
        cert_sha = halo.sha256(self.directory/'certification.json')
        self.raw = raw_scan.scan_catalog(self.raw_catalog, self.mask, self.reader(False),
            catalog_sha256=halo.sha256(self.directory/'raw_catalog.json'), certification_sha256=cert_sha)
        self.aod = aod_scan.scan_catalog(self.aod_catalog, self.mask, self.reader(True),
            catalog_sha256=halo.sha256(self.directory/'aod_catalog.json'), certification_sha256=cert_sha)
        self.write('raw.json', self.raw)
        self.write('aod.json', self.aod)

    def write(self, name, value):
        path = self.directory/name
        path.write_text(json.dumps(value)+'\n')
        return path

    def catalog(self, tier):
        processed = 'Run2023D-v1' if tier == 'RAW' else 'Run2023D-PromptReco-v1'
        version = 'v1' if tier == 'RAW' else 'PromptReco-v1'
        datasets = [f'/Muon{i}/{processed}/{tier}' for i in range(2)]
        records = []
        for index, dataset in enumerate(datasets):
            for lumi in (10, 11, 12):
                lfn = f'/store/data/Run2023D/Muon{index}/{tier}/{version}/{index}.root'
                files = [dict(lfn=lfn,source_url='root://cms/'+lfn)] if index == 0 or lumi == 10 else []
                records.append(dict(run=369943,luminosityBlock=lumi,dataset=dataset,query_complete=True,files=files))
        return dict(schema='shift-data-full-lumisection-catalog-v1', data_tier=tier,
                    datasets=datasets, lumimask=self.mask, records=records)

    def reader(self, aod):
        def read(source, *ignored):
            index = int(source['dataset'].split('/')[1][-1])
            metadata = dict(root_uuid=f'uuid{index}',events_tree_entries=len(self.rows[index]))
            def headers():
                for lumi, number, flag in self.rows[index]:
                    row = dict(run=369943,luminosityBlock=lumi,event=number,is_real_data=True)
                    if aod:
                        row['halo'] = dict(decision='selected' if flag else 'rejected')
                    yield row
            return metadata, headers()
        return read

    def prepare(self, **kwargs):
        return halo.prepare(self.directory/'aod.json', self.directory/'raw.json',
            self.directory/'template.json', self.directory/'plan',
            aod_catalog_path=self.directory/'aod_catalog.json', raw_catalog_path=self.directory/'raw_catalog.json',
            certification_path=self.directory/'certification.json', **kwargs)

    def test_selected_offsets_preserve_raw_entry_order_and_original_offsets(self):
        plan = halo.build_halo_plan(self.aod,self.raw)
        census = plan['selected_census']
        self.assertEqual([row['event'] for row in census['events']], [30,20,5,50])
        self.assertEqual([row['globalcohort_entry'] for row in census['events']], [0,1,2,3])
        self.assertEqual([row['original_globalcohort_entry'] for row in census['events']], [0,2,3,5])
        self.assertFalse(census['all_required_files_scanned_to_eof'])
        self.assertEqual(census['representation'], 'halo_selected_responses')
        self.assertEqual([row['original_events'] for row in plan['lumisections']], [4,2,1])
        self.assertEqual([row['selected_events'] for row in plan['lumisections']], [3,1,0])
        with self.assertRaisesRegex(ValueError,'prefix, partial scan'):
            halo.build_full_lumi_plan(self.raw_catalog,census,self.mask)

    def test_worker_chunks_never_cross_source_or_lumisection(self):
        plan = halo.build_halo_plan(self.aod,self.raw,max_events_per_job=1)
        self.assertEqual(len(plan['jobs']),4)
        for job in plan['jobs']:
            self.assertEqual(job['expected_event_count'],1)
            self.assertEqual(len({(row['run'],row['luminosityBlock'],row['source_index']) for row in job['events']}),1)
        for size in (0,101):
            with self.subTest(size=size),self.assertRaises(ValueError):
                halo.build_halo_plan(self.aod,self.raw,max_events_per_job=size)

    def test_full_original_set_required_including_rejected_events(self):
        bad = copy.deepcopy(self.raw)
        bad['events'].pop(1)
        bad['unique_originals'] -= 1
        with self.assertRaisesRegex(ValueError,'identity sets differ'):
            halo.build_halo_plan(self.aod,bad)
        bad = copy.deepcopy(self.aod)
        bad['flag_census_closed'] = False
        with self.assertRaisesRegex(ValueError,'partial AOD flags'):
            halo.build_halo_plan(bad,self.raw)

    def test_request_preserves_science_and_strips_old_transport_allocation_and_checksum(self):
        allocation = self.write('new_allocation.json',{'purpose':'synthetic new allocation'})
        contract = self.prepare(settings=dict(afs_allocation=halo.pin(allocation),native_timeout_seconds=1200))
        request = json.loads(Path(contract['jobs'][0]['request_path']).read_text())
        for key in halo.SCIENCE_FIELDS:
            self.assertEqual(request[key],self.template[key])
        self.assertFalse(any(key.startswith(('remote_','transport_qualification','recovery_','storage_claim')) for key in request))
        self.assertNotIn('source_file_adler32',request)
        self.assertNotIn('operational_canary',request)
        self.assertNotIn('paired_group',request)
        self.assertEqual(request['afs_allocation'],halo.pin(allocation))
        self.assertEqual(request['native_timeout_seconds'],1200)
        self.assertEqual(request['representation'],'halo_selected_responses')

    def test_closure_credits_full_denominator_only_after_all_selected_responses(self):
        contract = self.prepare(max_events_per_job=1)
        first = halo.completed_lumisections(contract,set())
        self.assertEqual(first['closed_lumimask'],{'369943':[[12,12]]})
        self.assertEqual(first['closed_original_events'],1)
        verified = {job['request_sha256'] for job in contract['jobs'] if job['luminosityBlock']==11}
        second = halo.completed_lumisections(contract,verified)
        self.assertEqual(second['closed_original_events'],3)
        self.assertEqual(second['closed_selected_events'],1)
        all_done = halo.completed_lumisections(contract,{job['request_sha256'] for job in contract['jobs']})
        self.assertEqual((all_done['closed_original_events'],all_done['closed_selected_events']),(7,4))
        self.assertEqual(all_done['closed_lumimask'],self.mask)
        with self.assertRaisesRegex(ValueError,'Unknown completed'):
            halo.completed_lumisections(contract,{'f'*64})

    def test_selected_census_reaches_ordinary_collector_without_original_exposure_confusion(self):
        contract = self.prepare()
        census = json.loads(Path(contract['pins']['selected_census']['path']).read_text())
        ready = []
        for job in contract['jobs']:
            request = json.loads(Path(job['request_path']).read_text())
            ready.append(dict(source_offset=job['source_offset'],expected_event_count=job['expected_event_count'],
                native_exit_verified=True,era='Run2023D',dataset=request['dataset'],origin_uri=request['source_url'],
                original_runs=[369943],path='/synthetic/output.root',root_uuid=job['name'],sha256='a'*64,
                bytes=123,producer_receipt='/synthetic/receipt.json'))
        inventory = build_cohort_publication_inventory({'ready':ready},census,4)
        self.assertEqual(inventory['expected_unique_events'],4)
        self.assertEqual(contract['original_events'],7)
        self.assertEqual(inventory['expected_identity_sha256'],contract['selected_identity_sha256'])

    def test_changed_input_or_request_blocks_later_lumisection_credit(self):
        contract = self.prepare()
        request = Path(contract['jobs'][0]['request_path'])
        original = request.read_text()
        request.write_text(original+' ')
        with self.assertRaisesRegex(ValueError,'worker request changed'):
            halo.completed_lumisections(contract,set())
        request.write_text(original)
        raw = self.directory/'raw.json'
        raw.write_text(raw.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'metadata changed'):
            halo.completed_lumisections(contract,set())

    def test_reusing_existing_preparation_directory_is_refused(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError,'new absolute persistent'):
            self.prepare()

    def test_whole_lumisection_packing_keeps_raw_order_and_closes_together(self):
        self.rows = [[(11, 50, True), (10, 30, True), (10, 10, False),
                      (10, 20, True), (11, 40, False), (12, 60, False)], [(10, 5, False)]]
        self.scan_inputs()
        contract = self.prepare(max_events_per_job=3, pack_lumisections=True)
        self.assertEqual(contract['schema'], halo.PACKED_LS_CONTRACT)
        self.assertEqual(len(contract['jobs']), 1)
        job, = contract['jobs']
        self.assertEqual([section['selected_events'] for section in job['lumisections']], [2,1])
        request = json.loads(Path(job['request_path']).read_text())
        self.assertEqual([event['event'] for event in request['events']], [50,30,20])
        self.assertEqual(halo.completed_lumisections(contract, set())['closed_lumimask'],
                         {'369943': [[12,12]]})
        completed = halo.completed_lumisections(contract, {job['request_sha256']})
        self.assertEqual(completed['closed_lumimask'], self.mask)
        self.assertEqual((completed['closed_original_events'], completed['closed_selected_events']), (7,3))
        bad = copy.deepcopy(contract)
        bad['jobs'][0]['lumisections'][0]['identity_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'Packed worker differs'):
            halo.completed_lumisections(bad, {job['request_sha256']})

    def test_mixed_source_and_oversized_lumisections_keep_single_section_workers(self):
        self.rows[0].extend((11, event, True) for event in (70, 80, 90))
        self.scan_inputs()
        contract = self.prepare(max_events_per_job=3, pack_lumisections=True)
        self.assertEqual([job['expected_event_count'] for job in contract['jobs']], [2,1,3,1])
        self.assertTrue(all(len(job['lumisections']) == 1 for job in contract['jobs']))


if __name__ == '__main__':
    unittest.main()
