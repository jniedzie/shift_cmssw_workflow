#!/usr/bin/env python3
"""Run and validate a bounded GEN pilot in a new output directory.

Use an already prepared CMSSW runtime. Never builds or submits batch jobs.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from fixed_target_generation import beam_settings, process_settings


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", choices=("jpsi", "chic", "psi2s", "qcd", "dy"), required=True)
    parser.add_argument("--lower", type=float, required=True)
    parser.add_argument("--upper", type=float, required=True)
    parser.add_argument("--events", type=int, default=20)
    parser.add_argument("--seed", type=int, default=13579)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    process_settings(args.sample, args.lower, args.upper)
    beam_settings(6800.)
    if not 1 <= args.events <= 10000 or not 1 <= args.seed <= 899999999:
        parser.error("Require 1..10000 events and seed 1..899999999")
    if not shutil.which("cmsRun") or not os.environ.get("CMSSW_BASE"):
        parser.error("Enter the existing CMSSW runtime first; no build is performed")
    # Same granular-library convention as setup_cmssw.sh; avoid mixing the
    # monolithic release bundles with the locally rebuilt package libraries.
    os.environ["LD_LIBRARY_PATH"] = ":".join(
        p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":") if "/biglib/" not in p)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    for name in ("fixed_target_gen_cfg.py", "fixed_target_generation.py", "audit_fixed_target_gen.py"):
        shutil.copy2(here / name, out / name)
    command = ["cmsRun", str(out / "fixed_target_gen_cfg.py"), f"sample={args.sample}",
               f"lower={args.lower}", f"upper={args.upper}", f"maxEvents={args.events}",
               f"seed={args.seed}", f"outputDir={out}"]
    manifest = {"command": command, "status": "running", "physics_valid": False,
                "source_sha256": {p.name: digest(p) for p in out.glob("*.py")}}
    base = Path(os.environ["CMSSW_BASE"])
    manifest["cmssw_git_head"] = subprocess.check_output(
        ["git", "-C", str(base / "src"), "rev-parse", "HEAD"], text=True).strip()
    manifest["cmssw_git_status"] = subprocess.check_output(
        ["git", "-C", str(base / "src"), "status", "--porcelain"], text=True)
    manifest["pythia_tool"] = subprocess.check_output(
        ["scram", "tool", "info", "pythia8"], cwd=base / "src", text=True)
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        with (out / "cmsRun.log").open("w") as log:
            subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=600)
        with (out / "audit.log").open("w") as log:
            subprocess.run([sys.executable, str(out / "audit_fixed_target_gen.py"), str(out)],
                           cwd=out, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        manifest["status"] = "GEN validation passed; physics provisional"
        manifest["output_sha256"] = digest(out / "gen.root")
        manifest["validation"] = json.loads((out / "validation.json").read_text())
    except (subprocess.SubprocessError, OSError) as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        raise
    finally:
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Validated {args.events} {args.sample} GEN events: {out}")


if __name__ == "__main__":
    main()
