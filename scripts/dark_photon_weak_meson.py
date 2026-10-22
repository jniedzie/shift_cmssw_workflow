# Code style: AI-code. AI-generated single-current weak meson transition kernel.
"""Spin-zero P -> P' A' through a single weak electromagnetic current.

D'Ambrosio et al., hep-ph/9808289 Eqs.(1)--(11), define the dimensionless W:
  H^mu = W / ((4 pi)^2 M^2) [q^2 (p+p')^mu - (M^2-m'^2) q^mu].
The mixing amplitude is epsilon*e*H.eps_A. It produces helicity-zero A' only;
the parent-frame direction is isotropic. This is different from P -> gamma A'.
The Born dilepton spectrum gives
  Gamma(P -> P' A') / (dGamma(P -> P' ll)/dq^2)
      = epsilon^2 3 pi q^2 / [alpha beta_l (1+2 m_l^2/q^2)].

NA62 arXiv:2209.05076 provides a radiatively unfolded W fit. Its measured
inclusive dilepton width includes extra real/virtual photons and must not be
inserted directly into this Born relation. Scalar, axial, weak-Z and two-photon
amplitudes are not supplied or inferred from an inclusive rate here. The
charged-kaon fit is not a substitute for an unknown neutral-kaon amplitude.
All fit parameters, domains, masses, rates and their references are caller
inputs. Parent flight/material transport and source normalization are separate.
Four-vectors are (E,px,py,pz), GeV; relative daughter positions are mm.
"""
import cmath
import math

from dark_photon_meson import MUON_MASS_GEV, boost, dot
from dark_photon_vector_meson import two_body_momentum


REFERENCES = {
    'current_and_pion_loop': 'https://arxiv.org/pdf/hep-ph/9808289#page=3',
    'charged_kaon_fit': 'https://arxiv.org/html/2209.05076v3#S4',
    'massive_lepton_angular_density': 'https://arxiv.org/html/2404.03643v2#S2',
}


def _real(value, name, *, nonnegative=False, positive=False):
    if (type(value) not in (int, float) or not math.isfinite(value) or
            nonnegative and value < 0 or positive and value <= 0):
        raise ValueError('Finite physical ' + name + ' required')
    return value


def _vector(values, *, complex_allowed=False):
    kinds = (int, float, complex) if complex_allowed else (int, float)
    if (not isinstance(values, (list, tuple)) or len(values) != 4 or
            not all(type(value) in kinds and cmath.isfinite(value) for value in values)):
        raise ValueError('Four finite momentum/polarization components required')


def _minkowski(first, second):
    return first[0]*second[0]-sum(a*b for a, b in zip(first[1:], second[1:]))


def pion_loop_chi(x):
    """Subtracted charged-pion loop, x=q^2/m_pi^2; chi(0)=0.

    The positive absorptive part above threshold is pi*(1-4/x)^(3/2)/6.
    The series avoids subtracting 1/x terms at small virtuality.
    """
    _real(x, 'pion-loop virtuality', nonnegative=True)
    if x < 1.e-3:
        return complex(x*(1/60+x*(1/840+x*(1/7560+x*(1/55440+x/360360)))))
    if x < 4:
        function = math.sqrt(4/x-1)*math.asin(math.sqrt(x)/2)
    elif x == 4:
        function = 0.
    else:
        beta = math.sqrt(1-4/x)
        function = -.5*beta*(math.log(4/x)-2*math.log1p(beta)+1j*math.pi)
    return 4/9-4/(3*x)-(1-4/x)*function/3


def chiral_form_factor(parent_mass, pion_mass, mass, model):
    """Explicit K+/K-/KS ChPT p^6 vector form factor, not a total weak rate.

    The pion electromagnetic factor is 1+z/rV^2 as in hep-ph/9808289.
    NA62 fitted a+,b+ with revised K->3pi alpha+,beta+; all four parameters
    and the declared rV^2 prescription must remain in the frozen input card.
    """
    for value, name in ((parent_mass, 'parent mass'), (pion_mass, 'pion mass')):
        _real(value, name, positive=True)
    _real(mass, 'current virtuality', nonnegative=True)
    if (model.get('kind') != 'chpt-p6-kaon-em-vector' or
            model.get('parent_pdg_id') not in (321, -321, 310) or
            not model.get('reference') or not model.get('loop_reference')):
        raise ValueError('Explicit qualified charged-kaon/KS vector model and references required')
    domain = model.get('valid_mass_range_gev')
    if (not isinstance(domain, (list, tuple)) or len(domain) != 2 or
            any(type(value) not in (int, float) or not math.isfinite(value) for value in domain) or
            not 0 <= domain[0] < domain[1] or not domain[0] <= mass <= domain[1] or
            domain[1] > parent_mass-pion_mass):
        raise ValueError('Requested virtuality is outside the explicit physical fit domain')
    a, b, alpha, beta = [_real(model.get(key), key)
                        for key in ('a', 'b', 'alpha_pipi', 'beta_pipi')]
    fermi = _real(model.get('fermi_constant_gev_minus2'), 'Fermi constant', positive=True)
    loop_pion = _real(model.get('loop_pion_mass_gev'), 'charged loop-pion mass', positive=True)
    rv_squared = _real(model.get('pion_vector_rv_squared'), 'pion vector mass ratio', positive=True)
    # The absorptive state is pi+ pi-. In a KS -> pi0 transition its mass
    # remains the charged-pion mass, distinct from the neutral recoil mass.
    z, rpi_squared = (mass/parent_mass)**2, (loop_pion/parent_mass)**2
    z0 = 1/3+rpi_squared
    loop = ((alpha+beta*(z-z0)/rpi_squared)/rpi_squared
            *(1+z/rv_squared)*pion_loop_chi(z/rpi_squared))
    return fermi*parent_mass**2*(a+b*z)+loop


def weak_current(parent, recoil, form_factor):
    """Gauge-invariant H^mu; e and epsilon are excluded."""
    for vector in (parent, recoil):
        _vector(vector)
    if not cmath.isfinite(form_factor):
        raise ValueError('Finite complex weak form factor required')
    parent_squared = _minkowski(parent, parent)
    recoil_squared = _minkowski(recoil, recoil)
    if parent[0] <= 0 or recoil[0] <= 0 or parent_squared <= 0 or recoil_squared < 0:
        raise ValueError('Physical future-directed spin-zero momenta required')
    q = tuple(a-b for a, b in zip(parent, recoil))
    total = tuple(a+b for a, b in zip(parent, recoil))
    q_squared = _minkowski(q, q)
    coefficient = form_factor/((4*math.pi)**2*parent_squared)
    return tuple(coefficient*(q_squared*p-(parent_squared-recoil_squared)*k)
                 for p, k in zip(total, q))


def amplitude(parent, recoil, dark_polarization, epsilon, alpha_em, form_factor):
    """One on-shell mixing amplitude; caller supplies the conjugate polarization."""
    _vector(dark_polarization, complex_allowed=True)
    _real(epsilon, 'mixing', nonnegative=True)
    _real(alpha_em, 'electromagnetic coupling', positive=True)
    if epsilon > .1:
        raise ValueError('Require a perturbative small-mixing model')
    return epsilon*math.sqrt(4*math.pi*alpha_em)*_minkowski(
        weak_current(parent, recoil, form_factor), dark_polarization)


def decay_width(parent_mass, recoil_mass, mass, epsilon, alpha_em, form_factor):
    """Leading single-current Gamma(P -> P' A'), GeV; no A' dimuon branching."""
    momentum = two_body_momentum(parent_mass, recoil_mass, mass)
    _real(epsilon, 'mixing', nonnegative=True)
    _real(alpha_em, 'electromagnetic coupling', positive=True)
    if epsilon > .1 or not cmath.isfinite(form_factor):
        raise ValueError('Require small mixing and a finite complex weak form factor')
    z, phase = (mass/parent_mass)**2, (2*momentum/parent_mass)**3
    value = epsilon**2*alpha_em*parent_mass*z*phase*abs(form_factor)**2/(4*(4*math.pi)**4)
    if not math.isfinite(value):
        raise ValueError('Weak decay width exceeds the finite arithmetic range')
    return value


def born_dilepton_differential_width(parent_mass, recoil_mass, mass,
                                     lepton_mass, alpha_em, form_factor):
    """Single-EM-vector dGamma/dq^2, GeV^-1, before QED radiation corrections."""
    momentum = two_body_momentum(parent_mass, recoil_mass, mass)
    _real(lepton_mass, 'lepton mass', nonnegative=True)
    _real(alpha_em, 'electromagnetic coupling', positive=True)
    if not cmath.isfinite(form_factor):
        raise ValueError('Finite complex weak form factor required')
    if mass <= 2*lepton_mass or momentum == 0:
        return 0.
    ratio = lepton_mass**2/mass**2
    phase = (2*momentum/parent_mass)**3
    return (alpha_em**2*phase*math.sqrt(1-4*ratio)*(1+2*ratio)*abs(form_factor)**2
            /(12*math.pi*(4*math.pi)**4*parent_mass))


def longitudinal_muon_density(cosine, mass, lepton_mass=MUON_MASS_GEV):
    """Normalized helicity-zero density; becomes 3/4 sin^2(theta) at m_l=0."""
    _real(cosine, 'helicity cosine')
    _real(mass, 'dilepton mass', positive=True)
    _real(lepton_mass, 'lepton mass', nonnegative=True)
    if not -1 <= cosine <= 1 or mass <= 2*lepton_mass:
        raise ValueError('Open dilepton channel and a physical helicity cosine required')
    beta_squared = 1-4*(lepton_mass/mass)**2
    return (1-beta_squared*cosine**2)/(2*(1-beta_squared/3))


def sample_decay(parent, recoil_mass, mass, proper_ctau_mm, rng):
    """Unconditional spin-zero -> spin-zero A' -> spin-zero mu+mu- chain.

    Parent flight precedes this relative A' flight. The caller owns the recoil
    charge/natural decay and applies the physical dimuon branching exactly once.
    """
    _vector(parent)
    if parent[0] <= 0:
        raise ValueError('Future-directed parent required')
    squared = math.fsum((parent[0]**2, *(-value**2 for value in parent[1:])))
    if squared <= 0:
        raise ValueError('Timelike parent required')
    parent_mass = math.sqrt(squared)
    momentum = two_body_momentum(parent_mass, recoil_mass, mass)
    _real(proper_ctau_mm, 'dark-photon proper lifetime', positive=True)
    if momentum == 0 or mass <= 2*MUON_MASS_GEV:
        raise ValueError('Closed weak/dimuon decay channel')
    cosine, phi = 2*rng.random()-1, 2*math.pi*rng.random()
    sine = math.sqrt(1-cosine*cosine)
    axis = (sine*math.cos(phi), sine*math.sin(phi), cosine)
    auxiliary = (0., 0., 1.) if abs(cosine) < .9 else (1., 0., 0.)
    cross = (axis[1]*auxiliary[2]-axis[2]*auxiliary[1],
             axis[2]*auxiliary[0]-axis[0]*auxiliary[2],
             axis[0]*auxiliary[1]-axis[1]*auxiliary[0])
    norm = math.sqrt(dot(cross, cross))
    first = tuple(value/norm for value in cross)
    second = (axis[1]*first[2]-axis[2]*first[1],
              axis[2]*first[0]-axis[0]*first[2],
              axis[0]*first[1]-axis[1]*first[0])
    maximum = longitudinal_muon_density(0., mass)
    while True:
        angle = 2*rng.random()-1
        if rng.random()*maximum <= longitudinal_muon_density(angle, mass):
            break
    azimuth, sine = 2*math.pi*rng.random(), math.sqrt(1-angle*angle)
    direction = tuple(angle*n+sine*(math.cos(azimuth)*u+math.sin(azimuth)*v)
                      for n, u, v in zip(axis, first, second))
    muon_momentum = math.sqrt((mass/2-MUON_MASS_GEV)*(mass/2+MUON_MASS_GEV))
    muon = (mass/2,)+tuple(muon_momentum*value for value in direction)
    antimuon = (mass/2,)+tuple(-muon_momentum*value for value in direction)
    dark_energy, recoil_energy = math.hypot(momentum, mass), math.hypot(momentum, recoil_mass)
    dark_beta = tuple(momentum*value/dark_energy for value in axis)
    parent_beta = tuple(value/parent[0] for value in parent[1:])
    dark = boost((dark_energy,)+tuple(momentum*value for value in axis), parent_beta)
    recoil = boost((recoil_energy,)+tuple(-momentum*value for value in axis), parent_beta)
    muon, antimuon = [boost(boost(value, dark_beta), parent_beta) for value in (muon, antimuon)]
    proper = -proper_ctau_mm*math.log1p(-rng.random())
    return dict(dark_photon_p4=dark, recoil_p4=recoil, muon_p4=muon, antimuon_p4=antimuon,
        dark_photon_mass_gev=mass, recoil_mass_gev=recoil_mass, muon_mass_gev=MUON_MASS_GEV,
        proper_decay_length_mm=proper, decay_displacement_mm=tuple(proper*p/mass for p in dark[1:]),
        decay_ct_mm=proper*dark[0]/mass, helicity_cosine=angle,
        dark_photon_polarization='longitudinal', parent_polarization='spin-zero',
        unconditional_decay=True, recoil_natural_decay_required=True)
