# Code style: human-code
"""Muon-enriched version of the complementary non-diffractive QCD class.

Keep the existing SoftQCD/MPI process ownership and CP5 settings. The decay
cylinder and stable-muon filter reproduce the earlier SHIFT mu-enriched
sample. These Pythia decays do not model parent interactions with LSS material;
this named sampling target therefore still needs a transport comparison.
"""
import FWCore.ParameterSet.Config as cms
from Configuration.GenProduction.QCD_SoftMpiPartition_FixedTarget_13p6TeV_pythia8_cff import generator as ordinary_generator

generator = ordinary_generator.clone()
generator.PythiaParameters.processParameters.extend([
    "ParticleDecays:limitTau0 = off",
    "ParticleDecays:limitCylinder = on",
    "ParticleDecays:xyMax = 8000.",
    "ParticleDecays:zMax = 151000.",
    "130:mayDecay = on",
    "211:mayDecay = on",
    "321:mayDecay = on",
])

# Require a stable muon moving from the positive-z source toward CMS. Preserve
# the old loose filter: there is no p or pT threshold and no Both-Both cut.
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
