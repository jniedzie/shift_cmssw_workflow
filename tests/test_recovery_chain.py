import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_retiring_chain import stage_provenance


class RecoveryTest(unittest.TestCase):
    def test_exact_seeded_chunk_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'configs/step1').mkdir(parents=True)
            (root / 'logs').mkdir()
            for chunk in (0, 1, 3, 39, 40, 390, 399):
                part = f'{chunk:04d}'
                seed = 21000000 + chunk
                (root / 'configs/step1' / f'events_step1_part{part}_seed{seed}_cfg.py').write_text('config')
                (root / 'logs' / f'step1_events_part{part}_seed{seed}.log').write_text('log')
            for chunk in (0, 1, 3, 39, 40, 390, 399):
                matches = stage_provenance(root, 1, chunk)
                self.assertEqual(len(matches), 2)
                self.assertTrue(all(f'part{chunk:04d}_' in p.name for p in matches))

    def test_term_waits_for_child_before_cleanup(self):
        code = '''
import signal,sys
sys.path.insert(0,sys.argv[1])
from run_retiring_chain import run_command,interrupted
signal.signal(signal.SIGTERM,interrupted)
try:
    run_command([sys.executable,'-c','import time; print("ready",flush=True); time.sleep(60)'])
except InterruptedError:
    print('child stopped',flush=True)
'''
        child = subprocess.Popen([sys.executable, '-c', code, str(ROOT / 'scripts')],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            child.send_signal(signal.SIGTERM)
            out, err = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, err)
            self.assertIn('child stopped', out)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__ == '__main__':
    unittest.main()
