#!/usr/bin/env python3
# Code style: AI-code. AI-generated durable dark-photon rate and GEN metadata exporter.
"""Retain audited GEN cross sections without changing a production deployment.

Ordinary plotting does not use this audit. This offline exporter preserves
full-precision per-run rates and denominators for later yield calculations.
It never submits, edits a sealed deployment, overwrites a different artifact,
or combines independent cross-section estimates without their trial weights.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

from pythia_generation_ledger import read_pythia_generation_ledger


SCHEMA = "shift-dark-photon-cross-sections-v1"
SAFE_NAME = re.compile(r"[A-Za-z0-9_-]+")


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def canonical_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def retain_bytes(path, payload):
    """Create once, or verify a byte-identical previous export."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("Refuse to overwrite different retained metadata: " + str(path))
    else:
        with path.open("xb") as stream:
            stream.write(payload)


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Require positive finite " + name)
    return float(value)


def validate_rates(rows):
    if not rows:
        raise ValueError("Missing authoritative GEN run cross sections")
    result = []
    for row in rows:
        value = positive(row["value_pb"], "cross section in pb")
        error = row["error_pb"]
        if isinstance(error, bool) or not isinstance(error, (int, float)) or not math.isfinite(error) or error < 0:
            raise ValueError("Require finite nonnegative cross-section error")
        result.append(dict(value_pb=value, error_pb=float(error)))
    return result


def dimuon_branching(table):
    if "br_mumu" in table:
        value = table["br_mumu"]
    else:
        value = math.fsum(row["branching_fraction"] for row in table["channels"]
                          if sorted(row["products"]) == [-13, 13])
    value = positive(value, "dimuon branching fraction")
    if value > 1:
        raise ValueError("Branching fraction exceeds one")
    return value


def rate_record(contract, audit, *, point, campaign, source_hashes, gen_path, gen_sha256):
    """Keep proposal and physical dimuon rates distinct; correct exactly once."""
    if contract.get("schema") != "shift-dark-photon-gen-v1" or contract.get("decay_mode") != "mumu":
        raise ValueError("Exporter currently requires the audited dimuon GEN contract")
    if audit.get("runtime_validated") is not True or contract.get("filtering") != "none":
        raise ValueError("Require an unfiltered, completed GEN runtime audit")
    epsilon = positive(abs(contract["epsilon"]), "physical epsilon")
    scale = positive(contract["sampling_amplitude_scale"], "proposal amplitude scale")
    if scale < 1:
        raise ValueError("Proposal amplitude cannot be smaller than the physical amplitude")
    physical_br = dimuon_branching(contract["width_authority"])
    native_br = dimuon_branching(contract["sampling_width_authority"])
    amplitude = 1 / scale**2
    # Spectral-width adapters state their BR replacement explicitly. Native
    # common-amplitude pilots have the same BR, apart from roundoff.
    if "cross_section_correction_components" in contract:
        components = contract["cross_section_correction_components"]
        branching = physical_br / native_br
        for name, expected in [("common_amplitude", amplitude), ("branching_ratio", branching)]:
            if not math.isclose(positive(components[name], name), expected, rel_tol=1e-10):
                raise ValueError("Cross-section correction component changed: " + name)
    else:
        if not math.isclose(physical_br, native_br, rel_tol=1e-6):
            raise ValueError("Different physical/native branching fractions require an explicit correction")
        branching = 1.
    combined = amplitude * branching
    if not math.isclose(positive(contract["production_rate_correction"], "rate correction"), combined, rel_tol=1e-10):
        raise ValueError("Physical rate correction does not match its amplitude/BR factors")
    native = validate_rates(audit["native_cross_sections"])
    physical = validate_rates(audit["target_cross_sections"])
    if len(native) != len(physical):
        raise ValueError("Native and physical GEN run counts differ")
    for proposal, target in zip(native, physical):
        for key in ("value_pb", "error_pb"):
            if not math.isclose(proposal[key]*combined, target[key], rel_tol=1e-10, abs_tol=0.):
                raise ValueError("Physical GEN run rate does not apply the correction exactly once")
    events = audit["events"]
    if type(events) is not int or events <= 0 or events != contract["requested_events"]:
        raise ValueError("Full accepted GEN denominator differs from its contract")
    if len(audit["event_ids"]) != events or len(set(map(tuple, audit["event_ids"]))) != events:
        raise ValueError("Full GEN identity ledger is incomplete or duplicated")
    sumw = positive(audit["sum_weights"], "full GEN sum of weights")
    sumw2 = positive(audit["sum_weights_squared"], "full GEN sum of squared weights")
    if sumw != events or sumw2 != events:
        raise ValueError("Native dark-photon GEN requires unit weights for every accepted event")
    ledger = audit["proposal_ledger"]
    if ledger["n_accepted"] != events or not ledger["n_tried"] >= ledger["n_selected"] >= events:
        raise ValueError("Pythia trial ledger differs from the full GEN denominator")
    printed_pb = positive(ledger["printed_cross_section_mb"], "printed generator rate") * 1e9
    # Four-significant-digit printed statistics are diagnostic, never the
    # rate authority. This still catches mb/pb or proposal/target mixups.
    if len(native) == 1 and not math.isclose(native[0]["value_pb"], printed_pb, rel_tol=1e-3):
        raise ValueError("Native GenRunInfo rate differs from the printed mb-to-pb diagnostic")
    identity_digest = hashlib.sha256(json.dumps(audit["event_ids"], separators=(",", ":")).encode()).hexdigest()
    return dict(schema=SCHEMA, point=point, campaign=campaign,
        mass_gev=contract["mass_gev"], epsilon=contract["epsilon"],
        sampling_epsilon=epsilon*scale, proper_ctau_mm=contract["width_authority"]["ctau_mm"],
        physical_br_mumu=physical_br, native_proposal_br_mumu=native_br,
        corrections=dict(common_amplitude=amplitude, branching_ratio=branching, combined=combined,
                         applications=1, extra_decay_branching_fraction_applied=False,
                         extra_detector_or_generator_filter_efficiency_applied=False),
        native_selected_cross_sections_pb=native, physical_selected_cross_sections_pb=physical,
        physical_inclusive_cross_sections_pb=[dict(value_pb=row["value_pb"]/physical_br,
            error_pb=row["error_pb"]/physical_br) for row in physical],
        inclusive_rate_derivation="Selected physical dimuon rate divided by physical BR_mumu; same pure-DY model and generated mass support",
        cross_section_authority="GEN Runs: generator/GenRunInfoProduct/internalXSec; units pb",
        cross_section_convention=contract["cross_section_convention"],
        full_gen=dict(accepted_events=events, sum_weights=sumw, sum_weights_squared=sumw2,
            event_ids_sha256=identity_digest, event_ids_file="metadata/GEN/validation.json",
            n_tried=ledger["n_tried"], n_selected=ledger["n_selected"], n_accepted=ledger["n_accepted"],
            full_gen_denominator=audit["full_gen_denominator"],
            interpretation="Pythia internal trials are not proton collisions or effective luminosity"),
        proposal_ledger=ledger, physical_width_authority=contract["width_authority"],
        native_proposal_width_authority=contract["sampling_width_authority"],
        mechanism=contract["mechanism"], model=contract["model"],
        generated_mass_support_gev=contract["generated_mass_support_gev"],
        beam_energy_GeV=contract["beam_energy_GeV"], source_model=contract["source_model"],
        seed=contract["seed"], run_number=contract["run_number"],
        rate_scope=audit["rate_scope"], runtime_validated=True, physics_valid=False,
        normalization_ready=False, production_rate_validated=False,
        low_mass_mechanisms_included=contract["low_mass_mechanisms_included"],
        prompt_interference_included=contract["prompt_interference_included"],
        gen_path=gen_path, gen_sha256=gen_sha256, source_sha256=source_hashes,
        exporter_sha256=sha256(__file__))


def input_files(directory, manifest):
    """Retain source/config/log authorities and spectral inputs; no ROOT copies."""
    directory = Path(directory)
    names = {"contract.json", "manifest.json", "validation.json", "cmsRun.log", "resolved_cfg.py", "audit.log"}
    for name, expected in manifest.get("source_sha256", {}).items():
        path = directory / name
        if not path.is_file() or sha256(path) != expected:
            raise ValueError("Frozen generator source changed: " + str(path))
        names.add(name)
    for relative in ("audit_revisions", "width_inputs"):
        for path in (directory / relative).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                names.add(str(path.relative_to(directory)))
    return sorted(names)


def publish_file(source, destination):
    """Publish a small sidecar on mounted EOS and hash its readback."""
    source, destination = Path(source), Path(destination)
    if not str(destination).startswith("/eos/user/") or ".." in destination.parts:
        raise ValueError("Publication requires a canonical /eos/user destination")
    expected = sha256(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256(destination) != expected:
            raise ValueError("Refuse to overwrite a different published sidecar: " + str(destination))
    else:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
    actual = sha256(destination)
    if actual != expected or destination.stat().st_size != source.stat().st_size:
        raise ValueError("Published sidecar failed SHA-256/size readback: " + str(destination))
    return dict(path=str(destination), bytes=destination.stat().st_size,
                sha256=expected, readback_sha256=actual)


def export_plan(plan_path, output, *, publish=False):
    plan_path, output = Path(plan_path).resolve(), Path(output).resolve()
    plan = read(plan_path)
    campaign = plan["campaign"]
    if not SAFE_NAME.fullmatch(campaign):
        raise ValueError("Unsafe production campaign name")
    inventory_path = plan_path.with_name("gen_inventory.json")
    inventory = read(inventory_path)
    if inventory.get("complete") is not True:
        raise ValueError("Require the completed full GEN inventory")
    publication_path = plan_path.with_name("gen_publication.json")
    publication = read(publication_path) if publication_path.exists() else None
    published = {row["point"]: row for row in publication["inputs"]} if publication else {}
    records = []
    for row in plan["points"]:
        name = row["point"]
        if not SAFE_NAME.fullmatch(name):
            raise ValueError("Unsafe signal point name")
        gen = Path(row["gen_directory"])
        parent = inventory["points"][name]
        contract, audit, manifest = [read(gen / filename) for filename in ("contract.json", "validation.json", "manifest.json")]
        source_hashes = {filename: sha256(gen / filename) for filename in ("contract.json", "validation.json", "manifest.json", "cmsRun.log")}
        for filename, key in [("contract.json", "contract_sha256"), ("validation.json", "validation_sha256"), ("manifest.json", "manifest_sha256")]:
            if parent[key] != source_hashes[filename]:
                raise ValueError("Pinned production GEN metadata changed: " + name + "/" + filename)
        if (audit["event_ids"] != parent["event_ids"] or audit["sum_weights"] != parent["native_sumw"] or
                audit["sum_weights_squared"] != parent["native_sumw2"] or manifest["output_sha256"] != parent["gen_sha256"]):
            raise ValueError("Production GEN population differs from its retained audit")
        fresh_ledger = read_pythia_generation_ledger(gen / "cmsRun.log", expected_accepted=audit["events"])
        if fresh_ledger != audit["proposal_ledger"]:
            raise ValueError("Generator log differs from the retained trial ledger")
        if publication:
            proof = published[name]
            if (publication.get("complete") is not True or proof["sha256"] != parent["gen_sha256"] or
                    proof["readback_sha256"] != parent["gen_sha256"] or proof["path"] != parent["gen_path"]):
                raise ValueError("Canonical GEN publication differs from its retained readback")
        elif sha256(parent["gen_path"]) != parent["gen_sha256"]:
            raise ValueError("Retained local GEN differs from its full audit")
        record = rate_record(contract, audit, point=name, campaign=campaign,
            source_hashes=source_hashes, gen_path=parent["gen_path"], gen_sha256=parent["gen_sha256"])
        base = Path(row["final_output_base"])
        if base.name != campaign or base.parent.name != "darkphoton_" + name:
            raise ValueError("Point publication directory does not match its campaign")
        stage = output / "points" / campaign / name
        retained = []
        for filename in input_files(gen, manifest):
            source = gen / filename
            local = stage / "metadata" / "GEN" / filename
            retain_bytes(local, source.read_bytes())
            retained.append(dict(local_path=str(local), path=str(base / "metadata" / "GEN" / filename),
                                 bytes=local.stat().st_size, sha256=sha256(local)))
        record["retained_gen_metadata"] = [dict(path=item["path"], bytes=item["bytes"], sha256=item["sha256"]) for item in retained]
        rate_path = stage / "cross_sections.json"
        retain_bytes(rate_path, canonical_bytes(record))
        # The usual two-column rate/error representation is diagnostic for
        # multi-run files: each row remains distinct, never blindly summed.
        text = "# physical selected dimuon cross section [pb], statistical error [pb]\n"
        text += "".join(f"{item['value_pb']:.17g} {item['error_pb']:.17g}\n" for item in record["physical_selected_cross_sections_pb"])
        retain_bytes(stage / "cross_sections.txt", text.encode())
        artifacts = retained + [dict(local_path=str(stage / filename), path=str(base / filename),
            bytes=(stage / filename).stat().st_size, sha256=sha256(stage / filename))
            for filename in ("cross_sections.txt", "cross_sections.json")]
        point_receipt = dict(point=name, campaign=campaign, rate_path=str(base / "cross_sections.json"),
                             record=record, artifacts=artifacts, published=False)
        if publish:
            point_receipt["publication_readbacks"] = [publish_file(item["local_path"], item["path"]) for item in artifacts]
            point_receipt["published"] = True
            retain_bytes(stage / "publication_receipt.json", canonical_bytes(point_receipt))
            point_receipt["receipt_publication"] = publish_file(stage / "publication_receipt.json", base / "metadata" / "GEN" / "cross_sections_publication.json")
        records.append(point_receipt)
        print(json.dumps(dict(point=name, campaign=campaign, sigma_mumu_pb=record["physical_selected_cross_sections_pb"][0]["value_pb"], published=publish)), flush=True)
    receipt = dict(schema="shift-dark-photon-rate-retention-v1", complete=True, published=publish,
        campaign=campaign, plan_path=str(plan_path), plan_sha256=sha256(plan_path),
        gen_inventory_path=str(inventory_path), gen_inventory_sha256=sha256(inventory_path), points=records,
        source_deployments_unchanged=True)
    retain_bytes(output / (campaign + ("_published" if publish else "_prepared") + ".json"), canonical_bytes(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publish", action="store_true", help="Publish new rate/GEN sidecars on mounted EOS with SHA-256 readback")
    args = parser.parse_args()
    for path in args.plan:
        export_plan(path, args.output, publish=args.publish)


if __name__ == "__main__":
    main()
