# Code style: AI-code. AI-generated detached GEN stage identity and handoff tests.
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_dark_photon_gen_stage import prepare, validate_plan
from run_dark_photon_gen_stage import (check_afs_margin, collect, publish_gen, read, run_point,
                                      sha256, validate_population, verify_seal, write_new)


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


def audited_population(base):
    plan, unused_prior, contract = fixture(base)
    row = plan['points'][0]; gen = Path(row['gen_directory']); gen.mkdir()
    write_new(gen / 'contract.json', contract)
    payload = b'synthetic complete ROOT fixture'
    (gen / 'gen.root').write_bytes(payload)
    source_path = Path(row['gen_argv'][1]).parent / 'dark_photon_gen_cfg.py'
    (gen / source_path.name).write_bytes(source_path.read_bytes())
    write_new(gen / 'manifest.json', dict(status='GEN runtime audit passed; physics provisional',
        output_sha256=sha256(gen / 'gen.root'), source_sha256={source_path.name:sha256(source_path)}))
    write_new(gen / 'validation.json', dict(runtime_validated=True, events=3, sum_weights=3.,
        sum_weights_squared=3., event_ids=[[row['run_number'],1,number] for number in [1,3,8]],
        event_rows=[dict(weight=1.) for _ in range(3)], proposal_ledger=dict(n_accepted=3),
        full_gen_denominator=dict(n_accepted=3, n_tried=8)))
    config = dict(scratch_base=str(base), publication_timeout_seconds=60,
                  retain_local_gen_root=False, prior_inventories=[])
    return row, config, payload


def remote_transport(payload):
    def transport(argv, **unused):
        if argv[0] == '/usr/bin/xrdfs':
            return subprocess.CompletedProcess(argv, 0, '', '')
        if argv[0] == '/usr/bin/xrdcp' and argv[-2].startswith('root://'):
            Path(argv[-1]).write_bytes(payload)
            return subprocess.CompletedProcess(argv, 0, '', '')
        raise AssertionError('Unexpected external mutation: ' + str(argv))
    return transport


def audited_meson_population(base):
    row, config, payload = audited_population(base)
    gen = Path(row['gen_directory'])
    (gen / 'meson_source_ledger.jsonl').write_text('Complete source rows already scanned by the runtime audit\n')
    model = dict(schema='shift-dark-photon-meson-model-v1', parents=[])
    write_new(gen / 'meson_model.json', model)
    exposure = dict(schema='shift-meson-source-exposure-v1', fixed_source_collisions=103,
        source_collisions=103, accepted_signal_events=3, sum_native_weights=103.,
        sum_native_weights_squared=103., native_n_tried=110, native_n_accepted=103,
        unit_output_gen_weights=True, coefficient_includes_br_mumu=True,
        coefficient_excludes_epsilon_squared=True, source_ledger_sha256=sha256(gen / 'meson_source_ledger.jsonl'))
    write_new(gen / 'source_exposure.json', exposure)
    contract = read(gen / 'contract.json')
    contract.update(production_mechanism='eta-etaprime-radiative', source_exposure=exposure,
        meson_model_card=model, source_exposure_sha256=sha256(gen / 'source_exposure.json'),
        source_ledger_sha256=sha256(gen / 'meson_source_ledger.jsonl'),
        meson_model_card_sha256=sha256(gen / 'meson_model.json'))
    (gen / 'contract.json').write_text(json.dumps(contract))
    Path(row['signal_contract']).write_text(json.dumps(contract))
    audit = read(gen / 'validation.json')
    audit.update(source_exposure=exposure, source_exposure_sha256=contract['source_exposure_sha256'],
        source_ledger_sha256=contract['source_ledger_sha256'],
        proposal_ledger=dict(n_tried=110,n_selected=105,n_accepted=103))
    audit['full_gen_denominator']['source_collisions']=103
    (gen / 'validation.json').write_text(json.dumps(audit))
    return row, config, payload


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

    def test_remote_only_receipt_allows_freshly_verified_collection_and_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); row, config, payload = audited_population(base)
            gen = Path(row['gen_directory'])
            with patch('run_dark_photon_gen_stage.command', side_effect=remote_transport(payload)) as remote:
                publication, parent = publish_gen(row, config)
                self.assertFalse((gen / 'gen.root').exists())
                receipt = read(gen / 'root_publication.json')
                self.assertEqual(receipt['metadata_sha256']['validation.json'], sha256(gen / 'validation.json'))
                self.assertEqual(publication['sha256'], read(gen / 'manifest.json')['output_sha256'])
                self.assertEqual(parent['event_ids'][1][2], 3)
                self.assertEqual(validate_population(row, config)[0]['events'], 3)
                self.assertEqual(publish_gen(row, config), (publication, parent))
                stage = base / 'stage'; stage.mkdir(); (stage / 'statuses').mkdir()
                write_new(stage / 'statuses' / (row['point'] + '.json'), dict(
                    complete=True, seal_sha256='seal', parent=parent, publication=publication))
                with patch('run_dark_photon_gen_stage.verify_seal', return_value=(dict(points=[row]), config)):
                    run_point(stage, 'seal', row['point'])
                    collect(stage, 'seal')
                self.assertTrue(read(stage / 'gen_inventory.json')['complete'])
                self.assertGreaterEqual(sum(call.args[0][0] == '/usr/bin/xrdcp'
                                            for call in remote.call_args_list), 6)
                self.assertFalse((gen / 'gen.root').exists())

    def test_remote_only_refuses_changed_audit_evidence_or_changed_canonical_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); row, config, payload = audited_population(base)
            gen = Path(row['gen_directory'])
            with patch('run_dark_photon_gen_stage.command', side_effect=remote_transport(payload)):
                publish_gen(row, config)
            with patch('run_dark_photon_gen_stage.command', side_effect=remote_transport(b'corrupt ROOT')):
                with self.assertRaisesRegex(ValueError, 'readback differs'):
                    validate_population(row, config)
            audit = read(gen / 'validation.json'); audit['changed_evidence'] = True
            (gen / 'validation.json').write_text(json.dumps(audit))
            with patch('run_dark_photon_gen_stage.command') as remote:
                with self.assertRaisesRegex(ValueError, 'publication proof differs'):
                    validate_population(row, config)
                remote.assert_not_called()

    def test_failed_readback_keeps_new_local_root_and_does_not_create_retention_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); row, config, unused = audited_population(base)
            gen = Path(row['gen_directory'])
            with patch('run_dark_photon_gen_stage.command', side_effect=remote_transport(b'incorrect')):
                with self.assertRaisesRegex(ValueError, 'readback differs'):
                    publish_gen(row, config)
            self.assertTrue((gen / 'gen.root').exists())
            self.assertFalse((gen / 'root_publication.json').exists())

    def test_missing_local_root_without_exact_publication_proof_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); row, config, payload = audited_population(base)
            gen = Path(row['gen_directory'])
            with patch('run_dark_photon_gen_stage.command', side_effect=remote_transport(payload)):
                publish_gen(row, config)
            receipt = read(gen / 'root_publication.json'); receipt['path'] += '.other'
            (gen / 'root_publication.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'publication proof differs'):
                validate_population(row, config)
            (gen / 'root_publication.json').unlink()
            with self.assertRaises(FileNotFoundError):
                validate_population(row, config)

    def test_default_retention_keeps_local_root_for_old_deployments(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); row, config, payload = audited_population(base)
            config.pop('retain_local_gen_root')
            with patch('run_dark_photon_gen_stage.command', side_effect=remote_transport(payload)):
                publish_gen(row, config)
            self.assertTrue((Path(row['gen_directory']) / 'gen.root').exists())
            self.assertFalse((Path(row['gen_directory']) / 'root_publication.json').exists())

    def test_remote_only_gate_preserves_the_reviewed_metadata_reservation(self):
        config = dict(workspace='/synthetic', minimum_afs_free_mb=1000,
                      retain_local_gen_root=False, afs_metadata_reservation_mb=256)
        with patch('run_dark_photon_gen_stage.command', return_value=subprocess.CompletedProcess(
                [], 0, 'Volume Name Quota Used\nvolume 2048000 819200\n', '')):
            with self.assertRaisesRegex(ValueError, 'margin is too small'):
                check_afs_margin(config)
        config['afs_metadata_reservation_mb'] = 0
        with patch('run_dark_photon_gen_stage.command', return_value=subprocess.CompletedProcess(
                [], 0, 'Volume Name Quota Used\nvolume 2048000 819200\n', '')):
            with self.assertRaisesRegex(ValueError, 'positive AFS metadata'):
                check_afs_margin(config)


class MesonGenStageTest(unittest.TestCase):
    def test_conditional_signal_and_source_counts_pass_without_recomputing_physics(self):
        with tempfile.TemporaryDirectory() as temporary:
            row, config, unused = audited_meson_population(Path(temporary))
            # Only hashes are needed here; the runtime audit owned the full
            # ledger scan and no meson physics module is imported at handoff.
            with patch('export_dark_photon_cross_sections.meson_source_exposure', side_effect=AssertionError('Repeated scan')):
                audit, unused_manifest, unused_contract = validate_population(row, config)
            self.assertEqual(audit['events'],3)
            self.assertEqual(audit['proposal_ledger']['n_accepted'],103)

    def test_each_pinned_meson_input_is_checked(self):
        for filename in ('source_exposure.json','meson_source_ledger.jsonl','meson_model.json'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                row, config, unused = audited_meson_population(Path(temporary))
                with (Path(row['gen_directory'])/filename).open('a') as output:output.write('\nchanged')
                with self.assertRaisesRegex(ValueError,'Pinned meson source .*changed'):
                    validate_population(row,config)

    def test_changed_source_runtime_boundary_is_rejected(self):
        mutations=[lambda audit:audit['proposal_ledger'].update(n_accepted=3),
            lambda audit:audit['proposal_ledger'].update(n_tried=111),
            lambda audit:audit['source_exposure'].update(fixed_source_collisions=3),
            lambda audit:audit['source_exposure'].update(accepted_signal_events=103),
            lambda audit:audit['full_gen_denominator'].update(source_collisions=3),
            lambda audit:audit.update(source_ledger_sha256='changed'),
            lambda audit:audit.update(source_exposure_sha256='changed')]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                row, config, unused = audited_meson_population(Path(temporary))
                path=Path(row['gen_directory'])/'validation.json'
                audit=read(path);mutation(audit);path.write_text(json.dumps(audit))
                with self.assertRaises(ValueError):validate_population(row,config)


if __name__ == '__main__':
    unittest.main()
