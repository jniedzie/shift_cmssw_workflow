#!/usr/bin/env python3
"""Validate paired nominal, fractional-phase and integer-BX GEN-SIM files."""

import argparse

from DataFormats.FWLite import Events, Handle


SUBDETECTORS = ("MuonCSCHits", "MuonRPCHits", "MuonGEMHits", "MuonDTHits")


def _scalar(event, instance, type_name="double"):
    handle = Handle(type_name)
    if not event.getByLabel("shiftEventTime", instance, handle):
        raise RuntimeError(f"missing shiftEventTime:{instance}")
    return handle.product()[0]


def _iterator_values(begin, end, convert):
    values = []
    iterator = begin
    while iterator != end:
        values.append(convert(iterator.__deref__()))
        iterator.__preinc__()
    return values


def _load(path):
    event = next(iter(Events(path)))
    hepmc_handle = Handle("edm::HepMCProduct")
    if not event.getByLabel("shiftEventTime", hepmc_handle):
        raise RuntimeError(f"missing shifted HepMC product in {path}")
    hepmc = hepmc_handle.product().GetEvent()
    vertices = _iterator_values(
        hepmc.vertices_begin(),
        hepmc.vertices_end(),
        lambda vertex: (
            vertex.position().x(),
            vertex.position().y(),
            vertex.position().z(),
            vertex.position().t(),
        ),
    )
    particles = _iterator_values(
        hepmc.particles_begin(),
        hepmc.particles_end(),
        lambda particle: (
            particle.barcode(),
            particle.pdg_id(),
            particle.momentum().px(),
            particle.momentum().py(),
            particle.momentum().pz(),
            particle.momentum().e(),
        ),
    )
    hits = {}
    for subdetector in SUBDETECTORS:
        handle = Handle("std::vector<PSimHit>")
        if not event.getByLabel("g4SimHits", subdetector, handle):
            raise RuntimeError(f"missing g4SimHits:{subdetector} in {path}")
        hits[subdetector] = [
            (
                hit.detUnitId(),
                hit.trackId(),
                hit.particleType(),
                hit.timeOfFlight(),
                hit.entryPoint().x(),
                hit.entryPoint().y(),
                hit.entryPoint().z(),
                hit.exitPoint().x(),
                hit.exitPoint().y(),
                hit.exitPoint().z(),
            )
            for hit in handle.product()
            if abs(hit.particleType()) == 13
        ]
    return {
        "source_z_mm": _scalar(event, "sourceZmm"),
        "source_ct_before_mm": _scalar(event, "sourceCtBeforeMm"),
        "shift_ct_mm": _scalar(event, "appliedShiftCtMm"),
        "shift_ns": _scalar(event, "appliedShiftNs"),
        "vertices": vertices,
        "particles": particles,
        "hits": hits,
    }


def _compare(name, nominal, shifted, expected_ns):
    if len(nominal["vertices"]) != len(shifted["vertices"]):
        raise AssertionError(f"{name}: vertex count changed")
    if len(nominal["particles"]) != len(shifted["particles"]):
        raise AssertionError(f"{name}: particle count changed")

    max_spatial_delta = max(
        abs(before[index] - after[index])
        for before, after in zip(nominal["vertices"], shifted["vertices"])
        for index in range(3)
    )
    max_momentum_delta = max(
        abs(before[index] - after[index])
        for before, after in zip(nominal["particles"], shifted["particles"])
        for index in range(2, 6)
    )
    vertex_ct_deltas = [
        after[3] - before[3]
        for before, after in zip(nominal["vertices"], shifted["vertices"])
    ]
    expected_ct_mm = expected_ns * 299.792458
    max_vertex_time_residual = max(
        abs(delta - expected_ct_mm) for delta in vertex_ct_deltas
    )
    if max_spatial_delta != 0.0 or max_momentum_delta != 0.0:
        raise AssertionError(f"{name}: timing changed spatial vertices or momenta")
    if max_vertex_time_residual > 1.0e-8:
        raise AssertionError(f"{name}: inconsistent HepMC common time shift")

    print(
        f"{name}: HepMC spatial delta={max_spatial_delta:g} mm, "
        f"momentum delta={max_momentum_delta:g} GeV, "
        f"ct delta={vertex_ct_deltas[0]:.9f} mm"
    )
    for subdetector in SUBDETECTORS:
        before_hits = nominal["hits"][subdetector]
        after_hits = shifted["hits"][subdetector]
        if len(before_hits) != len(after_hits):
            raise AssertionError(f"{name}: {subdetector} hit count changed")
        same_non_time_fields = all(
            before[:3] + before[4:] == after[:3] + after[4:]
            for before, after in zip(before_hits, after_hits)
        )
        if not same_non_time_fields:
            raise AssertionError(f"{name}: {subdetector} hit identity/position changed")
        residuals = [
            abs((after[3] - before[3]) - expected_ns)
            for before, after in zip(before_hits, after_hits)
        ]
        max_residual = max(residuals, default=0.0)
        if max_residual > 3.0e-6:
            raise AssertionError(f"{name}: {subdetector} time shift mismatch")
        crossing = "not crossed" if not before_hits else f"{len(before_hits)} muon hits"
        print(
            f"  {subdetector}: {crossing}, max time residual={max_residual:.3g} ns"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("nominal")
    parser.add_argument("phase")
    parser.add_argument("bx")
    parser.add_argument("--phase-ns", type=float, required=True)
    parser.add_argument("--bx-ns", type=float, required=True)
    args = parser.parse_args()

    nominal = _load(args.nominal)
    phase = _load(args.phase)
    bx = _load(args.bx)
    print(
        "nominal: "
        f"sourceZ={nominal['source_z_mm']:.9f} mm, "
        f"sourceCtBefore={nominal['source_ct_before_mm']:.9f} mm, "
        f"shift={nominal['shift_ct_mm']:.9f} mm "
        f"({nominal['shift_ns']:.9f} ns)"
    )
    _compare("phase", nominal, phase, args.phase_ns)
    _compare("bx", nominal, bx, args.bx_ns)


if __name__ == "__main__":
    main()
