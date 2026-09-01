#!/usr/bin/env python3

"""Sample whole correlated ZeroBias records onto a reference-BX timeline."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys

from zero_bias_trigger_library import (
    TriggerLibraryError,
    load_l1_menus,
    load_trigger_library,
    sample_loaded_events,
    validate_trigger_library,
)
from run3_trigger_rules import REQUIRED_HISTORY_BX, TriggerRuleEngine, ruleset_metadata


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", help="validated trigger-bit JSONL files")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--group-id", help="exact group ID printed by the validator")
    parser.add_argument("--start-bx", type=int, required=True)
    parser.add_argument("--end-bx", type=int, required=True, help="inclusive final relative BX")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--signal-events",
        type=int,
        default=1,
        help="number of independent SHIFT-event timelines to sample (default: 1)",
    )
    mask_group = parser.add_mutually_exclusive_group()
    mask_group.add_argument(
        "--colliding-bx-file",
        help="legacy relative-BX fixture; not sufficient for a physics result",
    )
    mask_group.add_argument(
        "--colliding-bx-mask",
        help="versioned cms-lpc-ip5-bunch-mask JSON",
    )
    parser.add_argument(
        "--reference-bx-slot",
        type=int,
        help="physical 1..3564 BX slot; required in fixed reference-slot mode",
    )
    parser.add_argument(
        "--reference-slot-mode",
        choices=("fixed", "uniform-filled", "uniform-colliding"),
        default="fixed",
        help="fixed slot or deterministic uniform sampling over filled SHIFT-beam slots",
    )
    parser.add_argument(
        "--shift-beam",
        type=int,
        choices=(1, 2),
        help="beam producing the SHIFT interaction; required with --colliding-bx-mask",
    )
    parser.add_argument(
        "--run-fill-map",
        help="versioned authoritative run-to-fill JSON; required with --colliding-bx-mask",
    )
    parser.add_argument("--without-replacement", action="store_true")
    parser.add_argument("--l1-menu", help="matching bit-to-name JSON for timeline provenance")
    parser.add_argument(
        "--trigger-rule-mode",
        choices=("none", "run3", "recorded"),
        default="none",
        help="none, synthetic Run-3 rules, or an already-recorded source L1A",
    )
    parser.add_argument(
        "--trigger-rule-history-start-bx",
        type=int,
        help="first warm-up BX processed before --start-bx when rules are enabled",
    )
    return parser.parse_args()


def load_colliding_bxs(path, timeline_bxs):
    if not path:
        return set(timeline_bxs)
    values = set()
    with open(path, encoding="utf-8") as source:
        for line_number, text in enumerate(source, start=1):
            value = text.partition("#")[0].strip()
            if not value:
                continue
            try:
                values.add(int(value))
            except ValueError as error:
                raise TriggerLibraryError(
                    f"{path}:{line_number}: colliding BX must be an integer"
                ) from error
    outside = values - set(timeline_bxs)
    if outside:
        raise TriggerLibraryError(f"colliding BX values outside requested timeline: {sorted(outside)}")
    return values


def load_ip5_bunch_mask(path, timeline_bxs, reference_bx_slot, shift_beam):
    with open(path, "rb") as source:
        payload_bytes = source.read()
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TriggerLibraryError(f"{path} is not valid JSON") from error
    if (
        payload.get("schema") != "cms-lpc-ip5-bunch-mask"
        or payload.get("schema_version") != 1
        or payload.get("orbit_slots") != 3564
    ):
        raise TriggerLibraryError(f"{path} is not a supported LPC IP5 bunch mask")
    fill_number = payload.get("fill_number")
    source_metadata = payload.get("source") or {}
    csv_sha256 = source_metadata.get("csv_sha256", "")
    if (
        not isinstance(fill_number, int)
        or fill_number <= 0
        or not payload.get("scheme_name")
        or not re.fullmatch(r"[0-9a-f]{64}", csv_sha256)
    ):
        raise TriggerLibraryError(f"{path} lacks complete LPC fill provenance")

    def validated_slots(field):
        values = payload.get(field)
        if (
            not isinstance(values, list)
            or any(type(value) is not int for value in values)
            or len(values) != len(set(values))
            or not set(values) <= set(range(1, 3565))
        ):
            raise TriggerLibraryError(f"{path} has invalid {field}")
        return set(values)

    beam1_slots = validated_slots("beam1_filled_bx_slots")
    beam2_slots = validated_slots("beam2_filled_bx_slots")
    colliding_slots = validated_slots("colliding_ip5_bx_slots")
    if not colliding_slots <= beam1_slots & beam2_slots:
        raise TriggerLibraryError("colliding IP5 slots are not filled in both beams")
    if reference_bx_slot is None or shift_beam is None:
        raise TriggerLibraryError(
            "--reference-bx-slot and --shift-beam are required with --colliding-bx-mask"
        )
    if not 1 <= reference_bx_slot <= 3564:
        raise TriggerLibraryError("--reference-bx-slot must be in 1..3564")
    beam_slots = beam1_slots if shift_beam == 1 else beam2_slots
    if reference_bx_slot not in beam_slots:
        raise TriggerLibraryError(
            f"reference BX slot {reference_bx_slot} is not filled in beam {shift_beam}"
        )
    relative_colliding = {
        bx for bx in timeline_bxs
        if ((reference_bx_slot - 1 + bx) % 3564) + 1 in colliding_slots
    }
    provenance = {
        "source_file": path,
        "file_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "fill_number": payload.get("fill_number"),
        "scheme_name": payload.get("scheme_name"),
        "reference_bx_slot": reference_bx_slot,
        "shift_beam": shift_beam,
    }
    return relative_colliding, provenance


def load_filled_reference_slots(path, shift_beam):
    """Return validated filled slots for one beam from a normalized LPC mask."""
    if shift_beam not in (1, 2):
        raise TriggerLibraryError("--shift-beam must be 1 or 2")
    with open(path, encoding="utf-8") as source:
        payload = json.load(source)
    if (
        payload.get("schema") != "cms-lpc-ip5-bunch-mask"
        or payload.get("schema_version") != 1
        or payload.get("orbit_slots") != 3564
    ):
        raise TriggerLibraryError(f"{path} is not a supported LPC IP5 bunch mask")
    slots = payload.get(f"beam{shift_beam}_filled_bx_slots")
    if (
        not isinstance(slots, list)
        or not slots
        or any(type(slot) is not int or not 1 <= slot <= 3564 for slot in slots)
        or len(slots) != len(set(slots))
    ):
        raise TriggerLibraryError(
            f"{path} has invalid beam{shift_beam}_filled_bx_slots"
        )
    return sorted(slots)


def load_colliding_reference_slots(path):
    """Return validated IP5 collision slots from a normalized LPC mask."""
    with open(path, encoding="utf-8") as source:
        payload = json.load(source)
    if (
        payload.get("schema") != "cms-lpc-ip5-bunch-mask"
        or payload.get("schema_version") != 1
        or payload.get("orbit_slots") != 3564
    ):
        raise TriggerLibraryError(f"{path} is not a supported LPC IP5 bunch mask")
    slots = payload.get("colliding_ip5_bx_slots")
    if (
        not isinstance(slots, list)
        or not slots
        or any(type(slot) is not int or not 1 <= slot <= 3564 for slot in slots)
        or len(slots) != len(set(slots))
    ):
        raise TriggerLibraryError(f"{path} has invalid colliding_ip5_bx_slots")
    return sorted(slots)


def select_reference_slots(filled_slots, mode, fixed_slot, signal_events, seed):
    """Choose one physical SHIFT-beam slot per signal event reproducibly."""
    if mode == "fixed":
        if fixed_slot is None:
            raise TriggerLibraryError(
                "--reference-bx-slot is required with --reference-slot-mode fixed"
            )
        if fixed_slot not in filled_slots:
            raise TriggerLibraryError(
                f"reference BX slot {fixed_slot} is not filled in the SHIFT beam"
            )
        return [fixed_slot] * signal_events
    if mode in ("uniform-filled", "uniform-colliding"):
        if fixed_slot is not None:
            raise TriggerLibraryError(
                f"--reference-bx-slot must be omitted with --reference-slot-mode {mode}"
            )
        generator = random.Random(seed ^ 0x5348494654)
        return [generator.choice(filled_slots) for _ in range(signal_events)]
    raise TriggerLibraryError(f"unsupported reference-slot mode {mode!r}")


def validate_run_fill_map(path, trigger_runs, fill_number):
    with open(path, "rb") as source:
        payload_bytes = source.read()
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TriggerLibraryError(f"{path} is not valid JSON") from error
    source_metadata = payload.get("source") or {}
    if (
        payload.get("schema") != "cms-run-to-fill-map"
        or payload.get("schema_version") != 1
        or not source_metadata.get("service")
        or not source_metadata.get("query")
        or not source_metadata.get("retrieved_at")
        or not isinstance(payload.get("runs"), dict)
    ):
        raise TriggerLibraryError(f"{path} lacks authoritative run-to-fill provenance")
    matched = {}
    for run in trigger_runs:
        record = payload["runs"].get(str(run))
        if not isinstance(record, dict) or type(record.get("fill_number")) is not int:
            raise TriggerLibraryError(f"{path} has no valid fill mapping for run {run}")
        observed_fill = record["fill_number"]
        if observed_fill != fill_number:
            raise TriggerLibraryError(
                f"run {run} maps to fill {observed_fill}, not bunch-mask fill {fill_number}"
            )
        matched[str(run)] = record
    return {
        "status": "validated",
        "source_file": path,
        "file_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "source": source_metadata,
        "trigger_runs": list(trigger_runs),
        "fill_number": fill_number,
        "matched_records": matched,
    }


def main():
    args = parse_args()
    if args.end_bx < args.start_bx:
        print("ERROR: --end-bx must be greater than or equal to --start-bx", file=sys.stderr)
        return 2
    if args.signal_events < 1:
        print("ERROR: --signal-events must be positive", file=sys.stderr)
        return 2
    if not args.colliding_bx_mask and (
        args.reference_bx_slot is not None
        or args.reference_slot_mode != "fixed"
        or args.shift_beam is not None
        or args.run_fill_map is not None
    ):
        print(
            "ERROR: --reference-bx-slot/--shift-beam/--run-fill-map require "
            "--colliding-bx-mask",
            file=sys.stderr,
        )
        return 2
    if args.trigger_rule_mode == "run3":
        if args.trigger_rule_history_start_bx is None:
            print(
                "ERROR: --trigger-rule-history-start-bx is required with "
                "--trigger-rule-mode run3",
                file=sys.stderr,
            )
            return 2
        if args.start_bx - args.trigger_rule_history_start_bx < REQUIRED_HISTORY_BX:
            print(
                f"ERROR: Run-3 rules require at least {REQUIRED_HISTORY_BX} warm-up BX "
                f"before --start-bx (got {args.start_bx - args.trigger_rule_history_start_bx})",
                file=sys.stderr,
            )
            return 2
    if args.trigger_rule_mode == "recorded" and (
        args.start_bx != 0 or args.end_bx != 0 or not args.colliding_bx_mask
    ):
        print(
            "ERROR: recorded trigger mode requires a physical fill mask and analysis BX 0 only",
            file=sys.stderr,
        )
        return 2

    try:
        library = load_trigger_library(args.inputs)
        errors, warnings, groups = validate_trigger_library(library)
        l1_menus = load_l1_menus([args.l1_menu] if args.l1_menu else [])
        if errors:
            raise TriggerLibraryError("input validation failed:\n  " + "\n  ".join(errors))

        groups_by_id = {key.group_id: (key, events) for key, events in groups.items()}
        if args.group_id:
            if args.group_id not in groups_by_id:
                raise TriggerLibraryError(
                    f"unknown group ID {args.group_id!r}; choices: {sorted(groups_by_id)}"
                )
            key, group_events = groups_by_id[args.group_id]
        elif len(groups_by_id) == 1:
            key, group_events = next(iter(groups_by_id.values()))
        else:
            raise TriggerLibraryError(
                "input has multiple trigger groups; select one with --group-id: "
                + ", ".join(sorted(groups_by_id))
            )

        l1_menu = l1_menus.get((key.l1_menu_uuid, key.l1_firmware_uuid))
        if args.l1_menu and not l1_menu:
            raise TriggerLibraryError(
                f"supplied L1 menu does not match group UUIDs in {key.group_id}"
            )

        timeline_start_bx = (
            args.trigger_rule_history_start_bx
            if args.trigger_rule_mode == "run3"
            else args.start_bx
        )
        timeline_bxs = list(range(timeline_start_bx, args.end_bx + 1))
        bunch_mask_metadata = None
        if args.colliding_bx_mask:
            if not args.run_fill_map:
                raise TriggerLibraryError(
                    "--run-fill-map is required with --colliding-bx-mask"
                )
            filled_reference_slots = (
                load_colliding_reference_slots(args.colliding_bx_mask)
                if args.reference_slot_mode == "uniform-colliding"
                else load_filled_reference_slots(args.colliding_bx_mask, args.shift_beam)
            )
            reference_slots = select_reference_slots(
                filled_reference_slots,
                args.reference_slot_mode,
                args.reference_bx_slot,
                args.signal_events,
                args.seed,
            )
            colliding_bxs_by_event = []
            for reference_slot in reference_slots:
                event_colliding_bxs, event_mask_metadata = load_ip5_bunch_mask(
                    args.colliding_bx_mask,
                    timeline_bxs,
                    reference_slot,
                    args.shift_beam,
                )
                colliding_bxs_by_event.append(event_colliding_bxs)
                if bunch_mask_metadata is None:
                    bunch_mask_metadata = event_mask_metadata
            bunch_mask_metadata["reference_bx_slot"] = (
                args.reference_bx_slot if args.reference_slot_mode == "fixed" else None
            )
            bunch_mask_metadata["reference_slot_sampling"] = {
                "mode": args.reference_slot_mode,
                "candidate_filled_slots": len(filled_reference_slots),
                "weighting_status": (
                    "fixed_control"
                    if args.reference_slot_mode == "fixed"
                    else (
                        "uniform_colliding_slots_provisional"
                        if args.reference_slot_mode == "uniform-colliding"
                        else "uniform_filled_slots_provisional"
                    )
                ),
                "physics_valid": False,
                "limitation": (
                    "authoritative per-bunch intensity weights are not available"
                ),
            }
            if args.trigger_rule_mode == "recorded" and any(
                0 not in values for values in colliding_bxs_by_event
            ):
                raise TriggerLibraryError(
                    "recorded central-L1A mode requires every selected reference slot to collide at IP5"
                )
        else:
            colliding_bxs = load_colliding_bxs(args.colliding_bx_file, timeline_bxs)
            colliding_bxs_by_event = [colliding_bxs] * args.signal_events
            reference_slots = [None] * args.signal_events
        trigger_runs = sorted({int(loaded.record["run"]) for loaded in group_events})
        run_fill_validation = (
            validate_run_fill_map(
                args.run_fill_map,
                trigger_runs,
                bunch_mask_metadata["fill_number"],
            )
            if bunch_mask_metadata
            else {
                "status": "missing_fill_mask",
                "trigger_runs": trigger_runs,
                "fill_number": None,
            }
        )
        sampled = iter(
            sample_loaded_events(
                group_events,
                sum(len(values) for values in colliding_bxs_by_event),
                args.seed,
                args.without_replacement,
            )
        )
    except (OSError, TriggerLibraryError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.name}.partial.{os.getpid()}")
    try:
        with temporary_path.open("w", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                    "record_type": "metadata",
                    "schema": "shift-zero-bias-trigger-timeline",
                    "schema_version": 1,
                    "input_files": args.inputs,
                    "trigger_group": key.as_dict(),
                    "seed": args.seed,
                    "signal_events": args.signal_events,
                    "start_bx": args.start_bx,
                    "end_bx": args.end_bx,
                    "timeline_start_bx": timeline_start_bx,
                    "colliding_bx_file": args.colliding_bx_file or "",
                    "colliding_bx_mask_file": args.colliding_bx_mask or "",
                    "colliding_bx_mask": bunch_mask_metadata,
                    "run_fill_map_file": args.run_fill_map or "",
                    "run_fill_validation": run_fill_validation,
                    "sampling": (
                        "without_replacement" if args.without_replacement else "with_replacement"
                    ),
                    "deadtime_applied": args.trigger_rule_mode in ("run3", "recorded"),
                    "trigger_rules_applied": args.trigger_rule_mode == "run3",
                    "trigger_rules_embodied_by_recorded_l1a": args.trigger_rule_mode == "recorded",
                    "trigger_rule_mode": args.trigger_rule_mode,
                    "trigger_rule_history_start_bx": args.trigger_rule_history_start_bx,
                    "trigger_rules": (
                        ruleset_metadata()
                        if args.trigger_rule_mode in ("run3", "recorded")
                        else None
                    ),
                    "l1_menu": (
                        {
                            "source_file": l1_menu["source_file"],
                            "global_tag": l1_menu.get("global_tag", ""),
                            "menu_uuid": l1_menu["menu_uuid"],
                            "firmware_uuid": l1_menu["firmware_uuid"],
                            "algorithms": {
                                str(bit): name for bit, name in sorted(l1_menu["algorithms"].items())
                            },
                        }
                        if l1_menu
                        else None
                    ),
                    "warnings": warnings,
                    },
                    sort_keys=True,
                )
                + "\n"
            )

            sample_index = 0
            for signal_event_index in range(args.signal_events):
                colliding_bxs = colliding_bxs_by_event[signal_event_index]
                reference_bx_slot = reference_slots[signal_event_index]
                rule_engine = (
                    TriggerRuleEngine() if args.trigger_rule_mode == "run3" else None
                )
                for timeline_bx in timeline_bxs:
                    record = {
                        "record_type": "timeline_bx",
                        "signal_event_index": signal_event_index,
                        "reference_bx_slot": reference_bx_slot,
                        "timeline_bx": timeline_bx,
                        "analysis_window": args.start_bx <= timeline_bx <= args.end_bx,
                        "colliding": timeline_bx in colliding_bxs,
                        "sample_index": None,
                        "source": None,
                        "candidate_decisions": None,
                        "readout_after_trigger_rules": None,
                        "trigger_rule_decision": None,
                    }
                    if timeline_bx in colliding_bxs:
                        loaded = next(sampled)
                        event = loaded.record
                        record["sample_index"] = sample_index
                        record["source"] = {
                            "file": loaded.source_file,
                            "line": loaded.source_line,
                            "run": event["run"],
                            "lumi": event["lumi"],
                            "event": event["event"],
                            "orbit": event["orbit"],
                            "bx": event["bx"],
                        }
                        record["candidate_decisions"] = {
                            "source_event_was_read_out": True,
                            "l1_by_bx": event["l1_by_bx"],
                            "l1_external_by_bx": event["l1_external_by_bx"],
                            "hlt_menu_id": event["hlt_menu_id"],
                            "hlt_accepted": event["hlt_accepted"],
                            "hlt_errors": event["hlt_errors"],
                        }
                        sample_index += 1
                    if rule_engine is not None:
                        candidate_l1a = bool(
                            record["candidate_decisions"]
                            and record["candidate_decisions"]["l1_by_bx"]["0"][0]["final_or"]
                        )
                        rule_decision = rule_engine.evaluate(timeline_bx, candidate_l1a)
                        record["trigger_rule_decision"] = rule_decision
                        record["readout_after_trigger_rules"] = rule_decision["accepted"]
                    elif args.trigger_rule_mode == "recorded" and record["candidate_decisions"]:
                        record["trigger_rule_decision"] = {
                            "candidate": True,
                            "accepted": True,
                            "reason": "recorded_source_event_l1a",
                            "rules_reapplied": False,
                            "recorded_tcds": event.get("tcds"),
                        }
                        record["readout_after_trigger_rules"] = True
                    output.write(json.dumps(record, sort_keys=True) + "\n")
        os.replace(temporary_path, output_path)
    except OSError as error:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"wrote {args.signal_events} timelines with {len(timeline_bxs)} BX records each "
        f"({sum(len(values) for values in colliding_bxs_by_event)} total colliding samples) "
        f"from group {key.group_id} to {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
