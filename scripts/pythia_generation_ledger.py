"""Extract one frozen GEN run's proposal counters from Pythia statistics.

CMS can print the same terminal Pythia table twice. Identical tables count
once; different snapshots, generator streams or concatenated runs are refused
because their ownership cannot be established from this log alone. Printed
cross sections are diagnostics. GenRunInfoProduct remains the rate authority.
These counters describe Pythia trials, not detector filtering or target flux.
"""

from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import Path
import re


_START = "PYTHIA Event and Cross Section Statistics"
_END = "End " + _START


def _counter_values(text):
    fields = text.split()
    if len(fields) != 3 or any(not re.fullmatch(r"[0-9]+", field) for field in fields):
        raise ValueError("Invalid Pythia Tried/Selected/Accepted counters")
    tried, selected, accepted = map(int, fields)
    if not tried >= selected >= accepted:
        raise ValueError("Pythia counters require tried >= selected >= accepted")
    return dict(n_tried=tried, n_selected=selected, n_accepted=accepted)


def _cross_section_values(text):
    fields = text.split()
    if len(fields) != 2:
        raise ValueError("Missing Pythia printed cross section or error")
    numbers = []
    for field in fields:
        try:
            value = float(field.replace("D", "E").replace("d", "e"))
        except ValueError as error:
            raise ValueError("Invalid Pythia printed cross section") from error
        if not math.isfinite(value) or value < 0:
            raise ValueError("Pythia printed cross section and error must be finite and nonnegative")
        numbers.append(value)
    return dict(printed_cross_section_mb=numbers[0], printed_cross_section_error_mb=numbers[1])


def _printed_interval(text):
    """Decimal interval represented by a rounded printed cross-section value."""
    try:
        value = Decimal(text.replace("D", "E").replace("d", "e"))
        half_step = Decimal(5).scaleb(value.as_tuple().exponent - 1)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Invalid printed Pythia cross-section precision") from error
    return value - half_step, value + half_step


def _parse_table(lines):
    header = " ".join(" ".join(lines).split())
    if not re.search(r"\bTried\s+Selected\s+Accepted\b", header) or "(mb)" not in header:
        raise ValueError("Pythia statistics lack their counter header or mb unit")
    rows, sums, printed_rates = [], [], []
    for line in lines:
        columns = [column.strip() for column in line.split("|")]
        if len(columns) < 3:
            continue
        name = columns[1]
        if not any(columns) or (len(columns) == 3 and not columns[0] and not columns[2]
                                and re.fullmatch(r"-+", name)):
            continue
        if name == "sum":
            if len(columns) != 5 or columns[0] or columns[4]:
                raise ValueError("Malformed Pythia statistics sum row")
            sums.append({**_counter_values(columns[2]), **_cross_section_values(columns[3])})
            printed_rates.append(("sum", columns[3].split()[0]))
            continue
        match = re.fullmatch(r"(.+?)\s+([0-9]+)", name)
        # A populated counter column belongs to a subprocess, not a header.
        # Recognize its code before checking columns so malformed zero-yield
        # rows cannot disappear merely because their omission leaves sums equal.
        header_row = len(columns) == 5 and (
            not columns[2] or "Number of events" in columns[2] or "Tried" in columns[2])
        if match is None and header_row:
            continue
        if match is None or len(columns) != 5 or columns[0] or columns[4]:
            raise ValueError("Malformed Pythia subprocess statistics row")
        rows.append(dict(name=" ".join(match.group(1).split()), code=int(match.group(2)),
                         **_counter_values(columns[2]), **_cross_section_values(columns[3])))
        printed_rates.append(("subprocess", columns[3].split()[0]))
    if len(sums) != 1 or not rows:
        raise ValueError("Require one Pythia sum row and at least one subprocess")
    if len({row["code"] for row in rows}) != len(rows):
        raise ValueError("Duplicate Pythia subprocess code")
    total = sums[0]
    for key in ("n_tried", "n_selected", "n_accepted"):
        if sum(row[key] for row in rows) != total[key]:
            raise ValueError("Pythia subprocess counters differ from the sum row")
    lower = upper = Decimal(0)
    for kind, token in printed_rates:
        interval = _printed_interval(token)
        if kind == "sum":
            total_interval = interval
        else:
            lower += interval[0]
            upper += interval[1]
    if upper < total_interval[0] or lower > total_interval[1]:
        raise ValueError("Pythia printed subprocess cross sections differ from the sum row")
    return {**total, "subprocesses": sorted(rows, key=lambda row: row["code"])}


def parse_pythia_generation_ledger(log_text, expected_accepted=None):
    """Validate terminal statistics, collapsing only identical repeated tables.

    ``expected_accepted`` is the audited persisted GEN event count for the
    unfiltered, one-stream pilot. Do not use this equality for filtered GEN.
    """
    if not isinstance(log_text, str):
        raise TypeError("Pythia log must be text")
    if expected_accepted is not None and (
            isinstance(expected_accepted, bool) or not isinstance(expected_accepted, int)
            or expected_accepted < 0):
        raise ValueError("Expected accepted-event count must be a nonnegative integer")
    tables, ranges, current, start = [], [], None, None
    for number, line in enumerate(log_text.splitlines(), 1):
        if _END in line:
            if current is None:
                raise ValueError("Pythia statistics end has no start")
            tables.append(_parse_table(current))
            ranges.append([start, number])
            current, start = None, None
        elif _START in line:
            if current is not None:
                raise ValueError("Nested or truncated Pythia statistics block")
            current, start = [], number
        elif current is not None:
            current.append(line)
    if current is not None:
        raise ValueError("Truncated Pythia statistics block")
    if not tables:
        raise ValueError("Missing Pythia generation statistics")
    if any(table != tables[0] for table in tables[1:]):
        raise ValueError("Conflicting Pythia statistics; cannot combine runs, streams or snapshots")
    total = tables[0]
    if expected_accepted is not None and total["n_accepted"] != expected_accepted:
        raise ValueError("Pythia accepted count differs from expected persisted GEN events")
    return dict(schema="shift-pythia-generation-ledger-v1", **total,
                tried_minus_selected=total["n_tried"] - total["n_selected"],
                selected_minus_accepted=total["n_selected"] - total["n_accepted"],
                statistics_blocks_seen=len(tables), identical_duplicate_blocks=len(tables) - 1,
                statistics_block_line_ranges=ranges, expected_accepted=expected_accepted,
                counter_scope="one unfiltered single-generator run; Pythia internal trials, not target exposure",
                cross_section_authority="GEN GenRunInfoProduct; printed log cross section is diagnostic")


def read_pythia_generation_ledger(log_path, expected_accepted=None):
    """Read a frozen log and retain its digest alongside the validated ledger."""
    path = Path(log_path).resolve()
    payload = path.read_bytes()
    ledger = parse_pythia_generation_ledger(payload.decode("utf-8"), expected_accepted)
    return {**ledger, "log_path": str(path), "log_sha256": hashlib.sha256(payload).hexdigest()}
