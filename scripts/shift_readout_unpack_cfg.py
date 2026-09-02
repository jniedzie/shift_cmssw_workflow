#!/usr/bin/env python3

"""Unpack a simulated triggered readout for SHIFT capture closure.

The input must be a Step-2 DIGI-RAW file produced with the standard Run-3
digitizers and packers.  This job does not re-emulate detector electronics or
trigger decisions.  It only unpacks ``rawDataCollector`` and persists the
signal truth, pre-pack digis/sim-links, and post-unpack digis needed to measure
which parts of a SHIFT muon were stored in this one triggered CMS event.
"""

import FWCore.ParameterSet.Config as cms
from Configuration.Eras.Era_Run3_cff import Run3
from Configuration.Eras.Era_Run3_2023_cff import Run3_2023
from Configuration.Eras.Era_Run3_2024_cff import Run3_2024
from FWCore.ParameterSet.VarParsing import VarParsing


options = VarParsing()
options.register(
    "inputFiles",
    [],
    VarParsing.multiplicity.list,
    VarParsing.varType.string,
    "Step-2 GEN-SIM-RAW input files",
)
options.register(
    "outputFile",
    "shift_readout_unpacked.root",
    VarParsing.multiplicity.singleton,
    VarParsing.varType.string,
    "EDM output containing pre-pack and post-unpack muon-detector products",
)
options.register(
    "maxEvents",
    10,
    VarParsing.multiplicity.singleton,
    VarParsing.varType.int,
    "number of triggered readout events to unpack",
)
options.register(
    "collisionYear",
    "2023",
    VarParsing.multiplicity.singleton,
    VarParsing.varType.string,
    "collision year selecting the matching CMSSW era",
)
options.register(
    "globalTag",
    "auto:phase1_2023_realistic",
    VarParsing.multiplicity.singleton,
    VarParsing.varType.string,
    "the same simulation GlobalTag used to produce the Step-2 input",
)
options.parseArguments()

if not options.inputFiles:
    raise RuntimeError("Pass at least one Step-2 file with inputFiles=...")

eras = {"2022": Run3, "2023": Run3_2023, "2024": Run3_2024}
if options.collisionYear not in eras:
    raise RuntimeError("collisionYear must be 2022, 2023, or 2024")

process = cms.Process("SHIFTREADOUT", eras[options.collisionYear])
process.load("FWCore.MessageService.MessageLogger_cfi")
process.load("Configuration.StandardSequences.FrontierConditions_GlobalTag_cff")
process.load("Configuration.StandardSequences.RawToDigi_cff")

from Configuration.AlCa.GlobalTag import GlobalTag

process.GlobalTag = GlobalTag(process.GlobalTag, options.globalTag, "")
process.MessageLogger.cerr.FwkReport.reportEvery = 10
process.maxEvents = cms.untracked.PSet(input=cms.untracked.int32(options.maxEvents))
process.source = cms.Source(
    "PoolSource",
    fileNames=cms.untracked.vstring(options.inputFiles),
)

# Standard RAW-to-digi modules read the already packed detector FED payloads.
# No digitizer, primitive emulator, L1 emulator, or packer is rerun here.
process.unpack = cms.Path(process.RawToDigi)

process.output = cms.OutputModule(
    "PoolOutputModule",
    fileName=cms.untracked.string(options.outputFile),
    outputCommands=cms.untracked.vstring(
        "drop *",
        "keep FEDRawDataCollection_rawDataCollector__*",
        "keep SimTracks_g4SimHits__*",
        "keep SimVertexs_g4SimHits__*",
        "keep PSimHits_g4SimHits_MuonDTHits_*",
        "keep PSimHits_g4SimHits_MuonCSCHits_*",
        "keep PSimHits_g4SimHits_MuonRPCHits_*",
        "keep PSimHits_g4SimHits_MuonGEMHits_*",
        "keep *_shiftEventTime_*_*",
        "keep *_shiftSimHitTime_*_*",
        "keep *_simMuonDTDigis_*_*",
        "keep *_simMuonCSCDigis_*_*",
        "keep *_simMuonRPCDigis_*_*",
        "keep *_simMuonGEMDigis_*_*",
        "keep *_simDtTriggerPrimitiveDigis_*_*",
        "keep *_simCscTriggerPrimitiveDigis_*_*",
        "keep *_simMuonGEMPadDigis_*_*",
        "keep *_simMuonGEMPadDigiClusters_*_*",
        "keep *_simBmtfDigis_*_*",
        "keep *_simKBmtfDigis_*_*",
        "keep *_simOmtfDigis_*_*",
        "keep *_simEmtfDigis_*_*",
        "keep *_simEmtfShowers_*_*",
        "keep *_simGmtStage2Digis_*_*",
        "keep *_muonDTDigis_*_SHIFTREADOUT",
        "keep *_muonCSCDigis_*_SHIFTREADOUT",
        "keep *_muonRPCDigis_*_SHIFTREADOUT",
        "keep *_muonGEMDigis_*_SHIFTREADOUT",
    ),
)
process.out = cms.EndPath(process.output)
