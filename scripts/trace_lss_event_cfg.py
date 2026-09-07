"""Run the first N events of an existing Step-1 config and log Geant4 primary paths."""

import os
import runpy

import FWCore.ParameterSet.Config as cms


base_config = os.environ.get("SHIFT_TRACE_BASE_CONFIG")
if not base_config:
    raise RuntimeError("set SHIFT_TRACE_BASE_CONFIG to the original Step-1 config")

namespace = runpy.run_path(base_config)
process = namespace["process"]
process.maxEvents.input = int(os.environ.get("SHIFT_TRACE_MAX_EVENTS", "3"))

# The base campaign config already owns geometry and field setup.  Keeping it
# intact is essential for a paired rerun; the old fallback is retained only
# for standalone visualization configs that do not have g4SimHits yet.
if not hasattr(process, "g4SimHits"):
    raise RuntimeError("base Step-1 config has no g4SimHits")
process.g4SimHits.SteppingAction.TracePrimaryTracksForVisualization = cms.untracked.bool(True)
process.g4SimHits.Generator.DebugMuonPrimaries = cms.untracked.bool(True)
process.g4SimHits.TrackingAction.DebugMuonPrimaryFates = cms.untracked.bool(True)
process.g4SimHits.SteppingAction.DebugMuonTracking = cms.untracked.bool(True)

# This is a diagnostic rerun.  Never overwrite the campaign output.
output_path = os.environ.get("SHIFT_TRACE_OUTPUT", "/tmp/shift_lss_event_trace.root")
for name in process.outputModules_():
    getattr(process, name).fileName = cms.untracked.string("file:" + output_path)
