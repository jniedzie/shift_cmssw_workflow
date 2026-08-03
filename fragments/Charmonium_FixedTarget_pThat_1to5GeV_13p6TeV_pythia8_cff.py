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
            'Beams:eA = 0.',
            'Beams:idB = 2212',
            'Beams:eB = 6800.',
            'Beams:allowVertexSpread = on',
            'Beams:offsetVertexZ = 148000.',  # 148 m, Pythia units are mm
            'Beams:offsetTime = -148000.',  # offset the time accordingly, units are mm/c
            'Beams:sigmaVertexZ = 500.',       # 0.5 m z smearing (Gaussian width)
            # Produce direct J/psi (the first entry in the 3S1 state vectors)
            # through every available colour-singlet and colour-octet channel.
            # Do not use "Charmonium:all": it also enables psi(2S), chi_c and
            # other charmonium states, so many generated events have no J/psi.
            'Charmonium:gg2ccbar(3S1)[3S1(1)]g = {on,off}',
            'Charmonium:gg2ccbar(3S1)[3S1(1)]gm = {on,off}',
            'Charmonium:gg2ccbar(3S1)[3S1(8)]g = {on,off}',
            'Charmonium:qg2ccbar(3S1)[3S1(8)]q = {on,off}',
            'Charmonium:qqbar2ccbar(3S1)[3S1(8)]g = {on,off}',
            'Charmonium:gg2ccbar(3S1)[1S0(8)]g = {on,off}',
            'Charmonium:qg2ccbar(3S1)[1S0(8)]q = {on,off}',
            'Charmonium:qqbar2ccbar(3S1)[1S0(8)]g = {on,off}',
            'Charmonium:gg2ccbar(3S1)[3PJ(8)]g = {on,off}',
            'Charmonium:qg2ccbar(3S1)[3PJ(8)]q = {on,off}',
            'Charmonium:qqbar2ccbar(3S1)[3PJ(8)]g = {on,off}',
            'PhaseSpace:pTHatMin = 1.',
            'PhaseSpace:pTHatMax = 5.',
            '443:onMode = off',
            '443:onIfMatch = 13 -13',
            # Hand both J/psi daughter muons to Geant4.  This is required
            # because limitTau0=off above would otherwise allow Pythia to
            # decay muons before detector simulation.
            '13:mayDecay = off',
        ),
        parameterSets=cms.vstring(
            'pythia8CommonSettings',
            'pythia8CP5Settings',
            'processParameters',
        )
    )
)
