"""Conservative source-analytic FLUKA bounds in mm, independent of meshing.

Bounds are ``(lower, upper)`` NumPy vectors.  Unsupported bodies return
infinite bounds, never a guessed finite extent.  Subtractions cannot enlarge
a positive intersection and are deliberately ignored.  These are candidate
selection bounds, not replacement solids or proof of non-empty material.
"""

from itertools import product

import numpy as np


def unknown_bounds():
    return np.full(3, -np.inf), np.full(3, np.inf)


def outward_bounds(lower, upper, *, scale_hint=0.0):
    """Round finite bounds outwards; numerical margins never remove material."""
    lower, upper = np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)
    if lower.shape != (3,) or upper.shape != (3,) or np.isnan([lower, upper]).any():
        raise ValueError("invalid analytic bounds")
    finite = np.concatenate([lower[np.isfinite(lower)], upper[np.isfinite(upper)]])
    scale_hint = float(scale_hint)
    if not np.isfinite(scale_hint):
        raise ValueError("non-finite analytic intermediate magnitude")
    margin = 64 * np.finfo(float).eps * max(1.0, abs(scale_hint), np.max(np.abs(finite), initial=0.0))
    return np.nextafter(lower - margin, -np.inf), np.nextafter(upper + margin, np.inf)


def transform_bounds(bounds, matrix):
    """Conservatively transform an AABB, including unbounded coordinates."""
    lower, upper = bounds
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("invalid affine transform")
    if not np.array_equal(matrix[3], [0, 0, 0, 1]):
        raise ValueError("non-affine transform")
    low, high = matrix[:3, 3].copy(), matrix[:3, 3].copy()
    magnitude = np.abs(matrix[:3, 3]).copy()
    for axis in range(3):
        for source_axis in range(3):
            coefficient = matrix[axis, source_axis]
            if coefficient == 0:  # Do not multiply an exact zero by infinity.
                continue
            products = coefficient * np.array([lower[source_axis], upper[source_axis]])
            finite_products = products[np.isfinite(products)]
            magnitude[axis] += np.max(np.abs(finite_products), initial=0.0)
            low[axis] += min(products)
            high[axis] += max(products)
    return outward_bounds(low, high, scale_hint=max(magnitude))


def bounds_are_disjoint(first, second):
    """Strict separation only; touching or uncertain bounds remain candidates."""
    return bool(np.any(first[1] < second[0]) or np.any(second[1] < first[0]))


def _points_bounds(points, matrix, *, scale_hint=0.0):
    points = np.asarray(points, dtype=float)
    transformed = points @ matrix[:3, :3].T + matrix[:3, 3]
    magnitude = np.abs(points) @ np.abs(matrix[:3, :3]).T + np.abs(matrix[:3, 3])
    return outward_bounds(transformed.min(axis=0), transformed.max(axis=0),
                          scale_hint=max(scale_hint, np.max(magnitude)))


def body_bounds(body):
    """Bound supported finite primitives analytically; unknown means infinity.

    Curved primitives use support functions, not chordal meshes.  The body's
    complete affine transform is applied to points and radial vectors.
    """
    kind = type(body).__name__
    known = {"RPP", "BOX", "WED", "RAW", "ARB", "SPH", "RCC", "REC", "TRC", "ELL",
             "XCC", "YCC", "ZCC", "XEC", "YEC", "ZEC", "PLA", "XYP", "XZP", "YZP"}
    if kind not in known:
        return unknown_bounds()
    matrix = np.asarray(body.transform.to4DMatrix(), dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all() or not np.array_equal(matrix[3], [0, 0, 0, 1]):
        raise ValueError(f"invalid transform for {body.name}")
    linear, translation = matrix[:3, :3], matrix[:3, 3]
    # Error padding must reflect operands before cancellation, not merely a
    # small translated result.  Bound all primitive coordinate/vector/radius
    # magnitudes through the affine linear map; 16 covers the short sums below.
    magnitudes = [1.0]
    for attribute in ("lower", "upper", "vertex", "edge1", "edge2", "edge3", "vertices",
                      "point", "radius", "face", "direction", "semiminor", "semimajor",
                      "major_centre", "major_radius", "minor_radius", "focus1", "focus2",
                      "length", "x", "y", "z", "xsemi", "ysemi", "zsemi"):
        value = getattr(body, attribute, None)
        if value is not None and not callable(value):
            magnitudes.append(float(np.max(np.abs(value))))
    scale_hint = 16 * max(magnitudes) * (1 + np.linalg.norm(linear, ord=np.inf)) + np.max(np.abs(translation))
    if not np.isfinite(scale_hint):
        raise ValueError(f"non-finite primitive magnitude for {body.name}")

    def outward(lower, upper):
        return outward_bounds(lower, upper, scale_hint=scale_hint)
    if kind == "RPP":
        return _points_bounds(list(product(*zip(body.lower, body.upper))), matrix, scale_hint=scale_hint)
    if kind in {"BOX", "WED", "RAW"}:
        # The enclosing parallelepiped is conservative for triangular prisms.
        points = [np.asarray(body.vertex) + a * body.edge1 + b * body.edge2 + c * body.edge3
                  for a, b, c in product((0, 1), repeat=3)]
        return _points_bounds(points, matrix, scale_hint=scale_hint)
    if kind == "ARB":
        return _points_bounds(body.vertices, matrix, scale_hint=scale_hint)
    if kind == "SPH":
        center = linear @ body.point + translation
        radius = abs(float(body.radius)) * np.linalg.norm(linear, axis=1)
        return outward(center - radius, center + radius)
    if kind in {"RCC", "REC", "TRC"}:
        face = body.major_centre if kind == "TRC" else body.face
        first = linear @ face + translation
        last = linear @ (face + body.direction) + translation
        if kind == "REC":
            radial = np.hypot(linear @ body.semiminor, linear @ body.semimajor)
            return outward(np.minimum(first, last) - radial, np.maximum(first, last) + radial)
        direction = np.asarray(body.direction, dtype=float)
        length = np.linalg.norm(direction)
        if not length > 0:
            raise ValueError(f"zero cylinder axis for {body.name}")
        unit = direction / length
        # Norm of each transformed coordinate's projection into the radial plane.
        projected = linear - np.outer(linear @ unit, unit)
        support = np.linalg.norm(projected, axis=1)
        radius0 = body.major_radius if kind == "TRC" else body.radius
        radius1 = body.minor_radius if kind == "TRC" else body.radius
        radial0, radial1 = abs(radius0) * support, abs(radius1) * support
        return outward(np.minimum(first - radial0, last - radial1), np.maximum(first + radial0, last + radial1))
    if kind == "ELL":
        # Enclosing source sphere avoids assumptions about axis degeneracy.
        center = linear @ ((body.focus1 + body.focus2) * 0.5) + translation
        radius = abs(float(body.length)) * 0.5 * np.linalg.norm(linear, axis=1)
        return outward(center - radius, center + radius)
    if kind in {"XCC", "YCC", "ZCC", "XEC", "YEC", "ZEC"}:
        axial = "XYZ".index(kind[0])
        radial_axes = [i for i in range(3) if i != axial]
        source_center, radii = np.zeros(3), np.zeros(3)
        for axis in radial_axes:
            attribute = "xyz"[axis]
            source_center[axis] = getattr(body, attribute)
            radii[axis] = body.radius if kind.endswith("CC") else getattr(body, attribute + "semi")
        center = linear @ source_center + translation
        radial = np.linalg.norm(linear * radii, axis=1)
        lower, upper = center - radial, center + radial
        unbounded = linear[:, axial] != 0
        lower[unbounded], upper[unbounded] = -np.inf, np.inf
        return outward(lower, upper)
    # A halfspace only has coordinate bounds when its normal has one exact
    # nonzero component.  Oblique constraints require the caller's LP audit.
    normal, point = body.toPlane()
    normal, point = np.asarray(normal), np.asarray(point)
    nonzero = np.flatnonzero(normal)
    lower, upper = unknown_bounds()
    if len(nonzero) == 1:
        axis = nonzero[0]
        if normal[axis] > 0:
            upper[axis] = point[axis]
        else:
            lower[axis] = point[axis]
    return outward(lower, upper)


def zone_bounds(zone):
    """Enclose an intersection, ignoring subtraction; nested zones supported."""
    lower, upper = unknown_bounds()
    for operation in zone.intersections:
        operand = operation.body
        bounds = zone_bounds(operand) if hasattr(operand, "intersections") else body_bounds(operand)
        lower, upper = np.maximum(lower, bounds[0]), np.minimum(upper, bounds[1])
    return lower, upper


def region_bounds(region):
    """Enclose the union of all region zones; empty regions fail closed."""
    if not region.zones:
        raise ValueError(f"region {region.name} has no zones")
    bounds = [zone_bounds(zone) for zone in region.zones]
    return np.min([item[0] for item in bounds], axis=0), np.max([item[1] for item in bounds], axis=0)
