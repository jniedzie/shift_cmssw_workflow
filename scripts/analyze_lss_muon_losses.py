#!/usr/bin/env python3
"""Classify captured primary-muon fates and summarize evidence for losses."""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path

PREFIX = re.compile(r"\[FixedTargetMuonDebug\]\[(?P<source>[^]]+)\]\s+(?P<body>.*)")
POINT = re.compile(r"\[ShiftEventDisplay\]\[G4Point\]\s+(?P<body>.*)")
FIELD = re.compile(r"(?P<key>[A-Za-z0-9_]+)=(?P<value>\([^)]*\)|[^\s]+)")

def parse_fields(body):
    return {m.group("key"): m.group("value") for m in FIELD.finditer(body)}

def vec(value):
    try:
        x = tuple(float(v) for v in value[1:-1].split(","))
        return x if len(x) == 3 else None
    except (AttributeError, TypeError, ValueError):
        return None

def angle(a, b):
    if not a or not b:
        return None
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(x*x for x in b))
    if na == 0 or nb == 0:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(x*y for x,y in zip(a,b))/(na*nb)))))

def before_cms(start, end, points, entrance_abs_z_mm):
    """Summarize transport up to the first inward entrance-plane crossing.

    This plane is a transport milestone, not a detector acceptance test.
    A missing trace is unknown, never proof that a particle missed CMS.
    """
    origin = vec(start.get("position_mm"))
    if origin is None or not points:
        return {"cms_plane_reached": None, "upstream_rock_path_m": None}
    side = 1 if origin[2] >= 0 else -1
    previous = origin
    materials = defaultdict(float)
    reached = side * origin[2] <= entrance_abs_z_mm
    crossing = None
    for point in points:
        if reached:
            break
        position = vec(point.get("position_mm"))
        if position is None:
            raise ValueError("trace point without a valid position")
        fraction = 1.0
        if side * position[2] <= entrance_abs_z_mm:
            fraction = (side * previous[2] - entrance_abs_z_mm) / (side * (previous[2] - position[2]))
            crossing = [a + fraction * (b-a) for a, b in zip(previous, position)]
            reached = True
        materials[point.get("material", "unknown")] += fraction * float(point.get("step_length_mm", 0.0)) / 1000.0
        previous = position
    terminal = vec(end.get("position_mm"))
    # The current trace hook omits the killing step. Never classify a final
    # point beyond the entrance as an upstream stop solely from trace absence.
    terminal_beyond = terminal is not None and side * terminal[2] <= entrance_abs_z_mm
    energy = float(end.get("kinetic_energy_GeV", "inf"))
    return {
        "cms_plane_reached": True if reached else (None if terminal_beyond else False),
        "cms_plane_crossing_mm": crossing,
        "upstream_rock_path_m": sum(v for k,v in materials.items() if any(s in k.lower() for s in ("rock", "earth", "soil"))),
        "upstream_materials_m": dict(materials),
        "low_energy_end_before_cms": not reached and not terminal_beyond and energy < 0.01,
        "end_position_mm": terminal,
    }

def classify(start, end, steps):
    reason = next((s.get("reason") for s in steps if s.get("stage") == "cmssw-kill"), None)
    if reason:
        category = "cmssw_transport_kill"
    else:
        volume = (end or {}).get("volume", "").lower()
        proc = (end or {}).get("last_process", "").lower()
        energy = float((end or {}).get("kinetic_energy_GeV", "inf"))
        if energy < 0.01 and any(t in volume for t in ("rock", "earth", "soil")):
            category = "stopped_in_rock"
        elif energy < 0.01 and volume not in ("", "outside-world"):
            category = "stopped_in_material"
        elif volume in ("", "outside-world") or "world" in volume:
            category = "left_world"
        elif "decay" in proc or "muon" in proc and "capture" in proc:
            category = "physics_process"
        else:
            category = "survived_or_unresolved"
    return category, reason

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", type=Path, nargs="+", help="one or more cmsRun logs")
    ap.add_argument("output", type=Path)
    ap.add_argument("--cms-entrance-abs-z-mm", type=float, default=11000.0,
                    help="transport entrance plane; crossing alone is not detector acceptance")
    args = ap.parse_args()
    grouped = defaultdict(lambda: {"starts": {}, "ends": {}, "steps": defaultdict(list), "points": defaultdict(list)})
    for path in args.log:
        for line in path.read_text(errors="replace").splitlines():
            m = PREFIX.search(line)
            point = POINT.search(line)
            if not m and not point:
                continue
            d = parse_fields((m or point).group("body")); event = d.get("event"); track = d.get("track_id")
            if event is None or track is None:
                continue
            key = (str(path), int(event), int(track)); source = m.group("source") if m else None
            if point:
                grouped[key]["points"][track].append(d)
            elif source == "PrimaryFate" and d.get("stage") == "track-start": grouped[key]["starts"][track] = d
            elif source == "PrimaryFate" and d.get("stage") == "track-end": grouped[key]["ends"][track] = d
            elif source == "G4Step": grouped[key]["steps"][track].append(d)
    rows = []
    for (source_log, event, track), g in sorted(grouped.items()):
        start = g["starts"].get(str(track)); end = g["ends"].get(str(track)); steps = g["steps"].get(str(track), [])
        if not start or not end: continue
        points = g["points"].get(str(track), [])
        category, reason = classify(start, end, steps)
        materials = defaultdict(float)
        for point in points:
            materials[point.get("material", "unknown")] += float(point.get("step_length_mm", 0.0))
        rock_path_m = sum(length for name, length in materials.items()
                          if any(token in name.lower() for token in ("rock", "earth", "soil", "earthboh"))) / 1000.0
        # Rock encountered anywhere is not evidence of stopping in rock.
        # Preserve the observed endpoint material separately from path totals.
        terminal_material = points[-1].get("material") if points else None
        upstream = before_cms(start, end, points, args.cms_entrance_abs_z_mm)
        rows.append({"source_log": source_log, "event": event, "track_id": int(track), "category": category, "kill_reason": reason,
                     "pdg_id": int(start["pdg_id"]), "start_momentum_GeV": vec(start.get("momentum_GeV")),
                     "start_volume": start.get("volume"), "end_volume": end.get("volume"),
                     "end_process": end.get("last_process"), "end_energy_GeV": float(end.get("kinetic_energy_GeV", "nan")),
                     "steps": int(end.get("steps", 0)), "rock_path_m": rock_path_m,
                     "terminal_recorded_material": terminal_material, **upstream,
                     "materials_m": {name: length / 1000.0 for name, length in sorted(materials.items())},
                     "deflection_angle_deg": angle(vec(start.get("momentum_GeV")), vec(end.get("momentum_GeV")))})
    counts = defaultdict(int)
    for row in rows: counts[row["category"]] += 1
    payload = {"format_version": 2, "tracks": rows, "counts": dict(sorted(counts.items())),
               "cms_entrance_abs_z_mm": args.cms_entrance_abs_z_mm,
               "boundary_note": "Entrance-plane transport milestone, not detector acceptance; terminal step may be absent"}
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print("tracks=" + str(len(rows)), "counts=" + json.dumps(dict(sorted(counts.items())), sort_keys=True))

if __name__ == "__main__": main()
