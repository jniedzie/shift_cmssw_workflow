# Code style: AI-code. AI-generated filtered publication/accounting regressions.
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_shift_production_health_audit as health
from gate_shift_weighted_gen import aggregate
from qcd_mu_enrichment import PROFILE, filtered_snapshot
from test_shift_weighted_receipts import fixture


def filtered_fixture(passed=1):
    record,manager,manifest,row=fixture()
    ledger=record['ledger'];semantic=record['semantic_audit']
    weights=semantic['weights'][:passed]
    counters=[dict(total_positive=ledger['accepted'],total_negative=0,
                   pass_positive=passed,pass_negative=0,sum_weights=ledger['sumw'],sum_weights2=ledger['sumw2'],
                   sum_pass_weights=math.fsum(weights),sum_pass_weights2=math.fsum(w*w for w in weights))]
    records=[dict(event_id=identity,weight=weight,hepmc_weight=weight,process=101,
                  pthat=6.,direct_scales=[],charged=10)
             for identity,weight in zip(semantic['event_ids'][:passed],weights)]
    selected=filtered_snapshot(ledger,ledger,records,semantic['weighted_run_info'],counters)
    semantic.update(selected,generator_profile=PROFILE,filter_records=counters)
    record.update(event_count=passed,generator_profile=PROFILE,generation_seconds=1.)
    manifest['generator_profile']=PROFILE
    return record,manager,manifest,row


class FilteredPublicationTest(unittest.TestCase):
    def test_publication_accepts_saved_count_smaller_than_source_accepted(self):
        record,manager,manifest,row=filtered_fixture()
        self.assertEqual(record['event_count'],1)
        self.assertEqual(record['ledger']['accepted'],2)
        checked=health.weighted_receipt(record,manager,manifest,row,'c'*64)
        self.assertEqual((checked['events'],checked['trials']), (1,4))
        self.assertAlmostEqual(checked['sumw'],.2)

    def test_fixed_trial_aggregate_counts_filtered_weights_and_source_accepted_separately(self):
        record,manager,manifest,row=filtered_fixture()
        result=aggregate([record,copy.deepcopy(record)])
        self.assertEqual((result['trials'],result['accepted'],result['source_accepted']), (8,2,4))
        self.assertAlmostEqual(result['sigma_mb'],65.*.4/8)
        self.assertNotAlmostEqual(result['sigma_mb'],65.*1.2/8)
        self.assertNotAlmostEqual(result['sigma_mb'],65.*.4/2)

    def test_zero_pass_publication_and_aggregate_keep_entire_exposure(self):
        record,manager,manifest,row=filtered_fixture(0)
        checked=health.weighted_receipt(record,manager,manifest,row,'c'*64)
        result=aggregate([record])
        self.assertEqual((checked['events'],result['trials'],result['source_accepted']), (0,4,2))
        self.assertEqual((result['sigma_mb'],result['error_mb']), (0.,0.))

    def test_publication_uses_the_frozen_job_output_base(self):
        record,manager,manifest,row=filtered_fixture()
        row['output_base']='/eos/user/test/independent_filtered_population'
        record['request']=copy.deepcopy(row)
        destination=row['output_base']+'/'+row['id']
        record['publication_directory']=destination
        for artifact in record['publication']:
            artifact['path']=destination+'/'+artifact['name']
        self.assertEqual(health.weighted_receipt(record,manager,manifest,row,'c'*64)['events'],1)
        record['publication_directory']=manifest['eos_base']+'/'+row['id']
        with self.assertRaisesRegex(ValueError,'publication directory'):
            health.weighted_receipt(record,manager,manifest,row,'c'*64)

    def test_second_filter_efficiency_or_changed_source_exposure_fails(self):
        for kind in ('second_efficiency','source_accepted','source_trials','filter_sum'):
            record,manager,manifest,row=filtered_fixture()
            semantic=record['semantic_audit']
            if kind=='second_efficiency':semantic['sigma_weighted_mb']*=semantic['weighted_filter_efficiency']
            elif kind=='source_accepted':semantic['source_accepted']=1
            elif kind=='source_trials':semantic['full_source_trial_denominator']=2
            else:semantic['filter_records'][0]['sum_weights']=.5
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                health.weighted_receipt(record,manager,manifest,row,'c'*64)

    def test_profiles_must_match_receipt_and_cannot_be_aggregated(self):
        record,manager,manifest,row=filtered_fixture()
        manifest['generator_profile']='ordinary'
        with self.assertRaisesRegex(ValueError,'profile'):
            health.weighted_receipt(record,manager,manifest,row,'c'*64)
        ordinary=fixture()[0]
        ordinary['generation_seconds']=1.
        with self.assertRaisesRegex(ValueError,'different generator profiles'):
            aggregate([record,ordinary])

    def test_finalizer_keeps_source_exposure_and_counts_only_saved_events(self):
        record,manager,manifest,row=filtered_fixture()
        records=[];jobs=[]
        for index in range(2):
            current=copy.deepcopy(record)
            request=copy.deepcopy(row)
            request.update(id='filtered_'+str(index),chunk_index=index,run=row['run']+index)
            current.update(request=request,node_id=request['id'])
            current['ledger']['run']=request['run']
            current['semantic_audit']['ledger']=copy.deepcopy(current['ledger'])
            for identity in current['semantic_audit']['event_ids']:
                identity[0]=request['run']
            records.append(current);jobs.append(request)
        manifest['jobs']=jobs
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);(base/'results').mkdir()
            path=base/'manifest.json';path.write_text(json.dumps(manifest))
            checksum=hashlib.sha256(path.read_bytes()).hexdigest()
            gate=dict(pass_=True,manifest_sha256=checksum,generator_profile=PROFILE,
                      production_plan={row['stratum']:dict(chunks=2,trials=8,effective_event_target=1.)})
            gate['pass']=gate.pop('pass_')
            (base/'scientific_gate.json').write_text(json.dumps(gate))
            for current in records:
                current['manifest_sha256']=checksum
                (base/'results'/(current['request']['id']+'.json')).write_text(json.dumps(current))
            script=Path(__file__).resolve().parents[1]/'scripts/finalize_shift_weighted_gen.py'
            result=subprocess.run([sys.executable,str(script),str(base)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            finished=json.loads((base/'production_complete.json').read_text())
            summary=finished['strata'][row['stratum']]
            self.assertEqual((summary['trials'],summary['accepted'],summary['source_accepted'],finished['events']), (8,2,4,2))
            self.assertAlmostEqual(summary['sigma_mb'],65.*.4/8)


if __name__=='__main__':
    unittest.main()
