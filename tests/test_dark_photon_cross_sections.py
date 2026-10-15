# Code style: AI-code. AI-generated rate-retention and correction regression tests.
"""Check native/physical rate separation and full GEN denominator retention."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from export_dark_photon_cross_sections import canonical_bytes, publish_file, publish_file_rpc, rate_record, retain_bytes


class CrossSectionAccounting(unittest.TestCase):
    def setUp(self):
        self.contract = dict(schema="shift-dark-photon-gen-v1", decay_mode="mumu", filtering="none",
            mass_gev=15., epsilon=1e-7, sampling_amplitude_scale=1e4, production_rate_correction=1e-8,
            width_authority=dict(br_mumu=.2, ctau_mm=100.),
            sampling_width_authority=dict(channels=[dict(branching_fraction=.2, products=[13,-13]),
                                                     dict(branching_fraction=.8, products=[11,-11])]),
            requested_events=2, cross_section_convention="native selected sigma includes BR once",
            mechanism="pure-on-shell-drell-yan", model="PBC-BC1-visible-hypercharge",
            generated_mass_support_gev=[14.85,15.15], beam_energy_GeV=6800.,
            source_model="provisional on-axis stationary proton target", seed=123, run_number=123,
            low_mass_mechanisms_included=False, prompt_interference_included=False)
        self.audit = dict(runtime_validated=True, events=2, event_ids=[[123,1,1],[123,1,4]],
            sum_weights=2., sum_weights_squared=2., native_cross_sections=[dict(value_pb=2.,error_pb=.1)],
            target_cross_sections=[dict(value_pb=2e-8,error_pb=1e-9)],
            proposal_ledger=dict(n_tried=50,n_selected=3,n_accepted=2,printed_cross_section_mb=2e-9),
            full_gen_denominator=dict(accepted_events=2,selection="none; outside-envelope decays retained"),
            rate_scope="Provisional DY benchmark; physical rate closure open")

    def record(self):
        return rate_record(self.contract,self.audit,point="m15_lab_100m",campaign="conditions",
                           source_hashes={"contract.json":"a"*64},gen_path="/eos/user/t/test/gen.root",gen_sha256="b"*64)

    def test_keep_dimuon_and_inferred_inclusive_rates_separate(self):
        record=self.record()
        self.assertEqual(record["native_selected_cross_sections_pb"][0]["value_pb"],2.)
        self.assertEqual(record["physical_selected_cross_sections_pb"][0]["value_pb"],2e-8)
        self.assertAlmostEqual(record["physical_inclusive_cross_sections_pb"][0]["value_pb"],1e-7)
        self.assertAlmostEqual(record["physical_inclusive_cross_sections_pb"][0]["error_pb"],5e-9)
        self.assertEqual(record["corrections"]["applications"],1)
        self.assertFalse(record["corrections"]["extra_decay_branching_fraction_applied"])
        self.assertFalse(record["normalization_ready"])

    def test_spectral_branching_replacement_is_applied_once(self):
        self.contract["width_authority"]["br_mumu"] = .3
        self.contract["cross_section_correction_components"] = dict(common_amplitude=1e-8,branching_ratio=1.5)
        self.contract["production_rate_correction"] = 1.5e-8
        self.audit["target_cross_sections"] = [dict(value_pb=3e-8,error_pb=1.5e-9)]
        result=self.record()
        self.assertEqual(result["corrections"]["branching_ratio"],1.4999999999999998)
        self.assertAlmostEqual(result["physical_inclusive_cross_sections_pb"][0]["value_pb"],1e-7)

    def test_store_all_tried_accepted_and_full_weight_denominators(self):
        result=self.record()["full_gen"]
        self.assertEqual((result["n_tried"],result["n_selected"],result["n_accepted"]),(50,3,2))
        self.assertEqual((result["accepted_events"],result["sum_weights"],result["sum_weights_squared"]),(2,2.,2.))
        self.assertEqual(result["event_ids_sha256"],hashlib.sha256(b"[[123,1,1],[123,1,4]]").hexdigest())

    def test_do_not_apply_correction_or_branching_twice(self):
        self.audit["target_cross_sections"][0]["value_pb"] *= .2
        with self.assertRaisesRegex(ValueError,"exactly once"):
            self.record()

    def test_distinct_run_estimates_are_not_summed_or_averaged(self):
        self.audit["native_cross_sections"].append(dict(value_pb=4.,error_pb=.2))
        self.audit["target_cross_sections"].append(dict(value_pb=4e-8,error_pb=2e-9))
        result=self.record()
        self.assertEqual(len(result["physical_selected_cross_sections_pb"]),2)
        self.assertEqual([item["value_pb"] for item in result["physical_selected_cross_sections_pb"]],[2e-8,4e-8])

    def test_refuse_unstated_branching_replacement(self):
        self.contract["width_authority"]["br_mumu"] = .3
        with self.assertRaisesRegex(ValueError,"explicit correction"):
            self.record()

    def test_refuse_mb_pb_mixup(self):
        self.audit["native_cross_sections"][0]["value_pb"] = 2e-9
        self.audit["target_cross_sections"][0]["value_pb"] = 2e-17
        with self.assertRaisesRegex(ValueError,"mb-to-pb"):
            self.record()

    def test_refuse_invalid_or_incomplete_rate_and_population_inputs(self):
        for mutation in [lambda:self.audit.update(runtime_validated=False),
                         lambda:self.audit.update(events=1),
                         lambda:self.audit.update(sum_weights=1.),
                         lambda:self.audit.update(event_ids=[[123,1,1],[123,1,1]]),
                         lambda:self.audit["proposal_ledger"].update(n_accepted=1),
                         lambda:self.audit["native_cross_sections"][0].update(error_pb=-1),
                         lambda:self.contract.update(production_rate_correction=2e-8)]:
            original_contract,original_audit=copy.deepcopy(self.contract),copy.deepcopy(self.audit)
            mutation()
            with self.assertRaises(ValueError):
                self.record()
            self.contract,self.audit=original_contract,original_audit


class DurablePublication(unittest.TestCase):
    def native_publication(self, previous, *, unknown_state=False):
        with tempfile.TemporaryDirectory() as temporary:
            source=Path(temporary)/"rate.json";source.write_bytes(b"exact rate\n")
            remote={"bytes":previous};uploads=[]
            def command(argv, **options):
                self.assertEqual(options["env"]["PATH"],"/usr/bin:/bin")
                self.assertNotIn("LD_LIBRARY_PATH",options["env"])
                if argv[0].endswith("xrdfs"):
                    if argv[2]=="stat":
                        if unknown_state:
                            return SimpleNamespace(returncode=1,stderr="Connection failed")
                        return SimpleNamespace(returncode=int(remote["bytes"] is None),
                            stderr="[3011] No such file" if remote["bytes"] is None else "")
                    self.assertEqual(argv[2:4],["mkdir","-p"])
                elif argv[0].endswith("xrdcp"):
                    if argv[-2].startswith("root://"):
                        Path(argv[-1]).write_bytes(remote["bytes"])
                    else:
                        self.assertIn("--posc",argv);self.assertNotIn("--force",argv)
                        uploads.append(argv);remote["bytes"]=Path(argv[-2]).read_bytes()
                else:
                    self.fail("Unexpected native command")
                return SimpleNamespace(returncode=0,stderr="")
            with patch("export_dark_photon_cross_sections.subprocess.run",side_effect=command):
                try:
                    result=publish_file_rpc(source,"/eos/user/t/test/cross_sections.json")
                except Exception:
                    self.assertFalse(uploads)
                    raise
            return result,uploads

    def test_native_publication_writes_absent_sidecar_and_hashes_readback(self):
        result,uploads=self.native_publication(None)
        self.assertEqual(len(uploads),1)
        self.assertEqual(result["sha256"],hashlib.sha256(b"exact rate\n").hexdigest())
        self.assertEqual(result["sha256"],result["readback_sha256"])

    def test_native_publication_accepts_equal_existing_bytes_without_upload(self):
        result,uploads=self.native_publication(b"exact rate\n")
        self.assertFalse(uploads)
        self.assertEqual(result["bytes"],len(b"exact rate\n"))

    def test_native_publication_never_overwrites_different_existing_bytes(self):
        with self.assertRaisesRegex(ValueError,"different EOS sidecar"):
            self.native_publication(b"different rate\n")

    def test_native_publication_unknown_state_stops_before_upload(self):
        with self.assertRaisesRegex(RuntimeError,"Unknown EOS sidecar state"):
            self.native_publication(None,unknown_state=True)

    def test_local_retention_is_idempotent_and_never_overwrites_different_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"record.json"
            content=canonical_bytes(dict(native_pb=1.2345678901234567))
            retain_bytes(path,content);retain_bytes(path,content)
            with self.assertRaisesRegex(ValueError,"different retained"):
                retain_bytes(path,b"different")
            self.assertEqual(path.read_bytes(),content)

    def test_publication_requires_canonical_eos_path(self):
        with self.assertRaisesRegex(ValueError,"canonical"):
            publish_file("/tmp/nonexistent","/tmp/elsewhere/record.json")

    def test_existing_published_rate_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"record.json";source.write_text("new rate")
            destination=Path("/eos/user/t/test/sample/cross_sections.json")
            with patch.object(Path,"mkdir"),patch.object(Path,"exists",return_value=True),patch(
                    "export_dark_photon_cross_sections.sha256",side_effect=["a"*64,"b"*64]):
                with self.assertRaisesRegex(ValueError,"different published"):
                    publish_file(source,destination)


if __name__ == "__main__":
    unittest.main()
