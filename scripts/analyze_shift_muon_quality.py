#!/usr/bin/env python3
"""Compare SHIFT standalone-track algorithms using diagnostic MC logs.

Truth is used only by this offline study.  The reported track-pair observables
use reconstructed quantities exclusively and are intended to guide duplicate
cleaning in collision data.
"""

from __future__ import annotations

import argparse
import glob
import math
import re
import statistics
from dataclasses import dataclass


LINE = re.compile(r"\[ShiftMuonRecoDebug\]\[(?P<kind>[^]]+)\]\s+(?P<body>.*)")
KINDS = ("DSAtrack", "CosmicTrack", "TraversingTrack")


def fields(body: str) -> dict[str, float]:
    result = {}
    for item in body.split():
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        try:
            result[key] = float(value.rstrip(","))
        except ValueError:
            pass
    return result


def unit(eta: float, phi: float) -> tuple[float, float, float]:
    cosh_eta = math.cosh(eta)
    return math.cos(phi) / cosh_eta, math.sin(phi) / cosh_eta, math.tanh(eta)


def dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def norm(a) -> float:
    return math.sqrt(dot(a, a))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


@dataclass
class Particle:
    pt: float
    eta: float
    phi: float
    position: tuple[float, float, float]
    valid_hits: int = 0
    normalized_chi2: float = float("inf")

    @property
    def direction(self):
        return unit(self.eta, self.phi)

    @property
    def momentum(self):
        return self.pt * math.cosh(self.eta)


def direction_error(a: Particle, b: Particle) -> float:
    return math.acos(min(1.0, max(-1.0, abs(dot(a.direction, b.direction)))))


def direction_delta_r(a: Particle, b: Particle) -> float:
    def delta_phi(first: float, second: float) -> float:
        value = abs(first - second)
        return min(value, 2.0 * math.pi - value)

    direct = math.hypot(a.eta - b.eta, delta_phi(a.phi, b.phi))
    reverse = math.hypot(a.eta + b.eta, delta_phi(a.phi + math.pi, b.phi))
    return min(direct, reverse)


def point_line_distance(point, track: Particle) -> float:
    return norm(cross(sub(point, track.position), track.direction))


def line_distance(a: Particle, b: Particle) -> float:
    normal = cross(a.direction, b.direction)
    normal_norm = norm(normal)
    delta = sub(b.position, a.position)
    if normal_norm < 1e-8:
        return norm(cross(delta, a.direction))
    return abs(dot(delta, normal)) / normal_norm


def quantile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[round(fraction * (len(ordered) - 1))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("logs", nargs="+", help="Step-3 log files or glob patterns")
    parser.add_argument("--max-dr", type=float, default=0.5, help="truth-match direction delta-R")
    args = parser.parse_args()
    paths = sorted({path for pattern in args.logs for path in glob.glob(pattern)})
    if not paths:
        parser.error("no logs matched")

    metrics = {kind: {name: [] for name in ("angle", "momentum", "origin")} for kind in KINDS}
    overlaps = {(a, b): [] for i, a in enumerate(KINDS) for b in KINDS[i + 1 :]}
    selections = {
        name: {metric: [] for metric in ("angle", "momentum", "origin")}
        for name in ("DSA-first", "cosmic-first", "traversing-first", "most-hits", "best-chi2", "target-DCA")
    }
    truth_count = matched_count = 0

    def analyze(muons: list[Particle], tracks: dict[str, list[Particle]]) -> None:
        nonlocal truth_count, matched_count
        for muon in muons:
            truth_count += 1
            candidates = {}
            for kind in KINDS:
                if not tracks[kind]:
                    continue
                track = min(tracks[kind], key=lambda item: direction_delta_r(muon, item))
                if direction_delta_r(muon, track) > args.max_dr:
                    continue
                angle = direction_error(muon, track)
                candidates[kind] = track
                metrics[kind]["angle"].append(angle)
                metrics[kind]["momentum"].append(abs(track.momentum / muon.momentum - 1.0))
                metrics[kind]["origin"].append(point_line_distance(muon.position, track))
            if candidates:
                matched_count += 1
            if len(candidates) >= 2:
                priority_orders = {
                    "DSA-first": KINDS,
                    "cosmic-first": ("CosmicTrack", "TraversingTrack", "DSAtrack"),
                    "traversing-first": ("TraversingTrack", "CosmicTrack", "DSAtrack"),
                }
                choices = {
                    name: next(candidates[kind] for kind in order if kind in candidates)
                    for name, order in priority_orders.items()
                }
                choices["most-hits"] = max(candidates.values(), key=lambda item: item.valid_hits)
                choices["best-chi2"] = min(candidates.values(), key=lambda item: item.normalized_chi2)
                nominal_target = (0.0, 0.0, 14800.0)
                choices["target-DCA"] = min(
                    candidates.values(), key=lambda item: point_line_distance(nominal_target, item)
                )
                for name, track in choices.items():
                    selections[name]["angle"].append(direction_error(muon, track))
                    selections[name]["momentum"].append(abs(track.momentum / muon.momentum - 1.0))
                    selections[name]["origin"].append(point_line_distance(muon.position, track))
            for pair, rows in overlaps.items():
                if pair[0] not in candidates or pair[1] not in candidates:
                    continue
                a, b = candidates[pair[0]], candidates[pair[1]]
                rows.append(
                    (
                        direction_error(muon, a),
                        direction_error(muon, b),
                        abs(a.momentum / muon.momentum - 1.0),
                        abs(b.momentum / muon.momentum - 1.0),
                        point_line_distance(muon.position, a),
                        point_line_distance(muon.position, b),
                        direction_error(a, b),
                        abs(a.pt - b.pt) / max(a.pt, b.pt),
                        line_distance(a, b),
                    )
                )

    for path in paths:
        muons: list[Particle] = []
        tracks = {kind: [] for kind in KINDS}
        have_event = False
        with open(path, encoding="utf-8", errors="replace") as source:
            for text in source:
                match = LINE.search(text)
                if not match:
                    continue
                kind = match.group("kind")
                data = fields(match.group("body"))
                if kind == "summary":
                    if have_event:
                        analyze(muons, tracks)
                    muons = []
                    tracks = {name: [] for name in KINDS}
                    have_event = True
                elif kind == "SimMuon" and data.get("vertIndex") == 0:
                    hits = sum(data.get(name, 0) for name in ("dtHits", "cscHits", "rpcHits", "gemHits"))
                    if hits:
                        muons.append(Particle(data["pt"], data["eta"], data["phi"], (0.0, 0.0, data["vertexZ"])))
                elif kind in tracks:
                    tracks[kind].append(
                        Particle(
                            data["pt"],
                            data["eta"],
                            data["phi"],
                            (data["vx"], data["vy"], data["vz"]),
                            int(data.get("validHits", 0)),
                            data.get("chi2", float("inf")) / max(data.get("ndof", 0), 1),
                        )
                    )
        if have_event:
            analyze(muons, tracks)

    print(f"logs={len(paths)} hit-bearing primary muons={truth_count} matched={matched_count}")
    for kind in KINDS:
        count = len(metrics[kind]["angle"])
        print(
            f"{kind}: n={count} median angle={statistics.median(metrics[kind]['angle']):.4g} rad "
            f"|dp/p|={statistics.median(metrics[kind]['momentum']):.4g} "
            f"originDCA={statistics.median(metrics[kind]['origin']):.4g} cm "
            f"(q95={quantile(metrics[kind]['origin'], .95):.4g} cm)"
        )
    for pair, rows in overlaps.items():
        if not rows:
            continue
        angle_a = sum(row[0] < row[1] for row in rows)
        momentum_a = sum(row[2] < row[3] for row in rows)
        origin_a = sum(row[4] < row[5] for row in rows)
        pair_angle = [row[6] for row in rows]
        pair_pt = [row[7] for row in rows]
        pair_distance = [row[8] for row in rows]
        print(
            f"{pair[0]} vs {pair[1]}: overlap={len(rows)}; {pair[0]} wins "
            f"angle={angle_a/len(rows):.1%}, momentum={momentum_a/len(rows):.1%}, "
            f"origin={origin_a/len(rows):.1%}; reco-only q95 "
            f"angle={quantile(pair_angle, .95):.4g} rad, relPt={quantile(pair_pt, .95):.4g}, "
            f"lineDCA={quantile(pair_distance, .95):.4g} cm"
        )
    print("selection on overlap (medians):")
    for name, values in selections.items():
        print(
            f"  {name}: angle={statistics.median(values['angle']):.4g} rad, "
            f"|dp/p|={statistics.median(values['momentum']):.4g}, "
            f"originDCA={statistics.median(values['origin']):.4g} cm"
        )


if __name__ == "__main__":
    main()
