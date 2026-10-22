#!/usr/bin/env python3
# Code style: AI-code. AI-generated assumed jaw first-interaction depth model.
"""Draw source positions from a declared jaw mixture and material path.

This is a simple source assumption, not measured beam-halo data. Depth follows
an exponential in nuclear interaction lengths, conditioned on a collision in
the selected jaw. It never modifies generator or reconstruction weights.
"""
import hashlib
import json
import math


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def path_segments(path):
    result = []
    for segment in path["segments"]:
        start, end = segment["start_cm"], segment["end_cm"]
        if (len(start) != 3 or len(end) != 3
                or any(isinstance(x, bool) or not isinstance(x, (int, float))
                       or not math.isfinite(x) for x in start + end)):
            raise ValueError("Jaw segments require finite CMS endpoints in cm")
        length = math.dist(start, end)
        interaction = segment["interaction_length_cm"]
        if (length <= 0 or isinstance(interaction, bool) or not isinstance(interaction, (int, float))
                or not math.isfinite(interaction) or interaction <= 0):
            raise ValueError("Jaw segments require positive lengths and interaction lengths")
        if not isinstance(segment.get("material"), str) or not segment["material"]:
            raise ValueError("Jaw segments require their native material name")
        if result:
            previous = result[-1]
            if math.dist(previous["end_cm"], start) > 1.e-7:
                raise ValueError("Jaw path segments must be contiguous and ordered")
            direction = [(end[i]-start[i])/length for i in range(3)]
            if math.fsum(direction[i]*previous["direction"][i] for i in range(3)) < 1.-1.e-10:
                raise ValueError("Jaw path must follow one straight incoming direction")
        result.append(dict(segment, length_cm=length, depth=length/interaction,
                           direction=[(end[i]-start[i])/length for i in range(3)]))
    if not result or not 0 < math.fsum(s["depth"] for s in result) < math.inf:
        raise ValueError("A jaw path needs positive finite material depth")
    return result


def first_interaction(path, uniform):
    """Invert the truncated first-interaction CDF on the ordered material path."""
    if (isinstance(uniform, bool) or not isinstance(uniform, (int, float))
            or not math.isfinite(uniform) or not 0 < uniform < 1):
        raise ValueError("Source depth requires an open-interval uniform draw")
    segments = path_segments(path)
    total = math.fsum(segment["depth"] for segment in segments)
    collision = -math.expm1(-total)
    depth = -math.log1p(-uniform*collision)
    before, distance = 0., 0.
    for index, segment in enumerate(segments):
        if depth < before+segment["depth"] or index == len(segments)-1:
            step = (depth-before)*segment["interaction_length_cm"]
            if not 0 <= step <= segment["length_cm"]:
                raise ValueError("First interaction escaped the declared material segment")
            point = [segment["start_cm"][i]+step*segment["direction"][i] for i in range(3)]
            return dict(target_position_cm=point, segment_index=index,
                material=segment["material"], depth_in_interaction_lengths=depth,
                incoming_path_distance_cm=distance+step,
                path_total_interaction_lengths=total,
                reference_collision_probability=collision)
        before += segment["depth"]
        distance += segment["length_cm"]


def source_position(profile, source_sha256, event_id, seed):
    """Use independent source-only hash domains; no detector outcome enters."""
    if (profile.get("schema") != "shift-assumed-jaw-depth-profile-v1"
            or profile.get("conditional_on_jaw_collision") is not True):
        raise ValueError("Declare the assumed source profile and collision conditioning")
    if (not isinstance(source_sha256, str) or len(source_sha256) != 64
            or any(x not in "0123456789abcdef" for x in source_sha256)
            or len(event_id) != 3 or any(type(x) is not int or x <= 0 for x in event_id)
            or not isinstance(seed, str) or not seed):
        raise ValueError("Source draw needs exact original identity, checksum and seed")
    paths = profile["paths"]
    probabilities = [path["mixture_probability"] for path in paths]
    if (not paths or len({path["id"] for path in paths}) != len(paths)
            or any(isinstance(p, bool) or not isinstance(p, (int, float))
                   or not math.isfinite(p) or not 0 < p <= 1 for p in probabilities)
            or not math.isclose(math.fsum(probabilities), 1., rel_tol=0., abs_tol=1.e-12)):
        raise ValueError("Declare distinct jaws with a normalized positive mixture")
    for path in paths:
        path_segments(path)
    # Midpoints of a 52-bit grid avoid either material boundary exactly.
    def draw(role):
        key = ["assumed-jaw-source-v1", seed, source_sha256, list(event_id), role]
        return (int(checksum(key)[:13], 16)+.5)/(1 << 52)
    jaw_draw, depth_draw = draw("jaw"), draw("depth")
    cumulative = 0.
    for index, path in enumerate(paths):
        cumulative += probabilities[index]
        if jaw_draw < cumulative or index == len(paths)-1:
            result = first_interaction(path, depth_draw)
            return dict(result, jaw_id=path["id"], jaw_probability=probabilities[index],
                jaw_uniform=jaw_draw, depth_uniform=depth_draw,
                source_profile_sha256=checksum(profile), source_seed=seed,
                original_source_sha256=source_sha256, event_id=list(event_id),
                statistical_unit="original event; source alternatives are not extra GEN exposure",
                source_model_assumed=True, normalization_ready=False,
                material_or_depth_weight_applied=False)
