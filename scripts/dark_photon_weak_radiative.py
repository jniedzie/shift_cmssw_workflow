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
The separate P6_KIND includes both a1/a2 unitarity-loop corrections. Its
dimensionless compact integrals are independently matched to unreduced
dimensional-regularization tensors, with exact negative-i0 pion cuts.
Unknown higher timelike operators remain outside this declared model.
Measured gamma gamma and gamma ee rates exceed this leading prediction;
none of them is used here to rescale the amplitude. Parent flight, material,
source exposure and BR(A' -> mu+mu-) remain separate.
Four-vectors are (E,px,py,pz), GeV, with metric (+---).
"""
import bisect
import cmath
from array import array
from functools import lru_cache
import json
import math
import numpy as np

from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_meson import MUON_MASS_GEV, boost, dot
from dark_photon_vector_meson import two_body_momentum, mean_lab_beta_gamma as two_body_mean_boost


REFERENCE = 'https://arxiv.org/pdf/hep-ph/9702278'
P6_KIND = 'chpt-p6-unitarity-offshell-vmd-kl-pi0-photon-vector'


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
            'chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector', P6_KIND) or
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
    if model['kind'] in ('chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector', P6_KIND):
        terms = model.get('vmd_exchange')
        if (not isinstance(terms, list) or not terms or
                not model.get('vmd_reference') or
                model.get('full_p6_loop_model') is not (model['kind'] == P6_KIND)):
            raise ValueError('Explicit off-shell VMD inputs and incomplete-p6 scope required')
        for term in terms:
            _number(term.get('mass_gev'), 'vector exchange mass', positive=True)
            coupling = term.get('coupling_gev_minus2')
            if (term['mass_gev'] <= model['parent_mass_gev'] or type(coupling) not in (int, float)
                    or not math.isfinite(coupling)):
                raise ValueError('Finite signed VMD coupling and an above-kaon vector pole required')
    if model['kind'] == P6_KIND:
        current = model.get('unitarity_model', {})
        if (current.get('kind') != 'kl-three-pion-polynomial-msbar' or
                not current.get('reference') or current.get('unqualified_higher_operators') is not True):
            raise ValueError('Explicit polynomial-loop model, subtraction convention and remaining operators required')
        for key in ('a1_gev_minus4', 'a2_gev_minus4'):
            value = current.get(key)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Finite signed weak polynomial coefficient required')
        _number(current.get('renormalization_scale_gev'), 'loop subtraction scale', positive=True)
        order = current.get('quadrature_order')
        if type(order) is not int or not 16 <= order <= 128:
            raise ValueError('Explicit bounded unitarity-loop quadrature required')


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


def _cut_moments(a, change, scale, maximum=4):
    """Integrals of t^n/D and t^n log(D/scale^2), D=a-change*t-i0.

    Integrating the second Feynman parameter analytically preserves the pion
    cut without a finite imaginary regulator or quadrature across a pole.
    """
    ratio = change/a
    if abs(ratio) < .2:
        logarithm = cmath.log(complex(a/scale**2, -0.))
        inverse = [sum(ratio**j/(n+j+1) for j in range(32))/a
                   for n in range(maximum+1)]
        logs = [logarithm/(n+1)-sum(ratio**j/(j*(n+j+1)) for j in range(1, 32))
                for n in range(maximum+1)]
        return inverse, logs
    first = cmath.log(complex(a/scale**2, -0.))
    last = cmath.log(complex((a-change)/scale**2, -0.))
    inverse = [(first-last)/change]
    for n in range(1, maximum+2):
        inverse.append(a/change*inverse[-1]-1/(change*n))
    logs = [(last+change*inverse[n+1])/(n+1) for n in range(maximum+1)]
    return inverse[:maximum+1], logs


@lru_cache(maxsize=4096)
def _unitarity_integrals(z, q, pion, scale, order):
    """Dimensionless Eq.(32)--(37) integrals, all momenta in units of mK.

    These normalization choices are independently checked against the
    unreduced dimensional-regularization tensors of Eqs.(24)--(27).
    """
    squared = pion**2
    def panels(invariant):
        if invariant <= 4*squared:
            return [0., .5, 1.]
        beta = math.sqrt(1-4*squared/invariant)
        return sorted(set([0., (1-beta)/2, .5, (1+beta)/2, 1.]))
    def nodes(edges):
        for low, high in zip(edges, edges[1:]):
            for node, weight in _gauss_legendre(order):
                angle = math.pi*(node+1)/2
                yield low+(high-low)*math.sin(angle/2)**2, weight*(high-low)*math.pi/4*math.sin(angle)
    logarithms = {(1,1):0j, (0,1):0j, (0,2):0j, (1,0):0j, (0,0):0j}
    inverses = {(3,1):0j, (2,1):0j, (2,2):0j, (1,1):0j, (1,2):0j, (1,3):0j}
    third = 0j
    for first, weight in nodes(sorted(set(panels(z)+panels(q)))):
        length = 1-first
        a, change = squared-first*(1-first)*q, first*length*(z-q)
        inverse, logs = _cut_moments(a, change, pion)
        for n, m in logarithms:
            logarithms[n,m] += weight*first**n*length**(m+1)*logs[m]
        for n, m in inverses:
            inverses[n,m] += weight*first**n*length**(m+1)*inverse[m]
        third += weight*length*(a*logs[0]-change*logs[1])
    fourth = fifth = 0j
    for first, weight in nodes(panels(q)):
        a = squared-first*(1-first)*q
        logarithm = cmath.log(complex(a/squared, -0.))
        fourth += weight*a*logarithm
        fifth += weight*(4*first**2-4*first+1)*logarithm
    return logarithms, inverses, third, fourth, fifth, math.log(squared/scale**2)


def unitarity_form_factors(parent, dark, photon, model):
    """A/B/D from the signed a2 polynomial loop, Eqs.(24)--(37).

    Output includes a2*e^2, but not i/(4pi)^2 or the dark-photon mixing.
    The a1 scalar-current term is added separately in amplitude().
    """
    mass = model['parent_mass_gev'];squared = mass**2
    current = model['unitarity_model']
    q = _dot(dark, dark)/squared
    z = (2*_dot(dark, photon)+_dot(dark, dark))/squared
    x1, x2 = _dot(parent, dark)/squared, _dot(parent, photon)/squared
    i1, i2, i3, i4, i5, logarithm = _unitarity_integrals(z, q,
        model['charged_loop_pion_mass_gev']/mass,
        current['renormalization_scale_gev']/mass, current['quadrature_order'])
    first = 16*(2*(1-2*(x1+x2))*i1[1,1]+x1*i1[0,1]+x2*(2*i1[0,2]-i1[0,1]+i1[1,0]))
    first -= 32*((2*x1*x1-x1*(z+q))*(-i2[3,1]+i2[2,1])+
        (2*x1*x2-x1*(z-q)/2-x2*(z+q)/2)*(2*i2[2,2]+i2[1,1]-i2[2,1]-i2[1,2])+
        (2*x2*x2-x2*(z-q))*(i2[1,2]-i2[1,3]))
    first += 4*logarithm/3
    second = -32*i3+16*i4+4*(z-q)*(-1+logarithm)/3
    third = (-second/2+16*(2*x2-(z-q)/2)*(2*i1[1,1]-i1[0,1])+
        16*(2*(x1-x2)-q)*(i1[1,0]-i1[0,0]/2)+4*(2*x1-(z+q)/2)*i5)
    coefficient = current['a2_gev_minus4']*squared*(4*math.pi*model['alpha_em'])
    return tuple(coefficient*value for value in (first, second, third))


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
    if model['kind'] == P6_KIND:
        invariant = vector_mass**2+2*product
        polynomial = ((model['parent_mass_gev']**2+model['recoil_pion_mass_gev']**2-invariant)*
                      (invariant-2*model['charged_loop_pion_mass_gev']**2))
        scalar += (model['unitarity_model']['a1_gev_minus4']/model['g8_gev_minus2']*
                   polynomial*scalar_loop(invariant, vector_mass**2, model['charged_loop_pion_mass_gev']))
    tensor = (_dot(dark_polarization, photon_polarization)-
        _dot(dark_polarization, photon)*_dot(photon_polarization, dark)/product)
    coefficient = -1j*epsilon*2*(4*math.pi*model['alpha_em'])*model['g8_gev_minus2']/(16*math.pi**2)
    result = coefficient*scalar*tensor
    if model['kind'] in ('chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector', P6_KIND):
        if parent is None:
            raise ValueError('Off-shell vector exchange requires the parent four-vector')
        a, b, d = vmd_form_factors(parent, dark, photon, model)
        if model['kind'] == P6_KIND:
            correction = unitarity_form_factors(parent, dark, photon, model)
            a, b, d = (value+loop/(4*math.pi)**2 for value, loop in zip((a,b,d), correction))
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
    if model['kind'] not in ('chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector', P6_KIND):
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
    if model['kind'] in ('chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector', P6_KIND):
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


def _resolved_model(parent_mass, model, *, energy=None, native_context=None, expected_p4=None):
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
    if native_context is not None:
        from dark_photon_kaon import native_source_mass_precision
        report=native_source_mass_precision(parent_mass,native_context,
            expected_p4=expected_p4,expected_pdg_id=inputs['parent_pdg_id'])
        if any(abs(value-reference)>numerical+64*math.ulp(reference)
               for value in native_context['native_record_mass_support_gev']):
            raise ValueError('Native table support exceeds the declared weak-current pole interval')
        allowance=max(allowance,numerical+report['mass_allowance_gev'])
    if abs(parent_mass-reference) > allowance:
        raise ValueError('Native kaon mass differs from the declared weak-current pole')
    return _integration_model(parent_mass,inputs)


def _integration_model(parent_mass, inputs):
    """Same current at a quadrature/control mass; not a source-admission gate."""
    result=dict(inputs,parent_mass_gev=parent_mass)
    domain=inputs['valid_vector_mass_range_gev']
    result['valid_vector_mass_range_gev']=[domain[0],min(domain[1],parent_mass-inputs['recoil_pion_mass_gev'])]
    return result


def _parent(parent, model, native_context=None):
    if len(parent) != 4 or not all(math.isfinite(value) for value in parent) or parent[0] <= 0:
        raise ValueError('Finite future-directed kaon four-vector required')
    squared = math.fsum((parent[0]**2, *(-value**2 for value in parent[1:])))
    if squared <= 0:
        raise ValueError('Timelike kaon required')
    return _resolved_model(math.sqrt(squared), model, energy=parent[0],
        native_context=native_context,expected_p4=parent)


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
    # These coefficients depend on momenta, not on polarization. Evaluating
    # six copies of the same pion-loop tensors dominated the source rate.
    # Keep amplitude() as an independent, unreduced polarization control.
    product=_dot(dark,photon)
    current_invariant=mass**2+2*product
    scalar=reduced_loop_current(current_invariant,mass,model)
    if model['kind']==P6_KIND:
        polynomial=((model['parent_mass_gev']**2+model['recoil_pion_mass_gev']**2-current_invariant)*
                    (current_invariant-2*model['charged_loop_pion_mass_gev']**2))
        scalar+=(model['unitarity_model']['a1_gev_minus4']/model['g8_gev_minus2']*
            polynomial*scalar_loop(current_invariant,mass**2,model['charged_loop_pion_mass_gev']))
    leading=-1j*2*(4*math.pi*model['alpha_em'])*model['g8_gev_minus2']/(16*math.pi**2)*scalar
    a=b=d=0j
    if model['kind'] in ('chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector',P6_KIND):
        a,b,d=vmd_form_factors(kaon,dark,photon,model)
        if model['kind']==P6_KIND:
            correction=unitarity_form_factors(kaon,dark,photon,model)
            a,b,d=(value+loop/(4*math.pi)**2 for value,loop in zip((a,b,d),correction))
    pq,pk=_dot(kaon,dark),_dot(kaon,photon)
    def polarized(first,second):
        eq,ek=_dot(first,dark),_dot(first,photon)
        fq,fp=_dot(second,dark),_dot(second,kaon)
        ep,ef=_dot(first,kaon),_dot(first,second)
        result=leading*(ef-ek*fq/product)
        return result+1j*(a*(ek*fq-product*ef)+
            b*(pq*pk/product*ef+ep*fp-pq/product*ek*fp-pk/product*fq*ep)+
            d*(mass**2*pk/product*ef-pk/product*eq*fq+eq*fp-mass**2/product*ek*fp))
    currents=[[polarized(first,second) for second in transverse] for first in polarizations]
    if mass == 0:
        currents.append([0j, 0j])
    return tuple(tuple(sum(a*b.conjugate() for a, b in zip(first, second))
                       for second in currents) for first in currents)


def _density(invariant, cosine, mass, model):
    parent, pion = model['parent_mass_gev'], model['recoil_pion_mass_gev']
    if invariant <= mass**2 or invariant >= (parent-pion)**2:
        return 0.
    if model['kind'] in ('chpt-p4-plus-offshell-vmd-kl-pi0-photon-vector', P6_KIND):
        density = polarization_density(invariant, cosine, mass, model)
        trace = sum(density[i][i].real for i in range(3))
    else:
        scalar = reduced_loop_current(invariant, mass, model)
        coefficient = 2*(4*math.pi*model['alpha_em'])*model['g8_gev_minus2']/(16*math.pi**2)
        trace = 2*abs(coefficient*scalar)**2
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


@lru_cache(maxsize=256)
def _source_width(mass, model_key):
    """Fixed-source rates repeat across collisions; never redo their integral."""
    return decay_width(json.loads(model_key), mass, epsilon=.001)/1.e-6


def branching_coefficient(parent_mass, mass, model, *, native_energy_gev=None, native_context=None):
    """BR(KL -> pi0 gamma A') / epsilon_em^2; dimuon BR is separate."""
    inputs = _resolved_model(parent_mass, model, energy=native_energy_gev,native_context=native_context)
    width = model.get('total_parent_width_gev')
    _number(width, 'total kaon width', positive=True)
    _model(inputs,mass)
    key = json.dumps(inputs, sort_keys=True, separators=(',', ':'), allow_nan=False)
    table=_native_mass_table(parent_mass,mass,model)
    value=(table.width(parent_mass) if table is not None else _source_width(mass,key))/width
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


def sample_decay(parent, mass, proper_ctau_mm, rng, *, model, native_context=None):
    """Unconditional correlated KL -> pi0 gamma A' -> pi0 gamma mu+mu-."""
    inputs = _parent(parent, model,native_context=native_context)
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


@lru_cache(maxsize=128)
def _rest_spectrum(mass,key,order):
    """Positive rest-frame quadrature reused for changing laboratory boosts.

    Retain the exact old quadrature order, weights and recoil masses. Only
    the analytic overall-rotation integral depends on the laboratory boost.
    """
    inputs=json.loads(key);parent_mass=inputs['parent_mass_gev']
    recoils,weights=array('d'),array('d')
    for low, high in zip(_s_edges(mass, inputs), _s_edges(mass, inputs)[1:]):
        for node, weight in _gauss_legendre(order):
            invariant = (high+low)/2+(high-low)*node/2
            for cosine, angular_weight in _gauss_legendre(order):
                weight_here = weight*(high-low)/2*angular_weight*_density(invariant, cosine, mass, inputs)
                kaon, dark, _, _ = _pair_frame(invariant, cosine, mass, inputs)
                dark = boost(dark, tuple(-x/kaon[0] for x in kaon[1:]))
                recoil_mass = math.sqrt(max(0., parent_mass**2+mass**2-2*parent_mass*dark[0]))
                if not math.isfinite(weight_here) or weight_here<0:
                    raise ValueError('Invalid positive weak-radiative quadrature weight')
                recoils.append(recoil_mass);weights.append(weight_here)
    if math.fsum(weights)<=0:
        raise ValueError('Closed weak radiative phase space')
    return recoils,weights


def _angular_means(parent_mass,gamma,beta_gamma,mass,recoils):
    """Vectorized form of the unchanged isotropic two-body angular integral."""
    recoils=np.frombuffer(recoils,dtype=np.float64)
    factors=(parent_mass-recoils-mass,parent_mass+recoils+mass,
             parent_mass-recoils+mass,parent_mass+recoils-mass)
    if np.any(factors[0]<=0):
        raise ValueError('Closed vector-to-pseudoscalar radiative channel')
    momentum=np.sqrt(factors[0]*factors[1]*factors[2]*factors[3])/(2*parent_mass)
    center=gamma*np.hypot(momentum,mass);half_range=beta_gamma*momentum
    radial=np.sqrt(np.maximum(0.,(center-mass)*(center+mass)))
    if beta_gamma==0:return radial/mass
    small=half_range<1.e-3*radial*radial/center
    means=np.empty_like(recoils)
    means[small]=(radial[small]-mass**2*half_range[small]**2/(6*radial[small]**3))/mass
    def primitive(energy):
        if np.any(mass-energy>8*np.spacing(np.maximum(center[~small],mass))):
            raise ValueError('The angular endpoint is below its mass shell')
        energy=np.maximum(energy,mass)
        momentum=np.sqrt((energy-mass)*(energy+mass));u=np.arcsinh(momentum/mass)
        result=.5*(energy*momentum-mass**2*u)
        tiny=u<1.e-3;z=u[tiny]
        result[tiny]=mass**2*z**3*(1/3+z*z*(1/15+z*z*(2/315+z*z/2835)))
        return result
    means[~small]=(primitive(center[~small]+half_range[~small])-
        primitive(center[~small]-half_range[~small]))/(2*half_range[~small]*mass)
    if np.any(~np.isfinite(means)) or np.any(means<0):
        raise ValueError('Invalid vector-meson angular mean boost')
    return means


def _mean_at_mass(integration_mass,angular_mass,gamma,beta_gamma,mass,key,order):
    inputs=_integration_model(integration_mass,json.loads(key))
    spectrum_key=json.dumps(inputs,sort_keys=True,separators=(',',':'),allow_nan=False)
    recoils,weights=_rest_spectrum(mass,spectrum_key,order)
    angular=_angular_means(angular_mass,gamma,beta_gamma,mass,recoils)
    weights=np.frombuffer(weights,dtype=np.float64)
    return float(np.dot(weights,angular))/math.fsum(weights)


class NativeMassTable:
    """Checked local numerical interpolation, without changing any momentum.

    The original admission guard still decides whether a mass is allowed.
    A narrow auxiliary interval covers its declared native-table allowance
    and ordinary binary64 jitter. Three mass nodes define the quadratic;
    four independent interior widths and eight boosts check it to5e-8.
    Source rates retain64-point quadrature and default means32-point
    quadrature. This interpolation budget is much smaller than the existing
    1e-3 native-precision completion gate. It is not a physics uncertainty.
    """
    tolerance=5.e-8
    maximum_beta_gamma=1.e5

    def __init__(self,mass,key):
        inputs=json.loads(key);self.center=inputs['parent_mass_gev']
        self.radius=min(max(2*inputs.get('pole_mass_numerical_tolerance_gev',0.),
            1.e-7*self.center),1.e-4*(self.center-inputs['recoil_pion_mass_gev']-mass))
        self.mass,self.key=mass,key
        self.masses=tuple(self.center+offset*self.radius for offset in (-1.,0.,1.))
        def width(value):
            current=_integration_model(value,inputs)
            return _source_width(mass,json.dumps(current,sort_keys=True,separators=(',',':'),allow_nan=False))
        self.widths=tuple(width(value) for value in self.masses)
        controls=(-.75,-.5,.5,.75);self.width_error=self.mean_error=0.
        for offset in controls:
            value=self.center+offset*self.radius;exact=width(value)
            self.width_error=max(self.width_error,abs(self.width(value)/exact-1))
            for beta_gamma in (0.,.001,.1,1.,10.,100.,1000.,self.maximum_beta_gamma):
                gamma=math.hypot(1.,beta_gamma)
                means=self.means(value,value,gamma,beta_gamma)
                exact=_mean_at_mass(value,value,gamma,beta_gamma,mass,key,32)
                self.mean_error=max(self.mean_error,abs(self.interpolate(value,means)/exact-1))
        self.valid=max(self.width_error,self.mean_error)<=self.tolerance

    def interpolate(self,value,values):
        x=(value-self.center)/self.radius
        return values[1]+x*(values[2]-values[0])/2+x*x*(values[2]+values[0]-2*values[1])/2

    def width(self,value):return self.interpolate(value,self.widths)

    def means(self,integration_mass,angular_mass,gamma,beta_gamma):
        # The two legacy invariant evaluations can differ by binary64
        # roundoff. Carry that same difference through every auxiliary node;
        # do not boost a neighbouring mass's recoil as a different particle.
        difference=angular_mass-integration_mass
        return tuple(_mean_at_mass(value,value+difference,gamma,beta_gamma,self.mass,self.key,32)
                     for value in self.masses)


@lru_cache(maxsize=12)
def _native_table(mass,key):
    try:
        table=NativeMassTable(mass,key)
    except ValueError as error:
        # Auxiliary nodes must not introduce a new validity requirement.
        # Unknown model errors still raise; these known numerical/endpoint
        # failures use the exact requested mass and its unchanged checks.
        if str(error) not in ('Closed weak radiative phase space',
                'Closed vector-to-pseudoscalar radiative channel',
                'The angular endpoint is below its mass shell',
                'Invalid vector-meson angular mean boost',
                'Invalid positive weak-radiative quadrature weight'):
            raise
        return None
    return table if table.valid else None


def _native_mass_table(parent_mass,mass,model):
    inputs=_inputs(model);pole=inputs['parent_mass_gev']
    gap=pole-inputs['recoil_pion_mass_gev']-mass
    if mass<=2*MUON_MASS_GEV or gap<=0 or parent_mass==pole:return None
    radius=min(max(2*inputs.get('pole_mass_numerical_tolerance_gev',0.),1.e-7*pole),1.e-4*gap)
    if radius<=64*math.ulp(pole) or abs(parent_mass-pole)>radius:return None
    key=json.dumps(inputs,sort_keys=True,separators=(',',':'),allow_nan=False)
    return _native_table(mass,key)


def mean_lab_beta_gamma(parent, mass, model, *, quadrature_order=32, native_context=None):
    """Rate-weighted mean A' |p|/m; no parent or A' flight is conditioned."""
    inputs=_parent(parent,model,native_context=native_context);_model(inputs,mass)
    if mass<=2*MUON_MASS_GEV:raise ValueError('Open dimuon channel required')
    if type(quadrature_order) is not int or quadrature_order<8:
        raise ValueError('Require an explicit quadrature order >=8')
    # Preserve the product-based invariant used by two_body_mean_boost,
    # independently of the pow-based invariant in the original parent gate.
    squared=math.fsum([parent[0]*parent[0]]+[-x*x for x in parent[1:]])
    angular_mass=math.sqrt(squared)
    gamma=parent[0]/angular_mass
    beta_gamma=math.sqrt(math.fsum(x*x for x in parent[1:]))/angular_mass
    table=(_native_mass_table(inputs['parent_mass_gev'],mass,model)
           if quadrature_order==32 and beta_gamma<=NativeMassTable.maximum_beta_gamma else None)
    if table is not None:
        return table.interpolate(inputs['parent_mass_gev'],table.means(
            inputs['parent_mass_gev'],angular_mass,gamma,beta_gamma))
    key=json.dumps(inputs,sort_keys=True,separators=(',',':'),allow_nan=False)
    return _mean_at_mass(inputs['parent_mass_gev'],angular_mass,gamma,beta_gamma,mass,key,quadrature_order)
