#!/usr/bin/env python3
"""Turn FixedTargetMuonDebug lines from cmsRun output into an event tree."""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field


PREFIX = re.compile(r"\[FixedTargetMuonDebug\]\[(?P<source>[^]]+)\]\s+(?P<body>.*)")
FIELD = re.compile(r"(?P<key>[A-Za-z0-9_]+)=(?P<value>\([^)]*\)|[^\s]+)")


def fields(body: str) -> dict[str, str]:
    return {match.group("key"): match.group("value") for match in FIELD.finditer(body)}


def vector(value: str | None) -> tuple[float, float, float] | None:
    if not value or not value.startswith("("):
        return None
    try:
        values = tuple(float(item) for item in value[1:-1].split(","))
        return values if len(values) == 3 else None
    except ValueError:
        return None


def particle_name(pdg_id: str) -> str:
    return {"13": "mu-", "-13": "mu+"}.get(pdg_id, f"PDG {pdg_id}")


@dataclass
class Record:
    source: str
    data: dict[str, str]


@dataclass
class Event:
    number: int
    records: list[Record] = field(default_factory=list)


def parse(stream) -> tuple[dict[int, Event], int]:
    events: dict[int, Event] = {}
    malformed = 0
    for line in stream:
        match = PREFIX.search(line)
        if not match:
            continue
        data = fields(match.group("body"))
        try:
            event_number = int(data["event"])
        except (KeyError, ValueError):
            malformed += 1
            continue
        events.setdefault(event_number, Event(event_number)).records.append(
            Record(match.group("source"), data)
        )
    return events, malformed


def compact(data: dict[str, str], keys: tuple[str, ...]) -> str:
    return " ".join(f"{key}={data[key]}" for key in keys if key in data)


def projected_r_at_z0(data: dict[str, str]) -> float | None:
    position = vector(data.get("position_mm"))
    momentum = vector(data.get("momentum_GeV"))
    if not position or not momentum or momentum[2] == 0:
        return None
    scale = -position[2] / momentum[2]
    return math.hypot(position[0] + scale * momentum[0], position[1] + scale * momentum[1])


def match_primary(accepted: Record, tracks: list[Record], used: set[str]) -> Record | None:
    target = vector(f"({accepted.data.get('px')},{accepted.data.get('py')},{accepted.data.get('pz')})")
    candidates = [r for r in tracks if r.data.get("primary") == "1" and r.data.get("track_id") not in used]
    candidates = [r for r in candidates if r.data.get("pdg_id") == accepted.data.get("pdgId")]
    if not candidates:
        return None
    if target:
        candidates.sort(key=lambda r: sum((a - b) ** 2 for a, b in zip(vector(r.data.get("momentum_GeV")) or (math.inf,) * 3, target)))
    return candidates[0]


def branch(lines: list[str], label: str, children: list[str], last: bool) -> None:
    joint = "└─" if last else "├─"
    lines.append(f"{joint} {label}")
    stem = "   " if last else "│  "
    lines.extend(stem + child for child in children)


def render_track(track: Record, by_track: dict[str, list[Record]]) -> list[str]:
    data = track.data
    track_id = data.get("track_id", "?")
    details: list[str] = []
    radius = projected_r_at_z0(data)
    start = compact(data, ("position_mm", "momentum_GeV", "kinetic_energy_GeV"))
    details.append(f"start: {start}")
    related = by_track[track_id]
    for index, record in enumerate(related):
        d = record.data
        if record.source == "G4Step":
            if d.get("stage") == "volume-transition":
                detail = compact(d, ("step", "pre_volume", "post_volume", "position_mm", "process"))
            elif d.get("stage") == "dead-region-bypass":
                detail = compact(
                    d,
                    ("stage", "decision", "step", "volume", "region", "reason", "vertex_z_mm", "momentum_direction_z"),
                )
            else:
                detail = compact(
                    d,
                    (
                        "stage",
                        "step",
                        "reason",
                        "dead_region_source",
                        "volume",
                        "region",
                        "configured_dead_region",
                        "cmstozdc_transport",
                        "cmstozdc_volume_match",
                        "zdc_particle_eligible",
                        "shifttocms_transport",
                        "shift_particle_eligible",
                        "vertex_z_mm",
                        "momentum_direction_z",
                        "global_time_ns",
                    ),
                )
            details.append(f"step: {detail}")
        elif record.source == "MuonSD":
            details.append(f"hit: {compact(d, ('stage', 'detector', 'volume', 'energy_deposit_GeV', 'energy_loss_GeV', 'accepted'))}")
        else:
            details.append(f"end: {compact(d, ('position_mm', 'kinetic_energy_GeV', 'global_time_ns', 'g4_status', 'last_process'))}")
    if radius is not None:
        was_killed = any(record.source == "G4Step" and record.data.get("stage") == "cmssw-kill" for record in related)
        qualifier = "hypothetical; track killed before z=0" if was_killed else "ignores field and interactions"
        details.append(f"straight-line projection: r(z=0)={radius / 1000:.1f} m ({qualifier})")
    children = [("└─ " if index == len(details) - 1 else "├─ ") + detail for index, detail in enumerate(details)]
    return [f"G4 track #{track_id} ({particle_name(data.get('pdg_id', '?'))}, parent={data.get('parent_id', '?')})"] + ["   " + item for item in children]


def render(event: Event) -> str:
    records = event.records
    accepted = [r for r in records if r.source in ("HepMC", "HepMC3") and r.data.get("stage") == "g4-primary" and abs(int(r.data.get("pdgId", 0))) == 13]
    inventory = {r.data.get("barcode", r.data.get("id", "?")): r for r in records if r.source in ("HepMC", "HepMC3") and r.data.get("stage") == "inventory" and abs(int(r.data.get("pdgId", 0))) == 13}
    starts = [r for r in records if r.source == "G4Track" and r.data.get("stage") == "track-start"]
    related: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        if record.source == "G4Step" or (record.source == "G4Track" and record.data.get("stage") == "track-end") or (record.source == "MuonSD" and "track_id" in record.data):
            related[record.data.get("track_id", "?")].append(record)

    lines = [f"Event {event.number}"]
    summary = next((r for r in records if r.source in ("HepMC", "HepMC3") and r.data.get("stage") == "event-summary"), None)
    if summary:
        branch(lines, "generator: " + compact(summary.data, ("status1_muons", "GEANT4_muon_primaries", "GEANT4_primaries", "GEANT4_vertices")), [], False)

    used: set[str] = set()
    muon_children: list[str] = []
    for accepted_record in accepted:
        d = accepted_record.data
        barcode = d.get("barcode", d.get("id", "?"))
        inv = inventory.get(barcode)
        label = f"{d.get('name', particle_name(d.get('pdgId', '?')))} barcode={barcode} pdg={d.get('pdgId')}"
        if inv:
            label += " " + compact(inv.data, ("status", "E", "vertex"))
        label += " [accepted as G4 primary]"
        muon_children.append(("└─ " if accepted_record is accepted[-1] else "├─ ") + label)
        track = match_primary(accepted_record, starts, used)
        if track:
            used.add(track.data["track_id"])
            rendered = render_track(track, related)
            prefix = "   " if accepted_record is accepted[-1] else "│  "
            muon_children.extend(prefix + line for line in rendered)
        else:
            muon_children.append("   └─ G4 track: not identifiable in captured lines")
    branch(lines, f"HepMC muons ({len(accepted)} accepted)", muon_children or ["└─ none captured"], False)

    summaries = [r for r in records if r.source == "MuonSD" and r.data.get("stage") == "event-summary"]
    detector_children = []
    for index, record in enumerate(summaries):
        mark = "└─" if index == len(summaries) - 1 else "├─"
        detector_children.append(f"{mark} {record.data.get('detector')}: " + compact(record.data, ("muon_sensitive_steps", "muon_positive_edep_steps", "muon_saved_psimhits")))
    branch(lines, "muon sensitive detectors", detector_children or ["└─ no summaries captured"], True)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", nargs="?", help="cmsRun log (default: stdin; use '-' for stdin)")
    parser.add_argument("--event", type=int, action="append", help="only print this event (repeatable)")
    args = parser.parse_args()
    stream = sys.stdin if not args.log or args.log == "-" else open(args.log, encoding="utf-8", errors="replace")
    try:
        events, malformed = parse(stream)
    finally:
        if stream is not sys.stdin:
            stream.close()
    selected = set(args.event or events)
    output = [render(events[number]) for number in sorted(events) if number in selected]
    if output:
        print("\n\n".join(output))
    if malformed:
        print(f"warning: skipped {malformed} debug lines without a valid event", file=sys.stderr)
    if not output:
        print("No matching FixedTargetMuonDebug events found.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
