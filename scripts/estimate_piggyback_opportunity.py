#!/usr/bin/env python3
"""Estimate usable stored-readout probability per colliding BX.

This is a bookkeeping estimate only.  It does not replace the Run-3
lumisection/BX-resolved trigger measurement.
"""
import argparse
import json


def estimate(l1a_hz, usable_hlt_hz, colliding_slots, crossing_hz=40_000_000.0,
             slots_per_orbit=3564):
    if min(l1a_hz, usable_hlt_hz, colliding_slots, crossing_hz) < 0:
        raise ValueError("rates and slot counts must be non-negative")
    if colliding_slots > slots_per_orbit:
        raise ValueError("colliding slots cannot exceed slots per orbit")
    colliding_bx_hz = crossing_hz * colliding_slots / slots_per_orbit
    return {
        "l1a_probability_per_colliding_bx": l1a_hz / colliding_bx_hz,
        "usable_hlt_probability_given_l1a": usable_hlt_hz / l1a_hz if l1a_hz else 0.0,
        "usable_stored_readout_probability_per_colliding_bx": usable_hlt_hz / colliding_bx_hz,
        "colliding_bx_rate_hz": colliding_bx_hz,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--l1a-hz", type=float, default=100_000)
    p.add_argument("--usable-hlt-hz", type=float, default=3_000)
    p.add_argument("--colliding-slots", type=float, default=386)
    p.add_argument("--crossing-hz", type=float, default=40_000_000)
    args = p.parse_args()
    result = estimate(args.l1a_hz, args.usable_hlt_hz, args.colliding_slots,
                      args.crossing_hz)
    result["inputs"] = vars(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
