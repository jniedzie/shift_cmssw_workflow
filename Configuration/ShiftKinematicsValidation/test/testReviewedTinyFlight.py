#!/usr/bin/env python3
# Code style: AI-code. AI-generated isolated-process tiny-flight authority tests.
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BINARY=sys.argv.pop(1)
ROW=dict(source_gen_sha256='a'*64,hepmc_event_number=42,barcode=7,pdg=413,status=2,
         p4_hex=[x.hex() for x in (0.,0.,100.,100.02)],generated_mass_hex=(2.01).hex(),
         birth_hex=[x.hex() for x in (0.,0.,0.,0.)],
         end_hex=[x.hex() for x in (0.,0.,1.2e-5,1.2e-5)],
         daughter_barcodes=[8,9],maximum_flight_mm=1.5e-5)

class ReviewedTinyFlight(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'authority.json';self.cases=Path(self.tmp.name)/'cases.json'
        self.cases.write_text(json.dumps([ROW]))
        self.data=dict(schema='shift-recorded-primary-particle-authority-v1',complete=True,
                       source_GEN_unchanged=True,
                       entries=[dict(ROW,maximum_energy_residual_GeV=1.e-6)],
                       reviewed_tiny_flights=[copy.deepcopy(ROW)])

    def run_case(self,mode='normal',expected='reject',authority=True,wrong_sha=False,gen=True):
        env={k:v for k,v in os.environ.items() if not k.startswith('SHIFT_RECORDED_PRIMARY_')}
        if authority:
            self.path.write_text(json.dumps(self.data))
            env.update(SHIFT_RECORDED_PRIMARY_AUTHORITY=str(self.path),
                       SHIFT_RECORDED_PRIMARY_AUTHORITY_SHA256=('0'*64 if wrong_sha else hashlib.sha256(self.path.read_bytes()).hexdigest()))
        if gen:env['SHIFT_RECORDED_PRIMARY_GEN_SHA256']=ROW['source_gen_sha256']
        result=subprocess.run([BINARY,str(self.cases),'0',mode,expected],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_exact_reviewed_particle_accepts(self):
        self.run_case(expected='accept')

    def test_identity_vectors_vertices_mass_graph_and_policy_are_bound(self):
        for mode in ['event','barcode','pdg','status','mass','momentum','birth','end','daughter','zero-budget','other-budget']:
            with self.subTest(mode=mode):self.run_case(mode)

    def test_missing_wrong_or_changed_authority_rejects(self):
        self.run_case(authority=False);self.run_case(wrong_sha=True);self.run_case(gen=False)
        self.data['reviewed_tiny_flights'][0]['source_gen_sha256']='b'*64;self.run_case()
        self.data['complete']=False;self.run_case()

    def test_larger_unphysical_or_duplicate_review_rejects(self):
        for limit in [0.,-1.,2.e-5,float('inf')]:
            with self.subTest(limit=limit):
                self.data['reviewed_tiny_flights'][0]['maximum_flight_mm']=limit;self.run_case()
        self.data['reviewed_tiny_flights']=[copy.deepcopy(ROW),copy.deepcopy(ROW)];self.run_case()

    def test_old_authority_does_not_enable_a_new_flight(self):
        self.data.pop('reviewed_tiny_flights');self.run_case()

if __name__=='__main__':unittest.main()
