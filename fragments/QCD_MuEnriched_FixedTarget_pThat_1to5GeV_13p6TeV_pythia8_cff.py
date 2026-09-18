"""Muon-enriched fixed-target HardQCD pilot for the SHIFT upstream source.

This follows CMS's QCD MuEnriched pattern: generate ordinary HardQCD, allow
long-lived pion/kaon decays in a bounded cylinder, then retain events with a
stable generator muon. The CMS central-collision cylinder and pT threshold are
not applicable to a source 148 m upstream, so the explicit SHIFT adaptations
below cover the source-to-CMS corridor without imposing a reconstruction-
motivated momentum cut.
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
            'Beams:allowVertexSpread = on',
            'Beams:offsetVertexZ = 148000.',
            'Beams:sigmaVertexZ = 500.',
            # CMS MuEnriched fragments use the same lifetime/cylinder mechanism,
            # but their 2 m x 4 m central-collision volume is not SHIFT geometry.
            'ParticleDecays:limitTau0 = off',
            'ParticleDecays:limitCylinder = on',
            'ParticleDecays:xyMax = 8000.',
            'ParticleDecays:zMax = 151000.',
            '130:mayDecay = on',
            '211:mayDecay = on',
            '321:mayDecay = on',
        ),
        parameterSets=cms.vstring('pythia8CommonSettings', 'pythia8CP5Settings', 'processParameters'),
    ),
)

# MCSmartSingleParticleFilter reads generator:unsmeared. Its "decay" bounds
# are the selected muon's production vertex in absolute HepMC mm. Require one
# stable muon moving from the +z source hemisphere toward CMS. No momentum cut
# is imposed: this sample first establishes QCD events that actually contain
# generator muons, independently of whether they will later reconstruct.
mugenfilter = cms.EDFilter(
    "MCSmartSingleParticleFilter",
    ParticleID=cms.untracked.vint32(13, -13),
    Status=cms.untracked.vint32(1, 1),
    MinP=cms.untracked.vdouble(0., 0.),
    MinPt=cms.untracked.vdouble(0., 0.),
    MinEta=cms.untracked.vdouble(-10., -10.),
    MaxEta=cms.untracked.vdouble(0., 0.),
    MinDecayRadius=cms.untracked.vdouble(0., 0.),
    MaxDecayRadius=cms.untracked.vdouble(8000., 8000.),
    MinDecayZ=cms.untracked.vdouble(0., 0.),
    MaxDecayZ=cms.untracked.vdouble(151000., 151000.),
)

ProductionFilterSequence = cms.Sequence(generator * mugenfilter)
