#!/usr/bin/env python3
"""Store compact primary-muon paths and material totals from a Geant4 log."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re


LINE = re.compile(
    r"\[ShiftEventDisplay\]\[G4Point\] event=(?P<event>-?\d+) track_id=(?P<track>\d+) "
    r"pdg_id=(?P<pdg>-?\d+) step=(?P<step>\d+) position_mm=\("
    r"(?P<x>[-+0-9.eE]+),(?P<y>[-+0-9.eE]+),(?P<z>[-+0-9.eE]+)\) "
    r"kinetic_energy_GeV=(?P<energy>[-+0-9.eE]+)"
    r"(?: step_length_mm=(?P<length>[-+0-9.eE]+) material=(?P<material>\S+))? "
    r"process=(?P<process>\S+)"
)


def compact_points(points, maximum):
    """Keep endpoints and evenly spaced drawing points without changing totals."""
    if len(points) <= maximum:
        return points
    indices = {round(index * (len(points) - 1) / (maximum - 1)) for index in range(maximum)}
    return [points[index] for index in sorted(indices)]


def material_rows(lengths):
    total = sum(lengths.values())
    return [
        {"name": name, "path_m": length_mm / 1000.0,
         "fraction": length_mm / total if total > 0.0 else 0.0}
        for name, length_mm in sorted(lengths.items(), key=lambda item: (-item[1], item[0]))
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--g4-event", type=int, help="store only this Geant4 event number")
    parser.add_argument("--event", type=int, help="EDM event number to use with --g4-event")
    parser.add_argument("--max-points", type=int, default=500,
                        help="maximum saved drawing points per muon")
    args = parser.parse_args()
    if (args.g4_event is None) != (args.event is None):
        parser.error("--g4-event and --event must be used together")
    if args.max_points < 2:
        parser.error("--max-points must be at least 2")

    tracks = defaultdict(lambda: {
        "pdg_id": None, "points_m": [], "steps": 0, "path_mm": 0.0,
        "materials_mm": defaultdict(float), "initial_energy_GeV": None,
        "final_energy_GeV": None, "last_process": None,
    })
    for line in args.log.read_text(errors="replace").splitlines():
        match = LINE.search(line)
        if not match:
            continue
        g4_event = int(match.group("event"))
        if args.g4_event is not None and g4_event != args.g4_event:
            continue
        track = tracks[(g4_event, int(match.group("track")))]
        track["pdg_id"] = int(match.group("pdg"))
        if abs(track["pdg_id"]) != 13:
            continue
        track["points_m"].append(
            [float(match.group(axis)) / 1000.0 for axis in ("x", "y", "z")]
        )
        track["steps"] += 1
        energy = float(match.group("energy"))
        if track["initial_energy_GeV"] is None:
            track["initial_energy_GeV"] = energy
        track["final_energy_GeV"] = energy
        if match.group("length") is not None:
            length_mm = float(match.group("length"))
            track["path_mm"] += length_mm
            track["materials_mm"][match.group("material")] += length_mm
        track["last_process"] = match.group("process")

    events = defaultdict(list)
    for (g4_event, track_id), track in sorted(tracks.items()):
        if not track["points_m"]:
            continue
        event_number = args.event if args.g4_event is not None else g4_event
        events[event_number].append({
            "track_id": track_id,
            "pdg_id": track["pdg_id"],
            "points_m": compact_points(track["points_m"], args.max_points),
            "recorded_steps": track["steps"],
            "path_m": track["path_mm"] / 1000.0,
            "materials": material_rows(track["materials_mm"]),
            "initial_energy_GeV": track["initial_energy_GeV"],
            "final_energy_GeV": track["final_energy_GeV"],
            "last_process": track["last_process"],
        })
    if not events:
        raise RuntimeError("no primary-muon Geant4 trace points found")
    payload = {
        "format_version": 2,
        "point_storage": f"at most {args.max_points} evenly spaced points per muon; material totals use every step",
        "events": [{"event": event, "tracks": event_tracks}
                   for event, event_tracks in sorted(events.items())],
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    track_count = sum(len(event["tracks"]) for event in payload["events"])
    point_count = sum(len(track["points_m"]) for event in payload["events"] for track in event["tracks"])
    print(f"events={len(payload['events'])} muons={track_count} stored_points={point_count}")


if __name__ == "__main__":
    main()
