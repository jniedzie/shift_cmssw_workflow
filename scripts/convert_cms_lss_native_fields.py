#!/usr/bin/env python3
"""Translate supplied native FLUKA 2D field payloads without placing them.

The output map syntax is consumed by ShiftLssMagneticFieldESProducer.  This
step preserves native numeric payloads and metadata, but deliberately does not
choose a FLUKA-to-CMSSW alignment or a field-domain approximation.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import sys

from audit_cms_lss_source import (
    IntakeError,
    active_lines,
    audit_bundle,
    legacy_map_comparison,
    native_cards,
    native_map_summary,
    safe_archive_members,
)


class FieldTranslationError(ValueError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _integer(value, label):
    number = float(value)
    if not math.isfinite(number) or not number.is_integer() or number < 2:
        raise FieldTranslationError(f"{label} must be an integer >= 2")
    return int(number)


def legacy_map_text(summary, values):
    """Serialize the supported native 2D subset losslessly as text numbers."""
    if summary.get("metadata_status") != "supported-comparison-subset":
        raise FieldTranslationError(
            "native field is outside the supported, audited 2D subset"
        )
    metadata = summary["metadata"]
    if any(metadata[key] != 0.0 for key in
           ("azimuth_degrees", "bend_radius_cm", "sagitta_cm")):
        raise FieldTranslationError(
            "nonzero azimuth, bend radius, or sagitta is not representable"
        )
    field_type = metadata["type"]
    if field_type not in ("QUAD", "QUADINT", "INTER2D", "KICKINT"):
        raise FieldTranslationError(f"unsupported native 2D type {field_type}")
    mapped = field_type != "QUAD"
    if mapped:
        if metadata["x_grid"] is None or metadata["y_grid"] is None:
            raise FieldTranslationError("mapped native field lacks grid metadata")
        nx = _integer(metadata["x_grid"][2], "x grid size")
        ny = _integer(metadata["y_grid"][2], "y grid size")
        if len(values) != 2 * nx * ny:
            raise FieldTranslationError(
                f"expected {2 * nx * ny} native values, found {len(values)}"
            )
    elif values:
        raise FieldTranslationError("analytic quadrupole unexpectedly has map data")

    origin = metadata["analytical_origin_cm"]
    lines = [
        f"TYPE {field_type}",
        f"SYMMETRY {metadata['symmetry']}",
        "QORIGIN " + " ".join(format(float(value), ".17g") for value in origin),
        f"QRADIUS {format(float(metadata['core_radius_cm']), '.17g')}",
    ]
    if mapped:
        lines.extend([
            "XGRID " + " ".join(format(float(value), ".17g")
                                  for value in metadata["x_grid"]),
            "YGRID " + " ".join(format(float(value), ".17g")
                                  for value in metadata["y_grid"]),
            "DATA",
        ])
        lines.extend(
            f"{format(values[index], '.17g')} {format(values[index + 1], '.17g')}"
            for index in range(0, len(values), 2)
        )
    return "\n".join(lines) + "\n"


def convert_native_fields(source_dir, deck_name, output_dir):
    source_dir, output_dir = Path(source_dir).resolve(), Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FieldTranslationError("output directory must be empty")
    report = audit_bundle(source_dir)
    if deck_name not in report["decks"]:
        raise FieldTranslationError(f"unknown supplied deck {deck_name}")
    deck = report["decks"][deck_name]
    referenced = sorted({card["sdum"] for card in deck["native_field_assignments"]})

    archive_paths = sorted(source_dir.glob("*.zip"))
    if len(archive_paths) != 1:
        raise FieldTranslationError("expected exactly one supplied field archive")
    archive_path = archive_paths[0]
    members = safe_archive_members(archive_path)
    definitions = {}
    for member, payload in members.items():
        if not member.endswith(".inp"):
            continue
        lines, symbols, _ = active_lines(payload.decode("utf-8"), member)
        if symbols:
            raise FieldTranslationError(f"{member}: preprocessor state is unsupported")
        cards = native_cards(lines, member)
        summary, values = native_map_summary(cards)
        primaries = [card for card in cards
                     if card["card"] == "MGNCREAT" and card["sdum"] not in ("&", "&&")]
        if len(primaries) != 1:
            continue
        name = primaries[0]["sdum"]
        if name in definitions:
            raise FieldTranslationError(f"duplicate archived field definition {name}")
        definitions[name] = (member, payload, summary, values)

    all_defined = {card["sdum"] for card in deck["native_field_definitions"]}
    inline = (set(referenced) & all_defined) - set(definitions)
    missing = sorted(set(referenced) - set(definitions) - inline)
    if missing:
        raise FieldTranslationError("missing referenced native fields: " + ", ".join(missing))

    output_dir.mkdir(parents=True, exist_ok=True)
    maps = {}
    for name in sorted(set(referenced) & set(definitions)):
        member, payload, summary, values = definitions[name]
        text = legacy_map_text(summary, values)
        destination = output_dir / f"{name}.dat"
        destination.write_text(text, encoding="ascii")
        roundtrip = legacy_map_comparison(destination, summary, values)
        if not roundtrip["comparison_equal"]:
            raise FieldTranslationError(
                f"{name}: written map failed numeric/metadata round-trip validation"
            )
        maps[name] = {
            "archive_member": member,
            "archive_member_sha256": sha256(payload),
            "metadata": summary["metadata"],
            "native_numeric_array_sha256": summary["numeric_array_sha256"],
            "native_numeric_value_count": summary["numeric_value_count"],
            "output": destination.name,
            "output_sha256": sha256(text.encode("ascii")),
            "roundtrip_metadata_equal": roundtrip["metadata_equal"],
            "roundtrip_numeric_arrays_equal": roundtrip["numeric_arrays_equal"],
        }

    inline_definitions = {}
    for name in sorted(inline):
        cards = [card for card in deck["native_field_definitions"]
                 if card["sdum"] == name]
        summary, values = native_map_summary(cards)
        if values or summary.get("metadata_status") != "supported-comparison-subset":
            raise FieldTranslationError(
                f"{name}: inline field is outside the supported analytic subset"
            )
        metadata = summary["metadata"]
        if metadata["type"] != "DIPOLE" or any(metadata[key] != 0.0 for key in
                                                ("azimuth_degrees", "bend_radius_cm", "sagitta_cm")):
            raise FieldTranslationError(
                f"{name}: inline field is not a straight local-Y analytic dipole"
            )
        inline_definitions[name] = {
            "cards": summary["cards"],
            "metadata": metadata,
            "field_expression": "Bx=0, By=MGNFIELD.WHAT(1) tesla, Bz=0",
            "reference": "https://flukafiles.web.cern.ch/manual/chapters/description_input/description_options/mgncreat.html",
        }

    deck_bytes = (source_dir / deck_name).read_bytes()
    unresolved_includes = [item["basename"] for item in deck["include_resolution"]
                           if item["resolution"] != "available-by-basename"]
    manifest = {
        "schema": "shift-cms-lss-native-field-maps-v1",
        "production_ready": False,
        "source_deck": deck_name,
        "source_deck_sha256": sha256(deck_bytes),
        "source_archive": archive_path.name,
        "source_archive_sha256": sha256(archive_path.read_bytes()),
        "referenced_fields": referenced,
        "inline_analytic_fields": sorted(set(referenced) & inline),
        "inline_analytic_definitions": inline_definitions,
        "maps": maps,
        "assignments": deck["native_field_assignments"],
        "unresolved_includes": unresolved_includes,
        "limitations": [
            "No FLUKA-to-CMSSW coordinate transform has been selected.",
            "Field support domains and native interpolation boundaries require independent validation.",
            "The absent MB.inp include remains unresolved even though field MB is not assigned.",
        ],
    }
    manifest_path = output_dir / "field_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--deck", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = convert_native_fields(args.source_dir, args.deck, args.output_dir)
    except (FieldTranslationError, IntakeError, OSError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {len(manifest['maps'])} native field maps; production_ready=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
