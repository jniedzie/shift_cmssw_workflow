"""Unfiltered HardQCD machinery test; not inclusive minimum-bias production.

Ownership: primary processes 111--116 and 121--124, distinct from direct
J/psi 401--410,441. Keep heavy-flavour decays (including nonprompt J/psi).
CMS common/CP5 and lifetime cutoff are preserved. No muon acceptance filter.
"""
import FWCore.ParameterSet.Config as cms
from Configuration.Generator.Pythia8CommonSettings_cfi import pythia8CommonSettingsBlock
from Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi import pythia8CP5SettingsBlock

generator = cms.EDFilter(
    "Pythia8GeneratorFilter",
    pythiaPylistVerbosity=cms.untracked.int32(0),
    pythiaHepMCVerbosity=cms.untracked.bool(False),
    maxEventsToPrint=cms.untracked.int32(0),
    comEnergy=cms.double(13600.),  # actual sqrt(s) set by frameType=2
    PythiaParameters=cms.PSet(
        pythia8CommonSettingsBlock, pythia8CP5SettingsBlock,
        processParameters=cms.vstring(
            'HardQCD:all = on',
            'SoftQCD:all = off',
            'Charmonium:all = off',
            'Bottomonium:all = off',
            'PhaseSpace:pTHatMin = 1.',
            'PhaseSpace:pTHatMax = 5.',
            'Beams:frameType = 2',
            'Beams:idA = 2212', 'Beams:eA = 0.',
            'Beams:idB = 2212', 'Beams:eB = 6800.',
            # Match existing production's spatial source convention. The
            # standard CMS beamspot is retained; SHIFT timing is applied later.
            'Beams:allowVertexSpread = on',
            'Beams:offsetVertexZ = 148000.',
            'Beams:sigmaVertexZ = 500.',
        ),
        parameterSets=cms.vstring('pythia8CommonSettings', 'pythia8CP5Settings', 'processParameters'),
    ),
)
