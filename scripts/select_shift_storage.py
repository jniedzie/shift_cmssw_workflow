#!/usr/bin/env python3
# Code style: AI-code.
"""Select an already qualified destination from a fresh admission snapshot."""
import argparse
import json
from pathlib import Path
import time


TIERS = (
    ("afs", "/afs/cern.ch/work/j/jniedzie/private"),
    ("private_eos", "/eos/user/j/jniedzie"),
    ("group_eos", "/eos/cms/store/group/phys_exotica/xtracks/jeremi/shift_cmssw"),
)


def count(record, key):
    value = record[key]
    if type(value) is not int or value < 0:
        raise ValueError(f"{key} must be a nonnegative integer")
    return value


def select(snapshot, now):
    """Qualification and protected commitments come from the launcher's own audit."""
    checked = count(snapshot, "checked_epoch")
    lifetime = count(snapshot, "max_age_seconds")
    if not lifetime or not checked <= now <= checked + lifetime:
        raise ValueError("Storage admission snapshot is stale or future-dated")
    if type(snapshot["restricted"]) is not bool:
        raise ValueError("Explicit sensitivity classification is required")
    relative = Path(snapshot["relative_path"])
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("Destination must be relative to the admitted root")
    failures = {}
    for name, root in TIERS:
        if name == "group_eos" and snapshot["restricted"]:
            failures[name] = "restricted artifacts require private storage"
            continue
        candidate = snapshot["destinations"].get(name, {})
        if candidate.get("qualified") is not True:
            failures[name] = "route lacks worker and downstream qualification"
            continue
        for unit in ("bytes", "files"):
            available = count(candidate, f"free_{unit}") - count(candidate, f"protected_{unit}")
            if available < count(candidate, f"required_{unit}"):
                failures[name] = f"insufficient unreserved {unit}"
                break
        else:
            return {"tier": name, "path": str(Path(root) / relative), "skipped": failures}
    raise ValueError(f"No admissible storage destination: {failures}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(select(json.loads(args.snapshot.read_text()), int(time.time()))))
    except (ValueError, TypeError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
