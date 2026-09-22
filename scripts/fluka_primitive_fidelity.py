"""Scoped FLUKA elliptical-cylinder parameter corrections for pyg4ometry.

G4EllipticalTube/GDML eltube uses two SEMIAXES and a Z HALF-LENGTH.
pyg4ometry 1.4.4's FLUKA REC/XEC/YEC/ZEC adapters supply full dimensions.
This guard corrects only those adapters, preserving source bodies, transforms,
and length-safety operations.  It does not certify finite approximations of
infinite cylinders or modify any installed package or live geometry.
"""

from contextlib import contextmanager
import math


AFFECTED_BODY_TYPES = ("REC", "XEC", "YEC", "ZEC")


class PrimitiveFidelityError(ValueError):
    pass


def _positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise PrimitiveFidelityError(f"{name}: expected positive finite length/expansion")
    return value


@contextmanager
def primitive_fidelity_guard(ledger):
    """Correct elliptical-cylinder dimensions and restore adapters on exit.

    Infinite-cylinder axial limits retain the caller's existing global AABB
    or finite-INFINITY surrogate, converting full length to half length.
    Their physical infinity/finite-context equivalence remains a separate
    validation gate; no artificial end face is certified here.
    """
    from pyg4ometry.fluka import body as module
    from pyg4ometry import geant4

    originals = {name: getattr(module, name).geant4Solid for name in AFFECTED_BODY_TYPES}
    ledger.update({"affected_body_types": list(AFFECTED_BODY_TYPES),
                   "parameter_contract": "elliptical semiaxes and axial half-length in mm",
                   "production_ready": False})
    records = ledger.setdefault("corrected_bodies", [])
    seen = set()

    def make(body, reg, dimensions, aabb):
        dimensions = [_positive(value, body.name) for value in dimensions]
        kind = type(body).__name__
        key = (kind, body.name, tuple(dimensions), aabb is not None)
        if key not in seen:
            record = {"name": body.name, "body_type": kind,
                      "gdml_dx_dy_dz_mm": dimensions,
                      "source_body_and_transform_unchanged": True}
            if kind != "REC":
                record.update({"finite_context_supplied": aabb is not None,
                               "finite_axial_extent_validated": False,
                               "axial_extent_source": "caller AABB full diagonal margin" if aabb is not None
                                                      else "upstream finite INFINITY surrogate"})
            records.append(record)
            seen.add(key)
        return geant4.solid.EllipticalTube(body.name, *dimensions, reg, lunit="mm")

    def rec(body, reg, aabb=None):
        expansion = _positive(body.transform.netExpansion(), body.name)
        return make(body, reg, (expansion * body.semiminor.length(),
                               expansion * body.semimajor.length(),
                               expansion * body.direction.length() / 2), aabb)

    def infinite(body, reg, aabb=None):
        expansion = _positive(body.transform.netExpansion(), body.name)
        attributes = {"XEC": ("zsemi", "ysemi"), "YEC": ("xsemi", "zsemi"),
                      "ZEC": ("xsemi", "ysemi")}[type(body).__name__]
        # _aabbToScaleFactor returns a full length in GLOBAL millimetres.
        # It must not be expanded again: the provided AABB is already global.
        length = _positive(body._aabbToScaleFactor(aabb), body.name)
        return make(body, reg, (expansion * getattr(body, attributes[0]),
                               expansion * getattr(body, attributes[1]), length / 2), aabb)

    try:
        module.REC.geant4Solid = rec
        for name in AFFECTED_BODY_TYPES[1:]:
            getattr(module, name).geant4Solid = infinite
        yield ledger
    finally:
        for name, original in originals.items():
            getattr(module, name).geant4Solid = original
