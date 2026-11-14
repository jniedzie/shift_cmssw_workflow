# Code style: AI-code.
"""Legacy reuse preserves source provenance and rejects changed physics/exposure."""
import hashlib
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_shift_legacy_sm_source as legacy


class LegacySourceTests(unittest.TestCase):
    def test_resolved_generator_digest_ignores_external_seed_but_checks_decays(self):
        a='process.generator = cms.EDFilter("Pythia8GeneratorFilter", decays=cms.bool(False))\n'
        self.assertEqual(legacy.generator_digest(a+'seed=1'),legacy.generator_digest(a+'seed=2'))
        self.assertNotEqual(legacy.generator_digest(a),legacy.generator_digest(a.replace('False','True')))
        for text in ['seed=1',a+a]:
            with self.assertRaisesRegex(ValueError,'one complete generator'):
                legacy.generator_digest(text)

    def run_stage(self, *, bad_hash=False, bad_rate=False, duplicate=False, bad_weight=False,
                  config_directory=False, ambiguous_config=False, prior_overlap=None):
        generator='process.generator = cms.EDFilter("Pythia8GeneratorFilter")\n'
        audit=dict(input_sha256='wrong' if bad_hash else hashlib.sha256(b'GEN').hexdigest())
        payloads={'gen.root':b'GEN','resolved_gen_cfg.py':generator.encode(),
                  'validation.json':json.dumps(audit).encode()}
        row=dict(family='dy',generation_sampling='native_class',events_or_trials=2,run=0)
        request=dict(source_request=row,publication_directory='/eos/user/j/jniedzie/shift_cmssw_support/test',
            legacy_input=dict(GEN_path='/eos/user/j/jniedzie/legacy/gen.root',
                metadata_pin=dict(path='/eos/user/j/jniedzie/legacy/validation.json',
                                  sha256=hashlib.sha256(payloads['validation.json']).hexdigest()),
                config_path='/eos/user/j/jniedzie/legacy/resolved_gen_cfg.py',
                generator_ast_sha256=legacy.generator_digest(generator),
                native_RunInfo=[dict(internal_xsec_pb=3.,error_pb=.2)]))
        if config_directory:
            request['legacy_input'].pop('config_path')
            request['legacy_input']['config_directory']='/eos/user/j/jniedzie/legacy/configs/step1'
            request['legacy_input']['GEN_path']='/eos/user/j/jniedzie/legacy/chunks__part00001__samples__step1__events_step1_part0001.root'
        def download(path,target,expected=None):
            data=payloads[target.name];target.write_bytes(data);digest=legacy.sha(target)
            if expected is not None:self.assertEqual(digest,expected)
            return dict(path=path,bytes=len(data),sha256=digest,independent_sha256_readback=True)
        def product(event,*args):
            return None,types.SimpleNamespace(weight=lambda:2. if bad_weight else 1.)
        fake=types.SimpleNamespace(Events=lambda path:[0,1])
        names=['events_step1_part0001_seed191100001_cfg.py']
        if ambiguous_config:names.append('events_step1_part0001_seed191100000_cfg.py')
        listing='\n'.join('/eos/user/j/jniedzie/legacy/configs/step1/'+name for name in names)
        exposure=dict(runs=[dict(products=dict(generator=dict(
            internal_xsec_pb=4. if bad_rate else 3.,internal_error_pb=.2)))])
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                if prior_overlap is not None:
                    census=dict(GEN_sha256s=[hashlib.sha256(b'GEN').hexdigest()]
                        if prior_overlap=='hash' else ['b'*64],
                        event_ids=[[71 if prior_overlap=='RLE' else 72,1,1]],
                        reserved_source_runs=[71 if prior_overlap=='run' else 72])
                    data=gzip.compress(json.dumps(census).encode());Path('prior.json.gz').write_bytes(data)
                    request['legacy_input']['prior_census_ref']=dict(path=str(Path('prior.json.gz').resolve()),
                        sha256=hashlib.sha256(data).hexdigest())
                with patch.dict(sys.modules,{'DataFormats.FWLite':fake}), \
                     patch.object(legacy.subprocess,'run',return_value=types.SimpleNamespace(stdout=listing)), \
                     patch.object(legacy,'download',download), \
                     patch('audit_shift_material_scout.identity',lambda e:[71,1,1 if duplicate else e+1]), \
                     patch('audit_shift_material_scout.product',product), \
                     patch('audit_shift_material_scout.metadata',return_value=exposure), \
                     patch('run_shift_sm_postprocessing.publish',return_value=dict(
                         path=request['publication_directory']+'/receipt.json',sha256='a'*64)) as publish:
                    if (bad_hash or bad_rate or duplicate or bad_weight or ambiguous_config
                            or prior_overlap in ('hash','RLE','run')):
                        with self.assertRaises(ValueError):legacy.stage_native_receipt(request)
                        publish.assert_not_called()
                    else:
                        effective,_=legacy.stage_native_receipt(request)
                        receipt=json.loads(Path('legacy_native_receipt.json').read_text())
                        self.assertEqual(effective['source_request']['run'],71)
                        self.assertEqual(request['source_request']['run'],0)
                        self.assertEqual(receipt['event_ids'],[[71,1,1],[71,1,2]])
                        self.assertEqual(receipt['semantic_audit']['weights'],[1.,1.])
                        self.assertEqual(receipt['legacy_receipt_adapter']['new_GEN_exposure'],0)
                        self.assertTrue(all(p['path'].startswith('/eos/user/j/jniedzie/legacy/')
                                            for p in receipt['publications']))
                        publish.assert_called_once()
            finally:os.chdir(cwd)

    def test_native_receipt_keeps_original_paths_ids_weights_and_rate(self):
        self.run_stage()

    def test_changed_hash_rate_duplicate_or_nonunit_weights_cannot_publish(self):
        for case in ['bad_hash','bad_rate','duplicate','bad_weight']:
            with self.subTest(case=case):self.run_stage(**{case:True})

    def test_original_config_filename_is_resolved_per_chunk_and_must_be_unique(self):
        self.run_stage(config_directory=True)
        self.run_stage(config_directory=True,ambiguous_config=True)

    def test_prior_source_overlap_is_rejected_without_rejecting_disjoint_inputs(self):
        for kind in ['hash','RLE','run','unrelated']:
            with self.subTest(kind=kind):self.run_stage(prior_overlap=kind)


if __name__ == '__main__':
    unittest.main()
