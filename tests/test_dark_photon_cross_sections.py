# Code style: AI-code. AI-generated rate-retention and correction regression tests.
"""Check native/physical rate separation and full GEN denominator retention."""
import copy
from contextlib import contextmanager
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from export_dark_photon_cross_sections import (canonical_bytes, cleanup_meson_ledger_readbacks, export_plan, input_files, open_meson_ledger,
    publish_file, publish_file_rpc, rate_record, resolve_meson_ledger, retain_bytes)
from pythia_generation_ledger import read_pythia_generation_ledger
from dark_photon_meson import radiative_branching, transition_form_factor_abs2
from dark_photon_meson_source import model_precision_budget, resolve_parent_kinematics


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


    def test_dy_exporter_import_still_works_without_meson_runtime_files(self):
        scripts=Path(__file__).resolve().parents[1]/"scripts"
        with tempfile.TemporaryDirectory() as temporary:
            for filename in ("export_dark_photon_cross_sections.py","pythia_generation_ledger.py"):
                (Path(temporary)/filename).write_bytes((scripts/filename).read_bytes())
            subprocess.run([sys.executable,"-I","-c",
                "import sys; sys.dont_write_bytecode=True; sys.path.insert(0,sys.argv[1]); "
                "import export_dark_photon_cross_sections; assert 'dark_photon_meson' not in sys.modules",temporary],
                cwd=temporary,check=True,capture_output=True,text=True)


class MesonCrossSectionAccounting(unittest.TestCase):
    """The physical rate uses all pp collisions, not conditional signal N."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.card = json.loads((Path(__file__).resolve().parents[1] /
            "config/campaigns/dark_photon_meson_model.json").read_text())
        self.models = {row["pdg_id"]: row for row in self.card["parents"]}
        self.epsilon, self.mass, self.branching = 1e-3, .3, .4
        self.width_authority = dict(provider="DarkCast 2.1", commit="test-pinned-source")
        self.width = dict(mass_gev=self.mass, epsilon=self.epsilon, br_mumu=self.branching,
            ctau_mm=1.973269804e-3, total_width_gev=1e-10,
            partial_widths_gev={"e_e":6e-11,"mu_mu":4e-11,"hadrons":0.},
            spectral_source=self.width_authority)
        eta, etaprime = self.parent(1,221), self.parent(2,331)
        populations = [None, [], [eta], [eta,etaprime], [eta]]
        self.rows = []
        slot = 0
        for trial, parents in enumerate(populations,1):
            coefficient = math.fsum(self.coefficient(parent) for parent in parents) if parents else 0.
            accepted = trial in (3,4)
            slot += int(accepted)
            self.rows.append(dict(schema="shift-meson-source-trial-v1",trial=trial,
                generator_success=parents is not None,native_weight=1. if parents is not None else None,
                parents=parents or [],coefficient_sum=coefficient,
                physical_probability=self.epsilon**2*coefficient,acceptance_probability=coefficient/100.,
                accepted=accepted,parent_index=1 if accepted else None,accepted_slot=slot if accepted else None))
        coefficients=[row["coefficient_sum"] for row in self.rows if row["generator_success"]]
        probabilities=[row["physical_probability"] for row in self.rows if row["generator_success"]]
        self.exposure=dict(schema="shift-meson-source-exposure-v1",fixed_source_collisions=4,
            source_calls=5,source_collisions=4,generator_failures=1,zero_eligible_parent_collisions=1,
            sum_native_weights=4.,sum_native_weights_squared=4.,sigma_source_mb=30.,sigma_source_error_mb=.3,
            native_n_tried=8,native_n_accepted=4,coefficient_sum=math.fsum(coefficients),
            coefficient_sum_squared=math.fsum(value**2 for value in coefficients),
            physical_probability_sum=math.fsum(probabilities),physical_probability_sum_squared=math.fsum(value**2 for value in probabilities),
            accepted_signal_events=2,analytic_coefficient_bound=100.,coefficient_includes_br_mumu=True,
            coefficient_excludes_epsilon_squared=True,unit_output_gen_weights=True)
        self.contract=dict(schema="shift-dark-photon-gen-v1",production_mechanism="eta-etaprime-radiative",
            decay_mode="mumu",filtering="none",mass_gev=self.mass,epsilon=self.epsilon,requested_events=2,
            physical_width_table=self.width,width_authority=self.width_authority,
            model="visible-photon-like-dark-photon",generated_mass_support_gev=[self.mass,self.mass],
            beam_energy_GeV=6800.,source_model="fixed-target unfiltered ND benchmark",seed=17,run_number=77)
        self.audit=dict(runtime_validated=True,events=2,event_ids=[[77,1,1],[77,1,2]],sum_weights=2.,sum_weights_squared=2.,
            proposal_ledger=dict(n_tried=8,n_selected=5,n_accepted=4,printed_cross_section_mb=30.),
            full_gen_denominator=dict(accepted_events=2,source_collisions=4),rate_scope="ND meson benchmark")
        self.freeze()

    def parent(self,index,pdg):
        mass=self.models[pdg]["mass_gev"]
        return dict(index=index,pdg_id=pdg,mass_gev=mass,p4=[mass,0.,0.,0.])

    def coefficient(self,parent):
        model=self.models[parent["pdg_id"]]
        ff=transition_form_factor_abs2(self.mass,model["transition_form_factor"])
        return radiative_branching(parent["mass_gev"],self.mass,1e-3,model["br_gamma_gamma"],math.sqrt(ff))/1e-6*self.branching

    def freeze(self):
        ledger=b"".join((json.dumps(row,sort_keys=True)+"\n").encode() for row in self.rows)
        (self.directory/"meson_source_ledger.jsonl").write_bytes(ledger)
        self.contract["source_ledger_sha256"]=hashlib.sha256(ledger).hexdigest()
        self.exposure["source_ledger_sha256"]=self.contract["source_ledger_sha256"]
        for name,value,key in [("source_exposure.json",self.exposure,"source_exposure_sha256"),
                               ("meson_model.json",self.card,"meson_model_card_sha256")]:
            content=canonical_bytes(value)
            (self.directory/name).write_bytes(content)
            self.contract[key]=hashlib.sha256(content).hexdigest()
        self.contract["source_exposure"]=copy.deepcopy(self.exposure)
        self.contract["meson_model_card"]=copy.deepcopy(self.card)
        (self.directory/"contract.json").write_bytes(canonical_bytes(self.contract))

    def record(self):
        return rate_record(self.contract,self.audit,point="m0p3_lab_30m",campaign="meson_conditions",
            source_hashes={"contract.json":"a"*64},gen_path="/eos/user/t/test/gen.root",gen_sha256="b"*64,
            source_directory=self.directory)

    def enable_native_precision(self):
        reference=self.directory/"ErrorChecks.xml"
        reference.write_text('Synthetic primary-reference fixture for native Check:mTolErr\n')
        support=[dict(pdg_id=pdg,mass_min_gev=model["mass_gev"],mass_max_gev=model["mass_gev"])
                 for pdg,model in self.models.items()]
        self.contract["source_precision"]=dict(check_event=True,mass_tolerance=.001,energy_tolerance=.01,
            reference=str(reference),reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            parents=support,prescription="native generated mass for branching and bound; unchanged p4 invariant for decay")
        resolved={pdg:dict(mass_min_gev=model["mass_gev"],mass_max_gev=model["mass_gev"],
            br_gamma_gamma=model["br_gamma_gamma"],form_factor_abs2=transition_form_factor_abs2(self.mass,model["transition_form_factor"]),
            relative_model_uncertainty=model_precision_budget(model,self.mass)) for pdg,model in self.models.items()}
        self.contract["resolved_parent_models"]={str(pdg):model for pdg,model in resolved.items()}
        # Real observed Pythia momentum precision example: retain it exactly.
        self.rows[2]["parents"][0]["p4"]=[570.5010019649538,-.7004529990853869,.7446772589972893,-570.4998228797658]
        differences=[];shifts=[]
        for row in self.rows:
            for parent in row["parents"]:parent["native_mass_gev"]=parent["mass_gev"]
            row["parents"]=resolve_parent_kinematics(row["parents"],.001,self.mass,resolved,self.branching)
            differences.extend(parent["coefficient_difference_from_kinematic_mass"] for parent in row["parents"])
            shifts.extend(abs(parent["coefficient_relative_precision_shift"]) for parent in row["parents"])
        self.exposure.update(coefficient_precision_difference_sum=math.fsum(differences),
            maximum_coefficient_relative_precision_shift=max(shifts),
            relative_rate_precision_shift=math.fsum(differences)/self.exposure["coefficient_sum"])
        self.freeze()

    @contextmanager
    def remote_ledger(self, encoding):
        raw=(self.directory/"meson_source_ledger.jsonl").read_bytes()
        encoded=gzip.compress(raw,mtime=0) if encoding=="gzip" else raw
        filename="meson_source_ledger.jsonl"+(".gz" if encoding=="gzip" else "")
        remote=Path("/eos/user/j/jniedzie/shift_cmssw_support/unit-fixture")/filename
        stored=self.directory/"stored_ledger";stored.write_bytes(encoded)
        receipt=dict(schema="shift-meson-source-ledger-publication-v1",path=str(remote),encoding=encoding,
            bytes=len(encoded),sha256=hashlib.sha256(encoded).hexdigest(),readback_sha256=hashlib.sha256(encoded).hexdigest(),
            uncompressed_bytes=len(raw),uncompressed_sha256=hashlib.sha256(raw).hexdigest())
        (self.directory/"meson_ledger_publication.json").write_bytes(canonical_bytes(receipt))
        (self.directory/"meson_source_ledger.jsonl").unlink()
        original_open,original_stat,original_gzip=Path.open,Path.stat,gzip.open
        def path_open(path,*args,**kwargs):return original_open(stored if path==remote else path,*args,**kwargs)
        def path_stat(path,*args,**kwargs):return original_stat(stored if path==remote else path,*args,**kwargs)
        def gzip_open(path,*args,**kwargs):return original_gzip(stored if Path(path)==remote else path,*args,**kwargs)
        with patch.object(Path,"open",path_open),patch.object(Path,"stat",path_stat),patch("gzip.open",side_effect=gzip_open):
            yield remote,receipt,stored

    def test_full_fixed_exposure_and_mb_conversion_not_signal_count(self):
        result=self.record()
        expected=30e9*self.exposure["physical_probability_sum"]/4
        self.assertAlmostEqual(result["physical_selected_cross_sections_pb"][0]["value_pb"],expected)
        self.assertEqual(result["native_source_cross_sections_pb"][0]["value_pb"],30e9)
        self.assertNotIn("native_selected_cross_sections_pb",result)
        self.assertEqual(result["full_gen"]["accepted_events"],2)
        self.assertEqual(result["full_gen"]["n_accepted"],4)
        self.assertEqual(result["source_exposure"]["zero_eligible_parent_collisions"],1)
        self.assertEqual(result["proposal_ledger"]["n_tried"],8)

    def test_whole_collision_variance_preserves_multiple_parent_correlation(self):
        result=self.record()
        total=self.exposure["physical_probability_sum"]
        squared=self.exposure["physical_probability_sum_squared"]
        variance=(squared-total**2/4)/(4*3)
        expected=math.hypot(30e9*math.sqrt(variance),.3e9*total/4)
        self.assertAlmostEqual(result["physical_selected_cross_sections_pb"][0]["error_pb"],expected)
        self.assertAlmostEqual(result["rate_uncertainty"]["variance_of_mean_collision_probability"],variance)

    def test_explicit_native_mass_precision_keeps_momenta_and_generated_mass_rate(self):
        before=self.record()["physical_selected_cross_sections_pb"]
        self.enable_native_precision()
        retained_p4=copy.deepcopy(self.rows[2]["parents"][0]["p4"])
        result=self.record()
        self.assertEqual(result["physical_selected_cross_sections_pb"],before)
        self.assertEqual(self.rows[2]["parents"][0]["p4"],retained_p4)
        self.assertGreater(result["source_exposure"]["maximum_coefficient_relative_precision_shift"],0.)
        self.assertGreater(result["source_exposure"]["relative_rate_precision_shift"],0.)

    def test_native_precision_metadata_and_frozen_tolerance_are_checked(self):
        self.enable_native_precision()
        original=copy.deepcopy((self.contract,self.exposure,self.rows))
        for mutation in [lambda:self.contract["source_precision"].update(check_event=False),
                         lambda:self.contract["source_precision"].update(mass_tolerance=1e-14),
                         lambda:self.rows[2]["parents"][0].update(coefficient_relative_precision_shift=1.),
                         lambda:self.exposure.update(relative_rate_precision_shift=1.)]:
            mutation();self.freeze()
            with self.assertRaises(ValueError):self.record()
            self.contract,self.exposure,self.rows=copy.deepcopy(original)
            self.freeze()

    def test_branching_is_in_parent_coefficient_exactly_once(self):
        result=self.record()
        selected=result["physical_selected_cross_sections_pb"][0]["value_pb"]
        inclusive=result["physical_inclusive_cross_sections_pb"][0]["value_pb"]
        self.assertAlmostEqual(inclusive*self.branching,selected)
        self.assertEqual(result["corrections"]["applications"],1)
        self.assertFalse(result["corrections"]["extra_decay_branching_fraction_applied"])
        self.rows[2]["coefficient_sum"]*=self.branching
        self.freeze()
        with self.assertRaisesRegex(ValueError,"parent branching sum"):
            self.record()

    def test_source_model_and_full_collision_ledger_are_retained(self):
        files=input_files(self.directory,{})
        for name in ("source_exposure.json","meson_source_ledger.jsonl","meson_model.json"):
            self.assertIn(name,files)

    def test_remote_identity_ledger_is_verified_and_only_pointer_is_retained(self):
        expected=self.record()["physical_selected_cross_sections_pb"]
        with self.remote_ledger("identity") as (remote,unused_receipt,unused_stored):
            self.assertEqual(resolve_meson_ledger(self.directory,self.contract),remote)
            self.assertEqual(self.record()["physical_selected_cross_sections_pb"],expected)
            files=input_files(self.directory,{})
            self.assertIn("meson_ledger_publication.json",files)
            self.assertNotIn("meson_source_ledger.jsonl",files)

    def test_remote_gzip_preserves_exact_raw_ledger_and_rates_without_expansion(self):
        expected=self.record()["physical_selected_cross_sections_pb"]
        with self.remote_ledger("gzip") as (remote,unused_receipt,unused_stored):
            self.assertEqual(resolve_meson_ledger(self.directory,self.contract),remote)
            with open_meson_ledger(remote) as source:self.assertEqual(sum(1 for unused in source),5)
            self.assertEqual(self.record()["physical_selected_cross_sections_pb"],expected)
            self.assertFalse((self.directory/"meson_source_ledger.jsonl").exists())

    def test_remote_ledger_receipt_and_actual_bytes_must_agree(self):
        with self.remote_ledger("gzip") as (unused_remote,receipt,stored):
            pointer=self.directory/"meson_ledger_publication.json"
            for key,value in [("path","/tmp/unapproved/meson_source_ledger.jsonl.gz"),
                              ("readback_sha256","changed"),("uncompressed_sha256","changed"),
                              ("uncompressed_bytes",1),("bytes",1)]:
                changed=dict(receipt);changed[key]=value;pointer.write_bytes(canonical_bytes(changed))
                with self.assertRaises(ValueError):resolve_meson_ledger(self.directory,self.contract)
            pointer.write_bytes(canonical_bytes(receipt))
            stored.write_bytes(b"different stored bytes")
            with self.assertRaisesRegex(ValueError,"readback"):
                resolve_meson_ledger(self.directory,self.contract)

    def test_worker_without_eos_mount_downloads_and_reuses_verified_local_gzip(self):
        self.addCleanup(cleanup_meson_ledger_readbacks)
        with self.remote_ledger("gzip") as (remote,unused_receipt,stored):
            previous_stat=Path.stat
            def missing_mount(path,*args,**kwargs):
                if path==remote:raise FileNotFoundError(str(path))
                return previous_stat(path,*args,**kwargs)
            def download(argv,**options):
                self.assertEqual(argv[0],"/usr/bin/xrdcp")
                self.assertEqual(argv[-2],"root://eosuser.cern.ch/"+str(remote))
                self.assertNotIn("LD_LIBRARY_PATH",options["env"])
                self.assertEqual(options["env"]["PATH"],"/usr/bin:/bin")
                Path(argv[-1]).write_bytes(stored.read_bytes())
                return SimpleNamespace(returncode=0)
            with patch.object(Path,"stat",missing_mount),patch(
                    "export_dark_photon_cross_sections.subprocess.run",side_effect=download) as transport:
                path=resolve_meson_ledger(self.directory,self.contract)
                self.assertNotEqual(path,remote)
                self.assertFalse(str(path).startswith("/afs/"))
                with open_meson_ledger(path) as rows:self.assertEqual(sum(1 for unused in rows),5)
                self.assertEqual(resolve_meson_ledger(self.directory,self.contract),path)
                self.assertEqual(transport.call_count,1)
                path.write_bytes(b"corrupted local cache")
                with self.assertRaisesRegex(ValueError,"readback"):
                    resolve_meson_ledger(self.directory,self.contract)
                self.assertEqual(transport.call_count,1)

    def test_failed_native_ledger_transport_is_unknown_and_not_a_stale_cache_hit(self):
        self.addCleanup(cleanup_meson_ledger_readbacks)
        with self.remote_ledger("identity") as (remote,unused_receipt,unused_stored):
            previous_stat=Path.stat
            def missing_mount(path,*args,**kwargs):
                if path==remote:raise FileNotFoundError(str(path))
                return previous_stat(path,*args,**kwargs)
            with patch.object(Path,"stat",missing_mount),patch(
                    "export_dark_photon_cross_sections.subprocess.run",
                    side_effect=subprocess.CalledProcessError(54,["xrdcp"],stderr="Connection failed")):
                with self.assertRaises(subprocess.CalledProcessError):resolve_meson_ledger(self.directory,self.contract)

    def test_changed_pinned_source_file_fails_before_rate_export(self):
        with (self.directory/"meson_source_ledger.jsonl").open("a") as stream:stream.write("{}\n")
        with self.assertRaisesRegex(ValueError,"Pinned meson source input changed"):
            self.record()

    def test_incomplete_or_inconsistent_source_accounting_is_rejected(self):
        mutations=[lambda:self.exposure.update(fixed_source_collisions=5),
            lambda:self.exposure.update(source_collisions=2),
            lambda:self.exposure.update(sum_native_weights=2.),
            lambda:self.exposure.update(coefficient_includes_br_mumu=False),
            lambda:self.exposure.update(zero_eligible_parent_collisions=0),
            lambda:self.exposure.update(physical_probability_sum_squared=0.),
            lambda:self.exposure.update(sigma_source_mb=30e9),
            lambda:self.rows[0].update(physical_probability=1.),
            lambda:self.rows[1].update(trial=1),
            lambda:self.rows[2].update(native_weight=2.),
            lambda:self.rows[2].update(accepted_slot=2),
            lambda:self.rows[2].update(parent_index=999),
            lambda:self.rows[3]["parents"].append(copy.deepcopy(self.rows[3]["parents"][0]))]
        for mutation in mutations:
            original=copy.deepcopy((self.contract,self.audit,self.exposure,self.rows))
            mutation();self.freeze()
            with self.assertRaises(ValueError):self.record()
            self.contract,self.audit,self.exposure,self.rows=original
            self.freeze()

    def test_native_source_counters_and_unit_signal_ids_stay_distinct(self):
        for mutation in [lambda:self.audit["proposal_ledger"].update(n_accepted=2),
                         lambda:self.audit.update(event_ids=[[77,1,1],[77,1,1]]),
                         lambda:self.audit.update(sum_weights_squared=1.)]:
            original=copy.deepcopy(self.audit)
            mutation()
            with self.assertRaises(ValueError):self.record()
            self.audit=original


    def test_export_plan_reads_native_source_log_and_retains_full_meson_inputs(self):
        log="\n".join([
            " *------- PYTHIA Event and Cross Section Statistics -------*",
            " | Subprocess Code | Number of events | sigma +- delta |",
            " | | Tried Selected Accepted | (estimated) (mb) |",
            " | non-diffractive 101 | 8 5 4 | 3.000e+01 3.000e-01 |",
            " | sum | 8 5 4 | 3.000e+01 3.000e-01 |",
            " *------- End PYTHIA Event and Cross Section Statistics -------*",""])
        (self.directory/"cmsRun.log").write_text(log)
        self.audit["proposal_ledger"]=read_pythia_generation_ledger(self.directory/"cmsRun.log",4)
        # The immutable audit records the original worker scratch location.
        # Retention moves its byte-identical log without rewriting evidence.
        self.audit["proposal_ledger"]["log_path"]="/pool/condor/original-source/gen/cmsRun.log"
        (self.directory/"validation.json").write_bytes(canonical_bytes(self.audit))
        gen=self.directory/"events.root"
        gen.write_bytes(b"synthetic ROOT identity; no ROOT reader is needed for retention")
        genhash=hashlib.sha256(gen.read_bytes()).hexdigest()
        (self.directory/"manifest.json").write_bytes(canonical_bytes(dict(output_sha256=genhash)))
        for name in ("resolved_cfg.py","audit.log"):(self.directory/name).write_text("small retained fixture\n")
        point,campaign="m0p3_lab_30m","meson_conditions"
        plan=dict(campaign=campaign,points=[dict(point=point,gen_directory=str(self.directory),
            final_output_base="/eos/user/t/test/darkphoton_"+point+"/"+campaign)])
        planpath=self.directory/"plan.json";planpath.write_bytes(canonical_bytes(plan))
        parent=dict(event_ids=self.audit["event_ids"],native_sumw=2.,native_sumw2=2.,gen_sha256=genhash,gen_path=str(gen))
        for filename,key in [("contract.json","contract_sha256"),("validation.json","validation_sha256"),("manifest.json","manifest_sha256")]:
            parent[key]=hashlib.sha256((self.directory/filename).read_bytes()).hexdigest()
        (self.directory/"gen_inventory.json").write_bytes(canonical_bytes(dict(complete=True,points={point:parent})))
        result=export_plan(planpath,self.directory/"retained")
        self.assertTrue(result["complete"])
        record=result["points"][0]["record"]
        self.assertEqual(record["proposal_ledger"]["n_accepted"],4)
        self.assertEqual(record["full_gen"]["accepted_events"],2)
        retained=self.directory/"retained/points"/campaign/point/"metadata/GEN"
        for filename in ("source_exposure.json","meson_source_ledger.jsonl","meson_model.json"):
            self.assertEqual((retained/filename).read_bytes(),(self.directory/filename).read_bytes())


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


class NativeModelPortability(unittest.TestCase):
    def test_cancellation_diagnostics_use_operand_ulps_without_relaxing_rates(self):
        from export_dark_photon_cross_sections import (native_precision_roundoff,
            same_number,same_signed_number)
        parent=dict(native_mass_gev=.493677,kinematic_mass_gev=.493677,p4=[320.,0.,0.,-319.],
            coefficient_difference_from_kinematic_mass=-3.6e-15)
        checked=dict(parent,coefficient_difference_from_kinematic_mass=-3.6e-15+2*math.ulp(7.344250125485237e-5))
        bounds=native_precision_roundoff(parent,checked,7.344250125485237e-5)
        stored,replayed=-4.9714676819689885e-11,-4.971489886429481e-11
        with self.assertRaises(ValueError):same_signed_number(stored,replayed,'ratio shift')
        same_signed_number(stored,replayed,'ratio shift',
            absolute_roundoff=bounds['coefficient_relative_precision_shift'])
        with self.assertRaises(ValueError):
            same_signed_number(stored,stored+64*math.ulp(1.),'ratio shift',
                absolute_roundoff=bounds['coefficient_relative_precision_shift'])
        same_signed_number(parent['coefficient_difference_from_kinematic_mass'],
            checked['coefficient_difference_from_kinematic_mass'],'coefficient subtraction',
            absolute_roundoff=bounds['coefficient_difference_from_kinematic_mass'])
        # Physical coefficients keep their original relative precision. A
        # caller receives no global absolute epsilon for rates or masses.
        with self.assertRaises(ValueError):same_number(7.344250125485237e-5,7.344250125485237e-5*1.000001,'physical rate')
        self.assertLess(bounds['native_mass_shell_deviation'],1e-16)

    def test_cdf_payload_preserves_native_hash_and_checks_every_node(self):
        from export_dark_photon_cross_sections import same_resolved_meson_model
        def authority(value):
            payload=dict(axes=[0.,.5,1.],cosines=[-1.,1.],values=[[0.,0.],[value,2.],[0.,0.]],loop_order=96)
            digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            return dict(schema='shift-neutral-vector-dipion-positive-cdf-rate-v1',
                settings=dict(cells_s=64),cdf_table_sha256=digest,cdf_table_payload=payload,
                width_over_em_mixing_squared_gev=(value+2)/2)
        native=authority(1.);portable=authority(math.nextafter(1.,2.))
        self.assertNotEqual(native['cdf_table_sha256'],portable['cdf_table_sha256'])
        same_resolved_meson_model(native,portable)
        with self.assertRaises(ValueError):same_resolved_meson_model(native,authority(1.001))
        corrupt=copy.deepcopy(portable);corrupt['cdf_table_payload']['values'][1][0]=3.
        with self.assertRaisesRegex(ValueError,'recorded SHA'):same_resolved_meson_model(native,corrupt)
        legacy={k:v for k,v in native.items() if k!='cdf_table_payload'}
        same_resolved_meson_model(legacy,native)
        with self.assertRaisesRegex(ValueError,'original native payload'):same_resolved_meson_model(legacy,portable)
        closed=dict(native,cdf_table_payload=None)
        with self.assertRaisesRegex(ValueError,'no numerical payload'):same_resolved_meson_model(closed,portable)

    def test_native_cdf_rate_is_retained_after_cross_runtime_validation(self):
        from dark_photon_neutral_vector_dipion import register_native_rate_authority,branching_coefficient
        import dark_photon_neutral_vector_dipion as kernel
        inputs=dict(reference_parent_mass_gev=.78,pion_mass_gev=.135,total_width_gev=.1,
                    em_mixing_over_epsilon_abs2=2.)
        payload=dict(parent_mass_gev=.78,pion_mass_gev=.135,mass_gev=.3,
            axes=[0.,.5,1.],cosines=[-1.,1.],values=[[0.,0.],[1.,2.],[0.,0.]])
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        authority=dict(cdf_table_payload=payload,cdf_table_sha256=digest,width_over_em_mixing_squared_gev=1.5)
        with patch.dict(kernel._AUDITED_NATIVE_RATES,{},clear=True), \
             patch.object(kernel,'_mass_precision',return_value=inputs), \
             patch.object(kernel,'_table',side_effect=AssertionError('Native rate must not be replaced')):
            register_native_rate_authority(inputs,.3,authority)
            self.assertEqual(branching_coefficient(.78,.3,inputs),30.)
            with self.assertRaisesRegex(ValueError,'does not integrate'):
                register_native_rate_authority(inputs,.3,dict(authority,width_over_em_mixing_squared_gev=1.500001))

    def test_one_ulp_libm_difference_preserves_exact_channel_and_model_ownership(self):
        from export_dark_photon_cross_sections import same_resolved_meson_model
        expected = dict(exclusive_channels=[dict(channel='eta_prime_dipion',
            relative_model_uncertainty=0.06211299937499416, kinematically_open=True,
            recoil_pdg_id=211)])
        stored = copy.deepcopy(expected)
        stored['exclusive_channels'][0]['relative_model_uncertainty'] = 0.06211299937499415
        same_resolved_meson_model(stored,expected)
        for key,value in [('channel','eta_gamma'), ('kinematically_open',1),
                          ('relative_model_uncertainty',0.07), ('recoil_pdg_id',211.0)]:
            altered = copy.deepcopy(stored)
            altered['exclusive_channels'][0][key] = value
            with self.subTest(key=key),self.assertRaises(ValueError):
                same_resolved_meson_model(altered,expected)


if __name__ == "__main__":
    unittest.main()
