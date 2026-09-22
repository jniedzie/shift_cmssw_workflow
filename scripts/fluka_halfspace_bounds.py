"""Certified finite contexts for diagnostic FLUKA half-space evaluation.

No model/world clipping is guessed. Linear bounds have nonnegative rational
dual certificates; unsupported or unbounded top-level contexts fail closed.
The guard preserves Boolean nesting, input bodies, and the first operand's
placement frame. Every process-local patch is restored on exit.
"""

from contextlib import contextmanager
from copy import copy
from fractions import Fraction
from hashlib import sha256
from itertools import combinations, product
import math
from weakref import WeakKeyDictionary

import numpy as np

from fluka_analytic_bounds import body_bounds


class HalfspaceBoundsError(ValueError):
    pass


class UnboundedHalfspaceError(HalfspaceBoundsError):
    pass


PLANE_TYPES = {"PLA", "XYP", "XZP", "YZP"}

# Output-registry-local deterministic naming, retained across nested/repeated
# guards without modifying the registry or keeping it alive. Reusing a named
# subzone must not register its intermediate Boolean solids twice under one
# name. This counter does not affect source/raw meshes or placement frames.
_REGISTRY_ZONE_SEQUENCES = WeakKeyDictionary()


def _fraction(value):
    value = float(value)
    if not math.isfinite(value):
        raise HalfspaceBoundsError("non-finite linear constraint")
    return Fraction.from_float(value)


def _plane_constraint(body, sign):
    kind = type(body).__name__
    if kind == "PLA":
        normal, point = body.normal, body.point
    else:
        axis = {"YZP": 0, "XZP": 1, "XYP": 2}[kind]
        normal = [int(i == axis) for i in range(3)]
        point = [getattr(body, "xyz"[axis]) if i == axis else 0 for i in range(3)]
    normal, point = tuple(map(_fraction, normal)), tuple(map(_fraction, point))
    if not any(normal):
        raise HalfspaceBoundsError("zero plane normal")
    matrix = np.asarray(body.transform.to4DMatrix())
    if matrix.shape != (4, 4) or not np.array_equal(matrix[3], [0, 0, 0, 1]):
        raise HalfspaceBoundsError("invalid plane affine transform")
    rows = [tuple(map(_fraction, row[:3])) for row in matrix[:3]]
    transformed = _exact_multipliers(rows, normal, require_nonnegative=False)
    if transformed is None:
        raise HalfspaceBoundsError("singular plane affine transform")
    translation = tuple(map(_fraction, matrix[:3, 3]))
    limit = sum(a*b for a, b in zip(normal, point)) + sum(a*b for a, b in zip(transformed, translation))
    return tuple(sign * value for value in transformed), sign * limit


def necessary_constraints(zone):
    """Necessary constraints only; negative nonplanes cannot enlarge a zone."""
    result = []
    for operation in zone.intersections:
        body = operation.body
        if hasattr(body, "intersections"):
            result.extend(necessary_constraints(body))
        elif type(body).__name__ in PLANE_TYPES:
            result.append(_plane_constraint(body, 1))
        else:
            lower, upper = body_bounds(body)
            for axis in range(3):
                for sign, value in ((1, upper[axis]), (-1, -lower[axis])):
                    if math.isfinite(float(value)):
                        row = tuple(Fraction(sign if i == axis else 0) for i in range(3))
                        result.append((row, _fraction(value)))
    for operation in zone.subtractions:
        if type(operation.body).__name__ in PLANE_TYPES:
            result.append(_plane_constraint(operation.body, -1))
    return list(dict.fromkeys(result))


def _exact_multipliers(rows, objective, require_nonnegative=True):
    """Solve A^T lambda=c exactly for at most three independent columns."""
    count = len(rows)
    matrix = [[rows[column][axis] for column in range(count)] + [objective[axis]]
              for axis in range(3)]
    pivot_row = 0
    pivots = []
    for column in range(count):
        pivot = next((i for i in range(pivot_row, 3) if matrix[i][column]), None)
        if pivot is None:
            return None
        matrix[pivot_row], matrix[pivot] = matrix[pivot], matrix[pivot_row]
        divisor = matrix[pivot_row][column]
        matrix[pivot_row] = [value / divisor for value in matrix[pivot_row]]
        for row in range(3):
            if row != pivot_row:
                multiplier = matrix[row][column]
                matrix[row] = [a - multiplier*b for a, b in zip(matrix[row], matrix[pivot_row])]
        pivots.append((column, pivot_row))
        pivot_row += 1
    if any(not any(row[:count]) and row[-1] for row in matrix):
        return None
    answer = [Fraction(0)] * count
    for column, row in pivots:
        answer[column] = matrix[row][-1]
    return answer if not require_nonnegative or all(value >= 0 for value in answer) else None


def _certified_upper(constraints, objective):
    # Coordinate-aligned single-row certificates need no numerical LP.
    singles = []
    for index, (row, limit) in enumerate(constraints):
        multipliers = _exact_multipliers([row], objective)
        if multipliers is not None:
            singles.append((multipliers[0] * limit, [index], multipliers))
    if singles:
        return min(singles, key=lambda item: item[0])
    from scipy.optimize import linprog
    rows = np.asarray([[float(value) for value in row] for row, _ in constraints])
    limits = np.asarray([float(limit) for _, limit in constraints])
    result = linprog(-np.asarray(objective, dtype=float), A_ub=rows, b_ub=limits,
                     bounds=[(None, None)] * 3, method="highs")
    if result.status == 3:
        raise UnboundedHalfspaceError("necessary half-space constraints are unbounded")
    if not result.success:
        raise HalfspaceBoundsError("linear bound unresolved (not an empty-material proof): " + result.message)
    dual = list(np.flatnonzero(result.ineqlin.marginals))
    tight = list(np.argsort(np.abs(result.ineqlin.residual))[:12])
    candidates = list(dict.fromkeys(dual + tight))
    for count in (1, 2, 3):
        for indices in combinations(candidates, count):
            multipliers = _exact_multipliers([constraints[i][0] for i in indices], objective)
            if multipliers is not None:
                bound = sum(weight * constraints[i][1] for i, weight in zip(indices, multipliers))
                return bound, list(map(int, indices)), multipliers
    raise HalfspaceBoundsError("numerical LP has no verified nonnegative rational bound certificate")


def certified_zone_bounds(zone, *, _constraints=None):
    """Return a conservative AABB and exact rational certificates, not a mesh."""
    constraints = necessary_constraints(zone) if _constraints is None else _constraints
    if not constraints:
        raise UnboundedHalfspaceError("no supported necessary constraints")
    lower, upper, proofs = [], [], []
    for axis in range(3):
        for sign in (-1, 1):
            objective = tuple(Fraction(sign if i == axis else 0) for i in range(3))
            bound, indices, weights = _certified_upper(constraints, objective)
            value = float(bound)
            if not math.isfinite(value):
                raise HalfspaceBoundsError("certified bound cannot be represented as finite float")
            # One outward ULP covers rounding of an exactly certified rational.
            value = math.nextafter(value, math.inf)
            (lower if sign == -1 else upper).append(-value if sign == -1 else value)
            proofs.append({"axis": axis, "sign": sign, "upper_rational": str(bound),
                           "constraint_indices": indices, "nonnegative_weights": list(map(str, weights))})
    if any(lo >= hi for lo, hi in zip(lower, upper)):
        raise HalfspaceBoundsError("contradictory or zero-width necessary bounds; not an empty-material proof")
    return (np.asarray(lower), np.asarray(upper)), {
        "constraints": [{"A": list(map(str, row)), "b": str(limit)} for row, limit in constraints],
        "dual_certificates": proofs, "bounds_mm": [lower, upper],
        "scope": "necessary linear constraints only; no nonempty-material assertion",
    }


def certified_region_bounds(region):
    """Strict finite bounds for a region union, suitable for lattice cells."""
    if not region.zones:
        raise HalfspaceBoundsError("cannot bound an empty region definition")
    bounds = [certified_zone_bounds(zone)[0] for zone in region.zones]
    return np.min([item[0] for item in bounds], axis=0), np.max([item[1] for item in bounds], axis=0)


def _contains_planes(zone):
    return any(type(op.body).__name__ in PLANE_TYPES or
               (hasattr(op.body, "intersections") and _contains_planes(op.body))
               for op in zone.intersections + zone.subtractions)


@contextmanager
def halfspace_bounds_guard(ledger):
    """Evaluate bounded halfspaces identically in raw meshes and GDML solids."""
    from pyg4ometry.fluka import PLA
    from pyg4ometry.fluka.body import _HalfSpaceMixin
    from pyg4ometry.fluka.region import Zone
    from pyg4ometry.fluka.vector import AABB

    originals = {name: getattr(Zone, name) for name in ("mesh", "geant4Solid", "centre")}
    original_size = _HalfSpaceMixin._boxFullSize
    contexts, cache = [], {}
    ledger.setdefault("certified_zones", [])
    ledger["production_ready"] = False

    def context(zone):
        if contexts:
            return contexts[-1]
        # Key by actual constraints, not mutable object identity. This also
        # shares repeated identical bounds without caching changed geometry.
        constraints = tuple(necessary_constraints(zone))
        if constraints not in cache:
            bounds, proof = certified_zone_bounds(zone, _constraints=constraints)
            record = proof if ledger.get("include_certificates", False) else {
                "bounds_mm": proof["bounds_mm"], "constraint_count": len(constraints),
                "certificate_count": len(proof["dual_certificates"]),
                "proof_method": "exact_nonnegative_rational_dual",
            }
            record["zone_name"] = zone.name
            ledger["certified_zones"].append(record)
            cache[constraints] = AABB(*bounds)
        return cache[constraints]

    def finite_size(body, aabb):
        if aabb is None:
            raise UnboundedHalfspaceError("halfspace evaluation requires a certified finite context")
        normal, point = map(np.asarray, body.toPlane())
        centre = np.asarray(aabb.centre)
        face = centre - normal * np.dot(normal, centre - point)
        corners = np.asarray(list(product(*zip(aabb.lower, aabb.upper))))
        radius = np.max(np.linalg.norm(corners - face, axis=1))
        # Cube face is the actual plane; all other faces lie beyond the context.
        source_scale = max(1.0, float(np.max(np.abs(corners))), float(np.max(np.abs(point))),
                           float(np.max(np.abs(centre))))
        length = 2 * float(radius) + 512*np.finfo(float).eps*source_scale
        if not math.isfinite(length) or length <= 0:
            raise HalfspaceBoundsError("invalid finite halfspace realization")
        return math.nextafter(length * (1 + 64*np.finfo(float).eps), math.inf)

    def realization(zone, bounds):
        signature = sha256(np.asarray([bounds.lower, bounds.upper], dtype=float).tobytes()).hexdigest()[:16]
        result = Zone(name=(zone.name + "__bounded_" + signature) if zone.name else None)
        if not zone.intersections:
            raise HalfspaceBoundsError("zone must retain an original positive placement operand")
        for operation in zone.intersections + zone.subtractions:
            body = operation.body
            positive = operation in zone.intersections
            if hasattr(body, "intersections"):
                replacement = body  # Preserve nesting and inherited evaluation context.
            elif not positive and type(body).__name__ in PLANE_TYPES:
                normal, point = body.toPlane()
                replacement = PLA(body.name + "__opposite_" + signature, -normal, point)
                positive = True
            else:
                replacement = copy(body)
                replacement.name = body.name + "__bounded_" + signature
            (result.addIntersection if positive else result.addSubtraction)(replacement)
        return result

    def mapping(zone, bounds):
        return {body.name: bounds for body in zone.bodies()}

    def mesh(zone, aabb=None):
        if not _contains_planes(zone):
            return originals["mesh"](zone, aabb=aabb)
        bounds = context(zone)
        contexts.append(bounds)
        try:
            return originals["mesh"](realization(zone, bounds), aabb=bounds)
        finally:
            contexts.pop()

    def geant4_solid(zone, reg, aabb=None):
        if not _contains_planes(zone):
            return originals["geant4Solid"](zone, reg, aabb=aabb)
        bounds = context(zone)
        contexts.append(bounds)
        try:
            bounded = realization(zone, bounds)
            sequence = _REGISTRY_ZONE_SEQUENCES.get(reg, 0) + 1
            _REGISTRY_ZONE_SEQUENCES[reg] = sequence
            bounded.name = (bounded.name or "zone") + "__evaluation_" + str(sequence)
            return originals["geant4Solid"](bounded, reg, aabb=mapping(bounded, bounds))
        finally:
            contexts.pop()

    def centre(zone, aabb=None):
        if not _contains_planes(zone):
            return originals["centre"](zone, aabb=aabb)
        bounds = context(zone)
        contexts.append(bounds)
        try:
            return originals["centre"](zone, aabb=mapping(zone, bounds))
        finally:
            contexts.pop()

    try:
        Zone.mesh, Zone.geant4Solid, Zone.centre = mesh, geant4_solid, centre
        _HalfSpaceMixin._boxFullSize = finite_size
        yield ledger
    finally:
        for name, original in originals.items():
            setattr(Zone, name, original)
        _HalfSpaceMixin._boxFullSize = original_size
