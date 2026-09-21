from pathlib import Path
import subprocess
import tempfile
import unittest

UPDATER = Path(__file__).resolve().parents[1] / 'scripts/update_cross_section.sh'


class CrossSectionExportTest(unittest.TestCase):
    def test_exports_pb_and_preserves_existing_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            log, output = Path(directory) / 'step1.log', Path(directory) / 'cross_sections.txt'
            log.write_text('Before Filter: total cross section = 6.178e+05 +- 4.649e+03 pb\n'
                           'After filter: final cross section = 6.178e+05 +- 4.649e+03 pb\n')
            command = ['bash', str(UPDATER), str(log), str(output), 'jpsi_pThat_1to2']
            subprocess.run(command, check=True)
            expected = output.read_text()
            self.assertIn('jpsi_pThat_1to2 before_filter=6.178e+05 +- 4.649e+03 pb', expected)
            self.assertIn('after_filter=6.178e+05 +- 4.649e+03 pb', expected)
            log.write_text('Incomplete later job\n')
            subprocess.run(command, check=True)
            self.assertEqual(output.read_text(), expected)

    def test_missing_summary_does_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            log, output = Path(directory) / 'step1.log', Path(directory) / 'cross_sections.txt'
            log.write_text('Incomplete job\n')
            result = subprocess.run(['bash', str(UPDATER), str(log), str(output), 'jpsi'],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
