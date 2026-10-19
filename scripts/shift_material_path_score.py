#!/usr/bin/env python3
# Code style: AI-code. AI-generated geometry-only parent-path ranking diagnostic.
"""Measure material along a saved parent chord, without correcting its weight.

The ROOT nuclear interaction lengths provide a cheap ranking feature only.
This calculation has no magnetic bending, energy-dependent hadronic processes,
elastic scattering or secondary showers. Validate rankings against actual
Geant4 transport; never use the attenuation proxy as a physics correction.
Geometry remains an explicitly supplied private input and is never copied.
"""
import argparse
from array import array
from collections import defaultdict
import json
import math
from pathlib import Path


def vector(value):
    if len(value) != 3 or any(not math.isfinite(float(x)) for x in value):
        raise ValueError('A path point needs three finite coordinates in cm')
    return [float(x) for x in value]


def artifact_point(point, frame, mirrored=False):
    """Invert the existing modelOrigin + R*(artifact + artifactOrigin) contract."""
    point = vector(point)
    origin = vector(frame['model_origin_cm'])
    artifact = vector(frame['artifact_origin_in_model_cm'])
    rotation = frame['model_to_cms']
    if len(rotation) != 9 or any(not math.isfinite(x) for x in rotation):
        raise ValueError('The geometry frame needs a finite three-by-three rotation')
    rows = [rotation[3*i:3*i+3] for i in range(3)]
    for i in range(3):
        for j in range(3):
            if not math.isclose(sum(rows[i][k]*rows[j][k] for k in range(3)),
                                float(i == j), abs_tol=1e-9):
                raise ValueError('The geometry rotation must be orthonormal')
    determinant = sum(rows[0][i]*(rows[1][(i+1)%3]*rows[2][(i+2)%3]
                                  - rows[1][(i+2)%3]*rows[2][(i+1)%3]) for i in range(3))
    if not math.isclose(determinant, 1., abs_tol=1e-9):
        raise ValueError('The geometry rotation must preserve handedness')
    if mirrored:
        # The symmetric placement rotates the complete placement around CMS Y.
        point = [-point[0], point[1], -point[2]]
    delta = [point[i]-origin[i] for i in range(3)]
    return [sum(rotation[3*j+i]*delta[j] for j in range(3))-artifact[i] for i in range(3)]


def external_chord(start, end, boundary):
    """Clip at the external/CMS handoff; leave the unmodelled portion explicit."""
    start, end = vector(start), vector(end)
    if boundary < 0 or not math.isfinite(boundary):
        raise ValueError('The external boundary needs a finite nonnegative distance')
    length = math.dist(start, end)
    if boundary == 0:
        return start, end, length, 0.
    if abs(start[2]) < boundary:
        return start, start, 0., length
    side = 1. if start[2] >= 0 else -1.
    if side*end[2] >= boundary:
        return start, end, length, 0.
    fraction = (side*boundary-start[2])/(end[2]-start[2])
    clipped = [a+fraction*(b-a) for a, b in zip(start, end)]
    return start, clipped, fraction*length, (1.-fraction)*length


def trace_chord(geometry, start, end, maximum_steps=20000):
    """Integrate exact geometry boundaries; unknown paths never become vacuum."""
    start, end = vector(start), vector(end)
    length = math.dist(start, end)
    result = dict(complete=False, chord_length_cm=length, traced_length_cm=0.,
                  column_density_g_cm2=0., interaction_depth_proxy=0., materials={},
                  first_depth_one_distance_cm=None)
    if length == 0:
        return dict(result, complete=True, external_chord_attenuation_proxy=1.)
    direction = [(b-a)/length for a, b in zip(start, end)]
    navigator = geometry.GetCurrentNavigator()
    navigator.InitTrack(array('d', start), array('d', direction))
    material_lengths = defaultdict(float)
    travelled = 0.
    reason = 'navigation step limit'
    for _ in range(maximum_steps):
        remaining = length-travelled
        if remaining <= max(1e-7, length*1e-10):
            result['complete'] = True
            break
        node = navigator.GetCurrentNode()
        if not node or navigator.IsOutside():
            reason = 'outside supplied geometry'
            break
        material = node.GetVolume().GetMaterial()
        if not material:
            reason = 'missing material'
            break
        density = float(material.GetDensity())
        interaction = float(material.GetIntLen())
        if (not math.isfinite(density) or density < 0 or not math.isfinite(interaction)
                or (density > 0 and interaction <= 0)):
            reason = 'unknown material density or interaction length'
            break
        navigator.FindNextBoundaryAndStep(remaining, False)
        step = float(navigator.GetStep())
        if not math.isfinite(step) or step <= 0 or step > remaining+1e-5:
            reason = 'invalid navigation step'
            break
        step = min(step, remaining)
        travelled += step
        material_lengths[str(material.GetName())] += step
        result['column_density_g_cm2'] += density*step
        if interaction > 0:
            before = result['interaction_depth_proxy']
            increment = step/interaction
            if before < 1. <= before+increment and result['first_depth_one_distance_cm'] is None:
                # This navigation step is in one material. Interpolate here
                # instead of repeatedly bisecting and retracing the whole ray.
                result['first_depth_one_distance_cm'] = travelled-step+(1.-before)*interaction
            result['interaction_depth_proxy'] += increment
    result['traced_length_cm'] = travelled
    result['materials'] = dict(material_lengths)
    if result['complete']:
        result['external_chord_attenuation_proxy'] = math.exp(-result['interaction_depth_proxy'])
    else:
        result['unknown_reason'] = reason
        result['external_chord_attenuation_proxy'] = None
    return result


def score_paths(geometry, paths, frame):
    records = []
    identities = set()
    for path in paths:
        identity = str(path['id'])
        if identity in identities:
            raise ValueError('Duplicate parent path identity: '+identity)
        identities.add(identity)
        start, end, external_length, unmodelled = external_chord(
            path['start_cm'], path['end_cm'], frame.get('minimum_abs_z_cm', 0.))
        mirrored = bool(frame.get('symmetric_two_sided') and start[2] < 0.)
        a, b = (artifact_point(point, frame, mirrored) for point in (start, end))
        record = trace_chord(geometry, a, b)
        record.update(id=identity, event_id=path.get('event_id'),
                      parent_pdg=path.get('parent_pdg'), external_chord_cm=external_length,
                      unmodelled_remaining_chord_cm=unmodelled,
                      full_saved_chord_coverage=record['complete'] and unmodelled == 0.)
        # No external path must not be mistaken for complete physical coverage.
        if external_length == 0 and unmodelled > 0:
            record.update(complete=False, unknown_reason='parent entirely outside scored external region',
                          external_chord_attenuation_proxy=None)
        records.append(record)
    return dict(schema='shift-gen-material-path-score-v1', paths=records,
                diagnostic_only=True, usable_as_physics_weight=False,
                field_bending_included=False, secondary_showers_included=False,
                energy_dependent_hadronic_cross_sections_included=False,
                geometry_coordinates='cm', material_units='ROOT CGS', frame=frame)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geometry', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inputs = json.loads(args.inputs.read_text())
    if inputs.get('schema') != 'shift-gen-material-path-input-v1':
        raise ValueError('Unsupported material path inputs')
    import ROOT
    ROOT.TGeoManager.LockDefaultUnits(False)
    ROOT.TGeoManager.SetDefaultUnits(ROOT.TGeoManager.kRootUnits)
    ROOT.TGeoManager.LockDefaultUnits(True)
    geometry = ROOT.TGeoManager.Import(str(args.geometry))
    if not geometry:
        raise ValueError('Cannot import the supplied private geometry')
    result = score_paths(geometry, inputs['paths'], inputs['frame'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as target:
        json.dump(result, target, indent=2, allow_nan=False)
        target.write('\n')
    print(json.dumps(dict(paths=len(result['paths']), complete=sum(x['complete'] for x in result['paths']))))


if __name__ == '__main__':
    main()
