import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from audit_soft_mpi_partition import (audit, EXPECTED_BINS, MODEL_CONTRACT,
                                      PARTITION_CONTRACT, PROCESS_CLASSES)


class SoftMpiClosureTest(unittest.TestCase):
    def records(self):
        records = []
        for process, event_class in PROCESS_CLASSES.items():
            for chunk, bounds in enumerate(EXPECTED_BINS):
                records.append(dict(schema='shift-production-gen-v1', process=process,
                    event_class=event_class, mpi_model_contract=MODEL_CONTRACT,
                    partition_contract=PARTITION_CONTRACT,
                    configured_pthat_bounds=list(bounds), chunk=chunk, events=10,
                    generated_filter_efficiency=1.,
                    runs=[dict(internal_xsec_pb=1., error_pb=.1)]))
        return records

    def test_complete_suite_and_inclusive_closure(self):
        report = audit(self.records(), inclusive_xsec_pb=12., inclusive_error_pb=.1)
        self.assertTrue(report['inclusive_closure']['passed'])
        self.assertTrue(report['normalization_ready'])
        self.assertEqual(len(report['bins']), 12)

    def test_missing_bin_fails(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete partition suite'):
            audit(self.records()[:-1])

    def test_duplicate_chunk_fails(self):
        records = self.records()
        records.append(copy.deepcopy(records[0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            audit(records)

    def test_class_or_model_mismatch_fails(self):
        for key, value in (('event_class', 'direct_jpsi'),
                           ('mpi_model_contract', 'different'),
                           ('generated_filter_efficiency', .5)):
            records = self.records()
            records[0][key] = value
            with self.assertRaises(ValueError):
                audit(records)

    def test_failed_cross_section_closure_fails(self):
        with self.assertRaisesRegex(ValueError, 'closure failed'):
            audit(self.records(), inclusive_xsec_pb=20., inclusive_error_pb=.1)


if __name__ == '__main__':
    unittest.main()
