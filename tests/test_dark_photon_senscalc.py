# Code style: AI-code. Exposure, frozen-input and interpolation controls.
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dark_photon_senscalc as model


class SensCalcBenchmark(unittest.TestCase):
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
