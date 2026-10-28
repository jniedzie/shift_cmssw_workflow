# Code style: AI-code. AI-generated focused diagnostic classifier/count tests.
import copy
import json
import gzip
import hashlib
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dy_branching_control import primary_decay, mass_counts, compare, native_bounds
from run_dy_branching_control import collect, retain_failure, finalize


def event(channel=(-13,13)):
    return [dict(barcode=1,pdg=23,hard_process=True,p4_GeV=[0.,0.,0.,24.],mothers=[],daughters=[2]),
            dict(barcode=2,pdg=23,hard_process=False,p4_GeV=[0.,0.,0.,24.],mothers=[1],daughters=[3,4]),
            dict(barcode=3,pdg=channel[0],hard_process=True,p4_GeV=[0.,0.,12.,12.],mothers=[2],daughters=[]),
            dict(barcode=4,pdg=channel[1],hard_process=True,p4_GeV=[0.,0.,-12.,12.],mothers=[2],daughters=[])]


class ControlTests(unittest.TestCase):
    def test_failed_GEN_retention_requires_a_bound_and_never_uses_production_storage(self):
        old=Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            try:
                os.chdir(temporary)
                request=dict(failure_publication_bound_bytes=0,
                    failure_publication_directory='/eos/user/j/jniedzie/shift_cmssw_support/test_failed_GEN')
                self.assertIsNone(retain_failure(request))
                Path('gen.root').write_bytes(b'failed native GEN reference')
                with self.assertRaisesRegex(ValueError,'registered retention'):
                    retain_failure(request)
                request['failure_publication_bound_bytes']=1024
                request['failure_publication_directory']='/eos/user/j/jniedzie/shift_cmssw/production'
                with self.assertRaisesRegex(ValueError,'registered support'):
                    retain_failure(request)
                request['failure_publication_directory']='/eos/user/j/jniedzie/shift_cmssw_support/test_failed_GEN'
                with patch('run_shift_weighted_gen.publish',return_value=['preserved']) as publish:
                    self.assertEqual(retain_failure(request),['preserved'])
                    self.assertEqual(publish.call_args.args[0],[(Path('gen.root'),'gen.root')])
                    request['failure_publication_bound_bytes']=1
                    with self.assertRaisesRegex(ValueError,'retention allowance'):
                        retain_failure(request)
                    self.assertEqual(publish.call_count,1)
            finally:
                os.chdir(old)

    def test_threshold_floor_and_both_hard_mass_bounds_are_native_and_pinned(self):
        parameters=['23:mMin = 0.211317','PhaseSpace:mHatMin = 0.211317',
                    'PhaseSpace:mHatMax = 0.5','23:onMode = off','23:onIfMatch = 13 -13']
        self.assertEqual(native_bounds(parameters,[.211317,.5]),[.211317,.5])
        with self.assertRaisesRegex(ValueError,'Native mass boundary'):
            native_bounds(parameters,[.5,1.])
        with self.assertRaisesRegex(ValueError,'23:mMin'):
            native_bounds(parameters[1:],[.211317,.5])

    def test_copy_chain_is_one_primary_and_direct_muons_count(self):
        result=primary_decay(event())
        self.assertTrue(result['primary23_mumu'])
        self.assertEqual(result['mass_GeV'],24.)
        self.assertEqual(result['primary23_copy_barcodes'],[1,2])

    def test_tau_daughter_muons_do_not_become_a_primary_muon_decay(self):
        rows=event((-15,15))
        rows[2]['daughters']=[5]
        rows.append(dict(barcode=5,pdg=13,hard_process=False,p4_GeV=[0.,0.,1.,1.],mothers=[3],daughters=[]))
        self.assertFalse(primary_decay(rows)['primary23_mumu'])

    def test_radiated_photon_does_not_remove_the_direct_muon_channel(self):
        rows=event();rows[1]['daughters'].append(5)
        rows.append(dict(barcode=5,pdg=22,hard_process=True,p4_GeV=[1.,0.,0.,1.],mothers=[2],daughters=[]))
        self.assertTrue(primary_decay(rows)['primary23_mumu'])

    def test_direct_muon_FSR_copy_is_followed_and_a_copy_fork_fails(self):
        rows=event();rows[2]['daughters']=[5,6]
        rows.extend([dict(barcode=5,pdg=-13,hard_process=False,p4_GeV=[0.,0.,10.,10.],mothers=[3],daughters=[]),
                     dict(barcode=6,pdg=22,hard_process=False,p4_GeV=[0.,0.,2.,2.],mothers=[3],daughters=[])])
        result=primary_decay(rows)
        self.assertTrue(result['primary23_mumu'])
        self.assertIn([3,5],result['direct_muon_copy_barcodes'])
        rows[2]['daughters'].append(7)
        rows.append(dict(barcode=7,pdg=-13,hard_process=False,p4_GeV=[0.,0.,1.,1.],mothers=[3],daughters=[]))
        with self.assertRaisesRegex(ValueError,'muon copy fork'):primary_decay(rows)

    def test_secondary_nonhard_boson_is_ignored_but_two_hard_bosons_fail(self):
        rows=event();rows.append(dict(barcode=10,pdg=23,hard_process=False,p4_GeV=[0.,0.,0.,3.],mothers=[],daughters=[3,4]))
        self.assertTrue(primary_decay(rows)['primary23_mumu'])
        rows[-1]['hard_process']=True
        with self.assertRaisesRegex(ValueError,'exactly one'):primary_decay(rows)

    def test_ambiguous_copy_fork_or_missing_hard_primary_fails(self):
        rows=event();rows[0]['daughters'].append(7)
        rows.append(dict(barcode=7,pdg=23,hard_process=False,p4_GeV=[0.,0.,0.,24.],mothers=[1],daughters=[3,4]))
        with self.assertRaisesRegex(ValueError,'forked'):primary_decay(rows)
        rows=event();rows[0]['hard_process']=False
        with self.assertRaisesRegex(ValueError,'exactly one'):primary_decay(rows)

    def test_mass_slice_rates_use_full_arm_exposure_and_no_guessed_BR(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            forced=dict(complete=True,arm='forced',plan_sha256='a'*64,accepted_gen_events=5000,
                source_xsec_pb=1.3,source_xsec_error_pb=.01,muonic_events=5000,
                mass_slices=[dict(bounds_GeV=[20.,25.],parents=2500,muonic=2500),
                             dict(bounds_GeV=[25.,None],parents=2500,muonic=2500)])
            natural=copy.deepcopy(forced);natural.update(arm='natural',source_xsec_pb=13.,muonic_events=500)
            natural['mass_slices'][0]['muonic']=250;natural['mass_slices'][1]['muonic']=250
            for name,report in [('forced',forced),('natural',natural)]:
                (base/(name+'.json')).write_text(json.dumps(report))
            compare(base/'forced.json',base/'natural.json',base/'comparison.json')
            result=json.loads((base/'comparison.json').read_text())
            self.assertFalse(result['normalization_ready'])
            self.assertFalse(result['correction_applied'])
            self.assertEqual(result['comparisons'][0]['natural_over_forced'],1.)
            self.assertEqual(result['comparisons'][1]['forced']['rate_pb'],.65)
            self.assertEqual(result['comparisons'][1]['natural']['rate_pb'],.65)
            self.assertEqual(result['comparisons'][1]['natural']['accepted_exposure'],5000)
            self.assertGreater(result['comparisons'][1]['natural']['approximate_independent_standard_error_pb'],
                               result['comparisons'][1]['natural']['conditional_binomial_error_pb'])

    def test_compact_collection_preserves_full_exposure_and_rejects_repeated_originals(self):
        def prepare(base, duplicate=False):
            plan=base/'plan.json';plan.write_text(json.dumps(dict(bounds_GeV=[20.,30.])))
            plan_sha=hashlib.sha256(plan.read_bytes()).hexdigest()
            requests={}
            for arm in ['forced','natural']:
                folder=base/arm;folder.mkdir()
                request=folder/'request.json';request.write_text(json.dumps(dict(arm=arm)))
                request_sha=hashlib.sha256(request.read_bytes()).hexdigest()
                rows=[dict(event_id=[1 if arm=='forced' else 2,1,i],native_weight=1.,
                    mass_GeV=mass,primary23_mumu=arm=='forced' or i==1)
                    for i,mass in [(1,24.),(2,26.)]]
                if duplicate:rows[1]['event_id']=rows[0]['event_id']
                packed=gzip.compress(('\n'.join(json.dumps(r) for r in rows)+'\n').encode(),mtime=0)
                audit=dict(complete=True,arm=arm,plan_sha256=plan_sha,accepted_gen_events=2,
                    source_xsec_pb=1. if arm=='forced' else 2.,source_xsec_error_pb=.01,
                    muonic_events=sum(r['primary23_mumu'] for r in rows),
                    mass_slices=mass_counts(rows,[[20.,25.],[25.,30.]]),
                    event_rows_sha256=hashlib.sha256(packed).hexdigest())
                with tarfile.open(folder/'return_payload.tar.gz','w:gz') as archive:
                    for name,raw in [('audit.json',json.dumps(audit).encode()),
                                     ('primary23_events.jsonl.gz',packed)]:
                        member=tarfile.TarInfo(name);member.size=len(raw);archive.addfile(member,io.BytesIO(raw))
                (folder/'result.json').write_text(json.dumps(dict(complete=True,exit_code=0,
                    request_sha256=request_sha,native_audit=audit)))
                (folder/'status.json').write_text(json.dumps(dict(complete=True,exit_code=0,
                    return_payload_sha256=hashlib.sha256((folder/'return_payload.tar.gz').read_bytes()).hexdigest())))
                requests[arm]=dict(request_path=str(request),request_sha256=request_sha)
            request=base/'collect.json';request.write_text(json.dumps(dict(arms=requests,
                plan_path=str(plan),plan_sha256=plan_sha,minimum_natural_muonic_count=1)))
            return request

        old=Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);good=base/'good';good.mkdir();request=prepare(good)
            try:
                os.chdir(good);collect(request)
                result=json.loads((good/'result.json').read_text())
                self.assertEqual(result['accepted_rows_reread'],dict(forced=2,natural=2))
                self.assertTrue(result['positive_count_target_met'])
                self.assertEqual(result['comparisons'][0]['natural_over_forced'],1.)
                self.assertFalse(result['normalization_ready'])
                # A complete but statistically inconclusive bin must finish
                # normally, without being promoted to production-ready.
                cases=[]
                for arm in ['forced','natural']:
                    folder=good/arm;path=folder/'request.json'
                    value=json.loads((folder/'result.json').read_text())
                    value['full_native_second_readback']=dict(complete=True,
                        every_accepted_identity_channel_weight_and_Run_Lumi_reread=True)
                    (folder/'result.json').write_text(json.dumps(value))
                    cases.append(dict(stratum='good',arm=arm,request_path=str(path),
                        request_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
                final=base/'final';final.mkdir();plan=good/'plan.json'
                master=base/'master.json';master.write_text(json.dumps(dict(cases=cases,science_workers=2,
                    accepted_GEN_events_per_worker=2,total_accepted_GEN_budget=4,
                    all_native_subplans=[dict(path=str(plan),sha256=hashlib.sha256(plan.read_bytes()).hexdigest())])))
                result['positive_count_target_met']=False
                (good/'result.json').write_text(json.dumps(result))
                final_request=final/'request.json';final_request.write_text(json.dumps(dict(plan_path=str(master),
                    plan_sha256=hashlib.sha256(master.read_bytes()).hexdigest(),comparisons=[dict(stratum='good',
                        request_path=str(good/'collect.json'),request_sha256=hashlib.sha256((good/'collect.json').read_bytes()).hexdigest())])))
                os.chdir(final);finalize(final_request)
                done=json.loads((final/'result.json').read_text())
                self.assertTrue(done['complete_frozen_draw_collected'])
                self.assertFalse(done['all_positive_count_targets_met'])
                self.assertFalse(done['full_production_ready'])
                value=json.loads(final_request.read_text());value['comparisons']=[]
                final_request.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'comparison census'):
                    finalize(final_request)
                bad=base/'bad';bad.mkdir();request=prepare(bad,duplicate=True)
                os.chdir(bad)
                with self.assertRaisesRegex(ValueError,'Returned native rows'):
                    collect(request)
            finally:
                os.chdir(old)


if __name__=='__main__':unittest.main()
