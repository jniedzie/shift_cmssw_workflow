#!/usr/bin/env python3
"""Convolve an unchanged SHIFT delay response with measured Run-3 L1A timelines.

This program intentionally has no rate model.  It accepts only explicit,
versioned parent-bunch weights and ordered L1A/HLT opportunities.  Supplying a
uniform fill mask or a ZeroBias library conditioned on recorded events is a
structural control and requires --allow-provisional.
"""

import argparse
import hashlib
import json
import math
import os
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
import sys


SCHEMA = "shift-event-collection-convolution"
BUNCH_SPACING_NS = Decimal("25")


class ConvolutionError(ValueError):
    pass


def _load_json(path):
    try:
        data = Path(path).read_bytes()
        return json.loads(data), hashlib.sha256(data).hexdigest()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConvolutionError(f"cannot read {path}: {error}") from error


def _number(value, field):
    if isinstance(value, bool):
        raise ConvolutionError(f"{field} must be a finite number")
    try:
        value = Decimal(str(value))
    except Exception as error:
        raise ConvolutionError(f"{field} must be a finite number") from error
    if not value.is_finite():
        raise ConvolutionError(f"{field} must be finite")
    return value


def _metadata(document, schema, path):
    if document.get("schema") != schema or document.get("schema_version") != 1:
        raise ConvolutionError(f"{path} is not {schema} schema version 1")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict) or not provenance.get("source"):
        raise ConvolutionError(f"{path} lacks provenance.source")


def load_parent_weights(path):
    document, digest = _load_json(path)
    _metadata(document, "shift-parent-bunch-distribution", path)
    weights = {}
    for item in document.get("slots", []):
        slot = item.get("slot")
        weight = _number(item.get("weight"), f"{path}: slot weight")
        if type(slot) is not int or not 1 <= slot <= 3564 or weight <= 0 or slot in weights:
            raise ConvolutionError(f"{path} has an invalid or duplicate parent slot")
        weights[slot] = weight
    if not weights:
        raise ConvolutionError(f"{path} has no parent-slot weights")
    total = sum(weights.values())
    return document, digest, {slot: weight / total for slot, weight in weights.items()}


def load_response(path):
    document, digest = _load_json(path)
    _metadata(document, "shift-event-delay-response", path)
    if _number(document.get("bunch_spacing_ns"), f"{path}: bunch_spacing_ns") != BUNCH_SPACING_NS:
        raise ConvolutionError(f"{path} does not use the Run-3 25 ns bunch spacing")
    events = {}
    for event in document.get("events", []):
        identifier = event.get("signal_event_id")
        physical_delay = _number(event.get("physical_delay_ns"), f"{path}: physical_delay_ns")
        if not isinstance(identifier, str) or not identifier or identifier in events:
            raise ConvolutionError(f"{path} has an invalid or duplicate signal_event_id")
        points = {}
        for point in event.get("responses", []):
            delay = _number(point.get("additional_delay_ns"), f"{path}: additional_delay_ns")
            if delay in points:
                raise ConvolutionError(f"{path}: duplicate response delay for {identifier}")
            required = ("readout", "reconstructed_muon", "reconstructed_dimuon")
            if any(type(point.get(name)) is not bool for name in required):
                raise ConvolutionError(f"{path}: response flags must be boolean")
            points[delay] = {name: point[name] for name in required} | {
                "classification": point.get("classification", "unspecified")
            }
        if not points:
            raise ConvolutionError(f"{path}: {identifier} has no response points")
        events[identifier] = {"physical_delay_ns": physical_delay, "responses": points}
    if not events:
        raise ConvolutionError(f"{path} has no response events")
    return document, digest, events


def load_timeline(path):
    records = []
    try:
        with Path(path).open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if line.strip():
                    records.append((line_number, json.loads(line)))
    except (OSError, json.JSONDecodeError) as error:
        raise ConvolutionError(f"cannot read {path}: {error}") from error
    if not records:
        raise ConvolutionError(f"{path} is empty")
    metadata = records[0][1]
    _metadata(metadata, "shift-l1a-opportunity-timeline", path)
    by_event = defaultdict(list)
    for line_number, record in records[1:]:
        required = ("signal_event_id", "parent_slot", "relative_bx", "l1a_recorded", "hlt_persisted")
        if any(field not in record for field in required):
            raise ConvolutionError(f"{path}:{line_number} lacks an opportunity field")
        identifier, slot, bx = record["signal_event_id"], record["parent_slot"], record["relative_bx"]
        if not isinstance(identifier, str) or type(slot) is not int or not 1 <= slot <= 3564 or type(bx) is not int:
            raise ConvolutionError(f"{path}:{line_number} has invalid event, slot, or BX")
        if type(record["l1a_recorded"]) is not bool or type(record["hlt_persisted"]) is not bool:
            raise ConvolutionError(f"{path}:{line_number} has non-boolean L1A/HLT state")
        if record["hlt_persisted"] and not record["l1a_recorded"]:
            raise ConvolutionError(f"{path}:{line_number} persists without a recorded L1A")
        by_event[identifier].append(record)
    if not by_event:
        raise ConvolutionError(f"{path} has no opportunities")
    return metadata, by_event


def convolve(parent_weights, response_events, timeline_events):
    unknown_slots = set()
    missing = []
    results = []
    for identifier, opportunities in sorted(timeline_events.items()):
        response = response_events.get(identifier)
        if response is None:
            raise ConvolutionError(f"timeline event {identifier} has no delay response")
        slots = {item["parent_slot"] for item in opportunities}
        if len(slots) != 1:
            raise ConvolutionError(f"timeline event {identifier} has multiple parent slots")
        slot = next(iter(slots))
        if slot not in parent_weights:
            unknown_slots.add(slot)
            continue
        selected = []
        for item in opportunities:
            if not item["l1a_recorded"]:
                continue
            delay = response["physical_delay_ns"] - BUNCH_SPACING_NS * item["relative_bx"]
            point = response["responses"].get(delay)
            if point is None:
                missing.append((identifier, str(delay)))
                continue
            selected.append((item, delay, point))
        if missing:
            continue
        readouts = [item for item in selected if item[2]["readout"]]
        persisted = [item for item in readouts if item[0]["hlt_persisted"]]
        results.append({
            "signal_event_id": identifier,
            "parent_slot": slot,
            "recorded_l1as": len(selected),
            "readout": bool(readouts),
            "hlt_persisted": bool(persisted),
            "reconstructed_muon": any(item[2]["reconstructed_muon"] for item in persisted),
            "reconstructed_dimuon": any(item[2]["reconstructed_dimuon"] for item in persisted),
            "readout_delays_ns": [float(item[1]) for item in readouts],
            "classifications": [item[2]["classification"] for item in readouts],
        })
    if unknown_slots:
        raise ConvolutionError(f"parent-weight input lacks timeline slots: {sorted(unknown_slots)}")
    sampled_slots = {item["parent_slot"] for item in results}
    unsampled_slots = sorted(set(parent_weights) - sampled_slots)
    if unsampled_slots:
        raise ConvolutionError(
            "timeline has no sampled event for weighted parent slots: "
            f"{unsampled_slots}"
        )
    if missing:
        preview = ", ".join(f"{event}@{delay} ns" for event, delay in missing[:5])
        raise ConvolutionError(f"response grid lacks required event-level delays: {preview}")
    if not results:
        raise ConvolutionError("no timeline events survived convolution")
    by_slot = defaultdict(list)
    for item in results:
        by_slot[item["parent_slot"]].append(item)
    totals = {"readout": Decimal(0), "hlt_persisted": Decimal(0), "reconstructed_muon": Decimal(0), "reconstructed_dimuon": Decimal(0)}
    slots = []
    for slot, items in sorted(by_slot.items()):
        scale = parent_weights[slot] / len(items)
        probabilities = {key: sum(Decimal(bool(item[key])) for item in items) / len(items) for key in totals}
        for key in totals:
            totals[key] += parent_weights[slot] * probabilities[key]
        slots.append({"slot": slot, "parent_probability": float(parent_weights[slot]), "sampled_events": len(items),
                      "probabilities": {key: float(value) for key, value in probabilities.items()}})
    return results, slots, {key: float(value) for key, value in totals.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-weights", required=True)
    parser.add_argument("--response", required=True)
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-provisional", action="store_true", help="write a clearly invalid structural control")
    args = parser.parse_args()
    try:
        parent, parent_hash, weights = load_parent_weights(args.parent_weights)
        response, response_hash, responses = load_response(args.response)
        timeline, timelines = load_timeline(args.timeline)
        inputs_valid = all(item.get("physics_valid") is True for item in (parent, response, timeline))
        if not inputs_valid and not args.allow_provisional:
            raise ConvolutionError("absolute convolution requires physics_valid=true on parent weights, response, and timeline")
        events, slots, totals = convolve(weights, responses, timelines)
        output = {
            "schema": SCHEMA, "schema_version": 1,
            "physics_valid": inputs_valid,
            "model_status": "unchanged",
            "limitations": ([] if inputs_valid else ["structural control: one or more inputs are provisional"]),
            "inputs": {"parent_weights": {"path": args.parent_weights, "sha256": parent_hash},
                       "response": {"path": args.response, "sha256": response_hash},
                       "timeline": args.timeline},
            "probabilities": totals, "by_parent_slot": slots, "events": events,
        }
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
        temporary.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    except ConvolutionError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(output["probabilities"], sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
