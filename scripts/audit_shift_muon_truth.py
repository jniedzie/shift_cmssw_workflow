#!/usr/bin/env python3
"""Audit SHIFT muon truth identity and select detector-crossing events.

Run this inside the CMSSW runtime on GEN-SIM or DIGI-RAW files.  The output is
JSON so the selected events and exact inputs can be frozen for later
SimHit-to-digi response tests.
"""

import argparse
import hashlib
import json
import os
import struct
from collections import Counter, defaultdict

from DataFormats.FWLite import Events, Handle


SUBDETECTORS = {
    "DT": "MuonDTHits",
    "CSC": "MuonCSCHits",
    "RPC": "MuonRPCHits",
    "GEM": "MuonGEMHits",
}


def _non_timing_hit_record(hit):
    entry = hit.entryPoint()
    exit_point = hit.exitPoint()
    return struct.pack(
        "<IIIiHH10f",
        int(hit.eventId().rawId()),
        int(hit.trackId()),
        int(hit.detUnitId()),
        int(hit.particleType()),
        int(hit.processType()),
        int(hit.hitProdType()),
        float(entry.x()),
        float(entry.y()),
        float(entry.z()),
        float(exit_point.x()),
        float(exit_point.y()),
        float(exit_point.z()),
        float(hit.pabs()),
        float(hit.energyLoss()),
        float(hit.thetaAtEntry()),
        float(hit.phiAtEntry()),
    )


def _event_id(encoded_event_id):
    return {
        "raw": int(encoded_event_id.rawId()),
        "bunch_crossing": int(encoded_event_id.bunchCrossing()),
        "event": int(encoded_event_id.event()),
    }


def _truth_key(obj):
    return int(obj.eventId().rawId()), int(obj.trackId())


def _event_number(event):
    event_id = event.eventAuxiliary().id()
    return {
        "run": int(event_id.run()),
        "lumi": int(event.eventAuxiliary().luminosityBlock()),
        "event": int(event_id.event()),
    }


def _get_product(event, module, instance, type_name, input_path):
    handle = Handle(type_name)
    if not event.getByLabel(module, instance, handle):
        label = f"{module}:{instance}" if instance else module
        raise RuntimeError(f"missing {label} ({type_name}) in {input_path}")
    return handle.product()


def _audit_event(event, input_path, track_module="g4SimHits", simhit_module="g4SimHits"):
    tracks = _get_product(
        event, track_module, "", "std::vector<SimTrack>", input_path
    )
    track_keys = Counter(_truth_key(track) for track in tracks)
    tracks_by_key = defaultdict(list)
    for track in tracks:
        tracks_by_key[_truth_key(track)].append(track)

    hit_summaries = defaultdict(
        lambda: {
            name: {
                "hits": 0,
                "det_units": set(),
                "tof_min_ns": None,
                "tof_max_ns": None,
                "non_timing_records": [],
            }
            for name in SUBDETECTORS
        }
    )
    orphan_muon_hits = Counter()
    particle_type_mismatches = Counter()

    for name, instance in SUBDETECTORS.items():
        hits = _get_product(
            event, simhit_module, instance, "std::vector<PSimHit>", input_path
        )
        for hit in hits:
            if abs(int(hit.particleType())) != 13:
                continue
            key = _truth_key(hit)
            if key not in tracks_by_key:
                orphan_muon_hits[name] += 1
            elif not any(int(track.type()) == int(hit.particleType()) for track in tracks_by_key[key]):
                particle_type_mismatches[name] += 1

            summary = hit_summaries[key][name]
            tof = float(hit.timeOfFlight())
            summary["hits"] += 1
            summary["det_units"].add(int(hit.detUnitId()))
            summary["non_timing_records"].append(_non_timing_hit_record(hit))
            summary["tof_min_ns"] = tof if summary["tof_min_ns"] is None else min(summary["tof_min_ns"], tof)
            summary["tof_max_ns"] = tof if summary["tof_max_ns"] is None else max(summary["tof_max_ns"], tof)

    signal_muons = []
    for track in tracks:
        # A generator-linked muon in the signal event.  The full key is still
        # retained; genpartIndex or PDG ID alone must never be used for matching.
        if abs(int(track.type())) != 13 or int(track.genpartIndex()) < 0:
            continue
        key = _truth_key(track)
        event_id = _event_id(track.eventId())
        if event_id["raw"] != 0:
            continue
        detector_counts = {}
        crossed = []
        for name in SUBDETECTORS:
            summary = hit_summaries[key][name]
            detector_counts[name] = {
                "hits": summary["hits"],
                "det_units": len(summary["det_units"]),
                "non_timing_sha256": hashlib.sha256(
                    b"".join(sorted(summary["non_timing_records"]))
                ).hexdigest(),
                "tof_min_ns": summary["tof_min_ns"],
                "tof_max_ns": summary["tof_max_ns"],
            }
            if summary["hits"]:
                crossed.append(name)
        momentum = track.momentum()
        signal_muons.append(
            {
                "event_id": event_id,
                "track_id": int(track.trackId()),
                "genpart_index": int(track.genpartIndex()),
                "vertex_index": int(track.vertIndex()),
                "pdg_id": int(track.type()),
                "pt_gev": float(momentum.pt()),
                "eta": float(momentum.eta()),
                "phi": float(momentum.phi()),
                "crossed": crossed,
                "subdetectors": detector_counts,
            }
        )

    duplicate_keys = [
        {"event_id_raw": key[0], "track_id": key[1], "count": count}
        for key, count in sorted(track_keys.items())
        if count != 1
    ]
    return {
        **_event_number(event),
        "sim_tracks": len(tracks),
        "signal_muons": signal_muons,
        "identity_failures": {
            "duplicate_track_keys": duplicate_keys,
            "orphan_muon_hits": dict(sorted(orphan_muon_hits.items())),
            "particle_type_mismatches": dict(sorted(particle_type_mismatches.items())),
        },
    }


def _has_identity_failure(event_summary):
    failures = event_summary["identity_failures"]
    return bool(
        failures["duplicate_track_keys"]
        or failures["orphan_muon_hits"]
        or failures["particle_type_mismatches"]
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Audit (EncodedEventId, SimTrackId) matching and report generated "
            "SHIFT muon crossings in DT, CSC, RPC and GEM."
        )
    )
    parser.add_argument("inputs", nargs="+", help="GEN-SIM or DIGI-RAW ROOT files")
    parser.add_argument("--output", required=True, help="output JSON path")
    parser.add_argument(
        "--max-events",
        type=int,
        default=-1,
        help="maximum events per input; negative means all (default: all)",
    )
    parser.add_argument(
        "--track-module",
        default="g4SimHits",
        help="module containing SimTracks (default: g4SimHits)",
    )
    parser.add_argument(
        "--simhit-module",
        default="g4SimHits",
        help="module containing muon PSimHits (default: g4SimHits)",
    )
    parser.add_argument(
        "--fail-on-identity-error",
        action="store_true",
        help="exit nonzero if a duplicate key, orphan hit or PDG mismatch is found",
    )
    args = parser.parse_args()

    if args.max_events == 0 or args.max_events < -1:
        parser.error("--max-events must be -1 or a positive integer")

    result = {
        "schema_version": 2,
        "identity_key": ["EncodedEventId.rawId", "SimTrackId"],
        "inputs": [],
        "events": [],
        "selection": {name: [] for name in SUBDETECTORS},
        "sources": {
            "sim_tracks": args.track_module,
            "muon_simhits": args.simhit_module,
        },
    }
    total_identity_failures = 0

    for input_path in args.inputs:
        result["inputs"].append(
            {
                "path": os.path.abspath(input_path),
                "size_bytes": os.path.getsize(input_path),
            }
        )
        for index, event in enumerate(Events(input_path)):
            if args.max_events >= 0 and index >= args.max_events:
                break
            summary = _audit_event(
                event,
                input_path,
                track_module=args.track_module,
                simhit_module=args.simhit_module,
            )
            summary["input"] = os.path.abspath(input_path)
            summary["input_event_index"] = index
            result["events"].append(summary)
            if _has_identity_failure(summary):
                total_identity_failures += 1
            for name in SUBDETECTORS:
                if any(name in muon["crossed"] for muon in summary["signal_muons"]):
                    result["selection"][name].append(
                        {
                            "input": summary["input"],
                            "run": summary["run"],
                            "lumi": summary["lumi"],
                            "event": summary["event"],
                            "input_event_index": index,
                        }
                    )

    result["summary"] = {
        "events": len(result["events"]),
        "signal_muons": sum(len(event["signal_muons"]) for event in result["events"]),
        "events_with_identity_failures": total_identity_failures,
        "events_with_crossing": {
            name: len(events) for name, events in result["selection"].items()
        },
    }
    with open(args.output, "w", encoding="utf-8") as output_file:
        json.dump(result, output_file, indent=2, sort_keys=True)
        output_file.write("\n")

    print(json.dumps(result["summary"], sort_keys=True))
    if args.fail_on_identity_error and total_identity_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
