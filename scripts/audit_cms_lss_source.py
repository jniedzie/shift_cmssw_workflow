#!/usr/bin/env python3
"""Inventory a supplied CMS LSS bundle without converting or changing it.

Native FLUKA field cards are retained verbatim. This is an intake audit, not
an implementation or validation of MGNCREAT/MGNFIELD physics semantics.
"""

import argparse
from collections import Counter
import difflib
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import zipfile


FIELD_MANUAL = "https://flukafiles.web.cern.ch/manual/chapters/description_input/description_options/mgnfield.html"
CREATE_MANUAL = "https://flukafiles.web.cern.ch/manual/chapters/description_input/description_options/mgncreat.html"


class IntakeError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_archive_members(path):
    """Read safe, unique regular entries in memory; never extract the ZIP."""
    members = {}
    seen = set()
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            name = entry.filename
            parts = PurePosixPath(name).parts
            mode = entry.external_attr >> 16
            if (not name or name.startswith("/") or "\\" in name
                    or ".." in parts or ":" in name or name in seen
                    or stat.S_ISLNK(mode)):
                raise IntakeError(f"unsafe or duplicate ZIP member: {name!r}")
            seen.add(name)
            if entry.is_dir():
                continue
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG):
                raise IntakeError(f"non-regular ZIP member: {name!r}")
            members[name] = archive.read(entry)
    return members


def active_lines(text, source):
    """Resolve the supplied decks' simple named-symbol conditionals.

    Unsupported expressions fail closed instead of applying C-preprocessor
    assumptions to FLUKA input. Includes are audited separately; this function
    deliberately does not pretend to preprocess unavailable include contents.
    """
    symbols = set()
    stack = []
    active = True
    output, directives = [], []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.startswith("#"):
            if active:
                output.append((number, raw))
            continue
        parts = raw[1:].split(None, 1)
        command = parts[0] if parts else ""
        argument = parts[1].strip() if len(parts) > 1 else ""
        directives.append({"source": source, "line": number, "raw": raw,
                           "parent_active": active})
        if command in ("if", "ifdef", "ifndef", "elif"):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", argument):
                raise IntakeError(f"{source}:{number}: unsupported conditional {raw}")
            condition = argument in symbols
            if command == "ifndef":
                condition = not condition
            if command == "elif":
                if not stack or stack[-1][2]:
                    raise IntakeError(f"{source}:{number}: unmatched elif")
                parent, taken, _ = stack[-1]
                active = parent and not taken and condition
                stack[-1][1] = taken or condition
            else:
                stack.append([active, condition, False])
                active = active and condition
        elif command == "else":
            if argument or not stack or stack[-1][2]:
                raise IntakeError(f"{source}:{number}: unmatched else")
            parent, taken, _ = stack[-1]
            active = parent and not taken
            stack[-1][2] = True
        elif command == "endif":
            if argument or not stack:
                raise IntakeError(f"{source}:{number}: unmatched endif")
            active = stack.pop()[0]
        elif command in ("define", "undef"):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", argument):
                raise IntakeError(f"{source}:{number}: unsupported definition {raw}")
            if active:
                if command == "define":
                    symbols.add(argument)
                else:
                    symbols.discard(argument)
        elif command == "include":
            if active:
                output.append((number, raw))
        else:
            raise IntakeError(f"{source}:{number}: unsupported directive {raw}")
    if stack:
        raise IntakeError(f"{source}: unclosed conditional")
    return output, sorted(symbols), directives


def native_cards(lines, source):
    result = []
    for number, raw in lines:
        keyword = raw.split(None, 1)[0] if raw.strip() else ""
        if keyword not in ("MGNCREAT", "MGNDATA", "MGNFIELD"):
            continue
        tokens = [item.strip() for item in raw.split(",")] if "," in raw else raw.split()
        if keyword == "MGNFIELD" and len(tokens) == 7:
            tokens.append("")  # Global integration-settings card has no SDUM.
        if len(tokens) != 8:
            raise IntakeError(f"{source}:{number}: unsupported native field-card layout: {raw}")
        result.append({"source": source, "line": number, "card": keyword,
                       "what": tokens[1:7], "sdum": tokens[7], "raw": raw})
    return result


def is_named_assignment(card):
    return card["card"] == "MGNFIELD" and bool(
        re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", card["sdum"]))


def finite_number(value):
    number = float(value.replace("D", "E")) if value else 0.0
    if not math.isfinite(number):
        raise IntakeError("non-finite magnetic field value")
    return number


def numeric_digest(values):
    """Canonical numeric-array hash: ordered IEEE754 binary64 big-endian."""
    return digest(b"".join(struct.pack(">d", value) for value in values))


def native_map_summary(cards):
    """Compare only supported map metadata; preserve unsupported cards raw."""
    values = [finite_number(value) for card in cards if card["card"] == "MGNDATA"
              for value in card["what"] if value]
    result = {
        "cards": [card for card in cards if card["card"] != "MGNDATA"],
        "data_card_count": sum(card["card"] == "MGNDATA" for card in cards),
        "numeric_value_count": len(values), "numeric_array_sha256": numeric_digest(values),
        "numeric_array_encoding": "ordered IEEE754 binary64 big-endian",
        "field_evaluation_validated": False,
    }
    creation = [card for card in cards if card["card"] == "MGNCREAT"]
    primaries = [card for card in creation if card["sdum"] not in ("&", "&&")]
    if len(primaries) != 1:
        result["metadata_status"] = "unsupported-number-of-field-definitions"
        return result, values
    first = [finite_number(value) for value in primaries[0]["what"]]
    types = {2: "DIPOLE", 4: "QUAD", 200: "INTER2D", 204: "QUADINT", 202: "KICKINT"}
    symmetry = {0: "NONE", 2: "X", 10: "Y", 12: "XY"}
    if first[0] not in types or first[4] not in symmetry or first[5] != 0:
        result["metadata_status"] = "unsupported-field-type-or-symmetry"
        return result, values
    continuation = {card["sdum"]: [finite_number(value) for value in card["what"]]
                    for card in creation if card["sdum"] in ("&", "&&")}
    if len(creation) != 1 + len(continuation):
        raise IntakeError("duplicate MGNCREAT continuation")
    extra = continuation.get("&", [0.0] * 6)
    bounds = continuation.get("&&", [0.0] * 6)
    if extra[5] != 0 or bounds[2] != 0 or bounds[5] != 0:
        result["metadata_status"] = "unsupported-3d-grid"
        return result, values
    mapped = first[0] >= 200
    metadata = {"type": types[first[0]], "symmetry": symmetry[first[4]],
                "core_radius_cm": abs(first[1]), "analytical_origin_cm": first[2:4] + [0.0],
                "azimuth_degrees": extra[0], "bend_radius_cm": extra[1], "sagitta_cm": extra[2],
                "x_grid": [bounds[0], bounds[3], extra[3]] if mapped else None,
                "y_grid": [bounds[1], bounds[4], extra[4]] if mapped else None}
    result.update(metadata_status="supported-comparison-subset", metadata=metadata,
                  metadata_reference=CREATE_MANUAL,
                  data_encoding_note="Numeric-token sequence comparison only; native field evaluation and MGNDATA packing still require reference validation.")
    return result, values


def legacy_map_comparison(path, native, native_values):
    path = Path(path)
    if not path.is_file():
        return {"path": str(path), "status": "missing", "comparison_equal": False}
    raw = path.read_bytes()
    metadata = {"type": None, "symmetry": "NONE", "core_radius_cm": 0.0,
                "analytical_origin_cm": [0.0, 0.0, 0.0], "azimuth_degrees": 0.0,
                "bend_radius_cm": 0.0, "sagitta_cm": 0.0, "x_grid": None, "y_grid": None}
    values, data_mode = [], False
    for line in raw.decode("utf-8").splitlines():
        tokens = line.split("#", 1)[0].split()
        if not tokens:
            continue
        if data_mode:
            if len(tokens) != 2:
                raise IntakeError(f"{path}: expected legacy two-component data row")
            values.extend(finite_number(token) for token in tokens)
        elif tokens == ["DATA"]:
            data_mode = True
        elif tokens[0] == "TYPE" and len(tokens) == 2:
            metadata["type"] = tokens[1]
        elif tokens[0] == "SYMMETRY" and len(tokens) == 2:
            metadata["symmetry"] = tokens[1]
        elif tokens[0] == "QRADIUS" and len(tokens) == 2:
            metadata["core_radius_cm"] = finite_number(tokens[1])
        elif tokens[0] in ("QORIGIN", "XGRID", "YGRID") and len(tokens) == 4:
            key = {"QORIGIN": "analytical_origin_cm", "XGRID": "x_grid", "YGRID": "y_grid"}[tokens[0]]
            metadata[key] = [finite_number(token) for token in tokens[1:]]
        else:
            raise IntakeError(f"{path}: unsupported legacy metadata: {line}")
    expected = native.get("metadata")
    differences = {key: {"native": expected.get(key), "legacy": value}
                   for key, value in metadata.items() if expected and expected.get(key) != value}
    arrays_equal = native_values == values
    metadata_equal = expected is not None and not differences
    return {"path": str(path.resolve()), "sha256": digest(raw), "status": "compared",
            "numeric_value_count": len(values), "numeric_array_sha256": numeric_digest(values),
            "numeric_arrays_equal": arrays_equal, "metadata": metadata,
            "metadata_equal": metadata_equal, "metadata_differences": differences,
            "comparison_equal": arrays_equal and metadata_equal,
            "placement_or_transport_equivalence": False}


def material_definition_audit(lines, source):
    """Retain duplicate active declarations without guessing override rules."""
    definitions, compositions, assignments = {}, {}, {}
    free_format, previous_compound = False, None
    for number, raw in lines:
        stripped = raw.split("!", 1)[0].strip()
        if not stripped or stripped.startswith(("*", "#")):
            continue
        keyword = stripped.split(None, 1)[0]
        if keyword in ("FREE", "FIXED"):
            free_format = keyword == "FREE"
        if keyword not in ("MATERIAL", "COMPOUND", "ASSIGNMA"):
            previous_compound = None
            continue
        if free_format:
            tokens = [item.strip() for item in stripped.split(",")] if "," in stripped else stripped.split()
            if len(tokens) != 8:
                raise IntakeError(f"{source}:{number}: unsupported free material-card layout")
            what, name = tokens[1:7], tokens[7]
        else:
            what = [raw[start:start + 10].strip() for start in range(10, 70, 10)]
            name = raw[70:80].strip()
        record = {"source": source, "line": number, "raw": raw, "what": what, "sdum": name}
        if keyword == "MATERIAL":
            if not name:
                raise IntakeError(f"{source}:{number}: unnamed MATERIAL needs native resolution")
            record["density_g_cm3"] = finite_number(what[2]) if what[2] else None
            record["normalized_parameters"] = []
            for value in what:
                try:
                    normalized = finite_number(value) if value else None
                except IntakeError:
                    raise
                except ValueError:
                    normalized = value  # Material references can be named.
                record["normalized_parameters"].append(normalized)
            definitions.setdefault(name, []).append(record)
            previous_compound = None
        elif keyword == "COMPOUND":
            if not name:
                raise IntakeError(f"{source}:{number}: unnamed COMPOUND needs native resolution")
            groups = compositions.setdefault(name, [])
            if previous_compound != name:
                groups.append([])
            groups[-1].append(record)
            previous_compound = name
        else:
            assignments.setdefault(what[0], []).append(record)
            previous_compound = None
    duplicates = {}
    for name, cards in definitions.items():
        if len(cards) < 2:
            continue
        blocks = compositions.get(name, [])
        components = []
        for block in blocks:
            entries = []
            for card in block:
                for index in (0, 2, 4):
                    amount, component = card["what"][index:index + 2]
                    if not amount and not component:
                        continue
                    if not amount or not component:
                        raise IntakeError(f"{source}:{card['line']}: incomplete COMPOUND component")
                    entries.append((component, finite_number(amount)))
            components.append(sorted(entries))
        ratios = []
        for entries in components:
            total = sum(abs(amount) for _, amount in entries)
            ratios.append({component: sum(amount for item, amount in entries if item == component) / total
                           for component, _ in entries} if total else dict(entries))
        proportional = bool(ratios) and all(
            set(block) == set(ratios[0]) and all(
                math.isclose(value, ratios[0][component], rel_tol=1e-12, abs_tol=1e-15)
                for component, value in block.items()) for block in ratios[1:])
        duplicates[name] = {
            "definitions": cards, "density_values_g_cm3": [card["density_g_cm3"] for card in cards],
            "conflicting_density": len({card["density_g_cm3"] for card in cards}) > 1,
            "conflicting_material_parameters": len({tuple(card["normalized_parameters"]) for card in cards}) > 1,
            "composition_blocks": blocks,
            "composition_blocks_equal": all(block == components[0] for block in components[1:]) if blocks else None,
            "composition_blocks_proportional": proportional if blocks else None,
            "composition_coefficient_ratios": ratios,
            "composition_comparison": "Signed component coefficient ratios only; no repeated-card semantics assumed.",
            "conflicting_composition_blocks": len(blocks) > 1 and not proportional,
            "active_assignments": assignments.get(name, []),
            "native_override_or_accumulation_semantics_validated": False,
        }
    return {
        "active_material_definition_count": sum(map(len, definitions.values())),
        "unique_material_names": sorted(definitions),
        "duplicate_material_count": len(duplicates), "duplicate_materials": duplicates,
        "conflicting_density_names": sorted(name for name, value in duplicates.items() if value["conflicting_density"]),
        "conflicting_composition_names": sorted(name for name, value in duplicates.items() if value["conflicting_composition_blocks"]),
        "duplicate_semantics_validated": False,
        "limitation": "Repeated MATERIAL and COMPOUND cards require native FLUKA reference or provider confirmation; no first/last-wins or accumulation rule is assumed.",
    }


def audit_bundle(source_dir, legacy_map_dir=None):
    source_dir = Path(source_dir).resolve()
    if not source_dir.is_dir():
        raise IntakeError(f"source directory does not exist: {source_dir}")
    supplied, archives, decks = {}, {}, {}
    for path in sorted(source_dir.iterdir()):
        if path.is_dir() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file():
            raise IntakeError(f"expected regular supplied file: {path}")
        data = path.read_bytes()
        supplied[path.name] = {"bytes": len(data), "sha256": digest(data)}
        if path.suffix.lower() == ".zip":
            archives[path.name] = safe_archive_members(path)
        elif path.suffix.lower() == ".inp":
            decks[path.name] = data.decode("utf-8")
    if not decks:
        raise IntakeError("no FLUKA .inp decks supplied")

    assets, asset_bytes, asset_cards = {}, {}, {}
    for archive, entries in archives.items():
        for name, data in entries.items():
            key = f"{archive}!{name}"
            assets[key] = {"bytes": len(data), "sha256": digest(data), "member": name}
            asset_bytes[key] = data
            if name.endswith(".inp"):
                included_lines, included_symbols, _ = active_lines(data.decode("utf-8"), key)
                if included_symbols or any(line.startswith("#") for _, line in included_lines):
                    raise IntakeError(f"include needs recursive preprocessing: {key}")
                all_cards = native_cards(included_lines, key)
                summary, values = native_map_summary(all_cards)
                assets[key]["native_field_map"] = summary
                asset_cards[key] = summary["cards"]
                if legacy_map_dir is not None:
                    legacy = Path(legacy_map_dir) / (PurePosixPath(name).stem + ".dat")
                    assets[key]["legacy_comparison"] = legacy_map_comparison(legacy, summary, values)

    # Provider includes can arrive as loose files beside the main decks rather
    # than inside the field archive. Parse their native cards once so an
    # absolute #include path can be resolved safely by basename.
    loose_deck_cards = {}
    for name, text in decks.items():
        included_lines, _, _ = active_lines(text, name)
        loose_deck_cards[name] = native_cards(included_lines, name)
    report = {
        "schema": "shift-cms-lss-source-intake", "schema_version": 1,
        "source_directory": str(source_dir), "production_ready": False,
        "input_files": supplied, "archive_members": assets, "decks": {},
        "fortran_include_dependencies": [],
        "native_assignment_semantics": {
            "reference": FIELD_MANUAL,
            "what2": "ROT-DEFI field-coordinate transformation",
            "what3_modes": {"0": "region", "1": "lattice-prototype-to-field", "2": "lattice-replica-to-field"},
            "what4": "first assigned region", "what5": "last assigned region", "what6": "region step",
            "runtime_lattice_field_resolution_validated": False,
        },
        "limitations": [
            "An inventory does not validate geometry conversion, alignment, field semantics, or transport.",
            "Missing includes remain unresolved even when their filename has no direct field assignment.",
            "No ATLAS field placement, polarity, material, or coordinate transform is reused.",
        ],
    }
    for key, data in asset_bytes.items():
        if not key.endswith((".f", ".ftn")):
            continue
        for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
            match = re.match(r"\s+include\s+['\"]([^'\"]+)['\"]", line, re.IGNORECASE)
            if match:
                dependency = match.group(1)
                candidates = [name for name, asset in assets.items()
                              if PurePosixPath(asset["member"]).name == dependency]
                report["fortran_include_dependencies"].append({
                    "source": key, "line": number, "raw": line,
                    "dependency": dependency, "archive_candidates": candidates,
                    "resolution": "available-by-basename" if len(candidates) == 1
                    else "external-installation-required",
                })
    for name, text in decks.items():
        lines, symbols, directives = active_lines(text, name)
        cards = native_cards(lines, name)
        includes = []
        for number, raw in lines:
            if not raw.startswith("#include"):
                continue
            requested = raw[len("#include"):].strip().strip('"<>')
            basename = PurePosixPath(requested).name
            archive_candidates = [key for key, asset in assets.items()
                                  if PurePosixPath(asset["member"]).name == basename]
            loose_candidates = [candidate for candidate in decks
                                if candidate != name and candidate == basename]
            candidates = archive_candidates + loose_candidates
            item = {"line": number, "raw": raw, "requested_path": requested,
                    "basename": basename, "archive_candidates": archive_candidates,
                    "loose_file_candidates": loose_candidates,
                    "include_candidates": candidates,
                    "resolution": "available-by-basename" if len(candidates) == 1
                    else "missing" if not candidates else "ambiguous"}
            if len(candidates) == 1:
                key = candidates[0]
                item["field_asset_reference"] = key
                cards.extend(asset_cards.get(key, loose_deck_cards.get(key, [])))
            includes.append(item)
        definitions = [card for card in cards if card["card"] == "MGNCREAT"
                       and card["sdum"] not in ("&", "&&")]
        assignments = [card for card in cards if is_named_assignment(card)]
        defined = {card["sdum"] for card in definitions}
        referenced = set(card["sdum"] for card in assignments)
        transforms = {raw.split()[-1] for _, raw in lines if raw.startswith("ROT-DEFI")}
        lattice_cells = {raw.split()[1] for _, raw in lines if raw.startswith("LATTICE")}
        for card in assignments:
            card["target_is_named_lattice_cell"] = card["what"][3] in lattice_cells
            card["assignment_mode_raw"] = card["what"][2]
        missing_transforms = sorted({card["what"][1] for card in assignments
                                     if card["what"][1] not in transforms
                                     and card["what"][1] not in ("", "0", "0.0")})
        for item in includes:
            item["direct_assignment_to_filename_stem"] = (
                PurePosixPath(item["basename"]).stem in referenced)
        headers = [{"line": i, "raw": line} for i, line in enumerate(text.splitlines()[:10], 1)]
        unresolved = [
            "Authoritative FLUKA-to-CMSSW coordinate transform and target beam/side alignment.",
            "CMS detector-owned volume boundary and exclusion of embedded FLUKA CMS structures.",
            "Independent native FLUKA reference for field vectors and material navigation.",
            "Run-specific optics, crossing angle, collimator settings, and source revision approval.",
        ]
        if "no rescaling of correctors done yet" in text[:2000]:
            unresolved.append("Source header reports 160 urad TIMBER versus 150 urad Twiss; correctors not rescaled.")
        report["decks"][name] = {
            "source_header": headers, "active_defines": symbols, "directives": directives,
            "include_resolution": includes,
            "source_complete": (all(item["resolution"] == "available-by-basename" for item in includes)
                                and not (referenced - defined) and not missing_transforms),
            "preprocessing_complete": all(item["resolution"] == "available-by-basename" for item in includes),
            "native_field_definitions": definitions,
            "native_field_assignments": assignments,
            "other_native_field_cards": [card for card in cards if card not in assignments
                                         and card not in definitions and card["card"] != "MGNDATA"],
            "assignment_count": len(assignments),
            "assignment_modes_raw": dict(sorted(Counter(card["assignment_mode_raw"] for card in assignments).items())),
            "assignments_targeting_named_lattice_cells": sum(card["target_is_named_lattice_cell"] for card in assignments),
            "assignments_by_field": dict(sorted(Counter(card["sdum"] for card in assignments).items())),
            "unresolved_named_fields": sorted(referenced - defined),
            "unresolved_field_transforms": missing_transforms,
            "available_but_unreferenced_definitions": sorted(defined - referenced),
            "active_source_cards": [{"line": i, "raw": line} for i, line in lines
                                    if line.startswith(("SPECSOUR", "SOURCE", "BEAM"))],
            "unresolved_metadata": unresolved,
            "material_definition_audit": material_definition_audit(lines, name),
        }
        if report["decks"][name]["material_definition_audit"]["duplicate_material_count"]:
            unresolved.append("Duplicate active material definitions require native override/accumulation semantics and provider confirmation of intended density/composition.")
    names = list(report["decks"])
    report["comparisons"] = []
    for first, second in zip(names, names[1:]):
        left, right = report["decks"][first], report["decks"][second]
        left_fields = {card["what"][3]: card for card in left["native_field_assignments"]}
        right_fields = {card["what"][3]: card for card in right["native_field_assignments"]}
        report["comparisons"].append({
            "first": first, "second": second,
            "defines_only_first": sorted(set(left["active_defines"]) - set(right["active_defines"])),
            "defines_only_second": sorted(set(right["active_defines"]) - set(left["active_defines"])),
            "field_regions_only_first": sorted(left_fields.keys() - right_fields.keys()),
            "field_regions_only_second": sorted(right_fields.keys() - left_fields.keys()),
            "raw_unified_diff": list(difflib.unified_diff(
                decks[first].splitlines(), decks[second].splitlines(),
                fromfile=first, tofile=second, lineterm="")),
            "changed_field_regions": [
                {"region": region, "first": left_fields[region], "second": right_fields[region]}
                for region in sorted(left_fields.keys() & right_fields.keys())
                if (left_fields[region]["what"], left_fields[region]["sdum"])
                != (right_fields[region]["what"], right_fields[region]["sdum"])],
        })
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--legacy-map-dir", type=Path,
                        help="Optional read-only comparison of same-basename legacy .dat maps")
    args = parser.parse_args()
    try:
        source = args.source_dir.resolve()
        destination = args.output_dir.resolve()
        if destination == source or source in destination.parents:
            raise IntakeError("output directory must be outside the read-only source bundle")
        report = audit_bundle(source, args.legacy_map_dir)
        destination.mkdir(parents=True, exist_ok=True)
        output = destination / "source_intake.json"
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (IntakeError, OSError, UnicodeError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {output}; production_ready=false")
    for name, deck in report["decks"].items():
        unresolved = [item["basename"] for item in deck["include_resolution"]
                      if item["resolution"] != "available-by-basename"]
        print(f"{name}: {deck['assignment_count']} field assignments; unresolved includes: {unresolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
