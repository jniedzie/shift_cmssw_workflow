#!/usr/bin/env python3
"""Bind central-BX piggyback decisions to the matching simulated EDM events."""

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import sys


SCHEMA = "shift-piggyback-central-readout"
SCHEMA_VERSION = 2


class PiggybackError(RuntimeError):
    pass


def load_timeline(path):
    metadata = None
    records = defaultdict(list)
    with open(path, encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("record_type") == "metadata":
                if metadata is not None:
                    raise PiggybackError(f"{path} contains multiple metadata records")
                metadata = record
            elif record.get("record_type") == "timeline_bx":
                records[int(record["signal_event_index"])].append(record)
            else:
                raise PiggybackError(f"{path}:{line_number} has an unknown record type")
    if (
        metadata is None
        or metadata.get("schema") != "shift-zero-bias-trigger-timeline"
        or metadata.get("schema_version") != 1
    ):
        raise PiggybackError(f"{path} is not a supported trigger timeline")
    return metadata, records


def validate_piggyback_metadata(metadata):
    if metadata.get("start_bx") != 0 or metadata.get("end_bx") != 0:
        raise PiggybackError("piggyback-central production requires analysis BX 0 only")
    if metadata.get("trigger_rules_applied"):
        raise PiggybackError(
            "piggyback-central production must not reapply synthetic rules to a recorded L1A"
        )
    if not metadata.get("trigger_rules_embodied_by_recorded_l1a"):
        raise PiggybackError(
            "piggyback-central production requires an already-recorded source L1A"
        )
    if not metadata.get("deadtime_applied"):
        raise PiggybackError("piggyback-central production requires deadtime provenance")
    mask = metadata.get("colliding_bx_mask") or {}
    if not mask or not metadata.get("run_fill_validation", {}).get("status") == "validated":
        raise PiggybackError("piggyback-central production requires a validated physical fill mask")
    sampling = mask.get("reference_slot_sampling") or {}
    if sampling.get("mode") not in ("fixed", "uniform-colliding"):
        raise PiggybackError("timeline lacks explicit reference-slot sampling provenance")
    return mask, sampling


def ordinary_l1_candidate(record):
    decisions = record.get("candidate_decisions") or {}
    return any(
        bool(block.get("final_or"))
        for block in decisions.get("l1_by_bx", {}).get("0", [])
    )


def readout_timing_contract(bx_offset, phase_ns, bunch_spacing_ns):
    if not isinstance(bx_offset, int):
        raise PiggybackError("SHIFT timing BX offset must be an integer")
    if not math.isfinite(phase_ns) or not math.isfinite(bunch_spacing_ns):
        raise PiggybackError("SHIFT timing phase and bunch spacing must be finite")
    if bunch_spacing_ns <= 0.0:
        raise PiggybackError("SHIFT timing bunch spacing must be positive")
    if phase_ns < 0.0 or phase_ns >= bunch_spacing_ns:
        raise PiggybackError(
            "piggyback SHIFT timing phase must satisfy 0 <= phase < bunch spacing"
        )
    relative_ns = bx_offset * bunch_spacing_ns + phase_ns
    return {
        "central_l1a_bx": 0,
        "shift_arrival_bx_offset": bx_offset,
        "shift_arrival_phase_ns": phase_ns,
        "bunch_spacing_ns": bunch_spacing_ns,
        "additional_shift_arrival_relative_to_l1a_ns": relative_ns,
        "sign_convention": (
            "positive means the SHIFT collision and its detector hits arrive later "
            "relative to the central BX-0 L1A"
        ),
        "electronics_configuration_modified": False,
    }


def build_piggyback_summary(
    timeline_path,
    event_ids,
    shift_arrival_bx_offset=0,
    shift_arrival_phase_ns=0.0,
    bunch_spacing_ns=25.0,
):
    metadata, records = load_timeline(timeline_path)
    mask, slot_sampling = validate_piggyback_metadata(metadata)
    timing = readout_timing_contract(
        shift_arrival_bx_offset, shift_arrival_phase_ns, bunch_spacing_ns
    )
    if len(records) != len(event_ids):
        raise PiggybackError(
            f"timeline has {len(records)} signal events but EDM has {len(event_ids)}"
        )

    decisions = []
    for signal_event_index, event_id in enumerate(event_ids):
        central = [
            record for record in records.get(signal_event_index, [])
            if record.get("analysis_window") and int(record["timeline_bx"]) == 0
        ]
        if len(central) != 1:
            raise PiggybackError(
                f"signal event {signal_event_index} has {len(central)} central analysis records"
            )
        record = central[0]
        source_decisions = record.get("candidate_decisions") or {}
        candidate = bool(
            record.get("colliding") and source_decisions.get("source_event_was_read_out")
        )
        rule_decision = record.get("trigger_rule_decision") or {}
        raw_readout = bool(
            candidate
            and record.get("readout_after_trigger_rules")
            and rule_decision.get("accepted")
            and rule_decision.get("reason") == "recorded_source_event_l1a"
            and rule_decision.get("rules_reapplied") is False
        )
        hlt_paths = list(source_decisions.get("hlt_accepted") or [])
        persisted = bool(raw_readout and hlt_paths)
        decisions.append(
            {
                "signal_event_index": signal_event_index,
                "event_id": event_id,
                "reference_bx_slot": record.get("reference_bx_slot"),
                "central_collision_present": bool(record.get("colliding")),
                "ordinary_recorded_l1a": candidate,
                "accepted_after_trigger_rules": raw_readout,
                "persisted_by_ordinary_hlt_proxy": persisted,
                "accepted_hlt_paths": hlt_paths,
                "trigger_rule_decision": rule_decision,
                "ordinary_trigger_source": record.get("source"),
                "signal_contributed_to_trigger_decision": False,
                "additional_shift_arrival_relative_to_l1a_ns": timing[
                    "additional_shift_arrival_relative_to_l1a_ns"
                ],
            }
        )

    if any(not item["accepted_after_trigger_rules"] for item in decisions):
        raise PiggybackError(
            "piggyback-central input contains an event without an already-recorded central L1A"
        )

    counts = {
        "events": len(decisions),
        "central_collision_present": sum(
            item["central_collision_present"] for item in decisions
        ),
        "ordinary_recorded_l1a": sum(item["ordinary_recorded_l1a"] for item in decisions),
        "accepted_after_trigger_rules": sum(
            item["accepted_after_trigger_rules"] for item in decisions
        ),
        "persisted_by_ordinary_hlt_proxy": sum(
            item["persisted_by_ordinary_hlt_proxy"] for item in decisions
        ),
    }
    rules_status = (metadata.get("trigger_rules") or {}).get("status", "missing")
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "scenario": "piggyback_central",
        "validated_boundary": (
            "ordinary central-collision BX-0 L1A -> configured physical SHIFT arrival "
            "time -> unchanged standard digitization and RAW readout"
        ),
        "readout_timing": timing,
        "signal_contributed_to_trigger_decision": False,
        "timeline_file": str(timeline_path),
        "timeline_file_sha256": hashlib.sha256(Path(timeline_path).read_bytes()).hexdigest(),
        "fill_number": mask["fill_number"],
        "shift_beam": mask["shift_beam"],
        "reference_slot_sampling": slot_sampling,
        "trigger_rules_status": rules_status,
        "conditional_readout_contract_valid": all(
            item["accepted_after_trigger_rules"] for item in decisions
        ),
        "physics_result_valid": bool(slot_sampling.get("physics_valid")),
        "limitations": [
            "The SHIFT signal is excluded from the trigger decision by construction.",
            "The sample is conditional on a central collision that was already accepted and recorded; it does not predict the absolute opportunity probability.",
            "The recorded trigger source and simulated pileup occupancy are sampled independently, so event-by-event trigger-class/occupancy correlations are not reproduced.",
            "HLT persistence is sampled from an ordinary recorded event; the overlaid SHIFT event is not rerun through data HLT.",
            "Uniform colliding-slot sampling is provisional until authoritative per-bunch luminosity weights are available.",
        ],
        "summary": counts,
        "decisions": decisions,
    }


def edm_event_ids(path):
    try:
        from DataFormats.FWLite import Events
    except ImportError as error:
        raise PiggybackError("CMSSW FWLite is required to read EDM event identities") from error
    result = []
    for event in Events(str(path)):
        event_id = event.eventAuxiliary().id()
        result.append(
            {
                "run": int(event_id.run()),
                "lumi": int(event_id.luminosityBlock()),
                "event": int(event_id.event()),
            }
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timeline")
    parser.add_argument("--edm-input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--shift-arrival-bx-offset", type=int, default=0)
    parser.add_argument("--shift-arrival-phase-ns", type=float, default=0.0)
    parser.add_argument("--bunch-spacing-ns", type=float, default=25.0)
    args = parser.parse_args()
    try:
        summary = build_piggyback_summary(
            args.timeline,
            edm_event_ids(args.edm_input),
            shift_arrival_bx_offset=args.shift_arrival_bx_offset,
            shift_arrival_phase_ns=args.shift_arrival_phase_ns,
            bunch_spacing_ns=args.bunch_spacing_ns,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f"{output.name}.partial.{os.getpid()}")
        with temporary.open("w", encoding="utf-8") as destination:
            json.dump(summary, destination, indent=2, sort_keys=True)
            destination.write("\n")
        os.replace(temporary, output)
    except (OSError, ValueError, json.JSONDecodeError, PiggybackError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
