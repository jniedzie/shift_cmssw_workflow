"""Unfiltered fixed-target HardQCD with the established SHIFT decay corridor.

No muon filter, momentum/acceptance gate, presampling or forced decay. All
generated events enter simulation. Preserve the prior decay model separately
from sampling; this remains an ATLAS-proxy machinery model, not final IR5 physics.
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
    PythiaParameters=cms.PSet(
        pythia8CommonSettingsBlock, pythia8CP5SettingsBlock,
        processParameters=cms.vstring(
            'HardQCD:all = on', 'SoftQCD:all = off',
            'Charmonium:all = off', 'Bottomonium:all = off',
            'PhaseSpace:pTHatMin = 1.', 'PhaseSpace:pTHatMax = 5.',
            'Beams:frameType = 2',
            'Beams:idA = 2212', 'Beams:eA = 0.',
            'Beams:idB = 2212', 'Beams:eB = 6800.',
            'Beams:allowVertexSpread = on',
            'Beams:offsetVertexZ = 148000.', 'Beams:sigmaVertexZ = 500.',
            'ParticleDecays:limitTau0 = off',
            'ParticleDecays:limitCylinder = on',
            'ParticleDecays:xyMax = 8000.', 'ParticleDecays:zMax = 151000.',
            '130:mayDecay = on', '211:mayDecay = on', '321:mayDecay = on',
        ),
        parameterSets=cms.vstring('pythia8CommonSettings', 'pythia8CP5Settings', 'processParameters'),
    ),
)
ProductionFilterSequence = cms.Sequence(generator)
