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

def classify(start, end, steps):
    reason = next((s.get("reason") for s in steps if s.get("stage") == "cmssw-kill"), None)
    if reason:
        category = "cmssw_transport_kill"
    else:
        volume = (end or {}).get("volume", "").lower()
        proc = (end or {}).get("last_process", "").lower()
        energy = float((end or {}).get("kinetic_energy_GeV", "inf"))
        rock = any(any(token in (s.get("pre_volume", "") + s.get("post_volume", "") + s.get("pre_region", "") + s.get("post_region", "")).lower() for token in ("rock", "earth", "molar", "soil")) for s in steps if s.get("stage") == "volume-transition")
        if energy < 0.01 and (rock or any(t in volume for t in ("rock", "earth", "soil"))):
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
    ap.add_argument("log", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    grouped = defaultdict(lambda: {"starts": {}, "ends": {}, "steps": defaultdict(list)})
    for line in args.log.read_text(errors="replace").splitlines():
        m = PREFIX.search(line)
        if not m:
            continue
        d = parse_fields(m.group("body")); event = d.get("event"); track = d.get("track_id")
        if event is None or track is None:
            continue
        key = (int(event), int(track)); source = m.group("source")
        if source == "PrimaryFate" and d.get("stage") == "track-start": grouped[key]["starts"][track] = d
        elif source == "PrimaryFate" and d.get("stage") == "track-end": grouped[key]["ends"][track] = d
        elif source == "G4Step": grouped[key]["steps"][track].append(d)
    rows = []
    for (event, track), g in sorted(grouped.items()):
        start = g["starts"].get(str(track)); end = g["ends"].get(str(track)); steps = g["steps"].get(str(track), [])
        if not start or not end: continue
        category, reason = classify(start, end, steps)
        rows.append({"event": event, "track_id": int(track), "category": category, "kill_reason": reason,
                     "start_volume": start.get("volume"), "end_volume": end.get("volume"),
                     "end_process": end.get("last_process"), "end_energy_GeV": float(end.get("kinetic_energy_GeV", "nan")),
                     "steps": int(end.get("steps", 0)), "deflection_angle_deg": angle(vec(start.get("momentum_GeV")), vec(end.get("momentum_GeV")))})
    counts = defaultdict(int)
    for row in rows: counts[row["category"]] += 1
    payload = {"format_version": 1, "tracks": rows, "counts": dict(sorted(counts.items()))}
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print("tracks=" + str(len(rows)), "counts=" + json.dumps(dict(sorted(counts.items())), sort_keys=True))

if __name__ == "__main__": main()
