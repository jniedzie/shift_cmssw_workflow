#!/usr/bin/env python3
"""Draw one selected NanoAOD event on top of the CMS and LSS overview."""

import argparse
import json
import math
from pathlib import Path
import sys

from visualize_lss_geometry import COLORS, LABELS, load_obj


TOPOLOGY_NAMES = {
    0: "near endcap only",
    1: "near endcap and barrel",
    2: "both endcaps",
    3: "far endcap only",
    4: "not classified",
}


def values(event, name, count=None):
    data = getattr(event, name)
    if count is None:
        return [float(value) for value in data]
    return [float(data[index]) for index in range(count)]


def select_event(events):
    """Choose the first valid pair with one both-endcaps muon and one other muon."""
    for entry in range(events.GetEntries()):
        events.GetEntry(entry)
        topologies = [int(events.ShiftMuon_topology[index]) for index in range(int(events.nShiftMuon))]
        for vertex in range(int(events.nShiftDimuonVertex)):
            first = int(events.ShiftDimuonVertex_muonIdx1[vertex])
            second = int(events.ShiftDimuonVertex_muonIdx2[vertex])
            valid = bool(events.ShiftDimuonVertex_kalmanValid[vertex]) or bool(
                events.ShiftDimuonVertex_constrainedValid[vertex]
            )
            if valid and topologies[first] != topologies[second] and 2 in (topologies[first], topologies[second]):
                return entry, vertex, first, second
    raise RuntimeError("no event has the requested two-muon topology and a valid dimuon vertex")


def read_event(path):
    try:
        import ROOT
    except ImportError as error:
        raise RuntimeError("run this script inside the CMSSW environment so it can read NanoAOD") from error
    source = ROOT.TFile.Open(str(path))
    if not source or source.IsZombie():
        raise RuntimeError(f"cannot open {path}")
    events = source.Get("Events")
    if not events:
        raise RuntimeError(f"{path} has no Events tree")
    entry, vertex_index, first, second = select_event(events)
    events.GetEntry(entry)
    gen_count = int(events.nGenPart)
    muon_count = int(events.nShiftMuon)
    gen = []
    for index in range(gen_count):
        gen.append({
            "index": index,
            "pdg_id": int(events.GenPart_pdgId[index]),
            "status": int(events.GenPart_status[index]),
            "start_m": [
                float(events.GenPart_vx[index]) / 100.0,
                float(events.GenPart_vy[index]) / 100.0,
                float(events.GenPart_vz[index]) / 100.0,
            ],
            "momentum": [
                float(events.GenPart_pt[index]) * math.cos(float(events.GenPart_phi[index])),
                float(events.GenPart_pt[index]) * math.sin(float(events.GenPart_phi[index])),
                float(events.GenPart_pz[index]),
            ],
        })
    reco = []
    for index in range(muon_count):
        reco.append({
            "index": index,
            "selected": index in (first, second),
            "topology": int(events.ShiftMuon_topology[index]),
            "gen_index": int(events.ShiftMuon_genPartIdx[index]),
            "points_m": [
                [float(events.ShiftMuon_entryX[index]) / 100.0, float(events.ShiftMuon_entryY[index]) / 100.0,
                 float(events.ShiftMuon_entryZ[index]) / 100.0],
                [float(events.ShiftMuon_exitX[index]) / 100.0, float(events.ShiftMuon_exitY[index]) / 100.0,
                 float(events.ShiftMuon_exitZ[index]) / 100.0],
            ],
        })
    constrained = bool(events.ShiftDimuonVertex_constrainedValid[vertex_index])
    prefix = "constrained" if constrained else ""
    def vertex_value(axis):
        name = f"ShiftDimuonVertex_{prefix + axis[0].upper() + axis[1:] if prefix else axis}"
        return float(getattr(events, name)[vertex_index]) / 100.0
    result = {
        "source": str(path),
        "entry": entry,
        "run": int(events.run),
        "lumi": int(events.luminosityBlock),
        "event": int(events.event),
        "selected_vertex": vertex_index,
        "selected_muons": [first, second],
        "vertex_m": [vertex_value("vx"), vertex_value("vy"), vertex_value("vz")],
        "vertex_kind": "target-constrained fit" if constrained else "dimuon fit",
        "gen_particles": gen,
        "reco_muons": reco,
    }
    source.Close()
    return result


def ray_to_z(particle, z_end=-18.0):
    start = particle["start_m"]
    momentum = particle["momentum"]
    if abs(momentum[2]) < 1e-12:
        scale = 20.0 / max(math.hypot(momentum[0], momentum[1]), 1e-12)
    else:
        scale = (z_end - start[2]) / momentum[2]
    return [start, [start[axis] + scale * momentum[axis] for axis in range(3)]]


def load_geant(path, event_number):
    if path is None:
        return []
    payload = json.loads(path.read_text())
    if int(payload["event"]) != event_number:
        raise RuntimeError(f"Geant4 trace is for event {payload['event']}, not {event_number}")
    # A particle can be killed at its creation point.  Such a one-point record
    # has no line to draw, so leave it out without discarding the whole event.
    return [track for track in payload.get("tracks", [])
            if len(track.get("points_m", [])) >= 2]


def geometry_polygons(meshes, axes):
    grouped = {category: [] for category in COLORS}
    for mesh in meshes:
        grouped[mesh.category].extend([
            [(mesh.vertices[index][axes[0]], mesh.vertices[index][axes[1]]) for index in triangle]
            for triangle in mesh.triangles
        ])
    return grouped


def draw_variant(path, meshes, event, geant_tracks, mode):
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    from matplotlib.lines import Line2D

    definitions = {
        "all_gen": ("All generated particles", True, False, False),
        "geant_stopped": ("Particles followed by Geant4", False, True, False),
        "gen_muons": ("Generated muons", True, False, False),
        "reco_muons": ("Reconstructed muons", False, False, True),
        "gen_vs_reco": ("Generated and reconstructed muons", True, False, True),
        "gen_vs_geant": ("Generated and Geant4 muons", True, True, False),
        "geant_vs_reco": ("Geant4 and reconstructed muons", False, True, True),
    }
    title, show_gen, show_geant, show_reco = definitions[mode]
    if show_geant and not geant_tracks:
        return False
    figure = plt.figure(figsize=(18, 13), facecolor="white")
    grid = figure.add_gridspec(2, 2, height_ratios=(1.0, 1.05), left=0.06, right=0.98,
                              bottom=0.09, top=0.89, wspace=0.13, hspace=0.24)
    all_vertices = [vertex for mesh in meshes for vertex in mesh.vertices]
    full_limits = tuple(
        (min(vertex[axis] for vertex in all_vertices), max(vertex[axis] for vertex in all_vertices))
        for axis in (2, 1)
    )
    panels = ((grid[0, :], (2, 1), "Full path: side view", full_limits),
              (grid[1, 0], (0, 1), "CMS: view along the beam line", ((-18, 18), (-18, 18))),
              (grid[1, 1], (2, 1), "CMS: side view", ((-18, 18), (-18, 18))))
    selected_gen = {event["reco_muons"][index]["gen_index"] for index in event["selected_muons"]}
    for slot, axes, panel_title, limits in panels:
        axis = figure.add_subplot(slot)
        axis.set_facecolor("white")
        for category, polygons in geometry_polygons(meshes, axes).items():
            red, green, blue, alpha = COLORS[category]
            axis.add_collection(PolyCollection(polygons, facecolors=[(red, green, blue, alpha * 0.38)],
                                               edgecolors="none", rasterized=True))
        if show_gen:
            particles = event["gen_particles"]
            if mode != "all_gen":
                particles = [particle for particle in particles if abs(particle["pdg_id"]) == 13 and
                             particle["index"] in selected_gen]
            for particle in particles:
                points = ray_to_z(particle)
                axis.plot([point[axes[0]] for point in points], [point[axes[1]] for point in points],
                          color="#d97706" if abs(particle["pdg_id"]) == 13 else "#64748b",
                          linewidth=2.5 if abs(particle["pdg_id"]) == 13 else 0.65,
                          alpha=0.95 if abs(particle["pdg_id"]) == 13 else 0.45,
                          linestyle="--", zorder=20)
        if show_geant:
            for track in geant_tracks:
                if mode != "geant_stopped" and abs(int(track["pdg_id"])) != 13:
                    continue
                points = track["points_m"]
                axis.plot([point[axes[0]] for point in points], [point[axes[1]] for point in points],
                          color="#0f766e", linewidth=2.4 if abs(int(track["pdg_id"])) == 13 else 0.7,
                          alpha=0.95 if abs(int(track["pdg_id"])) == 13 else 0.5, zorder=22)
                axis.scatter(points[-1][axes[0]], points[-1][axes[1]], marker="x", s=28,
                             color="#0f766e", linewidth=1.3, zorder=23)
        if show_reco:
            for muon in event["reco_muons"]:
                if not muon["selected"]:
                    continue
                points = muon["points_m"]
                axis.plot([point[axes[0]] for point in points], [point[axes[1]] for point in points],
                          color="#b91c1c", linewidth=3.0, zorder=24)
        vertex = event["vertex_m"]
        if show_reco:
            axis.scatter(vertex[axes[0]], vertex[axes[1]], marker="*", s=180, color="#111827",
                         edgecolor="white", linewidth=0.8, zorder=30)
        axis.autoscale_view()
        if limits:
            axis.set_xlim(*limits[0]); axis.set_ylim(*limits[1])
        axis.set_aspect("equal", adjustable="box")
        axis.set_title(panel_title, fontsize=14, color="#172033")
        axis.set_xlabel(("x", "y", "z")[axes[0]] + " [m]")
        axis.set_ylabel(("x", "y", "z")[axes[1]] + " [m]")
        axis.grid(color="#aeb8c6", alpha=0.38, linewidth=0.5)
    figure.suptitle(title, fontsize=21, fontweight="bold", color="#111827", y=0.97)
    first, second = event["selected_muons"]
    topologies = [TOPOLOGY_NAMES[event["reco_muons"][index]["topology"]] for index in (first, second)]
    figure.text(0.5, 0.93,
                f"Event {event['run']}:{event['lumi']}:{event['event']} — {topologies[0]} + {topologies[1]}; "
                f"{event['vertex_kind']} marked with a star", ha="center", color="#374151", fontsize=12)
    legend = []
    if show_gen:
        legend.append(Line2D([0], [0], color="#d97706", linestyle="--", linewidth=2.5,
                             label="generator direction (before material)"))
    if show_geant:
        legend.append(Line2D([0], [0], color="#0f766e", linewidth=2.5,
                             label="Geant4 path; x marks the last recorded point"))
    if show_reco:
        legend.extend([Line2D([0], [0], color="#b91c1c", linewidth=3, label="reconstructed muon"),
                       Line2D([0], [0], marker="*", color="#111827", linewidth=0, markersize=12,
                              label="dimuon vertex")])
    figure.legend(handles=legend, loc="lower center", ncol=len(legend), frameon=False, fontsize=10)
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("geometry_obj", type=Path)
    parser.add_argument("nanoaod", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--geant-tracks", type=Path,
                        help="JSON with Geant4 step points from the same event")
    args = parser.parse_args()
    meshes = load_obj(args.geometry_obj)
    event = read_event(args.nanoaod)
    geant_tracks = load_geant(args.geant_tracks, event["event"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    produced, waiting = [], []
    for mode in ("all_gen", "geant_stopped", "gen_muons", "reco_muons", "gen_vs_reco",
                 "gen_vs_geant", "geant_vs_reco"):
        output = args.output_dir / f"event_{event['event']}_{mode}.png"
        if draw_variant(output, meshes, event, geant_tracks, mode):
            produced.append(output.name)
        else:
            waiting.append(mode)
    manifest = {"event": event, "geant_source": str(args.geant_tracks) if args.geant_tracks else None,
                "produced": produced, "waiting_for_geant_trace": waiting}
    (args.output_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"event": event["event"], "produced": produced, "waiting_for_geant_trace": waiting}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
