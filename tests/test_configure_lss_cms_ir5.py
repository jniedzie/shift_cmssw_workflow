"""Check explicit CMS IR5 field-mode selection without running CMSSW."""

import json
import os
from pathlib import Path
import subprocess
import unittest


WORKFLOW = Path(__file__).resolve().parents[1]


class ConfigureLssCmsIr5Test(unittest.TestCase):
    def test_cms_ir5_field_factory_is_used_in_both_stages(self):
        command = r"""
source scripts/configure_lss.sh
configure_shift_lss
python3 - <<'PY'
import json, os
print(json.dumps({name: os.environ[name] for name in (
    'SHIFT_LSS_FIELD_MODE',
    'SHIFT_LSS_SIMULATION_PYTHON',
    'SHIFT_LSS_RECONSTRUCTION_PYTHON',
    'SHIFT_LSS_CONTRACT_SHA256',
)}))
PY
"""
        env = dict(
            os.environ,
            SHIFT_LSS_MATERIAL_MODE="none",
            SHIFT_LSS_FIELD_MODE="cms_ir5_2023_z1100",
            SHIFT_LSS_FIELD_SCALE="1.0",
            SHIFT_LSS_MODEL_ORIGIN_CM="0,0,0",
            SHIFT_LSS_MODEL_TO_CMS="1,0,0,0,1,0,0,0,1",
        )
        result = subprocess.run(
            ["bash", "-c", command], cwd=WORKFLOW, env=env,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        resolved = json.loads(result.stdout)
        self.assertEqual(resolved["SHIFT_LSS_FIELD_MODE"], "cms_ir5_2023_z1100")
        for stage in ("SIMULATION", "RECONSTRUCTION"):
            source = resolved[f"SHIFT_LSS_{stage}_PYTHON"]
            self.assertIn("shiftLssCmsIr5_2023Z1100FieldElements", source)
            self.assertNotIn("shiftLssIr1AtlasProxyFieldElements", source)
        self.assertRegex(resolved["SHIFT_LSS_CONTRACT_SHA256"], r"^[0-9a-f]{64}$")

    def test_cms_ir5_field_requires_explicit_scale(self):
        command = "source scripts/configure_lss.sh; configure_shift_lss"
        env = dict(
            os.environ,
            SHIFT_LSS_MATERIAL_MODE="none",
            SHIFT_LSS_FIELD_MODE="cms_ir5_2023_z1100",
            SHIFT_LSS_MODEL_ORIGIN_CM="0,0,0",
            SHIFT_LSS_MODEL_TO_CMS="1,0,0,0,1,0,0,0,1",
        )
        env.pop("SHIFT_LSS_FIELD_SCALE", None)
        result = subprocess.run(
            ["bash", "-c", command], cwd=WORKFLOW, env=env,
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHIFT_LSS_FIELD_SCALE is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
