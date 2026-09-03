#!/usr/bin/env python3
"""Draw compact Geant4/reconstruction comparisons and a run summary."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

from visualize_lss_geometry import COLORS, load_obj


TOPOLOGY_NAMES = {
    0: "near endcap only", 1: "near endcap and barrel", 2: "both endcaps",
    3: "far endcap only", 4: "not classified",
}
MUON_COLORS = {13: "#0f766e", -13: "#1d4ed8"}
GEN_COLORS = {13: "#d97706", -13: "#9333ea"}


def branch_value(event, name, index, default=0.0):
    if not hasattr(event, name):
        return default
    return float(getattr(event, name)[index])


def read_events(path):
    try:
        import ROOT
    except ImportError as error:
        raise RuntimeError("run inside a CMSSW environment so ROOT can read NanoAOD") from error
    source = ROOT.TFile.Open(str(path))
    if not source or source.IsZombie():
        raise RuntimeError(f"cannot open {path}")
    tree = source.Get("Events")
    if not tree:
        raise RuntimeError(f"{path} has no Events tree")
    result = []
    for entry in range(tree.GetEntries()):
        tree.GetEntry(entry)
        gen_muons = []
        for index in range(int(tree.nGenPart)):
            pdg_id = int(tree.GenPart_pdgId[index])
            if abs(pdg_id) != 13 or int(tree.GenPart_status[index]) != 1:
                continue
            gen_muons.append({
                "index": index,
                "pdg_id": pdg_id,
                "start_m": [float(tree.GenPart_vx[index]) / 100.0,
                            float(tree.GenPart_vy[index]) / 100.0,
                            float(tree.GenPart_vz[index]) / 100.0],
                "momentum": [float(tree.GenPart_pt[index]) * math.cos(float(tree.GenPart_phi[index])),
                             float(tree.GenPart_pt[index]) * math.sin(float(tree.GenPart_phi[index])),
                             float(tree.GenPart_pz[index])],
            })
        reco = []
        for index in range(int(tree.nShiftMuon)):
            reco.append({
                "index": index,
                "topology": int(tree.ShiftMuon_topology[index]),
                "gen_index": int(tree.ShiftMuon_genPartIdx[index]),
                "truth_matched": bool(branch_value(tree, "ShiftMuon_simTruthMatched", index, 0)),
                "detector_points_m": [
                    [float(tree.ShiftMuon_entryX[index]) / 100.0,
                     float(tree.ShiftMuon_entryY[index]) / 100.0,
                     float(tree.ShiftMuon_entryZ[index]) / 100.0],
                    [float(tree.ShiftMuon_exitX[index]) / 100.0,
                     float(tree.ShiftMuon_exitY[index]) / 100.0,
                     float(tree.ShiftMuon_exitZ[index]) / 100.0],
                ],
                "target_point_m": [float(tree.ShiftMuon_vx[index]) / 100.0,
                                   float(tree.ShiftMuon_vy[index]) / 100.0,
                                   float(tree.ShiftMuon_vz[index]) / 100.0],
            })
        vertices = []
        for index in range(int(tree.nShiftDimuonVertex)):
            constrained = bool(tree.ShiftDimuonVertex_constrainedValid[index])
            kalman = bool(tree.ShiftDimuonVertex_kalmanValid[index])
            if not constrained and not kalman:
                continue
            prefix = "constrained" if constrained else ""
            def coordinate(axis):
                suffix = prefix + axis[0].upper() + axis[1:] if prefix else axis
                return float(getattr(tree, "ShiftDimuonVertex_" + suffix)[index]) / 100.0
            vertices.append({
                "index": index,
                "muons": [int(tree.ShiftDimuonVertex_muonIdx1[index]),
                           int(tree.ShiftDimuonVertex_muonIdx2[index])],
                "point_m": [coordinate("vx"), coordinate("vy"), coordinate("vz")],
                "kind": "target-constrained fit" if constrained else "dimuon fit",
            })
        result.append({
            "entry": entry, "run": int(tree.run), "lumi": int(tree.luminosityBlock),
            "event": int(tree.event), "gen_muons": gen_muons, "reco_muons": reco,
            "vertices": vertices,
        })
    source.Close()
    return result


def select_event(events):
    """Prefer the requested topology, then accept any valid mixed pair."""
    fallback = None
    for event in events:
        if len(event["reco_muons"]) != 2:
            continue
        topologies = [muon["topology"] for muon in event["reco_muons"]]
        for vertex in event["vertices"]:
            if sorted(vertex["muons"]) != [0, 1] or topologies[0] == topologies[1]:
                continue
            if 2 in topologies:
                return event, vertex
            if fallback is None:
                fallback = (event, vertex)
    if fallback is not None:
        return fallback
    raise RuntimeError(
        "no processed event has exactly two reconstructed muons with different topologies "
        "and a valid dimuon vertex"
    )


def load_geant(path):
    payload = json.loads(path.read_text())
    if "events" in payload:
        return {int(event["event"]): event["tracks"] for event in payload["events"]}
    return {int(payload["event"]): payload.get("tracks", [])}


def geometry_polygons(meshes, axes):
    grouped = {category: [] for category in COLORS}
    for mesh in meshes:
        grouped[mesh.category].extend([
            [(mesh.vertices[index][axes[0]], mesh.vertices[index][axes[1]]) for index in triangle]
            for triangle in mesh.triangles
        ])
    return grouped


def generator_ray(particle, z_end=-18.0):
    start = particle["start_m"]
    momentum = particle["momentum"]
    scale = ((z_end - start[2]) / momentum[2] if abs(momentum[2]) > 1.e-12
             else 20.0 / max(math.hypot(momentum[0], momentum[1]), 1.e-12))
    return [start, [start[axis] + scale * momentum[axis] for axis in range(3)]]


def muon_name(pdg_id):
    return "mu-" if pdg_id == 13 else "mu+"


def clean_material_name(name):
    short = name.split(":")[-1]
    if short.startswith("G4_"):
        short = short[3:]
    aliases = {
        "Galactic": "vacuum", "AIR": "air", "Air": "air", "Vacuum": "vacuum",
        "EARTHBOH": "earth / rock (EARTHBOH)", "CONCRETE": "concrete",
        "Stand.Concrete": "standard concrete", "SS304L": "stainless steel (SS304L)",
        "COPPERSS": "copper (COPPERSS)", "M_Steel-008": "steel (M Steel-008)",
        "Steel-008": "steel (Steel-008)", "AISI-1018-Steel": "steel (AISI-1018)",
        "M_F_Air": "air", "M_B_Air": "air", "ME_free_space": "air",
    }
    return aliases.get(short, short.replace("_", " "))


def material_text(track):
    rows = track.get("materials", [])
    if not rows:
        return "material totals unavailable"
    combined = Counter()
    for row in rows:
        combined[clean_material_name(row["name"])] += row["fraction"]
    shown = combined.most_common(4)
    parts = [f"{name} {100.0 * fraction:.1f}%" for name, fraction in shown]
    remainder = 1.0 - sum(fraction for _, fraction in shown)
    if remainder > 0.0005:
        parts.append(f"other {100.0 * remainder:.1f}%")
    return " | ".join(parts)


def tracks_by_charge(tracks):
    return {int(track["pdg_id"]): track for track in tracks if abs(int(track["pdg_id"])) == 13}


def draw_event(path, meshes, event, vertex, tracks, mode):
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    from matplotlib.lines import Line2D

    show_gen = mode == "gen_vs_geant"
    show_reco = mode == "geant_vs_reco"
    title = "Generated and Geant4 muons" if show_gen else "Geant4 and reconstructed muons"
    figure = plt.figure(figsize=(18, 13), facecolor="white")
    grid = figure.add_gridspec(2, 2, height_ratios=(1.0, 1.05), left=0.06, right=0.98,
                              bottom=0.09, top=0.84, wspace=0.13, hspace=0.24)
    all_vertices = [point for mesh in meshes for point in mesh.vertices]
    full_limits = tuple((min(point[axis] for point in all_vertices),
                         max(point[axis] for point in all_vertices)) for axis in (2, 1))
    panels = ((grid[0, :], (2, 1), "Full path: side view", full_limits),
              (grid[1, 0], (0, 1), "CMS: view along the beam line", ((-18, 18), (-18, 18))),
              (grid[1, 1], (2, 1), "CMS: side view", ((-18, 18), (-18, 18))))
    charged_tracks = tracks_by_charge(tracks)
    gen_by_index = {particle["index"]: particle for particle in event["gen_muons"]}
    for slot, axes, panel_title, limits in panels:
        axis = figure.add_subplot(slot)
        axis.set_facecolor("white")
        for category, polygons in geometry_polygons(meshes, axes).items():
            red, green, blue, alpha = COLORS[category]
            axis.add_collection(PolyCollection(
                polygons, facecolors=[(red, green, blue, alpha * 0.38)],
                edgecolors="none", rasterized=True))
        if show_gen:
            for particle in event["gen_muons"]:
                points = generator_ray(particle)
                axis.plot([point[axes[0]] for point in points],
                          [point[axes[1]] for point in points],
                          color=GEN_COLORS[particle["pdg_id"]], linewidth=2.3,
                          linestyle="--", zorder=20)
        for pdg_id, track in charged_tracks.items():
            points = track["points_m"]
            if len(points) >= 2:
                axis.plot([point[axes[0]] for point in points],
                          [point[axes[1]] for point in points],
                          color=MUON_COLORS[pdg_id], linewidth=2.5, zorder=22)
                axis.scatter(points[-1][axes[0]], points[-1][axes[1]], marker="x", s=32,
                             color=MUON_COLORS[pdg_id], linewidth=1.4, zorder=23)
        if show_reco:
            for muon in event["reco_muons"]:
                particle = gen_by_index.get(muon["gen_index"])
                pdg_id = particle["pdg_id"] if particle else 13
                detector = muon["detector_points_m"]
                target = muon["target_point_m"]
                axis.plot([point[axes[0]] for point in detector],
                          [point[axes[1]] for point in detector],
                          color="#b91c1c", linewidth=3.2, zorder=25)
                axis.plot([target[axes[0]], detector[0][axes[0]]],
                          [target[axes[1]], detector[0][axes[1]]],
                          color="#b91c1c", linewidth=2.4, linestyle="--", zorder=24)
            point = vertex["point_m"]
            axis.scatter(point[axes[0]], point[axes[1]], marker="*", s=190,
                         color="#111827", edgecolor="white", linewidth=0.8, zorder=30)
        axis.set_xlim(*limits[0]); axis.set_ylim(*limits[1])
        axis.set_aspect("equal", adjustable="box")
        axis.set_title(panel_title, fontsize=14, color="#172033")
        axis.set_xlabel(("x", "y", "z")[axes[0]] + " [m]")
        axis.set_ylabel(("x", "y", "z")[axes[1]] + " [m]")
        axis.grid(color="#aeb8c6", alpha=0.38, linewidth=0.5)

    figure.suptitle(title, fontsize=21, fontweight="bold", color="#111827", y=0.98)
    topologies = [TOPOLOGY_NAMES[muon["topology"]] for muon in event["reco_muons"]]
    point = vertex["point_m"]
    figure.text(0.5, 0.945,
                f"Event {event['run']}:{event['lumi']}:{event['event']} — "
                f"{topologies[0]} + {topologies[1]}; vertex at "
                f"({point[0]:.3f}, {point[1]:.3f}, {point[2]:.3f}) m",
                ha="center", color="#374151", fontsize=12)
    for row, pdg_id in enumerate((13, -13)):
        track = charged_tracks.get(pdg_id)
        text = material_text(track) if track else "no Geant4 track"
        figure.text(0.5, 0.915 - 0.023 * row, f"{muon_name(pdg_id)} path: {text}",
                    ha="center", color=MUON_COLORS[pdg_id], fontsize=10.5)

    legend = []
    if show_gen:
        legend.extend([
            Line2D([0], [0], color=GEN_COLORS[13], linestyle="--", linewidth=2.5,
                   label="generated mu- direction"),
            Line2D([0], [0], color=GEN_COLORS[-13], linestyle="--", linewidth=2.5,
                   label="generated mu+ direction"),
        ])
    legend.extend([
        Line2D([0], [0], color=MUON_COLORS[13], linewidth=2.5,
               label="Geant4 mu- path"),
        Line2D([0], [0], color=MUON_COLORS[-13], linewidth=2.5,
               label="Geant4 mu+ path; x is the last point"),
    ])
    if show_reco:
        legend.extend([
            Line2D([0], [0], color="#b91c1c", linewidth=3, label="reconstructed track in CMS"),
            Line2D([0], [0], color="#b91c1c", linestyle="--", linewidth=2.4,
                   label="reconstructed track propagated back toward the target"),
            Line2D([0], [0], marker="*", color="#111827", linewidth=0,
                   markersize=12, label="dimuon vertex"),
        ])
    figure.legend(handles=legend, loc="lower center", ncol=len(legend), frameon=False, fontsize=10)
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)


def summary_stats(events, geant_events):
    material = Counter()
    transported = reached_cms = 0
    for tracks in geant_events.values():
        for track in tracks:
            if abs(int(track["pdg_id"])) != 13:
                continue
            transported += 1
            if any(abs(point[2]) <= 18.0 and math.hypot(point[0], point[1]) <= 18.0
                   for point in track["points_m"]):
                reached_cms += 1
            for row in track.get("materials", []):
                material[clean_material_name(row["name"])] += row["path_m"]
    reco_counts = Counter(min(len(event["reco_muons"]), 2) for event in events)
    return {
        "events_processed": len(events),
        "generated_muons": sum(len(event["gen_muons"]) for event in events),
        "geant_muons": transported,
        "geant_muons_reaching_cms": reached_cms,
        "reconstructed_muons": sum(len(event["reco_muons"]) for event in events),
        "generator_matched_reconstructed_muons": sum(
            muon["gen_index"] >= 0 for event in events for muon in event["reco_muons"]),
        "events_with_0_reco_muons": reco_counts[0],
        "events_with_1_reco_muon": reco_counts[1],
        "events_with_2_or_more_reco_muons": reco_counts[2],
        "events_with_valid_dimuon_vertex": sum(bool(event["vertices"]) for event in events),
        "material_path_m": dict(material.most_common()),
    }


def draw_summary(path, stats):
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 3, figsize=(18, 8), facecolor="white")
    figure.subplots_adjust(left=0.06, right=0.98, bottom=0.16, top=0.82, wspace=0.30)
    panels = [
        (["generated", "Geant4", "reach CMS", "reco candidates", "gen-matched reco"],
         [stats["generated_muons"], stats["geant_muons"], stats["geant_muons_reaching_cms"],
          stats["reconstructed_muons"], stats["generator_matched_reconstructed_muons"]],
         "Muon counts", "#0f766e"),
        (["0 muons", "1 muon", "2+ muons"],
         [stats["events_with_0_reco_muons"], stats["events_with_1_reco_muon"],
          stats["events_with_2_or_more_reco_muons"]], "Reconstructed muons per event", "#b91c1c"),
    ]
    for axis, (labels, values, title, color) in zip(axes[:2], panels):
        bars = axis.bar(labels, values, color=color, alpha=0.86)
        axis.bar_label(bars, padding=3, fontsize=12)
        axis.set_title(title, fontsize=15)
        axis.set_ylim(0, max(values + [1]) * 1.18)
        axis.grid(axis="y", color="#aeb8c6", alpha=0.35)
        axis.tick_params(axis="x", rotation=18)
    materials = list(stats["material_path_m"].items())
    total_path = sum(value for _, value in materials)
    shown = materials[:6]
    labels = [name for name, _ in shown]
    values = [100.0 * value / total_path if total_path else 0.0 for _, value in shown]
    if len(materials) > 6:
        labels.append("other")
        values.append(max(0.0, 100.0 - sum(values)))
    bars = axes[2].barh(labels[::-1], values[::-1], color="#1d4ed8", alpha=0.82)
    axes[2].bar_label(bars, fmt="%.1f%%", padding=3, fontsize=10)
    axes[2].set_title("Share of all Geant4 muon paths", fontsize=15)
    axes[2].set_xlabel("path length")
    axes[2].set_xlim(0, max(values + [1]) * 1.22)
    axes[2].grid(axis="x", color="#aeb8c6", alpha=0.35)
    figure.suptitle(f"Current LSS test geometry: {stats['events_processed']} event summary",
                    fontsize=22, fontweight="bold", color="#111827")
    figure.text(0.5, 0.875,
                f"{stats['geant_muons_reaching_cms']} of {stats['geant_muons']} Geant4 muons "
                f"reach the CMS-size region; {stats['events_with_valid_dimuon_vertex']} event has "
                "a valid dimuon vertex",
                ha="center", fontsize=13, color="#374151")
    figure.text(0.5, 0.055,
                "Temporary ATLAS-side test model; this is not the final CMS-side LSS geometry.",
                ha="center", fontsize=11, color="#4b5563")
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("geometry_obj", type=Path)
    parser.add_argument("nanoaod", type=Path)
    parser.add_argument("geant_tracks", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    meshes = load_obj(args.geometry_obj)
    events = read_events(args.nanoaod)
    geant_events = load_geant(args.geant_tracks)
    stats = summary_stats(events, geant_events)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_name = f"summary_{len(events)}_events.png"
    draw_summary(args.output_dir / summary_name, stats)
    event, vertex = select_event(events)
    tracks = geant_events.get(event["event"], [])
    if len(tracks_by_charge(tracks)) != 2:
        raise RuntimeError(f"event {event['event']} does not have both Geant4 muon tracks")
    names = []
    for mode in ("gen_vs_geant", "geant_vs_reco"):
        name = f"event_{event['event']}_{mode}.png"
        draw_event(args.output_dir / name, meshes, event, vertex, tracks, mode)
        names.append(name)
    manifest = {
        "geometry": "temporary ATLAS-side test model; not the final CMS-side LSS geometry",
        "nanoaod": str(args.nanoaod), "geant_tracks": str(args.geant_tracks),
        "selected_event": {"run": event["run"], "lumi": event["lumi"],
                           "event": event["event"], "topologies": [
                               TOPOLOGY_NAMES[muon["topology"]] for muon in event["reco_muons"]],
                           "vertex_m": vertex["point_m"], "vertex_kind": vertex["kind"]},
        "outputs": names + [summary_name], "stats": stats,
    }
    (args.output_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
