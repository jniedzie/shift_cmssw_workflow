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
                    edm_run_offset=(6*int(event_class == 'direct_jpsi') + chunk)*100000,
                    generated_filter_efficiency=1., fragment_sha256=event_class,
                    mpi_model_settings_sha256='a'*64,
                    pythia_process_statistics=dict(tried=10, selected=10, accepted=10, sigma_pb=1.),
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

    def test_overlapping_edm_run_namespace_fails(self):
        records = self.records()
        records[6]['edm_run_offset'] = 0
        with self.assertRaisesRegex(ValueError, 'EDM run namespace'):
            audit(records)

    def test_trial_weighting_for_fixed_accepted_event_chunks(self):
        records = self.records()
        second = copy.deepcopy(records[0])
        second['chunk'] = 100
        second['pythia_process_statistics'] = dict(tried=20, selected=20, accepted=10, sigma_pb=.5)
        second['runs'] = [dict(internal_xsec_pb=.5, error_pb=.05)]
        records.append(second)
        result = audit(records)
        self.assertAlmostEqual(result['bins'][0]['cross_section_pb'], 2./3.)
        self.assertEqual(result['bins'][0]['pythia_trials'], 30)

    def test_failed_cross_section_closure_fails(self):
        with self.assertRaisesRegex(ValueError, 'closure failed'):
            audit(self.records(), inclusive_xsec_pb=20., inclusive_error_pb=.1)

    def test_insufficient_bin_precision_blocks_readiness(self):
        records = self.records()
        records[0]['runs'][0]['error_pb'] = .5
        with self.assertRaisesRegex(ValueError, 'Insufficient generator statistics'):
            audit(records, inclusive_xsec_pb=12., inclusive_error_pb=.1)


if __name__ == '__main__':
    unittest.main()
