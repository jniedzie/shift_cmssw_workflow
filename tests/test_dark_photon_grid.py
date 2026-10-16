# Code style: AI-code. AI-generated signal planning regression tests.
"""Synthetic regression fixtures for immutable, unlaunched signal grid plans."""
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dark_photon_model import DEFAULT_EW_INPUTS, FERMIONS, fermion_widths_qcd_first_order, HBAR_C_GEV_MM
from dark_photon_sample_names import physical_point_name, point_mean_lab_flight_m
from prepare_dark_photon_grid import REFERENCE_SCHEMA, SOURCES, digest, plan_points, preflight_points, plan_detector_stage, repartition_detector_chunks, reuse_physical_points, validate_references, write_plan, meson_width_qualification, validate_physical_preflight


def fake_native(mass, epsilon, scale=1.):
    """Synthetic width fixture; never an experiment production table."""
    ew = DEFAULT_EW_INPUTS
    widths = fermion_widths_qcd_first_order(mass, epsilon, .163)
    total = math.fsum(widths.values()) * scale ** 2
    return dict(mass_gev=mass, pythia_version=8.317, m_width_gev=total,
                m_min_gev=.99*mass, m_max_gev=1.01*mass, proper_length_mm=HBAR_C_GEV_MM/total,
                alpha_em_at_mass=1/ew.alpha_inverse, alpha_s_at_mass=.163,
                sin2_theta_w=ew.sin2_theta_w, m_z_gev=ew.m_z_gev,
                fermion_masses_gev={str(f.pdg_id): f.mass_gev for f in FERMIONS},
                channels=[dict(products=[f.pdg_id, -f.pdg_id], on_shell_width_gev=widths[f.name]*scale**2,
                               branching_fraction=widths[f.name]*scale**2/total) for f in FERMIONS])


def references():
    rows = []
    for mass, boost in ((15.,80.414), (30.,68.), (50.,64.031)):
        width = fake_native(mass, 1e-7)
        width.update(epsilon=1e-7, total_width_gev=width['m_width_gev'], ctau_mm=width['proper_length_mm'],
                     br_mumu=next(c['branching_fraction'] for c in width['channels'] if c['products']==[13,-13]))
        rows.append(dict(mass_gev=mass, epsilon=1e-7, mean_beta_gamma=boost, width_authority=width,
                         boost_source='synthetic beta-gamma fixture; no measured MC assertion'))
    return dict(schema=REFERENCE_SCHEMA, sample_kind='simulation_only', common=[], cp5=[], references=rows)


def frozen_inputs(base, ref):
    scripts, templates, reference_path = base/'scripts', base/'templates', base/'references.json'
    scripts.mkdir(); templates.mkdir()
    for name in SOURCES:
        (scripts/name).write_text('# synthetic dependency\n')
    for i in range(1,5):
        (templates/f'step{i}.py').write_text('# synthetic fixed template\n')
    reference_path.write_text(json.dumps(ref))
    return scripts, templates, reference_path


class PhysicalGridPlanning(unittest.TestCase):
    def test_accepted_output_flag_survives_native_preflight_and_freezing(self):
        reference=references()
        plan=preflight_points(plan_points(reference,accepted_events=True),reference,fake_native)
        self.assertEqual(plan['event_budget_mode'],'outputAccepted')
        self.assertTrue(all(row['signal_contract']['maximum_framework_events']==1000
                            for row in plan['points']))
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary)
            scripts,templates,refpath=frozen_inputs(base,reference)
            result=write_plan(base/'frozen',refpath,templates,plan,{},scripts=scripts)
            for row in result['points']:
                self.assertIn('--accepted-events',row['gen_argv'])
                data=json.loads(Path(row['signal_contract']).read_text())
                self.assertEqual(data['event_budget_mode'],'outputAccepted')

    def test_nine_points_use_lab_estimate_unique_seeds_and_full_exponential(self):
        plan = plan_points(references())
        self.assertEqual(len(plan['points']), 9)
        self.assertEqual(plan['grid_mode'], 'mass-by-approximate-mean-lab-flight')
        self.assertEqual([p['seed'] for p in plan['points']], list(range(24693357,24693366)))
        self.assertEqual([p['run_number'] for p in plan['points']], list(range(24693357,24693366)))
        self.assertFalse(plan['lab_flight_is_fixed'])
        self.assertTrue(all(p['events']==100 and p['detector_count']==2 for p in plan['points']))
        for mass in (15.,30.,50.):
            rows = [p for p in plan['points'] if p['mass_gev']==mass]
            self.assertEqual([p['target_mean_lab_flight_m'] for p in rows], [None,5.,30.])
            self.assertAlmostEqual(rows[1]['epsilon']/rows[2]['epsilon'], math.sqrt(6), places=12)
            for row in rows[1:]:
                self.assertAlmostEqual(row['proper_ctau_reference_estimate_mm']*row['mean_beta_gamma_reference']/1000,
                                       row['target_mean_lab_flight_m'], places=12)

    def test_explicit_epsilon_columns_are_cartesian(self):
        eps = (1e-7,5e-8,2e-8)
        plan = plan_points(references(), epsilons=eps)
        self.assertEqual(plan['grid_mode'], 'cartesian-mass-epsilon')
        self.assertEqual({(p['mass_gev'],p['epsilon']) for p in plan['points']}, {(m,e) for m in (15.,30.,50.) for e in eps})
        self.assertTrue(all(p['target_mean_lab_flight_m'] is None for p in plan['points']))

    def test_reference_ambiguity_and_invalid_bounds_fail(self):
        mutations = (lambda r:r.update(sample_kind='data'), lambda r:r['references'].append(r['references'][0]),
                     lambda r:r['references'][0].update(mean_beta_gamma=0), lambda r:r['references'][0].pop('boost_source'),
                     lambda r:r['references'][0]['width_authority'].update(ctau_mm=1.))
        for mutate in mutations:
            value = copy.deepcopy(references()); mutate(value)
            with self.assertRaises(ValueError): validate_references(value)
        for kwargs in ({'masses':(15.,15.)}, {'masses':(12.,)}, {'events':10001}, {'detector_count':21},
                       {'events':1,'detector_count':2}, {'seed_base':899999900}, {'mean_lab_flights_m':(0,30)},
                       {'epsilons':(1e-7,1e-7)}, {'epsilons':(1e-7,1.0000000001e-7)},
                       {'epsilons':(.006,)}, {'prompt_epsilon':0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): plan_points(references(), **kwargs)

    def test_statistics_extension_reuses_old_physics_and_keeps_new_population(self):
        reference = references()
        old = preflight_points(plan_points(reference), reference, fake_native)
        changed_reference = copy.deepcopy(reference)
        changed_reference['references'][0]['mean_beta_gamma'] *= 1.1
        new = plan_points(changed_reference, events=9000, seed_base=251014001,
                          accepted_events=True)
        reused = reuse_physical_points(new, old)
        final = preflight_points(reused, changed_reference, fake_native)
        for previous, current in zip(old['points'], final['points']):
            self.assertEqual(current['epsilon'], previous['epsilon'])
            self.assertEqual(current['proper_ctau_mm'], previous['proper_ctau_mm'])
            self.assertEqual(current['mean_beta_gamma_reference'], previous['mean_beta_gamma_reference'])
            self.assertEqual(current['events'], 9000)
            self.assertNotEqual(current['seed'], previous['seed'])
        old['points'][0]['proper_ctau_mm'] *= 2
        with self.assertRaisesRegex(ValueError, 'reused physical lifetime'):
            preflight_points(reuse_physical_points(new, old), changed_reference, fake_native)

    def test_native_preflight_keeps_physical_width_and_lifetime(self):
        ref = references(); plan = preflight_points(plan_points(ref), ref, fake_native)
        self.assertTrue(plan['native_width_preflight_passed'])
        for row in plan['points']:
            data = row['signal_contract']; width = data['width_authority']; sampling = data['sampling_width_authority']
            self.assertAlmostEqual(width['total_width_gev']*row['proper_ctau_mm']/HBAR_C_GEV_MM, 1., places=12)
            self.assertEqual(width['ctau_mm'], row['proper_ctau_mm'])
            self.assertEqual(data['epsilon'], row['epsilon'])
            self.assertAlmostEqual(data['production_rate_correction']/((row['epsilon']/.005)**2), 1., places=12)
            self.assertGreater(sampling['total_width_gev'], width['total_width_gev'])
            self.assertFalse(data['physics_valid'])
            self.assertIn('full physical exponential', data['signal_decay_policy'])
            if row['target_mean_lab_flight_m'] is not None:
                self.assertAlmostEqual(row['mean_lab_flight_reference_estimate_m'],row['target_mean_lab_flight_m'],places=9)

    def test_changed_native_width_and_collapsed_proposal_are_refused(self):
        ref = references()
        def changed(m,e,s):
            t = fake_native(m,e,s); t['m_width_gev']*=2; t['proper_length_mm']/=2; return t
        def collapsed(m,e,s):
            t = fake_native(m,e,s); t['m_min_gev']=m; t['m_max_gev']=m; return t
        for probe in (changed,collapsed):
            with self.assertRaises(ValueError): preflight_points(plan_points(ref),ref,probe)


class CompleteDetectorStagePlanning(unittest.TestCase):
    def test_epsilon_selectors_publish_lab_names_without_changing_physics(self):
        ref = references()
        physical = preflight_points(plan_points(ref, masses=(15.,), epsilons=(1e-6, 1e-4, 1e-2),
                                   sampling_epsilon=.01, events=50, accepted_events=True), ref, fake_native)
        stage = plan_detector_stage(physical, '20261013-epsilon-grid', '/eos/user/t/test/data')
        for current, original in zip(stage['points'], physical['points']):
            flight = original['mean_lab_flight_reference_estimate_m']
            self.assertEqual(current['point'], physical_point_name(15., flight))
            self.assertEqual(current['original_point'], original['point'])
            self.assertEqual(current['selector_kind'], 'epsilon')
            self.assertEqual(current['mean_lab_flight_m'], flight)
            self.assertEqual(current['reference_mean_lab_flight_m'], flight)
            for key in ('epsilon', 'proper_ctau_mm', 'signal_contract', 'seed', 'run_number'):
                self.assertEqual(current[key], original[key])
            self.assertIsNone(current['target_mean_lab_flight_m'])
            self.assertIn('/darkphoton_' + current['point'] + '/', current['final_output_base'])
        self.assertEqual(physical['points'][0]['point'], 'm15_epsilon_1e-06')
        self.assertEqual(stage['detector_chunks'], 30)
        self.assertEqual(stage['events'], 150)
        self.assertTrue(all(len(row['chunks']) == 10 for row in stage['points']))
        self.assertTrue(all(row['groups'][0]['count'] == 50 for row in stage['points']))
        direct = physical['points'][2]['signal_contract']
        self.assertEqual(direct['width_authority']['pole_support_audit'],
                         direct['sampling_width_authority']['pole_support_audit'])

    def test_lab_names_are_concise_and_anchor_names_stay_familiar(self):
        self.assertEqual(physical_point_name(40., None, prompt=True), 'm40_prompt')
        for flight in (5., 30., 100., 1000.):
            self.assertEqual(physical_point_name(40., flight), f'm40_lab_{int(flight)}m')
        self.assertEqual(physical_point_name(2., 2.5787102372956836), 'm2_lab_2p58m')
        self.assertEqual(physical_point_name(2., 2.5787102372956833e-8), 'm2_lab_2p58em08m')
        self.assertEqual(physical_point_name(50., .01146646928466834), 'm50_lab_0p0115m')
        for value in (0., -1., float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                physical_point_name(40., value)
        with self.assertRaisesRegex(ValueError, 'reference mean lab flight'):
            point_mean_lab_flight_m(dict(point='m2_epsilon_1e-06', proper_ctau_mm=9.7))

    def test_rounded_lab_name_collision_is_rejected_before_publication(self):
        ref = references()
        physical = preflight_points(plan_points(ref, masses=(15.,), epsilons=(1.e-6, 1.00001e-6)),
                                    ref, fake_native)
        with self.assertRaisesRegex(ValueError, 'unique physical points'):
            plan_detector_stage(physical, '20261016-lab', '/eos/user/t/test/data')

    def test_statistics_extension_accepts_previous_lab_named_epsilon_point(self):
        ref = references()
        physical = preflight_points(plan_points(ref, masses=(15.,), epsilons=(1.e-6,)), ref, fake_native)
        previous = plan_detector_stage(physical, '20261016-lab', '/eos/user/t/test/data')
        new = plan_points(ref, masses=(15.,), epsilons=(1.e-6,), seed_base=251016001)
        reused = reuse_physical_points(new, previous)
        self.assertEqual(reused['points'][0]['epsilon'], previous['points'][0]['epsilon'])
        self.assertEqual(reused['points'][0]['statistics_extension_source_point'], previous['points'][0]['point'])

    def test_fifteen_thousand_events_are_partitioned_and_grouped_by_physical_point(self):
        ref = references()
        physical = preflight_points(plan_points(ref, mean_lab_flights_m=(5., 30., 100., 1000.),
                                   events=1000, detector_count=5), ref, fake_native)
        before = copy.deepcopy(physical)
        stage = plan_detector_stage(physical, '20261013-independent-stage2',
                                    '/eos/user/t/test/shift_cmssw', detector_job_base=1000000)
        self.assertEqual(physical, before)
        self.assertEqual(stage['events'], 15000)
        self.assertEqual(stage['detector_chunks'], 3000)
        jobs, paths = set(), set()
        for row, original in zip(stage['points'], before['points']):
            self.assertEqual(len(row['chunks']), 200)
            self.assertEqual([event for chunk in row['chunks']
                for event in range(chunk['skip'], chunk['skip'] + chunk['count'])], list(range(1000)))
            self.assertTrue(all(chunk['count'] == 5 for chunk in row['chunks']))
            self.assertEqual(row['groups'][0]['chunks'], list(range(200)))
            self.assertEqual(row['groups'][0]['count'], 1000)
            self.assertIn('/darkphoton_' + row['point'] + '/', row['groups'][0]['nano'])
            self.assertEqual(row['signal_contract'], original['signal_contract'])
            for chunk in row['chunks']:
                self.assertNotIn(chunk['job'], jobs)
                self.assertNotIn(chunk['nano'], paths)
                jobs.add(chunk['job']); paths.add(chunk['nano'])
        self.assertEqual(len(jobs), 3000)
        self.assertFalse(jobs.intersection(row['detector_job'] for row in stage['points']))
        self.assertTrue(stage['grouping_requires_complete_point'])
        self.assertFalse(stage['submitted'])

    def test_group_boundary_and_final_remainder_cover_every_event_once(self):
        ref = references()
        physical = preflight_points(plan_points(ref, masses=(15.,), epsilons=(.001,),
                                   events=23, detector_count=5), ref, fake_native)
        stage = plan_detector_stage(physical, 'stage2_20261013', '/eos/user/t/test/data',
                                    events_per_job=5, group_events=12)
        row = stage['points'][0]
        self.assertEqual([chunk['count'] for chunk in row['chunks']], [5, 5, 2, 5, 5, 1])
        self.assertEqual([group['chunks'] for group in row['groups']], [[0, 1, 2], [3, 4, 5]])
        self.assertEqual([group['count'] for group in row['groups']], [12, 11])
        for group in row['groups']:
            chunks = [row['chunks'][index] for index in group['chunks']]
            self.assertEqual([event for chunk in chunks
                for event in range(chunk['skip'], chunk['skip'] + chunk['count'])],
                list(range(group['skip'], group['skip'] + group['count'])))

    def test_invalid_stage_names_budgets_jobs_and_duplicate_runs_fail(self):
        ref = references()
        physical = preflight_points(plan_points(ref), ref, fake_native)
        for kwargs in ({'campaign': 'undated'}, {'campaign': '20260230-bad'},
                       {'campaign': '20261013/unsafe'}, {'eos_base': '/eos/home-j/test/data'},
                       {'eos_base': '/eos/user/t/test/../data'}, {'events_per_job': 21},
                       {'events_per_job': True}, {'group_events': 4}, {'detector_job_base': 6699998}):
            arguments = dict(campaign='20261013-stage2', eos_base='/eos/user/t/test/data')
            arguments.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                plan_detector_stage(physical, **arguments)
        for mutate in (lambda plan: plan.update(native_width_preflight_passed=False),
                       lambda plan: plan['points'][1].update(run_number=plan['points'][0]['run_number']),
                       lambda plan: plan['points'][0].update(events=99)):
            bad = copy.deepcopy(physical); mutate(bad)
            with self.assertRaises(ValueError):
                plan_detector_stage(bad, '20261013-stage2', '/eos/user/t/test/data')


class FrozenGridReceipts(unittest.TestCase):
    def test_new_gen_only_plan_uses_lab_names_and_keeps_the_original_epsilon(self):
        ref = references()
        physical = preflight_points(plan_points(ref, masses=(15.,), epsilons=(1.e-6,)), ref, fake_native)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            scripts, templates, path = frozen_inputs(base, ref)
            result = write_plan(base/'grid', path, templates, physical, {}, scripts, 'python3')
            row = result['points'][0]
            self.assertIn('/m15_lab_', row['gen_directory'])
            self.assertEqual(row['original_point'], 'm15_epsilon_1e-06')
            self.assertEqual(row['epsilon'], physical['points'][0]['epsilon'])
            self.assertEqual(json.loads(Path(row['signal_contract']).read_text()),
                             physical['points'][0]['signal_contract'])
            self.assertEqual(physical['points'][0]['point'], 'm15_epsilon_1e-06')

    def test_ambiguous_lab_names_fail_before_writing_any_frozen_files(self):
        ref = references()
        physical = preflight_points(plan_points(ref, masses=(15.,), epsilons=(1.e-6, 1.00001e-6)),
                                    ref, fake_native)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            scripts, templates, path = frozen_inputs(base, ref)
            with self.assertRaisesRegex(ValueError, 'rounded LAB names'):
                write_plan(base/'grid', path, templates, physical, {}, scripts, 'python3')
            self.assertFalse((base/'grid').exists())

    def test_complete_stage_freezes_grouping_contract_and_distinct_canary_command(self):
        ref = references()
        physical = preflight_points(plan_points(ref, masses=(15.,), epsilons=(.001,),
                                   events=1000, detector_count=5), ref, fake_native)
        stage = plan_detector_stage(physical, '20261013-stage2', '/eos/user/t/test/data')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); scripts, templates, path = frozen_inputs(base, ref)
            result = write_plan(base/'stage', path, templates, stage, {}, scripts, 'python3')
            self.assertTrue(result['detector_stage_prepared'])
            self.assertEqual(result['points'][0]['chunks'], stage['points'][0]['chunks'])
            self.assertEqual(result['points'][0]['groups'], stage['points'][0]['groups'])
            command = result['points'][0]['detector_argv']
            self.assertEqual(command[command.index('--job') + 1], '1000200')
            self.assertEqual(command[command.index('--count') + 1], '5')
            facade = result['frozen_dependencies']['sources']['shift_storage_paths.py']
            self.assertEqual(digest(facade['frozen_copy']), facade['sha256'])
            names = result['frozen_dependencies']['sources']['dark_photon_sample_names.py']
            self.assertEqual(digest(names['frozen_copy']), names['sha256'])
            self.assertFalse((base/'stage/runs').exists())

    def test_freeze_commands_without_creating_runtime_directories(self):
        ref = references(); plan = preflight_points(plan_points(ref),ref,fake_native)
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); scripts,templates,path = frozen_inputs(base,ref); out=base/'grid'
            result = write_plan(out,path,templates,plan,{'synthetic_runtime':True},scripts,'python3')
            self.assertFalse((out/'runs').exists()); self.assertFalse(result['launched']); self.assertFalse(result['normalization_ready'])
            self.assertIn('pythia_generation_ledger.py', result['frozen_dependencies']['sources'])
            for row in result['points']:
                self.assertEqual(digest(row['signal_contract']),row['signal_contract_sha256'])
                self.assertIn('--run-number',row['gen_argv']); self.assertIn(repr(row['epsilon']),row['gen_argv'])
                self.assertIn(str(out/'templates'),row['detector_argv'])
                self.assertFalse(Path(row['gen_directory']).exists()); self.assertFalse(Path(row['detector_directory']).exists())
            self.assertEqual(json.loads((out/'plan.json').read_text()),result)
            with self.assertRaises(FileExistsError): write_plan(out,path,templates,plan,{},scripts)

    def test_unpreflighted_plan_and_missing_dependency_fail_before_output(self):
        ref = references(); plan=plan_points(ref)
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); scripts,templates,path=frozen_inputs(base,ref); out=base/'grid'
            with self.assertRaisesRegex(ValueError,'native-width preflight'): write_plan(out,path,templates,plan,{},scripts)
            plan=preflight_points(plan,ref,fake_native); (templates/'step4.py').unlink()
            with self.assertRaisesRegex(ValueError,'Missing frozen dependency'): write_plan(out,path,templates,plan,{},scripts)
            self.assertFalse(out.exists())

    def test_changed_reference_or_sources_fail_before_output(self):
        ref=references(); plan=preflight_points(plan_points(ref),ref,fake_native)
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); scripts,templates,path=frozen_inputs(base,ref); out=base/'grid'
            changed=copy.deepcopy(ref); changed['references'][0]['mean_beta_gamma']+=1; path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError,'reference changed'): write_plan(out,path,templates,plan,{},scripts)
            path.write_text(json.dumps(ref))
            with self.assertRaisesRegex(ValueError,'preflight source changed'):
                write_plan(out,path,templates,plan,{'source_preflight_sha256':{'dark_photon_model.py':'0'*64}},scripts)
            self.assertFalse(out.exists())


class DetectorChunkReplacement(unittest.TestCase):
    @staticmethod
    def original():
        base = '/eos/user/synthetic/old'
        return [dict(chunk=i, skip=20*i, count=20, job=960000+i,
                     nano=f'{base}/nanoAOD/chunk_{i:03d}.root',
                     metadata=f'{base}/metadata/chunk_{i:03d}') for i in range(10)]

    def test_five_event_replacement_preserves_live_chunks_and_full_event_union(self):
        original = self.original()
        before = copy.deepcopy(original)
        identities = [[123, 1, event] for event in range(1, 201)]
        result = repartition_detector_chunks(original, identities, [0, 7], 5,
                                             '/eos/user/synthetic/replacement', 970000)
        self.assertEqual(len(result), 34)
        self.assertEqual(original, before)
        self.assertEqual([row for row in result if row['chunk'] in (0, 7)],
                         [original[0], original[7]])
        new = [row for row in result if row['chunk'] not in (0, 7)]
        self.assertTrue(all(row['count'] == 5 and row['chunk'] >= 10 for row in new))
        self.assertEqual(len({row['job'] for row in result}), len(result))
        self.assertEqual(len({row['nano'] for row in result}), len(result))
        self.assertTrue(all('/replacement/' in row['nano'] for row in new))
        union = [identity for row in result
                 for identity in identities[row['skip']:row['skip'] + row['count']]]
        self.assertEqual(union, identities)
        self.assertEqual(len(set(map(tuple, union))), 200)

    def test_short_final_slice_and_all_retained_partition(self):
        original = self.original()[:2]
        original[-1]['count'] = 17
        identities = [[123, 1, event] for event in range(1, 38)]
        result = repartition_detector_chunks(original, identities, [0], 5,
                                             '/eos/user/synthetic/replacement', 970000)
        self.assertEqual([row['count'] for row in result], [20, 5, 5, 5, 2])
        self.assertEqual(repartition_detector_chunks(original, identities, [0, 1], 5,
                         '/eos/user/synthetic/replacement', 970000), original)

    def test_corrupt_partition_duplicates_and_output_collisions_fail_closed(self):
        identities = [[123, 1, event] for event in range(1, 201)]
        for change in (lambda rows: rows[1].update(skip=19),
                       lambda rows: rows[1].update(chunk=0),
                       lambda rows: rows[1].update(job=rows[0]['job']),
                       lambda rows: rows[1].update(nano=rows[0]['nano']),
                       lambda rows: rows[-1].update(count=19)):
            rows = self.original()
            change(rows)
            with self.subTest(change=change), self.assertRaises(ValueError):
                repartition_detector_chunks(rows, identities, [], 5,
                    '/eos/user/synthetic/replacement', 970000)
        for retained, count, base, job in (([10], 5, '/eos/user/synthetic/new', 970000),
                ([0, 0], 5, '/eos/user/synthetic/new', 970000),
                ([True], 5, '/eos/user/synthetic/new', 970000),
                ([], 0, '/eos/user/synthetic/new', 970000),
                ([], 21, '/eos/user/synthetic/new', 970000),
                ([], 5, '/eos/user/synthetic/old', 970000),
                ([], 5, '/eos/user/synthetic/new', 960005),
                ([], 5, '/eos/', 970000),
                ([], 5, '/eos/user/synthetic//new', 970000),
                ([], 5, '/eos/user/synthetic/../old', 970000)):
            with self.subTest(retained=retained, count=count, base=base, job=job), self.assertRaises(ValueError):
                repartition_detector_chunks(self.original(), identities, retained, count, base, job)
        duplicate = copy.deepcopy(identities)
        duplicate[-1] = duplicate[0]
        with self.assertRaisesRegex(ValueError, 'Duplicate parent'):
            repartition_detector_chunks(self.original(), duplicate, [], 5,
                                        '/eos/user/synthetic/replacement', 970000)


class MesonQualification(unittest.TestCase):
    def make_plan(self, directory):
        width = dict(mass_gev=.3, epsilon=1.e-6, total_width_gev=2.e-12,
            partial_widths_gev={'mu_mu': 1.e-12, 'e_e': 1.e-12},
            ctau_mm=1.973269804e-13/2.e-12, br_mumu=.5, spectral_source={'pinned': 'source'})
        source = dict(source_collisions=100, fixed_source_collisions=100,
                      accepted_signal_events=2, native_n_accepted=100)
        contract = dict(production_mechanism='eta-etaprime-radiative', filtering='none',
            requested_events=2, mass_gev=.3, epsilon=1.e-6, seed=77, run_number=88,
            physical_width_table=width, width_authority=width['spectral_source'],
            source_exposure=source, meson_model_card_sha256='a'*64)
        (directory/'contract.json').write_text(json.dumps(contract))
        (directory/'validation.json').write_text(json.dumps(dict(runtime_validated=True, events=2,
                                                                source_exposure=source)))
        row = dict(point='m0p3_prompt', mass_gev=.3, epsilon=1.e-6, seed=77, run_number=88,
                   events=2, gen_directory=str(directory), signal_contract=str(directory/'contract.json'))
        plan = dict(production_mechanism='eta-etaprime-radiative', physical_width_preflight_passed=True,
                    points=[row])
        plan['meson_width_qualification'] = meson_width_qualification(plan['points'])
        return plan

    def test_meson_widths_qualify_without_claiming_native_resonance_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = self.make_plan(Path(temporary))
            stage = plan_detector_stage(plan, '20261016-mesons', '/eos/user/t/test/data')
            self.assertEqual(stage['events'], 2)
            self.assertNotIn('native_width_preflight_passed', stage)
            self.assertFalse(stage['meson_width_qualification']['absolute_production_rate_validated'])
            plan['points'][0]['proper_ctau_mm'] = 2 * 1.973269804e-13/2.e-12
            with self.assertRaisesRegex(ValueError, 'do not close'):
                validate_physical_preflight(plan)
            plan['points'][0].pop('proper_ctau_mm')
            plan['native_width_preflight_passed'] = True
            with self.assertRaisesRegex(ValueError, 'cannot claim native'):
                validate_physical_preflight(plan)

    def test_changed_qualified_evidence_and_unclosed_widths_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = self.make_plan(directory)
            plan['meson_width_qualification']['points']['m0p3_prompt']['validation_sha256'] = 'b'*64
            with self.assertRaisesRegex(ValueError, 'physical-width preflight'):
                validate_physical_preflight(plan)
            contract = json.loads((directory/'contract.json').read_text())
            contract['physical_width_table']['ctau_mm'] *= 2
            (directory/'contract.json').write_text(json.dumps(contract))
            with self.assertRaisesRegex(ValueError, 'do not close'):
                meson_width_qualification(plan['points'])

    def test_source_and_signal_denominators_cannot_be_confused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = self.make_plan(directory)
            contract = json.loads((directory/'contract.json').read_text())
            contract['source_exposure']['native_n_accepted'] = 2
            (directory/'contract.json').write_text(json.dumps(contract))
            audit = json.loads((directory/'validation.json').read_text())
            audit['source_exposure'] = contract['source_exposure']
            (directory/'validation.json').write_text(json.dumps(audit))
            with self.assertRaisesRegex(ValueError, 'source exposure'):
                meson_width_qualification(plan['points'])


if __name__=='__main__': unittest.main()
