# Code style: AI-code.
"""Explicit GEN sampling must preserve config, exposure and default semantics."""
import os
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_shift_smart_sm_source as source


class SourceSamplingTest(unittest.TestCase):
    def row(self, mode='auto', family='qcd', lower=2.):
        return dict(family=family, bounds=[lower, 5.], events_or_trials=1000,
                    seed=678911201, run=3612120001, generation_sampling=mode)

    def test_old_requests_keep_their_previous_route(self):
        for family in ['qcd', 'jpsi', 'dy']:
            for lower in [0., 1., 2., 5., 10., 20.]:
                row = dict(family=family, bounds=[lower, -1.])
                expected = 'weighted_trials' if family != 'dy' and lower > 0 else 'native_class'
                self.assertEqual(source.generation_sampling(row), expected)

    def test_explicit_modes_do_not_allow_unsupported_weighted_physics(self):
        self.assertEqual(source.generation_sampling(self.row('native_class')), 'native_class')
        for row in [self.row('weighted_trials', 'dy'), self.row('weighted_trials', lower=0.),
                    self.row('flatten_existing_weights')]:
            with self.assertRaises(ValueError):
                source.generation_sampling(row)

    def test_actual_gen_config_and_auditor_follow_the_selected_exposure(self):
        for mode in ['native_class', 'weighted_trials']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary);tools = base/'tools';fragment = tools/'fragments/QCD_SoftMpiPartition_FixedTarget_13p6TeV_pythia8_cff.py'
                fragment.parent.mkdir(parents=True);fragment.write_text('# synthetic fragment\n')
                directory = base/'raw';calls=[]

                def run(argv, log, **kwargs):
                    calls.append((argv, kwargs))
                    if argv[0] == 'cmsDriver.py':
                        (directory/'gen_cfg.py').write_text('# synthetic driver config\n')
                    if argv[0] == 'edmConfigDump':
                        Path(log).write_text('resolved config placeholder\n')
                    if 'audit' in str(log):
                        (directory/'validation.json').write_text('{}')

                with patch.object(source, 'TOOLS', tools), patch.object(source, 'run', run), \
                     patch.object(source, 'inspect_source', return_value='audited') as inspect, \
                     patch.dict(os.environ, CMSSW_BASE=str(base/'runtime'),
                                LD_PRELOAD='foreign-proposal.so'):
                    self.assertEqual(source.generate(dict(source_request=self.row(mode)), directory), 'audited')
                config = (directory/'gen_cfg.py').read_text()
                self.assertIn('pTHatMin = cms.double(2.0)', config)
                self.assertIn('pTHatMax = cms.double(5.0)', config)
                weighted = mode == 'weighted_trials'
                self.assertEqual('ShiftMpiWeightedProposalHook' in config, weighted)
                self.assertEqual(inspect.call_args.args[-1], weighted)
                cms_call = next(c for c in calls if c[0][0] == 'cmsRun')
                self.assertEqual('LD_PRELOAD' in cms_call[1]['env'], weighted)
                self.assertEqual(any('audit_shift_weighted_gen.py' in str(c[0]) for c in calls), weighted)
                self.assertEqual(any('audit_generation_chunk.py' in str(c[0]) for c in calls), not weighted)

    def test_wrong_exposure_is_rejected_before_reading_gen(self):
        with self.assertRaisesRegex(ValueError, 'exposure units'):
            source.inspect_source(self.row('native_class'), Path('/unused'), {}, None, True)

    def test_positive_bin_native_reuse_keeps_identity_and_accepted_exposure(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary);row = dict(self.row('native_class'), events_or_trials=1)
            identities = [[row['run'], 1, 1]]
            files = {'gen.root': b'synthetic GEN', 'validation.json': b'{}',
                     'resolved_gen_cfg.py': b'# synthetic resolved config\n'}
            publications = [dict(name=name, path='/eos/user/j/jniedzie/shift_cmssw_support/test/'+name, bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(), independent_sha256_readback=True)
                for name, data in files.items()]
            receipt = json.dumps(dict(schema='shift-smart-SM-native-source-v1',
                publications=publications, event_ids=identities,
                semantic_audit=dict(native_audit={}, weights=[1.]))).encode()
            files['source.json'] = receipt

            def download(argv, **kwargs):
                Path(argv[-1]).write_bytes(files[Path(argv[-2]).name])

            request = dict(source_request=row, resume_raw=dict(url='/eos/user/j/jniedzie/shift_cmssw_support/test/source.json',
                sha256=hashlib.sha256(receipt).hexdigest()))
            native = dict(denominator_kind='accepted_gen_events', full_source_events=1)
            with patch.object(source, 'command', download), patch.object(source, 'inspect_source',
                    return_value=(native, identities, [1.], {}, None)) as inspect:
                result = source.resume_source(request, base/'reused')
            self.assertFalse(inspect.call_args.args[-1])
            self.assertEqual(result[0]['full_source_events'], 1)
            self.assertEqual(result[1], identities)
            self.assertEqual(result[2], [1.])


if __name__ == '__main__':
    unittest.main()
