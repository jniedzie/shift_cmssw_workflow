import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from unfiltered_qcd_contract import check, MPI_JPSI_PROCESS, MPI_QCD_PROCESS
from soft_mpi_model import model_settings_digest, process_statistics
from collect_generation_metadata import combine
import tempfile


class SoftMpiPartitionTest(unittest.TestCase):
    def env(self, process=MPI_QCD_PROCESS, event_class="qcd", lower="0", upper="1"):
        return dict(
            PROCESS=process,
            GEN_EVENT_CLASS=event_class,
            GEN_PTHAT_MIN=lower,
            GEN_PTHAT_MAX=upper,
            CLEANUP_PREVIOUS_STEP="0",
            WORKFLOW_LOCAL_GENERATOR="1",
            N_EVENTS="20",
            N_JOBS="1",
            GEN_EVENT_RUN_OFFSET=str((6*int(event_class == "direct_jpsi") +
                                      ((0., 1.), (1., 2.), (2., 5.), (5., 10.),
                                       (10., 20.), (20., -1.)).index((float(lower), float(upper))))*100000)
            if (float(lower), float(upper)) in ((0., 1.), (1., 2.), (2., 5.),
                                               (5., 10.), (10., 20.), (20., -1.)) else "0",
        )

    def test_complementary_classes_accept_all_declared_bins(self):
        for process, event_class in ((MPI_QCD_PROCESS, "qcd"),
                                     (MPI_JPSI_PROCESS, "direct_jpsi")):
            for lower, upper in (("0", "1"), ("1", "2"), ("2", "5"),
                                 ("5", "10"), ("10", "20"), ("20", "-1")):
                self.assertTrue(check(self.env(process, event_class, lower, upper)))

    def test_class_or_bin_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            check(self.env(MPI_QCD_PROCESS, "direct_jpsi"))
        with self.assertRaises(ValueError):
            check(self.env(MPI_JPSI_PROCESS, "qcd"))
        with self.assertRaises(ValueError):
            check(self.env(lower="0.5", upper="1"))

    def test_fragments_share_one_soft_qcd_model(self):
        qcd = (ROOT / "fragments" /
               "QCD_SoftMpiPartition_FixedTarget_13p6TeV_pythia8_cff.py").read_text()
        jpsi = (ROOT / "fragments" /
                 "Charmonium_SoftMpiPartition_FixedTarget_13p6TeV_pythia8_cff.py").read_text()
        for text in (qcd, jpsi):
            self.assertIn('"SoftQCD:nonDiffractive = on"', text)
            self.assertIn('"MultipartonInteractions:processLevel = 3"', text)
            self.assertIn('pluginName=cms.string("ShiftMpiEventClassHook")', text)
            self.assertNotIn("PhaseSpace:pTHatMin", text)
            self.assertNotIn("ParticleDecays:limitTau0", text)
        self.assertIn('eventClass=cms.string("qcd")', qcd)
        self.assertIn('eventClass=cms.string("direct_jpsi")', jpsi)
        self.assertNotIn("443:onIfMatch", qcd)
        self.assertIn('"443:onIfMatch = 13 -13"', jpsi)

    def test_hook_is_complementary_and_half_open(self):
        hook = (ROOT / "Configuration" / "GenProduction" / "plugins" /
                "ShiftMpiEventClassHook.cc").read_text()
        self.assertIn('eventClass_ == "direct_jpsi" ? hasDirectJpsi : !hasDirectJpsi', hook)
        self.assertIn("scale >= pTHatMin_", hook)
        self.assertIn("scale < pTHatMax_", hook)
        for particle_id in (443, 9940003, 9941003, 9942003):
            self.assertIn(str(particle_id), hook)

    def test_model_hash_is_identical_across_complementary_fragments(self):
        qcd = ROOT / 'fragments/QCD_SoftMpiPartition_FixedTarget_13p6TeV_pythia8_cff.py'
        jpsi = ROOT / 'fragments/Charmonium_SoftMpiPartition_FixedTarget_13p6TeV_pythia8_cff.py'
        self.assertEqual(model_settings_digest(qcd, 'qcd'),
                         model_settings_digest(jpsi, 'direct_jpsi'))

    def test_parser_uses_final_pythia_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'cmsRun.log'
            log.write_text(' | non-diffractive 101 | 10 10 2 | 1.000e-03 7.071e-04 |\n'
                           ' | non-diffractive 101 | 30 30 2 | 3.333e-04 2.357e-04 |\n')
            stats = process_statistics(log)
            self.assertEqual((stats['tried'], stats['accepted']), (30, 2))
            self.assertAlmostEqual(stats['sigma_pb'], 333300.)

    def test_chunk_combiner_weights_by_pythia_trials(self):
        process = MPI_QCD_PROCESS
        records = []
        for chunk, trials, xsec in ((0, 10, 1.), (1, 20, .5)):
            records.append(dict(schema='shift-production-gen-v1', chunk=chunk, events=10,
                process=process, fragment_sha256='same', event_class='qcd',
                mpi_model_settings_sha256='a'*64, configured_pthat_bounds=[0., 1.],
                edm_run_offset=0,
                generated_filter_efficiency=1., forced_decay='none', sum_weights=10.,
                sum_weights_squared=10.,
                pythia_process_statistics=dict(tried=trials, selected=trials,
                                               accepted=10, sigma_pb=xsec),
                runs=[dict(internal_xsec_pb=xsec, error_pb=.1*xsec)]))
        report = combine(records, 2)
        self.assertAlmostEqual(report['cross_section_pb'], 2./3.)
        self.assertAlmostEqual(report['inclusive_cross_section_pb'], 1.)
        self.assertEqual(report['pythia_trials'], 30)
        self.assertAlmostEqual(sum(10*w for w in report['event_weight_pb_by_chunk'].values()),
                               report['cross_section_pb'])

    def test_collector_rejects_wrong_run_namespace(self):
        record = dict(schema='shift-production-gen-v1', chunk=0, events=10,
            process=MPI_QCD_PROCESS, fragment_sha256='same', event_class='qcd',
            mpi_model_settings_sha256='a'*64, configured_pthat_bounds=[0., 1.],
            edm_run_offset=100000, generated_filter_efficiency=1.,
            forced_decay='none', sum_weights=10., sum_weights_squared=10.,
            pythia_process_statistics=dict(tried=10, selected=10,
                                           accepted=10, sigma_pb=1.),
            runs=[dict(internal_xsec_pb=1., error_pb=.1)])
        with self.assertRaisesRegex(ValueError, 'EDM run namespace'):
            combine([record], 1)

    def test_campaign_run_offsets_are_disjoint(self):
        offsets = set()
        for sample in ('qcd', 'jpsi'):
            for bin_name in ('0to1', '1to2', '2to5', '5to10', '10to20', '20to-1'):
                command = f'set -a; MPI_PARTITION_SAMPLE={sample}; MPI_PARTITION_BIN={bin_name}; source config/campaigns/soft_mpi_gen_only_2023.env; env'
                result = subprocess.run(['bash', '-c', command], cwd=ROOT,
                                        env=dict(os.environ), capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
                offset = int(values['GEN_EVENT_RUN_OFFSET'])
                self.assertNotIn(offset, offsets)
                offsets.add(offset)
                self.assertTrue(check(values))
        self.assertEqual(offsets, set(range(0, 1200000, 100000)))

    def test_campaign_defaults_to_low_qcd_bin(self):
        command = "set -a; source config/campaigns/soft_mpi_partition_2023.env; env"
        result = subprocess.run(["bash", "-c", command], cwd=ROOT,
                                env=dict(os.environ), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        self.assertEqual(values["PROCESS"], MPI_QCD_PROCESS)
        self.assertEqual(values["GEN_EVENT_CLASS"], "qcd")
        self.assertEqual((values["GEN_PTHAT_MIN"], values["GEN_PTHAT_MAX"]), ("0", "1"))
        self.assertEqual(values["GEN_EVENT_RUN_OFFSET"], "0")


if __name__ == "__main__":
    unittest.main()
