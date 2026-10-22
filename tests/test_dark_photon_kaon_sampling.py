# Code style: AI-code. AI-generated unconditional kaon-chain sampling controls.
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_kaon import sample_radiative_decay, sample_weak_decay
from dark_photon_meson import MUON_MASS_GEV, boost


def invariant(momentum):
    return momentum[0]**2-sum(x*x for x in momentum[1:])


def helicity_cosine(parent,decay):
    backward=tuple(-x/parent[0] for x in parent[1:])
    dark=boost(decay['dark_photon_p4'],backward)
    muon=boost(decay['muon_p4'],backward)
    muon=boost(muon,tuple(-x/dark[0] for x in dark[1:]))
    return sum(a*b for a,b in zip(dark[1:],muon[1:]))/math.sqrt(
        sum(x*x for x in dark[1:])*sum(x*x for x in muon[1:]))


class KaonSamplingTest(unittest.TestCase):
    def test_both_cp_tensors_use_identical_unpolarized_transverse_sampling(self):
        parent=boost((.497611,0.,0.,0.),(.2,-.1,-.9))
        odd=sample_radiative_decay(130,parent,.3,12.,random.Random(861022))
        even=sample_radiative_decay(310,parent,.3,12.,random.Random(861022))
        for key in ('dark_photon_p4','photon_p4','muon_p4','antimuon_p4',
                    'proper_decay_length_mm','decay_displacement_mm','decay_ct_mm'):
            self.assertEqual(odd[key],even[key])
        self.assertEqual((odd['kaon_cp_tensor'],even['kaon_cp_tensor']),('odd','even'))

    def test_all_qualified_chains_conserve_momentum_and_preserve_physical_masses(self):
        for pdg,parent_mass,recoil_mass in ((130,.497611,0.),(310,.497611,0.),
                (321,.493677,.13957039),(-321,.493677,.13957039),(310,.497611,.1349768)):
            for beta in ((0.,0.,0.),(.2,-.3,.8)):
                parent=boost((parent_mass,0.,0.,0.),beta)
                rng=random.Random(861022+pdg)
                for _ in range(12):
                    decay=(sample_radiative_decay(pdg,parent,.25,17.,rng) if recoil_mass==0 else
                           sample_weak_decay(pdg,parent,recoil_mass,.25,17.,rng))
                    recoil=decay.get('recoil_p4',decay.get('photon_p4'))
                    dark=decay['dark_photon_p4']
                    for actual,expected in zip((x+y for x,y in zip(dark,recoil)),parent):
                        self.assertAlmostEqual(actual,expected,places=12)
                    for actual,expected in zip((x+y for x,y in zip(decay['muon_p4'],decay['antimuon_p4'])),dark):
                        self.assertAlmostEqual(actual,expected,places=12)
                    for momentum,mass in ((dark,.25),(recoil,recoil_mass),
                            (decay['muon_p4'],MUON_MASS_GEV),(decay['antimuon_p4'],MUON_MASS_GEV)):
                        self.assertAlmostEqual(invariant(momentum),mass*mass,places=12)
                    self.assertTrue(decay['unconditional_decay'])
                    self.assertFalse(decay['parent_flight_included'])

    def test_sampled_muon_density_matches_independent_tensor_helicity_moments(self):
        mass=.25;r=(MUON_MASS_GEV/mass)**2;beta2=1-4*r
        parent=boost((.497611,0.,0.,0.),(.1,-.2,.8))
        expected_transverse=((1+4*r)/3+(1-4*r)/5)/((1+4*r)+(1-4*r)/3)
        expected_longitudinal=(1/3-beta2/5)/(1-beta2/3)
        for mode,expected in (('transverse',expected_transverse),('longitudinal',expected_longitudinal)):
            rng=random.Random(861022)
            samples=[]
            for _ in range(8000):
                decay=(sample_radiative_decay(310,parent,mass,12.,rng) if mode=='transverse' else
                       sample_weak_decay(310,parent,.1349768,mass,12.,rng))
                samples.append(helicity_cosine(parent,decay)**2)
            mean=sum(samples)/len(samples)
            self.assertLess(abs(mean-expected),.012)

    def test_only_relative_dark_flight_is_sampled_without_position_selection(self):
        parent=(.497611,0.,0.,0.);rng=random.Random(861022);proper=[];z=[]
        for _ in range(8000):
            decay=sample_radiative_decay(130,parent,.25,12.,rng)
            proper.append(decay['proper_decay_length_mm']);z.append(decay['dark_photon_p4'][3])
            self.assertNotIn('parent_proper_decay_length_mm',decay)
            for actual,p in zip(decay['decay_displacement_mm'],decay['dark_photon_p4'][1:]):
                self.assertEqual(actual,decay['proper_decay_length_mm']*p/.25)
        self.assertLess(abs(sum(proper)/len(proper)/12-1),.04)
        self.assertLess(abs(sum(x<24 for x in proper)/len(proper)-(1-math.exp(-2))),.015)
        self.assertLess(abs(sum(z)/len(z)),.004)

    def test_charge_assignment_and_unsupported_physics_remain_explicit(self):
        for pdg,recoil in ((321,211),(-321,-211),(310,111)):
            decay=sample_weak_decay(pdg,(.497611,0.,0.,0.),.13957039,.25,12.,random.Random(1))
            self.assertEqual(decay['recoil_pdg_id'],recoil)
            self.assertEqual(decay['dark_photon_polarization'],'longitudinal')
        for pdg in (311,-311,130,221):
            with self.assertRaises(ValueError):
                sample_weak_decay(pdg,(.497611,0.,0.,0.),.13957039,.25,12.,random.Random(1))
        for pdg in (311,-311,321,221):
            with self.assertRaises(ValueError):
                sample_radiative_decay(pdg,(.497611,0.,0.,0.),.25,12.,random.Random(1))


if __name__=='__main__':unittest.main()
