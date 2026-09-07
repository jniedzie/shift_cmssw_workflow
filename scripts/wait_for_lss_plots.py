#!/usr/bin/env python3
"""Wait for complete paired production, then run fail-closed ROOT analysis."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("control", type=Path)
    ap.add_argument("comparison", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--chunks", type=int, default=1000)
    ap.add_argument("--events", type=int, default=10000)
    ap.add_argument("--timeout-hours", type=float, default=48)
    ap.add_argument("--require-transport", action="store_true",
                    help="require one detailed transport trace for every chunk")
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.timeout_hours*3600
    status_file = args.output/"completion_status.json"
    while True:
        counts = [len(list((p/"samples/step4").glob("events_NanoAOD_part_*.root"))) for p in (args.control,args.comparison)]
        logs = [len(list((p/"logs").glob("step4_events_*_part_*.log"))) for p in (args.control,args.comparison)]
        traces = len(list((args.comparison/"logs").glob("muon_transport_part*.json")))
        status = {"state": "waiting", "step4_files": counts, "step4_logs": logs,
                  "transport_files": traces, "expected_chunks": args.chunks, "unix_time": time.time()}
        status_file.write_text(json.dumps(status,indent=2)+"\n")
        print(json.dumps(status),flush=True)
        transport_ready = not args.require_transport or traces == args.chunks
        if counts == [args.chunks]*2 and logs == [args.chunks]*2 and transport_ready:
            break
        if any(n > args.chunks for n in counts+logs) or (args.require_transport and traces > args.chunks):
            raise RuntimeError("Unexpected extra chunks in production output")
        if time.monotonic() >= deadline:
            raise RuntimeError("Production did not complete within the monitoring timeout")
        time.sleep(60)
    command = [sys.executable, str(Path(__file__).with_name("compare_lss_reconstruction.py")),
               str(args.control),str(args.comparison),str(args.output),
               "--expected-events",str(args.events)]
    if args.require_transport:
        command.extend(["--transport-directory", str(args.comparison/"logs")])
    result = subprocess.run(command,check=False)
    status["state"] = "complete" if result.returncode == 0 else "analysis_failed"
    status["returncode"] = result.returncode
    status_file.write_text(json.dumps(status,indent=2)+"\n")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
