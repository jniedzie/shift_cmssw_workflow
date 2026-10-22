# Code style: AI-code. AI-generated collision-level census pooling controls.
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_dark_photon_meson_coverage import combine_source_inventories


def source(seed,counts):
    n = len(counts[130])
    rows = []
    for pdg,values in counts.items():
        total = sum(values)
        rows.append(dict(pdg_id=pdg,physical_parents=total,
            collisions_with_parent=sum(value>0 for value in values),
            sum_collision_count_squared=sum(value*value for value in values),
            final_native_records=total,modeled_parent_species=False,
            meson_mother_counts={},features={},exact_native_examples=[]))
    return dict(schema='shift-resolved-unfiltered-meson-source-inventory-v1',
        source_sampling_verified=True,resolved_source_configuration=True,
        source_class='SoftQCD:nonDiffractive',model_sha256='model',seed=seed,
        collision_exposure=n,generator_calls=n,source_record_stream_sha256=str(seed),
        resolved_settings=dict(source_settings=['fixed source']),
        source_configuration=dict(check_event=True,energy_tolerance=.01,mass_tolerance=.001),
        resolved_beams=dict(eA_gev=1.,eB_gev=1000.),native_info_beams_com_frame={},
        resolved_lifetime_settings=dict(limitTau0=True,tau0Max=10.),resolved_process_flags={},
        particle_data=[dict(pdg_id=pdg,may_decay=False,mean_proper_lifetime_mm=100.,mass_min_gev=.5)
                       for pdg in sorted(counts)],native_libraries=[dict(path='/cvmfs/native',sha256='native')],
        source_tool_sha256={f'/scratch/seed{seed}/audit.py':'same-code'},
        runtime=dict(cmssw_version='same-release',scram_arch='same-arch',cmssw_base=f'/scratch/{seed}'),
        native=dict(source_collisions=n,source_calls=n,generator_failures=0,
                    native_n_tried=n,native_n_accepted=n,sigma_source_mb=26.,sigma_source_error_mb=.0001),
        species=rows,excluded_bookkeeping=dict(neutral_flavor=1),
        collision_count_cross_moments=[dict(pdg_ids=[a,b],sum_count_product=sum(x*y for x,y in zip(counts[a],counts[b])))
            for a in sorted(counts) for b in sorted(counts) if a<=b])


class InventoryPoolTest(unittest.TestCase):
    def setUp(self):
        self.first = source(101,{130:[2,0],310:[0,1]})
        self.second = source(102,{130:[1,0,2],310:[0,1,1]})

    def test_pool_sums_whole_collision_moments_and_preserves_anticorrelation(self):
        combined = combine_source_inventories([self.first,self.second])
        self.assertEqual(combined['collision_exposure'],5)
        kl = next(row for row in combined['species'] if row['pdg_id']==130)
        self.assertEqual(kl['physical_parents'],5)
        self.assertEqual(kl['sum_collision_count_squared'],9)
        self.assertEqual(kl['collisions_with_parent'],3)
        cross = next(row['sum_count_product'] for row in combined['collision_count_cross_moments'] if row['pdg_ids']==[130,310])
        self.assertEqual(cross,2)
        self.assertAlmostEqual((cross-5*3/5)/(5-1),-.25)
        self.assertEqual(combined['excluded_bookkeeping']['neutral_flavor'],2)
        self.assertFalse(combined['pooled_quantiles_supported'])

    def test_repeated_seed_revision_contributes_no_extra_exposure(self):
        result = combine_source_inventories([self.first,copy.deepcopy(self.first),self.second])
        self.assertEqual(result['collision_exposure'],5)
        self.assertEqual(len(result['excluded_duplicate_revisions']),1)

    def test_conflicting_reuse_of_a_seed_requires_review(self):
        other = copy.deepcopy(self.first)
        other['source_record_stream_sha256'] = 'different'
        with self.assertRaisesRegex(ValueError,'Conflicting reuse'):
            combine_source_inventories([self.first,other])

    def test_source_conditions_table_code_or_model_change_is_rejected(self):
        for key,replacement in [('model_sha256','different'),('resolved_settings',{}),
                                ('particle_data',[]),('source_tool_sha256',{'/x/audit.py':'new-code'}),
                                ('native_libraries',[dict(path='/different',sha256='other')])]:
            with self.subTest(key=key):
                other = copy.deepcopy(self.second)
                other[key] = replacement
                with self.assertRaisesRegex(ValueError,'authority differs'):
                    combine_source_inventories([self.first,other])

    def test_invalid_cross_products_or_missing_diagonal_are_rejected(self):
        for mode in ('bad_cross','no_diagonal'):
            other = copy.deepcopy(self.second)
            if mode=='bad_cross':
                other['collision_count_cross_moments'][1]['sum_count_product'] = 10000
            else:
                other['collision_count_cross_moments'] = []
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'cross|diagonal'):
                combine_source_inventories([self.first,other])

    def test_feature_moments_merge_without_manufacturing_pooled_quantiles(self):
        self.first['species'][0]['features'] = dict(momentum=dict(count=2,mean=15.,sample_variance=50.,minimum=10.,maximum=20.))
        self.second['species'][0]['features'] = dict(momentum=dict(count=3,mean=40.,sample_variance=100.,minimum=30.,maximum=50.))
        result = combine_source_inventories([self.first,self.second])
        feature = result['species'][0]['features']['momentum']
        self.assertEqual(feature['mean'],30.)
        self.assertEqual(feature['sample_variance'],250.)
        self.assertIsNone(feature['quantiles'])


if __name__=='__main__':
    unittest.main()
