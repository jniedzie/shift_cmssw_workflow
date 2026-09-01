#!/usr/bin/env python3

"""Crash-isolated classification of raw FLUKA regions before conversion."""

from functools import reduce
import json
import os
import resource
import select
import signal


def isolated_region_bounds(region, timeout_seconds=300.0, include_zone_bounds=False):
    """Evaluate native CSG in a child so a CGAL failure is region-local."""

    if timeout_seconds <= 0.0:
        raise ValueError("timeout_seconds must be positive")
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            null_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null_fd, 1)
            os.dup2(null_fd, 2)
            os.close(null_fd)
            evaluated_zone_bounds = region.zoneAABBs(aabb=None)
            non_null_zone_bounds = [
                bound for bound in evaluated_zone_bounds if bound is not None
            ]
            if non_null_zone_bounds:
                bound = reduce(
                    lambda first, second: first.union(second),
                    non_null_zone_bounds,
                )
                payload = {
                    "status": "ok",
                    "bounds": [
                        list(map(float, bound.lower)),
                        list(map(float, bound.upper)),
                    ],
                }
            else:
                payload = {"status": "null"}
            if include_zone_bounds:
                payload["zone_bounds"] = [
                    None
                    if zone_bound is None
                    else [
                        list(map(float, zone_bound.lower)),
                        list(map(float, zone_bound.upper)),
                    ]
                    for zone_bound in evaluated_zone_bounds
                ]
        except BaseException as error:
            payload = {
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
            }
        encoded = json.dumps(payload).encode("utf-8")
        while encoded:
            written = os.write(write_fd, encoded)
            encoded = encoded[written:]
        os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    readable, _, _ = select.select([read_fd], [], [], timeout_seconds)
    if not readable:
        os.kill(child, signal.SIGKILL)
        os.waitpid(child, 0)
        os.close(read_fd)
        return {
            "status": "error",
            "error": f"native CSG evaluation exceeded {timeout_seconds:g} seconds",
            "timed_out": True,
        }

    chunks = []
    while True:
        chunk = os.read(read_fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(read_fd)
    _, status = os.waitpid(child, 0)
    if os.WIFSIGNALED(status):
        child_signal = os.WTERMSIG(status)
        return {
            "status": "error",
            "error": f"native CSG evaluation terminated by signal {child_signal}",
            "signal": child_signal,
        }
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        return {
            "status": "error",
            "error": f"CSG evaluation child exited with status {status}",
        }
    if not chunks:
        return {"status": "error", "error": "CSG evaluation child returned no result"}
    try:
        return json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return {"status": "error", "error": f"invalid child result: {error}"}


def classify_raw_regions(
    fluka_registry,
    region_names,
    timeout_seconds=300.0,
    progress_every=0,
    include_bounds=False,
):
    """Classify raw regions without allowing length safety to create material."""

    region_names = list(region_names)
    if len(region_names) != len(set(region_names)):
        raise ValueError("region_names contains duplicates")
    unknown = sorted(set(region_names) - set(fluka_registry.regionDict))
    if unknown:
        raise ValueError("unknown FLUKA regions: " + ", ".join(unknown))

    blackhole_regions = [
        name
        for name in region_names
        if (fluka_registry.assignmas.get(name)[0] if isinstance(fluka_registry.assignmas.get(name), (list, tuple)) else fluka_registry.assignmas.get(name)) == "BLCKHOLE"
    ]
    blackhole_set = set(blackhole_regions)
    evaluated_regions = [name for name in region_names if name not in blackhole_set]
    non_null_regions = []
    source_null_regions = []
    evaluation_errors = []
    bounds = {}
    zone_bounds = {}

    for index, name in enumerate(evaluated_regions, 1):
        if progress_every > 0 and index % progress_every == 0:
            print(
                f"classified raw bounds for {index}/{len(evaluated_regions)} regions",
                flush=True,
            )
        evaluation = isolated_region_bounds(
            fluka_registry.regionDict[name],
            timeout_seconds=timeout_seconds,
            include_zone_bounds=include_bounds,
        )
        if evaluation["status"] == "ok":
            non_null_regions.append(name)
            if include_bounds:
                bounds[name] = evaluation["bounds"]
                zone_bounds[name] = evaluation["zone_bounds"]
        elif evaluation["status"] == "null":
            source_null_regions.append(name)
            if include_bounds:
                zone_bounds[name] = evaluation["zone_bounds"]
        else:
            evaluation_errors.append({"name": name, **evaluation})

    result = {
        "requested_region_count": len(region_names),
        "blackhole_region_count": len(blackhole_regions),
        "blackhole_regions": blackhole_regions,
        "evaluated_region_count": len(evaluated_regions),
        "non_null_region_count": len(non_null_regions),
        "non_null_regions": non_null_regions,
        "source_null_region_count": len(source_null_regions),
        "source_null_regions": source_null_regions,
        "evaluation_error_count": len(evaluation_errors),
        "evaluation_errors": evaluation_errors,
        "timeout_seconds": timeout_seconds,
        "passed": not evaluation_errors,
    }
    if include_bounds:
        result["bounds_mm"] = bounds
        result["zone_bounds_mm"] = zone_bounds
    return result


def resolve_raw_region_classifications(primary, secondary, requested_regions):
    """Resolve CGAL null/failure cases with an independent pycsg evaluation."""

    requested_regions = list(requested_regions)
    blackhole = set(primary["blackhole_regions"])
    primary_non_null = set(primary["non_null_regions"])
    primary_null = set(primary["source_null_regions"])
    primary_errors = {
        item["name"]: item for item in primary["evaluation_errors"]
    }
    ambiguous = primary_null | set(primary_errors)

    secondary_non_null = set(secondary["non_null_regions"])
    secondary_null = set(secondary["source_null_regions"])
    secondary_errors = {
        item["name"]: item for item in secondary["evaluation_errors"]
    }
    secondary_classified = (
        secondary_non_null | secondary_null | set(secondary_errors)
    )
    missing = sorted(ambiguous - secondary_classified)
    unexpected = sorted(secondary_classified - ambiguous)
    if missing or unexpected:
        raise ValueError(
            "secondary classification region mismatch: "
            f"missing={missing}, unexpected={unexpected}"
        )

    non_null = set(primary_non_null)
    source_null = set()
    fallback_non_null = []
    disagreements = []
    unresolved = []
    deferred = []
    for name in requested_regions:
        if name not in ambiguous:
            continue
        if name in secondary_non_null:
            non_null.add(name)
            if name in primary_null:
                disagreements.append({
                    "name": name,
                    "primary": "null",
                    "secondary": "non_null",
                    "resolution": "retain",
                })
            else:
                fallback_non_null.append(name)
            continue
        if name in secondary_null and name in primary_null:
            source_null.add(name)
            continue

        if name in primary_null and name in secondary_errors:
            deferred.append(name)
            continue

        detail = {"name": name}
        if name in primary_errors:
            detail["primary_error"] = primary_errors[name]["error"]
        else:
            detail["primary_status"] = "null"
        if name in secondary_errors:
            detail["secondary_error"] = secondary_errors[name]["error"]
        else:
            detail["secondary_status"] = "null"
        detail["error"] = "raw region could not be classified consistently"
        unresolved.append(detail)

    non_null_regions = [
        name for name in requested_regions if name in non_null and name not in blackhole
    ]
    conversion_candidate_regions = [
        name
        for name in requested_regions
        if (name in non_null or name in deferred) and name not in blackhole
    ]
    source_null_regions = [
        name for name in requested_regions if name in source_null and name not in blackhole
    ]
    return {
        "requested_region_count": len(requested_regions),
        "blackhole_region_count": len(blackhole),
        "blackhole_regions": [
            name for name in requested_regions if name in blackhole
        ],
        "non_null_region_count": len(non_null_regions),
        "non_null_regions": non_null_regions,
        "conversion_candidate_region_count": len(conversion_candidate_regions),
        "conversion_candidate_regions": conversion_candidate_regions,
        "source_null_region_count": len(source_null_regions),
        "source_null_regions": source_null_regions,
        "fallback_non_null_region_count": len(fallback_non_null),
        "fallback_non_null_regions": fallback_non_null,
        "backend_disagreement_count": len(disagreements),
        "backend_disagreements": disagreements,
        "deferred_null_validation_region_count": len(deferred),
        "deferred_null_validation_regions": deferred,
        "evaluation_error_count": len(unresolved),
        "evaluation_errors": unresolved,
        "primary_backend": "cgal_sm",
        "secondary_backend": "pycsg",
        "primary_classification": primary,
        "secondary_classification": secondary,
        "passed": not unresolved,
    }
