"""Frozen GEN-only dark-photon configuration, used by run_dark_photon_gen.py."""
import json
from pathlib import Path
import sys

import FWCore.ParameterSet.Config as cms
from FWCore.ParameterSet.VarParsing import VarParsing
from Configuration.Eras.Era_Run3_2023_cff import Run3_2023

options = VarParsing()
options.register('pilotDirectory', '', VarParsing.multiplicity.singleton,
                 VarParsing.varType.string, 'Frozen pilot directory')
options.parseArguments()
out = Path(options.pilotDirectory).resolve()
data = json.loads((out / 'contract.json').read_text())
if data['schema'] != 'shift-dark-photon-gen-v1':
    raise ValueError('Wrong signal contract')
process = cms.Process('SHIFTBSMGEN', Run3_2023)
process.load('Configuration.StandardSequences.Services_cff')
process.load('SimGeneral.HepPDTESSource.pythiapdt_cfi')
process.maxEvents = cms.untracked.PSet(input=cms.untracked.int32(data['requested_events']))
process.source = cms.Source('EmptySource', firstRun=cms.untracked.uint32(data['run_number']))
process.options = cms.untracked.PSet(numberOfThreads=cms.untracked.uint32(1),
    numberOfStreams=cms.untracked.uint32(1), wantSummary=cms.untracked.bool(True))
process.generator = cms.EDFilter('Pythia8GeneratorFilter',
    pythiaPylistVerbosity=cms.untracked.int32(0), pythiaHepMCVerbosity=cms.untracked.bool(False),
    maxEventsToPrint=cms.untracked.int32(0), comEnergy=cms.double(13600.),
    PythiaParameters=cms.PSet(
        pythia8CommonSettings=cms.vstring(data['common']),
        pythia8CP5Settings=cms.vstring(data['cp5']),
        processParameters=cms.vstring(data['process_settings']),
        fixedTargetParameters=cms.vstring(data['beam_settings']),
        parameterSets=cms.vstring('pythia8CommonSettings', 'pythia8CP5Settings',
                                 'processParameters', 'fixedTargetParameters')))
process.darkPhotonHepMC = cms.EDProducer('ShiftDarkPhotonHepMCProducer',
    src=cms.InputTag('generator', 'unsmeared'), massGeV=cms.double(data['mass_gev']),
    generatedMassMinGeV=cms.double(data['generated_mass_support_gev'][0]),
    generatedMassMaxGeV=cms.double(data['generated_mass_support_gev'][1]))
process.load('IOMC.EventVertexGenerators.VtxSmearedGauss_cfi')
process.VtxSmeared.src = cms.InputTag('darkPhotonHepMC')
process.VtxSmeared.MeanZ = data['source_z_mm'] / 10.
process.VtxSmeared.SigmaZ = data['source_sigma_z_mm'] / 10.
process.VtxSmeared.SigmaX = 0.
process.VtxSmeared.SigmaY = 0.
process.VtxSmeared.TimeOffset = 0.
process.shiftMuonDecays = cms.EDProducer('ShiftMuonDecayProducer',
    src=cms.InputTag('VtxSmeared'), decayCylinderRadiusMm=cms.double(8000.),
    decayCylinderHalfLengthMm=cms.double(151000.))
from IOMC.ShiftEventTiming.shiftEventTime_cfi import shiftEventTime
# The nominal common time translation commutes with proper flight decays.
# Apply it once to the complete graph and retain canonical timing products.
process.shiftEventTime = shiftEventTime.clone(src=cms.InputTag('shiftMuonDecays'))
process.load('GeneratorInterface.Core.generatorSmeared_cfi')
process.generatorSmeared.currentTag = cms.untracked.InputTag('shiftEventTime')
process.load('PhysicsTools.HepMCCandAlgos.genParticles_cfi')
for offset, label in ((0, 'generator'), (1, 'VtxSmeared'), (77, 'shiftMuonDecays')):
    setattr(process.RandomNumberGeneratorService, label, cms.PSet(
        initialSeed=cms.untracked.uint32(data['seed'] + offset),
        engineName=cms.untracked.string('HepJamesRandom')))
process.gen = cms.Path(process.generator + process.darkPhotonHepMC + process.VtxSmeared
    + process.shiftMuonDecays + process.shiftEventTime + process.generatorSmeared + process.genParticles)
process.genXsecAnalyzer = cms.EDAnalyzer('GenXSecAnalyzer')
process.xsec = cms.EndPath(process.genXsecAnalyzer)
process.output = cms.OutputModule('PoolOutputModule',
    fileName=cms.untracked.string(str(out / 'gen.root')),
    outputCommands=cms.untracked.vstring('drop *', 'keep *_generator_*_*',
        'keep *_darkPhotonHepMC_*_*', 'keep *_VtxSmeared_*_*',
        'keep *_shiftMuonDecays_*_*', 'keep *_shiftEventTime_*_*',
        'keep *_generatorSmeared_*_*', 'keep *_genParticles_*_*'),
    SelectEvents=cms.untracked.PSet(SelectEvents=cms.vstring('gen')))
process.write = cms.EndPath(process.output)
process.schedule = cms.Schedule(process.gen, process.xsec, process.write)
process.MessageLogger.cerr.FwkReport.reportEvery = 20
(out / 'resolved_cfg.py').write_text(process.dumpPython())
