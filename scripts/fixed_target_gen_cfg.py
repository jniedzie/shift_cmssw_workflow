"""Small GEN-only pilot using CMS common settings, CP5 and physical source time.

Run via run_fixed_target_gen.py to preserve config, provenance and validation.
No Geant4, detector conditions, pileup, trigger or reconstruction is scheduled.
"""

import json
import math
import os
import runpy
from pathlib import Path
import sys

import FWCore.ParameterSet.Config as cms
from FWCore.ParameterSet.VarParsing import VarParsing
from Configuration.Eras.Era_Run3_2023_cff import Run3_2023
from Configuration.Generator.Pythia8CommonSettings_cfi import pythia8CommonSettingsBlock
from Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi import pythia8CP5SettingsBlock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixed_target_generation import beam_settings, process_settings, canonical_settings

options = VarParsing()
for name, default, kind in (
    ("sample", "qcd", VarParsing.varType.string),
    ("lower", 1., VarParsing.varType.float),
    ("upper", 5., VarParsing.varType.float),
    ("beamEnergy", 6800., VarParsing.varType.float),
    ("sourceZmm", 148000., VarParsing.varType.float),
    ("sourceSigmaZmm", 500., VarParsing.varType.float),
    ("seed", 13579, VarParsing.varType.int),
    ("maxEvents", 20, VarParsing.varType.int),
    ("outputDir", "", VarParsing.varType.string),
):
    options.register(name, default, VarParsing.multiplicity.singleton, kind, name)
options.parseArguments()
if not options.outputDir or not 1 <= options.maxEvents <= 10000 or not 1 <= options.seed <= 899999999:
    raise ValueError("Require outputDir, 1..10000 events and seed 1..899999999")
if not math.isfinite(options.sourceZmm) or options.sourceZmm <= 0:
    raise ValueError("Beam-B pilot requires a positive source z")
if not math.isfinite(options.sourceSigmaZmm) or options.sourceSigmaZmm < 0:
    raise ValueError("Require a finite nonnegative source width")
out = Path(options.outputDir).resolve()
settings = process_settings(options.sample, options.lower, options.upper)
beams = beam_settings(options.beamEnergy)

process = cms.Process("SHIFTGEN", Run3_2023)
process.load("Configuration.StandardSequences.Services_cff")
process.load("SimGeneral.HepPDTESSource.pythiapdt_cfi")
process.maxEvents = cms.untracked.PSet(input=cms.untracked.int32(options.maxEvents))
process.source = cms.Source("EmptySource", firstRun=cms.untracked.uint32(options.seed))
process.options = cms.untracked.PSet(numberOfThreads=cms.untracked.uint32(1),
                                    numberOfStreams=cms.untracked.uint32(1),
                                    wantSummary=cms.untracked.bool(True))
process.generator = cms.EDFilter(
    "Pythia8GeneratorFilter",
    pythiaPylistVerbosity=cms.untracked.int32(0),
    pythiaHepMCVerbosity=cms.untracked.bool(False),
    maxEventsToPrint=cms.untracked.int32(0),
    # Actual beam energies are set explicitly by frameType=2; this CMS field
    # is a nominal collider-era label, NOT the fixed-target sqrt(s).
    comEnergy=cms.double(13600.),
    PythiaParameters=cms.PSet(
        pythia8CommonSettingsBlock, pythia8CP5SettingsBlock,
        processParameters=cms.vstring(settings),
        fixedTargetParameters=cms.vstring(beams),
        parameterSets=cms.vstring("pythia8CommonSettings", "pythia8CP5Settings",
                                 "processParameters", "fixedTargetParameters")),
)
process.RandomNumberGeneratorService.generator = cms.PSet(
    initialSeed=cms.untracked.uint32(options.seed), engineName=cms.untracked.string("HepJamesRandom"))
process.RandomNumberGeneratorService.VtxSmeared = cms.PSet(
    initialSeed=cms.untracked.uint32(options.seed + 1), engineName=cms.untracked.string("HepJamesRandom"))
process.load("IOMC.EventVertexGenerators.VtxSmearedGauss_cfi")
process.VtxSmeared.MeanZ = options.sourceZmm / 10.  # module uses cm
process.VtxSmeared.SigmaZ = options.sourceSigmaZmm / 10.
process.VtxSmeared.SigmaX = 0.  # explicitly provisional on-axis source
process.VtxSmeared.SigmaY = 0.
process.VtxSmeared.TimeOffset = 0.
process.load("IOMC.ShiftEventTiming.shiftEventTime_cfi")
process.load("GeneratorInterface.Core.generatorSmeared_cfi")
process.generatorSmeared.currentTag = cms.untracked.InputTag("shiftEventTime")
process.load("PhysicsTools.HepMCCandAlgos.genParticles_cfi")
process.gen = cms.Path(process.generator + process.VtxSmeared + process.shiftEventTime
                       + process.generatorSmeared + process.genParticles)
process.genXsecAnalyzer = cms.EDAnalyzer("GenXSecAnalyzer")
process.xsec = cms.EndPath(process.genXsecAnalyzer)
process.output = cms.OutputModule("PoolOutputModule",
    fileName=cms.untracked.string(str(out / "gen.root")),
    outputCommands=cms.untracked.vstring("drop *", "keep *_generator_*_*",
        "keep *_VtxSmeared_*_*", "keep *_shiftEventTime_*_*",
        "keep *_generatorSmeared_*_*", "keep *_genParticles_*_*"),
    SelectEvents=cms.untracked.PSet(SelectEvents=cms.vstring("gen")))
process.write = cms.EndPath(process.output)
process.schedule = cms.Schedule(process.gen, process.xsec, process.write)
process.MessageLogger.cerr.FwkReport.reportEvery = 100

# Produced by configuration execution, before any events. Runtime validation
# and physics readiness have separate flags in the runner's manifest.
contract = dict(schema="shift-gen-pilot-v1", year=2023, sample=options.sample,
    bin_variable="mHat" if options.sample == "dy" else "pTHat",
    lower=options.lower, upper=options.upper, requested_events=options.maxEvents,
    seed=options.seed, beam_energy_GeV=options.beamEnergy,
    source_z_mm=options.sourceZmm, source_sigma_z_mm=options.sourceSigmaZmm,
    source_model="provisional on-axis stationary proton target; no nuclear model",
    cmssw=os.environ.get("CMSSW_VERSION"),
    common=list(pythia8CommonSettingsBlock.pythia8CommonSettings),
    cp5=list(pythia8CP5SettingsBlock.pythia8CP5Settings),
    process_settings=settings, beam_settings=beams,
    decay_policy="CMS lifetime cutoff; long-lived particles retained for transport",
    forced_decay={"qcd": None, "jpsi": "443 -> 13 -13",
                  "chic": "443 -> 13 -13", "psi2s": "443 -> 13 -13",
                  "dy": "23 -> 13 -13"}[options.sample],
    filtering="none", physics_valid=False, detector_simulated=False,
    normalization_ready=False, overlap_audit_complete=False)
reference = Path("/cvmfs/cms.cern.ch/el8_amd64_gcc11/cms/cmssw/CMSSW_13_0_13/src/Configuration/Generator/python")
contract["central_process_request"] = None
contract["reference_release"] = "CMSSW_13_0_13 (comparison only; not a verified process-specific campaign)"
if reference.is_dir():
    old_common = list(runpy.run_path(str(reference / "Pythia8CommonSettings_cfi.py"))[
        "pythia8CommonSettingsBlock"].pythia8CommonSettings)
    old_cp5 = list(runpy.run_path(str(reference / "MCTunes2017/PythiaCP5Settings_cfi.py"))[
        "pythia8CP5SettingsBlock"].pythia8CP5Settings)
    current, previous = canonical_settings(contract["common"]), canonical_settings(old_common)
    contract["reference_comparison"] = dict(path=str(reference), common=old_common, cp5=old_cp5,
        cp5_equal=canonical_settings(contract["cp5"]) == canonical_settings(old_cp5),
        common_only_current=[x for x in current if x not in previous],
        common_only_reference=[x for x in previous if x not in current])
else:
    contract["reference_comparison"] = None
(out / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
(out / "resolved_cfg.py").write_text(process.dumpPython())
