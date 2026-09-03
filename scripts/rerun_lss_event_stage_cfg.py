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
if hasattr(process.source, "eventsToProcess"):
    del process.source.eventsToProcess
for name in process.outputModules_():
    getattr(process, name).fileName = cms.untracked.string("file:" + output_file)

if os.environ.get("SHIFT_RERUN_ADD_LSS", "0") == "1":
    from PhysicsTools.ShiftLssGeometry.shiftLssExternalGeometry_cff import (
        customiseShiftLssExternalGeometry,
    )
    from PhysicsTools.ShiftMuonSegments.shiftLssIr1AtlasProxy_cff import (
        shiftLssIr1AtlasProxyFieldElements,
    )
    from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import (
        customiseShiftLssTransport,
    )

    model_origin = (0.0, 0.0, 0.0)
    model_to_cms = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    if not hasattr(process, "DDDetectorESProducerFromDB"):
        process.load("Configuration.Geometry.GeometryDD4hepSimDB_cff")
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
    process = customiseShiftLssTransport(
        process,
        fieldElements=shiftLssIr1AtlasProxyFieldElements(
            modelOriginCm=model_origin,
            modelToCms=model_to_cms,
            fieldScale=1.0,
        ),
        materialBoundaryAbsZCm=14800.0,
        geant4eMaximumPathLengthCm=20000.0,
    )
