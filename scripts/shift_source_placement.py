# Code style: AI-code. AI-generated controlled fixed-point source contract.
"""Place an unchanged GEN graph for a material canary, without rate claims."""
import math

TEST_SCOPE = "fixed-point-material-canary"
ASSUMED_SCOPE = "assumed-jaw-depth-canary"


def _point(point_cm):
    if (not isinstance(point_cm, (list, tuple)) or len(point_cm) != 3
            or any(isinstance(x, bool) or not isinstance(x, (int, float))
                   or not math.isfinite(x) or not math.isfinite(x * 10.) for x in point_cm)):
        raise ValueError("Source point requires three finite coordinates in cm")
    return list(map(float, point_cm))


def placement_contract(point_cm, input_label, *, test_scope, event_positions=None):
    if test_scope not in (TEST_SCOPE, ASSUMED_SCOPE):
        raise ValueError("Only explicit diagnostic source-placement scopes are supported")
    if not isinstance(input_label, str) or not input_label:
        raise ValueError("An explicit unshifted native HepMC input label is required")
    positions = []
    if test_scope == TEST_SCOPE:
        if event_positions is not None:
            raise ValueError("Fixed-point scope cannot also supply an event map")
        point_cm = _point(point_cm)
    else:
        if point_cm is not None or not isinstance(event_positions, (list, tuple)) or not event_positions:
            raise ValueError("Assumed-depth scope needs an explicit nonempty event map and no fixed point")
        seen = set()
        for row in event_positions:
            identity = row["event_id"]
            if (not isinstance(identity, (list, tuple)) or len(identity) != 3
                    or any(type(x) is not int or x < 1 or x > limit for x, limit in
                           zip(identity, (2**32-1, 2**32-1, 2**64-1)))
                    or tuple(identity) in seen):
                raise ValueError("Source map needs unique complete original event identities")
            seen.add(tuple(identity))
            positions.append(dict(event_id=list(identity), target_position_cm=_point(row["target_position_cm"])))
    return dict(schema="shift-fixed-source-placement-v1", test_scope=test_scope,
                target_position_cm=point_cm, event_positions=positions, input_label=input_label,
                whole_graph_translation=True, momenta_and_weights_unchanged=True,
                relative_vertex_separations_unchanged=True, ct_unchanged_until_nominal_timing=True,
                source_distribution_qualified=False, new_GEN_exposure=0,
                normalization_ready=False)


def configure_source_placement(process, point_cm, input_label, *, test_scope, event_positions=None):
    """Return the new producer; the caller schedules placement before timing."""
    contract = placement_contract(point_cm, input_label, test_scope=test_scope, event_positions=event_positions)
    import FWCore.ParameterSet.Config as cms
    process.shiftSourcePlacement = cms.EDProducer(
        "ShiftSourcePlacementProducer", src=cms.InputTag(input_label),
        targetPositionCm=cms.vdouble(contract["target_position_cm"] or []),
        testScope=cms.string(contract["test_scope"]),
        eventPositions=cms.VPSet(*[cms.PSet(run=cms.uint32(row["event_id"][0]),
            luminosityBlock=cms.uint32(row["event_id"][1]), event=cms.uint64(row["event_id"][2]),
            targetPositionCm=cms.vdouble(row["target_position_cm"])) for row in contract["event_positions"]]))
    return process.shiftSourcePlacement
