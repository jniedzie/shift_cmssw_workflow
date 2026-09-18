import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from audit_generation_chunk import QCD_CODES, JPSI_CODES
from collect_generation_metadata import combine


class QcdProductionTest(unittest.TestCase):
    def records(self):
        return [dict(schema='shift-production-gen-v1', chunk=i, events=10,
            process='QCD_FixedTarget_pThat_1to5GeV_13p6TeV', fragment_sha256='same',
            generated_filter_efficiency=1., forced_decay='none', sum_weights=10.,
            sum_weights_squared=10., runs=[dict(internal_xsec_pb=100.+20*i, error_pb=2.)])
            for i in range(2)]

    def test_disjoint_primary_processes(self):
        self.assertFalse(QCD_CODES & JPSI_CODES)
        self.assertIn(441, JPSI_CODES)
        self.assertNotIn(131, QCD_CODES)  # no separate three-parton sample

    def test_fragment_has_no_acceptance_or_lifetime_override(self):
        text = (ROOT/'fragments/QCD_FixedTarget_pThat_1to5GeV_13p6TeV_pythia8_cff.py').read_text()
        for forbidden in ('limitTau0 = off', 'minWidth', 'mayDecay', 'onIfMatch', 'EDFilter("PythiaFilter'):
            self.assertNotIn(forbidden, text)
        self.assertIn("'Charmonium:all = off'", text)
        self.assertIn("'SoftQCD:all = off'", text)

    def test_mu_enriched_fragment_uses_explicit_shift_corridor(self):
        text = (ROOT/'fragments/QCD_MuEnriched_FixedTarget_pThat_1to5GeV_13p6TeV_pythia8_cff.py').read_text()
        for required in ("'ParticleDecays:limitTau0 = off'",
                         "'ParticleDecays:limitCylinder = on'",
                         "'ParticleDecays:xyMax = 8000.'",
                         "'ParticleDecays:zMax = 151000.'",
                         '"MCSmartSingleParticleFilter"',
                         'ProductionFilterSequence = cms.Sequence(generator * mugenfilter)'):
            self.assertIn(required, text)
        self.assertNotIn('filterEfficiency=', text)
        self.assertNotIn('crossSection=', text)
        self.assertIn('MinP=cms.untracked.vdouble(0., 0.)', text)
        self.assertIn('MinPt=cms.untracked.vdouble(0., 0.)', text)
        self.assertIn('MinEta=cms.untracked.vdouble(-10., -10.)', text)
        self.assertIn('MaxEta=cms.untracked.vdouble(0., 0.)', text)

    def test_cross_sections_are_averaged_not_added(self):
        result = combine(self.records(), 2)
        self.assertEqual(result['cross_section_pb'], 110.)
        self.assertEqual(result['event_weight_pb_by_chunk'], {'0':5., '1':6.})

    def test_filtered_normalization_uses_attempted_denominator(self):
        records = self.records()
        for record, accepted in zip(records, (2, 4)):
            record['process'] = 'QCD_MuEnriched_FixedTarget_pThat_1to5GeV_13p6TeV'
            record['attempted_events'] = 10
            record['accepted_events'] = accepted
            record['events'] = accepted
            record['sum_weights'] = float(accepted)
            record['sum_weights_squared'] = float(accepted)
            record['generated_filter_efficiency'] = accepted/10.
        result = combine(records, 2)
        self.assertEqual(result['attempted_events'], 20)
        self.assertEqual(result['accepted_events'], 6)
        self.assertAlmostEqual(result['filter_efficiency'], .3)
        self.assertEqual(result['inclusive_cross_section_pb'], 110.)
        self.assertEqual(result['selected_cross_section_pb'], 34.)
        self.assertEqual(result['event_weight_pb_by_chunk'], {'0':5., '1':6.})

    def test_incomplete_or_duplicate_chunks_fail(self):
        for records in (self.records()[:1], [self.records()[0]]*2):
            with self.assertRaises(ValueError):
                combine(records, 2)

    def test_mixed_filtered_or_nonunit_samples_fail(self):
        for key, value in (('fragment_sha256','different'), ('forced_decay','muons'),
                           ('sum_weights',9.), ('generated_filter_efficiency',.5)):
            records = copy.deepcopy(self.records())
            records[0][key] = value
            with self.assertRaises(ValueError):
                combine(records, 2)

    def test_inconsistent_filtered_efficiency_fails(self):
        records = self.records()
        for record in records:
            record['process'] = 'QCD_MuEnriched_FixedTarget_pThat_1to5GeV_13p6TeV'
            record['attempted_events'] = 20
            record['accepted_events'] = 10
        with self.assertRaises(ValueError):
            combine(records, 2)


if __name__ == '__main__':
    unittest.main()
