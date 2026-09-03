"""Rerun an archived Step-1 config and log primary Geant4 paths."""

import os
import runpy

import FWCore.ParameterSet.Config as cms


base_config = os.environ.get("SHIFT_TRACE_BASE_CONFIG")
if not base_config:
    raise RuntimeError("set SHIFT_TRACE_BASE_CONFIG to the original Step-1 config")

process = runpy.run_path(base_config)["process"]
process.maxEvents.input = int(os.environ.get("SHIFT_TRACE_MAX_EVENTS", "3"))
process.g4SimHits.SteppingAction.TracePrimaryTracksForVisualization = cms.untracked.bool(True)
process.g4SimHits.Generator.DebugMuonPrimaries = cms.untracked.bool(True)
process.g4SimHits.TrackingAction.DebugMuonPrimaryFates = cms.untracked.bool(True)

# This is a diagnostic rerun. Never overwrite the campaign output.
output_path = os.environ.get("SHIFT_TRACE_OUTPUT", "/tmp/shift_baseline_event_trace.root")
for name in process.outputModules_():
    getattr(process, name).fileName = cms.untracked.string("file:" + output_path)
