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
from prepare_dark_photon_grid import REFERENCE_SCHEMA, SOURCES, digest, plan_points, preflight_points, validate_references, write_plan


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
        for kwargs in ({'masses':(15.,15.)}, {'masses':(12.,)}, {'events':1001}, {'detector_count':21},
                       {'events':1,'detector_count':2}, {'seed_base':899999900}, {'mean_lab_flights_m':(0,30)},
                       {'epsilons':(1e-7,1e-7)}, {'epsilons':(1e-7,1.0000000001e-7)},
                       {'epsilons':(.006,)}, {'prompt_epsilon':0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): plan_points(references(), **kwargs)

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


class FrozenGridReceipts(unittest.TestCase):
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


if __name__=='__main__': unittest.main()
