"""Generic, diagnostic-only FLUKA lattice conversion with exact cell clipping.

No parking-region conventions or tessellated AABBs are used.  Every converted
region is considered and only analytic strict separation can reject it.  A
positive mesh is a diagnostic witness, not native-FLUKA semantic validation;
ambiguous empty intersections fail closed instead of silently losing material.
"""

from contextlib import contextmanager
import importlib

import numpy as np

from fluka_analytic_bounds import bounds_are_disjoint, region_bounds, transform_bounds


class LatticeConversionError(RuntimeError):
    pass


def _affine(rotation, translation):
    result = np.identity(4)
    result[:3, :3], result[:3, 3] = rotation, translation
    return result


def _rigid_matrix(matrix, description):
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise LatticeConversionError(f"{description}: invalid affine matrix")
    if not np.array_equal(matrix[3], [0, 0, 0, 1]):
        raise LatticeConversionError(f"{description}: non-affine matrix")
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.identity(3), rtol=0, atol=1e-12) or not np.isclose(
            np.linalg.det(rotation), 1, rtol=0, atol=1e-12):
        raise LatticeConversionError(f"{description}: only proper rigid lattice transforms are supported")
    return matrix


def conservative_lattice_candidates(fluka_registry, region_names=None):
    """Select possible prototypes, including enclosed and curved components."""
    names = sorted(fluka_registry.regionDict if region_names is None else region_names)
    bounds = {name: region_bounds(fluka_registry.regionDict[name]) for name in names}
    result = {}
    for name, lattice in sorted(fluka_registry.latticeDict.items()):
        matrix = _rigid_matrix(lattice.getTransform().to4DMatrix(), f"lattice {name}")
        cell_bounds = transform_bounds(region_bounds(lattice.cellRegion), matrix)
        result[name] = [region for region in names if not bounds_are_disjoint(bounds[region], cell_bounds)]
    return result


@contextmanager
def lattice_conversion_guard(report, *, converter_module=None, source_registry=None,
                             cell_bounds_provider=region_bounds):
    """Install and restore a fail-closed, source-independent lattice converter.

    ``source_registry`` should be the complete source registry when the caller
    selects a subset of ordinary regions: potential omitted prototypes then
    raise an error.  Without it, completeness is only relative to the registry
    supplied by pyg4ometry.  ``cell_bounds_provider`` may supply independently
    certified analytic/LP finite bounds for cells bounded by oblique planes.
    A mesh-derived bounds provider is not safe and must not be passed here.
    """
    from pyg4ometry import config, fluka, geant4, transformation

    converter = converter_module or importlib.import_module("pyg4ometry.convert.fluka2Geant4")
    original_convert, original_contents = converter._convertLatticeCells, converter._getContentsOfLatticeCells
    report.update({"schema": "shift-generic-lattice-conversion-v1", "production_ready": False,
                   "candidate_selection": "conservative_source_analytic_bounds",
                   "source_bound_clipping": False, "exact_cell_clipping": True,
                   "coverage_scope": "complete_source_registry" if source_registry is not None else "converter_registry_only",
                   "independent_navigation_validation_required": True, "passed": False, "lattices": []})

    def placement_matrix(placement):
        return _rigid_matrix(_affine(transformation.tbxyz2matrix(
            transformation.reverse(placement.rotation.eval())), placement.position.eval()), placement.name)

    def unique_name(name, registry):
        # Fail on collisions instead of silently replacing an earlier definition.
        for dictionary in (registry.solidDict, registry.logicalVolumeDict, registry.physicalVolumeDict):
            if name in dictionary:
                raise LatticeConversionError(f"duplicate generated lattice name: {name}")
        return name

    def convert_impl(greg, freg, world, region_zone_aabbs, region_lvs):
        del region_zone_aabbs  # Tessellated extents must never select or clip prototypes.
        source = source_registry if source_registry is not None else freg
        candidates = conservative_lattice_candidates(source)
        placements = {}
        for item in world.daughterVolumes:
            if item.name.endswith("_pv") and item.name[:-3] in region_lvs:
                key = item.name[:-3]
                if key in placements:
                    raise LatticeConversionError(f"duplicate source placement: {key}")
                placements[key] = item
        if set(region_lvs) - set(placements):
            raise LatticeConversionError("converted source region is missing its original placement")
        records = report["lattices"]
        for cell_name, lattice in sorted(source.latticeDict.items()):
            candidate_names = candidates[cell_name]
            record = {"cell": cell_name, "candidate_names": candidate_names,
                      "analytically_rejected_count": len(source.regionDict) - len(candidate_names),
                      "placements": [], "passed": False}
            records.append(record)
            if not candidate_names:
                raise LatticeConversionError(f"lattice {cell_name} has no possible source prototypes")
            missing = sorted(set(candidate_names) - set(region_lvs))
            if missing:
                record["missing_converted_candidates"] = missing
                raise LatticeConversionError(f"lattice {cell_name} has unconverted possible prototypes: {missing}")
            cell_bounds = cell_bounds_provider(lattice.cellRegion)
            if not np.isfinite(cell_bounds).all() or np.any(cell_bounds[1] <= cell_bounds[0]):
                raise LatticeConversionError(f"lattice {cell_name} lacks a finite conservative analytic cell bound")
            record["physical_cell_bounds_mm"] = np.asarray(cell_bounds).tolist()
            # Make an unplaced source-accurate solid, with fresh operand names.
            cell = lattice.cellRegion.makeUnique(f"__{cell_name}_lattice_cell", fluka.FlukaRegistry())
            cell.name = unique_name(f"{cell_name}_lattice_cell", greg)
            bounds = fluka.AABB(*cell_bounds)
            body_bounds = {body.name: bounds for body in cell.bodies()}
            for body in cell.bodies():
                unique_name(body.name, greg)
            cell_solid = cell.geant4Solid(greg, aabb=body_bounds)
            cell_to_physical = _rigid_matrix(_affine(cell.rotation(), cell.centre(aabb=body_bounds)), cell_name)
            physical_to_prototype = _rigid_matrix(lattice.getTransform().to4DMatrix(), cell_name)
            record["physical_to_prototype_matrix"] = physical_to_prototype.tolist()
            for prototype_name in candidate_names:
                prototype_lv = region_lvs[prototype_name]
                prototype_to_model = placement_matrix(placements[prototype_name])
                cell_to_prototype_local = np.linalg.inv(prototype_to_model) @ physical_to_prototype @ cell_to_physical
                stem = f"{cell_name}__{prototype_name}_lattice"
                clipped = geant4.solid.Intersection(unique_name(stem + "_clip_solid", greg),
                    prototype_lv.solid, cell_solid,
                    [transformation.matrix2tbxyz(cell_to_prototype_local[:3, :3]),
                     cell_to_prototype_local[:3, 3].tolist()], greg)
                item = {"prototype": prototype_name, "clip_matrix": cell_to_prototype_local.tolist()}
                record["placements"].append(item)
                try:
                    mesh = clipped.mesh()
                    volume = abs(float(mesh.volume()))
                    if mesh.isNull() or not np.isfinite(volume) or volume <= 0:
                        raise ValueError("empty or non-positive intersection mesh")
                except Exception as error:
                    item["unresolved_intersection"] = f"{type(error).__name__}: {error}"
                    raise LatticeConversionError(
                        f"lattice {cell_name}, prototype {prototype_name}: intersection has no positive mesh witness; "
                        "cannot omit a potentially non-empty analytic solid") from error
                # Mesh success is recorded but does not replace the analytic solid.
                item["diagnostic_mesh_volume_mm3"] = volume
                original_meshing = config.doMeshing
                config.doMeshing = False
                try:
                    lv = geant4.LogicalVolume(clipped, prototype_lv.material,
                                             unique_name(stem + "_clip_lv", greg), greg)
                finally:
                    config.doMeshing = original_meshing
                prototype_to_physical = np.linalg.inv(physical_to_prototype) @ prototype_to_model
                pv = geant4.PhysicalVolume(
                    list(transformation.reverse(transformation.matrix2tbxyz(prototype_to_physical[:3, :3]))),
                    prototype_to_physical[:3, 3].tolist(), lv,
                    unique_name(stem + "_pv", greg), world, greg)
                item.update({"placement_name": pv.name, "prototype_to_physical_matrix": prototype_to_physical.tolist()})
            record["passed"] = True
        report.update({"passed": True, "lattice_count": len(records),
                       "placement_count": sum(len(record["placements"]) for record in records)})

    def convert(greg, freg, world, region_zone_aabbs, region_lvs):
        # pyg4ometry's caller silently catches UnboundLocalError from the
        # complete lattice call.  Never let an unrelated evaluation failure
        # escape under that type and turn a missing lattice into success.
        try:
            return convert_impl(greg, freg, world, region_zone_aabbs, region_lvs)
        except UnboundLocalError as error:
            report["passed"] = False
            report["evaluation_error"] = f"UnboundLocalError: {error}"
            raise LatticeConversionError(
                "lattice evaluation raised UnboundLocalError; refusing upstream silent omission"
            ) from error

    def contents(freg, ignored_mesh_bounds):
        del ignored_mesh_bounds
        return conservative_lattice_candidates(freg)

    converter._convertLatticeCells, converter._getContentsOfLatticeCells = convert, contents
    try:
        yield report
    finally:
        converter._convertLatticeCells, converter._getContentsOfLatticeCells = original_convert, original_contents
