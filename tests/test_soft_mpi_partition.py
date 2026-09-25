import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from unfiltered_qcd_contract import check, MPI_JPSI_PROCESS, MPI_QCD_PROCESS


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

    def test_campaign_defaults_to_low_qcd_bin(self):
        command = "set -a; source config/campaigns/soft_mpi_partition_2023.env; env"
        result = subprocess.run(["bash", "-c", command], cwd=ROOT,
                                env=dict(os.environ), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        self.assertEqual(values["PROCESS"], MPI_QCD_PROCESS)
        self.assertEqual(values["GEN_EVENT_CLASS"], "qcd")
        self.assertEqual((values["GEN_PTHAT_MIN"], values["GEN_PTHAT_MAX"]), ("0", "1"))


if __name__ == "__main__":
    unittest.main()
