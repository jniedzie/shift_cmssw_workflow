#!/usr/bin/env python3
"""Convert an explicit FLUKA deck into an uninstalled geometry-only diagnostic.

This runner never certifies a model or installs CMSSW data.  It preserves the
input and records preprocessing, omissions, parser failures, and conversion
coverage in its output directory.  The old proxy entrypoints remain unchanged.
"""

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import importlib
from importlib import metadata
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import traceback

from convert_ir1_fluka_geometry_full import (
    full_conversion_guards,
    lower_multi_unions_for_root,
    parse_world_dimensions,
)
from fluka_region_preflight import classify_raw_regions, resolve_raw_region_classifications
from fluka_material_fidelity import audit_material_cards, material_fidelity_guard
from fluka_material_reachability import material_reachability
from fluka_halfspace_bounds import halfspace_bounds_guard, certified_region_bounds
from fluka_lattice_conversion import lattice_conversion_guard
from ir1_fluka_geometry import (
    ProxyModelError,
    _install_raw_zone_aabb_fallback,
    audit_gdml_material_references,
    expand_predefined_materials,
    install_exact_half_space_preservation,
    normalized_deck,
    summarize_preflight_omissions,
    summarize_region_coverage,
    write_json_atomic,
)


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
FIELD_INCLUDE = re.compile(r"(?:[^\s]+/)?magnetic_field_maps/fluka_format/[A-Za-z0-9_.-]+\.inp\Z")
GEOMETRY_DIRECTIVES = {"$start_translat", "$end_translat", "$start_transform", "$end_transform",
                       "$start_expansion", "$end_expansion"}
ORTHOGONALITY_ROUNDOFF_TOLERANCE = 32 * sys.float_info.epsilon


def orthogonality_metrics(vectors):
    """Check angles at floating-point roundoff, independent of length units."""
    normalized = []
    for vector in vectors:
        values = [float(value) for value in vector]
        if len(values) != 3 or not all(math.isfinite(value) for value in values):
            raise ValueError("orthogonality requires finite three-component vectors")
        magnitude = math.hypot(*values)
        if not math.isfinite(magnitude) or magnitude == 0:
            raise ValueError("orthogonality requires nonzero finite vector lengths")
        normalized.append([value / magnitude for value in values])
    if len(normalized) != 3:
        raise ValueError("orthogonality requires three vectors")
    dots = [math.fsum(x*y for x, y in zip(normalized[a], normalized[b]))
            for a, b in ((0, 1), (0, 2), (1, 2))]
    if any(abs(value) > ORTHOGONALITY_ROUNDOFF_TOLERANCE for value in dots):
        raise ValueError("vectors are not mutually perpendicular at floating-point roundoff")
    return dots


@contextmanager
def normalized_orthogonality_guard(ledger):
    """Correct the upstream unit-dependent predicate without changing vectors."""
    body = importlib.import_module("pyg4ometry.fluka.body")
    original = body._raiseIfNotAllMutuallyPerpendicular

    def check(first, second, third, message):
        vectors = (first, second, third)
        dots = orthogonality_metrics(vectors)
        try:
            original(first, second, third, message)
        except ValueError:
            ledger.append({"vectors_unchanged": [[float(x) for x in vector] for vector in vectors],
                           "normalized_dot_products": dots,
                           "dimensionless_tolerance": ORTHOGONALITY_ROUNDOFF_TOLERANCE,
                           "original_error": message})

    body._raiseIfNotAllMutuallyPerpendicular = check
    try:
        yield
    finally:
        body._raiseIfNotAllMutuallyPerpendicular = original


def run_secondary_region_preflight(deck, regions, output, timeout, world_dimensions_mm):
    regions_path = output / "pycsg_regions.json"
    secondary_path = output / "pycsg_preflight.json"
    write_json_atomic(regions_path, list(regions))
    command = [sys.executable, str(Path(__file__).with_name("fluka_diagnostic_preflight_worker.py")),
               "--normalized-deck", str(deck), "--regions-json", str(regions_path),
               "--output", str(secondary_path), "--timeout-seconds", str(timeout),
               "--world-dimensions-mm", ",".join(map(str, world_dimensions_mm))]
    process = subprocess.run(command, text=True, capture_output=True,
                             timeout=timeout * max(1, len(regions)) + 300)
    (output / "pycsg_worker.log").write_text(process.stdout + process.stderr, encoding="utf-8")
    if process.returncode:
        raise ProxyModelError(f"diagnostic pycsg worker failed with status {process.returncode}; see pycsg_worker.log")
    secondary = json.loads(secondary_path.read_text())
    if secondary.get("backend") != "pycsg":
        raise ProxyModelError("diagnostic secondary worker did not use pycsg")
    return secondary


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare_deck(source, output, *, omit_field_map_includes=False):
    """Evaluate only FLUKA flag conditionals; fail on unsupported semantics.

    pyg4ometry 1.4.4 applies defines in inactive branches.  Evaluate the limited,
    explicit flag grammar first so that this upstream behavior cannot change
    geometry.  Numeric/macro expressions and other include types are rejected.
    """
    source, output = Path(source), Path(output)
    lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    flags, stack, emitted, directives, includes = set(), [], [], [], []
    result = {
        "input": str(source.resolve()), "input_sha256": sha256(source),
        "grammar": "FLUKA bare-symbol definedness conditionals and flag defines",
        "directives": directives, "omitted_field_map_includes": includes,
        "comment_unicode_escapes": [],
        "production_ready": False,
    }

    def active():
        return not stack or stack[-1]["active"]

    for number, original in enumerate(lines, 1):
        stripped = original.split("!", 1)[0].strip()
        emittable = original
        if not original.isascii():
            if not stripped or stripped.startswith("*"):
                emittable = original.encode("ascii", "backslashreplace").decode("ascii")
            elif "!" in original and original.split("!", 1)[0].isascii():
                code, comment = original.split("!", 1)
                emittable = code + "!" + comment.encode("ascii", "backslashreplace").decode("ascii")
            else:
                raise ProxyModelError(f"{source}:{number}: non-ASCII active syntax is unsupported")
            result["comment_unicode_escapes"].append({"source_line": number,
                "original": original.rstrip("\r\n"), "escaped": emittable.rstrip("\r\n")})
        if not stripped or stripped.startswith("*"):
            emitted.append(emittable)
            continue
        if not stripped.startswith("#"):
            if active():
                allowed_directive = stripped.split()[0] if stripped.startswith("$") else None
                if "$" in stripped and (stripped.count("$") != 1
                                         or allowed_directive not in GEOMETRY_DIRECTIVES):
                    raise ProxyModelError(f"{source}:{number}: unsupported variable expansion or geometry directive")
            emitted.append(emittable if active() else "\n")
            continue
        parts = stripped.split()
        operation = parts[0]
        record = {"source_line": number, "text": original.rstrip("\r\n"),
                  "active_before": active()}
        directives.append(record)
        if operation in ("#if", "#elif", "#define", "#undef"):
            if len(parts) != 2 or not IDENTIFIER.fullmatch(parts[1]):
                raise ProxyModelError(f"{source}:{number}: unsupported flag directive: {stripped}")
            name = parts[1]
        if operation == "#if":
            parent = active()
            match = name in flags
            stack.append({"parent": parent, "taken": match,
                          "active": parent and match, "else_seen": False})
        elif operation == "#elif":
            if not stack or stack[-1]["else_seen"]:
                raise ProxyModelError(f"{source}:{number}: unmatched or post-else #elif")
            frame = stack[-1]
            match = name in flags
            frame["active"] = frame["parent"] and not frame["taken"] and match
            frame["taken"] = frame["taken"] or match
        elif operation == "#else":
            if len(parts) != 1 or not stack or stack[-1]["else_seen"]:
                raise ProxyModelError(f"{source}:{number}: unmatched or duplicate #else")
            frame = stack[-1]
            frame["active"] = frame["parent"] and not frame["taken"]
            frame["taken"], frame["else_seen"] = True, True
        elif operation == "#endif":
            if len(parts) != 1 or not stack:
                raise ProxyModelError(f"{source}:{number}: unmatched #endif")
            stack.pop()
        elif operation == "#define":
            if active():
                flags.add(name)
        elif operation == "#undef":
            if active():
                flags.discard(name)
        elif operation == "#include":
            if len(parts) != 2:
                raise ProxyModelError(f"{source}:{number}: unsupported include syntax")
            if active():
                target = parts[1]
                if not omit_field_map_includes or not FIELD_INCLUDE.fullmatch(target):
                    raise ProxyModelError(f"{source}:{number}: unresolved include {target}; only explicitly acknowledged magnetic-field-map includes may be omitted")
                target_path = Path(target)
                if not target_path.is_absolute():
                    target_path = source.parent / target_path
                includes.append({
                    "source_line": number, "text": original.rstrip("\r\n"),
                    "line_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
                    "target": target, "exists_at_source_path": target_path.is_file(),
                    "target_sha256": sha256(target_path) if target_path.is_file() else None,
                    "reason": "explicit geometry-only diagnostic omission; field semantics unvalidated",
                })
        else:
            raise ProxyModelError(f"{source}:{number}: unsupported preprocessor directive {operation}")
        record["active_after"] = active()
        emitted.append("\n")
    if stack:
        raise ProxyModelError(f"{source}: unterminated #if block")
    # Preserve source line numbers through both conditional evaluation and the
    # include omission.  Only the established syntax normalization follows.
    active_path = output / "active_geometry_diagnostic.inp"
    active_path.write_text("".join(emitted), encoding="ascii")
    syntax_path = output / "syntax_normalized_geometry_diagnostic.inp"
    result["normalization"] = normalized_deck(active_path, syntax_path)
    syntax = syntax_path.read_text(encoding="ascii")
    result["nested_boolean_normalization"] = []
    if any(line.startswith("GEOBEGIN") for line in syntax.splitlines()):
        from fluka_boolean_normalization import normalize_nested_unions
        syntax, result["nested_boolean_normalization"] = normalize_nested_unions(syntax)
    normalized_path = output / "normalized_geometry_diagnostic.inp"
    normalized_path.write_text(syntax, encoding="ascii")
    result.update(active_defines=sorted(flags), active_deck_sha256=sha256(active_path),
                  syntax_deck_sha256=sha256(syntax_path), normalized_deck_sha256=sha256(normalized_path),
                  normalized_deck=str(normalized_path.resolve()))
    write_json_atomic(output / "preprocessing.json", result)
    return normalized_path, result


def registry_inventory(registry):
    if registry is None:
        return {}
    result = {}
    for name in ("bodyDict", "regionDict", "latticeDict", "materials", "rotoTranslations", "assignmas"):
        value = getattr(registry, name, None)
        if value is not None:
            names = [item if isinstance(item, str) else str(item.name) for item in value]
            result[name] = {"count": len(value), "names": names}
    result["material_assignments"] = dict(getattr(registry, "assignmas", {}))
    result["material_definitions_validated"] = False
    result["parsed_materials"] = {}
    for name, material in getattr(registry, "materials", {}).items():
        item = {"type": type(material).__name__}
        for attribute in ("density", "atomicNumber", "atomicMass", "massNumber"):
            value = getattr(material, attribute, None)
            item[attribute] = None if value is None else float(value)
        item["fraction_type"] = getattr(material, "fractionType", None)
        item["components"] = [{"material": component.name, "fraction": float(fraction)}
                              for component, fraction in getattr(material, "fractions", [])]
        result["parsed_materials"][name] = item
    return result


def audit_material_assignments(reader):
    """Expose the upstream reader's skipped assignment targets explicitly."""
    registry = reader.flukaregistry
    regions, lattices = set(registry.regionDict), set(registry.latticeDict)
    materials = set(registry.materials)
    unknown, lattice_cards, numeric_cards = [], [], []
    for card in reader.cards:
        if card.keyword not in ("ASSIGNMA", "ASSIGNMAT"):
            continue
        target = card.what2
        record = {"material": card.what1, "region_from": target, "region_to": card.what3,
                  "region_step": card.what4}
        if not isinstance(target, str):
            numeric_cards.append(record)
        elif target in lattices:
            lattice_cards.append(record)
        elif target not in regions:
            unknown.append(record)
    missing = sorted(regions - set(registry.assignmas))
    undefined = []
    for region, assignment in registry.assignmas.items():
        material = assignment[0] if isinstance(assignment, (tuple, list)) else assignment
        if material not in materials:
            undefined.append({"region": region, "material": material})
    return {"ordinary_source_region_count": len(regions), "parsed_lattice_cell_count": len(lattices),
            "total_source_region_labels_including_lattices": len(regions | lattices),
            "missing_ordinary_region_assignments": missing, "undefined_material_assignments": undefined,
            "ignored_unknown_region_assignment_cards": unknown,
            "lattice_cell_assignment_cards": lattice_cards, "numeric_region_assignment_cards": numeric_cards,
            "passed": not (missing or undefined or unknown or numeric_cards)}


def audit_raw_world_bounds(preflight, dimensions, tolerance_mm=0.01):
    """Reject known source material beyond the explicitly requested world."""
    bounds = dict(preflight["primary_classification"].get("bounds_mm", {}))
    bounds.update(preflight["secondary_classification"].get("bounds_mm", {}))
    expected = set(preflight["non_null_regions"])
    missing = sorted(expected - set(bounds))
    excesses = {}
    for name in sorted(expected & set(bounds)):
        low, high = bounds[name]
        if any(not math.isfinite(value) for value in low + high):
            excesses[name] = {"source_bounds_mm": bounds[name], "reason": "nonfinite"}
        elif any(low[axis] < -dimensions[axis]/2 - tolerance_mm
                 or high[axis] > dimensions[axis]/2 + tolerance_mm for axis in range(3)):
            excesses[name] = {"source_bounds_mm": bounds[name], "reason": "outside_diagnostic_world"}
    return {"world_dimensions_mm": list(dimensions), "tolerance_mm": tolerance_mm,
            "missing_source_bounds": missing, "outside_regions": excesses,
            "passed": not (missing or excesses),
            "scope": "raw non-null region mesh bounds; independent solid/lattice/ROOT containment remains required"}


def run_conversion(args, report):
    from pyg4ometry.fluka import Reader
    from pyg4ometry.gdml import Writer
    converter = importlib.import_module("pyg4ometry.convert.fluka2Geant4")
    report["packages"] = {name: metadata.version(name) for name in ("pyg4ometry", "numpy", "sympy")}
    report["all_installed_packages"] = dict(sorted((item.metadata["Name"], item.version)
                                                  for item in metadata.distributions()
                                                  if item.metadata.get("Name")))
    report["stage"] = "preprocessing"
    deck, preprocessing = prepare_deck(args.input, args.output_dir,
                                      omit_field_map_includes=args.omit_field_map_includes)
    report["preprocessing"] = preprocessing
    report["stage"] = "parsing"
    reader = Reader.__new__(Reader)
    performance = {}
    roundoff_acceptances = report["orthogonality_roundoff_acceptances"] = []
    material_fidelity = report["material_fidelity"] = {}
    material_roots = set()
    with material_fidelity_guard(material_fidelity, required_materials=material_roots), normalized_orthogonality_guard(roundoff_acceptances), full_conversion_guards(
            args.world_dimensions_mm, performance_report=performance) as skipped, halfspace_bounds_guard(report.setdefault("halfspace_bounds", {})):
        reader_module = importlib.import_module("pyg4ometry.fluka.reader")
        make_body = reader_module._make_body

        def make_body_with_diagnostic(parts, *parameters):
            begin = len(roundoff_acceptances)
            try:
                return make_body(parts, *parameters)
            except Exception:
                report["failed_body"] = {"type": str(parts[0]), "name": str(parts[1]),
                                         "tokens": [str(value) for value in parts]}
                raise
            finally:
                for entry in roundoff_acceptances[begin:]:
                    entry.update(body_type=str(parts[0]), body_name=str(parts[1]))

        reader_module._make_body = make_body_with_diagnostic
        try:
            Reader.__init__(reader, str(deck))
        finally:
            reader_module._make_body = make_body
            report["registry"] = registry_inventory(getattr(reader, "flukaregistry", None))
            write_json_atomic(args.output_dir / "registry_inventory.json", report["registry"])
        registry = reader.flukaregistry
        report["active_card_counts"] = {keyword: sum(card.keyword == keyword for card in reader.cards)
                                        for keyword in sorted({card.keyword for card in reader.cards})}
        report["material_assignment_audit"] = audit_material_assignments(reader)
        report["material_card_audit"] = audit_material_cards(reader.cards)
        report["material_reachability"] = material_reachability(report["registry"])
        # Include every source assignment, not just the selected ordinary
        # regions: a lattice can instantiate any prototype's material.
        material_roots.update(report["material_reachability"]["direct_material_regions"])
        material_roots.update(card["material"] for card in
                              report["material_assignment_audit"]["lattice_cell_assignment_cards"])
        report["parser_passed"] = True
        if not registry.regionDict:
            raise ProxyModelError("parser returned no FLUKA regions")
        requested = args.regions or list(registry.regionDict)
        if len(requested) != len(set(requested)):
            raise ProxyModelError("duplicate --regions entries")
        unknown = sorted(set(requested) - set(registry.regionDict))
        if unknown:
            raise ProxyModelError("unknown requested regions: " + ", ".join(unknown))
        report["requested_regions"] = requested
        report["full_source_requested"] = set(requested) == set(registry.regionDict)
        if not report["material_assignment_audit"]["passed"]:
            raise ProxyModelError("material-assignment audit has unresolved or skipped source cards")
        if args.parse_only:
            report["stage"] = "parsed_only"
            return
        report["stage"] = "raw_preflight"
        primary = classify_raw_regions(registry, requested,
                                       timeout_seconds=args.region_timeout_seconds, progress_every=100,
                                       include_bounds=True)
        write_json_atomic(args.output_dir / "primary_preflight.json", primary)
        ambiguous = primary["source_null_regions"] + [item["name"] for item in primary["evaluation_errors"]]
        secondary = (run_secondary_region_preflight(deck, ambiguous, args.output_dir,
                                                    args.region_timeout_seconds, args.world_dimensions_mm)
                     if ambiguous else {"non_null_regions": [], "source_null_regions": [],
                                        "evaluation_errors": [], "backend": "pycsg"})
        preflight = resolve_raw_region_classifications(primary, secondary, requested)
        write_json_atomic(args.output_dir / "raw_region_preflight.json", preflight)
        if preflight["evaluation_errors"]:
            raise ProxyModelError("raw region preflight has unresolved evaluation errors")
        report["raw_source_world_bounds"] = audit_raw_world_bounds(preflight, args.world_dimensions_mm)
        if not report["raw_source_world_bounds"]["passed"]:
            raise ProxyModelError("source-region bounds exceed the diagnostic world or are missing")
        report["stage"] = "conversion"
        preserved = []
        original_halfspaces = install_exact_half_space_preservation(converter, preserved)
        original_bounds = None
        fallbacks = []
        try:
            original_bounds, fallbacks = _install_raw_zone_aabb_fallback(converter, preflight)
            if args.ordinary_regions_only:
                # This explicit diagnostic mode cannot claim full-model coverage.
                report["omitted_lattice_cells"] = sorted(registry.latticeDict)
                original_lattice = converter._convertLatticeCells
                converter._convertLatticeCells = lambda *unused: None
                try:
                    converted = converter.fluka2Geant4(registry, regions=preflight["conversion_candidate_regions"])
                finally:
                    converter._convertLatticeCells = original_lattice
            else:
                with lattice_conversion_guard(report.setdefault("lattice_conversion", {}),
                                              converter_module=converter, source_registry=registry,
                                              cell_bounds_provider=certified_region_bounds):
                    converted = converter.fluka2Geant4(registry, regions=preflight["conversion_candidate_regions"])
                # Upstream historically suppresses some lattice exceptions.
                # A returned registry alone is not evidence of completion.
                if not report["lattice_conversion"]["passed"]:
                    raise ProxyModelError("lattice conversion did not complete its explicit coverage gate")
        finally:
            if original_bounds is not None:
                converter._getRegionZoneAABBs = original_bounds
            converter._filterHalfSpaces = original_halfspaces
        expanded = expand_predefined_materials(converted)
        gdml = args.output_dir / "geometry_diagnostic.gdml"
        writer = Writer()
        writer.addDetector(converted)
        writer.write(str(gdml))
        material_audit = audit_gdml_material_references(gdml)
        coverage = summarize_region_coverage(registry.regionDict, requested, converted.logicalVolumeDict)
        omissions = summarize_preflight_omissions(coverage, preflight)
        lowering = lower_multi_unions_for_root(gdml)
        report["geometry"] = {
            "gdml": gdml.name, "gdml_sha256": sha256(gdml),
            "world_volume": converted.getWorldVolume().name,
            "coverage": coverage, "omission_audit": omissions,
            "material_reference_audit": material_audit, "expanded_materials": expanded,
            "binary_union_lowering": lowering, "halfspace_preservation": preserved,
            "raw_zone_aabb_fallback": fallbacks,
            "null_only_body_count": len(skipped), "meshing_optimization": performance,
        }
        if material_audit["undefined_material_count"]:
            raise ProxyModelError("GDML contains undefined materials")
        if omissions["unexpected_omitted_regions"] or omissions["deferred_region_conversion_failures"]:
            raise ProxyModelError("conversion failed non-null source coverage")
        # Deferred nulls remain explicitly unresolved for readiness even where
        # the historical converter permits an omitted diagnostic region.
        report["unresolved_deferred_regions"] = preflight["deferred_null_validation_regions"]
        report["conversion_passed"] = True
        report["full_model_exported"] = (report["full_source_requested"] and
                                         not args.ordinary_regions_only)
        report["stage"] = "converted_diagnostic"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--world-dimensions-mm", type=parse_world_dimensions, required=True)
    parser.add_argument("--geometry-only", action="store_true", required=True)
    parser.add_argument("--omit-field-map-includes", action="store_true")
    parser.add_argument("--parse-only", action="store_true")
    parser.add_argument("--ordinary-regions-only", action="store_true",
                        help="explicitly omit ALL lattice cells for a limited geometry diagnostic")
    parser.add_argument("--regions", type=lambda value: value.split(","))
    parser.add_argument("--region-timeout-seconds", type=float, default=60.0)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if (not math.isfinite(args.region_timeout_seconds) or args.region_timeout_seconds <= 0
            or not all(math.isfinite(value) for value in args.world_dimensions_mm)):
        print("error: finite positive world dimensions and timeout are required", file=sys.stderr)
        return 2
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        print("error: diagnostic output directory must be empty", file=sys.stderr)
        return 2
    if any(part.startswith("CMSSW_") for part in args.output_dir.resolve().parts):
        print("error: diagnostic output must be outside CMSSW release trees", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {"schema": "shift-fluka-geometry-diagnostic-v1", "production_ready": False,
              "geometry_only": True, "parser_passed": False, "conversion_passed": False,
              "input": str(args.input.resolve()), "stage": "initialization",
              "python": sys.version, "python_executable": sys.executable,
              "argv": list(sys.argv[1:] if argv is None else argv),
              "code_sha256": {name: sha256(Path(__file__).with_name(name)) for name in
                              ("convert_fluka_geometry_diagnostic.py", "fluka_diagnostic_preflight_worker.py",
                               "convert_ir1_fluka_geometry_full.py", "ir1_fluka_geometry.py",
                               "fluka_region_preflight.py", "fluka_region_preflight_worker.py",
                               "fluka_boolean_normalization.py", "fluka_material_fidelity.py",
                               "fluka_material_reachability.py", "fluka_analytic_bounds.py",
                               "fluka_halfspace_bounds.py", "fluka_lattice_conversion.py",
                               "fluka_pycsg_compatibility.py")},
              "world_dimensions_mm": list(args.world_dimensions_mm),
              "field_conversion_validated": False, "cmssw_installation_modified": False,
              "material_definitions_validated": False,
              "limitations": ["Diagnostic output is not bounded for CMS attachment.",
                              "Field maps, magnetic transport, and native FLUKA closure are not validated.",
                              "Source bounds, lattice placement, overlaps, gaps, materials, and CMS interface require independent audits."]}
    failure = None
    with (args.output_dir / "conversion.log").open("w", encoding="utf-8") as log:
        with redirect_stdout(log), redirect_stderr(log):
            try:
                report["input_sha256"] = sha256(args.input)
                run_conversion(args, report)
            except Exception as error:
                failure = error
                report["error"] = {"type": type(error).__name__, "message": str(error),
                                   "traceback": traceback.format_exc()}
                traceback.print_exc()
            finally:
                write_json_atomic(args.output_dir / "conversion_report.json", report)
    print(f"{report['stage']}: parser_passed={report['parser_passed']}, conversion_passed={report['conversion_passed']}, production_ready=False")
    if failure is not None:
        print(f"error: {type(failure).__name__}: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
