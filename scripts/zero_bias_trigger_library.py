#!/usr/bin/env python3

"""Shared validation and grouping helpers for ZeroBias trigger JSONL files."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
import json
import random

from run3_trigger_rules import validate_recorded_l1a_history


SUPPORTED_SCHEMA = "shift-zero-bias-trigger-bits"
SUPPORTED_SCHEMA_VERSION = 1


class TriggerLibraryError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class TriggerGroupKey:
    hlt_menu_id: str
    l1_menu_uuid: int
    l1_firmware_uuid: int
    prescale_column: int

    @property
    def group_id(self):
        return (
            f"hlt-{self.hlt_menu_id}_l1-{self.l1_menu_uuid:08x}_"
            f"fw-{self.l1_firmware_uuid:08x}_ps-{self.prescale_column}"
        )

    def as_dict(self):
        return {
            "group_id": self.group_id,
            "hlt_menu_id": self.hlt_menu_id,
            "l1_menu_uuid": self.l1_menu_uuid,
            "l1_firmware_uuid": self.l1_firmware_uuid,
            "prescale_column": self.prescale_column,
        }


@dataclass
class LoadedEvent:
    record: dict
    source_file: str
    source_line: int


@dataclass
class TriggerLibrary:
    metadata: list
    menus: dict
    events: list


def load_l1_menu(path):
    with open(path, encoding="utf-8") as source:
        record = json.load(source)
    if record.get("schema") != "shift-zero-bias-l1-menu" or record.get("schema_version") != 1:
        raise TriggerLibraryError(f"{path}: unsupported L1 menu schema")
    algorithms = record.get("algorithms")
    if not isinstance(algorithms, dict) or not algorithms:
        raise TriggerLibraryError(f"{path}: L1 menu has no algorithm mapping")
    normalized = {}
    for bit_text, name in algorithms.items():
        try:
            bit = int(bit_text)
        except (TypeError, ValueError) as error:
            raise TriggerLibraryError(f"{path}: invalid L1 algorithm bit {bit_text!r}") from error
        if bit < 0 or bit >= 512 or not isinstance(name, str) or not name:
            raise TriggerLibraryError(f"{path}: invalid L1 algorithm mapping {bit_text!r}: {name!r}")
        normalized[bit] = name
    if len(normalized.values()) != len(set(normalized.values())):
        raise TriggerLibraryError(f"{path}: duplicate L1 algorithm names")
    record["algorithms"] = normalized
    record["menu_uuid"] = int(record["menu_uuid"])
    record["firmware_uuid"] = int(record["firmware_uuid"])
    record["source_file"] = path
    return record


def load_l1_menus(paths):
    menus = {}
    for path in paths:
        menu = load_l1_menu(path)
        key = (menu["menu_uuid"], menu["firmware_uuid"])
        if key in menus and menus[key]["algorithms"] != menu["algorithms"]:
            raise TriggerLibraryError(f"{path}: conflicting mapping for L1 menu/firmware UUID {key}")
        menus[key] = menu
    return menus


def _load_json_line(path, line_number, text):
    try:
        record = json.loads(text)
    except json.JSONDecodeError as error:
        raise TriggerLibraryError(f"{path}:{line_number}: invalid JSON: {error}") from error
    if not isinstance(record, dict):
        raise TriggerLibraryError(f"{path}:{line_number}: record must be a JSON object")
    return record


def load_trigger_library(paths):
    metadata = []
    menus = {}
    events = []

    for path in paths:
        with open(path, encoding="utf-8") as source:
            for line_number, text in enumerate(source, start=1):
                if not text.strip():
                    continue
                record = _load_json_line(path, line_number, text)
                record_type = record.get("record_type")
                if record_type == "metadata":
                    if record.get("schema") != SUPPORTED_SCHEMA:
                        raise TriggerLibraryError(
                            f"{path}:{line_number}: unsupported schema {record.get('schema')!r}"
                        )
                    if record.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
                        raise TriggerLibraryError(
                            f"{path}:{line_number}: unsupported schema version "
                            f"{record.get('schema_version')!r}"
                        )
                    metadata.append({"source_file": path, "record": record})
                elif record_type == "hlt_menu":
                    menu_id = record.get("menu_id")
                    paths_in_menu = record.get("paths")
                    if not isinstance(menu_id, str) or not isinstance(paths_in_menu, list):
                        raise TriggerLibraryError(f"{path}:{line_number}: malformed HLT menu record")
                    if menu_id in menus and menus[menu_id] != paths_in_menu:
                        raise TriggerLibraryError(
                            f"{path}:{line_number}: HLT menu ID {menu_id} has conflicting path lists"
                        )
                    menus[menu_id] = paths_in_menu
                elif record_type == "event":
                    events.append(LoadedEvent(record, path, line_number))
                else:
                    raise TriggerLibraryError(
                        f"{path}:{line_number}: unknown record_type {record_type!r}"
                    )

    if not metadata:
        raise TriggerLibraryError("library has no metadata record")
    if not menus:
        raise TriggerLibraryError("library has no HLT menu record")
    if not events:
        raise TriggerLibraryError("library has no event record")
    return TriggerLibrary(metadata, menus, events)


def bx_zero_block(event):
    blocks = event.get("l1_by_bx", {}).get("0")
    if not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict):
        raise TriggerLibraryError("event must contain exactly one uGT algorithm block at relative BX 0")
    return blocks[0]


def event_group_key(event):
    block = bx_zero_block(event)
    try:
        return TriggerGroupKey(
            str(event["hlt_menu_id"]),
            int(block["menu_uuid"]),
            int(block["firmware_uuid"]),
            int(block["prescale_column"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise TriggerLibraryError(f"event has incomplete trigger-group provenance: {error}") from error


def _validate_fired_bits(bits, label):
    if not isinstance(bits, list) or any(not isinstance(bit, int) for bit in bits):
        raise TriggerLibraryError(f"{label} must be a list of integer bit numbers")
    if len(bits) != len(set(bits)):
        raise TriggerLibraryError(f"{label} contains duplicate bit numbers")
    if any(bit < 0 or bit >= 512 for bit in bits):
        raise TriggerLibraryError(f"{label} contains a bit outside [0, 511]")


def validate_trigger_library(library):
    errors = []
    warnings = []
    seen_events = {}
    groups = defaultdict(list)
    missing_tcds_count = 0

    for loaded in library.events:
        event = loaded.record
        location = f"{loaded.source_file}:{loaded.source_line}"
        try:
            identity = (int(event["run"]), int(event["lumi"]), int(event["event"]))
            if identity in seen_events:
                errors.append(f"{location}: duplicate event {identity}; first at {seen_events[identity]}")
            else:
                seen_events[identity] = location

            menu_id = event["hlt_menu_id"]
            if menu_id not in library.menus:
                raise TriggerLibraryError(f"unknown HLT menu ID {menu_id}")
            menu_paths = set(library.menus[menu_id])
            for field in ("hlt_accepted", "hlt_errors"):
                decisions = event.get(field)
                if not isinstance(decisions, list) or any(path not in menu_paths for path in decisions):
                    raise TriggerLibraryError(f"{field} contains a path absent from HLT menu {menu_id}")

            l1_by_bx = event.get("l1_by_bx")
            if not isinstance(l1_by_bx, dict) or "0" not in l1_by_bx:
                raise TriggerLibraryError("missing l1_by_bx or relative BX 0")
            for bx, blocks in l1_by_bx.items():
                if not isinstance(blocks, list) or not blocks:
                    raise TriggerLibraryError(f"relative BX {bx} has no algorithm block")
                for block_index, block in enumerate(blocks):
                    prefix = f"relative BX {bx} block {block_index}"
                    for stage in ("initial", "intermediate", "final"):
                        _validate_fired_bits(block.get(stage), f"{prefix} {stage}")
                    initial = set(block["initial"])
                    intermediate = set(block["intermediate"])
                    final = set(block["final"])
                    if not intermediate <= initial:
                        raise TriggerLibraryError(f"{prefix}: intermediate bits are not a subset of initial")
                    if not final <= intermediate:
                        raise TriggerLibraryError(f"{prefix}: final bits are not a subset of intermediate")
                    if block.get("final_or") and not final:
                        raise TriggerLibraryError(f"{prefix}: final_or is true but final bit set is empty")

            tcds = event.get("tcds")
            if tcds is None:
                missing_tcds_count += 1
            else:
                history = tcds.get("l1a_history")
                if not isinstance(history, list):
                    raise TriggerLibraryError("TCDS l1a_history must be a list")
                indices = [entry.get("index") for entry in history]
                deltas = [entry.get("delta_bx") for entry in history]
                if any(not isinstance(value, int) for value in indices + deltas):
                    raise TriggerLibraryError("TCDS history indices and delta_bx values must be integers")
                if indices != sorted(indices, reverse=True):
                    raise TriggerLibraryError("TCDS L1A history indices must be nearest-first")
                history_violations = validate_recorded_l1a_history(deltas)
                if history_violations:
                    raise TriggerLibraryError(
                        "TCDS L1A history violates Run-3 rule candidate: "
                        + "; ".join(history_violations)
                    )

            groups[event_group_key(event)].append(loaded)
        except (KeyError, TypeError, ValueError, TriggerLibraryError) as error:
            errors.append(f"{location}: {error}")

    source_datasets = {
        entry["record"].get("source_dataset", "") for entry in library.metadata
    }
    if "" in source_datasets:
        warnings.append("at least one input lacks source_dataset provenance")
    if missing_tcds_count:
        warnings.append(
            f"{missing_tcds_count} events lack TCDS L1A-history provenance"
        )
    warnings.append(
        "fill, luminosity/pileup and colliding-bunch metadata are not yet present in schema version 1"
    )
    return errors, warnings, dict(groups)


def summarize_group(key, loaded_events, menus, top_pairs=25, l1_menu=None):
    hlt_counts = Counter()
    hlt_pairs = Counter()
    l1_counts = {stage: Counter() for stage in ("initial", "intermediate", "final")}
    final_or_count = 0
    runs = set()
    lumis = set()
    absolute_bxs = set()

    for loaded in loaded_events:
        event = loaded.record
        accepted = sorted(set(event["hlt_accepted"]))
        hlt_counts.update(accepted)
        hlt_pairs.update(combinations(accepted, 2))
        block = bx_zero_block(event)
        for stage in l1_counts:
            l1_counts[stage].update(block[stage])
        final_or_count += int(bool(block["final_or"]))
        runs.add(event["run"])
        lumis.add((event["run"], event["lumi"]))
        absolute_bxs.add(event["bx"])

    event_count = len(loaded_events)
    result = {
        **key.as_dict(),
        "event_count": event_count,
        "runs": sorted(runs),
        "run_lumis": [list(item) for item in sorted(lumis)],
        "observed_absolute_bx_count": len(absolute_bxs),
        "hlt_path_count": len(menus[key.hlt_menu_id]),
        "final_or_count": final_or_count,
        "final_or_fraction": final_or_count / event_count,
        "hlt_accept_counts": dict(sorted(hlt_counts.items())),
        "l1_bit_counts": {
            stage: {str(bit): count for bit, count in sorted(counter.items())}
            for stage, counter in l1_counts.items()
        },
        "top_hlt_accept_pairs": [
            {"paths": list(pair), "count": count}
            for pair, count in hlt_pairs.most_common(top_pairs)
        ],
    }
    if l1_menu:
        result["l1_menu_source"] = l1_menu["source_file"]
        result["l1_algorithm_counts"] = {
            stage: [
                {
                    "bit": bit,
                    "name": l1_menu["algorithms"].get(bit, ""),
                    "count": count,
                }
                for bit, count in sorted(counter.items())
            ]
            for stage, counter in l1_counts.items()
        }
    return result


def sample_loaded_events(loaded_events, count, seed, without_replacement=False):
    if count < 0:
        raise TriggerLibraryError("sample count must be non-negative")
    if without_replacement and count > len(loaded_events):
        raise TriggerLibraryError(
            f"cannot sample {count} records without replacement from {len(loaded_events)} events"
        )
    generator = random.Random(seed)
    if without_replacement:
        return generator.sample(loaded_events, count)
    return [generator.choice(loaded_events) for _ in range(count)]
