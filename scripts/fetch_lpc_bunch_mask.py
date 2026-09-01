#!/usr/bin/env python3
"""Normalize an official LPC fill-scheme response into a CMS BX mask."""

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import URLError
from urllib.request import urlopen


SCHEMA = "cms-lpc-ip5-bunch-mask"
SCHEMA_VERSION = 1
ORBIT_SLOTS = 3564
LPC_URL = "https://lpc.web.cern.ch/cgi-bin/schemeInfo.py?fill={fill}&fmt=json"


class FillingSchemeError(RuntimeError):
    pass


def _legacy_beam_rows(csv_text, beam):
    lines = csv_text.splitlines()
    marker = f"BEAM {beam}"
    try:
        start = lines.index(marker)
    except ValueError as error:
        raise FillingSchemeError(f"missing {marker} table") from error
    if start + 1 >= len(lines):
        raise FillingSchemeError(f"missing {marker} header")
    block = [lines[start + 1]]
    for line in lines[start + 2:]:
        if not line.strip():
            break
        block.append(line)
    reader = csv.DictReader(io.StringIO("\n".join(block)))
    required = {"RFbucket", "Slot", "Head-On IP5"}
    if not reader.fieldnames or not required <= set(reader.fieldnames):
        raise FillingSchemeError(f"{marker} table lacks required columns")
    result = []
    for row in reader:
        try:
            slot = int(row["Slot"])
            rf_bucket = int(row["RFbucket"])
            head_on_ip5 = int(row["Head-On IP5"])
        except (TypeError, ValueError) as error:
            raise FillingSchemeError(f"invalid {marker} row: {row}") from error
        if not 1 <= slot <= ORBIT_SLOTS:
            raise FillingSchemeError(f"{marker} slot {slot} is outside 1..{ORBIT_SLOTS}")
        if head_on_ip5 not in (0, 1):
            raise FillingSchemeError(f"{marker} slot {slot} has non-boolean Head-On IP5")
        result.append(
            {"slot": slot, "rf_bucket": rf_bucket, "head_on_ip5": head_on_ip5}
        )
    if not result:
        raise FillingSchemeError(f"{marker} table is empty")
    if len({row["slot"] for row in result}) != len(result):
        raise FillingSchemeError(f"{marker} table contains duplicate slots")
    return result


def _rf_bucket_to_slot(value, marker):
    try:
        rf_bucket = int(value)
    except (TypeError, ValueError) as error:
        raise FillingSchemeError(f"invalid {marker} RF bucket {value!r}") from error
    if not 1 <= rf_bucket <= 35631 or (rf_bucket - 1) % 10:
        raise FillingSchemeError(
            f"{marker} RF bucket {rf_bucket} does not identify a 25 ns orbit slot"
        )
    return rf_bucket, (rf_bucket - 1) // 10 + 1


def _head_on_beam_rows(csv_text, beam):
    lines = csv_text.splitlines()
    marker = f"HEAD ON COLLISIONS FOR B{beam}"
    try:
        start = lines.index(marker)
    except ValueError as error:
        raise FillingSchemeError(f"missing {marker} table") from error
    if start + 1 >= len(lines):
        raise FillingSchemeError(f"missing {marker} header")
    block = [lines[start + 1]]
    for line in lines[start + 2:]:
        if not line.strip():
            break
        block.append(line)
    reader = csv.DictReader(io.StringIO("\n".join(block)))
    bucket_field = f"B{beam} bucket number"
    required = {bucket_field, "IP5"}
    if not reader.fieldnames or not required <= set(reader.fieldnames):
        raise FillingSchemeError(f"{marker} table lacks required columns")
    result = []
    for row in reader:
        rf_bucket, slot = _rf_bucket_to_slot(row[bucket_field], marker)
        ip5_value = row["IP5"].strip()
        if ip5_value == "-":
            head_on_ip5 = 0
        else:
            ip5_bucket, _ = _rf_bucket_to_slot(ip5_value, f"{marker} IP5")
            if ip5_bucket != rf_bucket:
                raise FillingSchemeError(
                    f"{marker} RF bucket {rf_bucket} maps to unexpected IP5 bucket "
                    f"{ip5_bucket}"
                )
            head_on_ip5 = 1
        result.append(
            {"slot": slot, "rf_bucket": rf_bucket, "head_on_ip5": head_on_ip5}
        )
    if not result:
        raise FillingSchemeError(f"{marker} table is empty")
    if len({row["slot"] for row in result}) != len(result):
        raise FillingSchemeError(f"{marker} table contains duplicate slots")
    return result


def _beam_rows(csv_text, beam):
    if f"HEAD ON COLLISIONS FOR B{beam}" in csv_text:
        return _head_on_beam_rows(csv_text, beam)
    return _legacy_beam_rows(csv_text, beam)


def normalize_lpc_response(payload, fill):
    fill_key = str(fill)
    try:
        fill_record = payload["fills"][fill_key]
        scheme_name = fill_record["name"]
        csv_text = fill_record["csv"]
    except (KeyError, TypeError) as error:
        message = payload.get("error") if isinstance(payload, dict) else None
        raise FillingSchemeError(message or f"LPC response has no fill {fill}") from error

    beam1 = _beam_rows(csv_text, 1)
    beam2 = _beam_rows(csv_text, 2)
    beam1_slots = {row["slot"] for row in beam1}
    beam2_slots = {row["slot"] for row in beam2}
    beam1_ip5 = {row["slot"] for row in beam1 if row["head_on_ip5"]}
    beam2_ip5 = {row["slot"] for row in beam2 if row["head_on_ip5"]}
    if beam1_ip5 != beam2_ip5:
        raise FillingSchemeError("beam tables disagree on the IP5 head-on BX slots")

    match = re.search(r"^Collisions at IP1&5\s*:\s*(\d+)\s*$", csv_text, re.MULTILINE)
    declared = int(match.group(1)) if match else None
    if declared is not None and declared != len(beam1_ip5):
        raise FillingSchemeError(
            f"IP5 mask has {len(beam1_ip5)} slots but CSV declares {declared}"
        )

    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "fill_number": int(fill),
        "scheme_name": scheme_name,
        "orbit_slots": ORBIT_SLOTS,
        "beam1_filled_bx_slots": sorted(beam1_slots),
        "beam2_filled_bx_slots": sorted(beam2_slots),
        "colliding_ip5_bx_slots": sorted(beam1_ip5),
        "counts": {
            "beam1_filled": len(beam1_slots),
            "beam2_filled": len(beam2_slots),
            "colliding_ip5": len(beam1_ip5),
        },
        "source": {
            "service": "CERN LHC Programme Coordination filling-scheme service",
            "url": LPC_URL.format(fill=fill),
            "csv_sha256": hashlib.sha256(csv_text.encode("utf-8")).hexdigest(),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fill", type=int)
    parser.add_argument("--input-json", help="saved LPC JSON response; otherwise fetch live")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.input_json:
            with open(args.input_json, encoding="utf-8") as source:
                payload = json.load(source)
        else:
            with urlopen(LPC_URL.format(fill=args.fill), timeout=60) as response:
                payload = json.load(response)
        result = normalize_lpc_response(payload, args.fill)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f"{output.name}.partial.{os.getpid()}")
        with temporary.open("w", encoding="utf-8") as destination:
            json.dump(result, destination, indent=2, sort_keys=True)
            destination.write("\n")
        os.replace(temporary, output)
    except (OSError, URLError, ValueError, FillingSchemeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        f"wrote fill {result['fill_number']} scheme {result['scheme_name']}: "
        f"{result['counts']['beam1_filled']} B1, "
        f"{result['counts']['beam2_filled']} B2, "
        f"{result['counts']['colliding_ip5']} colliding IP5 BX slots"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
