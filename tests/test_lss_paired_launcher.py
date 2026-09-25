"""Check mode isolation at the launcher/configuration boundary without Condor."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parents[1]


class LssPairedLauncherTest(unittest.TestCase):
    def resolve(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            shutil.copy2(WORKFLOW / "scripts/run_lss_paired_production.sh", root / "scripts")
            # Replace only the submission boundary: use the real configuration
            # builder, but never submit a job, build CMSSW, or touch EOS.
            boundary = root / "run_condor.sh"
            boundary.write_text('''#!/usr/bin/env bash
set -euo pipefail
source "$LSS_TEST_WORKFLOW/scripts/configure_lss.sh"
configure_shift_lss
export SHIFT_LSS_SIMULATION_PYTHON SHIFT_LSS_RECONSTRUCTION_PYTHON
python3 - <<'PY'
import json, os
print(json.dumps({key: os.environ[key] for key in (
    "SHIFT_LSS_MATERIAL_MODE", "SHIFT_LSS_FIELD_MODE",
    "SHIFT_LSS_SYMMETRIC_TWO_SIDED",
    "SHIFT_LSS_SIMULATION_PYTHON", "SHIFT_LSS_RECONSTRUCTION_PYTHON",
    "GENERATOR_SEED", "SIMULATION_SEED", "COLLISION_YEAR",
    "PILEUP_MODE", "SHIFT_TIMING_MODE", "TRIGGER_SCENARIO",
)}))
PY
''')
            boundary.chmod(0o755)
            # Even hostile inherited mode values must not leak across runs.
            env = dict(os.environ, LSS_TEST_WORKFLOW=str(WORKFLOW),
                       CMSSW_SRC=str(WORKFLOW.parent / "CMSSW_17_0_0_pre4/src"),
                       SHIFT_LSS_MATERIAL_MODE="external",
                       SHIFT_LSS_FIELD_MODE="ir1_atlas_proxy",
                       SHIFT_LSS_SYMMETRIC_TWO_SIDED="true")
            result = subprocess.run(
                [str(root / "scripts/run_lss_paired_production.sh"), mode,
                 "contract_test_no_submission", "--steps", "4"],
                env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)

    def test_all_four_modes_keep_simulation_and_reconstruction_consistent(self):
        for mode, material, field in (
                ("control", "none", "none"),
                ("material", "external", "none"),
                ("field", "none", "ir1_atlas_proxy"),
                ("combined", "external", "ir1_atlas_proxy")):
            with self.subTest(mode=mode):
                resolved = self.resolve(mode)
                self.assertEqual(resolved["SHIFT_LSS_MATERIAL_MODE"], material)
                self.assertEqual(resolved["SHIFT_LSS_FIELD_MODE"], field)
                self.assertEqual(resolved["SHIFT_LSS_SYMMETRIC_TWO_SIDED"], "False")
                for stage in ("SIMULATION", "RECONSTRUCTION"):
                    source = resolved[f"SHIFT_LSS_{stage}_PYTHON"]
                    self.assertEqual("customiseShiftLssExternalGeometry" in source,
                                     material == "external")
                    self.assertEqual("shiftLssIr1AtlasProxyFieldElements" in source,
                                     field != "none")
                self.assertEqual(resolved["GENERATOR_SEED"], "13579")
                self.assertEqual(resolved["SIMULATION_SEED"], "24680")
                self.assertEqual(resolved["COLLISION_YEAR"], "2023")
                self.assertEqual(resolved["PILEUP_MODE"], "none")
                self.assertEqual(resolved["SHIFT_TIMING_MODE"], "nominal")
                self.assertEqual(resolved["TRIGGER_SCENARIO"], "none")


if __name__ == "__main__":
    unittest.main()
