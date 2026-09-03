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

# Use the same temporary model placement as the geometry overview.  It is a
# software test of the visualization chain, not an approved CMS-side placement.
from PhysicsTools.ShiftLssGeometry.shiftLssExternalGeometry_cff import customiseShiftLssExternalGeometry
from PhysicsTools.ShiftMuonSegments.shiftLssIr1AtlasProxy_cff import shiftLssIr1AtlasProxyFieldElements
from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customiseShiftLssMagneticField

model_origin = (0.0, 0.0, 0.0)
model_to_cms = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
process = customiseShiftLssExternalGeometry(
    process,
    gdmlFile=("PhysicsTools/ShiftLssGeometry/data/ir1_atlas_proxy/"
              "lhc_ir1_atlas_proxy_bounded.gdml"),
    artifactOriginInModelCm=(0.0, 4299.5, 14575.200000105498),
    modelOriginCm=model_origin,
    modelToCms=model_to_cms,
    minimumAbsZCm=1100.0,
    checkOverlaps=False,
)
process = customiseShiftLssMagneticField(
    process,
    fieldElements=shiftLssIr1AtlasProxyFieldElements(
        modelOriginCm=model_origin,
        modelToCms=model_to_cms,
        fieldScale=1.0,
    ),
)
process.g4SimHits.SteppingAction.TracePrimaryTracksForVisualization = cms.untracked.bool(True)
process.g4SimHits.Generator.DebugMuonPrimaries = cms.untracked.bool(True)
process.g4SimHits.TrackingAction.DebugMuonPrimaryFates = cms.untracked.bool(True)

# This is a diagnostic rerun.  Never overwrite the campaign output.
output_path = os.environ.get("SHIFT_TRACE_OUTPUT", "/tmp/shift_lss_event_trace.root")
for name in process.outputModules_():
    getattr(process, name).fileName = cms.untracked.string("file:" + output_path)
