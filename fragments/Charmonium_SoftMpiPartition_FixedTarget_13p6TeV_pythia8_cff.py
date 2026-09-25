"""Direct-J/psi class from the same SoftQCD/MPI phase space as QCD.

The selected event contains at least one hard MPI colour-singlet or
colour-octet state that directly becomes J/psi.  Feed-down and nonprompt J/psi
without such a state belong to the complementary QCD class.  The dimuon decay
is forced for detector efficiency; its branching convention remains explicit.
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
        eventClass=cms.string("direct_jpsi"),
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
            "MultipartonInteractions:processLevel = 3",
            "443:onMode = off",
            "443:onIfMatch = 13 -13",
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
