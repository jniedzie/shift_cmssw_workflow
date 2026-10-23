# Code style: AI-code. AI-generated regressions for reviewed pair collection.
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from summarize_shift_material_response import collect_paired


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PairCollectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.job = self.base/'verifiers/control'
        self.pair = dict(id='control', event_id=[1, 1, 7])
        self.plan = dict(cohort_id='control-cohort', pairs=[self.pair], samples=[{}],
                         publication_directory='/synthetic/paired',
                         full_source_denominators={'qcd': {'count': 500}})
        save(self.base/'plan.json', self.plan)
        save(self.job/'request.json', dict(argv=['python3', 'compare_paired.py', str(self.base), 'control'],
                                          cohort_id='control-cohort'))
        self.pin = dict(path=str(self.job/'request.json'), sha256=sha(self.job/'request.json'))
        archive = self.base/'frozen/frozen_tools.tar.gz'
        archive.parent.mkdir()
        archive.write_bytes(b'original frozen tools')
        (self.base/'production.dag').write_text('VARS control RequestSHA="'+self.pin['sha256']
            +'" ToolsSHA="'+sha(archive)+'"\n')
        save(self.base/'preparation/request_binding.json',
             dict(plan_sha256=sha(self.base/'plan.json'), requests={'control': self.pin},
                  archive_sha256=sha(archive)))
        save(self.base/'preparation/root_review.json',
             dict(request_binding_sha256=sha(self.base/'preparation/request_binding.json'),
                  archive_sha256=sha(archive)))
        self.summary = self.base/'pair_summaries/control.json'
        unit = {key: value for key, value in dict(source_id='qcd',
                original_source_sha256='a'*64, prepared_input_GEN_sha256='b'*64,
                timed_GEN_graph_sha256={'1:1:7': 'c'*64}, full_source_metadata_sha256='d'*64,
                weight_audit={'Double_weights_exact': True}).items()}
        save(self.summary, dict(complete=True, pair=self.pair,
                               units={'legacy': unit, 'complete': copy.deepcopy(unit)}))
        (self.job/'return_payload.tar.gz').write_bytes(b'preserved archive')
        save(self.job/'status.json', dict(complete=True, exit_code=0,
             return_payload_sha256=sha(self.job/'return_payload.tar.gz')))
        self.bind_summary()
        (self.job/'events.log').write_text('000 (123.000.000) submitted\n...\n'
            '001 (123.000.000) executed\n...\n005 (123.000.000) terminated\n'
            'Normal termination (return value 0)\n...\n')

    def bind_summary(self):
        save(self.job/'result.json', dict(complete=True, exit_code=0, selector='control',
             summary_sha256=sha(self.summary), published=[dict(
             url='root://eosuser.cern.ch//synthetic/paired/pairs/control/comparison.json',
             bytes=self.summary.stat().st_size, sha256=sha(self.summary))]))

    def test_old_receipt_is_bound_by_reviewed_request_and_native_execution(self):
        fetched = []
        report = collect_paired(self.base, lambda item, path: fetched.append(item))
        self.assertEqual(report['request_hashes'], {'control': self.pin['sha256']})
        self.assertEqual(report['originals'], 1)
        self.assertEqual(len(fetched), 1)
        self.assertEqual(report['source_exposure_counted_once_per_source_per_mode'],
                         self.plan['full_source_denominators'])
        self.assertTrue(report['published_pairs_independently_read_back'])
        self.assertFalse(report['physics_normalization_ready'])

    def test_changed_request_is_rejected(self):
        (self.job/'request.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'request changed'):
            collect_paired(self.base)

    def test_changed_review_inventory_is_rejected(self):
        (self.base/'preparation/request_binding.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'inventory or plan'):
            collect_paired(self.base)

    def test_failed_native_attempt_is_rejected(self):
        p = self.job/'events.log'
        p.write_text(p.read_text().replace('return value 0', 'return value 1'))
        with self.assertRaisesRegex(ValueError, 'native successful'):
            collect_paired(self.base)

    def test_changed_summary_is_rejected(self):
        self.summary.write_text(self.summary.read_text()+' ')
        with self.assertRaisesRegex(ValueError, 'summary changed'):
            collect_paired(self.base)

    def test_changed_submitted_binding_is_rejected(self):
        (self.base/'production.dag').write_text('VARS control RequestSHA="wrong"\n')
        with self.assertRaisesRegex(ValueError, 'submitted request/runtime binding'):
            collect_paired(self.base)

    def test_matching_file_hashes_do_not_hide_changed_weights(self):
        item = json.loads(self.summary.read_text())
        item['units']['complete']['weight_audit']['Double_weights_exact'] = False
        save(self.summary, item)
        self.bind_summary()
        with self.assertRaisesRegex(ValueError, 'weight or exposure differs'):
            collect_paired(self.base)

    def test_remote_readback_failure_is_preserved(self):
        def fail(item, path):
            raise ValueError('Remote payload differs')
        with self.assertRaisesRegex(ValueError, 'Remote payload differs'):
            collect_paired(self.base, fail)


if __name__ == '__main__':
    unittest.main()
