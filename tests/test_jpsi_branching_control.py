# Code style: AI-code. AI-generated distinct-parent and fixed-exposure controls.
import copy
import math
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from jpsi_branching_control import channels, comparison, rate, summaries, source_slots, exact_replay_frames


def particle(code,pdg,status,mothers=(),daughters=()):
    return dict(barcode=code,pdg=pdg,status=status,mothers=list(mothers),daughters=list(daughters),
                p4_GeV=[0.,0.,1.,math.sqrt(1.+3.0969**2)])


def event(muonic=True):
    return [particle(1,9940003,33,(),(2,)),particle(2,443,2,(1,),(3,)),
        particle(3,443,2,(2,),(4,5)),particle(4,13 if muonic else 11,1,(3,)),
        particle(5,-13 if muonic else -11,1,(3,))]


class Parents(unittest.TestCase):
    def test_copies_and_FSR_count_one_parent(self):
        rows=event();rows[3]['daughters']=[6,7]
        rows += [particle(6,13,1,(4,)),particle(7,22,1,(4,))]
        out=channels(rows)
        self.assertEqual((out['terminal443'],out['muonic443'],out['direct_muonic443']),(1,1,1))
        self.assertEqual(out['parents'][0]['direct_muon_copy_barcodes'],[[4,6],[5]])

    def test_multiple_and_nonprompt_parents_do_not_share_one_BR(self):
        rows=event(False)+[particle(10,511,2,(),(11,)),particle(11,443,2,(10,),(12,13)),
                          particle(12,13,1,(11,)),particle(13,-13,1,(11,))]
        out=channels(rows)
        self.assertEqual((out['terminal443'],out['muonic443'],out['direct_muonic443']),(2,1,0))
        self.assertTrue(out['any_muonic443']);self.assertFalse(out['any_direct_muonic443'])

    def test_natural_nonmuonic_source_stays_in_exposure(self):
        out=channels(event(False));self.assertEqual(out['direct_terminal443'],1)
        self.assertFalse(out['any_muonic443'])

    def test_missing_direct_ancestry_rejected(self):
        rows=event();rows[0]['pdg']=511
        with self.assertRaisesRegex(ValueError,'no identifiable'):channels(rows)

    def test_fork_and_missing_decay_rejected(self):
        rows=event();rows[1]['daughters'].append(8);rows.append(particle(8,443,2,(2,),(4,5)))
        with self.assertRaisesRegex(ValueError,'forked'):channels(rows)
        rows=event();rows[2]['daughters']=[]
        with self.assertRaisesRegex(ValueError,'no recorded decay'):channels(rows)

    def test_tau_muons_are_not_primary_muonic443(self):
        rows=event();rows[3]['pdg']=15;rows[4]['pdg']=-15
        rows[3]['daughters']=[6];rows.append(particle(6,13,1,(4,)))
        self.assertEqual(channels(rows)['muonic443'],0)


class FixedTrials(unittest.TestCase):
    def test_rejected_slots_zero_not_saved_denominator(self):
        out=rate([2.,4.],10,100.)
        self.assertEqual(out['rate_pb'],60.)
        self.assertAlmostEqual(out['fixed_trial_standard_error_pb'],100*math.sqrt((20-36/10)/(10*9)))

    def test_full_slot_inventory_retains_missing_originals_as_zero(self):
        rows=[dict(event_id=[7,1,2],native_weight=3.)]
        out=source_slots(rows,dict(run=7,requested_trials=4))
        self.assertEqual([r['event_id'] for r in out],[[7,1,i] for i in range(1,5)])
        self.assertEqual([r['selected_source_estimator_weight'] for r in out],[0.,3.,0.,0.])
        self.assertEqual([r['saved_selected_source'] for r in out],[False,True,False,False])
        with self.assertRaises(ValueError):source_slots(rows+rows,dict(run=7,requested_trials=4))
        with self.assertRaises(ValueError):source_slots(rows,dict(run=8,requested_trials=4))

    def test_weight_and_parent_multiplicity_both_enter(self):
        rows=[dict(native_weight=2.,**channels(event()))]
        ledger=dict(requested_trials=20000,sigma_nd_mb=1e-9)
        out=summaries(rows,ledger)
        self.assertEqual(out['source_class']['rate_pb'],.0001)
        self.assertEqual(out['muonic443']['rate_pb'],.0001)

    def test_zero_is_unknown_rate(self):
        self.assertTrue(rate([],20000,1.)['zero_observation_is_not_zero_physical_rate'])
        with self.assertRaises(ValueError):rate([float('nan')],10,1.)
        with self.assertRaises(ValueError):rate([1.]*11,10,1.)

    def test_comparison_keeps_arms_separate_and_no_factor(self):
        a=dict(complete=True,arm='forced',plan_sha256='x',fixed_source_trials=20000,
               source_model_sha256='a',ledger=dict(sigma_nd_mb=1.),statistics={'source_class':rate([1.],20000,1.)})
        b=copy.deepcopy(a);b['arm']='natural'
        proof=dict(complete=True,full_HepMC_native_binary64_values_and_serialization_exact=True,
            GenEventInfo_IDs_native_weights_and_order_exact=True,full_Run_Lumi_native_ledger_exact=True,
            exposure_reused_not_additive=True,source_trials=20000)
        with self.assertRaisesRegex(ValueError,'must gate'):comparison(a,b,{})
        out=comparison(a,b,proof)
        self.assertEqual(out['comparisons']['source_class']['natural_over_forced'],1.)
        self.assertFalse(out['correction_applied']);self.assertFalse(out['normalization_ready'])
        b['fixed_source_trials']=19999
        with self.assertRaises(ValueError):comparison(a,b,proof)


class CanonicalReplay(unittest.TestCase):
    def test_complete_graph_info_weight_and_ID_stream(self):
        a=[dict(event_id=[7,1,2],graphs={'raw':'a','timed':'b'},info={'weights':[.25]})]
        self.assertEqual(exact_replay_frames(iter(a),iter(copy.deepcopy(a)))[0],1)
        for field in ['weight','graph','identity']:
            b=copy.deepcopy(a)
            if field=='weight':b[0]['info']['weights'][0]=.5
            elif field=='graph':b[0]['graphs']['raw']='changed vertex or edge'
            else:b[0]['event_id'][2]=3
            with self.assertRaisesRegex(ValueError,'differs'):exact_replay_frames(iter(a),iter(b))

    def test_missing_repeated_originals_fail(self):
        a=[dict(event_id=[7,1,2],graphs={},info={})]
        with self.assertRaisesRegex(ValueError,'count'):exact_replay_frames(a,[])
        with self.assertRaisesRegex(ValueError,'Duplicate'):exact_replay_frames(a+a,a+a)


if __name__=='__main__':unittest.main()
