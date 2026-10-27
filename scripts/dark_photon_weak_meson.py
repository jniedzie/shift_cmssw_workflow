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
inserted directly into this Born relation. Scalar, axial and two-photon
amplitudes are not inferred from an inclusive rate here. The separately
matched KL current retains the hypercharge model's neutral-current penguin;
it excludes the semileptonic weak-Z/box coefficient. The
charged-kaon fit is not a substitute for an unknown neutral-kaon amplitude.
All fit parameters, domains, masses, rates and their references are caller
inputs. Parent flight/material transport and source normalization are separate.
Four-vectors are (E,px,py,pz), GeV; relative daughter positions are mm.
"""
import cmath
import bisect
from decimal import Decimal, localcontext
from functools import lru_cache
import json
import math

from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_double_radiative import polarizations
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


def inami_lim_functions(x):
    """One-loop C0,D0,E0 and gauge-independent Z0=C0+D0/4.

    Buchalla/Buras/Lautenbacher hep-ph/9512380 Eqs.VI.15, XIV.2,
    XXI.8; Dekens et al. JHEP11(2021)127 Eqs.D39--40. Decimal near
    x=1 avoids cancellation of the apparent third/fourth-order poles.
    """
    _real(x, 'positive internal-quark mass ratio', positive=True)
    if x == 1.:
        return dict(C0=3/16, D0=11/216, E0=43/72, Z0=3/16+11/864)
    if abs(x-1) < .1:
        with localcontext() as context:
            context.prec = 60
            t = Decimal(str(x));u = t-1;logarithm = t.ln()
            c = t/8*((t-6)/u+(3*t+2)/u**2*logarithm)
            d = -Decimal(4)/9*logarithm+(-19*t**3+25*t**2)/(36*u**3)+t**2*(5*t**2-2*t-6)/(18*u**4)*logarithm
            e = -Decimal(2)/3*logarithm-t*(18-11*t-t**2)/(12*u**3)+t**2*(15-16*t+4*t**2)/(6*u**4)*logarithm
            return dict(C0=float(c), D0=float(d), E0=float(e), Z0=float(c+d/4))
    u, logarithm = x-1, math.log(x)
    c = x/8*((x-6)/u+(3*x+2)/u**2*logarithm)
    d = -4/9*logarithm+(-19*x**3+25*x**2)/(36*u**3)+x**2*(5*x**2-2*x-6)/(18*u**4)*logarithm
    e = -2/3*logarithm-x*(18-11*x-x**2)/(12*u**3)+x**2*(15-16*x+4*x**2)/(6*u**4)*logarithm
    return dict(C0=c, D0=d, E0=e, Z0=c+d/4)


def kl_cpv_form_factor_components(parent_mass, pion_mass, mass, model):
    """KL -> pi0 A' W after factoring the *actual* EM mixing amplitude.

    Matching is the hypercharge current, not the semileptonic y7V: the
    photon part D0-P0-PE*E0 combines with 4*rNC*C0. Here
    rNC=(neutral/EM)*mW^2/(sin(thetaW)*cos(thetaW)*q^2), using the frozen model's
    exact neutral-mass diagonalization. At small mixing and q^2/mZ^2,
    this is 4*Z0-P0-PE*E0. Y0/sin²(thetaW), including semileptonic
    Z/box interactions, is excluded. The finite-q² residual of this
    dimension-six matching is a higher-order EFT approximation.

    In the CP convention KL=(K0-Kbar0+epsilonK*(K0+Kbar0))/sqrt(2),
    W_direct=i*sqrt(2)*GF*M²*Im(lambda_t)*f+(q²)*(matched coefficient).
    Indirect epsilonK*W_KS is added coherently before squaring. The KS
    current must already be evaluated at this exact signal mass; neither
    a charged-kaon fit nor an incoherent KS branching ratio is substituted.
    """
    from dark_photon_model import ElectroweakInputs, mixing_parameters
    if (model.get('kind') != 'hypercharge-kl-pi0-cpv' or model.get('parent_pdg_id') != 130 or
            model.get('qcd_scheme') != 'NDR' or
            model.get('normalization_convention') != 'matched-hypercharge-current-after-factoring-exact-em-mixing' or
            any(not model.get(name) for name in ('reference', 'qcd_reference', 'ckm_reference', 'semileptonic_reference'))):
        raise ValueError('Attributed matched KL hypercharge-current model required')
    for value, name in ((parent_mass, 'parent mass'), (pion_mass, 'pion mass'), (mass, 'signal mass')):
        _real(value, name, positive=True)
    domain = model.get('valid_mass_range_gev')
    if (not isinstance(domain, (list, tuple)) or len(domain) != 2 or
            not all(type(x) in (int, float) and math.isfinite(x) for x in domain) or
            not 0 <= domain[0] <= mass <= domain[1] <= parent_mass-pion_mass or
            model.get('signal_mass_gev') != mass):
        raise ValueError('KL current requires its exact frozen signal mass and physical domain')
    epsilon = _real(model.get('physical_epsilon'), 'physical mixing', positive=True)
    if epsilon > .1:
        raise ValueError('Require perturbative small kinetic mixing')
    ew = ElectroweakInputs(**model.get('electroweak_inputs', {}))
    mixing = mixing_parameters(mass, epsilon, ew)
    fermi = _real(model.get('fermi_constant_gev_minus2'), 'Fermi constant', positive=True)
    imaginary_ckm = _real(model.get('imag_lambda_t'), 'imaginary CKM product')
    top = _real(model.get('running_top_mass_gev'), 'running top mass', positive=True)
    mw = _real(model.get('w_mass_gev'), 'W mass', positive=True)
    ratio = (mixing.neutral_current_coefficient/mixing.em_coefficient
             *mw**2/(ew.sin_theta_w*ew.cos_theta_w*mass**2))
    _real(model.get('qcd_scale_gev'), 'QCD renormalization scale', positive=True)
    p0 = _real(model.get('qcd_p0'), 'QCD current-current coefficient')
    pe = _real(model.get('qcd_pe'), 'QCD gluon-penguin coefficient')
    fzero = _real(model.get('fplus_zero'), 'semileptonic form factor at zero', positive=True)
    pole = _real(model.get('fplus_pole_mass_gev'), 'measured semileptonic pole parameter', positive=True)
    if mass >= pole:
        raise ValueError('Semileptonic fit domain crosses its explicit pole')
    functions = inami_lim_functions((top/mw)**2)
    matched = functions['D0']-p0-pe*functions['E0']+4*ratio*functions['C0']
    fplus = fzero/(1-(mass/pole)**2)
    direct = 1j*math.sqrt(2)*fermi*parent_mass**2*imaginary_ckm*fplus*matched
    complex_values = []
    for name in ('kaon_mixing_epsilon', 'indirect_ks_form_factor_at_signal_mass'):
        row = model.get(name)
        if (not isinstance(row, (list, tuple)) or len(row) != 2 or
                not all(type(x) in (int, float) and math.isfinite(x) for x in row)):
            raise ValueError('Explicit '+name+'=[real,imaginary] required')
        complex_values.append(complex(*row))
    kaon_mixing, ks_current = complex_values
    normalization = math.sqrt(1+abs(kaon_mixing)**2)
    indirect = kaon_mixing*ks_current
    return dict(direct=direct/normalization, indirect=indirect/normalization,
        total=(direct+indirect)/normalization, neutral_current_ratio=ratio,
        matched_short_distance_coefficient=matched, semileptonic_form_factor=fplus)


def kl_cpv_form_factor(parent_mass, pion_mass, mass, model):
    return kl_cpv_form_factor_components(parent_mass, pion_mass, mass, model)['total']


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


def _dipion_model(model, mass):
    """An attributed finite current, never an inferred experimental bound.

    The coefficient convention is H=sum(c_i B_i), amplitude=epsilon*e*H.eps.
    B_E2=dq*((q.p2)p1-(q.p1)p2)/M^5, dq=(p1-p2).q;
    B_CR=(q^2 P-(P.q)q)/M^3*(s-m_pi^2)/(s-M^2);
    B_C=(q^2 P-(P.q)q)/M^3; B_M2=dq*eps(p1,p2,q)/M^5.
    All c_i are dimensionless. E2/CR follow 2602.15117 Appendix A;
    the Bose-even M2 tensor is the neutral-kaon magnetic quadrupole.
    Coefficients and timelike continuation must be supplied independently.
    """
    inputs = model.get('neutral_kaon_dipion_model', model)
    if (inputs.get('kind') != 'neutral-kaon-dipion-finite-current' or
            inputs.get('parent_pdg_id') not in (130, 310) or
            not inputs.get('reference') or not inputs.get('coefficient_reference')):
        raise ValueError('Attributed neutral-kaon finite-current model required')
    parent = _real(inputs.get('reference_parent_mass_gev'), 'parent mass', positive=True)
    pion = _real(inputs.get('pion_mass_gev'), 'pion mass', positive=True)
    _real(inputs.get('alpha_em'), 'electromagnetic coupling', positive=True)
    _real(inputs.get('total_width_gev'), 'parent width', positive=True)
    _real(mass, 'vector mass', nonnegative=True)
    domain = inputs.get('valid_mass_range_gev')
    if (not isinstance(domain, (list, tuple)) or len(domain) != 2 or
            not all(type(x) in (int, float) and math.isfinite(x) for x in domain) or
            not 0 <= domain[0] <= mass <= domain[1] <= parent-2*pion):
        raise ValueError('Requested mass is outside the finite-current model domain')
    coefficients = inputs.get('coefficients', {})
    names = ('electric_quadrupole', 'charge_radius_pole', 'electric_contact', 'magnetic_quadrupole')
    if set(coefficients) != set(names):
        raise ValueError('All four finite-current complex coefficients must be explicit')
    for row in coefficients.values():
        if (not isinstance(row, (list, tuple)) or len(row) != 2 or
                not all(type(x) in (int, float) and math.isfinite(x) for x in row)):
            raise ValueError('Complex coefficient must be [real, imaginary]')
    return inputs


def neutral_dipion_current(parent, first, second, *, model):
    """Coherent E2, charge-radius pole/contact and M2 hadronic current."""
    for value in (parent, first, second):
        _vector(value)
    scale = model['reference_parent_mass_gev']
    q = tuple(p-a-b for p, a, b in zip(parent, first, second))
    q2, pq = _minkowski(q, q), _minkowski(parent, q)
    pair = tuple(a+b for a, b in zip(first, second))
    s = _minkowski(pair, pair)
    difference = _minkowski(tuple(a-b for a, b in zip(first, second)), q)
    qp1, qp2 = _minkowski(q, first), _minkowski(q, second)
    electric = tuple(difference*(qp2*a-qp1*b)/scale**5 for a, b in zip(first, second))
    contact = tuple((q2*p-pq*k)/scale**3 for p, k in zip(parent, q))
    pole = tuple(value*(s-model['pion_mass_gev']**2)/(s-scale**2) for value in contact)
    # epsilon^{0123}=+1, with covariant momentum components under the epsilon.
    covariant = [(p[0], -p[1], -p[2], -p[3]) for p in (first, second, q)]
    magnetic = []
    for mu in range(4):
        rest = [index for index in range(4) if index != mu]
        a, b, c = rest
        determinant = (covariant[0][a]*(covariant[1][b]*covariant[2][c]-covariant[1][c]*covariant[2][b])
            -covariant[0][b]*(covariant[1][a]*covariant[2][c]-covariant[1][c]*covariant[2][a])
            +covariant[0][c]*(covariant[1][a]*covariant[2][b]-covariant[1][b]*covariant[2][a]))
        magnetic.append((-1)**mu*difference*determinant/scale**5)
    names = ('electric_quadrupole', 'charge_radius_pole', 'electric_contact', 'magnetic_quadrupole')
    weights = [complex(*model['coefficients'][name]) for name in names]
    return tuple(sum(weight*basis[i] for weight, basis in zip(weights, (electric, pole, contact, magnetic)))
                 for i in range(4))


def neutral_dipion_rest_kinematics(parent_mass, pion_mass, mass, invariant, cosine):
    """A' along +z; first pion helicity angle is in the dipion rest frame."""
    if not -1 <= cosine <= 1 or not 4*pion_mass**2 <= invariant <= (parent_mass-mass)**2:
        raise ValueError('Physical dipion phase space required')
    root = math.sqrt(invariant)
    outer = two_body_momentum(parent_mass, root, mass)
    dark = (math.hypot(outer, mass), 0., 0., outer)
    pair_energy = math.hypot(outer, root)
    inner = math.sqrt(max(0., invariant/4-pion_mass**2))
    first = (root/2, inner*math.sqrt(max(0., 1-cosine*cosine)), 0., inner*cosine)
    second = (root/2, -first[1], 0., -first[3])
    beta = (0., 0., -outer/pair_energy)
    return (parent_mass, 0., 0., 0.), dark, boost(first, beta), boost(second, beta)


def neutral_dipion_polarization_density(invariant, cosine, mass, model):
    """A' spin density in the real Cartesian polarization basis; epsilon=1."""
    inputs = _dipion_model(model, mass)
    parent, dark, first, second = neutral_dipion_rest_kinematics(
        inputs['reference_parent_mass_gev'], inputs['pion_mass_gev'], mass, invariant, cosine)
    current = neutral_dipion_current(parent, first, second, model=inputs)
    values = [math.sqrt(4*math.pi*inputs['alpha_em'])*_minkowski(current, state)
              for state in polarizations(dark, mass)]
    return tuple(tuple(a*b.conjugate() for b in values) for a in values)


def neutral_dipion_differential_width(invariant, cosine, mass, model):
    """dGamma/ds/dcos(theta_pi), epsilon=1, including identical-pion 1/2!."""
    inputs = _dipion_model(model, mass)
    parent, pion = inputs['reference_parent_mass_gev'], inputs['pion_mass_gev']
    if invariant in (4*pion**2, (parent-mass)**2):
        return 0.
    rho = neutral_dipion_polarization_density(invariant, cosine, mass, inputs)
    trace = sum(rho[i][i].real for i in range(len(rho)))
    outer = two_body_momentum(parent, math.sqrt(invariant), mass)
    inner = math.sqrt(max(0., invariant/4-pion**2))
    return trace*outer*inner/(256*math.pi**3*parent**2*math.sqrt(invariant))


def _dipion_angular_polynomial(invariant, mass, model):
    # Bose-even local basis gives exactly A+B*c^2+C*c^4 after spin sum.
    a = neutral_dipion_differential_width(invariant, 0., mass, model)
    at_one = neutral_dipion_differential_width(invariant, 1., mass, model)-a
    at_half = neutral_dipion_differential_width(invariant, math.sqrt(.5), mass, model)-a
    c = 2*at_one-4*at_half
    return a, at_one-c, c


def _dipion_angular_integral(invariant, mass, model):
    a, b, c = _dipion_angular_polynomial(invariant, mass, model)
    result = 2*(a+b/3+c/5)
    if result < -1.e-12*max(abs(a), abs(b), abs(c)):
        raise RuntimeError('Nonpositive coherent dipion angular integral')
    return max(0., result)


def _dipion_integral(function, low, high, order=8):
    return (high-low)/2*math.fsum(weight*function((low+high)/2+(high-low)*node/2)
                                for node, weight in _gauss_legendre(order))


def _dipion_t_density(coordinate, mass, model):
    if coordinate in (0., 1.):
        return 0.
    low = 4*model['pion_mass_gev']**2
    span = (model['reference_parent_mass_gev']-mass)**2-low
    invariant = low+span*math.sin(math.pi*coordinate/2)**2
    return _dipion_angular_integral(invariant, mass, model)*span*math.pi/2*math.sin(math.pi*coordinate)


@lru_cache(maxsize=64)
def _dipion_segments(mass, key):
    model = json.loads(key)
    function = lambda coordinate: _dipion_t_density(coordinate, mass, model)
    cumulative, total = [], 0.
    for index in range(32):
        total += _dipion_integral(function, index/32, (index+1)/32)
        cumulative.append(total)
    if not math.isfinite(total) or total <= 0:
        raise ValueError('Positive open finite-current dipion rate required')
    return tuple(cumulative)


def neutral_dipion_decay_width(model, mass, *, epsilon, quadrature_order=32):
    """Finite-model width, GeV; no dimuon branching or parent-flight weighting."""
    inputs = _dipion_model(model, mass)
    _real(epsilon, 'mixing', nonnegative=True)
    if epsilon > .1 or type(quadrature_order) is not int or not 8 <= quadrature_order <= 128:
        raise ValueError('Small mixing and bounded quadrature order required')
    if mass >= inputs['reference_parent_mass_gev']-2*inputs['pion_mass_gev']:
        return 0.
    return epsilon**2*_dipion_integral(lambda x: _dipion_t_density(x, mass, inputs),
                                     0., 1., quadrature_order)


def _dipion_native_inputs(parent_mass, mass, model, *, energy=None,
                          native_context=None, parent_p4=None):
    inputs = _dipion_model(model, mass)
    _real(parent_mass, 'native parent mass', positive=True)
    reference = inputs['reference_parent_mass_gev']
    tolerance = inputs.get('pole_mass_numerical_tolerance_gev', 0.)
    _real(tolerance, 'documented native pole-mass tolerance', nonnegative=True)
    if tolerance > 1.e-5:
        raise ValueError('Neutral-kaon pole tolerance exceeds the numerical-table allowance')
    binary_error = 32*math.ulp((energy or parent_mass)**2)/(parent_mass+reference)
    if native_context is not None:
        # A small lab energy can result from cancellation of large source-
        # frame boost terms. Admit only the independently checked immutable
        # native record and its propagated arithmetic allowance, preserving
        # the finite table support and the existing no-context guard.
        from dark_photon_kaon import native_source_mass_precision
        precision = native_source_mass_precision(parent_mass, native_context,
            expected_p4=parent_p4, expected_pdg_id=inputs['parent_pdg_id'])
        table_mass = precision['native_mass_gev']
        table_roundoff = 8*max(math.ulp(table_mass), math.ulp(reference))
        if abs(table_mass-reference) > tolerance+table_roundoff:
            raise ValueError('Native table mass differs from the declared neutral-kaon reference support')
        binary_error = max(binary_error, precision['mass_allowance_gev'])
    if abs(parent_mass-reference) > tolerance+binary_error:
        raise ValueError('Native/reference neutral-kaon mass mismatch exceeds documented tolerance')
    if parent_mass == reference:
        return inputs
    # Resolve all current factors and phase space at the actual native pole;
    # using reference kinematics would spoil conservation in the final graph.
    domain = inputs['valid_mass_range_gev']
    return dict(inputs, reference_parent_mass_gev=parent_mass,
        valid_mass_range_gev=[domain[0], min(domain[1], parent_mass-2*inputs['pion_mass_gev'])])


def neutral_dipion_branching_coefficient(parent_mass, mass, model, *, native_energy_gev=None,
                                        native_context=None):
    inputs = _dipion_native_inputs(parent_mass, mass, model, energy=native_energy_gev,
                                  native_context=native_context)
    key = json.dumps(inputs, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return _dipion_segments(mass, key)[-1]/inputs['total_width_gev']


def sample_neutral_dipion_decay(parent, mass, proper_ctau_mm, rng, *, model,
                               native_context=None):
    """Correlated spin-zero -> pi0 pi0 A' -> pi0 pi0 mu+mu- chain.

    The native parent must fly and undergo material transport before this
    relative decay is placed. Its flight and A' flight are never conditioned.
    Quadrature CDFs integrate the specified current, without scanned maxima.
    """
    from dark_photon_kaon_leptonic import (sample_vector_muon_direction,
        _isotropic_rotation, _boost_from_parent_rest)
    inputs = _dipion_model(model, mass)
    _real(proper_ctau_mm, 'proper lifetime', positive=True)
    _vector(parent)
    reference = inputs['reference_parent_mass_gev']
    squared = math.fsum((parent[0]**2, *(-x*x for x in parent[1:])))
    if parent[0] <= 0 or squared <= 0 or mass <= 2*MUON_MASS_GEV:
        raise ValueError('Native on-shell neutral kaon and open dimuon channel required')
    inputs = _dipion_native_inputs(math.sqrt(squared), mass, inputs, energy=parent[0],
                                  native_context=native_context, parent_p4=parent)
    scale = inputs['reference_parent_mass_gev']
    key = json.dumps(inputs, sort_keys=True, separators=(',', ':'), allow_nan=False)
    cumulative = _dipion_segments(mass, key)
    draw = rng.random()*cumulative[-1]
    index = min(bisect.bisect_right(cumulative, draw), len(cumulative)-1)
    base, low, high = (cumulative[index-1] if index else 0.), index/32, (index+1)/32
    start = low
    function = lambda x: _dipion_t_density(x, mass, inputs)
    segment_rate = cumulative[index]-base
    coordinate = low+(high-low)*(draw-base)/segment_rate
    for _ in range(32):
        residual = _dipion_integral(function, start, coordinate)-(draw-base)
        if abs(residual) <= 2.e-13*segment_rate:
            break
        if residual < 0:
            low = coordinate
        else:
            high = coordinate
        density = function(coordinate)
        candidate = coordinate-residual/density if density > 0 else (low+high)/2
        coordinate = candidate if low < candidate < high else (low+high)/2
    else:
        raise RuntimeError('Neutral dipion invariant CDF inversion did not converge')
    s_low = 4*inputs['pion_mass_gev']**2
    invariant = s_low+((scale-mass)**2-s_low)*math.sin(math.pi*coordinate/2)**2
    a, b, c = _dipion_angular_polynomial(invariant, mass, inputs)
    half = a+b/3+c/5
    target = (2*rng.random()-1)*half
    low, high = -1., 1.
    for _ in range(48):
        middle = (low+high)/2
        if a*middle+b*middle**3/3+c*middle**5/5 < target:
            low = middle
        else:
            high = middle
    cosine = (low+high)/2
    _, dark, first, second = neutral_dipion_rest_kinematics(scale, inputs['pion_mass_gev'], mass, invariant, cosine)
    rho = neutral_dipion_polarization_density(invariant, cosine, mass, inputs)
    direction, trials = sample_vector_muon_direction(rho, mass, rng)
    beta = tuple(x/dark[0] for x in dark[1:])
    axes = [boost(state, tuple(-x for x in beta))[1:] for state in polarizations(dark, mass)]
    momentum = math.sqrt((mass/2-MUON_MASS_GEV)*(mass/2+MUON_MASS_GEV))
    spatial = tuple(momentum*math.fsum(direction[i]*axes[i][j] for i in range(3)) for j in range(3))
    muons = [boost((mass/2, *(sign*x for x in spatial)), beta) for sign in (1., -1.)]
    rotation = _isotropic_rotation(rng)
    dark, first, second, muon, antimuon = [_boost_from_parent_rest((v[0], *(rotation@v[1:])), parent, scale)
        for v in (dark, first, second, *muons)]
    proper = -proper_ctau_mm*math.log1p(-rng.random())
    return dict(dark_photon_p4=dark, muon_p4=muon, antimuon_p4=antimuon,
        recoil_p4s=[first, second], recoil_pdg_ids=[111, 111], recoil_masses_gev=[inputs['pion_mass_gev']]*2,
        dark_photon_mass_gev=mass, muon_mass_gev=MUON_MASS_GEV, proper_decay_length_mm=proper,
        decay_displacement_mm=tuple(proper*x/mass for x in dark[1:]), decay_ct_mm=proper*dark[0]/mass,
        unconditional_decay=True, recoil_natural_decay_required=True, parent_polarization='spin-zero',
        amplitude_model=inputs['kind'], polarization_model='full-coherent-massive-vector-density',
        dipion_mass_squared=invariant, pion_helicity_cosine=cosine, angular_proposal_trials=trials,
        cdf_rate_over_epsilon_squared_gev=cumulative[-1], reference_parent_mass_gev=reference,
        event_parent_mass_gev=scale)


def mean_neutral_dipion_lab_beta_gamma(parent, mass, model, *, native_context=None):
    """Rate-weighted mean A' boost for the unconditioned spin-zero decay."""
    from dark_photon_vector_meson import mean_lab_beta_gamma as two_body_mean
    _vector(parent)
    squared = math.fsum((parent[0]**2, *(-x*x for x in parent[1:])))
    if parent[0] <= 0 or squared <= 0:
        raise ValueError('Future timelike native parent required')
    inputs = _dipion_native_inputs(math.sqrt(squared), mass, model, energy=parent[0],
                                  native_context=native_context, parent_p4=parent)
    scale = inputs['reference_parent_mass_gev']
    low = 4*inputs['pion_mass_gev']**2
    span = (scale-mass)**2-low
    def mean(x):
        s = low+span*math.sin(math.pi*x/2)**2
        return _dipion_t_density(x, mass, inputs)*two_body_mean(parent, math.sqrt(s), mass, polarization='unpolarized')
    denominator = _dipion_integral(lambda x: _dipion_t_density(x, mass, inputs), 0., 1., 32)
    return _dipion_integral(mean, 0., 1., 32)/denominator
