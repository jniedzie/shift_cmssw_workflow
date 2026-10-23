# Code style: AI-code. AI-generated complete-group and publication-contract tests.
"""Exercise exact identities, ROOT histogram sums and measured storage bounds."""
from array import array
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dark_photon_production_support import (
    collect, finalize, publication_paths, sha256, validate_complete_groups,
    validate_group_reports, validate_histogram_merge, validate_publication_canary,
)
from prepare_dark_photon_grid import plan_detector_stage
from prepare_dark_photon_production import extend_worker
from shift_production_selection import default_policy
from shift_material_preselection import checksum


def write(path, value):
    path.write_text(json.dumps(value))


def root_module():
    try:
        import ROOT
        return ROOT
    except ImportError:
        raise unittest.SkipTest('ROOT is required for the semantic merge checks')


def write_nano(path, identities, weights=None):
    ROOT = root_module()
    source = ROOT.TFile(str(path), 'RECREATE')
    tree = ROOT.TTree('Events', 'Synthetic signal identities')
    run, lumi, event, weight = array('I', [0]), array('I', [0]), array('Q', [0]), array('f', [1.])
    tree.Branch('run', run, 'run/i')
    tree.Branch('luminosityBlock', lumi, 'luminosityBlock/i')
    tree.Branch('event', event, 'event/l')
    tree.Branch('genWeight', weight, 'genWeight/F')
    for index, identity in enumerate(identities):
        run[0], lumi[0], event[0] = identity
        weight[0] = weights[index] if weights is not None else 1.
        tree.Fill()
    tree.Write()
    source.Close()


class CompleteGroups(unittest.TestCase):
    def plan(self):
        physical = dict(native_width_preflight_passed=True,
            points=[dict(point='m15_prompt', mass_gev=15., events=4021, run_number=8001)])
        stage = plan_detector_stage(physical, '20261014-statistics', '/eos/user/t/test/shift',
                                   events_per_job=20, group_events=2000, detector_job_base=4000001)
        row = dict(stage['points'][0], group_events=stage['group_events'])
        # Gaps in accepted GEN identities are deliberately retained.
        identities = [[8001, 1, 2 * index + 1] for index in range(4021)]
        return row, identities

    def test_large_point_has_complete_bounded_groups_and_short_final_chunk(self):
        row, identities = self.plan()
        groups = validate_complete_groups(row, identities)
        self.assertEqual([group['count'] for group in groups], [2000, 2000, 21])
        self.assertEqual([chunk['count'] for chunk in row['chunks'][-2:]], [20, 1])
        self.assertEqual(sum(group['count'] for group in groups), len(identities))

    def test_reused_identity_or_duplicate_chunk_is_rejected(self):
        row, identities = self.plan()
        duplicate = identities.copy()
        duplicate[-1] = duplicate[0]
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            validate_complete_groups(row, duplicate)
        row['chunks'][-1]['chunk'] = row['chunks'][0]['chunk']
        with self.assertRaisesRegex(ValueError, 'partition'):
            validate_complete_groups(row, identities)

    def test_groups_cannot_overlap_cut_chunks_or_omit_events(self):
        row, identities = self.plan()
        for mutate in [lambda value: value['groups'][1].update(skip=0),
                       lambda value: value['groups'][0].update(count=1999),
                       lambda value: value['groups'][0]['chunks'].pop(),
                       lambda value: value['groups'][1]['chunks'].append(0)]:
            damaged = copy.deepcopy(row)
            mutate(damaged)
            with self.assertRaises(ValueError):
                validate_complete_groups(damaged, identities)

    def test_chunks_above_twenty_and_unbounded_groups_are_rejected(self):
        row, identities = self.plan()
        row['chunks'][0]['count'] = 21
        with self.assertRaisesRegex(ValueError, 'partition'):
            validate_complete_groups(row, identities)
        row, identities = self.plan()
        row['group_events'] = 1000
        with self.assertRaisesRegex(ValueError, 'bounded'):
            validate_complete_groups(row, identities)


class WorkerProfiles(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        # Use the immutable qualified implementation, rather than a mock
        # version of its receipt checks or its full-chain invocation.
        qualified = Path(__file__).resolve().parents[2] / 'validation/bsm_stage2_20261013/production/chunk_worker.py'
        if not qualified.is_file():
            self.skipTest('Qualified deployment is not available in this checkout')
        text = extend_worker(qualified.read_text(), 'nano-metadata-v1')
        self.worker_path = self.base / 'chunk_worker.py'
        self.worker_path.write_text(text)
        specification = importlib.util.spec_from_file_location('_test_profile_worker', self.worker_path)
        self.worker = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(self.worker)
        self.chunk = dict(chunk=0, skip=0, count=20, job=4000001,
            nano='/eos/user/t/test/shift/darkphoton_m15_prompt/20261014-statistics/nanoAOD/chunk_000.root',
            metadata='/eos/user/t/test/shift/darkphoton_m15_prompt/20261014-statistics/metadata/chunk_000')
        self.plan = dict(publication_profile='nano-metadata-v1', points=[dict(point='m15_prompt',
                         events=20, chunks=[self.chunk])])
        self.expected = [[8001, 1, index + 1] for index in range(20)]
        self.inventory = dict(points={'m15_prompt': dict(events=20, event_ids=self.expected, gen_sha256='a' * 64)})
        self.origin = dict(seal_sha256='b' * 64, plan_sha256='c' * 64)
        write(self.base / 'plan.json', self.plan)

    def receipt(self):
        paths = publication_paths(self.chunk, 'nano-metadata-v1')
        return dict(complete=True, point='m15_prompt', chunk=0, events=20,
            expected_event_ids=self.expected, native_sumw=20., parent_gen_sha256='a' * 64,
            sample_role='validation_only', selection_applied=False, publication_profile='nano-metadata-v1',
            nano_sha256='d' * 64, **self.origin,
            published={tier: dict(path=path, bytes=100, sha256='d' * 64) for tier, path in paths.items()},
            audited_scratch_tiers={tier: dict(bytes=100, sha256='e' * 64)
                                  for tier in ('GEN_SIM', 'DIGI_RAW_HLT', 'AODSIM')})

    def test_nano_only_contract_keeps_full_scratch_audits_and_native_denominator(self):
        receipt = self.receipt()
        self.assertEqual(set(self.worker.publication_paths(self.chunk)), {'nanoAOD', 'metadata'})
        self.assertIs(self.worker.validate_chunk_status(self.plan, self.inventory, 'm15_prompt', 0,
                                                       receipt, self.origin), receipt)
        receipt['audited_scratch_tiers'].pop('AODSIM')
        with self.assertRaisesRegex(ValueError, 'scratch evidence'):
            self.worker.validate_chunk_status(self.plan, self.inventory, 'm15_prompt', 0, receipt, self.origin)

    def test_incomplete_publication_foreign_profile_and_changed_uid_fail(self):
        for mutate in [lambda value: value['published'].pop('metadata'),
                       lambda value: value.update(publication_profile='all-tiers-v1'),
                       lambda value: value.update(expected_event_ids=self.expected[:-1]),
                       lambda value: value.update(native_sumw=19.)]:
            receipt = self.receipt()
            mutate(receipt)
            with self.assertRaises(ValueError):
                self.worker.validate_chunk_status(self.plan, self.inventory, 'm15_prompt', 0, receipt, self.origin)

    def test_quota_hook_is_not_duplicated_for_an_extended_template(self):
        qualified = Path(__file__).resolve().parents[2] / 'validation/bsm_mass_extension_20261013/production/chunk_worker.py'
        extended = extend_worker(qualified.read_text(), 'nano-metadata-v1')
        self.assertEqual(extended.count('extra_bytes, extra_files, external = external_chunk_reservations(budget)'), 1)
        compile(extended, 'extended_worker', 'exec')


class CompleteGroupReports(unittest.TestCase):
    def setUp(self):
        physical = dict(native_width_preflight_passed=True,
                        points=[dict(point='m15_prompt', mass_gev=15., events=23, run_number=8001)])
        stage = plan_detector_stage(physical, '20261014-statistics', '/eos/user/t/test/shift',
                                   events_per_job=5, group_events=12, detector_job_base=4000001)
        self.row = dict(stage['points'][0], group_events=12)
        self.parent = dict(event_ids=[[8001, 1, 2 * index + 1] for index in range(23)], gen_sha256='a' * 64)
        self.seal, self.plan_hash, self.model = 'b' * 64, 'c' * 64, 'd' * 64
        prefix = self.row['final_output_base']
        publications, scored = [], []
        def artifact(path):
            return dict(path=path, sha256='e' * 64, bytes=100)
        for group in self.row['groups']:
            number, count = group['group'], group['count']
            publication = dict(group=number, events=count,
                event_ids=self.parent['event_ids'][group['skip']:group['skip'] + count],
                native_sumw=count, native_sumw2=count, grouped=artifact(group['nano']))
            publications.append(publication)
            metadata = prefix + f'/metadata/bdt_grouped/group_{number:03d}'
            marker = dict(complete=True, events=count, source=group['nano'], source_sha256='e' * 64,
                          pairs=number, model_sha256=self.model, plan_sha256=self.seal, selection_applied=False,
                          published=dict(nano=artifact(prefix + f'/nanoAOD_merged/ntuple_{number}.root'),
                                         histograms=artifact(prefix + f'/histograms/ntuple_{number}.root'),
                                         score_receipt=artifact(metadata + '/score.json'),
                                         bdt_hist_receipt=artifact(metadata + '/bdt_hist.json'),
                                         score_log=artifact(metadata + '/score.log'),
                                         histogram_log=artifact(metadata + '/histogram.log')))
            scored.append(dict(group=number, score_histograms=dict(complete=True, marker=marker)))
        union = dict(complete=True, point='m15_prompt', parent_gen=self.parent, parent_gen_ledger_count=1,
                     event_ids=self.parent['event_ids'], events=23, native_sumw=23., native_sumw2=23.,
                     inputs=[artifact(chunk['nano']) for chunk in self.row['chunks']], groups=publications,
                     seal_sha256=self.seal, plan_sha256=self.plan_hash,
                     histogramming_started_after_complete_grouping=True)
        marker = dict(complete=True, events=23, plan_sha256=self.seal, model_sha256=self.model,
                      selection_applied=False, groups=2, pairs=1,
                      published=dict(histograms=artifact(prefix + '/histograms_merged/ntuple_0.root')))
        self.report = dict(complete=True, point='m15_prompt', events=23, sample_role='validation_only',
                           final_output_base=prefix, seal_sha256=self.seal, plan_sha256=self.plan_hash,
                           union=union, groups=scored, score_histograms=dict(complete=True, marker=marker),
                           histogram_merge=dict(complete=True, inputs=2))

    def check(self, report):
        return validate_group_reports(self.row, self.parent, report, self.seal, self.plan_hash, self.model)

    def test_groups_keep_one_full_gen_parent_and_one_verified_merged_histogram(self):
        groups, scored = self.check(self.report)
        self.assertEqual([group['events'] for group in groups], [12, 11])
        self.assertEqual(self.report['union']['parent_gen_ledger_count'], 1)
        self.assertEqual(len(scored), 2)

    def test_missing_last_group_repeated_parent_or_foreign_score_fails(self):
        for mutate in [lambda value: value['union'].update(parent_gen_ledger_count=2),
                       lambda value: value['union']['groups'].pop(),
                       lambda value: value['groups'][1]['score_histograms']['marker'].update(source_sha256='f' * 64),
                       lambda value: value['groups'][1]['score_histograms']['marker'].update(events=10),
                       lambda value: value['union']['groups'][1]['event_ids'].pop(),
                       lambda value: value['score_histograms']['marker'].update(groups=1),
                       lambda value: value['histogram_merge'].update(complete=False)]:
            damaged = copy.deepcopy(self.report)
            mutate(damaged)
            with self.assertRaises(ValueError):
                self.check(damaged)


class WeightedCompleteGroupReports(CompleteGroupReports):
    def setUp(self):
        super().setUp()
        self.parent.update(events=46, event_ids=[[8001,1,index+1] for index in range(46)],
                           native_sumw=46., native_sumw2=46.)
        policy=default_policy('weighted-report-test','before-reco')
        contract=dict(schema='shift-production-material-selection-v1', policy=policy,
            policy_sha256=checksum(policy), ledger_sha256='f'*64,
            track_all_hadron_decays=True,maximum_untracked_hadron_flight_mm=1.e-5)
        probabilities=[.1 if index%2 else .25 for index in range(23)]
        selection=dict(gen_sha256='a'*64,full_gen_events=46,selected_indices=list(range(0,46,2)),
                       sampling_probabilities=probabilities,production_selection=contract)
        self.row.update(full_gen_events=46,material_selection=selection)
        self.report.update(material_selection=selection,full_gen_events=46,
            histogram_sumw=sum(1./p for p in probabilities),weight_branch='shiftSamplingGenWeight')

    def test_changed_weight_sum_or_denominator_fails(self):
        for changes in (dict(full_gen_events=23),dict(histogram_sumw=23.),dict(weight_branch='genWeight')):
            report=copy.deepcopy(self.report);report.update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.check(report)


class RootHistogramMerge(unittest.TestCase):
    def setUp(self):
        self.ROOT = root_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def make(self, name, points):
        path = self.base / name
        root = self.ROOT.TFile(str(path), 'RECREATE')
        histogram = self.ROOT.TH1D('mass', 'Synthetic weighted irregular histogram', 2, array('d', [0., 1., 3.]))
        histogram.Sumw2()
        for value, weight in points:
            histogram.Fill(value, weight)
        histogram.Write()
        root.Close()
        return path

    def merge(self, inputs, name='merged.root'):
        destination = self.base / name
        merger = self.ROOT.TFileMerger(False)
        merger.OutputFile(str(destination), 'RECREATE')
        for path in inputs:
            merger.AddFile(str(path))
        self.assertTrue(merger.Merge())
        return destination

    def test_root_merge_preserves_irregular_axes_flow_bins_sumw2_and_entries(self):
        first = self.make('one.root', [(-1., 2.), (.2, 1.), (2., -2.)])
        second = self.make('two.root', [(4., 3.), (.8, 2.), (2., 1.)])
        merged = self.merge([first, second])
        report = validate_histogram_merge([first, second], merged)
        self.assertEqual(report['histograms'], 1)
        self.assertTrue(report['flow_bins_checked'])
        self.assertTrue(report['sum_weights_squared_checked'])

    def test_missing_group_or_changed_contents_and_errors_fail(self):
        first = self.make('one.root', [(-1., 2.), (.2, 1.)])
        second = self.make('two.root', [(4., 3.), (.8, 2.)])
        merged = self.merge([first, second])
        with self.assertRaisesRegex(ValueError, 'contents'):
            validate_histogram_merge([first], merged)
        root = self.ROOT.TFile(str(merged), 'UPDATE')
        histogram = root.Get('mass')
        histogram.SetBinError(1, 99.)
        histogram.Write('', self.ROOT.TObject.kOverwrite)
        root.Close()
        with self.assertRaisesRegex(ValueError, 'variances'):
            validate_histogram_merge([first, second], merged)


class MeasuredPublication(unittest.TestCase):
    def setUp(self):
        root_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.identities = [[8001, 1, 2 * index + 1] for index in range(20)]
        nano, archive = self.base / 'nano.root', self.base / 'metadata.tar.gz'
        write_nano(nano, self.identities)
        common = dict(complete=True, nano=dict(sumw=20.), nano_sha256=sha256(nano))
        signal = dict(complete=True, requested_event_ids=self.identities)
        with tarfile.open(archive, 'w:gz') as bundle:
            for name, value in [('report.json', common), ('signal_report.json', signal), ('launch.log', 'complete')]:
                payload = json.dumps(value).encode()
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))
        artifacts = {tier: dict(path=str(path), bytes=path.stat().st_size, sha256=sha256(path))
                     for tier, path in [('nanoAOD', nano), ('metadata', archive)]}
        plan_path, seal_path, status_path = [self.base / name for name in ('plan.json', 'seal.json', 'status.json')]
        write(plan_path, dict(points=[]))
        write(seal_path, dict(files={str(plan_path): sha256(plan_path)}))
        status = dict(complete=True, events=20, native_sumw=20., selection_applied=False,
                      expected_event_ids=self.identities, plan_sha256=sha256(plan_path), seal_sha256=sha256(seal_path),
                      published=artifacts)
        write(status_path, status)
        self.proof = dict(schema='shift-dark-photon-publication-canary-v1', complete=True,
            publication_profile='nano-metadata-v1', tested_chunk_events=20,
            source_status_path=str(status_path), source_status_sha256=sha256(status_path),
            source_seal_path=str(seal_path), source_seal_sha256=sha256(seal_path), retained_artifacts=artifacts)
        self.proof_path = self.base / 'qualification.json'
        write(self.proof_path, self.proof)
        self.budget = dict(publication_profile='nano-metadata-v1', publication_canary_sha256=sha256(self.proof_path),
                           publication_safety_multiplier=3, bytes_per_detector_chunk=10000000, files_per_detector_chunk=3)

    def test_twenty_event_measured_bound_covers_smaller_chunks_without_rescaling_denominator(self):
        result = validate_publication_canary(self.proof_path, self.budget, 20)
        self.assertTrue(result['complete'])
        self.assertEqual(result['tested_chunk_events'], 20)
        self.assertEqual(result['required_bound_bytes'], 3 * result['measured_payload_bytes'])
        self.assertTrue(validate_publication_canary(self.proof_path, self.budget, 5)['complete'])

    def test_underestimated_budget_and_unmeasured_larger_chunks_fail(self):
        budget = dict(self.budget, bytes_per_detector_chunk=1)
        with self.assertRaisesRegex(ValueError, 'measured'):
            validate_publication_canary(self.proof_path, budget, 20)
        with self.assertRaisesRegex(ValueError, 'largest chunk'):
            validate_publication_canary(self.proof_path, self.budget, 21)

    def test_changed_metadata_and_nonunit_nano_cannot_qualify_publication(self):
        artifact = self.proof['retained_artifacts']['metadata']
        with Path(artifact['path']).open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'artifact'):
            validate_publication_canary(self.proof_path, self.budget, 20)
        # A maliciously refreshed file hash still cannot hide different
        # native weights from the original complete detector receipt.
        nano = Path(self.proof['retained_artifacts']['nanoAOD']['path'])
        write_nano(nano, self.identities, weights=[1.] * 19 + [2.])
        self.proof['retained_artifacts']['nanoAOD'].update(bytes=nano.stat().st_size, sha256=sha256(nano))
        write(self.proof_path, self.proof)
        status_path = Path(self.proof['source_status_path'])
        status = json.loads(status_path.read_text())
        status['published']['nanoAOD'] = self.proof['retained_artifacts']['nanoAOD']
        write(status_path, status)
        self.proof['source_status_sha256'] = sha256(status_path)
        # Remove the unrelated archive mismatch from this second assertion.
        archive = Path(self.proof['retained_artifacts']['metadata']['path'])
        self.proof['retained_artifacts']['metadata'].update(bytes=archive.stat().st_size, sha256=sha256(archive))
        status['published']['metadata'] = self.proof['retained_artifacts']['metadata']
        write(status_path, status)
        self.proof['source_status_sha256'] = sha256(status_path)
        write(self.proof_path, self.proof)
        budget = dict(self.budget, publication_canary_sha256=sha256(self.proof_path))
        with self.assertRaisesRegex(ValueError, 'native weights'):
            validate_publication_canary(self.proof_path, budget, 20)


class LocalFinalizer(unittest.TestCase):
    def test_complete_nano_union_is_grouped_before_any_scoring_and_histograms_are_merged(self):
        """Use real ROOT merges and qualified receipt checks with local transport.

        Scoring is replaced by an identity-preserving component so this test
        checks orchestration without depending on private trained model data.
        """
        ROOT = root_module()
        import uproot  # Load its runtime before the temporary module overrides.
        qualified_base = Path(__file__).resolve().parents[2] / 'validation/bsm_stage2_20261013/production'
        if not qualified_base.is_dir():
            self.skipTest('Qualified deployment is unavailable')
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            deployment, scratch = base / 'deployment', base / 'scratch'
            deployment.mkdir()
            scratch.mkdir()
            (deployment / 'statuses').mkdir()
            (deployment / 'completed_points').mkdir()
            authority = deployment / 'frozen/scoring/sm_axis_authority.root'
            authority.parent.mkdir(parents=True)
            fixture = ROOT.TFile(str(authority), 'RECREATE')
            fixture.Close()
            worker_path = deployment / 'chunk_worker.py'
            worker_path.write_text(extend_worker((qualified_base / 'chunk_worker.py').read_text(), 'nano-metadata-v1'))
            specification = importlib.util.spec_from_file_location('_local_grouped_worker', worker_path)
            worker = importlib.util.module_from_spec(specification)
            specification.loader.exec_module(worker)
            physical = dict(native_width_preflight_passed=True,
                points=[dict(point='m15_prompt', events=23, mass_gev=15., epsilon=.001,
                             proper_ctau_mm=1e-6, target_mean_lab_flight_m=None, run_number=8001)])
            plan = plan_detector_stage(physical, '20261014-statistics', '/eos/user/t/test/shift',
                                       events_per_job=5, group_events=12, detector_job_base=4000001)
            plan.update(publication_profile='nano-metadata-v1', publication_tiers=['nanoAOD'])
            row = plan['points'][0]
            identities = [[8001, 1, 2 * index + 1] for index in range(23)]
            parent = dict(events=23, event_ids=identities, native_sumw=23., native_sumw2=23., gen_sha256='a' * 64)
            write(deployment / 'plan.json', plan)
            write(deployment / 'gen_inventory.json', dict(points={'m15_prompt': parent}))
            write(deployment / 'storage_budget.json', dict(detector_chunks=len(row['chunks']), bytes_per_detector_chunk=10000000))
            write(deployment / 'seal.json', dict(synthetic=True))
            seal_hash, plan_hash = sha256(deployment / 'seal.json'), sha256(deployment / 'plan.json')
            model_hash = 'd' * 64
            write(deployment / 'scoring_components.json', dict(model_sha256=model_hash))
            remote_files, published = {}, []
            for chunk in row['chunks']:
                nano = base / f'input_{chunk["chunk"]}.root'
                selected = identities[chunk['skip']:chunk['skip'] + chunk['count']]
                write_nano(nano, selected)
                destinations = publication_paths(chunk, 'nano-metadata-v1')
                status = dict(complete=True, point='m15_prompt', chunk=chunk['chunk'], events=chunk['count'],
                    expected_event_ids=selected, native_sumw=chunk['count'], parent_gen_sha256='a' * 64,
                    sample_role='validation_only', selection_applied=False, publication_profile='nano-metadata-v1',
                    nano_sha256=sha256(nano), seal_sha256=seal_hash, plan_sha256=plan_hash,
                    audited_scratch_tiers={tier: dict(bytes=100, sha256='e' * 64)
                                          for tier in ('GEN_SIM', 'DIGI_RAW_HLT', 'AODSIM')},
                    published=dict(nanoAOD=dict(path=chunk['nano'], sha256=sha256(nano), bytes=nano.stat().st_size),
                                   metadata=dict(path=destinations['metadata'], sha256='f' * 64, bytes=100)))
                marker = deployment / 'statuses' / f'm15_prompt_{chunk["chunk"]:03d}.json'
                write(marker, status)
                remote_files[chunk['nano']] = nano
                remote_files[chunk['metadata'] + '/complete.json'] = marker
            def transfer(arguments):
                remote = arguments[-2].removeprefix(worker.HOST + '/')
                shutil.copyfile(remote_files[remote], arguments[-1])
            def publish(local, remote, work):
                if remote in remote_files:
                    raise FileExistsError(remote)
                remote_files[remote] = Path(local)
                published.append(remote)
                return dict(path=remote, sha256=sha256(local), bytes=Path(local).stat().st_size)
            worker.xrd, worker.publish = transfer, publish
            worker.check_headroom = lambda: dict(known=True)
            worker.verify_seal = lambda *args, **kwargs: None
            qualified_spec = importlib.util.spec_from_file_location('_local_qualified_finalizer', qualified_base / 'finalize_point.py')
            qualified = importlib.util.module_from_spec(qualified_spec)
            with patch.dict(sys.modules, {'chunk_worker': worker}):
                qualified_spec.loader.exec_module(qualified)
            qualified.validate_histogram_axes = lambda *args: 1081
            score_calls = []
            def score_file(scoreplan, descriptor, work, cache, analysis, seal):
                self.assertEqual(sum('/nanoAOD_merged_unscored/' in path for path in published), 2)
                score_calls.append(descriptor['index'])
                work.mkdir()
                source = Path(descriptor['source_transport_path'])
                scored = work / 'scored.root'
                shutil.copyfile(source, scored)
                histogram_path = work / 'histograms.root'
                root = ROOT.TFile(str(histogram_path), 'RECREATE')
                for name in ('rawEventsCutFlow', 'cutFlow'):
                    histogram = ROOT.TH1D(name, name, 2, 0., 2.)
                    histogram.Sumw2()
                    for _ in range(descriptor['expected_events']):
                        histogram.Fill(.5)
                    histogram.Write()
                root.Close()
                artifacts = {'nano': scored, 'histograms': histogram_path}
                for name in ('score_receipt', 'bdt_hist_receipt', 'score_log', 'histogram_log'):
                    artifacts[name] = work / name
                    artifacts[name].write_text('synthetic component receipt')
                destinations = dict(nano=descriptor['output_nano'], histograms=descriptor['output_histograms'],
                                    **{name: descriptor[name] for name in
                                       ('score_receipt', 'bdt_hist_receipt', 'score_log', 'histogram_log')})
                records = {}
                for name, local in artifacts.items():
                    scorer.io.put_remote(local, destinations[name])
                    records[name] = dict(path=destinations[name], sha256=sha256(local), bytes=local.stat().st_size)
                return dict(complete=True, marker=dict(complete=True, events=descriptor['expected_events'], pairs=0,
                    source=descriptor['source_logical_path'], source_sha256=descriptor['source_sha256'],
                    model_sha256=model_hash, plan_sha256=seal, selection_applied=False, published=records))
            def check_counts(path, count, weight):
                root = ROOT.TFile.Open(str(path))
                self.assertEqual(root.Get('rawEventsCutFlow').GetBinContent(1), count)
                self.assertEqual(root.Get('cutFlow').GetBinContent(1), weight)
                root.Close()
            scorer = SimpleNamespace(io=SimpleNamespace(put_remote=lambda local, remote: None),
                prepare_analysis=lambda *args: scratch / 'analysis', run_file=score_file,
                check_standard_histograms=check_counts)
            with patch.dict(sys.modules, {'chunk_worker': worker, 'qualified_finalizer': qualified,
                                         'run_merged_dimuon_bdt': scorer}), \
                    patch.dict(os.environ, {'_CONDOR_SCRATCH_DIR': str(scratch)}):
                report = finalize('m15_prompt', seal_hash, base / 'cache')
                collect(seal_hash)
            self.assertEqual(score_calls, [0, 1])
            self.assertEqual(report['union']['event_ids'], identities)
            self.assertEqual(report['union']['parent_gen_ledger_count'], 1)
            self.assertEqual([group['events'] for group in report['union']['groups']], [12, 11])
            self.assertTrue(report['histogram_merge']['sum_weights_squared_checked'])
            performance = json.loads((deployment / 'performance_inputs.json').read_text())
            self.assertEqual(performance['full_gen_population_count'], 1)
            self.assertEqual(len(performance['inputs']), 2)
            bins = json.loads((deployment / 'bin_histogram_inputs.json').read_text())
            self.assertEqual(len(bins['samples']), 1)


if __name__ == '__main__':
    unittest.main()
