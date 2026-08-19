#!/usr/bin/env python3

"""Unpack only the uGT decision record needed by the SHIFT trigger study."""

import FWCore.ParameterSet.Config as cms
from FWCore.ParameterSet.VarParsing import VarParsing
from Configuration.Eras.Era_Run3_cff import Run3
from Configuration.Eras.Era_Run3_2023_cff import Run3_2023
from Configuration.Eras.Era_Run3_2024_cff import Run3_2024


options = VarParsing()
options.register(
    "inputFiles",
    [],
    VarParsing.multiplicity.list,
    VarParsing.varType.string,
    "ZeroBias RAW input files",
)
options.register(
    "outputFile",
    "zero_bias_ugt.root",
    VarParsing.multiplicity.singleton,
    VarParsing.varType.string,
    "small EDM output containing uGT and HLT decisions",
)
options.register(
    "maxEvents",
    100,
    VarParsing.multiplicity.singleton,
    VarParsing.varType.int,
    "number of events to unpack",
)
options.register(
    "collisionYear",
    "2023",
    VarParsing.multiplicity.singleton,
    VarParsing.varType.string,
    "collision year selecting the matching CMSSW era",
)
options.parseArguments()

if not options.inputFiles:
    raise RuntimeError("Pass at least one ZeroBias RAW file with inputFiles=...")

eras = {"2022": Run3, "2023": Run3_2023, "2024": Run3_2024}
if options.collisionYear not in eras:
    raise RuntimeError("collisionYear must be 2022, 2023, or 2024")

process = cms.Process("SHIFTZB", eras[options.collisionYear])
process.load("FWCore.MessageService.MessageLogger_cfi")
process.MessageLogger.cerr.FwkReport.reportEvery = 100

process.maxEvents = cms.untracked.PSet(
    input=cms.untracked.int32(options.maxEvents),
)
process.source = cms.Source(
    "PoolSource",
    fileNames=cms.untracked.vstring(options.inputFiles),
)

# This is the standard Stage-2 uGT RAW unpacker, restricted to FED 1404 by its
# central cfi.  No L1 re-emulation is performed: these are the decisions that
# were stored in data, including their initial, post-prescale and final states.
process.load("EventFilter.L1TRawToDigi.gtStage2Digis_cfi")
process.unpack = cms.Path(process.gtStage2Digis)

process.output = cms.OutputModule(
    "PoolOutputModule",
    fileName=cms.untracked.string(options.outputFile),
    outputCommands=cms.untracked.vstring(
        "drop *",
        "keep GlobalAlgBlkBXVector_gtStage2Digis__SHIFTZB",
        "keep GlobalExtBlkBXVector_gtStage2Digis__SHIFTZB",
        "keep edmTriggerResults_TriggerResults__HLT",
    ),
)
process.out = cms.EndPath(process.output)
