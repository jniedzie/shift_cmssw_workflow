# Code style: AI-code. Independent two-muon probe input guards.
import copy
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from shift_two_muon_probe import require_probe_units,validate_probe,write_hepmc


class TwoMuonProbeTest(unittest.TestCase):
    def probe(self):
        return dict(schema='shift-two-muon-response-probe-v1',timing='already-timed-CMS-frame',event_id=[600500001,1,959],
            diagnostic_only=True,new_GEN_exposure=0,physical_cross_section_pb=None,probe_weight=1.,
            muons=[dict(barcode=43,pdg=-13,momentum_GeV=[1.,2.,-100.],energy_GeV=math.hypot(1.,2.,100.,.105),generated_mass_GeV=.105,
                        birth_mm=[-68.,0.,148440.,-148440.]),
                   dict(barcode=51,pdg=13,momentum_GeV=[-2.,1.,-200.],energy_GeV=math.hypot(2.,1.,200.,.105),generated_mass_GeV=.105,
                        birth_mm=[-69.,1.,148400.,-148399.])])

    def test_writer_keeps_independent_vertices_momenta_times_and_signs(self):
        probe=self.probe();before=copy.deepcopy(probe)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'probe.hepmc';write_hepmc(path,probe);lines=path.read_text().splitlines()
        self.assertEqual(probe,before)
        vertices=[line.split() for line in lines if line.startswith('V ')]
        particles=[line.split() for line in lines if line.startswith('P ')]
        self.assertEqual([list(map(float,v[3:7])) for v in vertices],[m['birth_mm'] for m in probe['muons']])
        self.assertEqual([list(map(float,p[3:7])) for p in particles],[m['momentum_GeV']+[m['energy_GeV']] for m in probe['muons']])
        self.assertEqual([int(p[2]) for p in particles],[-13,13])

    def test_rejects_same_sign_or_extra_particle(self):
        probe=self.probe();probe['muons'][1]['pdg']=-13
        with self.assertRaisesRegex(ValueError,'exactly two opposite-sign'):validate_probe(probe)
        probe=self.probe();probe['muons'].append(copy.deepcopy(probe['muons'][0]))
        with self.assertRaisesRegex(ValueError,'exactly two opposite-sign'):validate_probe(probe)

    def test_rejects_float_pdg_before_writing_integer_hepmc_field(self):
        probe=self.probe();probe['muons'][0]['pdg']=-13.
        with self.assertRaisesRegex(ValueError,'exactly two opposite-sign'):validate_probe(probe)

    def test_requires_native_gev_mm_instead_of_silently_relabelling(self):
        hep=Mock();hep.momentum_unit.return_value=7;hep.length_unit.return_value=9
        with patch('shift_gen_material_features.hepmc_unit_names',
                   return_value=({7:'GEV',8:'MEV'},{9:'MM',10:'CM'})):
            require_probe_units(hep)
            for momentum,length in [(8,9),(7,10),(0,9)]:
                hep.momentum_unit.return_value=momentum;hep.length_unit.return_value=length
                with self.assertRaisesRegex(ValueError,'native GEV/MM'):require_probe_units(hep)

    def test_requires_known_already_applied_physical_time(self):
        probe=self.probe();probe['timing']='apply-a-favorable-offset'
        with self.assertRaisesRegex(ValueError,'physical CMS ct'):validate_probe(probe)
        probe=self.probe();probe['muons'][0]['birth_mm'][3]=float('nan')
        with self.assertRaisesRegex(ValueError,'finite birth_mm'):validate_probe(probe)

    def test_rejects_duplicate_barcodes_or_spacelike_p4(self):
        probe=self.probe();probe['muons'][1]['barcode']=43
        with self.assertRaisesRegex(ValueError,'barcodes must differ'):validate_probe(probe)
        probe=self.probe();probe['muons'][0]['energy_GeV']=1.
        with self.assertRaisesRegex(ValueError,'timelike'):validate_probe(probe)

    def test_probe_cannot_claim_a_physical_rate_or_exposure(self):
        for key,value in [('probe_weight',2.),('physical_cross_section_pb',1.),('new_GEN_exposure',1),('diagnostic_only',False)]:
            probe=self.probe();probe[key]=value
            with self.assertRaisesRegex(ValueError,'zero SM exposure'):validate_probe(probe)

    def test_boosted_roundoff_is_accepted_but_inconsistent_energy_is_rejected(self):
        probe=self.probe();muon=probe['muons'][0]
        muon['momentum_GeV']=[1.,2.,-6000.]
        muon['energy_GeV']=math.hypot(*muon['momentum_GeV'],muon['generated_mass_GeV'])
        self.assertIs(validate_probe(probe),probe)
        muon['energy_GeV']+=1.e-5
        with self.assertRaisesRegex(ValueError,'propagated precision'):validate_probe(probe)


if __name__=='__main__':unittest.main()
