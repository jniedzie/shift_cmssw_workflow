#!/usr/bin/env python3
# Code style: AI-code. AI-generated isolated-process exact authority regression.
"""Run with the newly built private testRecordedPrimaryAuthority binary."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BINARY=sys.argv.pop(1)
ROWS=json.loads(subprocess.check_output([BINARY,'cases'],text=True))

class ExactParticleAuthority(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'authority.json'
        self.data=dict(schema='shift-recorded-primary-particle-authority-v1',complete=True,
                       source_GEN_unchanged=True,entries=copy.deepcopy(ROWS))

    def run_case(self,index=0,mode='normal',expected='reject', *, authority=True,gen=True,wrong_sha=False):
        env={key:value for key,value in os.environ.items() if not key.startswith('SHIFT_RECORDED_PRIMARY_')}
        if authority:
            self.path.write_text(json.dumps(self.data))
            env['SHIFT_RECORDED_PRIMARY_AUTHORITY']=str(self.path)
            env['SHIFT_RECORDED_PRIMARY_AUTHORITY_SHA256']=('0'*64 if wrong_sha else
                hashlib.sha256(self.path.read_bytes()).hexdigest())
        if gen:
            env['SHIFT_RECORDED_PRIMARY_GEN_SHA256']=ROWS[index]['source_gen_sha256']
        result=subprocess.run([BINARY,str(index),mode,expected],env=env,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_four_observed_particles_reconcile_only_with_exact_authority(self):
        for index in range(4):
            with self.subTest(index=index):
                self.run_case(index,expected='accept')
                self.run_case(index,authority=False)

    def test_event_barcode_pdg_and_one_bit_momentum_or_mass_mismatch_reject(self):
        for mode in ['wrong-event','wrong-barcode','wrong-pdg','wrong-px','wrong-mass']:
            with self.subTest(mode=mode):self.run_case(mode=mode)

    def test_wrong_sha_missing_GEN_and_foreign_GEN_reject(self):
        self.run_case(wrong_sha=True);self.run_case(gen=False)
        self.data['entries'][0]['source_gen_sha256']='f'*64
        self.run_case()

    def test_changed_proof_or_too_small_forward_bound_reject(self):
        self.data['complete']=False;self.run_case()
        self.data['complete']=True
        self.data['entries'][0]['maximum_energy_residual_GeV']*=.01
        self.run_case()

    def test_conservation_cap_does_not_expand_for_large_authority_bound(self):
        row=self.data['entries'][0]
        energy=float.fromhex(row['p4_hex'][3]);row['p4_hex'][3]=(energy+.01*energy).hex()
        row['maximum_energy_residual_GeV']=100.
        self.run_case(mode='over-cap')

    def test_duplicate_identity_rejects(self):
        self.data['entries'].append(copy.deepcopy(self.data['entries'][0]));self.run_case()

    def test_old_v3_good_control_does_not_consult_optional_authority(self):
        self.run_case(mode='legacy',expected='accept',authority=False,gen=False)
        self.run_case(mode='legacy',expected='accept',wrong_sha=True,gen=False)

if __name__=='__main__':unittest.main()
