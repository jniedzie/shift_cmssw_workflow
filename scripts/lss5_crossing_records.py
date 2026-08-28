#!/usr/bin/env python3

"""Strict parser for FLUKA particle records at an LSS5/CMS interface."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile


SCHEMA = "shift-fluka-interface-crossings"
SCHEMA_VERSION = 1


class CrossingFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedCrossings:
    header_lines: list
    events: list
    content_sha256: str
    particle_count: int


@dataclass(frozen=True)
class CrossingScan:
    header_lines: list
    content_sha256: str
    event_count: int
    particle_count: int


def _open_text(path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="ascii", newline="")
    return open(path, encoding="ascii", newline="")


def _parse_integer(token, label, location):
    try:
        return int(token)
    except ValueError as error:
        raise CrossingFormatError(f"{location}: {label} is not an integer: {token!r}") from error


def _parse_decimal(token, label, location):
    try:
        value = Decimal(token)
    except InvalidOperation as error:
        raise CrossingFormatError(f"{location}: {label} is not numeric: {token!r}") from error
    if not value.is_finite():
        raise CrossingFormatError(f"{location}: {label} must be finite: {token!r}")
    return value


def _parse_particle(tokens, location):
    if len(tokens) != 12:
        raise CrossingFormatError(f"{location}: expected 12 columns, found {len(tokens)}")

    fluka_run = _parse_integer(tokens[0], "FLUKA run number", location)
    primary_event = _parse_integer(tokens[1], "primary event number", location)
    particle_id = _parse_integer(tokens[2], "FLUKA particle ID", location)
    generation = _parse_integer(tokens[11], "generation number", location)
    if fluka_run < 0 or primary_event < 0 or generation < 0:
        raise CrossingFormatError(f"{location}: run, event, and generation must be non-negative")

    decimal_labels = (
        "kinetic energy",
        "statistical weight",
        "crossing x",
        "crossing y",
        "x direction cosine",
        "y direction cosine",
        "particle age",
        "original collision z",
    )
    decimals = [
        _parse_decimal(token, label, location)
        for token, label in zip(tokens[3:11], decimal_labels)
    ]
    kinetic_energy, weight, _, _, x_cosine, y_cosine, age, _ = decimals
    if kinetic_energy < 0 or weight < 0 or age < 0:
        raise CrossingFormatError(
            f"{location}: kinetic energy, statistical weight, and particle age must be non-negative"
        )
    transverse_norm_squared = x_cosine * x_cosine + y_cosine * y_cosine
    if transverse_norm_squared > Decimal("1.000000000001"):
        raise CrossingFormatError(f"{location}: transverse direction-cosine norm exceeds one")

    particle = {
        "fluka_particle_id": particle_id,
        "kinetic_energy_gev": tokens[3],
        "statistical_weight": tokens[4],
        "crossing_x_cm": tokens[5],
        "crossing_y_cm": tokens[6],
        "x_direction_cosine": tokens[7],
        "y_direction_cosine": tokens[8],
        "longitudinal_direction_cosine_magnitude": format(
            math.sqrt(max(0.0, 1.0 - float(transverse_norm_squared))), ".17g"
        ),
        "longitudinal_direction_sign": None,
        "particle_age_seconds": tokens[9],
        "original_collision_z_cm": tokens[10],
        "generation": generation,
    }
    return (fluka_run, primary_event), particle


def _iter_crossings(path, content_hash=None, header_lines=None):
    path = Path(path)
    current_key = None
    current_event = None

    with _open_text(path) as source:
        for line_number, line in enumerate(source, start=1):
            if content_hash is not None:
                content_hash.update(line.encode("ascii"))
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                if header_lines is not None:
                    header_lines.append({"source_line": line_number, "text": stripped})
                continue

            key, particle = _parse_particle(stripped.split(), f"{path}:{line_number}")
            if key != current_key:
                if current_key is not None:
                    yield current_event
                if current_key is not None and key < current_key:
                    raise CrossingFormatError(
                        f"{path}:{line_number}: event keys must be contiguous and nondecreasing; "
                        f"found {key} after {current_key}"
                    )
                current_key = key
                current_event = {
                    "record_type": "event",
                    "fluka_run": key[0],
                    "primary_event": key[1],
                    "first_source_line": line_number,
                    "particles": [],
                }
            current_event["particles"].append(particle)
            current_event["last_source_line"] = line_number

    if current_event is not None:
        yield current_event


def scan_crossings(path):
    """Validate and summarize a source without retaining particle records."""

    content_hash = hashlib.sha256()
    header_lines = []
    event_count = 0
    particle_count = 0
    for event in _iter_crossings(path, content_hash, header_lines):
        event_count += 1
        particle_count += len(event["particles"])

    if event_count == 0:
        raise CrossingFormatError(f"{path}: no particle records found")
    return CrossingScan(
        header_lines=header_lines,
        content_sha256=content_hash.hexdigest(),
        event_count=event_count,
        particle_count=particle_count,
    )


def iter_crossing_events(path):
    """Yield validated, contiguous event records in source order."""

    yield from _iter_crossings(path)


def parse_crossings(path):
    """Parse a small source file into memory, primarily for focused tests."""

    scan = scan_crossings(path)
    events = list(iter_crossing_events(path))

    return ParsedCrossings(
        header_lines=scan.header_lines,
        events=events,
        content_sha256=scan.content_sha256,
        particle_count=scan.particle_count,
    )


def metadata_record(parsed, source_path, model_label, interface_label):
    return {
        "record_type": "metadata",
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "source": {
            "path": str(Path(source_path).resolve()),
            "decompressed_content_sha256": parsed.content_sha256,
            "header_lines": parsed.header_lines,
        },
        "model": {
            "label": model_label,
            "status": "software_fixture",
            "run3_ir5_geometry_validated": False,
            "run3_ir5_magnetic_fields_validated": False,
        },
        "interface": {
            "label": interface_label,
            "coordinate_transform": None,
            "scoring_surface_position": None,
            "longitudinal_direction_sign": None,
            "fluka_to_pdg_mapping": None,
        },
        "units": {
            "kinetic_energy": "GeV",
            "crossing_x": "cm",
            "crossing_y": "cm",
            "particle_age": "s",
            "original_collision_z": "cm",
        },
        "counts": {
            "events": getattr(parsed, "event_count", len(getattr(parsed, "events", []))),
            "particles": parsed.particle_count,
        },
        "limitations": [
            "This record does not establish a Run-3 IR5 geometry or magnetic-field model.",
            "The source does not encode the scoring-surface position or signed longitudinal direction.",
            "Coordinate transforms and FLUKA-to-PDG particle mapping remain unresolved.",
        ],
    }


def write_jsonl(parsed, output_path, source_path, model_label, interface_label):
    metadata = metadata_record(parsed, source_path, model_label, interface_label)
    with open(output_path, "w", encoding="utf-8") as output:
        output.write(json.dumps(metadata, sort_keys=True) + "\n")
        for event in parsed.events:
            output.write(json.dumps(event, sort_keys=True) + "\n")


def convert_to_jsonl(source_path, output_path, model_label, interface_label):
    """Validate once, then stream records without retaining the full source."""

    scan = scan_crossings(source_path)
    metadata = metadata_record(scan, source_path, model_label, interface_label)
    output_path = Path(output_path)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".partial",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        open_output = gzip.open if str(output_path).endswith(".gz") else open
        with open_output(temporary_path, "wt", encoding="utf-8") as output:
            output.write(json.dumps(metadata, sort_keys=True) + "\n")
            for event in iter_crossing_events(source_path):
                output.write(json.dumps(event, sort_keys=True) + "\n")
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return scan
