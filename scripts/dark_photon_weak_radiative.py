# Code style: AI-code. AI-generated leading weak neutral-kaon radiative current.
"""Rate/current benchmark for KL -> pi0 gamma A' at O(p^4), not a source.

Donoghue and Gabbiani hep-ph/9702278 Eqs.(6)--(15) give the charged-pion and
charged-kaon loops with one off-shell photon. The Feynman-parameter integral
fixes I=[m^2 DeltaF-q^2 DeltaG/2]/(s-q^2). The factor 1/2 follows from the
symmetry integral (1-x)log(...)=1/2 integral log(...); it is also necessary
for the soft-real-photon limit. Tests compare to the original double integral.

The complex pion cut and both loops are retained. This leading A tensor is
not the O(p^6) A/B/D amplitude, a VMD fit or an off-shell rate envelope.
Measured gamma gamma and gamma ee rates exceed this leading prediction;
none of them is used here to rescale the amplitude. Parent flight, material,
source exposure, an event sampler and BR(A' -> mu+mu-) remain separate.
Four-vectors are (E,px,py,pz), GeV, with metric (+---).
"""
import math

from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_vector_meson import two_body_momentum


REFERENCE = 'https://arxiv.org/pdf/hep-ph/9702278'


def _dot(first, second):
    return first[0]*second[0]-sum(a*b for a, b in zip(first[1:], second[1:]))


def _number(value, name, *, positive=False):
    if (type(value) not in (int, float) or not math.isfinite(value) or
            value < 0 or positive and value == 0):
        raise ValueError('Finite physical '+name+' required')
    return value


def loop_functions(invariant, mass):
    """F(a),G(a) of Eqs.(10),(11), retaining the negative-i0 pion cut."""
    _number(invariant, 'loop virtuality')
    _number(mass, 'loop mass', positive=True)
    x = invariant/mass**2
    if x == 0:
        return 0j, 0j
    if x <= 4:
        angle = math.asin(math.sqrt(x)/2)
        return complex(-2*angle**2), complex(-2+2*math.sqrt(4/x-1)*angle)
    beta = math.sqrt(1-4/x)
    logarithm = math.log(4/x)-2*math.log1p(beta)+1j*math.pi
    return .5*logarithm**2, -2-beta*logarithm


def _g_prime(invariant, mass):
    x = invariant/mass**2
    if x < .01:
        return -sum(math.factorial(n)**2/math.factorial(2*n+1)*x**(n-1)
                    for n in range(1, 14))/mass**2
    if x < 4:
        first = -math.asin(math.sqrt(x)/2)/(mass**2*math.sqrt(x)*math.sqrt(1-x/4))
        return (2*mass**2*first+1)/invariant
    beta = math.sqrt(1-4/x)
    logarithm = math.log(4/x)-2*math.log1p(beta)+1j*math.pi
    return 1/invariant-2*mass**2*logarithm/(beta*invariant**2)


def scalar_loop(pair_invariant, vector_invariant, loop_mass):
    """1+2I with stable soft-photon evaluation; s=(q+k)^2 and k^2=0."""
    s, q = pair_invariant, vector_invariant
    _number(s, 'photon-pair invariant')
    _number(q, 'vector invariant')
    _number(loop_mass, 'loop mass', positive=True)
    if s < q:
        raise ValueError('Future-directed photon needs s >= q^2')
    difference = s-q
    if difference == 0:
        return 0j
    threshold = 4*loop_mass**2
    # Exact integral identity D/(s-q)=integral_0^1 dt t G'(q+t(s-q)).
    # Away from its physical branch point this avoids subtracting 1-1.
    if (difference < 1.e-4*max(s, loop_mass**2) and
            min(abs(s-threshold), abs(q-threshold)) > 100*difference):
        return difference*sum(weight*(node+1)/4*
            _g_prime(q+(node+1)*difference/2, loop_mass)
            for node, weight in _gauss_legendre(8))
    fs, gs = loop_functions(s, loop_mass)
    fq, gq = loop_functions(q, loop_mass)
    return 1+2*(loop_mass**2*(fs-fq)-q*(gs-gq)/2)/difference


def _model(model, mass):
    if (model.get('kind') != 'chpt-p4-kl-pi0-photon-vector' or
            model.get('parent_pdg_id') != 130 or not model.get('reference')):
        raise ValueError('Explicit leading KL radiative-current authority required')
    for key in ('parent_mass_gev', 'recoil_pion_mass_gev', 'charged_loop_pion_mass_gev',
                'charged_loop_kaon_mass_gev', 'g8_gev_minus2', 'alpha_em'):
        _number(model.get(key), key, positive=True)
    domain = model.get('valid_vector_mass_range_gev')
    if (not isinstance(domain, (list, tuple)) or len(domain) != 2 or
            any(type(value) not in (int, float) or not math.isfinite(value) for value in domain) or
            not 0 <= domain[0] < domain[1] or not domain[0] <= mass <= domain[1] or
            domain[1] > model['parent_mass_gev']-model['recoil_pion_mass_gev']):
        raise ValueError('Requested vector mass exceeds the explicit current domain')


def reduced_loop_current(pair_invariant, vector_mass, model):
    """Mass-dimension-two scalar, before 2 e^2 G8/(16pi^2) and the tensor."""
    _number(vector_mass, 'vector mass')
    _model(model, vector_mass)
    q2 = vector_mass**2
    if not q2 <= pair_invariant <= (model['parent_mass_gev']-model['recoil_pion_mass_gev'])**2:
        raise ValueError('Photon-pair invariant outside physical decay phase space')
    pion, kaon = model['charged_loop_pion_mass_gev'], model['charged_loop_kaon_mass_gev']
    return ((pair_invariant-pion**2)*scalar_loop(pair_invariant, q2, pion)+
        (model['parent_mass_gev']**2+pion**2-pair_invariant)*
        scalar_loop(pair_invariant, q2, kaon))


def amplitude(dark, photon, dark_polarization, photon_polarization, vector_mass,
              epsilon, model):
    """Gauge-invariant leading current; caller supplies conjugate polarizations."""
    _number(epsilon, 'mixing')
    if epsilon > .1:
        raise ValueError('Require small perturbative mixing')
    for vector in (dark, photon, dark_polarization, photon_polarization):
        if (not isinstance(vector, (list, tuple)) or len(vector) != 4 or
                any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)):
            raise ValueError('Four finite real momentum/polarization components required')
    tolerance = 64*math.ulp(max(dark[0]**2, photon[0]**2, vector_mass**2))
    if (dark[0] <= 0 or photon[0] <= 0 or
            abs(_dot(dark, dark)-vector_mass**2) > tolerance or
            abs(_dot(photon, photon)) > tolerance):
        raise ValueError('Physical on-shell vector and real photon required')
    product = _dot(dark, photon)
    if product <= 0:
        raise ValueError('Open future-directed photon phase space required')
    scalar = reduced_loop_current(vector_mass**2+2*product, vector_mass, model)
    tensor = (_dot(dark_polarization, photon_polarization)-
        _dot(dark_polarization, photon)*_dot(photon_polarization, dark)/product)
    coefficient = -1j*epsilon*2*(4*math.pi*model['alpha_em'])*model['g8_gev_minus2']/(16*math.pi**2)
    return coefficient*scalar*tensor


def decay_width(model, vector_mass, *, epsilon, quadrature_order=64):
    """Gamma(KL -> pi0 gamma A'), GeV; both photons are distinguishable.

    The vector-mass-zero limit is twice epsilon^2*Gamma(gamma gamma).
    The one-dimensional phase integral includes all vector polarizations.
    """
    _number(vector_mass, 'vector mass')
    _number(epsilon, 'mixing')
    if epsilon > .1 or type(quadrature_order) is not int or quadrature_order < 8:
        raise ValueError('Require small mixing and an explicit quadrature order >=8')
    _model(model, vector_mass)
    parent, recoil = model['parent_mass_gev'], model['recoil_pion_mass_gev']
    low, high = vector_mass**2, (parent-recoil)**2
    if low >= high:
        return 0.
    threshold = 4*model['charged_loop_pion_mass_gev']**2
    edges = [low]+([threshold] if low < threshold < high else [])+[high]
    coefficient = epsilon**2*2*(2*(4*math.pi*model['alpha_em'])*
        model['g8_gev_minus2']/(16*math.pi**2))**2
    result = 0.
    for start, end in zip(edges, edges[1:]):
        for node, weight in _gauss_legendre(quadrature_order):
            invariant = (start+end)/2+(end-start)*node/2
            scalar = reduced_loop_current(invariant, vector_mass, model)
            momentum = two_body_momentum(parent, recoil, math.sqrt(invariant))
            phase = 2*parent*momentum*(invariant-low)/(256*math.pi**3*parent**3*invariant)
            result += weight*(end-start)/2*coefficient*abs(scalar)**2*phase
    return result
