#!/usr/bin/env python3
"""Fail closed if resolved Step 1 and Step 4 LSS configurations disagree."""

import argparse
import contextlib
import io
import json
import math
import runpy
import subprocess
import sys
from pathlib import Path


CONTRACT_FIELDS = (
    "contractVersion",
    "contractSha256",
    "materialMode",
    "fieldMode",
    "gdmlSha256",
    "fieldScale",
    "artifactOriginInModelCm",
    "modelOriginCm",
    "modelToCms",
)


def value(parameter):
    raw = parameter.value() if hasattr(parameter, "value") else parameter
    if isinstance(raw, (list, tuple)):
        return [value(item) for item in raw]
    return raw


def load_process(path):
    # Several standard NanoAOD customisations print while a configuration is
    # imported. Keep the audit output machine-readable.
    with contextlib.redirect_stdout(io.StringIO()):
        namespace = runpy.run_path(str(path))
    if "process" not in namespace:
        raise RuntimeError(f"{path}: no CMSSW process was defined")
    return namespace["process"]


def require(process, name, path):
    if not hasattr(process, name):
        raise RuntimeError(f"{path}: required process.{name} is absent")
    return getattr(process, name)


def contract(process, path):
    pset = require(process, "shiftLssWorkflowContract", path)
    return {name: value(getattr(pset, name)) for name in CONTRACT_FIELDS}


def finite_vector(parameter, length, description):
    result = [float(item) for item in value(parameter)]
    if len(result) != length or not all(math.isfinite(item) for item in result):
        raise RuntimeError(f"invalid {description}: expected {length} finite values")
    return result


def audit_runtime(process, resolved, path, is_step4):
    material_mode = resolved["materialMode"]
    field_mode = resolved["fieldMode"]

    if material_mode == "external":
        source = require(process, "shiftLssGeometryESSource", path)
        geometry_contract = require(process, "shiftLssGeometryContract", path)
        artifact_origin = [
            float(item) for item in resolved["artifactOriginInModelCm"].split(",")
        ]
        if finite_vector(source.artifactOriginInModelCm, 3, "artifact origin") != artifact_origin:
            raise RuntimeError(
                f"{path}: external-geometry artifact origin disagrees with workflow contract"
            )
        model_origin = [float(item) for item in resolved["modelOriginCm"]]
        if finite_vector(source.modelOriginCm, 3, "model origin") != model_origin:
            raise RuntimeError(
                f"{path}: external-geometry model origin disagrees with workflow contract"
            )
        model_to_cms = [float(item) for item in resolved["modelToCms"]]
        if finite_vector(source.modelToCms, 9, "model rotation") != model_to_cms:
            raise RuntimeError(
                f"{path}: external-geometry rotation disagrees with workflow contract"
            )
        configured_gdml = source.gdmlPath if hasattr(source, "gdmlPath") else source.gdmlFile
        if value(configured_gdml) != value(geometry_contract.gdmlFile):
            raise RuntimeError(
                f"{path}: external-geometry GDML disagrees with its resolved contract"
            )
    elif material_mode != "none":
        raise RuntimeError(f"{path}: unsupported material mode {material_mode!r}")

    if field_mode in ("ir1_atlas_proxy", "cms_ir5_2023_z1100"):
        require(process, "shiftLssMagneticField", path)
        require(process, "shiftLssFieldContract", path)
    elif field_mode != "none":
        raise RuntimeError(f"{path}: unsupported field mode {field_mode!r}")

    if is_step4:
        shift_muon_table = require(process, "shiftMuonTable", path)
        if not hasattr(shift_muon_table, "lssTransport"):
            raise RuntimeError(f"{path}: shiftMuonTable.lssTransport is absent")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step1", type=Path, help="resolved Step 1 Python configuration")
    parser.add_argument("step4", type=Path, nargs='?', help="resolved Step 4 Python configuration")
    parser.add_argument('--inspect-stage', choices=('1', '4'), help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.inspect_stage:
        process = load_process(args.step1)
        resolved = contract(process, args.step1)
        audit_runtime(process, resolved, args.step1, args.inspect_stage == '4')
        print(json.dumps(resolved, sort_keys=True))
        return
    if args.step4 is None:
        parser.error('step4 is required')
    # CMSSW stores era choices globally. A fully expanded dumpPython snapshot
    # has no era declaration, and cannot share an interpreter with a cmsDriver
    # configuration that declares an era. Inspect each in a fresh interpreter;
    # retain all contract and runtime checks before comparing the results.
    def inspect(path, stage):
        return json.loads(subprocess.check_output(
            [sys.executable, str(Path(__file__).resolve()), str(path),
             '--inspect-stage', stage], text=True))
    step1_contract = inspect(args.step1, '1')
    step4_contract = inspect(args.step4, '4')
    if step1_contract != step4_contract:
        differing = [
            name for name in CONTRACT_FIELDS if step1_contract[name] != step4_contract[name]
        ]
        raise RuntimeError("Step 1/Step 4 LSS contracts differ: " + ", ".join(differing))
    if step1_contract["materialMode"] == "none" and step1_contract["fieldMode"] == "none":
        raise RuntimeError("both LSS material and field are disabled")

    print(json.dumps({"status": "ok", **step1_contract}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
