# Code style: AI-code. AI-generated isolated runtime-helper inventory checks.
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import prepare_dark_photon_grid as grid
import prepare_dark_photon_meson_production as meson
import prepare_dark_photon_meson_controller as rollover
import prepare_dark_photon_statistics as direct


class RuntimeFreezeInventory(unittest.TestCase):
    def test_new_grid_and_meson_deployments_import_only_their_actual_frozen_helpers(self):
        required=set(grid.RUNTIME_HELPER_SOURCES)|{'dark_photon_detector_runtime.py'}
        for name,inventory in [('grid',grid.SOURCES),('meson',meson.TOOLS),
                               ('direct',set(grid.SOURCES)|set(direct.OPERATIONAL_TOOLS))]:
            with self.subTest(inventory=name),tempfile.TemporaryDirectory() as temporary:
                frozen=Path(temporary)
                self.assertTrue(required<=set(inventory))
                for filename in inventory:
                    shutil.copy2(SCRIPTS/filename,frozen/filename)
                code='''import json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
import dark_photon_runtime_recovery as adapter
import dark_photon_stage_budget as budget
import prepare_dark_photon_kinematics_preflight as scan
import run_dark_photon_runtime_recovery as guard
import run_dark_photon_statistics as durable
import dark_photon_detector_runtime as runtime
modules=(adapter,budget,scan,guard,durable,runtime)
assert all(Path(m.__file__).parent==root for m in modules)
assert callable(adapter.future_preparation) and callable(adapter.future_preflight)
assert callable(scan.prepare) and callable(guard.completed_attempt)
assert callable(durable.submission) and callable(runtime.validate_profile)
print(json.dumps({m.__name__:str(Path(m.__file__).relative_to(root)) for m in modules}))
'''
                result=subprocess.run(['/usr/bin/python3','-I','-c',code,str(frozen)],cwd=frozen,
                    text=True,capture_output=True,check=True)
                self.assertEqual(len(json.loads(result.stdout)),6)

    def test_rollover_updates_the_whole_runtime_helper_set_instead_of_retaining_old_imports(self):
        self.assertTrue(set(grid.RUNTIME_HELPER_SOURCES)<=set(rollover.REPLACEMENT_TOOLS))
        self.assertIn('dark_photon_detector_runtime.py',rollover.REPLACEMENT_TOOLS)
        self.assertTrue(set(grid.RUNTIME_HELPER_SOURCES)<=set(direct.OPERATIONAL_TOOLS))


if __name__=='__main__':unittest.main()
