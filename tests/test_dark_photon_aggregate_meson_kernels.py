# Code style: AI-code. AI-generated isolated aggregate-kernel closure tests.
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_statistics as controller
from test_dark_photon_charged_rho_radiative import MODEL as CHARGED_MODEL


class AggregateMesonKernels(unittest.TestCase):
    def model(self, entries):
        path=Path(__file__).resolve().parents[1]/'config/campaigns/dark_photon_meson_extended_model.json'
        model=controller.read(path)
        model['parents']=[row for row in model['parents'] if row.get('decay_type')!='charged-vector-dipion']
        if entries==16:
            model['parents']=[row for row in model['parents'] if row.get('decay_type')!='vector-dipion']
        if entries==19:
            for pdg in (213,-213):
                row=copy.deepcopy(next(row for row in model['parents'] if row['pdg_id']==pdg))
                row.update(channel=('rho_plus' if pdg>0 else 'rho_minus')+'->pi_charged_pi0_Aprime',
                    decay_type='charged-vector-dipion',charged_pion_mass_gev=CHARGED_MODEL['charged_pion_mass_gev'],
                    neutral_pion_mass_gev=CHARGED_MODEL['neutral_pion_mass_gev'],br_pipi=1.,
                    recoil_pdg_ids=[211 if pdg>0 else -211,111],
                    charged_rho_radiative_model=copy.deepcopy(CHARGED_MODEL))
                model['parents'].append(row)
        model['channels']=[row['channel'] for row in model['parents']]
        self.assertEqual(len(model['parents']),entries)
        return model

    def fixture(self, root, entries, missing=None):
        tools=root/'tools';tools.mkdir()
        names=['aggregate_dark_photon_samples.py','dark_photon_production_support.py',
            'export_dark_photon_cross_sections.py','pythia_generation_ledger.py','dark_photon_meson.py',
            'dark_photon_meson_source.py','dark_photon_vector_meson.py','dark_photon_dipion_meson.py']
        # Current source contains lazy imports for its complete model. All
        # referenced helpers travel with it, including for a smaller card.
        names += ['dark_photon_rho_radiative.py','dark_photon_charged_rho_radiative.py',
            'dark_photon_kaon.py','dark_photon_kaon_leptonic.py','dark_photon_weak_meson.py',
            'dark_photon_model.py','dark_photon_neutral_vector_dipion.py','dark_photon_double_radiative.py',
            'dark_photon_material_selection.py','shift_production_selection.py','shift_material_preselection.py']
        for name in names:
            if name!=missing:shutil.copy2(Path(controller.__file__).with_name(name),tools/name)
        card=root/'model.json';controller.new(card,self.model(entries))
        guard=root/'guard';guard.mkdir()
        controller.new(guard/'seal.json',{})
        controller.new(guard/'plan.json',dict(points=[dict(point='m0p3_prompt')]))
        (guard/'finalizer_worker.sh').write_text('#!/bin/bash\npython3 '+str(guard/'finalize_point.py')+'\n')
        (guard/'finalizer.sub').write_text('initialdir = '+str(guard)+'\narguments = old\nqueue\n')
        sources=[]
        for index in range(2):
            plan=root/('source'+str(index)+'.json')
            controller.new(plan,dict(points=[dict(point='m0p3_prompt',mass_gev=.3,epsilon=.001,
                proper_ctau_mm=0.,sampling_epsilon=.001,events=20)]))
            sources.append(dict(point='m0p3_prompt',plan_path=str(plan)))
        config=dict(directory=str(root),tools=str(tools),production_mechanism='meson-radiative',
            model_card=str(card),aggregation_guard=str(guard),campaign_prefix='closure-test',
            eos_base='/eos/test',conditions={})
        return config,sources,card

    def test_isolated_16_17_19_aggregate_helpers_import_and_compute_model_bound(self):
        for entries in (16,17,19):
            with self.subTest(entries=entries),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                config,sources,card=self.fixture(root,entries)
                target=root/'aggregate'
                controller.aggregate_plan(config,'m0p3_prompt',sources,target)
                probe='''import json,math,sys
sys.path.insert(0,sys.argv[1])
import aggregate_dark_photon_samples
from dark_photon_meson_source import resolved_parent_models,coefficient_bound
card=json.load(open(sys.argv[2]))
parents={row['pdg_id']:dict(pdg_id=row['pdg_id'],mass_min_gev=row['mass_gev'],
    mass_max_gev=row['mass_gev'],mass_width_gev=0.) for row in card['parents']}
models=resolved_parent_models(card,dict(parents=list(parents.values())),.3)
bound=coefficient_bound(113.,.5,models,.3)
assert math.isfinite(bound) and bound>0
print(json.dumps(dict(entries=len(card['parents']),bound=bound)))
'''
                completed=subprocess.run([sys.executable,'-I','-B','-c',probe,str(target),str(card)],
                    cwd=target,capture_output=True,text=True,check=True,timeout=120)
                self.assertEqual(json.loads(completed.stdout)['entries'],entries)
                self.assertTrue((target/'dark_photon_rho_radiative.py').is_file())
                self.assertTrue((target/'dark_photon_charged_rho_radiative.py').is_file())
                plan=controller.read(target/'plan.json')
                for name in ('dark_photon_rho_radiative.py','dark_photon_charged_rho_radiative.py'):
                    if (target/name).is_file():
                        self.assertEqual(plan['dependencies'][str(target/name)],controller.sha(target/name))

    def test_nested_lazy_imports_are_frozen_recursively_and_missing_dependency_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for name in ('aggregate_dark_photon_samples','dark_photon_production_support',
                         'export_dark_photon_cross_sections','pythia_generation_ledger'):
                (root/(name+'.py')).write_text('')
            (root/'aggregate_dark_photon_samples.py').write_text('def rate():\n from dark_photon_kaon import kernel\n')
            (root/'dark_photon_kaon.py').write_text('def kernel():\n import dark_photon_weak_meson\n')
            dependency=root/'dark_photon_weak_meson.py';dependency.write_text('')
            names=controller.aggregate_helper_names(dict(tools=str(root)))
            self.assertIn('dark_photon_kaon.py',names)
            self.assertIn('dark_photon_weak_meson.py',names)
            dependency.unlink()
            with self.assertRaisesRegex(FileNotFoundError,'dark_photon_weak_meson'):
                controller.aggregate_helper_names(dict(tools=str(root)))

    def test_missing_required_kernel_stops_before_creating_aggregate(self):
        for entries,missing in ((17,'dark_photon_rho_radiative.py'),
                                (19,'dark_photon_rho_radiative.py'),
                                (19,'dark_photon_charged_rho_radiative.py')):
            with self.subTest(entries=entries),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                config,sources,_=self.fixture(root,entries,missing)
                target=root/'aggregate'
                with self.assertRaisesRegex(FileNotFoundError,missing):
                    controller.aggregate_plan(config,'m0p3_prompt',sources,target)
                self.assertFalse(target.exists())


if __name__=='__main__':unittest.main()
