import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_shift_weighted_gen import (ALGORITHM, CP5_SETTINGS, LEDGER_SCHEMA,
                                      validate_config, validate_ledger, validate_snapshot)


def fixture(weights=(.2, .4), sample='qcd'):
    trials = 4
    sumw, sumw2 = math.fsum(weights), math.fsum(w*w for w in weights)
    sigma = 65.*sumw/trials
    error = 65.*math.sqrt(max(0., sumw2-sumw*sumw/trials)/(trials*(trials-1)))
    ledger = dict(schema=LEDGER_SCHEMA, algorithm=ALGORITHM, complete=True,
        native_gen_lumi_is_trial_ledger=False, physics_valid=False, normalization_ready=False,
        sample=sample, lower=5., upper=10., requested_trials=trials, framework_slots=trials,
        tried=trials, proposal_calls=5, accounted_proposal_calls=5, max_proposal_calls_per_slot=2,
        intrinsic_retry_slots=1, ordinary_proposal_calls=1, ordinary_proposal_fraction=.1,
        accepted=len(weights), accumulated_accepted=len(weights), sumw=sumw, sumw2=sumw2,
        sigma_nd_mb=65., sigma_mb=sigma, sigma_error_mb=error, run=30000001, lumi=1,
        first_parent_event=1, last_parent_event=trials, initializations=1,
        unexpected_failures=0, accounting_failures=0,
        status_counts=[len(weights)]+[0]*7+[trials-len(weights)]+[0]*7)
    events = [dict(event_id=[30000001, 1, i+1], weight=w, hepmc_weight=w, process=101,
                   pthat=6., direct_scales=[7.] if sample == 'direct_jpsi' else [], charged=10)
              for i, w in enumerate(weights)]
    run = dict(internal_xsec_pb=sigma*1e9, error_pb=error*1e9, filter_efficiency=1.)
    return ledger, events, run


def configuration(ledger):
    cp5 = ', '.join(repr(key+'='+value) for key, value in CP5_SETTINGS.items())
    common = ', '.join(map(repr, ['Tune:preferLHAPDF = 2', 'Main:timesAllowErrors = 10000',
        'Check:epTolErr = 0.01', 'Beams:setProductionScalesFromLHEF = off', 'SLHA:minMassSM = 1000.',
        'ParticleDecays:limitTau0 = on', 'ParticleDecays:tau0Max = 10', 'HadronLevel:QED = on']))
    settings = ['SoftQCD:all = off', 'SoftQCD:nonDiffractive = on', 'HardQCD:all = off',
                'Charmonium:all = off', 'Bottomonium:all = off', 'MultipartonInteractions:processLevel = 3',
                'Beams:frameType = 2', 'Beams:idA = 2212', 'Beams:eA = 0.',
                'Beams:idB = 2212', 'Beams:eB = 6800.', 'Beams:allowVertexSpread = on',
                'Beams:offsetVertexZ = 148000.', 'Beams:sigmaVertexZ = 500.', 'Check:abortIfVeto = on']
    if ledger['sample'] == 'direct_jpsi':
        settings.extend(['443:onMode = off', '443:onIfMatch = 13 -13'])
    process = ', '.join(map(repr, settings))
    return f'''
process.generator = cms.EDFilter('Pythia8GeneratorFilter',
 PythiaParameters=cms.PSet(parameterSets=cms.vstring('pythia8CommonSettings', 'pythia8CP5Settings', 'processParameters'),
 pythia8CommonSettings=cms.vstring({common}),pythia8CP5Settings=cms.vstring({cp5}), processParameters=cms.vstring({process})),
 UserCustomization=cms.VPSet(cms.PSet(pluginName=cms.string('ShiftMpiWeightedProposalHook'),
 eventClass=cms.string({ledger['sample']!r}),eventRun=cms.uint32(30000001),proposalTrials=cms.uint32(4),
 pTHatMin=cms.double(5),pTHatMax=cms.double(10),statisticsFile=cms.string('proposal_ledger.json'))))
process.source = cms.Source('EmptySource',firstRun=cms.uint32(30000001),firstEvent=cms.uint64(1),
 firstLuminosityBlock=cms.uint32(1),numberEventsInRun=cms.uint32(5),numberEventsInLuminosityBlock=cms.uint32(5))
process.maxEvents = cms.PSet(input=cms.int32(4))
process.options = cms.PSet(numberOfThreads=cms.uint32(1),numberOfStreams=cms.uint32(1))
process.shiftWeightedNormalization = cms.EDProducer('ShiftWeightedGenRunInfoProducer',statisticsFile=cms.string('proposal_ledger.json'))
'''


class WeightedGenAuditTest(unittest.TestCase):
    def test_fixed_budget_uses_zero_trials_and_allows_internal_retries(self):
        ledger, events, run = fixture()
        events[-1]['event_id'][-1] = 4  # Accepted parent slots are sparse.
        result = validate_snapshot(ledger, copy.deepcopy(ledger), events, run)
        self.assertEqual(result['events'], 2)
        self.assertAlmostEqual(result['sigma_weighted_mb'], 65.*.6/4)
        self.assertNotAlmostEqual(result['sigma_weighted_mb'], 65.*.6/2)

    def test_zero_accepted_budget_preserves_zero_estimate(self):
        ledger, events, run = fixture(())
        result = validate_snapshot(ledger, ledger, events, run)
        self.assertEqual((result['events'], result['sigma_weighted_mb'], result['error_weighted_mb']), (0, 0., 0.))

    def test_tiny_tail_weight_sum_cannot_be_replaced_by_zero(self):
        ledger, events, run = fixture((1e-18, 2e-18))
        ledger['sumw'] = 0.
        with self.assertRaisesRegex(ValueError, 'sum of accepted weights'):
            validate_snapshot(ledger, ledger, events, run)

    def test_ledger_sidecar_must_equal_named_run_product(self):
        ledger, events, run = fixture()
        embedded = dict(ledger, proposal_calls=6)
        with self.assertRaisesRegex(ValueError, 'differs from JSON'):
            validate_snapshot(ledger, embedded, events, run)

    def test_all_trial_and_retry_counters_are_checked(self):
        for key, value in [('tried', 5), ('accounted_proposal_calls', 4), ('framework_slots', True),
                           ('ordinary_proposal_fraction', 0.), ('intrinsic_retry_slots', 0)]:
            ledger, _, _ = fixture()
            ledger[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_ledger(ledger)

    def test_trial_terminal_statuses_cannot_drop_rejected_slots(self):
        ledger, _, _ = fixture()
        ledger['status_counts'][8] = 0
        with self.assertRaisesRegex(ValueError, 'terminal statuses'):
            validate_ledger(ledger)

    def test_event_namespace_duplicates_and_missing_events_fail(self):
        for mutation in ('duplicate', 'wrong_run', 'outside_budget', 'missing'):
            ledger, events, run = fixture()
            if mutation == 'duplicate':
                events[1]['event_id'] = list(events[0]['event_id'])
            elif mutation == 'wrong_run':
                events[0]['event_id'][0] += 1
            elif mutation == 'outside_budget':
                events[0]['event_id'][2] = 5
            else:
                events.pop()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_snapshot(ledger, ledger, events, run)

    def test_nominal_weights_and_run_normalization_cannot_drift(self):
        for mutation in ('hepmc', 'zero_weight', 'nan_weight', 'run_xsec', 'run_error', 'filter'):
            ledger, events, run = fixture()
            if mutation == 'hepmc':
                events[0]['hepmc_weight'] *= 2
            elif mutation == 'zero_weight':
                events[0]['weight'] = 0.
            elif mutation == 'nan_weight':
                events[0]['weight'] = math.nan
            elif mutation == 'run_xsec':
                run['internal_xsec_pb'] *= 2
            elif mutation == 'run_error':
                run['error_pb'] *= 2
            else:
                run['filter_efficiency'] = .5
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_snapshot(ledger, ledger, events, run)

    def test_complementary_ownership_and_half_open_bin(self):
        for sample in ('qcd', 'direct_jpsi'):
            ledger, events, run = fixture(sample=sample)
            validate_snapshot(ledger, ledger, events, run)
            events[0]['direct_scales'] = [] if sample == 'direct_jpsi' else [7.]
            with self.assertRaisesRegex(ValueError, 'ownership'):
                validate_snapshot(ledger, ledger, events, run)
        ledger, events, run = fixture()
        events[0]['pthat'] = 10.
        with self.assertRaisesRegex(ValueError, 'half-open'):
            validate_snapshot(ledger, ledger, events, run)

    def test_resolved_source_cp5_and_hook_are_static_and_identical(self):
        ledger, _, _ = fixture()
        source = configuration(ledger)
        self.assertEqual(len(validate_config(source, ledger)), 64)
        for old, new in [('Beams:eB = 6800.', 'Beams:eB = 6500.'), ('pT0Ref=1.41', 'pT0Ref=1.42'),
                         ('proposalTrials=cms.uint32(4)', 'proposalTrials=cms.uint32(5)'),
                         ('firstEvent=cms.uint64(1)', 'firstEvent=cms.uint64(2)'),
                         ('numberOfStreams=cms.uint32(1)', 'numberOfStreams=cms.uint32(2)'),
                         ('input=cms.int32(4)', 'input=cms.int32(5)')]:
            with self.subTest(old=old), self.assertRaises(ValueError):
                validate_config(source.replace(old, new), ledger)

    def test_common_model_digest_excludes_only_class_decay_and_retry_guard(self):
        qcd, _, _ = fixture()
        jpsi, _, _ = fixture(sample='direct_jpsi')
        self.assertEqual(validate_config(configuration(qcd), qcd), validate_config(configuration(jpsi), jpsi))

    def test_complete_ordered_model_is_bound_to_preserved_reference(self):
        ledger, _, _ = fixture()
        source = configuration(ledger)
        for old, new in [('ParticleDecays:tau0Max = 10', 'ParticleDecays:tau0Max = 20'),
                         ('Beams:sigmaVertexZ = 500.', 'Beams:sigmaVertexZ = 600.'),
                         ("'Check:abortIfVeto = on'", "'TimeShower:QEDshowerByQ = off', 'Check:abortIfVeto = on'"),
                         ("'pythia8CommonSettings', 'pythia8CP5Settings'", "'pythia8CP5Settings', 'pythia8CommonSettings'")]:
            with self.subTest(old=old), self.assertRaisesRegex(ValueError, 'Ordered source model'):
                validate_config(source.replace(old, new), ledger)


if __name__ == '__main__':
    unittest.main()
