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
            'Charmonium:all = on',       # prompt charmonium production (J/psi, chi_c, psi(2S), ...)
            'PhaseSpace:pTHatMin = 10.',
            'PhaseSpace:pTHatMax = 20.',
            '443:onMode = off',          # only retain the requested J/psi decay mode
            '443:onIfMatch = 13 -13',
        ),
        parameterSets=cms.vstring(
            'pythia8CommonSettings',
            'pythia8CP5Settings',
            'processParameters',
        )
    )
)
