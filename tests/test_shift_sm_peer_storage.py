# Code style: AI-code.
"""Storage credit must follow terminal, retained bytes rather than forecasts."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import shift_sm_peer_storage as peer
from run_shift_sm_postprocessing_controller import science_write_reservation


class PeerStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.item=dict(path='/eos/user/j/jniedzie/shift_cmssw/sample/new/nano.root',bytes=8,sha256='a'*64)
        self.identity=dict(Id='100',Size='8',MTime='one',CTime='one')
        self.group=dict(proof=dict(cluster=12,terminal_event='native success'),objects=[self.item])

    def write(self,name,value):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(value));return p

    def test_retained_readback_is_reused_only_for_unchanged_bytes_and_proof(self):
        with patch.object(peer,'retained_identity',return_value=self.identity),patch.object(peer,'download',side_effect=lambda source,target,digest:target.write_bytes(b'checked')) as download:
            first=peer.verify_group(self.group,self.root/'cache')
            second=peer.verify_group(self.group,self.root/'cache')
            self.assertEqual(first,second);self.assertEqual(download.call_count,1)
        changed=dict(self.identity,CTime='new')
        with patch.object(peer,'retained_identity',return_value=changed):
            with self.assertRaisesRegex(ValueError,'group changed'):
                peer.verify_group(self.group,self.root/'cache')

    def test_missing_or_conflicting_objects_never_receive_partial_credit(self):
        missing=copy.deepcopy(self.group);missing['objects'].append(dict(self.item,path=self.item['path']+'.second'))
        with patch.object(peer,'retained_identity',side_effect=[self.identity,None]),patch.object(peer,'download') as download:
            self.assertEqual(peer.verify_group(missing,self.root/'cache'),[])
            self.assertEqual(download.call_count,0)
        conflicting=copy.deepcopy(self.group);conflicting['objects'].append(dict(self.item,sha256='b'*64))
        with self.assertRaisesRegex(ValueError,'Conflicting peer'):
            peer.verify_group(conflicting,self.root/'cache')

    def primitive(self):
        base='/eos/user/j/jniedzie/shift_cmssw/sample/new-p'
        p=self.write('plan.json',dict(campaign='new-p',points=[dict(point='p',final_output_base=base)]))
        seal=self.write('seal.json',dict(files={str(p):peer.sha(p)}))
        self.write('submission.json',dict(cluster=12,task=str(self.root)))
        (self.root/'pipeline.dag.dagman.log').write_text('005 (12.000.000) Job terminated.\nNormal termination (return value 0)\n...\n')
        self.write('completed_points/p.json',dict(complete=True,point='p',plan_sha256=peer.sha(p),seal_sha256=peer.sha(seal),main_histograms=dict(complete=True,published=dict(merged_nano=dict(self.item,path=base+'/nano.root')))))
        return p

    def test_live_writer_or_failed_native_owner_keeps_the_whole_claim(self):
        self.primitive()
        self.assertIsNone(peer.closed_primitive(self.root,'new',[dict(Iwd=str(self.root/'jobs'))]))
        self.assertEqual(len(peer.closed_primitive(self.root,'new',[])['objects']),1)
        with (self.root/'pipeline.dag.dagman.log').open('a') as f:
            f.write('009 (12.000.000) Job removed.\n...\n')
        self.assertIsNone(peer.closed_primitive(self.root,'new',[]))

    def test_original_scientific_path_and_seal_cannot_be_relocated_or_changed(self):
        p=self.primitive();a=json.loads(p.read_text());a['campaign']='different-p';p.write_text(json.dumps(a))
        with self.assertRaisesRegex(ValueError,'original seal'):
            peer.closed_primitive(self.root,'new',[])

    def test_science_credit_preserves_unused_headroom_and_does_not_recredit_old_GEN(self):
        config=dict(reserved_science_bytes=1000,paid_science_source_ids=['new'],paid_preparation_source_ids=[])
        nano=dict(kind='Nano',path='/new/nano',bytes=50,sha256='n'*64)
        gen=dict(kind='preparation',path='/new/prepared',bytes=20,sha256='g'*64)
        state=dict(response_sources=dict(new=dict(closed_epoch=20,verified_paid_objects=[nano,gen]),old=dict(closed_epoch=1,verified_paid_objects=[dict(nano,path='/old/nano',bytes=900)])))
        self.assertEqual(science_write_reservation(config,state,10),(1000,1000))
        self.assertEqual(science_write_reservation(config,state,30),(1000,950))
        config['paid_preparation_source_ids']=['new']
        self.assertEqual(science_write_reservation(config,state,30),(1000,930))
        state['response_sources']['new']['verified_paid_objects'].append(nano)
        self.assertEqual(science_write_reservation(config,state,30),(1000,930))
        state['response_sources']['new']['verified_paid_objects'].append(dict(nano,bytes=51))
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            science_write_reservation(config,state,30)


if __name__=='__main__':unittest.main()
