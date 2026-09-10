from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_ir1_bounded_gdml import classify_internal_world_gaps


class SourceSpanTest(unittest.TestCase):
    def test_shell_preserves_exterior_space_but_does_not_hide_internal_gap(self):
        names = ['shell', 'world', 'source_a', 'world', 'source_b', 'world', 'shell']
        segments = [dict(logical_volume=name, start_mm=i, end_mm=i + 1.)
                    for i, name in enumerate(names)]
        scans = {'ray': segments}
        definitions = {'ray': dict(base_name='ray', is_central=True)}
        raw, internal, _ = classify_internal_world_gaps(scans, definitions, 'world', .01)
        self.assertEqual([g['start_mm'] for g in internal], [1, 3, 5])
        _, internal, _ = classify_internal_world_gaps(
            scans, definitions, 'world', .01, envelope_volumes=('shell',))
        self.assertEqual([g['start_mm'] for g in internal], [3])
        self.assertEqual(len(raw['ray']), 3)
        self.assertEqual([s['logical_volume'] for s in segments], names)

    def test_without_shell_the_original_classification_is_unchanged(self):
        scans = {'ray': [dict(logical_volume=name, start_mm=i, end_mm=i + 1.)
                        for i, name in enumerate(['world', 'a', 'world', 'b', 'world'])]}
        definitions = {'ray': dict(base_name='ray', is_central=True)}
        baseline = classify_internal_world_gaps(scans, definitions, 'world', .01)
        self.assertEqual(baseline, classify_internal_world_gaps(
            scans, definitions, 'world', .01, envelope_volumes=('absent_shell',)))
        self.assertEqual([g['start_mm'] for g in baseline[1]], [2])


if __name__ == '__main__':
    unittest.main()
