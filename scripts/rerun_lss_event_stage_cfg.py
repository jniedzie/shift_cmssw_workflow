"""Redirect one archived Step-2/3/4 config to the selected LSS event files."""

import os
import runpy

import FWCore.ParameterSet.Config as cms


base_config = os.environ.get("SHIFT_RERUN_BASE_CONFIG")
input_file = os.environ.get("SHIFT_RERUN_INPUT")
output_file = os.environ.get("SHIFT_RERUN_OUTPUT")
if not base_config or not input_file or not output_file:
    raise RuntimeError("SHIFT_RERUN_BASE_CONFIG, SHIFT_RERUN_INPUT and SHIFT_RERUN_OUTPUT are required")

process = runpy.run_path(base_config)["process"]
process.maxEvents.input = int(os.environ.get("SHIFT_RERUN_MAX_EVENTS", "3"))
process.source.fileNames = cms.untracked.vstring("file:" + input_file)
if hasattr(process.source, "secondaryFileNames"):
    process.source.secondaryFileNames = cms.untracked.vstring()
for name in process.outputModules_():
    getattr(process, name).fileName = cms.untracked.string("file:" + output_file)
