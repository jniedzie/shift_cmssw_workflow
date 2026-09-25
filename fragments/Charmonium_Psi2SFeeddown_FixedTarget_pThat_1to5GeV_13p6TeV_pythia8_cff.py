"""Standalone prompt psi(2S) production for J/psi feed-down studies.

The parent state decays naturally; only a daughter J/psi is forced to dimuons.
No J/psi acceptance filter is applied. Keep this primary-process sample separate
from direct J/psi and HardQCD, and audit branching/secondary-MPI overlap.
"""
import FWCore.ParameterSet.Config as cms
from Configuration.Generator.Pythia8CommonSettings_cfi import pythia8CommonSettingsBlock
from Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi import pythia8CP5SettingsBlock

channels = ['gg2ccbar(3S1)[3S1(1)]g', 'gg2ccbar(3S1)[3S1(1)]gm']
channels += [f'{initial}2ccbar(3S1)[{state}]{out}'
             for state in ('3S1(8)', '1S0(8)', '3PJ(8)')
             for initial, out in (('gg', 'g'), ('qg', 'q'), ('qqbar', 'g'))]
generator = cms.EDFilter(
    'Pythia8GeneratorFilter',
    pythiaPylistVerbosity=cms.untracked.int32(0),
    pythiaHepMCVerbosity=cms.untracked.bool(False),
    maxEventsToPrint=cms.untracked.int32(0),
    comEnergy=cms.double(13600.),
    PythiaParameters=cms.PSet(
        pythia8CommonSettingsBlock, pythia8CP5SettingsBlock,
        processParameters=cms.vstring(
            [f'Charmonium:{channel} = {{off,on}}' for channel in channels] + [
                'PhaseSpace:pTHatMin = 1.', 'PhaseSpace:pTHatMax = 5.',
                '443:onMode = off', '443:onIfMatch = 13 -13',
                'Beams:frameType = 2',
                'Beams:idA = 2212', 'Beams:eA = 0.',
                'Beams:idB = 2212', 'Beams:eB = 6800.',
                'Beams:allowVertexSpread = on',
                'Beams:offsetVertexZ = 148000.', 'Beams:sigmaVertexZ = 500.',
            ]),
        parameterSets=cms.vstring('pythia8CommonSettings', 'pythia8CP5Settings', 'processParameters'),
    ),
)
ProductionFilterSequence = cms.Sequence(generator)
