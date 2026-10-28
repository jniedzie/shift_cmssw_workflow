# Code style: AI-code. AI-generated checks of exact transport-response links.
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_shift_transport_response import parse_logger,summarize_selected


class TransportResponseTest(unittest.TestCase):
    def native(self):
        return dict(metadata={},events=[dict(event_id=[1,1,7],GEN_muons={},GEN_parents=[],GEN_children={8:[9]},
            tracks=[dict(trackId=41,pdg=211,GEN_barcode=8,vertex=0,momentum=[0.,0.,100.,100.]),
                    dict(trackId=902,pdg=-13,GEN_barcode=9,vertex=1,momentum=[0.,0.,50.,50.])],
            vertices=[dict(vertex=110,parent=-1,position=[0.,0.,0.,0.],process=0),
                      dict(vertex=111,parent=41,position=[0.,0.,10.,0.],process=201)],
            tracking_hit_collections=dict(MuonCSCHits=[dict(track=902,csc_endcap=1,csc_station=2),
                dict(track=41,csc_endcap=2,csc_station=4),dict(track=12345,csc_endcap=2,csc_station=3)]))])

    def logs(self):return dict(decays=[],terminals=[],delegates=[],errors=[])

    def test_parent_is_G4_ID_and_vertex_is_vector_offset(self):
        result=summarize_selected(self.native(),self.logs(),[9],[8])
        track=result['selected_muons'][0]['saved_tracks'][0]
        self.assertEqual(track['birth_vertex']['vertex'],111)
        self.assertEqual(track['saved_parent_chain'],[dict(trackId=41,eventId=0,pdg=211,GEN_barcode=8)])
        self.assertEqual(track['missing_saved_parent_tracks'],[])

    def test_CSC_hits_and_reco_links_require_exact_track_identity(self):
        truth=dict(stored_fields=dict(ShiftMuon_hitSimTrackId=[902.,41.,-1.]))
        result=summarize_selected(self.native(),self.logs(),[9],[8],reco_truth=truth)
        track=result['selected_muons'][0]['saved_tracks'][0]
        self.assertEqual(track['CSC_hits'],1)
        self.assertEqual(track['CSC_station_hits'],dict(plus={2:1},minus={}))
        self.assertEqual(track['reco_muon_indices'],[0])
        self.assertEqual(result['native_CSC_hit_tracks_missing_from_saved_tracks'],[dict(eventId=0,trackId=12345)])

    def test_absent_tracks_or_logs_remain_unknown(self):
        result=summarize_selected(self.native(),self.logs(),[999],[998])
        self.assertFalse(result['selected_muons'][0]['actual_birth_observed'])
        self.assertTrue(result['selected_muons'][0]['missing_saved_track_is_unknown'])
        self.assertEqual(result['selected_parents'][0]['observed_fate'],'unknown')
        self.assertIsNone(result['observed_decays_pass_diagnostic_conservation'])

    def test_passive_exact_ID_bridges_an_unknown_native_barcode(self):
        native=self.native();native['events'][0]['tracks'][1]['GEN_barcode']=-1
        logs=self.logs();logs['terminals']=[dict(g4_event=7,barcode=9,trackId=902,pdg=-13,step=1)]
        truth=dict(stored_fields=dict(ShiftMuon_hitSimTrackId=[902.]))
        muon=summarize_selected(native,logs,[9],[8],reco_truth=truth)['selected_muons'][0]
        self.assertEqual(len(muon['saved_tracks']),1)
        track=muon['saved_tracks'][0]
        self.assertEqual(track['native_GEN_barcode'],-1)
        self.assertEqual(track['association_evidence'][0]['kind'],'passive_barcode_and_exact_G4_track_ID')
        self.assertEqual(track['CSC_hits'],1)
        self.assertEqual(track['reco_muon_indices'],[0])
        self.assertEqual(muon['native_track_association_ambiguities'],[])

    def test_repeated_ID_in_different_native_events_is_not_guessed(self):
        native=self.native();event=native['events'][0];event['tracks'][1]['GEN_barcode']=-1
        event['tracks'].append(dict(event['tracks'][1],eventId=7))
        logs=self.logs();logs['terminals']=[dict(g4_event=7,barcode=9,trackId=902,pdg=-13,step=1)]
        muon=summarize_selected(native,logs,[9],[8])['selected_muons'][0]
        self.assertEqual(muon['saved_tracks'],[])
        self.assertEqual(len(muon['native_track_association_ambiguities'][0]['native_candidates']),2)
        # An explicitly recorded native event ID can establish the exact link;
        # G4 event number alone is deliberately not used as that native ID.
        logs['terminals'][0]['eventId']=7
        muon=summarize_selected(native,logs,[9],[8])['selected_muons'][0]
        self.assertEqual(muon['saved_tracks'][0]['eventId'],7)
        self.assertTrue(muon['saved_tracks'][0]['reco_track_ID_is_ambiguous'])

    def test_known_stop_does_not_invent_a_decay(self):
        logs=self.logs();logs['terminals']=[dict(g4_event=7,barcode=8,trackId=41,parentId=0,pdg=211,
            step=10,process_subtype=121,status_before_original_action=2,
            assigned_proper_time_ns=10.,proper_time_ns=1.)]
        native=self.native();native['events'][0]['tracks']=native['events'][0]['tracks'][:1]
        native['events'][0]['tracking_hit_collections']['MuonCSCHits']=[]
        result=summarize_selected(native,logs,[9],[8])
        parent=result['selected_parents'][0]
        self.assertEqual(parent['observed_fate'],'inelastic_termination_observed')
        self.assertEqual(parent['actual_decays'],[])
        self.assertEqual(parent['stopped_parent_with_observed_decay'],[])
        self.assertEqual(result['selected_muons'][0]['actual_parent_decay_records'],[])
        self.assertEqual(parent['selected_descendant_births_despite_early_termination'],[])

    def test_a_saved_descendant_of_a_stopped_parent_is_reported_for_review(self):
        native=self.native();native['events'][0]['vertices'][1]['parent']=-1
        logs=self.logs();logs['terminals']=[dict(g4_event=7,barcode=8,trackId=41,parentId=0,pdg=211,
            step=10,process_subtype=121,status_before_original_action=2,
            assigned_proper_time_ns=10.,proper_time_ns=1.)]
        parent=summarize_selected(native,logs,[9],[8])['selected_parents'][0]
        self.assertEqual(parent['GEN_selected_descendant_muon_barcodes'],[9])
        self.assertEqual(parent['selected_descendants_injected_as_roots_despite_early_termination'],
                         [dict(barcode=9,trackId=902)])
        self.assertEqual(parent['selected_descendant_births_despite_early_termination'][0]['barcode'],9)

    def test_complete_rows_and_delegate_paths_are_preserved(self):
        row=dict(g4_event=7,barcode=8,trackId=41,parent_entry_p4_GeV=[0.,0.,1.,2.],
                 children=[dict(barcode=9,pdg=13,p4_GeV=[0.,0.,1.,2.])])
        log='[SHIFTParentLoggerDelegate] native_symbol=/frozen/native.so\n[SHIFTParentDecay] '+json.dumps(row)
        parsed=parse_logger(log)
        self.assertEqual(parsed['decays'],[row])
        self.assertEqual(parsed['delegates'],[dict(symbol='native_symbol',path='/frozen/native.so')])
        result=summarize_selected(self.native(),parsed,[9],[8])
        self.assertTrue(result['observed_decays_pass_diagnostic_conservation'])
        self.assertEqual(result['decay_conservation'][0]['residual_GeV'],0.)
        with self.assertRaises(json.JSONDecodeError):parse_logger('[SHIFTParentDecay] {truncated')

    def test_G4_event_is_the_native_EDM_number_not_zero_based_position(self):
        native=self.native();native['events'][0]['event_id']=[600500001,1,859]
        logs=self.logs();logs['terminals']=[dict(g4_event=859,barcode=9,trackId=902,pdg=-13,step=1)]
        result=summarize_selected(native,logs,[9],[8],expected_event_id=[600500001,1,859],
                                  reco_labels=[dict(event_id=[600500001,1,859])])
        self.assertEqual(result['event_identity_binding']['expected_g4_event_id'],859)
        logs['terminals'][0]['g4_event']=0
        with self.assertRaisesRegex(ValueError,'another original'):
            summarize_selected(native,logs,[9],[8])

    def test_signed_G4_conversion_preserves_full_ID_protection(self):
        native=self.native();native['events'][0]['event_id']=[600500001,2,2**32+7]
        logs=self.logs();logs['terminals']=[dict(g4_event=7,barcode=9,trackId=902,pdg=-13,step=1)]
        expected=list(native['events'][0]['event_id'])
        result=summarize_selected(native,logs,[9],[8],expected_event_id=expected)
        self.assertEqual(result['event_id'],expected)
        with self.assertRaisesRegex(ValueError,'complete event identity'):
            summarize_selected(native,logs,[9],[8],expected_event_id=[600500001,2,7])
        with self.assertRaisesRegex(ValueError,'complete original identity'):
            summarize_selected(native,logs,[9],[8],reco_labels=dict(event_id=[600500001,1,2**32+7]))
        native['events'][0]['event_id'][2]=2**31+7
        logs['terminals'][0]['g4_event']=-2**31+7
        result=summarize_selected(native,logs,[9],[8])
        self.assertEqual(result['event_identity_binding']['expected_g4_event_id'],-2**31+7)


if __name__=='__main__':unittest.main()
