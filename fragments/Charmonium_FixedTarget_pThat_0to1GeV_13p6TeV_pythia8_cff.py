import FWCore.ParameterSet.Config as cms
from Configuration.Generator.Pythia8CommonSettings_cfi import pythia8CommonSettingsBlock
from Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi import pythia8CP5SettingsBlock

generator = cms.EDFilter(
    "Pythia8GeneratorFilter",
    pythiaPylistVerbosity=cms.untracked.int32(0),
    filterEfficiency=cms.untracked.double(1.0),
    pythiaHepMCVerbosity=cms.untracked.bool(False),
    comEnergy=cms.double(13600.0),
    maxEventsToPrint=cms.untracked.int32(1),
    PythiaParameters=cms.PSet(
        pythia8CommonSettingsBlock,
        pythia8CP5SettingsBlock,
        processParameters=cms.vstring(
            'ParticleDecays:limitTau0 = off',
            'ResonanceWidths:minWidth = 1e-30',
            'Beams:frameType = 2',
            'Beams:idA = 2212',
            'Beams:eA = 6800.',
            'Beams:idB = 2212',
            'Beams:eB = 0.',
            'Beams:allowVertexSpread = on',
            'Beams:offsetVertexZ = 148000.',  # 148 m, Pythia units are mm
            'Beams:sigmaVertexZ = 500.',       # 0.5 m z smearing (Gaussian width)
            'Charmonium:all = on',
            'PhaseSpace:pTHatMin = 0.',
            'PhaseSpace:pTHatMax = 1.',
            '443:onMode = off',
            '443:onIfMatch = 13 -13',
        ),
        parameterSets=cms.vstring(
            'pythia8CommonSettings',
            'pythia8CP5Settings',
            'processParameters',
        )
    )
)
