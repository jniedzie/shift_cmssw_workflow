"""Recover Born sampling pThat for built-in HardQCD and direct charmonium.

Pythia 8.317 PhaseSpace2to2tauyz::finalKin assigns constituent masses to
particles massless in the matrix element, preserving theta and sHat, then
updates pTHat. CMS stores that updated value in GenEventInfoProduct. The
configured bin acts on the earlier massless pThat. Heavy-quark matrix elements
(121--124) already use massive phase space and must not be rescaled again.
For direct charmonium the onium (leg 3) is already massive at Born level;
only the recoiling light parton (leg 4) can acquire a constituent mass.
"""
import math


def born_pthat(code, stored, shat, m3, m4):
    if not all(math.isfinite(x) for x in (stored, shat, m3, m4)) or stored < 0 or shat <= 0 or min(m3, m4) < 0:
        raise ValueError('Invalid hard-process kinematics')
    if code in range(121, 125):
        return stored
    qcd = code in range(111, 117)
    onia = code in range(401, 411) or code == 441
    if not (qcd or onia):
        raise ValueError('Unsupported hard-process code')
    if onia and m3 <= 0:
        raise ValueError('Missing onium mass in hard-process leg 3')
    discriminant = (shat - m3*m3 - m4*m4)**2 - 4*m3*m3*m4*m4
    if shat <= (m3 + m4)**2 or discriminant <= 0:
        raise ValueError('Closed massive phase space')
    born_numerator = shat if qcd else shat - m3*m3
    return stored * born_numerator / math.sqrt(discriminant)


def from_hepmc(hepmc, code, stored):
    if not math.isfinite(stored) or stored < 0:
        raise ValueError('Invalid stored pThat')
    if code in range(121, 125):
        return stored
    particles = [hepmc.barcode_to_particle(i) for i in (3, 4, 5, 6)]
    if any(not p for p in particles):
        raise ValueError('Missing Pythia hard-process records')
    if [p.status() for p in particles] != [21, 21, 23, 23]:
        raise ValueError('Unexpected Pythia hard-process record layout')
    a, b, c, d = [p.momentum() for p in particles]
    for name in ('px', 'py', 'pz', 'e'):
        incoming = getattr(a, name)() + getattr(b, name)()
        outgoing = getattr(c, name)() + getattr(d, name)()
        if abs(incoming-outgoing) > 1.e-7 * max(1., a.e()+b.e()):
            raise ValueError('Hard-process four-momentum does not close')
    if max(abs(a.perp()), abs(b.perp()), abs(c.perp()-stored), abs(d.perp()-stored)) > 1.e-7:
        raise ValueError('Stored pThat differs from the primary hard-process record')
    shat = (a.e()+b.e())**2 - (a.px()+b.px())**2 - (a.py()+b.py())**2 - (a.pz()+b.pz())**2
    return born_pthat(code, stored, shat, particles[2].generated_mass(), particles[3].generated_mass())
