# Code style: AI-code. AI-generated fresh-campaign storage and ownership tests.
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_dark_photon_meson_production as production


class FreshProduction(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.queue = self.write('queue.json', [])
        self.registration = dict(schema='shift-fresh-dp-storage-registration-v1',
            complete=True, campaign_prefix='20261023_dp_material_csc_v1-meson',
            peer_reservations=[dict(name='SM', reserved_bytes=75000000000),
                               dict(name='directDY', reserved_bytes=30000000000)],
            native_queue_evidence=self.queue,
            registered_controller_directories=['/persistent/SM', '/persistent/DP'])
        self.authority = dict(schema='shift-fresh-dp-production-authority-v1',
            campaign_prefix=self.registration['campaign_prefix'], cancelled_exposure_credit=0,
            resume_cancelled_deployments=False, preserve_previous_ready_outputs=True,
            storage_registration=self.write('registration.json', self.registration))
        self.config = dict(production_mechanism='meson-radiative',
            campaign_prefix=self.registration['campaign_prefix'],
            fresh_production_authority=self.write('authority.json', self.authority))

    def write(self, name, value):
        path = self.root/name
        path.write_text(json.dumps(value))
        return dict(path=str(path), sha256=production.durable.sha(path))

    def replace_authority(self):
        self.config['fresh_production_authority'] = self.write('authority.json', self.authority)

    def test_fresh_campaign_preserves_every_peer_reservation_without_old_adaptive_tree(self):
        self.assertEqual(production.adaptive_reservation(self.config), 105000000000)
        self.assertIsNone(production.prior_meson_configuration(self.config))
        ready, evidence = production.prior_publication_ready(self.config, dict(point='m0p3_prompt'))
        self.assertTrue(ready)
        self.assertEqual(evidence['allowed_previous_campaigns'], [])
        self.assertTrue(evidence['fresh_campaign_without_prior_exposure'])

    def test_empty_frozen_draw_blocks_only_its_point_before_histogram_publication(self):
        batch = dict(batch='empty', point='m0p3_prompt')
        directory = self.root/'batches/empty/gen'
        directory.mkdir(parents=True)
        (directory/'material_selected_source.json').write_text(json.dumps(dict(selected_indices=[])))
        config = dict(directory=str(self.root), batches=[batch], conditions=dict(sha256='conditions'))
        state = {}
        self.assertIsNone(production.cumulative_result(config,state,batch,None,{}))
        self.assertEqual(list(state['blocked_points']), ['m0p3_prompt'])
        self.assertEqual(state['blocked_points']['m0p3_prompt']['batch'], 'empty')

    def test_legacy_configuration_still_requires_prior_ownership(self):
        config = dict(production_mechanism='meson-radiative')
        with self.assertRaises(KeyError):
            production.prior_meson_configuration(config)

    def test_fresh_cannot_inherit_cancelled_source_or_adaptive_identity(self):
        for key in ('prior_meson_publication', 'adaptive_directory', 'prior_meson_controller_directory'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'inheriting cancelled'):
                production.fresh_production_authority(dict(self.config, **{key:'/cancelled'}))

    def test_cancelled_exposure_credit_and_resume_are_rejected(self):
        for key, value in [('cancelled_exposure_credit', 1), ('resume_cancelled_deployments', True)]:
            old = copy.deepcopy(self.authority)
            self.authority[key] = value
            self.replace_authority()
            with self.subTest(key=key), self.assertRaises(ValueError):
                production.fresh_production_authority(self.config)
            self.authority = old

    def test_missing_storage_registration_and_changed_queue_evidence_fail_closed(self):
        (self.root/'queue.json').write_text('[{"unexpected":"changed"}]')
        with self.assertRaises(ValueError):
            production.fresh_production_authority(self.config)

    def test_negative_and_duplicate_peer_claims_are_rejected(self):
        for claims in ([dict(name='SM', reserved_bytes=-1)],
                       [dict(name='SM', reserved_bytes=1), dict(name='SM', reserved_bytes=2)]):
            self.registration['peer_reservations'] = claims
            self.authority['storage_registration'] = self.write('registration.json', self.registration)
            self.replace_authority()
            with self.assertRaises(ValueError):
                production.fresh_production_authority(self.config)


if __name__ == '__main__':
    unittest.main()
