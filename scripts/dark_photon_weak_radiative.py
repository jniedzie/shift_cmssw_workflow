# Code style: AI-code. AI-generated leading weak neutral-kaon radiative current.
"""Correlated KL -> pi0 gamma A' current and declared-model decay sampler.

Donoghue and Gabbiani hep-ph/9702278 Eqs.(6)--(15) give the charged-pion and
charged-kaon loops with one off-shell photon. The Feynman-parameter integral
fixes I=[m^2 DeltaF-q^2 DeltaG/2]/(s-q^2). The factor 1/2 follows from the
symmetry integral (1-x)log(...)=1/2 integral log(...); it is also necessary
for the soft-real-photon limit. Tests compare to the original double integral.

The complex pion cut and both loops are retained. Optional off-shell rho/omega
exchange follows Eqs.(38)--(41), rather than an on-shell a_V rescaling. P4
plus that exchange is not the full O(p6) loop amplitude or a rate envelope.
Measured gamma gamma and gamma ee rates exceed this leading prediction;
none of them is used here to rescale the amplitude. Parent flight, material,
source exposure and BR(A' -> mu+mu-) remain separate.
Four-vectors are (E,px,py,pz), GeV, with metric (+---).
"""
import bisect
from functools import lru_cache
import json
import math

from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_meson import MUON_MASS_GEV, boost, dot
from dark_photon_vector_meson import two_body_momentum, mean_lab_beta_gamma as two_body_mean_boost


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
    if (model.get('kind') not in ('chpt-p4-kl-pi0-photon-vector',
            'chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector') or
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
    if model['kind'] == 'chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector':
        terms = model.get('vmd_exchange')
        if (not isinstance(terms, list) or not terms or
                not model.get('vmd_reference') or model.get('full_p6_loop_model') is not False):
            raise ValueError('Explicit off-shell VMD inputs and incomplete-p6 scope required')
        for term in terms:
            _number(term.get('mass_gev'), 'vector exchange mass', positive=True)
            coupling = term.get('coupling_gev_minus2')
            if (term['mass_gev'] <= model['parent_mass_gev'] or type(coupling) not in (int, float)
                    or not math.isfinite(coupling)):
                raise ValueError('Finite signed VMD coupling and an above-kaon vector pole required')


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
              epsilon, model, *, parent=None):
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
    result = coefficient*scalar*tensor
    if model['kind'] == 'chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector':
        if parent is None:
            raise ValueError('Off-shell vector exchange requires the parent four-vector')
        a, b, d = vmd_form_factors(parent, dark, photon, model)
        pq, pk = _dot(parent, dark), _dot(parent, photon)
        eq, ek = _dot(dark_polarization, dark), _dot(dark_polarization, photon)
        fq, fp = _dot(photon_polarization, dark), _dot(photon_polarization, parent)
        ep, ef = _dot(dark_polarization, parent), _dot(dark_polarization, photon_polarization)
        first = ek*fq-product*ef
        second = pq*pk/product*ef+ep*fp-pq/product*ek*fp-pk/product*fq*ep
        third = vector_mass**2*pk/product*ef-pk/product*eq*fq+eq*fp-vector_mass**2/product*ek*fp
        # Eq.(7) is i times the A tensor in Eq.(28). Keep that same overall
        # phase for Eqs.(38)--(40), including their physical signed couplings.
        result += 1j*epsilon*(a*first+b*second+d*third)
    return result


def vmd_form_factors(parent, dark, photon, model):
    """Attributed off-shell A/B/D tree terms, hep-ph/9702278 Eqs.(38)--(41)."""
    for vector in (parent, dark, photon):
        if len(vector) != 4 or not all(math.isfinite(value) for value in vector):
            raise ValueError('Three finite physical four-vectors required')
    _model(model, math.sqrt(max(0., _dot(dark, dark))))
    if model['kind'] != 'chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector':
        raise ValueError('Explicit off-shell VMD model required')
    parent_squared, pq, pk, product = _dot(parent, parent), _dot(parent, dark), _dot(parent, photon), _dot(dark, photon)
    if parent_squared <= 0 or min(pq, pk, product) <= 0:
        raise ValueError('Open future-directed vector exchange phase space required')
    first = second = third = 0.
    for term in model['vmd_exchange']:
        pole, coupling = term['mass_gev']**2, term['coupling_gev_minus2']
        real_denominator = parent_squared-2*pk-pole
        virtual_denominator = parent_squared+_dot(dark, dark)-2*pq-pole
        if max(real_denominator, virtual_denominator) >= 0:
            raise ValueError('Vector exchange must stay below its declared poles')
        first -= coupling*((parent_squared-pk)/real_denominator+(parent_squared-pq)/virtual_denominator)
        second -= coupling*product*(1/real_denominator+1/virtual_denominator)
        third += coupling*product/virtual_denominator
    return first, second, third


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
    if model['kind'] == 'chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector':
        return _integrated_width(model, vector_mass, epsilon, quadrature_order)
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


def _inputs(model):
    return model.get('weak_radiative_model', model)


def _resolved_model(parent_mass, model, *, energy=None):
    """Allow propagated native shell roundoff, never a physical mass scan."""
    _number(parent_mass, 'parent mass', positive=True)
    inputs = dict(_inputs(model))
    reference = inputs['parent_mass_gev']
    numerical = inputs.get('pole_mass_numerical_tolerance_gev', 0.)
    _number(numerical, 'declared numerical pole tolerance')
    if numerical > 1.e-5:
        raise ValueError('A numerical pole tolerance cannot define broad kaon support')
    allowance = numerical+64*math.ulp(reference)
    if energy is not None:
        _number(energy, 'native parent energy', positive=True)
        allowance += 64*math.ulp(max(energy**2, reference**2))/reference
    if abs(parent_mass-reference) > allowance:
        raise ValueError('Native kaon mass differs from the declared weak-current pole')
    inputs['parent_mass_gev'] = parent_mass
    domain = inputs['valid_vector_mass_range_gev']
    inputs['valid_vector_mass_range_gev'] = [domain[0], min(domain[1], parent_mass-inputs['recoil_pion_mass_gev'])]
    return inputs


def _parent(parent, model):
    if len(parent) != 4 or not all(math.isfinite(value) for value in parent) or parent[0] <= 0:
        raise ValueError('Finite future-directed kaon four-vector required')
    squared = math.fsum((parent[0]**2, *(-value**2 for value in parent[1:])))
    if squared <= 0:
        raise ValueError('Timelike kaon required')
    return _resolved_model(math.sqrt(squared), model, energy=parent[0])


def _pair_frame(invariant, cosine, mass, model):
    """A' is +z in the photon-pair frame; the kaon defines the xz plane."""
    root, parent, pion = math.sqrt(invariant), model['parent_mass_gev'], model['recoil_pion_mass_gev']
    momentum = (invariant-mass**2)/(2*root)
    dark = (math.hypot(momentum, mass), 0., 0., momentum)
    photon = (momentum, 0., 0., -momentum)
    energy = (parent**2+invariant-pion**2)/(2*root)
    kaon_momentum = two_body_momentum(parent, pion, root)*parent/root
    kaon = (energy, kaon_momentum*math.sqrt(max(0., 1-cosine**2)), 0., kaon_momentum*cosine)
    recoil = tuple(a-b-c for a, b, c in zip(kaon, dark, photon))
    return kaon, dark, photon, recoil


def polarization_density(invariant, cosine, mass, model):
    """Positive A' spin matrix in its rest frame, before epsilon squared.

    The z axis is the dark momentum in the photon-pair frame. Returning a
    Gram matrix retains transverse/longitudinal interference for VMD terms.
    """
    model = _inputs(model)
    _model(model, mass)
    if not math.isfinite(cosine) or not -1 <= cosine <= 1:
        raise ValueError('Physical kaon helicity cosine required')
    if not mass**2 < invariant < (model['parent_mass_gev']-model['recoil_pion_mass_gev'])**2:
        raise ValueError('Open photon-pair phase space required')
    kaon, dark, photon, _ = _pair_frame(invariant, cosine, mass, model)
    transverse = [(0., 1., 0., 0.), (0., 0., 1., 0.)]
    polarizations = transverse+([(dark[3]/mass, 0., 0., dark[0]/mass)] if mass else [])
    currents = [[amplitude(dark, photon, first, second, mass, .001, model, parent=kaon)*1000
                 for second in transverse] for first in polarizations]
    if mass == 0:
        currents.append([0j, 0j])
    return tuple(tuple(sum(a*b.conjugate() for a, b in zip(first, second))
                       for second in currents) for first in currents)


def _density(invariant, cosine, mass, model):
    parent, pion = model['parent_mass_gev'], model['recoil_pion_mass_gev']
    if invariant <= mass**2 or invariant >= (parent-pion)**2:
        return 0.
    scalar = reduced_loop_current(invariant, mass, model)
    coefficient = 2*(4*math.pi*model['alpha_em'])*model['g8_gev_minus2']/(16*math.pi**2)
    trace = 2*abs(coefficient*scalar)**2
    if model['kind'] == 'chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector':
        density = polarization_density(invariant, cosine, mass, model)
        trace = sum(density[i][i].real for i in range(3))
    phase = 2*parent*two_body_momentum(parent, pion, math.sqrt(invariant))*(invariant-mass**2)/invariant
    return phase*trace


def _s_edges(mass, model):
    low, high = mass**2, (model['parent_mass_gev']-model['recoil_pion_mass_gev'])**2
    threshold = 4*model['charged_loop_pion_mass_gev']**2
    return [low]+([threshold] if low < threshold < high else [])+[high]


def _angular_density(invariant, mass, model, order=16):
    if model['kind'] == 'chpt-p4-kl-pi0-photon-vector':
        return 2*_density(invariant, 0., mass, model)
    return sum(weight*_density(invariant, node, mass, model) for node, weight in _gauss_legendre(order))


def _integrated_width(model, mass, epsilon, order):
    total = 0.
    for low, high in zip(_s_edges(mass, model), _s_edges(mass, model)[1:]):
        for node, weight in _gauss_legendre(order):
            invariant = (high+low)/2+(high-low)*node/2
            total += weight*(high-low)/2*_angular_density(invariant, mass, model, order)
    return epsilon**2*total/(512*math.pi**3*model['parent_mass_gev']**3)


def branching_coefficient(parent_mass, mass, model):
    """BR(KL -> pi0 gamma A') / epsilon_em^2; dimuon BR is separate."""
    inputs = _resolved_model(parent_mass, model)
    width = model.get('total_parent_width_gev')
    _number(width, 'total kaon width', positive=True)
    value = decay_width(inputs, mass, epsilon=.001)/1.e-6/width
    if not math.isfinite(value) or value < 0:
        raise ValueError('Invalid weak radiative branching coefficient')
    return value


def _integral(function, low, high, order=8):
    return (high-low)/2*sum(weight*function((high+low)/2+(high-low)*node/2)
                            for node, weight in _gauss_legendre(order))


def _transformed_density(coordinate, low, high, mass, model):
    angle = math.pi*coordinate/2
    invariant = low+(high-low)*math.sin(angle)**2
    jacobian = (high-low)*math.pi*math.sin(angle)*math.cos(angle)
    return jacobian*_angular_density(invariant, mass, model)


@lru_cache(maxsize=128)
def _sampling_segments(mass, key):
    model = json.loads(key);edges = _s_edges(mass, model)
    segments, cumulative = [], [0.]
    for low, high in zip(edges, edges[1:]):
        for index in range(32):
            a, b = index/32, (index+1)/32
            weight = _integral(lambda x: _transformed_density(x, low, high, mass, model), a, b)
            segments.append((low, high, a, b));cumulative.append(cumulative[-1]+weight)
    if cumulative[-1] <= 0:
        raise ValueError('Closed weak radiative current')
    return tuple(segments), tuple(cumulative)


def _invert_integral(function, low, high, target):
    start = low
    for _ in range(36):
        middle = (low+high)/2
        if _integral(function, start, middle) < target:
            low = middle
        else:
            high = middle
    return (low+high)/2


def _sample_invariants(mass, model, rng):
    key = json.dumps(model, sort_keys=True, separators=(',', ':'), allow_nan=False)
    segments, cumulative = _sampling_segments(mass, key)
    uniform = rng.random()*cumulative[-1]
    index = min(len(segments)-1, bisect.bisect_right(cumulative, uniform)-1)
    low, high, a, b = segments[index]
    coordinate = _invert_integral(lambda x: _transformed_density(x, low, high, mass, model),
                                  a, b, uniform-cumulative[index])
    invariant = low+(high-low)*math.sin(math.pi*coordinate/2)**2
    if model['kind'] == 'chpt-p4-kl-pi0-photon-vector':
        return invariant, 2*rng.random()-1
    function = lambda cosine: _density(invariant, cosine, mass, model)
    cosine = _invert_integral(function, -1., 1., rng.random()*_integral(function, -1., 1., 16))
    return invariant, cosine


def _isotropic(rng):
    cosine, phi = 2*rng.random()-1, 2*math.pi*rng.random()
    sine = math.sqrt(max(0., 1-cosine*cosine))
    return sine*math.cos(phi), sine*math.sin(phi), cosine


def _cross(first, second):
    return (first[1]*second[2]-first[2]*second[1], first[2]*second[0]-first[0]*second[2],
            first[0]*second[1]-first[1]*second[0])


def sample_decay(parent, mass, proper_ctau_mm, rng, *, model):
    """Unconditional correlated KL -> pi0 gamma A' -> pi0 gamma mu+mu-."""
    inputs = _parent(parent, model)
    _model(inputs, mass);_number(proper_ctau_mm, 'proper lifetime', positive=True)
    if mass <= 2*MUON_MASS_GEV or mass >= inputs['parent_mass_gev']-inputs['recoil_pion_mass_gev']:
        raise ValueError('Open radiative and dimuon channels required')
    invariant, cosine = _sample_invariants(mass, inputs, rng)
    kaon, dark, photon, recoil = _pair_frame(invariant, cosine, mass, inputs)
    density = polarization_density(invariant, cosine, mass, inputs)
    trace = sum(density[i][i].real for i in range(3))
    beta_mu_squared = 1-4*MUON_MASS_GEV**2/mass**2
    while True:
        direction = _isotropic(rng)
        projection = sum(direction[i]*density[i][j].real*direction[j] for i in range(3) for j in range(3))
        probability = 1-beta_mu_squared*projection/trace
        if not -1.e-12 <= probability <= 1+1.e-12:
            raise RuntimeError('Nonphysical weak radiative polarization density')
        if rng.random() <= probability:
            break
    muon_momentum = math.sqrt((mass/2-MUON_MASS_GEV)*(mass/2+MUON_MASS_GEV))
    dark_beta = (0., 0., dark[3]/dark[0])
    muon = boost((mass/2,)+tuple(muon_momentum*x for x in direction), dark_beta)
    antimuon = boost((mass/2,)+tuple(-muon_momentum*x for x in direction), dark_beta)
    pair_beta = tuple(-x/kaon[0] for x in kaon[1:])
    rest = [boost(value, pair_beta) for value in (dark, photon, recoil, muon, antimuon)]
    axis = _isotropic(rng);auxiliary = (0., 0., 1.) if abs(axis[2]) < .9 else (1., 0., 0.)
    first = _cross(axis, auxiliary);length = math.sqrt(dot(first, first));first = tuple(x/length for x in first)
    second = _cross(axis, first);angle = 2*math.pi*rng.random()
    x_axis = tuple(math.cos(angle)*a+math.sin(angle)*b for a, b in zip(first, second))
    y_axis = _cross(axis, x_axis);parent_beta = tuple(x/parent[0] for x in parent[1:])
    def to_lab(value):
        rotated = (value[0],)+tuple(value[1]*x+value[2]*y+value[3]*z for x, y, z in zip(x_axis, y_axis, axis))
        return boost(rotated, parent_beta)
    dark, photon, recoil, muon, antimuon = map(to_lab, rest)
    proper = -proper_ctau_mm*math.log1p(-rng.random())
    return dict(dark_photon_p4=dark, muon_p4=muon, antimuon_p4=antimuon,
        recoil_p4s=[recoil, photon], recoil_pdg_ids=[111, 22], recoil_masses_gev=[inputs['recoil_pion_mass_gev'], 0.],
        dark_photon_mass_gev=mass, muon_mass_gev=MUON_MASS_GEV, proper_decay_length_mm=proper,
        decay_displacement_mm=tuple(proper*x/mass for x in dark[1:]), decay_ct_mm=proper*dark[0]/mass,
        unconditional_decay=True, recoil_natural_decay_required=True, parent_polarization='spin-zero',
        amplitude_model=inputs['kind'], polarization_model='full-massive-vector-density',
        photon_pair_invariant_gev2=invariant, kaon_helicity_cosine=cosine, dimuon_helicity_cosine=direction[2])


def mean_lab_beta_gamma(parent, mass, model, *, quadrature_order=32):
    """Rate-weighted mean A' |p|/m; no parent or A' flight is conditioned."""
    inputs = _parent(parent, model);_model(inputs, mass)
    if mass <= 2*MUON_MASS_GEV:
        raise ValueError('Open dimuon channel required')
    numerator = denominator = 0.;parent_mass = inputs['parent_mass_gev']
    for low, high in zip(_s_edges(mass, inputs), _s_edges(mass, inputs)[1:]):
        for node, weight in _gauss_legendre(quadrature_order):
            invariant = (high+low)/2+(high-low)*node/2
            for cosine, angular_weight in _gauss_legendre(quadrature_order):
                weight_here = weight*(high-low)/2*angular_weight*_density(invariant, cosine, mass, inputs)
                kaon, dark, _, _ = _pair_frame(invariant, cosine, mass, inputs)
                dark = boost(dark, tuple(-x/kaon[0] for x in kaon[1:]))
                recoil_mass = math.sqrt(max(0., parent_mass**2+mass**2-2*parent_mass*dark[0]))
                mean = two_body_mean_boost(parent, recoil_mass, mass, polarization='unpolarized')
                numerator += weight_here*mean;denominator += weight_here
    if denominator <= 0:
        raise ValueError('Closed weak radiative phase space')
    return numerator/denominator
