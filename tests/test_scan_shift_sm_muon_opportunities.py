# Code style: AI-code.
"""Focused provenance, census and descriptive-geometry regression checks."""
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from scan_shift_sm_muon_opportunities import describe_event, load_pin, parent_union_description, scan, validate_census


def geometry():
    layers = []
    for endcap, sign in ((1, 1), (2, -1)):
        for station, z in ((1, 650.), (2, 830.)):
            layers.append(dict(raw_id=len(layers)+1, endcap=endcap, station=station,
                ring=1, chamber=1, layer=1, center_cm=[250., 0., sign*z],
                axes=dict(x=[1., 0., 0.], y=[0., 1., 0.], z=[0., 0., 1.]),
                bottom_half_width_cm=100., top_half_width_cm=100.,
                half_length_cm=100., half_thickness_cm=.1))
    return dict(schema='shift-CSC-active-layer-surfaces-v1', layers=layers)


def event(number=1, weight=-.125):
    muons = []
    for barcode, pdg in ((1, 13), (2, -13)):
        muons.append(dict(barcode=barcode, pdg=pdg, birth_cm=[250., 0., 14800.],
            momentum_GeV=[0., 0., -100.], energy_GeV=math.hypot(100., .1057),
            p_GeV=100., pt_GeV=0., transport_parent_barcodes=[], ancestor_barcodes=[],
            first_non_muon_parent_barcodes=[], immediate_parent_barcodes=[],
            material_to_CMS_entry=dict(complete=False, unknown_reason='test unknown material')))
    return dict(event_id=[100, 1, number], native_weight=weight, muons=muons, GEN_muons=2,
        ancestor_nodes=[], unknown_transport_ancestor_barcodes=[], source_id='s1',
        original_source_identity_known=True, original_source_sha256='a'*64,
        source_file_sha256='b'*64)


def fixture():
    events = [event(), event(2, 0.)]
    events[1].update(muons=[], GEN_muons=0)
    source = dict(source_id='s1', stratum='qcd_20toinf',
        original_full_exposure=dict(saved_events=2, gen_sha256='a'*64, full_source_trials=200),
        feature_pin=dict(path='features.json.gz', sha256='c'*64, bytes=10),
        prepared_GEN_pin=dict(path='GEN.root', sha256='b'*64, bytes=1000),
        event_authority=dict(event_ids=[e['event_id'] for e in events],
                             native_weights=[e['native_weight'] for e in events]))
    authority = dict(complete=True, exit_code=0, source_id='s1', source=source['original_full_exposure'],
        prepared_publications=[source['feature_pin'], source['prepared_GEN_pin']])
    features = dict(schema='shift-gen-material-features-v1', maximum_events_per_source=None,
        prefix_requested=False, zero_GEN_muon_records_retained=True,
        sources=[dict(sha256='b'*64, source_file_event_count=2)], events=events,
        summary=dict(events=2, source_read_counts=[dict(source_file_sha256='b'*64,
            source_file_event_count=2, events_read=2, entire_input_file_read=True)]))
    return source, authority, features


def write_pin(path, value, compressed=False):
    data = json.dumps(value, allow_nan=False).encode()
    if compressed:
        data = gzip.compress(data, mtime=0)
    path.write_bytes(data)
    return dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))


class OpportunityTest(unittest.TestCase):
    def parent_event(self, depth=1.):
        original = event()
        original['parent_flights'] = [dict(barcode=7, pdg=223, birth_cm=[250., 0., 14800.],
            material=dict(complete=True, full_saved_chord_coverage=True, interaction_depth_proxy=depth))]
        original['ancestor_nodes'] = [dict(barcode=7, pdg=223, status=2, immediate_parent_barcodes=[])]
        for muon in original['muons']:
            muon.update(transport_parent_barcodes=[7], ancestor_barcodes=[7], first_non_muon_parent_barcodes=[7])
        return original

    def test_optional_union_depth_counts_shared_parent_once_and_preserves_default(self):
        original = self.parent_event()
        self.assertIsNone(describe_event(original, geometry())[1])
        counts, result = describe_event(original, geometry(), 1.)
        self.assertEqual(counts['no_listed_transport_parent_OS_pairs'], 0)
        self.assertEqual(counts['known_shallow_parent_OS_pairs'], 1)
        pair = result['pairs'][0]
        self.assertEqual(pair['transport_parent_union']['known_union_depth'], 1.)
        self.assertEqual(pair['transport_parent_union']['barcodes'], [7])
        self.assertEqual(len(pair['transport_parent_union']['parent_records']), 1)
        self.assertEqual(pair['pt_GeV'], 0.)
        self.assertTrue(pair['both_muons_cross_two_stations_each_endcap'])
        self.assertEqual(describe_event(self.parent_event(1.0001), geometry(), 1.)[0][
            'omitted_above_known_parent_depth_OS_pairs'], 1)

    def test_unknown_parent_depth_and_coverage_are_never_replaced_by_zero(self):
        for change in [dict(complete=False), dict(full_saved_chord_coverage=False),
                       dict(interaction_depth_proxy=None)]:
            with self.subTest(change=change):
                original = self.parent_event(0.)
                original['parent_flights'][0]['material'].update(change)
                description = parent_union_description(original['muons'], {7: original['parent_flights'][0]}, [])
                self.assertIsNone(description['known_union_depth'])
                self.assertFalse(description['all_parent_paths_known_and_fully_covered'])
                counts, result = describe_event(original, geometry(), 1.)
                self.assertEqual(counts['omitted_unknown_parent_path_OS_pairs'], 1)
                self.assertIsNone(result)
        original = self.parent_event()
        original['parent_flights'] = []
        self.assertEqual(describe_event(original, geometry(), 1.)[0]['pointing_OS_pairs_with_missing_parent_flight'], 1)
        original = self.parent_event()
        original['unknown_transport_ancestor_barcodes'] = [7]
        self.assertEqual(describe_event(original, geometry(), 1.)[0]['pointing_OS_pairs_with_unknown_transport_ancestry'], 1)

    def test_geometry_is_checked_before_unknown_parent_paths_or_inventory(self):
        original = self.parent_event()
        original['parent_flights'][0]['material']['full_saved_chord_coverage'] = False
        original['muons'][0]['momentum_GeV'] = [0., 0., 100.]
        counts, result = describe_event(original, geometry(), 1.)
        self.assertEqual(counts['all_OS_pairs'], 1)
        self.assertEqual(counts['omitted_unknown_or_no_two_forward_stations'], 1)
        self.assertEqual(counts.get('omitted_unknown_parent_path_OS_pairs', 0), 0)
        self.assertIsNone(result)
        original['muons'][0].pop('transport_parent_barcodes')
        counts, result = describe_event(original, geometry(), 1.)
        self.assertEqual(counts['OS_pairs_unknown_transport_parent_inventory'], 1)
        self.assertEqual(counts.get('pointing_OS_pairs_unknown_transport_parent_inventory', 0), 0)

    def test_shallow_option_preserves_no_parent_pair_and_separate_original_scope_count(self):
        counts, result = describe_event(event(), geometry(), 1.)
        self.assertEqual(counts['no_listed_transport_parent_OS_pairs'], 1)
        self.assertEqual(result['pairs'][0]['transport_parent_union']['known_union_depth'], 0.)
        self.assertEqual(result['pairs'][0]['transport_parent_union']['parent_records'], [])
        self.assertTrue(result['pairs'][0]['transport_parent_union']['all_parent_paths_known_and_fully_covered'])

    def test_same_pair_exact_geometry_low_pt_and_unknown_material_are_descriptive(self):
        counts, result = describe_event(event(), geometry())
        self.assertEqual(counts['reported_pairs'], 1)
        pair = result['pairs'][0]
        self.assertEqual(pair['descriptive_rank_min_stations_then_layers'], [2, 2])
        self.assertTrue(pair['both_muons_cross_two_stations_each_endcap'])
        self.assertAlmostEqual(pair['mass_GeV'], 2*.1057)
        self.assertEqual(pair['pt_GeV'], 0.)
        self.assertEqual(result['native_weight'], -.125)
        muon = result['candidate_muons'][0]
        self.assertEqual(muon['GEN']['material_to_CMS_entry']['unknown_reason'], 'test unknown material')
        self.assertNotIn('hits', muon['active_CSC'])
        self.assertNotIn('layer_ids', muon['active_CSC']['by_endcap']['plus'])

    def test_unknown_ancestry_remains_in_report_and_is_never_called_prompt(self):
        original = event()
        original['muons'][0].update(ancestor_barcodes=[99], first_non_muon_parent_barcodes=[99])
        original['unknown_transport_ancestor_barcodes'] = [99]
        counts, result = describe_event(original, geometry())
        self.assertEqual(counts['reported_pairs_with_unknown_ancestry'], 1)
        self.assertTrue(result['pairs'][0]['ancestry_unknown'])
        ancestry = result['candidate_muons'][0]['ancestry']
        self.assertEqual(ancestry['missing_ancestor_barcodes'], [99])
        self.assertEqual(ancestry['unknown_transport_ancestor_barcodes'], [99])
        self.assertTrue(ancestry['no_listed_transport_parent_is_not_proof_of_prompt_origin'])
        union = parent_union_description(original['muons'], {}, [99])
        self.assertIsNone(union['known_union_depth'])
        self.assertFalse(union['all_parent_paths_known_and_fully_covered'])
        counts, result = describe_event(original, geometry(), 1.)
        self.assertEqual(counts['pointing_OS_pairs_with_unknown_transport_ancestry'], 1)
        self.assertIsNone(result)

    def test_transport_inventory_and_nonpointing_omissions_are_counted(self):
        original = event()
        original['muons'][0]['transport_parent_barcodes'] = [99]
        counts, result = describe_event(original, geometry())
        self.assertEqual(counts['all_OS_pairs'], 1)
        self.assertEqual(counts['muons_with_listed_transport_parents'], 1)
        self.assertIsNone(result)
        original['muons'][0].pop('transport_parent_barcodes')
        self.assertEqual(describe_event(original, geometry())[0]['muons_unknown_transport_parent_inventory'], 1)
        original = event()
        original['muons'][0]['birth_cm'] = None
        counts, result = describe_event(original, geometry())
        self.assertEqual(counts['omitted_unknown_or_no_two_forward_stations'], 1)
        self.assertEqual(len(result['unranked_pairs']), 1)
        original = event()
        original['muons'][0]['birth_cm'][0] = 10000.
        self.assertEqual(describe_event(original, geometry())[0]['omitted_outside_broad_500cm_support'], 1)

    def test_full_native_census_identity_weight_and_root_binding_must_match(self):
        source, authority, features = fixture()
        validate_census(source, authority, features)
        mutations = [lambda f: f['events'].reverse(),
                     lambda f: f['events'][0].update(native_weight=-.12500000000000003),
                     lambda f: f['events'][1].update(native_weight=-0.),
                     lambda f: f['events'][0].update(original_source_sha256='d'*64),
                     lambda f: f['events'][0].update(source_file_sha256='d'*64),
                     lambda f: f.update(prefix_requested=True),
                     lambda f: f['events'].pop()]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(features)
                mutate(changed)
                with self.assertRaises(ValueError):
                    validate_census(source, authority, changed)

    def test_authenticated_complete_scan_without_root_download_and_no_exposure_duplication(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source, authority, features = fixture()
            source['feature_pin'] = write_pin(folder/'features.json.gz', features, True)
            authority['prepared_publications'] = [source['feature_pin'], source['prepared_GEN_pin']]
            source['source_authority'] = write_pin(folder/'authority.json', authority)
            request = dict(sources=[source], map_pin=write_pin(folder/'map.json.gz', geometry(), True))
            report = scan(request)
            self.assertTrue(report['complete'])
            self.assertEqual(report['totals']['events'], 2)
            self.assertEqual(report['totals']['zero_muon_events'], 1)
            self.assertEqual(report['totals']['reported_pairs'], 1)
            self.assertEqual(report['sources'][0]['original_full_exposure']['full_source_trials'], 200)
            self.assertFalse((folder/'GEN.root').exists())
            with self.assertRaisesRegex(ValueError, 'Duplicate source'):
                scan(dict(request, sources=[source, source]))
            changed = copy.deepcopy(source)
            changed['source_id'] = 's2'
            with self.assertRaisesRegex(ValueError, 'Duplicate raw'):
                scan(dict(request, sources=[source, changed]))
            changed['original_full_exposure']['gen_sha256'] = 'd'*64
            with self.assertRaisesRegex(ValueError, 'Duplicate prepared'):
                scan(dict(request, sources=[source, changed]))
            with self.assertRaisesRegex(ValueError, 'report exceeds'):
                scan(dict(request, maximum_report_bytes=100))

    def test_file_hash_size_expansion_and_output_bounds_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            pin = write_pin(folder/'data.json.gz', dict(data='x'*10000), True)
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                load_pin(dict(pin, sha256='e'*64), folder, 1000, 20000)
            with self.assertRaisesRegex(ValueError, 'byte'):
                load_pin(dict(pin, bytes=pin['bytes']+1), folder, 1000, 20000)
            with self.assertRaisesRegex(ValueError, 'Expanded'):
                load_pin(pin, folder, 1000, 100)


if __name__ == '__main__':
    unittest.main()
