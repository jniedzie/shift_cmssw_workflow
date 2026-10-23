# Code style: human-code.
"""Import an audited meson signal graph using the common SHIFT GEN chain."""
import json
from pathlib import Path

import FWCore.ParameterSet.Config as cms
from FWCore.ParameterSet.VarParsing import VarParsing
from Configuration.Eras.Era_Run3_2023_cff import Run3_2023

options = VarParsing()
options.register('pilotDirectory', '', VarParsing.multiplicity.singleton,
                 VarParsing.varType.string, 'Frozen meson pilot directory')
options.parseArguments()
out = Path(options.pilotDirectory).resolve()
data = json.loads((out / 'contract.json').read_text())
if data['production_mechanism'] not in ('eta-etaprime-radiative', 'meson-radiative'):
    raise ValueError('Expected a separately normalized meson signal source')

process = cms.Process('SHIFTBSMGEN', Run3_2023)
process.load('Configuration.StandardSequences.Services_cff')
process.load('SimGeneral.HepPDTESSource.pythiapdt_cfi')
process.maxEvents = cms.untracked.PSet(input=cms.untracked.int32(data['requested_events']))
process.source = cms.Source('MCFileSource',
    fileNames=cms.untracked.vstring('file:' + str(out / 'signal.hepmc')),
    firstRun=cms.untracked.uint32(data['run_number']),
    # The legacy reader does not supply this current source-base default.
    firstLuminosityBlockForEachRun=cms.untracked.VLuminosityBlockID(),
    firstLuminosityBlock=cms.untracked.uint32(1), firstEvent=cms.untracked.uint64(1))
process.options = cms.untracked.PSet(numberOfThreads=cms.untracked.uint32(1),
    numberOfStreams=cms.untracked.uint32(1), wantSummary=cms.untracked.bool(True))

# Keep the usual generator-weight label for the unchanged detector replay.
# The source's actual Pythia cross sections remain in the full trial ledger.
process.generator = cms.EDAlias(source=cms.VPSet(
    cms.PSet(type=cms.string('GenEventInfoProduct'),
             fromProductInstance=cms.string('generator'), toProductInstance=cms.string('')),
    cms.PSet(type=cms.string('edmHepMCProduct'),
             fromProductInstance=cms.string('generator'), toProductInstance=cms.string('unsmeared'))))
process.darkPhotonHepMC = cms.EDProducer('ShiftDarkPhotonHepMCProducer',
    src=cms.InputTag('source', 'generator'), massGeV=cms.double(data['mass_gev']),
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
process.shiftEventTime = shiftEventTime.clone(src=cms.InputTag('shiftMuonDecays'))
process.load('GeneratorInterface.Core.generatorSmeared_cfi')
process.generatorSmeared.currentTag = cms.untracked.InputTag('shiftEventTime')
process.load('PhysicsTools.HepMCCandAlgos.genParticles_cfi')
for offset, label in ((1, 'VtxSmeared'), (77, 'shiftMuonDecays')):
    setattr(process.RandomNumberGeneratorService, label, cms.PSet(
        initialSeed=cms.untracked.uint32(data['seed'] + offset),
        engineName=cms.untracked.string('HepJamesRandom')))
process.gen = cms.Path(process.darkPhotonHepMC + process.VtxSmeared
    + process.shiftMuonDecays + process.shiftEventTime + process.generatorSmeared + process.genParticles)
process.output = cms.OutputModule('PoolOutputModule',
    fileName=cms.untracked.string(str(out / 'gen.root')),
    outputCommands=cms.untracked.vstring('drop *', 'keep *_generator_*_*',
        'keep *_darkPhotonHepMC_*_*', 'keep *_VtxSmeared_*_*',
        'keep *_shiftMuonDecays_*_*', 'keep *_shiftEventTime_*_*',
        'keep *_generatorSmeared_*_*', 'keep *_genParticles_*_*'),
    SelectEvents=cms.untracked.PSet(SelectEvents=cms.vstring('gen')))
process.write = cms.EndPath(process.output)
process.schedule = cms.Schedule(process.gen, process.write)
if 'source_placement' in data:
    from shift_source_placement import ASSUMED_SCOPE, configure_source_placement
    configure_source_placement(process, None, 'VtxSmeared', test_scope=ASSUMED_SCOPE,
        event_positions=data['source_placement']['event_positions'])
    process.shiftMuonDecays.src = cms.InputTag('shiftSourcePlacement')
    process.gen.replace(process.VtxSmeared, process.VtxSmeared + process.shiftSourcePlacement)
    process.output.outputCommands.append('keep *_shiftSourcePlacement_*_*')
process.MessageLogger.cerr.FwkReport.reportEvery = 20
(out / 'resolved_cfg.py').write_text(process.dumpPython())
