# Code style: AI-code. AI-generated gauge-invariant rho radiative decay kernel.
"""rho0 -> pi+ pi- A', A' -> mu+mu- in a declared scalar-QED benchmark.

Gauging g*rho_mu*(pi- d_mu pi+ - pi+ d_mu pi-) gives both charged-pion
final-state-radiation diagrams and their contact term. With metric (+---),
Delta=p_plus-p_minus, k=p_A and D_+-=2*p_+- . k+k^2,

 T^{mu nu} = (Delta+k)^mu (2*p_plus+k)^nu/D_plus
           - (Delta-k)^mu (2*p_minus+k)^nu/D_minus - 2*g^{mu nu}.

Both k_nu*T^{mu nu} and P_mu*T^{mu nu} vanish for equal pion masses.
All three massive A' polarizations and the unpolarized rho spin average are
retained, including their correlation with the subsequent dimuon decay.
The normalization is to Gamma(rho -> pi pi)=g^2*M*beta_pi^3/(48*pi).
The measured cut photon rate is an independent cross-check, not a fitted
normalization. A massless photon requires a positive rest-frame energy cut.

This pointlike pion FSR/contact model is a benchmark, not a complete hadronic
amplitude. Structure-dependent, pion-rescattering and timelike form-factor
terms are not inferred from agreement with the real-photon rate. See the
gauged rho-pi-pi term in hep-ph/9903217 Eq.(4). All input values, assumptions
and provenance must be supplied by the model card. Only a pole-mass rho and
an explicitly unpolarized parent are supported; no broad-rho continuation is
provided. Source exposure and BR(A' -> mu mu) belong to the collision source.

Four-vectors are (E,px,py,pz), GeV. Decay displacements are (x,y,z,ct), mm.
"""
import bisect
from functools import lru_cache
import json
import math

from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_meson import MUON_MASS_GEV, boost, dot
from dark_photon_vector_meson import mean_lab_beta_gamma as two_body_mean_boost
from dark_photon_vector_meson import two_body_momentum

NATIVE_KINEMATIC_RATE_PRECISION_LIMIT = 1.e-3


def _positive(value, name, *, allow_zero=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or (value < 0 if allow_zero else value <= 0)):
        raise ValueError('Explicit finite physical '+name+' required')
    return value


def _model_key(model):
    inputs = model.get('rho_radiative_model', model)
    key = json.dumps(inputs, sort_keys=True, separators=(',', ':'), allow_nan=False)
    _validated_model(key)
    return key


@lru_cache(maxsize=64)
def _validated_model(key):
    model = json.loads(key)
    if model.get('kind') != 'gauged-derivative-scalar-qed' or not model.get('reference'):
        raise ValueError('Explicit rho scalar-QED model and reference required')
    for name in ('alpha', 'pion_mass_gev', 'reference_parent_mass_gev',
                 'pole_mass_numerical_tolerance_gev'):
        _positive(model.get(name), name)
    if model['pole_mass_numerical_tolerance_gev'] > 1.e-5:
        raise ValueError('A numerical rho mass tolerance cannot define broad physical support')
    if model['reference_parent_mass_gev'] <= 2*model['pion_mass_gev']:
        raise ValueError('The reference rho must be above the two-pion threshold')
    if model.get('parent_polarization') != 'unpolarized':
        raise ValueError('An explicitly unpolarized rho is required')
    if model.get('parent_mass_continuation') != 'zero-width-pole':
        raise ValueError('Only the declared zero-width rho pole is supported')
    domain = model.get('valid_mass_range_gev')
    if (not isinstance(domain, list) or len(domain) != 2
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in domain)
            or not 0 <= domain[0] < domain[1]
            or domain[1] > model['reference_parent_mass_gev']-2*model['pion_mass_gev']+1.e-12):
        raise ValueError('Explicit physical rho transition mass domain required')
    normalization = model.get('normalization', {})
    branching = normalization.get('branching_fraction')
    if (normalization.get('channel') != 'pipi' or not normalization.get('reference')
            or type(branching) not in (int, float) or not math.isfinite(branching)
            or not 0 < branching <= 1):
        raise ValueError('Explicit rho -> pi pi branching normalization required')
    if not model.get('assumption') or model.get('signal_rate_validated') is not False:
        raise ValueError('The provisional scalar-QED signal-rate assumption must remain explicit')
    return model


def _check_parent_mass(parent_mass, model, *, kinematic_energy=None,
                       native_mass_gev=None, native_mass_tolerance=None):
    _positive(parent_mass, 'parent mass')
    reference = model['reference_parent_mass_gev']
    if native_mass_gev is not None or native_mass_tolerance is not None:
        _positive(native_mass_gev, 'native pole mass')
        _positive(native_mass_tolerance, 'unchanged native Pythia mass tolerance')
        _positive(kinematic_energy, 'native four-vector energy')
        if abs(native_mass_gev-reference) > 64*math.ulp(reference):
            raise ValueError('The generated native rho mass must remain at its zero-width pole')
        deviation = abs(parent_mass-native_mass_gev)/max(kinematic_energy, 1.)
        if deviation > native_mass_tolerance:
            raise ValueError('Native rho four-vector violates the unchanged Pythia mass tolerance')
        # Pythia can leave a small shell residual exceeding local arithmetic
        # roundoff even with its particle mass fixed at the pole. This path
        # accepts only explicit native context; its rate effect is checked
        # independently below. It never defines a physical broad-rho source.
        return
    # The source fixes the native rho mass at its pole. Reconstructing its
    # invariant from a fast native four-vector loses a few floating-point
    # digits. This allowance covers that cancellation, not an off-shell model.
    allowance = model['pole_mass_numerical_tolerance_gev']+64*math.ulp(reference)
    if kinematic_energy is not None:
        allowance += 64*math.ulp(kinematic_energy*kinematic_energy)/reference
    if abs(parent_mass-reference) > allowance:
        raise ValueError('Off-shell rho masses are unsupported by the pole-only model')


def _check_mass(mass, model):
    _positive(mass, 'dark-photon mass')
    low, high = model['valid_mass_range_gev']
    if not low <= mass <= high:
        raise ValueError('Requested mass lies outside the rho transition-model domain')


def _minkowski(first, second):
    return math.fsum((first[0]*second[0], *(-a*b for a, b in zip(first[1:], second[1:]))))


def current_tensor(positive, negative, dark):
    """Return T^{mu nu}; useful for independent covariant Ward-identity audits."""
    if any(len(vector) != 4 or not all(math.isfinite(x) for x in vector)
           for vector in (positive, negative, dark)):
        raise ValueError('Three finite four-vectors are required')
    virtuality = _minkowski(dark, dark)
    denominators = (2*_minkowski(positive, dark)+virtuality,
                    2*_minkowski(negative, dark)+virtuality)
    if min(denominators) <= 0:
        raise ValueError('Physical positive pion-radiation denominators required')
    difference = tuple(a-b for a, b in zip(positive, negative))
    return tuple(tuple(
        (difference[mu]+dark[mu])*(2*positive[nu]+dark[nu])/denominators[0]
        -(difference[mu]-dark[mu])*(2*negative[nu]+dark[nu])/denominators[1]
        -(2 if mu == 0 else -2) * (mu == nu)
        for nu in range(4)) for mu in range(4))


def _density_components(s, cosine, parent_mass, mass, pion_mass):
    """Positive spin-density factors in the rho rest frame, k along +z.

    After boosting to the A' rest frame, the three rho polarization currents
    are rows (Bxx,0,L*Bxz), (0,2,0), (Bzx,0,L*Bzz), with L=m_A/E_A.
    Their Gram matrix is the physical A' polarization density. This avoids
    subtracting large timelike terms and retains the massive longitudinal
    component. For a real photon L=0 leaves its two transverse states.
    """
    beta2 = max(0., 1-4*pion_mass*pion_mass/s)
    beta = math.sqrt(beta2)
    energy = (parent_mass*parent_mass+mass*mass-s)/(2*parent_mass)
    recoil_energy = parent_mass-energy
    momentum = two_body_momentum(parent_mass, math.sqrt(s), mass)
    denominator = energy*energy-momentum*momentum*beta2*cosine*cosine
    if denominator <= 0:
        raise ValueError('Nonpositive scalar-QED radiation denominator')
    transverse = math.sqrt(s)*beta*math.sqrt(max(0., 1-cosine*cosine))
    longitudinal = recoil_energy*beta*cosine
    first = 2*energy/(parent_mass*denominator)
    second = 2*beta*cosine*(parent_mass*energy-mass*mass)/(parent_mass*denominator)
    bxx = 2+transverse*transverse*first
    bxz = transverse*longitudinal*first
    bzx = transverse*second
    bzz = 2+longitudinal*second
    longitudinal_factor = mass/energy
    return (bxx, longitudinal_factor*bxz, bzx, longitudinal_factor*bzz)


def _matrix_squared(s, cosine, parent_mass, mass, pion_mass):
    return (4+math.fsum(x*x for x in
            _density_components(s, cosine, parent_mass, mass, pion_mass)))/3


def matrix_element_squared(s, cosine, parent_mass, mass, model):
    """Spin-averaged |M|²/(e² epsilon² g_rho-pipi²), without phase space."""
    inputs = _validated_model(_model_key(model))
    _check_parent_mass(parent_mass, inputs)
    _positive(mass, 'vector mass', allow_zero=True)
    if not math.isfinite(cosine) or not -1 <= cosine <= 1:
        raise ValueError('Physical pion helicity cosine required')
    if not math.isfinite(s) or not 4*inputs['pion_mass_gev']**2 <= s <= (parent_mass-mass)**2:
        raise ValueError('Physical dipion mass squared required')
    return _matrix_squared(s, cosine, parent_mass, mass, inputs['pion_mass_gev'])


def _phase_density(s, cosine, parent_mass, mass, pion_mass):
    low, high = 4*pion_mass*pion_mass, (parent_mass-mass)**2
    if s <= low or s >= high:
        return 0.
    beta = math.sqrt(1-low/s)
    momentum = two_body_momentum(parent_mass, math.sqrt(s), mass)
    return momentum*beta*_matrix_squared(s, cosine, parent_mass, mass, pion_mass)


def _rate_prefactor(parent_mass, model):
    beta = math.sqrt(1-4*model['pion_mass_gev']**2/parent_mass**2)
    return 3*model['alpha']/(4*math.pi*parent_mass**3*beta**3)


@lru_cache(maxsize=256)
def _quadrature(parent_mass, mass, key, count=64, photon_cut=0.):
    model = _validated_model(key)
    low = 4*model['pion_mass_gev']**2
    high = ((parent_mass-mass)**2 if mass else
            parent_mass*parent_mass-2*parent_mass*photon_cut)
    if high <= low:
        return ()
    if mass == 0 and photon_cut <= 0:
        raise ValueError('The massless photon rate requires a positive rest-frame energy cut')
    nodes = []
    # A sin² coordinate removes the physical square-root endpoints. Positive
    # helicity cosine suffices because the rate is charge-conjugation even.
    for node, weight in _gauss_legendre(count):
        theta = (node+1)*math.pi/4
        sine, cosine = math.sin(theta), math.cos(theta)
        s = low+(high-low)*sine*sine
        jacobian = 2*(high-low)*sine*cosine*math.pi/4
        angular = math.fsum(w*_phase_density(s, (x+1)/2, parent_mass, mass,
                          model['pion_mass_gev']) for x, w in _gauss_legendre(count//2))
        # Transforming cos(theta_pi) from [-1,1] to [0,1] contributes 1/2;
        # the equal negative-cosine half supplies the compensating factor 2.
        nodes.append((s, weight*jacobian*angular))
    return tuple(nodes)


@lru_cache(maxsize=256)
def _integral(parent_mass, mass, key, count=64, photon_cut=0.):
    return math.fsum(weight for _, weight in _quadrature(parent_mass, mass, key, count, photon_cut))


def _exact_coefficient(parent_mass, mass, key):
    model = _validated_model(key)
    if mass >= parent_mass-2*model['pion_mass_gev']:
        return 0.
    return (model['normalization']['branching_fraction']*_rate_prefactor(parent_mass, model)
            *_integral(parent_mass, mass, key))


@lru_cache(maxsize=128)
def _rate_interpolation(mass, key):
    """Qualify a tiny numerical pole interval once, independently of events.

    Comparing a native pole mass with the invariant reconstructed from its
    large lab four-vector is a numerical test, not a physical broad-rho model.
    Exact quadrature for every slightly different invariant would dominate
    inclusive source generation. Nine exact nodes preserve the nonzero mass
    sensitivity. Twenty-four independent quarter/midpoint evaluations check
    linear interpolation to 1e-10 relative. Close to a threshold, or if this
    check fails, the caller falls back to exact quadrature without caching an
    unchecked interpolation. The model records the allowed numerical interval.
    """
    model = _validated_model(key)
    nominal = model['reference_parent_mass_gev']
    tolerance = model['pole_mass_numerical_tolerance_gev']
    if nominal-2*model['pion_mass_gev']-mass <= 4*tolerance:
        return dict(qualified=False, reason='near-closed-endpoint')
    masses = tuple(nominal-tolerance+index*tolerance/4 for index in range(9))
    values = tuple(_exact_coefficient(parent_mass, mass, key) for parent_mass in masses)
    differences = []
    for index in range(8):
        for fraction in (.25, .5, .75):
            parent_mass = masses[index]+fraction*(masses[index+1]-masses[index])
            exact = _exact_coefficient(parent_mass, mass, key)
            linear = values[index]+fraction*(values[index+1]-values[index])
            differences.append(abs(linear/exact-1))
    maximum = max(differences)
    return dict(qualified=maximum <= 1.e-10, reason='quarter-midpoint-convergence',
                nodes=masses, coefficients=values, independent_checks=len(differences),
                maximum_checked_relative_error=maximum, required_relative_error=1.e-10,
                nominal_coefficient=values[4],
                endpoint_relative_shifts=(values[0]/values[4]-1, values[-1]/values[4]-1))


def rate_interpolation_validation(mass, model):
    """Compact receipt for the numerical pole-mass comparison acceleration."""
    key = _model_key(model)
    _check_mass(mass, _validated_model(key))
    return dict(_rate_interpolation(mass, key))


def _interpolated_or_exact_coefficient(parent_mass, mass, key):
    interpolation = _rate_interpolation(mass, key)
    if interpolation['qualified']:
        nodes = interpolation['nodes']
        if nodes[0] <= parent_mass <= nodes[-1]:
            index = min(7, max(0, bisect.bisect_right(nodes, parent_mass)-1))
            fraction = (parent_mass-nodes[index])/(nodes[index+1]-nodes[index])
            values = interpolation['coefficients']
            return values[index]+fraction*(values[index+1]-values[index])
    return _exact_coefficient(parent_mass, mass, key)


def branching_coefficient(parent_mass, mass, model, *, native_mass_gev=None,
                          native_energy_gev=None, native_mass_tolerance=None):
    """BR(rho -> pi pi A')/epsilon²; BR(A' -> mu mu) is excluded.

    A physical rate uses the native zero-width pole mass. The optional native
    context is only for the separate precision diagnostic: preserve Pythia's
    actual four-vector, evaluate its kinematic rate with exact quadrature when
    outside the tiny interpolation band, and require its relative rate shift
    to stay below the explicit 0.1% numerical-accuracy target. The source must
    still use the pole coefficient for its physical probability. Agreement
    with the measured real-photon branching fraction is not this tolerance.
    """
    key = _model_key(model)
    inputs = _validated_model(key)
    context = any(value is not None for value in
                  (native_mass_gev, native_energy_gev, native_mass_tolerance))
    if context and any(value is None for value in
                       (native_mass_gev, native_energy_gev, native_mass_tolerance)):
        raise ValueError('Complete native pole, energy and Pythia tolerance context required')
    _check_parent_mass(parent_mass, inputs, kinematic_energy=native_energy_gev,
                       native_mass_gev=native_mass_gev, native_mass_tolerance=native_mass_tolerance)
    _positive(mass, 'dark-photon mass')
    if context and ((parent_mass > mass+2*inputs['pion_mass_gev']) !=
                    (native_mass_gev > mass+2*inputs['pion_mass_gev'])):
        raise ValueError('Native rho precision changes whether the rare decay is open')
    if mass >= parent_mass-2*inputs['pion_mass_gev']:
        return 0.
    _check_mass(mass, inputs)
    coefficient = _interpolated_or_exact_coefficient(parent_mass, mass, key)
    if context:
        nominal = _interpolated_or_exact_coefficient(native_mass_gev, mass, key)
        if abs(coefficient/nominal-1) > NATIVE_KINEMATIC_RATE_PRECISION_LIMIT:
            raise ValueError('Native rho mass-shell effect exceeds the 0.1% numerical rate target')
    return coefficient


def photon_branching_above_cut(cut_energy_gev, model):
    """Independent real-photon BR for E_gamma>cut in the rho rest frame."""
    _positive(cut_energy_gev, 'photon energy cut')
    key = _model_key(model)
    inputs = _validated_model(key)
    parent_mass = inputs['reference_parent_mass_gev']
    return (inputs['normalization']['branching_fraction']*_rate_prefactor(parent_mass, inputs)
            *_integral(parent_mass, 0., key, photon_cut=cut_energy_gev))


def mean_lab_beta_gamma(parent, mass, model, *, native_mass_gev=None, native_mass_tolerance=None):
    """Exact isotropic orientation mean, integrated over the dipion spectrum."""
    key = _model_key(model)
    inputs = _validated_model(key)
    parent_mass = _parent_mass(parent, inputs, signal_mass=mass,
                              native_mass_gev=native_mass_gev, native_mass_tolerance=native_mass_tolerance)
    _check_mass(mass, inputs)
    if mass <= 2*MUON_MASS_GEV or mass >= parent_mass-2*inputs['pion_mass_gev']:
        raise ValueError('Open dipion and dimuon channels required')
    # The physical source rho is the declared pole. Reuse that spectrum for
    # different lab four-vectors; their tiny invariant differences are a native
    # numerical precision issue, checked separately in source accounting. The
    # existing exact angular mean still uses the supplied lab four-vector.
    nodes = _quadrature(inputs['reference_parent_mass_gev'], mass, key)
    if (abs(parent_mass-inputs['reference_parent_mass_gev']) > inputs['pole_mass_numerical_tolerance_gev']
            or nodes[-1][0] >= (parent_mass-mass)**2):
        # Extremely close to the endpoint, an allowed native roundoff can
        # close the last pole quadrature point. Recompute its actual support
        # rather than dropping that point or clipping its recoil momentum.
        nodes = _quadrature(parent_mass, mass, key)
    normalization = math.fsum(weight for _, weight in nodes)
    return math.fsum(weight*two_body_mean_boost(parent, math.sqrt(s), mass,
                     polarization='unpolarized') for s, weight in nodes)/normalization


def _cell_bound(low_s, high_s, low_c, high_c, parent_mass, mass, pion_mass):
    """Analytic upper bound on p_A*beta_pi*|T|² for a phase-space rectangle."""
    energy_high = (parent_mass**2+mass**2-low_s)/(2*parent_mass)
    energy_low = (parent_mass**2+mass**2-high_s)/(2*parent_mass)
    momentum_high = math.sqrt(max(0., (energy_high-mass)*(energy_high+mass)))
    beta_high = math.sqrt(max(0., 1-4*pion_mass**2/high_s))
    velocity_high = momentum_high/energy_high
    denominator_low = energy_low**2*(1-velocity_high**2*beta_high**2*high_c**2)
    if denominator_low <= 0:
        raise RuntimeError('No finite scalar-QED rejection bound')
    transverse_high = math.sqrt(high_s)*beta_high*math.sqrt(max(0., 1-low_c*low_c))
    longitudinal_high = (parent_mass-energy_low)*beta_high*high_c
    first_high = 2*energy_high/(parent_mass*denominator_low)
    second_high = (2*beta_high*high_c*(parent_mass*energy_high-mass*mass)
                   /(parent_mass*denominator_low))
    components = (2+transverse_high**2*first_high,
                  mass/energy_low*transverse_high*longitudinal_high*first_high,
                  transverse_high*second_high,
                  mass/energy_low*(2+longitudinal_high*second_high))
    return momentum_high*beta_high*(4+math.fsum(x*x for x in components))/3*(1+2.e-12)


@lru_cache(maxsize=128)
def _envelope(parent_mass, mass, key):
    inputs = _validated_model(key)
    low = 4*inputs['pion_mass_gev']**2
    extent = (parent_mass-mass)**2-low
    if extent <= 0:
        raise ValueError('Closed rho dipion channel')
    cells, cumulative = [], [0.]
    for s_index in range(64):
        low_s, high_s = low+extent*s_index/64, low+extent*(s_index+1)/64
        for c_index in range(16):
            low_c, high_c = c_index/16, (c_index+1)/16
            bound = _cell_bound(low_s, high_s, low_c, high_c, parent_mass,
                                mass, inputs['pion_mass_gev'])
            cells.append((low_s, high_s, low_c, high_c, bound))
            cumulative.append(cumulative[-1]+(high_s-low_s)*(high_c-low_c)*bound)
    return tuple(cells), tuple(cumulative)


def _sample_invariants(parent_mass, mass, key, rng):
    cells, cumulative = _envelope(parent_mass, mass, key)
    pion_mass = _validated_model(key)['pion_mass_gev']
    while True:
        index = min(len(cells)-1, bisect.bisect_right(cumulative, rng.random()*cumulative[-1])-1)
        low_s, high_s, low_c, high_c, bound = cells[index]
        s = low_s+(high_s-low_s)*rng.random()
        cosine = low_c+(high_c-low_c)*rng.random()
        value = _phase_density(s, cosine, parent_mass, mass, pion_mass)
        if value > bound*(1+1.e-10):
            raise RuntimeError('Scalar-QED rejection-envelope bound violated')
        if rng.random()*bound <= value:
            return s, cosine if rng.random() < .5 else -cosine


def _isotropic(rng):
    cosine, phi = 2*rng.random()-1, 2*math.pi*rng.random()
    sine = math.sqrt(max(0., 1-cosine*cosine))
    return sine*math.cos(phi), sine*math.sin(phi), cosine


def _cross(first, second):
    return (first[1]*second[2]-first[2]*second[1],
            first[2]*second[0]-first[0]*second[2],
            first[0]*second[1]-first[1]*second[0])


def _parent_mass(parent, model, *, signal_mass=None, native_mass_gev=None, native_mass_tolerance=None):
    if len(parent) != 4 or not all(math.isfinite(x) for x in parent) or parent[0] <= 0:
        raise ValueError('A finite timelike parent four-vector is required')
    invariant = _minkowski(parent, parent)
    if invariant <= 0:
        raise ValueError('The rho parent must be timelike')
    result = math.sqrt(invariant)
    _check_parent_mass(result, model, kinematic_energy=parent[0],
                       native_mass_gev=native_mass_gev, native_mass_tolerance=native_mass_tolerance)
    if native_mass_gev is not None or native_mass_tolerance is not None:
        branching_coefficient(result, signal_mass, model, native_mass_gev=native_mass_gev,
                              native_energy_gev=parent[0], native_mass_tolerance=native_mass_tolerance)
    return result


def sample_decay(parent, mass, proper_ctau_mm, rng, *, model,
                 native_mass_gev=None, native_mass_tolerance=None):
    """Sample an unconditional correlated rho -> pi pi A' -> pi pi mu mu."""
    key = _model_key(model)
    inputs = _validated_model(key)
    parent_mass = _parent_mass(parent, inputs, signal_mass=mass,
                              native_mass_gev=native_mass_gev, native_mass_tolerance=native_mass_tolerance)
    _positive(proper_ctau_mm, 'proper decay length')
    _check_mass(mass, inputs)
    pion_mass = inputs['pion_mass_gev']
    if not 2*MUON_MASS_GEV < mass < parent_mass-2*pion_mass:
        raise ValueError('Open dipion and dimuon channels required')
    s, cosine = _sample_invariants(parent_mass, mass, key, rng)
    bxx, bxz, bzx, bzz = _density_components(s, cosine, parent_mass, mass, pion_mass)
    trace = bxx*bxx+bxz*bxz+4+bzx*bzx+bzz*bzz
    beta_mu2 = 1-4*MUON_MASS_GEV**2/mass**2
    while True:
        direction = _isotropic(rng)
        projection = ((bxx*direction[0]+bxz*direction[2])**2
                      +4*direction[1]**2+(bzx*direction[0]+bzz*direction[2])**2)
        probability = 1-beta_mu2*projection/trace
        if not 0 <= probability <= 1+1.e-12:
            raise RuntimeError('Nonphysical correlated dimuon spin density')
        if rng.random() <= probability:
            break
    momentum = two_body_momentum(parent_mass, math.sqrt(s), mass)
    dark_energy = math.hypot(momentum, mass)
    pair_energy = parent_mass-dark_energy
    pion_momentum = math.sqrt(s/4-pion_mass*pion_mass)
    sine = math.sqrt(max(0., 1-cosine*cosine))
    pion_axis = (sine, 0., cosine)
    pair_beta = (0., 0., -momentum/pair_energy)
    positive = boost((math.sqrt(s)/2,)+tuple(pion_momentum*x for x in pion_axis), pair_beta)
    negative = boost((math.sqrt(s)/2,)+tuple(-pion_momentum*x for x in pion_axis), pair_beta)
    dark = (dark_energy, 0., 0., momentum)
    muon_momentum = math.sqrt(mass*mass/4-MUON_MASS_GEV**2)
    dark_beta = (0., 0., momentum/dark_energy)
    muon = boost((mass/2,)+tuple(muon_momentum*x for x in direction), dark_beta)
    antimuon = boost((mass/2,)+tuple(-muon_momentum*x for x in direction), dark_beta)
    # An isotropic A' axis plus a uniform rotation of the pion plane covers
    # the complete SO(3) orientation for the unpolarized parent.
    axis = _isotropic(rng)
    auxiliary = (0., 0., 1.) if abs(axis[2]) < .9 else (1., 0., 0.)
    first = _cross(axis, auxiliary)
    length = math.sqrt(dot(first, first))
    first = tuple(x/length for x in first)
    second = _cross(axis, first)
    angle = 2*math.pi*rng.random()
    x_axis = tuple(math.cos(angle)*a+math.sin(angle)*b for a, b in zip(first, second))
    y_axis = _cross(axis, x_axis)
    parent_beta = tuple(x/parent[0] for x in parent[1:])

    def to_lab(vector):
        rotated = (vector[0],)+tuple(vector[1]*a+vector[2]*b+vector[3]*c
                                    for a, b, c in zip(x_axis, y_axis, axis))
        return boost(rotated, parent_beta)

    dark, positive, negative, muon, antimuon = map(to_lab, (dark, positive, negative, muon, antimuon))
    proper_length = -proper_ctau_mm*math.log1p(-rng.random())
    return dict(dark_photon_p4=dark, muon_p4=muon, antimuon_p4=antimuon,
                recoil_p4s=[positive, negative], recoil_pdg_ids=[211, -211],
                recoil_masses_gev=[pion_mass, pion_mass], dark_photon_mass_gev=mass,
                muon_mass_gev=MUON_MASS_GEV, proper_decay_length_mm=proper_length,
                decay_displacement_mm=tuple(proper_length*x/mass for x in dark[1:]),
                decay_ct_mm=proper_length*dark[0]/mass, unconditional_decay=True,
                recoil_natural_decay_required=True, parent_polarization='unpolarized',
                amplitude_model='gauged-derivative-scalar-qed',
                polarization_model='full-massive-vector-density')
