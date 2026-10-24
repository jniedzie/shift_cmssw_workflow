# Code style: AI-code. AI-generated committed GEN storage accounting tests.
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_meson_production as meson


class CommittedGENStorage(unittest.TestCase):
    def fixture(self,root):
        def pin(name,value):
            p=root/name;p.write_text(json.dumps(value))
            return dict(path=str(p),sha256=meson.durable.sha(p))
        path='/eos/user/j/jniedzie/shift_cmssw/darkphoton_m2_prompt/campaign/GEN/gen.root'
        publication=dict(path=path,bytes=100,sha256='a'*64,readback_sha256='a'*64)
        plan=pin('plan.json',dict(points=[dict(point='m2_prompt',gen_remote_path=path,full_gen_events=20)]))
        manifest=pin('manifest.json',dict(output_sha256='a'*64))
        seal=pin('seal.json',dict(files={plan['path']:plan['sha256'],manifest['path']:manifest['sha256']}))
        status=pin('status.json',dict(complete=True,publication=publication,
            parent=dict(events=20,manifest_sha256=manifest['sha256'])))
        row=dict(point='m2_prompt',plan=plan,manifest=manifest,seal=seal,status=status,
            gen_submission=pin('submission.json',dict(cluster=123)),publication=publication,
            independent_sha256_readback='a'*64,eos_mtime_ns=10)
        registration=dict(path='/registered/storage.json',sha256='b'*64)
        evidence=dict(schema='shift-committed-direct-GEN-storage-v1',reserved_bytes=30000000000,
            storage_registration=registration,gen_managers=[123],objects=[row],total_bytes=100)
        authority=dict(original_storage_registration=copy.deepcopy(registration),committed_direct_GEN=pin('credit.json',evidence))
        return authority,evidence,pin,path

    def test_exact_committed_bytes_release_only_their_existing_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            authority,_,_,gen=self.fixture(Path(temporary))
            original=Path.stat
            def stat(path,*a,**k):
                return SimpleNamespace(st_size=100,st_mtime_ns=10) if str(path)==gen else original(path,*a,**k)
            with patch.object(Path,'stat',stat):
                self.assertEqual(meson.committed_direct_gen_credit(authority,set()),100)
            self.assertEqual(meson.committed_direct_gen_credit({},set()),0)

    def test_changed_or_live_evidence_never_releases_storage(self):
        for scenario in ('live','duplicate','size','mtime','readback','total','publication','manifest','registration'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                authority,evidence,pin,gen=self.fixture(Path(temporary));live=set();size=100;mtime=10
                if scenario=='live':live={123}
                elif scenario=='duplicate':evidence['objects'].append(copy.deepcopy(evidence['objects'][0]));evidence['total_bytes']=200
                elif scenario=='size':size=99
                elif scenario=='mtime':mtime=11
                elif scenario=='readback':evidence['objects'][0]['independent_sha256_readback']='c'*64
                elif scenario=='total':evidence['total_bytes']=101
                elif scenario=='publication':evidence['objects'][0]['publication']['path']='/eos/unrelated/gen.root'
                elif scenario=='manifest':Path(evidence['objects'][0]['manifest']['path']).write_text('{}')
                else:evidence['storage_registration']['sha256']='c'*64
                authority['committed_direct_GEN']=pin('credit.json',evidence)
                original=Path.stat
                def stat(path,*a,**k):
                    return SimpleNamespace(st_size=size,st_mtime_ns=mtime) if str(path)==gen else original(path,*a,**k)
                with patch.object(Path,'stat',stat),self.assertRaises((ValueError,FileNotFoundError)):
                    meson.committed_direct_gen_credit(authority,live)


class ClosedDetectorStorage(unittest.TestCase):
    def fixture(self,root):
        def pin(name,value):
            p=root/name;p.write_text(json.dumps(value))
            return dict(path=str(p),sha256=meson.durable.sha(p))
        path='/eos/user/j/jniedzie/shift_cmssw/darkphoton_m2_prompt/campaign/nanoAOD/chunk_000.root'
        publication=dict(path=path,bytes=100,sha256='a'*64)
        plan=pin('plan.json',dict(points=[dict(point='m2_prompt',chunks=[dict(chunk=0,count=10,
            nano=path,metadata='/eos/user/j/jniedzie/shift_cmssw/metadata')])]))
        seal=pin('seal.json',dict(files={plan['path']:plan['sha256']}))
        status=pin('status.json',dict(complete=True,point='m2_prompt',chunk=0,events=10,
            plan_sha256=plan['sha256'],seal_sha256=seal['sha256'],published=dict(nanoAOD=publication)))
        row=dict(status=status,point='m2_prompt',chunk=0,kind='nanoAOD',publication=publication,
            independent_sha256_readback='a'*64,eos_mtime_ns=10)
        native=dict(ClusterId=123,ProcId=0,Iwd=str(root),JobStatus=4,ExitCode=0,ExitBySignal=False)
        wave=dict(plan=plan,seal=seal,submission=pin('submission.json',dict(cluster=123,task=str(root))),terminal=native,objects=[row])
        registration=dict(path='/registered/storage.json',sha256='b'*64)
        evidence=dict(schema='shift-committed-terminal-direct-storage-v1',reserved_bytes=30000000000,
            storage_registration=registration,waves=[wave],total_bytes=100)
        authority=dict(original_storage_registration=copy.deepcopy(registration),committed_direct_detector=pin('credit.json',evidence))
        return authority,evidence,pin,path

    def test_successful_closed_wave_credits_exact_existing_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            authority,_,_,nano=self.fixture(Path(temporary));original=Path.stat
            def stat(path,*a,**k):
                return SimpleNamespace(st_size=100,st_mtime_ns=10) if str(path)==nano else original(path,*a,**k)
            with patch.object(Path,'stat',stat):self.assertEqual(meson.committed_direct_detector_credit(authority,set()),100)

    def test_live_failed_partial_changed_or_duplicate_outputs_keep_their_claim(self):
        for scenario in ('live','failed','partial','duplicate','mtime','readback'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as temporary:
                authority,evidence,pin,nano=self.fixture(Path(temporary));live=set();mtime=10;wave=evidence['waves'][0]
                if scenario=='live':live={123}
                elif scenario=='failed':wave['terminal']['ExitCode']=1
                elif scenario=='partial':
                    status=meson.read(wave['objects'][0]['status']['path']);status['complete']=False
                    wave['objects'][0]['status']=pin('status.json',status)
                elif scenario=='duplicate':wave['objects'].append(copy.deepcopy(wave['objects'][0]));evidence['total_bytes']=200
                elif scenario=='mtime':mtime=11
                else:wave['objects'][0]['independent_sha256_readback']='b'*64
                authority['committed_direct_detector']=pin('credit.json',evidence);original=Path.stat
                def stat(path,*a,**k):
                    return SimpleNamespace(st_size=100,st_mtime_ns=mtime) if str(path)==nano else original(path,*a,**k)
                with patch.object(Path,'stat',stat),self.assertRaises(ValueError):meson.committed_direct_detector_credit(authority,live)

    def test_completed_workers_credit_outputs_while_other_wave_workers_run(self):
        for scenario in ('valid','live_child','failed_child','wrong_node'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as temporary:
                authority,evidence,pin,nano=self.fixture(Path(temporary));wave=evidence['waves'][0]
                wave.pop('terminal');wave['terminal_chunks']=True
                native=dict(cluster=456,submission_event='D_m2_prompt_000',
                    terminal_event='005 (456.000.000) Job terminated.\nNormal termination (return value 0)')
                wave['objects'][0]['native']=native;live={123}
                if scenario=='live_child':live.add(456)
                elif scenario=='failed_child':native['terminal_event']=native['terminal_event'].replace('value 0','value 1')
                elif scenario=='wrong_node':native['submission_event']='D_m5_prompt_000'
                authority['committed_direct_detector']=pin('credit.json',evidence);original=Path.stat
                def stat(path,*a,**k):
                    return SimpleNamespace(st_size=100,st_mtime_ns=10) if str(path)==nano else original(path,*a,**k)
                with patch.object(Path,'stat',stat):
                    if scenario=='valid':self.assertEqual(meson.committed_direct_detector_credit(authority,live),100)
                    else:
                        with self.assertRaises(ValueError):meson.committed_direct_detector_credit(authority,live)


if __name__=='__main__':unittest.main()
