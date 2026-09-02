#!/usr/bin/env python3
"""Fail closed if resolved Step 1 and Step 4 LSS configurations disagree."""

import argparse
import contextlib
import io
import json
import math
import runpy
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
        if value(source.gdmlFile) != value(geometry_contract.gdmlFile):
            raise RuntimeError(
                f"{path}: external-geometry GDML disagrees with its resolved contract"
            )
    elif material_mode != "none":
        raise RuntimeError(f"{path}: unsupported material mode {material_mode!r}")

    if field_mode == "ir1_atlas_proxy":
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
    parser.add_argument("step4", type=Path, help="resolved Step 4 Python configuration")
    args = parser.parse_args()

    step1 = load_process(args.step1)
    step4 = load_process(args.step4)
    step1_contract = contract(step1, args.step1)
    step4_contract = contract(step4, args.step4)
    if step1_contract != step4_contract:
        differing = [
            name for name in CONTRACT_FIELDS if step1_contract[name] != step4_contract[name]
        ]
        raise RuntimeError("Step 1/Step 4 LSS contracts differ: " + ", ".join(differing))
    if step1_contract["materialMode"] == "none" and step1_contract["fieldMode"] == "none":
        raise RuntimeError("both LSS material and field are disabled")

    audit_runtime(step1, step1_contract, args.step1, False)
    audit_runtime(step4, step4_contract, args.step4, True)
    print(json.dumps({"status": "ok", **step1_contract}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
