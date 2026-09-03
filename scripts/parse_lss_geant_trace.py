#!/usr/bin/env python3
"""Convert ShiftEventDisplay Geant4 log lines to the event-view JSON format."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re


LINE = re.compile(
    r"\[ShiftEventDisplay\]\[G4Point\] event=(?P<event>-?\d+) track_id=(?P<track>\d+) "
    r"pdg_id=(?P<pdg>-?\d+) step=(?P<step>\d+) position_mm=\("
    r"(?P<x>[-+0-9.eE]+),(?P<y>[-+0-9.eE]+),(?P<z>[-+0-9.eE]+)\) "
    r"kinetic_energy_GeV=(?P<energy>[-+0-9.eE]+) process=(?P<process>\S+)"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--g4-event", type=int, required=True,
                        help="Geant4 event number exactly as printed in the log")
    parser.add_argument("--event", type=int, required=True,
                        help="EDM event number used by the event visualization")
    args = parser.parse_args()
    tracks = defaultdict(lambda: {"pdg_id": None, "points_m": [], "steps": [], "last_process": None})
    for line in args.log.read_text(errors="replace").splitlines():
        match = LINE.search(line)
        if not match or int(match.group("event")) != args.g4_event:
            continue
        track = tracks[int(match.group("track"))]
        track["pdg_id"] = int(match.group("pdg"))
        track["points_m"].append([float(match.group(axis)) / 1000.0 for axis in ("x", "y", "z")])
        track["steps"].append(int(match.group("step")))
        track["last_process"] = match.group("process")
    payload = {
        "event": args.event,
        "g4_event": args.g4_event,
        "tracks": [{"track_id": track_id, **track} for track_id, track in sorted(tracks.items())],
    }
    if not payload["tracks"]:
        raise RuntimeError(f"no Geant4 trace points found for event {args.g4_event}")
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"tracks={len(payload['tracks'])} points={sum(len(track['points_m']) for track in payload['tracks'])}")


if __name__ == "__main__":
    main()
