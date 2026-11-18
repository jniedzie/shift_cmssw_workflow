#!/usr/bin/env python3
# Code style: AI-code.
"""AI-generated preparation of a bounded, restricted Run-3 RAW reconstruction.

Run inside an initialized CMSSW runtime. This prepares a config; it never runs
cmsRun, rebuilds CMSSW, submits jobs or publishes reconstructed observables.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess


def validate_request(dataset, inputs, global_tag, era, max_events, certification):
    if not re.fullmatch(r"/[^/]+/Run2023[BCD]-[^/]+/RAW", dataset):
        raise ValueError("pilot requires one explicit 2023 B/C/D RAW dataset")
    if not inputs or len(inputs) != len(set(inputs)):
        raise ValueError("provide distinct input files in frozen ownership order")
    if any(not name.startswith(("root://", "/store/", "file:")) for name in inputs):
        raise ValueError("input files must use root://, /store/, or file: names")
    if not re.fullmatch(r"[0-9]+X_dataRun3_[A-Za-z0-9_]+", global_tag):
        raise ValueError("provide a concrete Run-3 data GlobalTag, not auto or MC conditions")
    if era != "Run3_2023":
        raise ValueError("this bounded pilot supports the installed Run3_2023 era only")
    if type(max_events) is not int or not 1 <= max_events <= 1000:
        raise ValueError("pilot max-events must be between 1 and 1000")
    if not isinstance(certification, dict) or not certification:
        raise ValueError("certification must be a nonempty run/lumisection JSON object")
    for run, ranges in certification.items():
        if not isinstance(run, str) or not run.isdecimal() or int(run) <= 0 or not ranges:
            raise ValueError("invalid certification run")
        previous = 0
        for bounds in ranges:
            if (not isinstance(bounds, list) or len(bounds) != 2
                    or any(type(value) is not int for value in bounds)
                    or bounds[0] <= previous or bounds[1] < bounds[0]):
                raise ValueError("invalid or overlapping certification lumisection ranges")
            previous = bounds[1]


def validate_profile(path, expected_sha256):
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("reconstruction profile requires an explicit SHA-256 pin")
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("reconstruction profile differs from its qualified SHA-256 pin")


def customise_data(process, certification_file, reconstruction_profile, profile_sha256, geometry_file_tag=None):
    """Attach an explicit qualified SHIFT reconstruction profile to data RECO."""
    import FWCore.ParameterSet.Config as cms
    from FWCore.PythonUtilities.LumiList import LumiList
    from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import (
        customiseRecoForShiftMuons,
        customiseTraversingShiftMuonReco,
    )

    process.source.duplicateCheckMode = cms.untracked.string("checkAllFilesOpened")
    process.source.lumisToProcess = LumiList(filename=certification_file).getVLuminosityBlockRange()
    process.options.numberOfThreads = cms.untracked.uint32(1)
    process.options.numberOfStreams = cms.untracked.uint32(1)
    process.options.wantSummary = cms.untracked.bool(False)
    # Preserve the production reconstruction baseline: DT standard, no tracker
    # augmentation or GEM extension. No trigger filter or GEN-based admission.
    process = customiseRecoForShiftMuons(
        process, enableDTMeasurement=True, enableGEMMeasurement=False,
    )
    process = customiseTraversingShiftMuonReco(
        process, trackerMode="none", enableDTMeasurement=True,
    )
    validate_profile(reconstruction_profile, profile_sha256)
    profile = runpy.run_path(reconstruction_profile)
    apply_profile = profile.get("customise_shift_data_profile")
    if not callable(apply_profile):
        raise ValueError("qualified profile lacks customise_shift_data_profile(process)")
    process = apply_profile(process)
    if geometry_file_tag is not None:
        if geometry_file_tag != "Geometry_133DD4hep2_Extended2023_mc":
            raise ValueError("data geometry override must use the qualified Extended2023 payload")
        process.GlobalTag.toGet.append(cms.PSet(
            record=cms.string("GeometryFileRcd"),
            label=cms.untracked.string("Extended"),
            tag=cms.string(geometry_file_tag),
            connect=cms.string("frontier://FrontierProd/CMS_CONDITIONS"),
        ))
    process.shiftMuonTable.produceHitTruthAssociation = cms.bool(False)
    process.shiftMuonTable.produceMomentumClosureDiagnostics = cms.bool(False)
    process.shiftMuonTable.directionalRefitLogGeometryMaterialComparison = cms.bool(False)
    # The combined RAW-to-Nano job must run the additional muon paths before
    # Nano tables consume them. The usual simulation chain has separate jobs.
    traversing = process.shiftTraversingMuon_step
    paths = [path for path in process.schedule if path is not traversing]
    nano_index = paths.index(process.nanoAOD_step)
    paths.insert(nano_index, traversing)
    process.schedule = cms.Schedule(*paths)
    # This is restricted intermediate storage, not a blinded analysis delivery.
    # Keep only SHIFT tables; ordinary histograms and event exports are forbidden.
    process.NANOAODoutput.outputCommands = cms.untracked.vstring(
        "drop *",
        "keep nanoaodFlatTable_shiftMuonTable_*_*",
        "keep nanoaodFlatTable_shiftMuonSegmentsTable_*_*",
    )
    process.MessageLogger.cerr.threshold = cms.untracked.string("ERROR")
    return process


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--input", action="append", required=True, dest="inputs")
    parser.add_argument("--certification", type=Path, required=True)
    parser.add_argument("--global-tag", required=True)
    parser.add_argument("--era", required=True)
    parser.add_argument("--max-events", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reconstruction-profile", type=Path, required=True)
    parser.add_argument("--profile-sha256", required=True)
    parser.add_argument("--geometry-file-tag", choices=["Geometry_133DD4hep2_Extended2023_mc"],
                        help="supply only the qualified ideal geometry record missing from the data GlobalTag")
    args = parser.parse_args()
    certification = json.loads(args.certification.read_text())
    validate_request(args.dataset, args.inputs, args.global_tag, args.era,
                     args.max_events, certification)
    validate_profile(args.reconstruction_profile, args.profile_sha256)
    if not os.environ.get("CMSSW_BASE") or shutil.which("cmsDriver.py") is None:
        parser.error("initialize the existing CMSSW runtime before preparing data reconstruction")
    os.umask(0o077)
    directory = args.output_dir.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    helper = directory / "data_reconstruction_helpers.py"
    shutil.copyfile(Path(__file__).resolve(), helper)
    frozen_certification = directory / "certification.json"
    shutil.copyfile(args.certification, frozen_certification)
    frozen_profile = directory / "reconstruction_profile.py"
    shutil.copyfile(args.reconstruction_profile, frozen_profile)
    config = directory / "reconstruct_cfg.py"
    output = directory / "restricted_nano.root"
    customisation = (
        "import runpy; process = runpy.run_path("
        + repr(str(helper)) + ")['customise_data'](process, "
        + repr(str(frozen_certification)) + ", " + repr(str(frozen_profile))
        + ", " + repr(args.profile_sha256) + ", " + repr(args.geometry_file_tag) + ")"
    )
    command = [
        "cmsDriver.py", "shift_data", "--data", "--step", "RAW2DIGI,L1Reco,RECO,PAT,NANO",
        "--conditions", args.global_tag, "--era", args.era,
        "--geometry", "DB:Extended", "--datatier", "NANOAOD", "--eventcontent", "NANOAOD",
        "--filein", ",".join(args.inputs), "--fileout", "file:" + str(output),
        "--python_filename", str(config), "--nThreads", "1", "--nStreams", "1",
        "--no_exec", "-n", str(args.max_events), "--customise_commands", customisation,
    ]
    # Configuration diagnostics remain inside the restricted directory too.
    with (directory / "preparation.log").open("x") as log:
        subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True)
    proof = {
        "schema_version": 1, "stage": "configuration_prepared_only",
        "dataset": args.dataset, "input_files": args.inputs, "era": args.era,
        "global_tag": args.global_tag, "max_events": args.max_events,
        "cmssw_version": os.environ.get("CMSSW_VERSION"),
        "cmssw_base": os.environ["CMSSW_BASE"],
        "certification_sha256": hashlib.sha256(frozen_certification.read_bytes()).hexdigest(),
        "helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "duplicate_policy": "checkAllFilesOpened; fixed file order; global publication dedup required",
        "physics_output": "restricted intermediate only; no analysis publication",
        "input_kind": "collision-data",
        "reconstruction_scope": "explicit hash-pinned reconstruction profile; full data integration required",
        "reconstruction_profile": str(args.reconstruction_profile.resolve()),
        "reconstruction_profile_sha256": args.profile_sha256,
        "geometry_file_tag": args.geometry_file_tag,
        "command": command,
    }
    (directory / "preparation.json").write_text(json.dumps(proof, indent=2) + "\n")
    print("Prepared restricted data reconstruction configuration:", config)


if __name__ == "__main__":
    main()
