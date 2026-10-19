# Code style: AI-code. AI-generated persistent-controller regression tests.
import errno
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import watch_dark_photon_production as controller
from dark_photon_sample_names import physical_point_name


class DurableControllerRecords(unittest.TestCase):
    def test_transient_afs_timeout_retries_but_permission_failure_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            original = Path.write_text
            attempts = []
            def write(target, content):
                attempts.append(target)
                if len(attempts) == 1:
                    raise TimeoutError(errno.ETIMEDOUT, 'AFS temporarily unavailable')
                return original(target, content)
            with patch.object(Path, 'write_text', write), patch.object(controller.time, 'sleep') as delay:
                controller.save(path, dict(heartbeat=1))
            self.assertEqual(json.loads(path.read_text()), dict(heartbeat=1))
            delay.assert_called_once_with(2)
            with patch.object(Path, 'write_text', side_effect=PermissionError(errno.EACCES, 'permission denied')), \
                    patch.object(controller.time, 'sleep') as delay:
                with self.assertRaises(PermissionError):
                    controller.save(path, dict(heartbeat=2))
                delay.assert_not_called()
            self.assertEqual(json.loads(path.read_text()), dict(heartbeat=1))


class ProductionControllerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.state = self.base / 'live_status.json'
        self.config = dict(schema='shift-bsm-campaign-alias-candidates-v1', alias='latest',
                           source_records=[], settings={})
        self.old = self.production('20261012-same-settings', 200)
        self.new = self.production('20261013-same-settings', 1000)
        self.config_path = self.base / 'candidate_map.json'
        self.save_config()

    def production(self, campaign, events, *, point='m15_prompt', epsilon=.001):
        directory = self.base / 'deployments' / campaign
        directory.mkdir(parents=True)
        output = self.base / 'data' / ('darkphoton_' + point) / campaign
        output.mkdir(parents=True)
        row = dict(point=point, mass_gev=15, epsilon=epsilon,
                   proper_ctau_mm=.000001, sampling_epsilon=.005,
                   target_mean_lab_flight_m=None, events=events,
                   final_output_base=str(output))
        plan = directory / 'plan.json'
        plan.write_text(json.dumps(dict(campaign=campaign, points=[row])))
        (directory / 'seal.json').write_text(json.dumps(dict(campaign=campaign)))
        generator = directory / 'generator.py'
        generator.write_text('fixed physics settings\n')
        source = dict(campaign=campaign, plan_path=str(plan),
                      plan_sha256=controller.sha256(plan),
                      seal_sha256=controller.sha256(directory / 'seal.json'))
        self.config['source_records'].append(source)
        self.config['settings'][campaign] = dict(generator=dict(path=str(generator),
                                                              sha256=controller.sha256(generator)))
        return dict(source=source, directory=directory, row=row, output=output)

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config))

    def configuration(self):
        return controller.load_configuration(self.config_path)

    def complete(self, production):
        path = production['output'] / 'histograms_merged' / 'ntuple_0.root'
        path.parent.mkdir()
        path.write_bytes(b'closed merged histogram publication')
        report = dict(complete=True, point=production['row']['point'], events=production['row']['events'],
            plan_sha256=production['source']['plan_sha256'],
            seal_sha256=production['source']['seal_sha256'],
            final_output_base=str(production['output']),
            score_histograms=dict(marker=dict(complete=True, published=dict(histograms=dict(
                path=str(path), bytes=path.stat().st_size, sha256=controller.sha256(path))))))
        directory = production['directory'] / 'completed_points'
        directory.mkdir()
        (directory / (production['row']['point'] + '.json')).write_text(json.dumps(report))
        return path

    def preview(self):
        campaign = '20261012-compatible-partial-preview'
        directory = self.old['output'].parent / campaign
        histogram = directory / 'histograms_merged' / 'ntuple_0.root'
        histogram.parent.mkdir(parents=True)
        histogram.write_bytes(b'audited partial histogram')
        receipt = directory / 'metadata' / 'partial_preview.json'
        receipt.parent.mkdir()
        receipt.write_text(json.dumps(dict(point='m15_prompt', events=180, partial=True)))
        record = dict(point='m15_prompt', campaign=campaign,
            condition_campaign=self.old['source']['campaign'],
            source_plan_sha256=self.old['source']['plan_sha256'], events=180,
            histogram_path=str(histogram), histogram_bytes=histogram.stat().st_size,
            histogram_sha256=controller.sha256(histogram), receipt_path=str(receipt),
            receipt_sha256=controller.sha256(receipt))
        self.config['external_ready_inputs'] = [record]
        self.save_config()
        return record

    def step(self, configuration=None, *, quota_known=True, query_error=False):
        if configuration is None:
            configuration = self.configuration()
        snapshot = dict(health='running', scheduler_state='known')
        quota = dict(eos=dict(free_files=1000, free_logical_bytes=10**9)) if quota_known else dict(state='unknown')
        effect = RuntimeError('Scheduler temporarily unavailable') if query_error else None
        with patch.object(controller, 'scheduler_snapshot', return_value=snapshot, side_effect=effect), \
                patch.object(controller, 'quota_snapshot', return_value=quota):
            return controller.step(configuration, self.state, publish=True)

    @property
    def alias(self):
        return self.old['output'].parent / 'latest'

    def test_ready_old_output_remains_selected_while_new_production_is_pending(self):
        self.complete(self.old)
        (self.new['output'] / 'histograms_merged').mkdir()
        (self.new['output'] / 'histograms_merged' / 'partial-upload.root').write_bytes(b'unfinished')
        state = self.step()
        self.assertEqual(self.alias.readlink().name, self.old['source']['campaign'])
        self.assertEqual(state['ready_points'], 1)
        self.assertEqual(state['latest_targets_ready'], 0)
        self.assertFalse(state['complete'])

    def test_closed_verified_new_merged_output_advances_alias(self):
        self.complete(self.old)
        self.step()
        self.complete(self.new)
        state = self.step()
        self.assertEqual(self.alias.readlink().name, self.new['source']['campaign'])
        self.assertTrue(state['points']['m15_prompt']['alias_changed'])
        self.assertTrue(state['complete'])
        self.assertTrue(self.old['output'].is_dir())

    def test_ready_epsilon_point_gets_latest_alias_without_renaming_old_points(self):
        epsilon = self.production('20261013-epsilon-grid', 50,
                                  point='m15_epsilon_1e-06', epsilon=1.e-6)
        self.complete(self.old)
        self.complete(epsilon)
        self.save_config()
        state = self.step()
        point = state['points']['m15_epsilon_1e-06']
        self.assertTrue(point['ready'])
        self.assertEqual((epsilon['output'].parent / 'latest').readlink().name,
                         epsilon['source']['campaign'])
        self.assertTrue(state['points']['m15_prompt']['ready'])

    def test_canonical_lab_exponents_load_as_candidates_and_owned_points(self):
        productions = []
        for index, flight in enumerate((2.5787102372956833e-8, 999.9)):
            point = physical_point_name(15., flight)
            production = self.production('20261016-lab-exponent-' + str(index), 50,
                                         point=point)
            self.complete(production)
            productions.append(production)
        self.config['owned_points'] = [production['row']['point'] for production in productions]
        self.save_config()
        state = self.step()
        self.assertEqual(set(state['points']), {'m15_lab_2p58em08m', 'm15_lab_1ep03m'})
        self.assertTrue(state['complete'])
        for production in productions:
            self.assertTrue(state['points'][production['row']['point']]['ready'])
            self.assertEqual((production['output'].parent / 'latest').readlink().name,
                             production['source']['campaign'])

    def test_restart_reuses_state_without_republishing_or_losing_old_output(self):
        self.complete(self.old)
        first = self.step()
        second = self.step(self.configuration())
        self.assertEqual(first['started_epoch'], second['started_epoch'])
        self.assertEqual(second['heartbeat_count'], 2)
        self.assertFalse(second['points']['m15_prompt']['alias_changed'])

    def test_owned_points_limit_alias_updates_without_losing_allowed_older_targets(self):
        other = self.production('20261013-other-point',50,point='m30_epsilon_1e-06')
        self.complete(self.old)
        self.complete(other)
        self.config['owned_points'] = ['m15_prompt']
        self.save_config()
        first = self.step()
        self.assertEqual(first['expected_points'],1)
        self.assertEqual(self.alias.readlink().name,self.old['source']['campaign'])
        self.assertFalse((other['output'].parent/'latest').is_symlink())
        self.complete(self.new)
        second = self.step()
        self.assertEqual(self.alias.readlink().name,self.new['source']['campaign'])
        self.assertTrue(second['complete'])

    def test_invalid_or_unknown_owned_point_selection_is_rejected(self):
        for owned in ([],['m15_prompt','m15_prompt'],['m99_prompt'],'m15_prompt',[None]):
            self.config['owned_points'] = owned
            self.save_config()
            with self.assertRaisesRegex(ValueError,'Owned points'):
                self.configuration()

    def test_incompatible_generator_settings_are_rejected_before_publication(self):
        artifact = self.config['settings'][self.new['source']['campaign']]['generator']
        Path(artifact['path']).write_text('different physical model\n')
        artifact['sha256'] = controller.sha256(artifact['path'])
        self.save_config()
        with self.assertRaisesRegex(ValueError, 'Incompatible settings'):
            self.configuration()
        self.assertFalse(self.alias.is_symlink())

    def test_mislabeled_overlapping_physical_point_is_rejected(self):
        path = self.new['directory'] / 'plan.json'
        plan = json.loads(path.read_text())
        plan['points'][0]['epsilon'] = .002
        path.write_text(json.dumps(plan))
        self.new['source']['plan_sha256'] = controller.sha256(path)
        self.save_config()
        with self.assertRaisesRegex(ValueError, 'incompatible physical settings'):
            self.configuration()

    def test_corrupt_new_output_does_not_hide_good_old_histograms(self):
        self.complete(self.old)
        bad = self.complete(self.new)
        bad.write_bytes(b'changed after verified publication')
        state = self.step()
        self.assertEqual(self.alias.readlink().name, self.old['source']['campaign'])
        self.assertTrue(state['attention_required'])
        self.assertTrue(state['points']['m15_prompt']['candidate_errors'])

    def test_real_directory_at_alias_is_protected(self):
        self.complete(self.old)
        self.alias.mkdir()
        marker = self.alias / 'protected.root'
        marker.write_bytes(b'preserve me')
        state = self.step()
        self.assertEqual(marker.read_bytes(), b'preserve me')
        self.assertIn('real alias directory', state['points']['m15_prompt']['publication_error'])

    def test_unknown_quota_preserves_existing_alias(self):
        self.complete(self.old)
        self.step()
        self.complete(self.new)
        state = self.step(quota_known=False)
        self.assertEqual(self.alias.readlink().name, self.old['source']['campaign'])
        self.assertIn('known EOS quota', state['points']['m15_prompt']['publication_error'])

    def test_scheduler_outage_is_unknown_not_failed_and_cannot_trigger_job_actions(self):
        self.complete(self.old)
        state = self.step(query_error=True)
        self.assertTrue(state['attention_required'])
        self.assertEqual(state['productions'][0]['scheduler_state'], 'unknown')
        self.assertTrue(state['points']['m15_prompt']['ready'])

    def test_changed_live_plan_blocks_only_affected_campaign(self):
        self.complete(self.old)
        self.complete(self.new)
        configuration = self.configuration()
        (self.new['directory'] / 'plan.json').write_text('{}')
        state = self.step(configuration)
        self.assertEqual(self.alias.readlink().name, self.old['source']['campaign'])
        self.assertEqual(state['productions'][1]['health'], 'inputs_changed')

    def test_restart_with_another_config_preserves_previous_state(self):
        self.complete(self.old)
        previous = self.step()
        self.config['afs_quota_path'] = '/different-config'
        self.save_config()
        with self.assertRaisesRegex(ValueError, 'another frozen config'):
            self.step()
        self.assertEqual(json.loads(self.state.read_text()), previous)

    def test_audited_partial_preview_is_retained_then_replaced_by_new_completed_run(self):
        preview = self.preview()
        first = self.step()
        self.assertEqual(self.alias.readlink().name, preview['campaign'])
        self.assertTrue(first['points']['m15_prompt']['partial'])
        self.assertFalse(first['complete'])
        self.complete(self.new)
        second = self.step()
        self.assertEqual(self.alias.readlink().name, self.new['source']['campaign'])
        self.assertEqual(second['points']['m15_prompt']['events'], 1000)
        self.assertNotIn('partial', second['points']['m15_prompt'])

    def test_preview_cannot_claim_other_conditions_or_exceed_original_gen(self):
        preview = self.preview()
        preview['source_plan_sha256'] = '0' * 64
        self.save_config()
        with self.assertRaisesRegex(ValueError, 'condition authority'):
            self.configuration()
        preview['source_plan_sha256'] = self.old['source']['plan_sha256']
        preview['events'] = 201
        self.save_config()
        with self.assertRaisesRegex(ValueError, 'Preview events differ'):
            self.configuration()

    def test_preview_file_corruption_preserves_link_and_reports_failure(self):
        preview = self.preview()
        self.step()
        Path(preview['histogram_path']).write_bytes(b'different content after review')
        state = self.step()
        self.assertEqual(self.alias.readlink().name, preview['campaign'])
        self.assertTrue(state['attention_required'])
        self.assertFalse(state['points']['m15_prompt']['ready'])


class SchedulerStatusTest(unittest.TestCase):
    def test_only_exact_metrics_can_establish_terminal_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'submission_receipt.json').write_text('{"manager_cluster":123}')
            source = dict(directory=directory, campaign='20261013-test')
            with patch.object(controller, 'query', return_value=[]):
                absent = controller.scheduler_snapshot(source)
                self.assertEqual(absent['health'], 'manager_not_live')
                self.assertNotIn('terminal', absent)
                (directory / 'pipeline.dag.metrics').write_text(json.dumps(dict(
                    dagman_id=123, end_time=10, exitcode=1)))
                failed = controller.scheduler_snapshot(source)
                self.assertEqual(failed['health'], 'failed')
                self.assertTrue(failed['terminal'])


class RpcSafetyTest(unittest.TestCase):
    def test_rpc_directory_resolution_uses_native_eos_fileinfo_mode(self):
        legacy = '/eos/user/j/jniedzie/shift_cmssw/darkphoton_m5_lab_5m'
        canonical = '/eos/user/j/jniedzie/shift_cmssw/darkphoton_m5_ctau_39p640715mm'
        # Directory fileinfo has no status/type; EOS reports an octal mode.
        outputs = [f'keylength.file=53 file={legacy} size=30 status=symlink '
                   'target=darkphoton_m5_ctau_39p640715mm mode=0000',
                   f'keylength.file=63 file={canonical} treesize=334960488 '
                   'treecontainers=75 treefiles=272 container=3 files=2 mode=42755 uid=32542']
        with tempfile.TemporaryDirectory() as temporary:
            rpc = controller.EosRpc('root://eosuser.cern.ch', temporary)
            with patch.object(rpc, 'command', side_effect=outputs) as command:
                self.assertEqual(rpc.canonical_alias_path(legacy + '/latest'), Path(canonical) / 'latest')
            self.assertEqual([call.args[0][2:] for call in command.call_args_list],
                             [['fileinfo', legacy, '-m'], ['fileinfo', canonical, '-m']])

    def test_rpc_cannot_replace_a_real_file_or_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            rpc = controller.EosRpc('root://eosuser.cern.ch', temporary)
            with patch.object(rpc, 'metadata', side_effect=[dict(mode='42755', container='3', files='2'), dict(status='file')]), \
                    patch.object(rpc, 'command') as command:
                with self.assertRaisesRegex(ValueError, 'real alias directory'):
                    rpc.publish_alias('/eos/user/point/latest', '20261013-new', ['20261013-new'])
            command.assert_not_called()

    def test_rpc_replacement_requires_reviewed_previous_target_and_confirms_new_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            rpc = controller.EosRpc('root://eosuser.cern.ch', temporary)
            with patch.object(rpc, 'metadata', side_effect=[dict(mode='42755', container='3', files='2'),
                                                          dict(status='symlink', target='20261012-old'),
                                                          dict(status='symlink', target='20261013-new')]), \
                    patch.object(rpc, 'command') as command:
                self.assertTrue(rpc.publish_alias('/eos/user/point/latest', '20261013-new',
                                                  ['20261012-old', '20261013-new']))
            arguments = command.call_args.args[0]
            self.assertEqual(arguments[2:5], ['file', 'symlink', '-f'])
            self.assertEqual(arguments[-2:], ['/eos/user/point/latest', '20261013-new'])
            self.assertNotIn('rm', arguments)

    def test_rpc_publishes_through_renamed_sample_parent_and_reads_same_alias(self):
        for target in ('darkphoton_m5_ctau_123mm', '/eos/user/data/darkphoton_m5_ctau_123mm'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                rpc = controller.EosRpc('root://eosuser.cern.ch', temporary)
                legacy = Path('/eos/user/data/darkphoton_m5_lab_5m')
                canonical = legacy.with_name('darkphoton_m5_ctau_123mm')
                aliases = {canonical / 'latest': '20261012-old'}
                def metadata(path):
                    path = Path(path)
                    if path == legacy:
                        return dict(status='symlink', target=target)
                    if path == canonical:
                        return dict(mode='42755', container='3', files='2')
                    if path in aliases:
                        return dict(status='symlink', target=aliases[path])
                    self.fail('Unexpected metadata path: ' + str(path))
                def publish(arguments):
                    self.assertEqual(arguments[2:], ['file', 'symlink', '-f',
                                                     str(canonical / 'latest'), '20261013-new'])
                    aliases[canonical / 'latest'] = arguments[-1]
                    return ''
                with patch.object(rpc, 'metadata', side_effect=metadata), \
                        patch.object(rpc, 'command', side_effect=publish) as command:
                    self.assertTrue(rpc.publish_alias(legacy / 'latest', '20261013-new',
                                                      ['20261012-old', '20261013-new']))
                    self.assertEqual(rpc.alias_target(legacy / 'latest'), '20261013-new')
                    self.assertFalse(rpc.publish_alias(legacy / 'latest', '20261013-new',
                                                       ['20261012-old', '20261013-new']))
                    command.assert_called_once()

    def test_rpc_parent_links_cannot_escape_reviewed_sample_namespace(self):
        for target in ('/eos/user/other/point', '../other/point', 'root://other/point', ''):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                rpc = controller.EosRpc('root://eosuser.cern.ch', temporary)
                with patch.object(rpc, 'metadata', return_value=dict(status='symlink', target=target)), \
                        patch.object(rpc, 'command') as command:
                    with self.assertRaisesRegex(ValueError, 'namespace|no target'):
                        rpc.publish_alias('/eos/user/point/latest', '20261013-new', ['20261013-new'])
                    command.assert_not_called()

    def test_rpc_parent_symlink_loop_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            rpc = controller.EosRpc('root://eosuser.cern.ch', temporary)
            def metadata(path):
                return dict(status='symlink', target='second' if Path(path).name == 'point' else 'point')
            with patch.object(rpc, 'metadata', side_effect=metadata), \
                    patch.object(rpc, 'command') as command:
                with self.assertRaisesRegex(ValueError, 'symlink loop'):
                    rpc.publish_alias('/eos/user/point/latest', '20261013-new', ['20261013-new'])
                command.assert_not_called()

    def test_rpc_renamed_parent_keeps_reviewed_existing_campaign_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            rpc = controller.EosRpc('root://eosuser.cern.ch', temporary)
            with patch.object(rpc, 'metadata', side_effect=[dict(status='symlink', target='renamed'),
                                                          dict(mode='42755', container='3', files='2'),
                                                          dict(status='symlink', target='unreviewed')]), \
                    patch.object(rpc, 'command') as command:
                with self.assertRaisesRegex(ValueError, 'outside reviewed'):
                    rpc.publish_alias('/eos/user/point/latest', '20261013-new', ['20261013-new'])
                command.assert_not_called()

    def test_rpc_cache_does_not_hide_same_size_source_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            rpc = controller.EosRpc('root://eosuser.cern.ch', temporary)
            payload = b'reviewed file'
            expected = controller.hashlib.sha256(payload).hexdigest()
            def copy(arguments):
                Path(arguments[-1]).write_bytes(payload)
                return ''
            with patch.object(rpc, 'metadata', return_value=dict(size=len(payload))), \
                    patch.object(rpc, 'command', side_effect=copy):
                rpc.verify_file('/eos/user/file.root', len(payload), expected)
                payload = b'corruptd file'
                with self.assertRaisesRegex(ValueError, 'input changed'):
                    rpc.verify_file('/eos/user/file.root', len(payload), expected)


if __name__ == '__main__':
    unittest.main()
