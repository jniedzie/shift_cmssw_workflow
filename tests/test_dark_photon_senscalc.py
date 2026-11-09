# Code style: AI-code. Exposure, frozen-input and interpolation controls.
import json
import math
import io
import shutil
import tarfile
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_senscalc as model


class SensCalcBenchmark(unittest.TestCase):
    def test_pbc_components_add_DY_in_the_published_overlap_without_replacing_radiation(self):
        expected={.22:['senscalc'],1.49:['senscalc'],1.5:['senscalc','dy'],
                  1.8:['senscalc','dy'],2.:['dy'],70.:['dy']}
        for mass,names in expected.items():
            with self.subTest(mass=mass):
                definition=model.pbc_bc1_benchmark(mass,1.e-5)
                self.assertEqual([row['name'] for row in definition['components']],names)
                self.assertEqual(len(set(row['mechanism'] for row in definition['components'])),len(names))
                self.assertTrue(definition['combination']['separate_GEN_denominators'])
                self.assertTrue(definition['combination']['apply_epsilon_and_dimuon_branching_once'])
                self.assertFalse(definition['source_qualified'])
                self.assertFalse(definition['production_qualified'])
        overlap=model.pbc_bc1_benchmark(1.8,1.e-5)
        self.assertIn('isotropic',overlap['components'][0]['source_graph']['dimuon_decay'])
        self.assertIn('native PYTHIA',overlap['components'][1]['source_graph']['prescription'])

    def test_pbc_radiation_scenarios_keep_published_correlation_and_real_table_endpoints(self):
        for name,scale in [('Lower',.5),('Central',1.),('Upper',2.)]:
            definition=model.pbc_bc1_benchmark(.9,1.e-5,radiation_variation=name)
            radiation=definition['radiation'];source=definition['components'][0]
            self.assertEqual((radiation['scenario'],radiation['lambda_p_gev']),(name,scale))
            self.assertTrue(radiation['correlated_across_masses'])
            self.assertEqual(radiation['table_max_mass_gev'],dict(Lower=1.99,Central=2.,Upper=1.99))
            self.assertEqual(source['bremsstrahlung']['form_factor_variation'],name)
            self.assertEqual(source['bremsstrahlung']['lambda_p_gev'],scale)
            self.assertFalse(source['source_ISR'])
        for name in ('Lower','Upper'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError,'no extrapolation or zero-rate'):
                    model.pbc_bc1_benchmark(1.99,1.e-5,radiation_variation=name)
        self.assertEqual(len(model.pbc_bc1_benchmark(1.99,1.e-5)['components']),2)
        beyond=model.pbc_bc1_benchmark(2.,1.e-5,radiation_variation='Upper')
        self.assertFalse(beyond['radiation']['applicable'])
        self.assertIn('not a physical zero',beyond['radiation']['outside_support'])
        self.assertTrue(any('fragmentation' in scope for scope in beyond['unmodelled_above_source_range']))

    def test_pbc_definition_can_use_the_pinned_tables_in_a_frozen_deployment(self):
        original=Path(model.__file__).resolve().parents[1]/'config/reference/senscalc'
        with tempfile.TemporaryDirectory() as tmp:
            tables=Path(tmp)
            for path in original.glob('FFproton*.txt'):shutil.copyfile(path,tables/path.name)
            definition=model.pbc_bc1_benchmark(.9,1.e-5,table_directory=tables)
            self.assertEqual(definition['components'][0]['bremsstrahlung']['table_directory'],str(tables.resolve()))

    def test_pbc_visible_definition_keeps_SM_neutrinos_and_exact_mixing_above_spectral_range(self):
        low=model.pbc_bc1_benchmark(1.8,.001)
        high=model.pbc_bc1_benchmark(70.,.001)
        self.assertEqual(low['low_mass_coupling'],'epsilon * e * Q_f')
        self.assertEqual(low['width']['provider'],'DarkCast 2.1')
        self.assertFalse(low['width']['neutral_current_width_included'])
        self.assertFalse(high['width']['hidden_decay_channels'])
        self.assertTrue(high['width']['neutral_current_width_included'])
        self.assertIn('neutrinos',high['width']['scope'])
        self.assertIn('axial',high['high_mass_coupling'])
        self.assertTrue(model.pbc_bc1_benchmark(12.,.001)['width']['neutral_current_width_included'])
        self.assertFalse(high['components'][0]['higher_order_corrections_qualified'])
        self.assertEqual(high['components'][0]['rate_order'],'LO')

    def test_pbc_definition_rejects_closed_channels_unknown_scenarios_and_unsupported_couplings(self):
        for mass,epsilon in [(.211316751,1.e-5),(.1,1.e-5),(float('nan'),1.e-5),
                             (1.,0.),(1.,.02),(1.,float('inf'))]:
            with self.subTest(mass=mass,epsilon=epsilon):
                with self.assertRaises(ValueError):model.pbc_bc1_benchmark(mass,epsilon)
        with self.assertRaisesRegex(ValueError,'correlated'):
            model.pbc_bc1_benchmark(.9,1.e-5,radiation_variation='Baseline')

    def test_pbc_implementation_scope_stops_below_Z_without_restricting_the_particle_definition(self):
        definition=model.pbc_bc1_benchmark(90.,1.e-5)
        self.assertEqual(definition['implementation_mass_scope_gev']['maximum'],91.1876)
        self.assertIn('not the PBC model',definition['implementation_mass_scope_gev']['scope'])
        for mass in (91.1876,100.):
            with self.subTest(mass=mass):
                with self.assertRaisesRegex(ValueError,'below mZ'):
                    model.pbc_bc1_benchmark(mass,1.e-5)

    def test_source_capsule_checks_downloaded_bytes_and_rejects_archive_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'payload.tar.gz'
            with tarfile.open(archive,'w:gz') as payload:
                item=tarfile.TarInfo('../escape');item.size=1
                payload.addfile(item,io.BytesIO(b'x'))
            profile=dict(schema='shift-senscalc-source-capsule-v1',
                url='root://eosuser.cern.ch//eos/user/j/jniedzie/shift_cmssw_support/synthetic.tar.gz',
                bytes=archive.stat().st_size,sha256='0'*64,runtime_receipt_sha256='1'*64)
            def transfer(argv,**kwargs):Path(argv[-1]).write_bytes(archive.read_bytes())
            with patch.object(model.subprocess,'run',side_effect=transfer):
                with self.assertRaisesRegex(ValueError,'pinned bytes'):
                    model.unpack_runtime(profile,root/'corrupt')
                profile['sha256']=model.digest(archive)
                with self.assertRaisesRegex(ValueError,'Unsafe'):
                    model.unpack_runtime(profile,root/'escape_attempt')
            self.assertFalse((root/'escape').exists())

    @staticmethod
    def collision(trial,weights):
        return dict(trial=trial,generator_success=True,native_weight=1.,
            candidates=[dict(id=4900221,weight_over_epsilon_squared=weight,
                p4=[.5,0.,0.,math.sqrt(.5**2-.25**2)]) for weight in weights])

    def test_multiple_candidates_share_one_source_exposure_and_statistical_trial(self):
        rows=[self.collision(1,[2.,3.]),self.collision(2,[]),self.collision(3,[7.])]
        result=model.source_summary(rows,30.)
        self.assertEqual(result['source_collisions'],3)
        self.assertEqual(result['candidates'],3)
        self.assertEqual(result['sigma_over_epsilon_squared_pb'],120.e9)
        self.assertAlmostEqual(result['candidate_effective_statistics'],144./62.)
        self.assertAlmostEqual(result['mean_beta_gamma'],math.sqrt(3.))
        self.assertAlmostEqual(result['sigma_statistical_error_over_epsilon_squared_pb']/30.e9,
            math.sqrt(26./6.))
        self.assertFalse(result['dimuon_branching_included'])
        self.assertFalse(result['decay_acceptance_included'])

    def test_generator_failures_never_become_zero_response_or_completed_exposure(self):
        rows=[self.collision(1,[2.]),dict(trial=2,generator_success=False,candidates=[]),
              self.collision(3,[])]
        result=model.source_summary(rows,30.)
        self.assertEqual((result['source_calls'],result['source_collisions'],result['generator_failures']),(3,2,1))
        self.assertFalse(result['rate_qualified'])
        self.assertIsNone(result['sigma_over_epsilon_squared_pb'])
        self.assertIsNone(result['channels']['eta-decay']['sigma_over_epsilon_squared_pb'])
        with self.assertRaisesRegex(ValueError,'every original source trial'):
            model.source_summary([rows[0],rows[2]],30.)

    def test_zero_observed_candidates_do_not_get_a_zero_uncertainty(self):
        result=model.source_summary([self.collision(1,[]),self.collision(2,[])],30.)
        self.assertEqual(result['sigma_over_epsilon_squared_pb'],0.)
        self.assertIsNone(result['sigma_statistical_error_over_epsilon_squared_pb'])
        self.assertIsNone(result['mean_beta_gamma'])

    def test_a_cohort_cannot_silently_change_the_signal_mass(self):
        rows=[self.collision(1,[2.]),self.collision(2,[])]
        result=model.source_summary(rows,30.,mass=.25)
        self.assertAlmostEqual(result['mean_beta_gamma'],math.sqrt(3.))
        with self.assertRaisesRegex(ValueError,'mass differs'):
            model.source_summary(rows,30.,mass=.4)

    def test_native_threshold_roundoff_uses_the_existing_frozen_GEN_mass_support(self):
        c=dict(id=4900113,weight_over_epsilon_squared=1.,
            p4=[92.55666457401239,-.14361024122829152,.17390699612242702,-92.55612831963005])
        rows=[dict(trial=1,generator_success=True,native_weight=1.,candidates=[c]),
              dict(trial=2,generator_success=True,native_weight=1.,candidates=[])]
        model.source_summary(rows,26.,mass=.22)
        # A displacement beyond the unchanged +/-1 ppm GEN mass support fails.
        with self.assertRaisesRegex(ValueError,'mass differs'):
            model.source_summary(rows,26.,mass=.22*(1+3.e-6))

    def test_native_pole_mass_review_is_exact_and_preserves_source_accounting(self):
        p4=[129.07712435564986,.6339862424770483,.1737338378333942,-129.07448202175664]
        candidate=dict(id=4900223,weight_over_epsilon_squared=.08395798210197798,p4=p4)
        rows=[dict(trial=1,generator_success=True,native_weight=1.,candidates=[candidate]),
              dict(trial=2,generator_success=True,native_weight=1.,candidates=[])]
        original=json.dumps(rows)
        result=model.source_summary(rows,26.,mass=.5)
        self.assertEqual(result['candidates'],1)
        self.assertEqual(result['sum_weights_over_epsilon_squared'],candidate['weight_over_epsilon_squared'])
        self.assertEqual(json.dumps(rows),original)
        for unreviewed in ([math.nextafter(p4[0],math.inf),*p4[1:]],p4):
            candidate['p4']=unreviewed
            with self.assertRaisesRegex(ValueError,'mass differs'):
                model.source_summary(rows,26.,mass=.5 if unreviewed is not p4 else .49999)

    def test_runtime_tampering_or_escape_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);binary=directory/'source';binary.write_bytes(b'synthetic runtime')
            paths=['source','upstream/lib/libpythia8.so','upstream/share/Pythia8/xmldoc/ParticleData.xml',
                   'upstream/share/Pythia8/xmldoc/Index.xml','upstream/share/Pythia8/xmldoc/HadronWidths.dat',
                   'upstream/share/Pythia8/xmldoc/NucleonExcitations.dat',
                   'upstream/share/Pythia8/pdfdata/synthetic.dat']
            for name in paths[1:]:
                p=directory/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'synthetic input')
            receipt=dict(schema='shift-senscalc-source-runtime-v1',complete=True,
                upstream_commit=model.PYTHIA_COMMIT,archive_sha256=model.ARCHIVE_SHA256,
                upstream_directory='upstream',files={name:model.digest(directory/name) for name in paths})
            model.write_json(directory/'runtime.json',receipt)
            model.checked_runtime(directory)
            incomplete=dict(receipt,files={name:sha for name,sha in receipt['files'].items() if '/pdfdata/' not in name})
            model.write_json(directory/'runtime.json',incomplete)
            with self.assertRaisesRegex(ValueError,'auxiliary PDF data'):
                model.checked_runtime(directory)
            model.write_json(directory/'runtime.json',receipt)
            binary.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'Changed frozen reference'):
                model.checked_runtime(directory)
            binary.write_bytes(b'synthetic runtime')
            receipt['files']['../outside']=model.digest(binary)
            model.write_json(directory/'runtime.json',receipt)
            with self.assertRaisesRegex(ValueError,'Changed frozen reference'):
                model.checked_runtime(directory)

    def test_published_log_interpolation_and_epsilon_scaling_without_extrapolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            table=Path(tmp)/'width.txt';table.write_text('0.1 0.01\n0.2 0.04\n')
            with patch.object(model,'WIDTH_SHA256',model.digest(table)):
                self.assertAlmostEqual(model.total_width(table,math.sqrt(.02),.001)/1.e-6,.02)
                self.assertAlmostEqual(model.total_width(table,.1,.002)/model.total_width(table,.1,.001),4.)
                with self.assertRaisesRegex(ValueError,'outside pinned'):
                    model.total_width(table,.21,.001)
                table.write_text('0.1 0.02\n0.2 0.08\n')
                with self.assertRaisesRegex(ValueError,'Unexpected pinned'):
                    model.total_width(table,.1,.001)


if __name__=='__main__':unittest.main()
