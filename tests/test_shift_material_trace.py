# Code style: AI-code. AI-generated GEN-to-material trace regression tests.
import copy
import contextlib
import gzip
import io
import json
import math
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from trace_shift_material_response import pair_kinematics, summarize, trace_event, collect_scans, sha
from shift_material_response import TARGETS
from shift_material_response import sampling_probabilities


def fixture():
    muons = [dict(barcode=i, pdg=13*sign, momentum_GeV=[sign*15., 0., -40.],
                  energy_GeV=math.sqrt(15.**2+40.**2+.105658**2), p_GeV=math.hypot(15., 40.),
                  birth_cm=[0., 0., 14000.], incoming_towards_CMS_z0=True,
                  straight_line_towards_CMS_entry=True, projected_CMS_entry_cm=[0., 0., 1100.],
                  material_to_CMS_entry={'complete': True}, transport_parent_barcodes=[10])
             for i, sign in [(1, 1), (2, -1)]]
    feature = dict(event_id=[1, 1, 1], native_weight=2., GEN_muons=2, ranking={}, muons=muons,
                   parent_flights=[dict(barcode=10, material=dict(interaction_depth_proxy=2.,
                                                               full_saved_chord_coverage=True))])
    audit = dict(event_id=[1, 1, 1], native_weight=2., actual_saved_muons=2,
                 muons_with_any_CSC_hits=2, muons_with_required_CSC_stations_each_endcap=2,
                 opposite_charge_supported_pairs=1, GEN_parents=[],
                 muons=[dict(track_id=m['barcode'], matched_GEN_muon={'barcode': m['barcode']},
                             CSC_station_hits={'plus': {'1': 6, '2': 6}, 'minus': {'1': 6, '2': 6}})
                        for m in muons])
    response = dict(event_id=[1, 1, 1], native_weight=2., source_id='source',
                    original_source_sha256='a'*64, stratum='qcd_20toinf', benchmark_probability=.2,
                    candidate_probability=.5, retained_pairs=[])
    return feature, audit, response


class TraceTest(unittest.TestCase):
    def test_census_pools_full_trials_once_and_groups_original_weights(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            sources = []
            for index, weight in enumerate([.2, .4]):
                sid = str(index)
                source_hash = str(index+1)*64
                job = directory/'jobs'/sid
                job.mkdir(parents=True)
                (job/'request.json').write_text('{}')
                flags = {target: target == 'mass_gt20' for target in TARGETS}
                pair = dict(targets=flags, incoming=index == 0)
                event = dict(event_id=[1, 1, index+1], sampling_probability=1.,
                             native_weight=weight, GEN_tail_pairs=[pair])
                selection = dict(source_GEN_sha256=source_hash, source_receipt_sha256='a'*64,
                    full_source_trials=10, sigma_nd_pb=100., counts={'originals': 10},
                    selected_events=[event])
                payload = job/'return_payload.tar.gz'
                data = gzip.compress(json.dumps(selection).encode())
                with tarfile.open(payload, 'w:gz') as archive:
                    item = tarfile.TarInfo('tail_scan/GEN_tail_selection.json.gz')
                    item.size = len(data)
                    archive.addfile(item, io.BytesIO(data))
                (job/'result.json').write_text(json.dumps(dict(complete=True, targets={
                    target: dict(tail_events=int(flags[target]),
                                 incoming_tail_events=int(flags[target] and index == 0))
                    for target in TARGETS})))
                (job/'status.json').write_text(json.dumps(dict(complete=True, return_payload_sha256=sha(payload))))
                sources.append(dict(id=sid, GEN_sha256=source_hash, source_receipt_sha256='a'*64,
                    source_receipt_path='synthetic', worker_request_sha256=sha(job/'request.json'),
                    full_source_trials=10, saved_events=10))
            (directory/'plan.json').write_text(json.dumps(dict(sources=sources,
                full_source_trials=20, saved_events=20)))
            with contextlib.redirect_stdout(io.StringIO()):
                collect_scans(directory, directory/'out')
            report = json.loads((directory/'out/summary.json').read_text())['targets']['mass_gt20']
            self.assertAlmostEqual(report['GEN_filtered_cross_section_pb'], 3.)
            self.assertAlmostEqual(report['incoming_GEN_filtered_cross_section_pb'], 1.)
            self.assertAlmostEqual(report['effective_originals'], 1.8)

    def test_tail_and_control_weights_keep_their_own_probabilities(self):
        sample = dict(event_ids=[[1, 1, 1], [1, 1, 2]], probabilities=[1., .01])
        probabilities = sampling_probabilities(sample)
        self.assertEqual([weight/p for weight, p in zip([2., 3.], probabilities)], [2., 300.])
        self.assertEqual(sampling_probabilities(dict(event_ids=sample['event_ids'], probability=.2)), [.2, .2])
        for values in ([1.], [1., 0.], [True, .01]):
            with self.assertRaises(ValueError):
                sampling_probabilities(dict(sample, probabilities=values))

    def test_uses_invariant_mass_and_vector_pair_pt(self):
        feature, _, _ = fixture()
        kin = pair_kinematics(*feature['muons'])
        self.assertAlmostEqual(kin['mass'], 2*math.hypot(15., .105658))
        self.assertEqual(kin['pt'], 0.)
        rotated = copy.deepcopy(feature['muons'])
        for muon in rotated:
            muon['momentum_GeV'] = [muon['momentum_GeV'][2], 0., muon['momentum_GeV'][0]]
        self.assertAlmostEqual(pair_kinematics(*rotated)['mass'], kin['mass'])
        self.assertEqual(pair_kinematics(*rotated)['pt'], 80.)

    def test_truth_matched_pair_stops_before_reconstruction(self):
        row = trace_event(*fixture())
        self.assertTrue(row['GEN_OS_pairs'][0]['stages']['both_muons_both_endcaps_2stations'])
        self.assertEqual(row['GEN_OS_pairs'][0]['known_parent_interaction_depth'], 2.)
        report = summarize([row])['targets']['mass_gt20']
        self.assertEqual(report['GEN_OS_events'], 1)
        self.assertEqual(report['both_muons_both_endcaps_2stations_events'], 1)
        self.assertEqual(report['reco_OS_BothBoth_tail_events'], 0)

    def test_unmatched_secondary_cannot_rescue_gen_pair(self):
        feature, audit, response = fixture()
        audit['muons'][1]['matched_GEN_muon'] = None
        row = trace_event(feature, audit, response)
        self.assertFalse(row['GEN_OS_pairs'][0]['stages']['matched_saved_G4_muons'])
        self.assertEqual(summarize([row])['totals']['any_SIM_OS_supported_pair_events'], 1)

    def test_one_endcap_is_not_both_endcaps(self):
        feature, audit, response = fixture()
        audit['muons'][0]['CSC_station_hits']['minus'] = {}
        stages = trace_event(feature, audit, response)['GEN_OS_pairs'][0]['stages']
        self.assertTrue(stages['both_muons_any_CSC'])
        self.assertFalse(stages['both_muons_both_endcaps_1station'])

    def test_multiple_pairs_do_not_multiply_event_count(self):
        feature, audit, response = fixture()
        feature['muons'].append(dict(feature['muons'][0], barcode=3))
        row = trace_event(feature, audit, response)
        report = summarize([row])['targets']['mass_gt20']
        self.assertEqual(report['GEN_pairs'], 2)
        self.assertEqual(report['GEN_OS_events'], 1)

    def test_mismatched_identity_or_weight_fails(self):
        feature, audit, response = fixture()
        response['event_id'] = [1, 1, 2]
        with self.assertRaises(ValueError):
            trace_event(feature, audit, response)
        response['event_id'] = [1, 1, 1]
        audit['native_weight'] = 3.
        with self.assertRaises(ValueError):
            trace_event(feature, audit, response)


if __name__ == '__main__':
    unittest.main()
