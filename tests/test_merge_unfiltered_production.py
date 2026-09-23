import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from merge_unfiltered_production import validate_rows, publish

class MergeTest(unittest.TestCase):
    def setUp(self):
        self.rows = {(2,1,1):(0,0,0), (2,1,3):(2,1,1)}
        self.gen = dict(event_ids=[[2,1,1],[2,1,3]], events=2, attempted_events=2,
                        framework_requested_events=3, generated_filter_efficiency=1)

    def test_actual_generated_count_not_requested_slots(self):
        validate_rows(self.rows, self.gen, {})

    def test_duplicate_across_chunks_rejected(self):
        with self.assertRaises(ValueError):
            validate_rows(self.rows, self.gen, {(2,1,3):(0,0,0)})

    def test_exact_id_and_denominator_checks(self):
        for key, value in [('event_ids',[[2,1,1],[2,1,2]]),('events',3),
                           ('attempted_events',3),('generated_filter_efficiency',0.9)]:
            bad = copy.deepcopy(self.gen)
            bad[key] = value
            with self.assertRaises(ValueError):
                validate_rows(self.rows,bad,{})

    def test_publication_is_exact_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, dest = Path(tmp)/'source', Path(tmp)/'dest'
            source.write_bytes(b'payload')
            publish(source,dest)
            self.assertEqual(dest.read_bytes(),b'payload')
            with self.assertRaises(ValueError):
                publish(source,dest)

if __name__ == '__main__':
    unittest.main()
