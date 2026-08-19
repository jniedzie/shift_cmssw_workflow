#!/usr/bin/env python3

"""Persist the conditions-resolved L1 menu as standard L1uGTTree aliases."""

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
    "zero_bias_l1_menu.root",
    VarParsing.multiplicity.singleton,
    VarParsing.varType.string,
    "ROOT output containing the L1uGTTree aliases",
)
options.register(
    "globalTag",
    "auto:run3_data_prompt",
    VarParsing.multiplicity.singleton,
    VarParsing.varType.string,
    "conditions GlobalTag used to resolve the run-dependent L1 menu",
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

process = cms.Process("SHIFTZBMENU", eras[options.collisionYear])
process.load("FWCore.MessageService.MessageLogger_cfi")
process.load("Configuration.StandardSequences.FrontierConditions_GlobalTag_cff")
from Configuration.AlCa.GlobalTag import GlobalTag

process.GlobalTag = GlobalTag(process.GlobalTag, options.globalTag, "")
process.maxEvents = cms.untracked.PSet(input=cms.untracked.int32(1))
process.source = cms.Source("PoolSource", fileNames=cms.untracked.vstring(options.inputFiles))

process.load("EventFilter.L1TRawToDigi.gtStage2Digis_cfi")
process.load("L1Trigger.L1TNtuples.l1uGTTree_cfi")
process.TFileService = cms.Service("TFileService", fileName=cms.string(options.outputFile))
process.path = cms.Path(process.gtStage2Digis + process.l1uGTTree)
