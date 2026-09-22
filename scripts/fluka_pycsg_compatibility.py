"""Scoped, input-independent repairs for the independent pycsg diagnostic.

Never mask a meshing exception or infer empty geometry from an error. The only
Boolean short circuits below are the exact identities for a zero-polygon input.
The planar decomposition uses exact rational predicates on the input floats;
it adds no tolerance, moves no vertices and never invokes CGAL.
"""

from contextlib import contextmanager
from fractions import Fraction
import importlib
import math


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, c):
    return (_cross(a, b, c) == 0 and
            min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and
            min(a[1], b[1]) <= c[1] <= max(a[1], b[1]))


def _segments_intersect(a, b, c, d):
    ab_c, ab_d, cd_a, cd_b = (_cross(a, b, c), _cross(a, b, d),
                              _cross(c, d, a), _cross(c, d, b))
    return ((ab_c * ab_d < 0 and cd_a * cd_b < 0) or
            _on_segment(a, b, c) or _on_segment(a, b, d) or
            _on_segment(c, d, a) or _on_segment(c, d, b))


def triangulate_polygon_2d(polygon):
    """Return orientation-preserving triangles of a finite simple polygon.

    An optional repeated closing point and exact collinear intermediate points
    are geometrically redundant. All other duplicates, degeneracies, and any
    touching/crossing non-neighbouring edges are rejected, including holes.
    """
    vertices = []
    for point in polygon:
        if len(point) != 2:
            raise ValueError("polygon vertices must have exactly two coordinates")
        vertex = tuple(float(coordinate) for coordinate in point)
        if not all(math.isfinite(coordinate) for coordinate in vertex):
            raise ValueError("polygon coordinates must be finite")
        vertices.append(vertex)
    if len(vertices) > 1 and vertices[0] == vertices[-1]:
        vertices.pop()
    if len(vertices) < 3 or len(set(vertices)) != len(vertices):
        raise ValueError("polygon needs at least three distinct vertices without duplicates")
    exact = [tuple(Fraction.from_float(coordinate) for coordinate in point)
             for point in vertices]
    # Reject a non-simple boundary before eliminating collinear points.
    n = len(vertices)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if _segments_intersect(exact[i], exact[(i + 1) % n],
                                   exact[j], exact[(j + 1) % n]):
                raise ValueError("polygon has crossing or touching non-neighbouring edges")
    indices = list(range(n))
    changed = True
    while changed and len(indices) >= 3:
        changed = False
        for offset, current in enumerate(indices):
            previous, following = indices[offset - 1], indices[(offset + 1) % len(indices)]
            if _cross(exact[previous], exact[current], exact[following]) == 0:
                if not _on_segment(exact[previous], exact[following], exact[current]):
                    raise ValueError("polygon contains a collinear backtracking edge")
                indices.pop(offset)
                changed = True
                break
    if len(indices) < 3:
        raise ValueError("polygon has zero area")
    twice_area = sum(exact[indices[i]][0] * exact[indices[(i + 1) % len(indices)]][1] -
                     exact[indices[(i + 1) % len(indices)]][0] * exact[indices[i]][1]
                     for i in range(len(indices)))
    if twice_area == 0:
        raise ValueError("polygon has zero area")
    orientation = 1 if twice_area > 0 else -1
    triangles = []
    while len(indices) > 3:
        for offset, current in enumerate(indices):
            previous, following = indices[offset - 1], indices[(offset + 1) % len(indices)]
            a, b, c = exact[previous], exact[current], exact[following]
            if orientation * _cross(a, b, c) <= 0:
                continue
            if any(all(orientation * value >= 0 for value in
                       (_cross(a, b, exact[other]), _cross(b, c, exact[other]),
                        _cross(c, a, exact[other])))
                   for other in indices if other not in (previous, current, following)):
                continue
            triangles.append([vertices[previous], vertices[current], vertices[following]])
            indices.pop(offset)
            break
        else:
            raise ValueError("polygon ear clipping stalled; refusing ambiguous decomposition")
    triangles.append([vertices[index] for index in indices])
    return triangles


class IndependentPolygonProcessing:
    """Subset required by pyg4ometry's two pycsg primitive builders."""

    triangulatePolygon2d = staticmethod(triangulate_polygon_2d)
    decomposePolygon2d = staticmethod(triangulate_polygon_2d)


@contextmanager
def pycsg_compatibility_guard():
    """Restore every patched method/global even when parsing or meshing fails."""
    from pyg4ometry import config
    from pyg4ometry.pycsg.core import CSG

    if config.backendName() != "pycsg":
        raise RuntimeError("pycsg compatibility guard requires the independent pycsg backend")
    originals = {name: getattr(CSG, name) for name in ("union", "subtract", "intersect")}

    def union(first, second):
        if first.isNull():
            return second.clone()
        if second.isNull():
            return first.clone()
        return originals["union"](first, second)

    def subtract(first, second):
        if first.isNull() or second.isNull():
            return first.clone()
        return originals["subtract"](first, second)

    def intersect(first, second):
        if first.isNull() or second.isNull():
            return CSG.fromPolygons([])
        return originals["intersect"](first, second)

    missing = object()
    modules = [importlib.import_module("pyg4ometry.geant4.solid." + name)
               for name in ("ExtrudedSolid", "GenericPolyhedra")]
    polygon_originals = [(module, getattr(module, "_PolygonProcessing", missing))
                         for module in modules]
    try:
        CSG.union, CSG.subtract, CSG.intersect = union, subtract, intersect
        for module in modules:
            module._PolygonProcessing = IndependentPolygonProcessing
        yield {
            "empty_set_identities": ["union", "subtract", "intersect"],
            "polygon_decomposition": "pure_python_exact_predicate_ear_clipping",
            "cgal_used_for_secondary_booleans_or_polygon_decomposition": False,
            "geometry_preserving_scope": "zero-polygon operands and simple 2D polygons only",
        }
    finally:
        for name, original in originals.items():
            setattr(CSG, name, original)
        for module, original in polygon_originals:
            if original is missing:
                delattr(module, "_PolygonProcessing")
            else:
                module._PolygonProcessing = original
