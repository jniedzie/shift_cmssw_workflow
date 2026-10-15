# Code style: AI-code. AI-generated weighted muon-filter regression tests.
import ast
import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_shift_weighted_gen import _keyword, validate_config
from qcd_mu_enrichment import FILTER, SETTINGS, filtered_snapshot, ordinary_source_config
from test_shift_weighted_gen_audit import configuration, fixture


def filters(ledger, saved):
    return [dict(total_positive=ledger['accepted'], total_negative=0,
                 pass_positive=len(saved), pass_negative=0,
                 sum_weights=ledger['sumw'], sum_weights2=ledger['sumw2'],
                 sum_pass_weights=math.fsum(row['weight'] for row in saved),
                 sum_pass_weights2=math.fsum(row['weight']**2 for row in saved))]


def muon_config(ledger):
    tree = ast.parse(configuration(ledger))
    generator = tree.body[0].value
    parameters = _keyword(_keyword(generator, 'PythiaParameters'), 'processParameters')
    parameters.args.extend(ast.Constant(value=text) for text in SETTINGS)
    source = ast.unparse(tree)
    source += "\nprocess.mugenfilter = cms.EDFilter('MCSmartSingleParticleFilter',\n"
    source += ',\n'.join(name+'=cms.untracked.'+('vint32' if name in ('ParticleID','Status') else 'vdouble')+'('+','.join(map(repr, values))+')' for name,values in FILTER.items())+')\n'
    return source+'process.generation_step = cms.Path(process.ProductionFilterSequence + process.pgen + process.mugenfilter)\n'


class MuonEnrichmentTest(unittest.TestCase):
    def test_filtered_rate_keeps_all_trials_and_applies_efficiency_once(self):
        ledger, events, run = fixture()
        saved = events[:1]
        result = filtered_snapshot(ledger, copy.deepcopy(ledger), saved, run, filters(ledger,saved))
        self.assertAlmostEqual(result['sigma_weighted_mb'],65.*.2/4)
        self.assertAlmostEqual(result['weighted_filter_efficiency'],1./3)
        self.assertNotAlmostEqual(result['sigma_weighted_mb'],65.*.2/len(saved))
        self.assertNotAlmostEqual(result['sigma_weighted_mb'],65.*.2/4/3)
        self.assertEqual(result['full_source_trial_denominator'],4)

    def test_empty_saved_subset_is_a_valid_zero_contribution(self):
        ledger, events, run = fixture()
        result = filtered_snapshot(ledger,ledger,[],run,filters(ledger,[]))
        self.assertEqual((result['events'],result['sigma_weighted_mb']), (0,0.))

    def test_dropped_source_or_saved_counts_and_weights_fail(self):
        ledger, events, run = fixture()
        for key,value in [('total_positive',1),('pass_positive',2),('sum_weights',.4),
                          ('sum_weights2',0.),('sum_pass_weights',.1),('sum_pass_weights2',.01)]:
            counters=filters(ledger,events[:1]);counters[0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                filtered_snapshot(ledger,ledger,events[:1],run,counters)

    def test_saved_subset_still_checks_original_ids_and_weights(self):
        ledger, events, run = fixture()
        events[0]['event_id'][2]=5
        with self.assertRaisesRegex(ValueError,'out-of-budget'):
            filtered_snapshot(ledger,ledger,events[:1],run,filters(ledger,events[:1]))

    def test_profile_removes_only_declared_settings_before_full_model_check(self):
        ledger,events,run=fixture()
        source=muon_config(ledger)
        ordinary=ordinary_source_config(source,ledger)
        self.assertEqual(validate_config(ordinary,ledger),validate_config(configuration(ledger),ledger))
        with self.assertRaisesRegex(ValueError,'Ordered source model'):
            validate_config(ordinary_source_config(source.replace('tau0Max = 10','tau0Max = 20'),ledger),ledger)

    def test_changed_decay_cylinder_filter_or_path_fail(self):
        ledger,events,run=fixture()
        source=muon_config(ledger)
        for old,new in [('xyMax = 8000.','xyMax = 9000.'),
                        ('MinP=cms.untracked.vdouble(0.0,0.0)','MinP=cms.untracked.vdouble(5.0,5.0)'),
                        ('+ process.mugenfilter',''),
                        ('MCSmartSingleParticleFilter','MCParticlePairFilter')]:
            with self.subTest(old=old),self.assertRaises(ValueError):
                ordinary_source_config(source.replace(old,new),ledger)

    def test_profile_is_rejected_for_direct_jpsi(self):
        ledger,events,run=fixture(sample='direct_jpsi')
        with self.assertRaisesRegex(ValueError,'direct-Jpsi'):
            ordinary_source_config(muon_config(ledger),ledger)

    def test_prefilter_cross_section_still_uses_all_source_weights(self):
        ledger,events,run=fixture()
        ledger['sigma_mb']*=2
        run['internal_xsec_pb']*=2
        with self.assertRaisesRegex(ValueError,'Prefilter fixed-trial cross section'):
            filtered_snapshot(ledger,ledger,events[:1],run,filters(ledger,events[:1]))


if __name__=='__main__':
    unittest.main()
