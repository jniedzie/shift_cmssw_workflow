# Code style: AI-code. AI-generated coherent pseudoscalar dipion decay kernel.
"""P -> pi+ pi- A', A' -> mu+mu- with the magnetic electromagnetic current.

The current is J^mu=epsilon^{mu nu alpha beta} p+_nu p-_alpha q_beta F(s,q²).
It is conserved and linearly polarizes A' normal to the pion plane. Decaying
an independently sampled transverse A' would lose the measured plane relation.

The coherent contact/rho/omega benchmark is BESIII arXiv:2402.01993 Eqs.4-7,
11, Table4 (eta-prime), and arXiv:2501.10130 Eqs.4-5 (eta). The two printed
dipion propagators contain dimensionally inconsistent m²*Gamma; this code
uses the conventional dimensionally correct m*Gamma. The virtual propagator
uses the hadronic pi-pi threshold in its running width, independent of which
lepton observes the electromagnetic current. This explicit model choice is
not a precision dispersive form factor or a claim of inclusive completeness.

All branching fractions and amplitude parameters are caller-supplied, with
provenance. Normalization can use a measured real-photon or Dalitz branching
fraction. The virtual/current model should be cross-checked against the other
measured modes before use. BR(A' -> mu+mu-) is deliberately excluded here.
Do not additionally generate eta-prime -> rho A': the rho is already coherent
with the contact and omega amplitudes in this same pi+ pi- A' final state.

Four-vectors are (E,px,py,pz), GeV. Displacements are (x,y,z,ct), mm.
The narrow-parent continuation fixes the total parent width to its reference
pole value. It is not a model of a broad parent's off-shell branching ratio.
"""
import bisect
import cmath
from functools import lru_cache
import json
import math

from dark_photon_meson import MUON_MASS_GEV, boost, dot
from dark_photon_vector_meson import two_body_momentum, mean_lab_beta_gamma as two_body_mean_boost


def _finite_positive(value, name, allow_zero=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (
            value < 0 if allow_zero else value <= 0):
        raise ValueError('Explicit finite physical '+name+' required')
    return value


def _model_key(model):
    model = model.get('dipion_model', model)
    key = json.dumps(model, sort_keys=True, separators=(',', ':'), allow_nan=False)
    _validated_model(key)
    return key


@lru_cache(maxsize=64)
def _validated_model(key):
    model = json.loads(key)
    if model.get('kind') != 'besiii-coherent-magnetic-dipion' or not model.get('reference'):
        raise ValueError('Explicit coherent dipion model and reference required')
    for name in ('pion_mass_gev', 'reference_parent_mass_gev', 'rho_mass_gev',
                 'rho_width_gev', 'virtual_rho_mass_gev'):
        _finite_positive(model.get(name), name)
    if model['rho_mass_gev'] <= 2*model['pion_mass_gev']:
        raise ValueError('The rho pole must lie above its two-pion threshold')
    if model['virtual_rho_mass_gev'] <= 2*model['pion_mass_gev']:
        raise ValueError('The virtual rho pole must lie above its two-pion threshold')
    if model['reference_parent_mass_gev'] <= 2*model['pion_mass_gev']:
        raise ValueError('The reference parent must lie above its two-pion threshold')
    for name in ('c1_minus_c2', 'c3'):
        value = model.get(name)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Explicit finite coherent amplitude parameter required')
    if model.get('virtual_width_prescription') != 'hadronic-pion-running-width':
        raise ValueError('An explicit universal hadronic virtual-width prescription is required')
    if model.get('parent_mass_continuation') != 'fixed-pole-total-width':
        raise ValueError('Explicit narrow-parent fixed-width continuation required')
    omega = model.get('omega_mixing')
    if omega is not None:
        for name in ('mass_gev', 'width_gev'):
            _finite_positive(omega.get(name), 'omega '+name)
        _finite_positive(omega.get('amplitude'), 'omega mixing amplitude', allow_zero=True)
        if not math.isfinite(omega.get('phase_radians', math.nan)):
            raise ValueError('Explicit finite rho-omega mixing phase required')
    normalization = model.get('normalization', {})
    branching = normalization.get('branching_fraction')
    if (type(branching) not in (int, float) or not math.isfinite(branching)
            or not 0 < branching <= 1 or not normalization.get('reference')):
        raise ValueError('Explicit measured normalization branching fraction required')
    if normalization.get('channel') not in ('pipi-gamma', 'pipi-lepton-pair'):
        raise ValueError('Unsupported dipion normalization channel')
    if normalization['channel'] == 'pipi-lepton-pair':
        _finite_positive(normalization.get('lepton_mass_gev'), 'normalization lepton mass')
        _finite_positive(normalization.get('alpha'), 'normalization alpha')
        if model['reference_parent_mass_gev'] <= 2*(model['pion_mass_gev']+normalization['lepton_mass_gev']):
            raise ValueError('Closed measured Dalitz normalization channel')
    return model


def running_rho_width(s, pole_mass, pole_width, pion_mass):
    """P-wave pi-pi width, BESIII Eq.7; zero below the hadronic threshold."""
    threshold = 4*pion_mass*pion_mass
    if s <= threshold:
        return 0.
    return pole_width*math.sqrt(s)/pole_mass*((1-threshold/s)/(1-threshold/pole_mass**2))**1.5


def _rho(s, pole, model):
    width = running_rho_width(s, pole, model['rho_width_gev'], model['pion_mass_gev'])
    return pole*pole/complex(pole*pole-s, -pole*width)


def _amplitude(s, q2, model):
    pion_propagator = _rho(s, model['rho_mass_gev'], model)
    omega = model.get('omega_mixing')
    if omega:
        pole = omega['mass_gev']
        pion_propagator += omega['amplitude']*cmath.exp(1j*omega['phase_radians'])*(
            pole*pole/complex(pole*pole-s, -pole*omega['width_gev']))
    virtual = _rho(q2, model['virtual_rho_mass_gev'], model)
    difference, c3 = model['c1_minus_c2'], model['c3']
    return (1-.75*(difference+c3)+.75*(difference-c3)*virtual
            +1.5*c3*pion_propagator*virtual)


def magnetic_form_factor(s, q2, model):
    """Complex coherent amplitude; never sum the component rates."""
    if not all(math.isfinite(x) and x >= 0 for x in (s, q2)):
        raise ValueError('Finite nonnegative invariant masses squared required')
    return _amplitude(s, q2, _validated_model(_model_key(model)))


def _spectrum(s, parent_mass, mass, model):
    threshold = 4*model['pion_mass_gev']**2
    if s <= threshold or s >= (parent_mass-mass)**2:
        return 0.
    # Factoring lambda protects the endpoint against cancellation.
    lam = (parent_mass-math.sqrt(s)-mass)*(parent_mass+math.sqrt(s)+mass)*(
        parent_mass-math.sqrt(s)+mass)*(parent_mass+math.sqrt(s)-mass)
    return s*(1-threshold/s)**1.5*max(0., lam)**1.5*abs(_amplitude(s, mass*mass, model))**2


def dipion_spectrum(s, parent_mass, mass, model):
    """Unnormalized dGamma/ds times M³; pion angles have been integrated."""
    if not all(math.isfinite(x) and x >= 0 for x in (s, parent_mass, mass)) or parent_mass == 0:
        raise ValueError('Finite physical dipion invariants required')
    return _spectrum(s, parent_mass, mass, _validated_model(_model_key(model)))


def _adaptive_simpson(function, low, high, absolute=1.e-13, relative=2.e-9):
    # Start with 32 panels so a narrow omega feature cannot fall between all
    # initial points. Each panel then has its own adaptive error estimate.
    def recurse(a, b, fa, fm, fb, whole, tolerance, depth):
        middle = (a+b)/2
        first, second = (a+middle)/2, (middle+b)/2
        ffirst, fsecond = function(first), function(second)
        left = (middle-a)*(fa+4*ffirst+fm)/6
        right = (b-middle)*(fm+4*fsecond+fb)/6
        delta = left+right-whole
        if abs(delta) <= 15*tolerance:
            return left+right+delta/15
        if depth == 0:
            raise RuntimeError('Dipion normalization quadrature did not converge')
        return (recurse(a, middle, fa, ffirst, fm, left, tolerance/2, depth-1)
                +recurse(middle, b, fm, fsecond, fb, right, tolerance/2, depth-1))
    panels = []
    for index in range(32):
        a = low+(high-low)*index/32
        b = low+(high-low)*(index+1)/32
        fa, fm, fb = function(a), function((a+b)/2), function(b)
        whole = (b-a)*(fa+4*fm+fb)/6
        panels.append((a, b, fa, fm, fb, whole))
    estimate = math.fsum(row[-1] for row in panels)
    tolerance = max(absolute, relative*abs(estimate))/32
    return math.fsum(recurse(*row, tolerance, 18) for row in panels)


@lru_cache(maxsize=2048)
def _integral(parent_mass, mass, key):
    model = _validated_model(key)
    low = 4*model['pion_mass_gev']**2
    high = (parent_mass-mass)**2
    if parent_mass <= mass+2*model['pion_mass_gev']:
        return 0.
    return _adaptive_simpson(lambda s: _spectrum(s, parent_mass, mass, model), low, high)


@lru_cache(maxsize=64)
def _normalization_integral(key):
    model = _validated_model(key)
    parent_mass = model['reference_parent_mass_gev']
    normalization = model['normalization']
    if normalization['channel'] == 'pipi-gamma':
        return _integral(parent_mass, 0., key)/parent_mass**3
    return _dalitz_integral(parent_mass, normalization['lepton_mass_gev'], normalization['alpha'], key)


@lru_cache(maxsize=128)
def _dalitz_integral(parent_mass, lepton_mass, alpha, key, angular_term='rate'):
    model = _validated_model(key)
    if parent_mass <= 2*(model['pion_mass_gev']+lepton_mass):
        return 0.
    lower = math.log(4*lepton_mass*lepton_mass)
    upper = math.log((parent_mass-2*model['pion_mass_gev'])**2)

    def integrand(unit):
        # q² starts at the lepton threshold with beta ~ sqrt(log(q²/q²min)).
        # Squaring the integration coordinate removes that endpoint cusp.
        log_q2 = lower+(upper-lower)*unit*unit
        q2 = math.exp(log_q2)
        beta2 = max(0., 1-4*lepton_mass*lepton_mass/q2)
        rate = (2*(upper-lower)*unit*alpha/(3*math.pi)*math.sqrt(beta2)*
                (1+2*lepton_mass*lepton_mass/q2)*_integral(parent_mass, math.sqrt(q2), key)/parent_mass**3)
        if angular_term == 'plane-constant':
            return rate/(1-beta2/3)
        if angular_term == 'plane-sine-squared':
            return rate*(-2*beta2/3)/(1-beta2/3)
        return rate
    return _adaptive_simpson(integrand, 0., 1., relative=2.e-7)


def predicted_dalitz_branching(lepton_mass, model, *, alpha=1/137.035999084):
    """Independent lepton-channel cross-check of the chosen normalization."""
    _finite_positive(lepton_mass, 'Dalitz lepton mass')
    _finite_positive(alpha, 'Dalitz alpha')
    key = _model_key(model)
    inputs = _validated_model(key)
    return inputs['normalization']['branching_fraction']*_dalitz_integral(
        inputs['reference_parent_mass_gev'], lepton_mass, alpha, key)/_normalization_integral(key)


def dalitz_plane_coefficient(lepton_mass, model, *, alpha=1/137.035999084):
    """Coefficient a in dGamma/dphi proportional to 1+a*sin²(phi)."""
    _finite_positive(lepton_mass, 'Dalitz lepton mass')
    _finite_positive(alpha, 'Dalitz alpha')
    key = _model_key(model)
    inputs = _validated_model(key)
    parent_mass = inputs['reference_parent_mass_gev']
    constant = _dalitz_integral(parent_mass, lepton_mass, alpha, key, 'plane-constant')
    if constant == 0:
        raise ValueError('Closed Dalitz angular cross-check')
    return _dalitz_integral(parent_mass, lepton_mass, alpha, key, 'plane-sine-squared')/constant


def branching_coefficient(parent_mass, mass, model):
    """BR(P -> pi+pi-A') / epsilon²; excludes the dark-photon muon BR.

    Photon normalization has no identical-photon factor two. For a measured
    Dalitz normalization use dGamma_ll/dq²=alpha/(3pi*q²)*beta_l*(1+2r)*
    Gamma_gamma*(q²), including the hadronic-current form factor in Gamma*.
    With fixed total parent width this coefficient increases with M. Therefore
    evaluating it at the declared upper parent-mass support is a safe bound.
    """
    if not all(math.isfinite(x) for x in (parent_mass, mass)) or parent_mass <= 0 or mass < 0:
        raise ValueError('Finite physical parent and signal masses required')
    key = _model_key(model)
    inputs = _validated_model(key)
    value = _integral(parent_mass, mass, key)/parent_mass**3
    return inputs['normalization']['branching_fraction']*value/_normalization_integral(key)


@lru_cache(maxsize=8)
def _gauss_legendre(count):
    nodes = []
    for index in range(1, (count+1)//2+1):
        root = math.cos(math.pi*(index-.25)/(count+.5))
        for _ in range(30):
            first, second = 1., root
            for order in range(2, count+1):
                first, second = second, ((2*order-1)*root*second-(order-1)*first)/order
            derivative = count*(root*second-first)/(root*root-1)
            change = second/derivative
            root -= change
            if abs(change) < 2.e-15:
                break
        else:
            raise RuntimeError('Gauss-Legendre nodes did not converge')
        weight = 2/((1-root*root)*derivative*derivative)
        nodes.append((-root, weight))
        if count+1-index != index:
            nodes.append((root, weight))
    return tuple(sorted(nodes))


@lru_cache(maxsize=256)
def _moment_nodes(parent_mass, mass, key, count):
    model = _validated_model(key)
    low = 4*model['pion_mass_gev']**2
    extent = (parent_mass-mass)**2-low
    if parent_mass <= mass+2*model['pion_mass_gev']:
        raise ValueError('Closed dipion channel')
    # s=threshold+extent*sin²(theta) removes both phase-space endpoint cusps.
    nodes = []
    for node, weight in _gauss_legendre(count):
        angle = (node+1)*math.pi/4
        sine, cosine = math.sin(angle), math.cos(angle)
        s = low+extent*sine*sine
        value = weight*math.pi/4*2*extent*sine*cosine*_spectrum(s, parent_mass, mass, model)
        nodes.append((math.sqrt(s), value))
    normalization = math.fsum(weight for _, weight in nodes)
    return tuple((recoil, weight/normalization) for recoil, weight in nodes)


def mean_lab_beta_gamma(parent, mass, model):
    """Full angular and dipion-spectrum mean; no detector/position filtering.

    Parent decay is spin-zero, so its A' direction is isotropic for each s.
    Use the exact analytic two-body angular mean at 64 cached quadrature nodes.
    The reference LAB anchor remains the original physical epsilon/lifetime;
    this new mechanism's different mean flight must be recorded separately.
    """
    if len(parent) != 4 or not all(math.isfinite(x) for x in parent) or parent[0] <= 0:
        raise ValueError('A finite timelike parent four-vector is required')
    squared = math.fsum((parent[0]*parent[0], -dot(parent[1:], parent[1:])))
    if squared <= 0 or not math.isfinite(mass) or mass <= 2*MUON_MASS_GEV:
        raise ValueError('Timelike parent and open dimuon channel required')
    key = _model_key(model)
    return math.fsum(weight*two_body_mean_boost(parent, recoil, mass, polarization='unpolarized')
                     for recoil, weight in _moment_nodes(math.sqrt(squared), mass, key, 64))


def _rho_interval_bound(low, high, pole, model):
    distance = max(low-pole*pole, pole*pole-high, 0.)
    width = running_rho_width(low, pole, model['rho_width_gev'], model['pion_mass_gev'])
    denominator = math.hypot(distance, pole*width)
    if denominator == 0:
        raise ValueError('A zero-width resonance has no finite rejection bound')
    return pole*pole/denominator


@lru_cache(maxsize=256)
def _envelope(parent_mass, mass, key):
    model = _validated_model(key)
    low = 4*model['pion_mass_gev']**2
    high = (parent_mass-mass)**2
    virtual = abs(_rho(mass*mass, model['virtual_rho_mass_gev'], model))
    difference, c3 = model['c1_minus_c2'], model['c3']
    base = abs(1-.75*(difference+c3))+.75*abs(difference-c3)*virtual
    edges, bounds, cumulative = [low], [], [0.]
    for index in range(128):
        a, b = low+(high-low)*index/128, low+(high-low)*(index+1)/128
        rho_bound = _rho_interval_bound(a, b, model['rho_mass_gev'], model)
        omega = model.get('omega_mixing')
        if omega:
            pole = omega['mass_gev']
            distance = max(a-pole*pole, pole*pole-b, 0.)
            rho_bound += omega['amplitude']*pole*pole/math.hypot(distance, pole*omega['width_gev'])
        amplitude_bound = base+1.5*abs(c3)*rho_bound*virtual
        lam = max(0., ((parent_mass-mass)**2-a)*((parent_mass+mass)**2-a))
        bound = b*(1-low/b)**1.5*lam**1.5*amplitude_bound**2*(1+1.e-12)
        edges.append(b)
        bounds.append(bound)
        cumulative.append(cumulative[-1]+(b-a)*bound)
    return edges, bounds, cumulative


def _sample_s(parent_mass, mass, key, rng):
    model = _validated_model(key)
    edges, bounds, cumulative = _envelope(parent_mass, mass, key)
    while True:
        index = min(127, bisect.bisect_right(cumulative, rng.random()*cumulative[-1])-1)
        s = edges[index]+rng.random()*(edges[index+1]-edges[index])
        value = _spectrum(s, parent_mass, mass, model)
        if value > bounds[index]*(1+1.e-10):
            raise RuntimeError('Dipion rejection-envelope bound violated')
        if rng.random()*bounds[index] <= value:
            return s


def _cross(first, second):
    return (first[1]*second[2]-first[2]*second[1],
            first[2]*second[0]-first[0]*second[2],
            first[0]*second[1]-first[1]*second[0])


def _unit(vector):
    length = math.sqrt(dot(vector, vector))
    if length == 0:
        raise ValueError('An angular basis axis has zero length')
    return tuple(value/length for value in vector)


def _isotropic(rng):
    cosine = 2*rng.random()-1
    phi = 2*math.pi*rng.random()
    sine = math.sqrt(max(0., 1-cosine*cosine))
    return sine*math.cos(phi), sine*math.sin(phi), cosine


def sample_decay(parent, mass, proper_ctau_mm, rng, *, model):
    """Sample the full correlated magnetic decay, with no acceptance cuts."""
    key = _model_key(model)
    inputs = _validated_model(key)
    if len(parent) != 4 or not all(math.isfinite(x) for x in parent) or parent[0] <= 0:
        raise ValueError('A finite timelike parent four-vector is required')
    parent_squared = math.fsum((parent[0]**2, -dot(parent[1:], parent[1:])))
    if parent_squared <= 0:
        raise ValueError('The parent must be timelike')
    parent_mass = math.sqrt(parent_squared)
    pion_mass = inputs['pion_mass_gev']
    if not 2*MUON_MASS_GEV < mass < parent_mass-2*pion_mass:
        raise ValueError('Closed dipion or dimuon channel')
    _finite_positive(proper_ctau_mm, 'proper decay length')
    s = _sample_s(parent_mass, mass, key, rng)
    axis = _isotropic(rng)
    auxiliary = (0., 0., 1.) if abs(axis[2]) < .9 else (1., 0., 0.)
    first = _unit(_cross(axis, auxiliary))
    second = _cross(axis, first)
    # The pion helicity density is 1-cos²(theta_pi).
    while True:
        cosine = 2*rng.random()-1
        if rng.random() <= 1-cosine*cosine:
            break
    pion_phi = 2*math.pi*rng.random()
    radial = tuple(math.cos(pion_phi)*a+math.sin(pion_phi)*b for a, b in zip(first, second))
    polarization = _unit(_cross(axis, radial))
    sine = math.sqrt(1-cosine*cosine)
    pion_direction = tuple(cosine*a+sine*b for a, b in zip(axis, radial))
    pion_momentum = math.sqrt(s/4-pion_mass*pion_mass)
    positive = (math.sqrt(s)/2,)+tuple(pion_momentum*x for x in pion_direction)
    negative = (math.sqrt(s)/2,)+tuple(-pion_momentum*x for x in pion_direction)
    # A' is linearly polarized normal to the pion plane: 1-beta_mu²(n.e)².
    beta_mu2 = 1-4*MUON_MASS_GEV**2/mass**2
    while True:
        muon_direction = _isotropic(rng)
        if rng.random() <= 1-beta_mu2*dot(muon_direction, polarization)**2:
            break
    muon_momentum = math.sqrt(mass*mass/4-MUON_MASS_GEV**2)
    muon = (mass/2,)+tuple(muon_momentum*x for x in muon_direction)
    antimuon = (mass/2,)+tuple(-muon_momentum*x for x in muon_direction)
    momentum = two_body_momentum(parent_mass, math.sqrt(s), mass)
    dark_energy, dipion_energy = math.hypot(momentum, mass), math.hypot(momentum, math.sqrt(s))
    parent_beta = tuple(x/parent[0] for x in parent[1:])
    dark_beta = tuple(momentum*x/dark_energy for x in axis)
    dipion_beta = tuple(-momentum*x/dipion_energy for x in axis)
    dark = boost((dark_energy,)+tuple(momentum*x for x in axis), parent_beta)
    positive = boost(boost(positive, dipion_beta), parent_beta)
    negative = boost(boost(negative, dipion_beta), parent_beta)
    muon = boost(boost(muon, dark_beta), parent_beta)
    antimuon = boost(boost(antimuon, dark_beta), parent_beta)
    length = -proper_ctau_mm*math.log1p(-rng.random())
    return dict(dark_photon_p4=dark, muon_p4=muon, antimuon_p4=antimuon,
                recoil_p4s=[positive, negative], recoil_pdg_ids=[211, -211],
                recoil_masses_gev=[pion_mass, pion_mass],
                dark_photon_mass_gev=mass, muon_mass_gev=MUON_MASS_GEV,
                proper_decay_length_mm=length,
                decay_displacement_mm=tuple(length*x/mass for x in dark[1:]),
                decay_ct_mm=length*dark[0]/mass, unconditional_decay=True,
                recoil_natural_decay_required=True,
                amplitude_model='coherent-magnetic-dipion',
                polarization_model='linear-normal-to-pion-plane')
