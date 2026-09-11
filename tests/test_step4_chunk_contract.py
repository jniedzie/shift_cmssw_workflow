"""Submission preflight must reject ambiguous chunks and incompatible covariance modes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
WORKFLOW=Path(__file__).resolve().parents[1]
class Step4Contract(unittest.TestCase):
    def check(self, chunks, jobs=1, **overrides):
        with tempfile.TemporaryDirectory() as temporary:
            manifest=Path(temporary)/'chunks.txt'; manifest.write_text(chunks)
            env=dict(os.environ, N_JOBS=str(jobs), STEP4_INPUTS_PER_JOB='1',
                     CONDOR_CHUNKS_FILE=str(manifest), SHIFT_TARGET_CONSISTENT_BACKWARD_COVARIANCE='1',
                     SHIFT_TARGET_MEAN_ENERGY_LOSS_JACOBIAN='1')
            env.update(overrides)
            return subprocess.run([str(WORKFLOW/'scripts/run_lss_paired_production.sh'),
                'control','contract_test_no_submission','--steps','4','--check'],env=env,
                capture_output=True,text=True)
    def test_sparse_chunk_ids_pass_without_submission(self):
        result=self.check('0\n37\n991\n',jobs=3)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('no build, cleanup, or submission',result.stdout)
    def test_invalid_or_duplicate_chunks_fail(self):
        for chunks,jobs in [('0\n0\n',2),('-1\n',1),('07\n',1),('1;false\n',1),('',1),('0\n',2)]:
            with self.subTest(chunks=chunks): self.assertNotEqual(self.check(chunks,jobs).returncode,0)
    def test_incompatible_covariance_fails(self):
        result=self.check('0\n',SHIFT_TARGET_CONSISTENT_BACKWARD_COVARIANCE='0')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('requires consistent backward covariance',result.stderr)
    def test_invalid_boolean_fails(self):
        self.assertNotEqual(self.check('0\n',SHIFT_TARGET_MEAN_ENERGY_LOSS_JACOBIAN='yes').returncode,0)
if __name__=='__main__': unittest.main()
