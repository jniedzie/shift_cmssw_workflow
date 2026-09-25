"""Complementary SoftQCD/MPI event class without direct hard J/psi.

The custom hook partitions Pythia's one eikonalized non-diffractive phase
space.  Feed-down, nonprompt and hadronization J/psi remain in this class;
only hard MPI states that directly become J/psi belong to the signal class.
"""
import FWCore.ParameterSet.Config as cms
from Configuration.Generator.Pythia8CommonSettings_cfi import pythia8CommonSettingsBlock
from Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi import pythia8CP5SettingsBlock

generator = cms.EDFilter(
    "Pythia8GeneratorFilter",
    pythiaPylistVerbosity=cms.untracked.int32(0),
    pythiaHepMCVerbosity=cms.untracked.bool(False),
    maxEventsToPrint=cms.untracked.int32(0),
    comEnergy=cms.double(13600.),
    UserCustomization=cms.VPSet(cms.PSet(
        pluginName=cms.string("ShiftMpiEventClassHook"),
        eventClass=cms.string("qcd"),
        pTHatMin=cms.double(0.),
        pTHatMax=cms.double(1.),
    )),
    PythiaParameters=cms.PSet(
        pythia8CommonSettingsBlock,
        pythia8CP5SettingsBlock,
        processParameters=cms.vstring(
            "SoftQCD:all = off",
            "SoftQCD:nonDiffractive = on",
            "HardQCD:all = off",
            "Charmonium:all = off",
            "Bottomonium:all = off",
            # Level 3 is common to both complementary event classes.  It
            # includes Pythia's automatically MPI-damped onium channels.
            "MultipartonInteractions:processLevel = 3",
            "Beams:frameType = 2",
            "Beams:idA = 2212",
            "Beams:eA = 0.",
            "Beams:idB = 2212",
            "Beams:eB = 6800.",
            "Beams:allowVertexSpread = on",
            "Beams:offsetVertexZ = 148000.",
            "Beams:sigmaVertexZ = 500.",
        ),
        parameterSets=cms.vstring(
            "pythia8CommonSettings", "pythia8CP5Settings", "processParameters"
        ),
    ),
)
ProductionFilterSequence = cms.Sequence(generator)
