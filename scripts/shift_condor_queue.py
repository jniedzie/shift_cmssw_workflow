# Code style: AI-code.
"""Count current Condor jobs by role and state."""

import argparse
from collections import Counter, defaultdict
import getpass
import json
from pathlib import PurePosixPath
import re

from shift_condor_native import NativeCondorError, query


STATUSES = {
    1: "IDLE",
    2: "RUN",
    5: "HELD",
    6: "TRANSFER",
    7: "SUSPEND",
    4: "COMPLETE",
    3: "REMOVED",
}

DAG_COUNTS = {
    "DAG_NodesDone": "done",
    "DAG_NodesQueued": "submitted",
    "DAG_NodesReady": "ready to submit",
    "DAG_NodesUnready": "waiting for dependencies",
    "DAG_NodesPrerun": "pre-script",
    "DAG_NodesPostrun": "post-script",
    "DAG_NodesFailed": "failed",
    "DAG_NodesFutile": "blocked by failure",
}


def batch_name(ad):
    return ad.get("JobBatchName") or "(unnamed)"


def command_name(ad):
    return PurePosixPath(ad.get("Cmd") or "").name


def is_controller(ad):
    if batch_name(ad).startswith("controller_") or command_name(ad) == "condor_dagman":
        return True
    # These frozen SHIFT dispatchers launch and wait for a detector DAG.
    # PREP nodes using the same executable do the input preparation themselves.
    return (batch_name(ad).startswith("SHIFT_DP_") and command_name(ad) == "dispatch_v3.sh"
            and re.fullmatch(r"RESPONSE[0-9]*", ad.get("DAGNodeName") or "") is not None)


def job_id(ad):
    return f"{ad['ClusterId']}.{ad['ProcId']}"


def summarize(ads, by_batch):
    by_role = {"workers": Counter(), "controllers": Counter()}
    batches = defaultdict(Counter)
    for ad in ads:
        role = "controllers" if is_controller(ad) else "workers"
        status = STATUSES.get(ad.get("JobStatus"), "OTHER")
        by_role[role][status] += 1
        if by_batch:
            batches[(role, batch_name(ad))][status] += 1

    total = by_role["workers"] + by_role["controllers"]
    statuses = list(STATUSES.values())
    if total["OTHER"]:
        statuses.append("OTHER")
    rows = [(f"{role}: {name}", counts) for (role, name), counts in sorted(batches.items())]
    rows.extend(by_role.items())
    rows.append(("all jobs", total))
    width = max(len(label) for label, _ in rows)
    print(f"{'JOBS':<{width}} " + " ".join(f"{status:>8}" for status in (*statuses, "TOTAL")))
    for label, counts in rows:
        values = [counts[status] for status in statuses]
        print(f"{label:<{width}} " + " ".join(f"{value:>8}" for value in (*values, sum(values))))


def find_manager(ads, target):
    managers = [
        ad for ad in ads
        if command_name(ad) == "condor_dagman"
        and target in (batch_name(ad), job_id(ad), str(ad["ClusterId"]))
    ]
    if not managers:
        raise ValueError(f"No queued DAG manager matches {target!r}")
    if len(managers) > 1:
        ids = ", ".join(job_id(ad) for ad in managers)
        raise ValueError(f"More than one DAG manager matches {target!r}. Use a job ID ({ids}).")
    return managers[0]


def jobs_in_dag(ads, manager):
    children = defaultdict(list)
    for ad in ads:
        if "DAGManJobId" in ad:
            children[ad["DAGManJobId"]].append(ad)
    jobs = []
    visited = set()
    pending = [manager]
    while pending:
        ad = pending.pop()
        if job_id(ad) in visited:
            continue
        visited.add(job_id(ad))
        jobs.append(ad)
        if command_name(ad) == "condor_dagman":
            pending.extend(children[ad["ClusterId"]])
    return jobs


def show_manager(manager):
    print(f"DAG manager {job_id(manager)}  {batch_name(manager)}")
    total = manager.get("DAG_NodesTotal")
    if total is None:
        print("DAG step counts are unavailable.")
    else:
        counts = ", ".join(
            f"{manager[key]} {label}" for key, label in DAG_COUNTS.items() if manager.get(key)
        )
        print(f"DAG steps (reported by the manager)  {total} total" + (f"  |  {counts}" if counts else ""))
    print()


def list_controllers(ads):
    print(f"{'CONTROLLER ID':<15} {'STATE':<8} {'PARENT DAG':<12} BATCH NAME")
    for ad in sorted(filter(is_controller, ads), key=lambda ad: (ad["ClusterId"], ad["ProcId"])):
        state = STATUSES.get(ad.get("JobStatus"), "OTHER")
        parent = ad.get("DAGManJobId")
        print(f"{job_id(ad):<15} {state:<8} {parent or '-':<12} {batch_name(ad)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--by-batch", action="store_true", help="also show each batch name")
    view.add_argument("--controllers", action="store_true", help="list individual controllers and their job IDs")
    parser.add_argument("--dag", metavar="JOB_ID_OR_NAME", help="show one DAG and its queued descendants")
    args = parser.parse_args()
    constraint = "Owner == " + json.dumps(getpass.getuser())
    attributes = ["ClusterId", "ProcId", "JobBatchName", "JobStatus", "Cmd", "DAGManJobId", "DAGNodeName"]
    if args.dag:
        attributes.extend(["DAG_NodesTotal", *DAG_COUNTS])
    try:
        ads = query(constraint, attributes)
    except NativeCondorError as error:
        parser.exit(1, f"{error}\n")
    if args.dag:
        try:
            manager = find_manager(ads, args.dag)
        except ValueError as error:
            parser.error(str(error))
        ads = jobs_in_dag(ads, manager)
        show_manager(manager)
    if args.controllers:
        list_controllers(ads)
    else:
        summarize(ads, args.by_batch)


if __name__ == "__main__":
    main()
