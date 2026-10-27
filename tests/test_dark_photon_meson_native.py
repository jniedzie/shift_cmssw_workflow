# Code style: AI-code. AI-generated clean-runtime native-library resolution tests.
import os
import copy
import hashlib
import json
import math
from functools import wraps
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dark_photon_meson_native import cmssw_native_libraries, enable_correlated_recoils


class NativeLibrariesTest(unittest.TestCase):
    def test_validation_override_preflight_preserves_all_changed_table_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'validation.dec'
            path.write_text('Decay J/psi\n1.0 mu+ mu- VLL;\nEnddecay\nEnd\n')
            record=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            previous=dict(validation_decay_override=True,files=[record])
            source=SimpleNamespace(_shift_evtgen_provenance=previous)
            self.assertIs(enable_correlated_recoils(source,decay_file=path),previous)
            # Changed bytes at the same path are also a different override.
            path.write_text('Decay J/psi\n1.0 K+ K- PHSP;\nEnddecay\nEnd\n')
            with self.assertRaisesRegex(RuntimeError,'fresh interpreter'):
                enable_correlated_recoils(source,decay_file=path)
            self.assertIs(source._shift_evtgen_provenance,previous)

    def test_missing_validation_file_is_rejected_before_native_initialization(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileNotFoundError):
                enable_correlated_recoils(SimpleNamespace(),decay_file=Path(temporary)/'absent.dec')

    def test_scram_versioned_sonames_and_aliases_resolve_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hepmc = root/'hepmc'; hepmc.mkdir()
            pythia = root/'pythia'; pythia.mkdir()
            for name in ('HepMC', 'HepMCfio'):
                (hepmc/('lib'+name+'.so.4.0.0')).write_bytes(b'native')
                (hepmc/('lib'+name+'.so.4')).symlink_to('lib'+name+'.so.4.0.0')
            (pythia/'libpythia8.so').write_bytes(b'native')
            def information(command, cwd, text):
                self.assertEqual(command[:3], ['scram', 'tool', 'info'])
                self.assertEqual(cwd, root/'src')
                return 'LIBDIR='+str(hepmc if command[-1]=='hepmc' else pythia)+'\n'
            with patch.dict(os.environ, {'CMSSW_BASE':str(root)}), \
                 patch('dark_photon_meson_native.subprocess.check_output', side_effect=information):
                self.assertEqual(cmssw_native_libraries(), [str(hepmc/'libHepMC.so.4.0.0'),
                    str(hepmc/'libHepMCfio.so.4.0.0'), str(pythia/'libpythia8.so')])
                # An unrelated inherited library is never a fallback.
                (hepmc/'libHepMC.so.4.0.0').unlink()
                with self.assertRaisesRegex(RuntimeError, 'Missing or ambiguous'):
                    cmssw_native_libraries()

    def test_ambiguous_scram_directory_is_rejected(self):
        with patch.dict(os.environ, {'CMSSW_BASE':'/current/release'}), \
             patch('dark_photon_meson_native.subprocess.check_output',
                   return_value='LIBDIR=/one\nLIBDIR=/two\n'):
            with self.assertRaisesRegex(RuntimeError, 'SCRAM library directory'):
                cmssw_native_libraries()


def isolated_native_case(test):
    """Exercise EvtGen's actual process-global lifecycle in a clean process."""
    @wraps(test)
    def run(self):
        if os.environ.get('SHIFT_NATIVE_RECOIL_CASE')==test.__name__:
            return test(self)
        environment=dict(os.environ,SHIFT_NATIVE_RECOIL_CASE=test.__name__)
        started=time.monotonic()
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),
            type(self).__name__+'.'+test.__name__],env=environment,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=240)
        self.assertEqual(result.returncode,0,result.stdout)
        print(test.__name__+': passed in '+format(time.monotonic()-started,'.1f')+' s',
            file=sys.stderr,flush=True)
    return run


@unittest.skipUnless(os.environ.get('CMSSW_BASE') and os.environ.get('PYTHIA8DATA')
                     and os.environ.get('EVTGENDATA'), 'Requires the native CMSSW/EvtGen runtime')
class CorrelatedRecoilTest(unittest.TestCase):
    def setUp(self):
        if os.environ.get('SHIFT_NATIVE_RECOIL_CASE')!=self._testMethodName:
            return
        import ROOT
        from dark_photon_meson_native import native_source
        from fixed_target_generation import beam_settings
        self.ROOT=ROOT
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.source=native_source(beam_settings(6800.)+['SoftQCD:nonDiffractive=on',
            '13:mayDecay=off','130:mayDecay=off','310:mayDecay=off','321:mayDecay=off',
            '443:m0=3.09690'],20261026,[441,443,10441,20443,445,10443,100443])
        if not hasattr(ROOT,'shiftTestRecoilParent'):
            fixture=Path(__file__).parent/'fixtures/correlated_recoil.cc'
            self.assertTrue(ROOT.gInterpreter.Declare(fixture.read_text()))

    def seed_parent(self,pdg,mass,p4):
        values=self.ROOT.std.vector('double')()
        for value in p4:values.push_back(value)
        self.ROOT.shiftTestRecoilParent(self.source,pdg,mass,values)

    @isolated_native_case
    def test_default_heavy_recoils_decay_and_preserve_spectators(self):
        from dark_photon_meson_native import inject_decay
        from dark_photon_quarkonium import sample_decay
        from dark_photon_meson import boost
        from test_dark_photon_quarkonium import QuarkoniumDecayTest,channel_model,MASSES
        identifiers={'eta_c':441,'chi_c0':10441,'chi_c1':20443,'chi_c2':445,'h_c':10443,'psi_2S':100443}
        for channel in QuarkoniumDecayTest.channels:
            model=channel_model(channel);name=channel.split('->')[0];mass=MASSES[name]
            for seed in range(3):
                with self.subTest(channel=channel,seed=seed):
                    p4=boost((mass,0.,0.,0.),(.13,-.24,.72))
                    self.seed_parent(identifiers[name],mass,p4)
                    before=self.ROOT.shiftTestRecoilSpectator(self.source)
                    sampled=sample_decay(p4,.22,100.,random.Random(31+seed),model=model)
                    inject_decay(self.source,7,sampled)
                    self.assertEqual(self.ROOT.shiftTestRecoilSpectator(self.source),before)
                    self.assertEqual(len(sampled['recoil_records']),1)
                    if model['recoil_pdg_id']!=22:
                        self.assertTrue(sampled['recoil_records'][0]['natural_decay'])
                    if sampled['recoil_spin_correlations_required']:
                        self.assertTrue(sampled['recoil_spin_density_attached'])
                        self.assertEqual(sampled['recoil_spin_adapter'],'EvtGen-forward-density-v1')
        from dark_photon_vector_meson import sample_decay as vector_decay
        for recoil,recoil_mass in ((111,.1349768),(221,.547862),(331,.95778)):
            with self.subTest(jpsi_recoil=recoil):
                p4=boost((3.0969,0.,0.,0.),(.13,-.24,.72))
                self.seed_parent(443,3.0969,p4)
                before=self.ROOT.shiftTestRecoilSpectator(self.source)
                sampled=vector_decay(p4,recoil_mass,.3,100.,random.Random(42),polarization='unpolarized')
                sampled.update(recoil_pdg_id=recoil,recoil_mass_gev=recoil_mass)
                inject_decay(self.source,7,sampled)
                self.assertEqual(self.ROOT.shiftTestRecoilSpectator(self.source),before)
                self.assertTrue(sampled['recoil_records'][0]['natural_decay'])

    @isolated_native_case
    def test_real_evtgen_muons_follow_transported_diagonal_and_offdiagonal_density(self):
        from dark_photon_meson_native import enable_correlated_recoils,inject_decay
        from dark_photon_quarkonium import sample_decay
        from dark_photon_meson import boost
        from test_dark_photon_quarkonium import channel_model,MASSES
        path=Path(self.temporary.name)/'vector.dec'
        path.write_text('Decay J/psi\n1.0 mu+ mu- VLL;\nEnddecay\nEnd\n')
        self.source._shift_evtgen_provenance=enable_correlated_recoils(self.source,decay_file=path)
        self.assertTrue(self.source._shift_evtgen_provenance['validation_decay_override'])
        self.assertIs(enable_correlated_recoils(self.source),self.source._shift_evtgen_provenance)
        mass=MASSES['chi_c0'];model=channel_model('chi_c0->Jpsi_Aprime')
        p4=boost((mass,0.,0.,0.),(.25,-.17,.61))
        template=sample_decay(p4,.3,100.,random.Random(41),model=model)
        beta2=1-4*(.1056583715/MASSES['Jpsi'])**2
        count=2500
        matrices=[[[0.,0.,0.],[0.,0.,0.],[0.,0.,1.]],
                  [[.5,0.,0.],[0.,.5,0.],[0.,0.,0.]],
                  [[.5,0.,.5],[0.,0.,0.],[.5,0.,.5]]]
        for density in matrices:
            moments=[[0.]*3 for _ in range(3)];squares=[[0.]*3 for _ in range(3)]
            for event in range(count):
                self.seed_parent(10441,mass,p4);sampled=copy.deepcopy(template)
                sampled['recoil_spin_density_matrix']=[[[value,0.] for value in row] for row in density]
                inject_decay(self.source,7,sampled)
                muon=list(self.ROOT.shiftTestRecoilMuon(self.source))
                recoil=sampled['recoil_p4'];recoil_mass=MASSES['Jpsi']
                energy=(muon[0]*recoil[0]-sum(a*b for a,b in zip(muon[1:],recoil[1:])))/recoil_mass
                momentum=math.sqrt(energy**2-.1056583715**2)
                direction=[-(muon[0]*basis[0]-sum(a*b for a,b in zip(muon[1:],basis[1:])))/momentum
                           for basis in sampled['recoil_polarization_basis_p4']]
                for i in range(3):
                    for j in range(3):
                        value=direction[i]*direction[j]
                        moments[i][j]+=value;squares[i][j]+=value*value
            for i in range(3):
                for j in range(3):
                    measured=moments[i][j]/count
                    expected=((5-beta2)*(i==j)-2*beta2*density[i][j])/(5*(3-beta2))
                    uncertainty=math.sqrt(max(0.,squares[i][j]/count-measured**2)/count)
                    self.assertLess(abs(measured-expected),6*uncertainty+.001,
                        dict(density=density,component=(i,j),measured=measured,expected=expected))

    @isolated_native_case
    def test_missing_spin_contract_is_rejected_before_mutating_the_graph(self):
        from dark_photon_meson_native import inject_decay
        from dark_photon_quarkonium import sample_decay
        from test_dark_photon_quarkonium import channel_model,MASSES
        mass=MASSES['chi_c0'];p4=(mass,0.,0.,0.)
        self.seed_parent(10441,mass,p4)
        sampled=sample_decay(p4,.3,100.,random.Random(41),model=channel_model('chi_c0->Jpsi_Aprime'))
        sampled.pop('recoil_spin_density_matrix');before=self.ROOT.shiftTestRecoilSpectator(self.source)
        with self.assertRaisesRegex(ValueError,'Missing conditional'):
            inject_decay(self.source,7,sampled)
        self.assertEqual(self.ROOT.shiftTestRecoilSpectator(self.source),before)

    @isolated_native_case
    def test_repeated_enable_is_safe_and_second_source_requires_fresh_process(self):
        from dark_photon_meson_native import enable_correlated_recoils,native_source,inject_decay
        from fixed_target_generation import beam_settings
        from dark_photon_quarkonium import sample_decay
        from test_dark_photon_quarkonium import channel_model,MASSES
        provenance=self.source._shift_evtgen_provenance
        self.assertIs(enable_correlated_recoils(self.source),provenance)
        self.assertFalse(provenance['validation_decay_override'])
        with self.assertRaisesRegex(self.ROOT.std.runtime_error,'fresh interpreter'):
            native_source(beam_settings(6800.)+['SoftQCD:nonDiffractive=on'],20261027,[10441])
        # A rejected second owner must leave the first source usable.
        mass=MASSES['chi_c0'];p4=(mass,0.,0.,0.)
        self.seed_parent(10441,mass,p4)
        sampled=sample_decay(p4,.3,100.,random.Random(41),model=channel_model('chi_c0->Jpsi_Aprime'))
        inject_decay(self.source,7,sampled)
        self.assertTrue(sampled['recoil_spin_density_attached'])
        # Generating EvtGen's full tree must not bypass the source's disabled
        # weak decays. These kaons belong to later material transport.
        path=Path(self.temporary.name)/'kaons.dec'
        path.write_text('Decay J/psi\n1.0 K+ K- PHSP;\nEnddecay\nEnd\n')
        enable_correlated_recoils(self.source,decay_file=path)
        self.seed_parent(10441,mass,p4)
        inject_decay(self.source,7,sampled)
        self.assertEqual(list(self.ROOT.shiftTestStoppedRecoilKaons(self.source)),[-321,321])


if __name__ == '__main__':
    unittest.main()
