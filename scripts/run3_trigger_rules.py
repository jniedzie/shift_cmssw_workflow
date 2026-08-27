#!/usr/bin/env python3

"""Ordered L1A trigger-rule evaluation for the Run-3 SHIFT proxy."""

from dataclasses import dataclass


RULESET_NAME = "run3-standard-v1"
RULESET_SOURCE = (
    "CMSSW EventFilter/L1TRawToDigi/plugins/"
    "TriggerRulePrefireVetoFilter.cc"
)
REQUIRED_HISTORY_BX = 240


@dataclass(frozen=True)
class TriggerRule:
    name: str
    max_accepts: int
    window_bx: int

    def as_dict(self):
        return {
            "name": self.name,
            "max_accepts": self.max_accepts,
            "window_bx": self.window_bx,
        }


RUN3_STANDARD_RULES = (
    TriggerRule("one_in_three", 1, 3),
    TriggerRule("two_in_twenty_five", 2, 25),
    TriggerRule("three_in_one_hundred", 3, 100),
    TriggerRule("four_in_two_hundred_forty", 4, 240),
)


def ruleset_metadata():
    return {
        "name": RULESET_NAME,
        "source": RULESET_SOURCE,
        "status": "requires_run_period_tcds_validation",
        "required_history_bx": REQUIRED_HISTORY_BX,
        "rules": [rule.as_dict() for rule in RUN3_STANDARD_RULES],
    }


class TriggerRuleEngine:
    """Apply a fixed ruleset to monotonically increasing candidate BXs."""

    def __init__(self, rules=RUN3_STANDARD_RULES):
        self.rules = tuple(rules)
        if not self.rules:
            raise ValueError("trigger-rule engine requires at least one rule")
        self._accepted_bxs = []
        self._last_bx = None

    @property
    def accepted_bxs(self):
        return tuple(self._accepted_bxs)

    def evaluate(self, bx, candidate):
        if self._last_bx is not None and bx <= self._last_bx:
            raise ValueError("trigger-rule BXs must be strictly increasing")
        self._last_bx = bx

        decision = {
            "candidate": bool(candidate),
            "accepted": False,
            "reason": "not_candidate",
            "rule_checks": [],
            "violated_rules": [],
        }
        if not candidate:
            return decision

        for rule in self.rules:
            preceding = [
                accepted_bx
                for accepted_bx in self._accepted_bxs
                if bx - accepted_bx < rule.window_bx
            ]
            check = {
                **rule.as_dict(),
                "preceding_accepted_bxs": preceding,
                "preceding_accept_count": len(preceding),
                "would_allow": len(preceding) < rule.max_accepts,
            }
            decision["rule_checks"].append(check)
            if not check["would_allow"]:
                decision["violated_rules"].append(check)

        if not decision["violated_rules"]:
            decision["accepted"] = True
            decision["reason"] = "accepted"
            self._accepted_bxs.append(bx)
        else:
            decision["reason"] = "blocked_by_trigger_rules"
        return decision


def validate_recorded_l1a_history(delta_bxs, rules=RUN3_STANDARD_RULES):
    """Return rule violations in a recorded preceding-L1A delta sequence."""

    normalized = [int(delta) for delta in delta_bxs]
    violations = []
    if any(delta <= 0 for delta in normalized):
        violations.append("recorded L1A deltas must be positive")
    if normalized != sorted(normalized):
        violations.append("recorded L1A deltas must be ordered nearest-first")
    for rule in rules:
        count = sum(delta < rule.window_bx for delta in normalized)
        if count >= rule.max_accepts:
            violations.append(
                f"{rule.name}: found {count} preceding L1As inside {rule.window_bx} BX"
            )
    return violations
