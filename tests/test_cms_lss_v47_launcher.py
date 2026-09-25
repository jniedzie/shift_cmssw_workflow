"""Check the CMS comparison recipe at the submission boundary."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parents[1]


class CmsLssV47LauncherTest(unittest.TestCase):
    def fixture(self, root, unresolved=None):
        source = root / "source"
        payload = root / "payload"
        (payload / "geometry").mkdir(parents=True)
        (payload / "field_maps").mkdir()
        source.mkdir()
        (source / "lhc_IR5_2023-2024.inp").write_text(
            "#include /provider/MB.inp\nMGNFIELD 1 0 0 region 0 0 MB\n")
        (source / "MB.inp").write_text(
            "FREE\nMGNCREAT , 4.0, 5.0, 9.7, 0.0, 2.0, , MB\nFIXED\n")
        gdml = payload / "geometry/model.gdml"
        gdml.write_text("<gdml/>\n")
        maps = []
        for name in ("MQXA.dat", "MQXB.dat", "MBXW.dat", "MQYana.dat"):
            path = payload / "field_maps" / name
            path.write_text(name + "\n")
            maps.append(path)
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = root / "payload_manifest.json"
        manifest.write_text(json.dumps({
            "staging_complete": True,
            "unresolved_native_includes_not_consumed": unresolved or [],
            "files": {path.name: {"sha256": digest(path)} for path in [gdml, *maps]},
        }))
        return source, payload, gdml, manifest

    def run_launcher(self, unresolved=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            shutil.copy2(WORKFLOW / "scripts/run_cms_lss_v47_comparison.sh", root / "scripts")
            shutil.copy2(WORKFLOW / "scripts/audit_cms_lss_source.py", root / "scripts")
            source, payload, gdml, manifest = self.fixture(root, unresolved)
            boundary = root / "run_condor.sh"
            boundary.write_text('''#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json, os
keys = (
    "PROCESS", "N_JOBS", "N_EVENTS", "GENERATOR_SEED", "SIMULATION_SEED",
    "PILEUP_MODE", "SHIFT_TIMING_MODE", "SHIFT_TIMING_BEAM_DIRECTION_Z",
    "SHIFT_LSS_MATERIAL_MODE", "SHIFT_LSS_FIELD_MODE",
    "SHIFT_LSS_SYMMETRIC_TWO_SIDED", "SHIFT_LSS_FIELD_DATA_DIRECTORY",
    "SHIFT_USE_VERTEX_CONSTRAINED_REFIT",
    "SHIFT_USE_MATERIAL_AWARE_VERTEX_TRANSPORT", "SHIFT_USE_FORWARD_COMMON_VERTEX_FIT",
    "SHIFT_TARGET_DETAILED_MATERIAL", "SHIFT_TARGET_CONSISTENT_BACKWARD_COVARIANCE",
    "SHIFT_TARGET_MEAN_ENERGY_LOSS_JACOBIAN", "SHIFT_TARGET_UNQUENCHED_IONIZATION_VARIANCE",
    "SHIFT_TARGET_FIELD_GRADIENT_JACOBIAN", "SHIFT_TARGET_MOMENT_FIT",
)
print(json.dumps({key: os.environ[key] for key in keys}))
PY
''')
            boundary.chmod(0o755)
            (root / "bin").mkdir()
            checksum = root / "bin/sha256sum"
            checksum.write_text("#!/bin/sh\nexec /usr/bin/shasum -a 256 \"$@\"\n")
            checksum.chmod(0o755)
            env = dict(os.environ, LSS_TEST_WORKFLOW=str(WORKFLOW),
                       PATH=f"{root / 'bin'}:/usr/bin:/bin",
                       CMS_LSS_SOURCE_DIR=str(source), CMS_LSS_PAYLOAD_DIR=str(payload),
                       CMS_LSS_GDML_FILE=str(gdml),
                       CMS_LSS_FIELD_DATA_DIRECTORY=str(payload / "field_maps"),
                       CMS_LSS_PAYLOAD_MANIFEST=str(manifest),
                       CMSSW_SRC=str(WORKFLOW.parent / "CMSSW_17_0_0_pre4/src"))
            return subprocess.run(
                [str(root / "scripts/run_cms_lss_v47_comparison.sh"),
                 "cms_lss_v47_contract_test", "--check"],
                env=env, capture_output=True, text=True)

    def test_exact_v47_recipe_with_cms_payload(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        values = json.loads(result.stdout)
        self.assertEqual(values["PROCESS"], "Charmonium_FixedTarget_pThat_1to5GeV_13p6TeV")
        self.assertEqual((values["N_JOBS"], values["N_EVENTS"]), ("1000", "10"))
        self.assertEqual((values["GENERATOR_SEED"], values["SIMULATION_SEED"]),
                         ("13579", "24680"))
        self.assertEqual(values["PILEUP_MODE"], "none")
        self.assertEqual(values["SHIFT_TIMING_MODE"], "nominal")
        self.assertEqual(values["SHIFT_TIMING_BEAM_DIRECTION_Z"], "-1")
        self.assertEqual(values["SHIFT_LSS_MATERIAL_MODE"], "external")
        self.assertEqual(values["SHIFT_LSS_FIELD_MODE"], "cms_ir5_2023_z1100")
        self.assertEqual(values["SHIFT_LSS_SYMMETRIC_TWO_SIDED"], "true")
        self.assertTrue(values["SHIFT_LSS_FIELD_DATA_DIRECTORY"].endswith("/field_maps"))
        for key in (
                "SHIFT_USE_VERTEX_CONSTRAINED_REFIT", "SHIFT_TARGET_DETAILED_MATERIAL",
                "SHIFT_TARGET_CONSISTENT_BACKWARD_COVARIANCE",
                "SHIFT_TARGET_MEAN_ENERGY_LOSS_JACOBIAN",
                "SHIFT_TARGET_UNQUENCHED_IONIZATION_VARIANCE",
                "SHIFT_TARGET_FIELD_GRADIENT_JACOBIAN", "SHIFT_TARGET_MOMENT_FIT"):
            self.assertEqual(values[key], "1")
        self.assertEqual(values["SHIFT_USE_MATERIAL_AWARE_VERTEX_TRANSPORT"], "0")
        self.assertEqual(values["SHIFT_USE_FORWARD_COMMON_VERTEX_FIT"], "0")

    def test_incomplete_payload_is_rejected_before_submission(self):
        result = self.run_launcher(["MB.inp"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unresolved native includes", result.stderr)


if __name__ == "__main__":
    unittest.main()
