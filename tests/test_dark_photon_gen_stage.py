# Code style: AI-code. AI-generated detached GEN stage identity and handoff tests.
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_dark_photon_gen_stage import prepare, validate_plan
from run_dark_photon_gen_stage import read, sha256, validate_population, verify_seal, write_new


def fixture(base):
    source = base / 'sources'; source.mkdir()
    scripts = {}
    for name in ['run_dark_photon_gen.py', 'dark_photon_gen_cfg.py']:
        path = source / name; path.write_text('# synthetic generator source\n')
        scripts[name] = dict(frozen_copy=str(path), sha256=sha256(path))
    reference = base / 'references.json'; write_new(reference, dict(synthetic=True))
    contract = dict(requested_events=3, event_budget_mode='outputAccepted', maximum_framework_events=30,
                    mass_gev=15., epsilon=1e-7, seed=251014001, run_number=251014001)
    contract_path = base / 'contract.json'; write_new(contract_path, contract)
    row = dict(point='m15_lab_5m', events=3, mass_gev=15., epsilon=1e-7, proper_ctau_mm=1.,
        seed=251014001, run_number=251014001, signal_contract=str(contract_path),
        signal_contract_sha256=sha256(contract_path), gen_directory=str(base / 'gen'),
        gen_argv=['/usr/bin/python3', str(source / 'run_dark_photon_gen.py')],
        final_output_base='/eos/user/t/test/darkphoton_m15/20261014-test')
    plan = dict(schema='shift-dark-photon-grid-plan-v1', native_width_preflight_passed=True,
        detector_stage_prepared=True, event_budget_mode='outputAccepted', points=[row],
        runtime=dict(cmssw_base='/synthetic/cmssw'), frozen_dependencies=dict(
            sources=scripts, templates={}, references=dict(frozen_copy=str(reference), sha256=sha256(reference))))
    prior = base / 'prior.json'; write_new(prior, dict(points={'old':dict(seed=123, event_ids=[[123,1,1]])}))
    return plan, prior, contract


class DetachedGenStageTest(unittest.TestCase):
    def test_preparation_rejects_old_runs_before_materializing_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); plan, prior, unused = fixture(base)
            self.assertEqual(validate_plan(plan, [prior])['events'], 3)
            changed = copy.deepcopy(plan); changed['points'][0]['run_number'] = 123
            with self.assertRaisesRegex(ValueError, 'namespace'):
                validate_plan(changed, [prior])

    def test_sealed_dag_keeps_gen_and_downstream_order_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); plan, prior, unused = fixture(base)
            plan_path = base / 'preparation.json'; write_new(plan_path, plan)
            runtime = base / 'runtime.json'; write_new(runtime, dict(files={}))
            handoff = base / 'handoff.json'; write_new(handoff, dict(handoff_argvs=[['/usr/bin/true']]))
            args = argparse.Namespace(output=base/'stage', plan=plan_path, prior_inventories=[prior],
                runtime_fingerprint=runtime, handoff_config=handoff, handoff_dependencies=[],
                workspace=base, scratch_base=base, gen_workers=3, gen_worker_timeout=14400,
                publication_timeout=600, handoff_timeout=3600, minimum_afs_free_mb=1000)
            receipt = prepare(args)
            dag = (args.output / 'pipeline.dag').read_text()
            self.assertIn('PARENT G_m15_lab_5m CHILD COLLECT', dag)
            self.assertIn('PARENT COLLECT CHILD HANDOFF', dag)
            self.assertIn('MAXJOBS GEN 3', dag)
            self.assertNotIn('RETRY', dag)
            verify_seal(args.output, receipt['seal_sha256'])
            Path(plan['points'][0]['gen_argv'][1]).write_text('# mutated generator\n')
            with self.assertRaisesRegex(ValueError, 'dependency changed'):
                verify_seal(args.output, receipt['seal_sha256'])

    def test_accepted_generator_gaps_are_kept_and_bad_weights_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); plan, unused_prior, contract = fixture(base)
            row = plan['points'][0]; gen = Path(row['gen_directory']); gen.mkdir()
            write_new(gen / 'contract.json', contract)
            (gen / 'gen.root').write_bytes(b'synthetic ROOT fixture')
            source_path = Path(row['gen_argv'][1]).parent / 'dark_photon_gen_cfg.py'
            (gen / source_path.name).write_bytes(source_path.read_bytes())
            manifest = dict(status='GEN runtime audit passed; physics provisional',
                output_sha256=sha256(gen/'gen.root'), source_sha256={source_path.name:sha256(source_path)})
            write_new(gen / 'manifest.json', manifest)
            audit = dict(runtime_validated=True, events=3, sum_weights=3., sum_weights_squared=3.,
                event_ids=[[row['run_number'],1,number] for number in [1,3,8]],
                event_rows=[dict(weight=1.) for _ in range(3)], proposal_ledger=dict(n_accepted=3))
            write_new(gen / 'validation.json', audit)
            self.assertEqual(validate_population(row)[0]['event_ids'][1][2], 3)
            audit['event_rows'][0]['weight']=0.
            (gen/'validation.json').write_text(json.dumps(audit))
            with self.assertRaisesRegex(ValueError, 'native weights'):
                validate_population(row)


if __name__ == '__main__':
    unittest.main()
