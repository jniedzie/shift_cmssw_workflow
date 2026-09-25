#!/usr/bin/env python3
"""Inspect only freshly generated pilot products; never reads reconstruction."""

from collections import Counter
import json
import math
from pathlib import Path
import sys

from DataFormats.FWLite import Events, Handle, Runs


def product(event, label, typename):
    handle = Handle(typename)
    event.getByLabel(label, handle)
    if not handle.isValid():
        raise RuntimeError(f"Missing {label} ({typename})")
    return handle.product()


def main(directory):
    out = Path(directory)
    contract = json.loads((out / "contract.json").read_text())
    if contract["schema"] != "shift-gen-pilot-v1" or contract["detector_simulated"]:
        raise ValueError("Only GEN pilot products are accepted")
    path = str(out / "gen.root")
    sample = contract["sample"]
    parent_ids = {"chic": {10441, 20443, 445}, "psi2s": {100443}}
    expected_codes = {"chic": set(range(411, 417)),
                      "psi2s": set(range(401, 411)) | {441}}
    counts, codes = Counter(), Counter()
    identities = set()
    weights = []
    maximum_timing_error = 0.
    beam_summary = None
    for event in Events(path):
        identity = (event.eventAuxiliary().run(), event.eventAuxiliary().event())
        if identity in identities:
            raise AssertionError("Duplicate event identity")
        identities.add(identity)
        info = product(event, "generator", "GenEventInfoProduct")
        weights.append(float(info.weight()))
        code = int(info.signalProcessID())
        if sample in expected_codes and code not in expected_codes[sample]:
            raise AssertionError(f"Unexpected feed-down hard process: {code}")
        codes[str(code)] += 1
        hepmc = product(event, ("generator", "unsmeared"), "edm::HepMCProduct").GetEvent()
        beams = hepmc.beam_particles()
        a, b = beams.first.momentum(), beams.second.momentum()
        if (abs(a.pz()) > 1.e-6 or abs(a.e() - 0.938272) > 1.e-4
                or abs(b.e() - contract["beam_energy_GeV"]) > 1.e-4 or b.pz() >= 0):
            raise AssertionError("Generated beams do not describe the fixed-target Beam-B configuration")
        beam_summary = dict(target_energy_GeV=a.e(), projectile_energy_GeV=b.e(),
                            projectile_pz_GeV=b.pz(),
                            sqrt_s_GeV=math.sqrt((a.e()+b.e())**2 - (a.pz()+b.pz())**2))
        source_z = product(event, ("shiftEventTime", "sourceZmm"), "double")[0]
        shift = product(event, ("shiftEventTime", "appliedShiftCtMm"), "double")[0]
        if not math.isfinite(source_z) or source_z <= 0:
            raise AssertionError("Invalid source placement")
        maximum_timing_error = max(maximum_timing_error, abs(shift + source_z))
        particles = product(event, "genParticles", "std::vector<reco::GenParticle>")
        counts["events"] += 1
        if sample in parent_ids and not any(abs(p.pdgId()) in parent_ids[sample] for p in particles):
            raise AssertionError(f"Missing generated {sample} parent")
        for p in particles:
            if p.status() == 1:
                counts[f"stable_abs_pdg_{abs(p.pdgId())}"] += 1
            if abs(p.pdgId()) in (443, 100443, 10441, 20443, 445):
                counts[f"charmonium_record_abs_pdg_{abs(p.pdgId())}"] += 1
    if counts["events"] != contract["requested_events"] or maximum_timing_error > 1.e-7:
        raise AssertionError("Event count or physical time mismatch")
    if not all(math.isfinite(w) for w in weights):
        raise AssertionError("Nonfinite generator weights")
    run_info = []
    for run in Runs(path):
        info = product(run, "generator", "GenRunInfoProduct")
        xsec = info.internalXSec()
        if not math.isfinite(xsec.value()) or xsec.value() <= 0:
            raise AssertionError("Missing positive generated cross section")
        run_info.append({"internal_xsec_pb": xsec.value(), "error_pb": xsec.error()})
    if not run_info:
        raise AssertionError("Missing run products")
    report = dict(counts=counts, hard_process_codes=codes, sum_weights=sum(weights),
        sum_weights_squared=sum(w*w for w in weights), run_info=run_info,
        maximum_timing_error_mm=maximum_timing_error,
        beams=beam_summary,
        generator_filter="none", physics_valid=False, normalization_ready=False,
        caveat="Cross-section/forced-decay convention and source/overlap validation remain open")
    (out / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print("GEN products, event identities, weights and physical timing validated")


if __name__ == "__main__":
    main(sys.argv[1])
