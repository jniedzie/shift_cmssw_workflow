# Code style: AI-code. AI-generated frozen-source and normalization regressions.
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dark_photon_dy_prescription as prescription


class DYPrescriptionTest(unittest.TestCase):
    def table(self):
        return dict(schema=prescription.TABLE_SCHEMA, name='msht20-nnlo',
            beam_energy_GeV=6800., hard_pdf=dict(set='MSHT20nnlo_as118', member=0),
            normalization_reference='native-clipped-LO', epsilon_reference=.001,
            native_pdf_settings=list(prescription.PDF_SYMMETRY_SETTINGS),
            epsilon_upper_bound=.001, small_mixing_qualified=True,
            weak_current=dict(sin2_theta_w=.2312, m_z_gev=91.1876),
            pdf_inputs={name:'0'*64 for name in prescription.PDF_INPUT_NAMES},
            pdf_data_root='/nonexistent/test/PDFs',
            theory_scope='Synthetic authority fixture, never a physics qualification',
            points=[dict(mass_gev=2., factor=1.8, common_numerical_error=.006, qualified=True)])

    def contract(self):
        return dict(mechanism='pure-on-shell-drell-yan', mass_gev=2., epsilon=.001,
            beam_energy_GeV=6800., requested_events=100, production_rate_correction=.005,
            physical_br_mumu=.25, width_authority=dict(ctau_mm=1.),
            process_settings=['StandardModel:sin2thetaW = 0.2312', '23:m0 = 91.1876'],
            cp5=['PDF:pSet = LHAPDF6:NNPDF31_nnlo_as_0118'])

    def test_legacy_path_needs_no_external_table_and_is_unchanged(self):
        source=self.contract();before=copy.deepcopy(source)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Unexpected optional input')):
            self.assertEqual(prescription.hard_pdf_settings(None), [])
            self.assertEqual(prescription.prescription_files(None), {})
            self.assertIsNone(prescription.configure_hard_pdf(None))
            self.assertEqual(prescription.apply_prescription(source, None), before)
            self.assertIsNone(prescription.validate_prescription(source, None))
            self.assertEqual(prescription.prescription_input_files(source, None), [])
        self.assertEqual(source, before)

    def test_source_freeze_and_isolated_replay_preserve_native_weights_and_rates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);table=root/'authority.json';table.write_text(json.dumps(self.table()))
            frozen=root/'frozen';frozen.mkdir()
            original_files=prescription.prescription_files
            files=original_files('msht20-nnlo', table)
            with patch.object(prescription, 'prescription_files', return_value=files):
                checks=prescription.freeze_prescription('msht20-nnlo', frozen)
            (frozen/'manifest.json').write_text(json.dumps(dict(dy_pdf_runtime=dict(
                schema='shift-dy-pdf-runtime-v1', files=self.table()['pdf_inputs']))))
            contract=prescription.apply_prescription(self.contract(), 'msht20-nnlo', table_path=table)
            self.assertEqual(contract['production_rate_correction'], .005)
            self.assertEqual(contract['physical_br_mumu'], .25)
            self.assertEqual(contract['requested_events'], 100)
            self.assertEqual(contract['cp5'], self.contract()['cp5'])
            spec=importlib.util.spec_from_file_location('isolated_dy_prescription', frozen/prescription.HELPER_NAME)
            isolated=importlib.util.module_from_spec(spec);spec.loader.exec_module(isolated)
            replay=isolated.validate_prescription(contract, frozen)
            self.assertEqual(replay['normalization']['factor'], 1.8)
            self.assertEqual(replay['normalization']['common_numerical_error'], .006)
            self.assertEqual(set(checks), {prescription.HELPER_NAME, prescription.TABLE_NAME})
            self.assertEqual(len(isolated.prescription_input_files(contract, frozen)), 2)
            with self.assertRaisesRegex(ValueError, 'already applied'):
                prescription.apply_prescription(contract, 'msht20-nnlo', table_path=table)
            (frozen/prescription.TABLE_NAME).write_text('{}')
            with self.assertRaises(ValueError):isolated.validate_prescription(contract, frozen)
            with patch.object(prescription, 'prescription_files', return_value=files):
                with self.assertRaisesRegex(ValueError, 'Frozen DY input differs'):
                    prescription.freeze_prescription('msht20-nnlo', frozen)

    def test_effective_hard_pdf_weak_current_and_code_must_match_the_frozen_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);table=root/prescription.TABLE_NAME;table.write_text(json.dumps(self.table()))
            (root/prescription.HELPER_NAME).write_bytes(Path(prescription.__file__).read_bytes())
            (root/'manifest.json').write_text(json.dumps(dict(dy_pdf_runtime=dict(
                schema='shift-dy-pdf-runtime-v1', files=self.table()['pdf_inputs']))))
            original=prescription.apply_prescription(self.contract(), 'msht20-nnlo', table_path=table)
            for setting in ['PDF:useHard = off', 'PDF:pHardSet = anotherPDF', '23:m0 = 90.']:
                contract=copy.deepcopy(original);contract['process_settings'].append(setting)
                with self.subTest(setting=setting), self.assertRaises(ValueError):
                    prescription.validate_prescription(contract, root)
            (root/prescription.HELPER_NAME).write_text('# changed helper\n')
            with self.assertRaisesRegex(ValueError, 'helper is missing or changed'):
                prescription.validate_prescription(original, root)

    def test_unsupported_scope_and_unqualified_authority_fail_without_interpolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            table=Path(temporary)/'authority.json';table.write_text(json.dumps(self.table()))
            for mass,epsilon in [(1.8,.001),(2.,.01),(2.,0.),(2.,float('nan'))]:
                with self.subTest(mass=mass,epsilon=epsilon),self.assertRaises(ValueError):
                    prescription.prescription_metadata('msht20-nnlo', mass, epsilon, table)
            bad=self.contract();bad['mechanism']='senscalc-meson-fragmentation-bremsstrahlung'
            with self.assertRaisesRegex(ValueError, 'only to native SHIFT DY'):
                prescription.apply_prescription(bad, 'msht20-nnlo', table_path=table)
            for change in ['duplicate_mass','negative_factor','unqualified','wrong_PDF','epsilon_continuation']:
                value=self.table();epsilon=.001
                if change=='duplicate_mass':value['points']*=2
                elif change=='negative_factor':value['points'][0]['factor']=-1.
                elif change=='unqualified':value['points'][0]['qualified']=False
                elif change=='wrong_PDF':value['hard_pdf']['set']='anotherPDF'
                else:value['small_mixing_qualified']=False;epsilon=1.e-6
                table.write_text(json.dumps(value))
                with self.subTest(change=change),self.assertRaises(ValueError):
                    prescription.prescription_metadata('msht20-nnlo', 2., epsilon, table)

    def test_real_pdf_data_are_checked_before_setting_the_standard_search_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);value=self.table();data=root/'PDFs';data.mkdir()
            for filename in prescription.PDF_INPUT_NAMES:
                target=data/filename;target.parent.mkdir(exist_ok=True)
                target.write_text('Synthetic PDF fixture: '+filename)
                value['pdf_inputs'][filename]=prescription.digest(target)
            value['pdf_data_root']=str(data)
            table=root/'authority.json';table.write_text(json.dumps(value))
            with patch.dict(os.environ, {'LHAPDF_DATA_PATH':'/earlier/unchecked/path'}):
                proof=prescription.configure_hard_pdf('msht20-nnlo', table)
                self.assertEqual(proof['files'],value['pdf_inputs'])
                self.assertEqual(os.environ['LHAPDF_DATA_PATH'],str(data)+':/earlier/unchecked/path')
            (data/prescription.PDF_INPUT_NAMES[0]).write_text('Changed data')
            with self.assertRaisesRegex(ValueError, 'PDF input differs'):
                prescription.configure_hard_pdf('msht20-nnlo', table)


if __name__ == '__main__':
    unittest.main()
