# Code style: AI-code. AI-generated charged-rho pole decay benchmark.
"""rho+- -> pi+- pi0 A' with a declared charged-vector g=2 benchmark.

The effective Lagrangian is
  -1/2 W*_{mu nu} W^{mu nu} + M^2 W*_{mu} W^mu
  + i e epsilon kappa W*_{mu} W_{nu} F^{mu nu}
  + g W*_{mu} [pi0 D^mu pi+ - pi+ partial^mu pi0] + h.c.,
where W_{mu nu}=D_mu W_nu-D_nu W_mu and kappa=g_EM-1=1.
An overall coupling/phase convention is immaterial. The Pauli sign is defined
by the explicit vertex below: its soft real-photon limit is Eq.(2) of
hep-ph/9707202 with Delta kappa=0. Gauge invariance does not determine kappa.
The constant g_EM=2 and absent higher electromagnetic/hadronic operators are
model assumptions, not a measured charged-rho form factor.

With (+---), P incoming, p charged pion, r neutral pion, k outgoing A',
q=P-k=p+r, Delta=p-r, the three diagrams give
 T^{mu nu}=(Delta+k)^mu(2p+k)^nu/(2p.k+k^2)-g^{mu nu}
   +Gamma^{mu alpha nu}(g_{alpha beta}-q_alpha q_beta/M^2)
      Delta^beta/(q^2-M^2),
 Gamma=(P+q)^nu g^{mu alpha}-q^mu g^{alpha nu}
       -P^alpha g^{mu nu}+kappa(k^mu g^{alpha nu}-k^alpha g^{mu nu}).
These are pion radiation, the gauged derivative contact, and rho radiation.
No real-photon soft truncation or photon-index transverse projection is used.
The raw Ward contraction is proportional to P^mu; it vanishes for each
physical initial-rho polarization. Unequal pion masses are retained in the
propagator numerator. All three massive A' polarizations and their dimuon
correlations are kept, with the unpolarized rho factor 1/3.

The normalization is Gamma(rho -> pi pi)=g^2 M beta0^3/(48 pi), using an
explicit model-card branching approximation. Only a zero-width pole parent
is supported. This exploratory local benchmark omits broad-resonance source
interference, structure-dependent transitions, and timelike form factors.
Four-vectors are (E,px,py,pz) in GeV; lengths are mm.
"""
import bisect
from functools import lru_cache
import json
import hashlib
import math

from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_meson import MUON_MASS_GEV, boost, dot
from dark_photon_rho_radiative import (
    NATIVE_KINEMATIC_RATE_PRECISION_LIMIT, _check_parent_mass, _cross, _isotropic,
)
from dark_photon_vector_meson import mean_lab_beta_gamma as two_body_mean_boost
from dark_photon_vector_meson import two_body_momentum, radiative_branching


def _positive(value, name, *, allow_zero=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or (value < 0 if allow_zero else value <= 0)):
        raise ValueError('Explicit finite physical '+name+' required')


def _model_key(model):
    inputs = model.get('charged_rho_radiative_model', model)
    key = json.dumps(inputs, sort_keys=True, separators=(',', ':'), allow_nan=False)
    _validated_model(key)
    return key


@lru_cache(maxsize=64)
def _validated_model(key):
    model = json.loads(key)
    if model.get('kind') != 'gauged-proca-pion-g2-pole' or not model.get('reference'):
        raise ValueError('Explicit charged-rho Proca benchmark and reference required')
    if model.get('electromagnetic_gyromagnetic_ratio') != 2:
        raise ValueError('Only the explicitly declared charged-rho g_EM=2 benchmark is supported')
    for name in ('alpha', 'charged_pion_mass_gev', 'neutral_pion_mass_gev',
                 'reference_parent_mass_gev', 'pole_mass_numerical_tolerance_gev'):
        _positive(model.get(name), name)
    if model['pole_mass_numerical_tolerance_gev'] > 1.e-5:
        raise ValueError('Numerical pole tolerance cannot define broad physical support')
    threshold = model['charged_pion_mass_gev']+model['neutral_pion_mass_gev']
    if model['reference_parent_mass_gev'] <= threshold:
        raise ValueError('Reference charged rho must be above the dipion threshold')
    if (model.get('parent_polarization') != 'unpolarized'
            or model.get('parent_mass_continuation') != 'zero-width-pole'):
        raise ValueError('An unpolarized zero-width-pole charged rho is required')
    domain = model.get('valid_mass_range_gev')
    if (not isinstance(domain, list) or len(domain) != 2
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in domain)
            or not 0 <= domain[0] < domain[1]
            or domain[1] > model['reference_parent_mass_gev']-threshold+1.e-12):
        raise ValueError('Explicit physical charged-rho transition mass domain required')
    normalization = model.get('normalization', {})
    branching = normalization.get('branching_fraction')
    if (normalization.get('channel') != 'charged-pion-neutral-pion'
            or not normalization.get('reference')
            or type(branching) not in (int, float) or not math.isfinite(branching)
            or not 0 < branching <= 1):
        raise ValueError('Explicit charged-rho dipion branching normalization required')
    if not model.get('assumption') or model.get('signal_rate_validated') is not False:
        raise ValueError('The provisional charged-vector rate assumption must remain explicit')
    return model


def _check_mass(mass, model):
    _positive(mass, 'dark-photon mass')
    low, high = model['valid_mass_range_gev']
    if not low <= mass <= high:
        raise ValueError('Requested mass lies outside the charged-rho model domain')


def _minkowski(first, second):
    return math.fsum((first[0]*second[0], *(-a*b for a, b in zip(first[1:], second[1:]))))


def current_tensor(charged, neutral, dark, parent_mass, *, charge=1):
    """Raw T^{mu nu}, before the physical initial-vector spin contraction.

    k_nu T^{mu nu}=-P^mu[P.G(q).Delta], not identically zero as a four-tensor.
    eta.P=0 makes the physical amplitude gauge invariant. Charge conjugation
    changes only the overall sign here, so rho+ and rho- rates are identical.
    """
    if charge not in (-1, 1):
        raise ValueError('Charged rho must have charge +1 or -1')
    _positive(parent_mass, 'rho mass')
    if any(len(vector) != 4 or not all(math.isfinite(x) for x in vector)
           for vector in (charged, neutral, dark)):
        raise ValueError('Three finite daughter four-vectors required')
    parent = tuple(math.fsum(values) for values in zip(charged, neutral, dark))
    recoil = tuple(a+b for a, b in zip(charged, neutral))
    delta = tuple(a-b for a, b in zip(charged, neutral))
    virtuality = _minkowski(dark, dark)
    pion_denominator = 2*_minkowski(charged, dark)+virtuality
    rho_denominator = _minkowski(recoil, recoil)-parent_mass**2
    if pion_denominator <= 0 or rho_denominator >= 0:
        raise ValueError('Physical pion and virtual-rho propagators required')
    longitudinal = _minkowski(recoil, delta)/parent_mass**2
    propagated = tuple(a-longitudinal*b for a, b in zip(delta, recoil))
    scalar = _minkowski(parent, propagated)+_minkowski(dark, propagated)
    return tuple(tuple(charge*(
        (delta[mu]+dark[mu])*(2*charged[nu]+dark[nu])/pion_denominator
        -(1 if mu == 0 else -1)*(mu == nu)
        +((parent[nu]+recoil[nu])*propagated[mu]
          +(dark[mu]-recoil[mu])*propagated[nu]
          -scalar*(1 if mu == 0 else -1)*(mu == nu))/rho_denominator)
        for nu in range(4)) for mu in range(4))


def _rest_kinematics(s, cosine, parent_mass, mass, charged_mass, neutral_mass):
    root_s = math.sqrt(s)
    momentum = two_body_momentum(parent_mass, root_s, mass)
    energy = (parent_mass**2+mass**2-s)/(2*parent_mass)
    recoil_energy = parent_mass-energy
    pion_momentum = two_body_momentum(root_s, charged_mass, neutral_mass)
    charged_energy = (s+charged_mass**2-neutral_mass**2)/(2*root_s)
    neutral_energy = root_s-charged_energy
    sine = math.sqrt(max(0., 1-cosine*cosine))
    charged = ((recoil_energy*charged_energy-momentum*pion_momentum*cosine)/root_s,
               pion_momentum*sine, 0.,
               (recoil_energy*pion_momentum*cosine-momentum*charged_energy)/root_s)
    neutral = ((recoil_energy*neutral_energy+momentum*pion_momentum*cosine)/root_s,
               -pion_momentum*sine, 0.,
               (-recoil_energy*pion_momentum*cosine-momentum*neutral_energy)/root_s)
    return charged, neutral, (energy, 0., 0., momentum)


def _density_components(s, cosine, parent_mass, mass, charged_mass, neutral_mass):
    """Five nonzero entries Bxx,Bxz,Byy,Bzx,Bzz of physical spin amplitudes.

    The A' longitudinal amplitude uses the *raw physical* Ward identity to
    evaluate (K*T_i0-E*T_i3)/m as -(m/E)*T_i3. This stable algebra retains its
    massive longitudinal component; it does not project away any current.
    """
    charged, neutral, dark = _rest_kinematics(
        s, cosine, parent_mass, mass, charged_mass, neutral_mass)
    energy, momentum = dark[0], dark[3]
    delta0, deltax, deltaz = (charged[index]-neutral[index] for index in (0, 1, 3))
    delta_mass = charged_mass**2-neutral_mass**2
    v0 = delta0-(parent_mass-energy)*delta_mass/parent_mass**2
    vz = deltaz+momentum*delta_mass/parent_mass**2
    scalar = (parent_mass+energy)*v0-momentum*vz
    pion_denominator = mass**2+2*(energy*charged[0]-momentum*charged[3])
    rho_denominator = s-parent_mass**2
    bxx = 1+deltax*deltax/pion_denominator+scalar/rho_denominator
    bxz = deltax*deltaz/pion_denominator-momentum*deltax/rho_denominator
    byy = 1+scalar/rho_denominator
    bzx = (deltaz+momentum)*deltax/pion_denominator+2*momentum*deltax/rho_denominator
    bzz = (1+(deltaz+momentum)*deltaz/pion_denominator
           +(momentum*vz+scalar)/rho_denominator)
    factor = mass/energy
    return bxx, factor*bxz, byy, bzx, factor*bzz


def _matrix_squared(s, cosine, parent_mass, mass, charged_mass, neutral_mass):
    return math.fsum(value*value for value in _density_components(
        s, cosine, parent_mass, mass, charged_mass, neutral_mass))/3


def matrix_element_squared(s, cosine, parent_mass, mass, model):
    """Unpolarized |M|²/(e² epsilon² g_rho-pipi²), including all spins."""
    inputs = _validated_model(_model_key(model))
    _check_parent_mass(parent_mass, inputs)
    _positive(mass, 'vector mass', allow_zero=True)
    threshold = inputs['charged_pion_mass_gev']+inputs['neutral_pion_mass_gev']
    if (not math.isfinite(s) or not threshold**2 <= s <= (parent_mass-mass)**2
            or not math.isfinite(cosine) or not -1 <= cosine <= 1):
        raise ValueError('Physical dipion mass and pion helicity cosine required')
    return _matrix_squared(s, cosine, parent_mass, mass,
                           inputs['charged_pion_mass_gev'], inputs['neutral_pion_mass_gev'])


def _phase_density(s, cosine, parent_mass, mass, charged_mass, neutral_mass):
    if s <= (charged_mass+neutral_mass)**2 or s >= (parent_mass-mass)**2:
        return 0.
    beta = 2*two_body_momentum(math.sqrt(s), charged_mass, neutral_mass)/math.sqrt(s)
    return (two_body_momentum(parent_mass, math.sqrt(s), mass)*beta
            *_matrix_squared(s, cosine, parent_mass, mass, charged_mass, neutral_mass))


def _rate_prefactor(parent_mass, model):
    beta = 2*two_body_momentum(parent_mass, model['charged_pion_mass_gev'],
                             model['neutral_pion_mass_gev'])/parent_mass
    return 3*model['alpha']/(4*math.pi*parent_mass**3*beta**3)


@lru_cache(maxsize=256)
def _quadrature(parent_mass, mass, key, count=64, photon_cut=0.):
    inputs = _validated_model(key)
    charged_mass, neutral_mass = inputs['charged_pion_mass_gev'], inputs['neutral_pion_mass_gev']
    low = (charged_mass+neutral_mass)**2
    high = (parent_mass-mass)**2 if mass else parent_mass**2-2*parent_mass*photon_cut
    if high <= low:
        return ()
    if mass == 0 and photon_cut <= 0:
        raise ValueError('Massless photon rate requires a positive energy cut')
    nodes = []
    for node, weight in _gauss_legendre(count):
        theta = (node+1)*math.pi/4
        sine, cosine = math.sin(theta), math.cos(theta)
        s = low+(high-low)*sine*sine
        jacobian = 2*(high-low)*sine*cosine*math.pi/4
        angular = math.fsum(w*_phase_density(s, x, parent_mass, mass, charged_mass, neutral_mass)
                           for x, w in _gauss_legendre(count//2))
        nodes.append((s, weight*jacobian*angular))
    return tuple(nodes)


@lru_cache(maxsize=256)
def _integral(parent_mass, mass, key, count=64, photon_cut=0.):
    return math.fsum(weight for _, weight in _quadrature(parent_mass, mass, key, count, photon_cut))


def _exact_coefficient(parent_mass, mass, key):
    inputs = _validated_model(key)
    if mass >= parent_mass-inputs['charged_pion_mass_gev']-inputs['neutral_pion_mass_gev']:
        return 0.
    return (inputs['normalization']['branching_fraction']*_rate_prefactor(parent_mass, inputs)
            *_integral(parent_mass, mass, key))


def _local_cubic(parent_mass, nodes, values):
    index = min(len(nodes)-2, max(0, bisect.bisect_right(nodes, parent_mass)-1))
    first = max(0, min(len(nodes)-4, index-1))
    indices = tuple(range(first, first+4))
    return math.fsum(values[i]*math.prod((parent_mass-nodes[j])/(nodes[i]-nodes[j])
                     for j in indices if j != i) for i in indices)


@lru_cache(maxsize=128)
def _rate_interpolation(mass, key):
    """Qualify cubic interpolation on the same tiny numerical pole interval.

    At the .5-GeV endpoint linear interpolation would miss the unchanged
    1e-10 precision target, forcing exact quadrature for each source parent.
    Four local nodes retain curvature; 24 independent quarter/midpoint
    evaluations still have to pass that target. Nothing is extrapolated.
    """
    inputs = _validated_model(key)
    nominal, tolerance = inputs['reference_parent_mass_gev'], inputs['pole_mass_numerical_tolerance_gev']
    threshold = inputs['charged_pion_mass_gev']+inputs['neutral_pion_mass_gev']
    if nominal-threshold-mass <= 4*tolerance:
        return dict(qualified=False, reason='near-closed-endpoint')
    masses = tuple(nominal-tolerance+index*tolerance/4 for index in range(9))
    values = tuple(_exact_coefficient(parent_mass, mass, key) for parent_mass in masses)
    differences = []
    for index in range(8):
        for fraction in (.25, .5, .75):
            parent_mass = masses[index]+fraction*(masses[index+1]-masses[index])
            exact = _exact_coefficient(parent_mass, mass, key)
            interpolated = _local_cubic(parent_mass, masses, values)
            differences.append(abs(interpolated/exact-1))
    maximum = max(differences)
    return dict(qualified=maximum <= 1.e-10, reason='cubic-quarter-midpoint-convergence',
                nodes=masses, coefficients=values, independent_checks=len(differences),
                maximum_checked_relative_error=maximum, required_relative_error=1.e-10,
                nominal_coefficient=values[4])


def rate_interpolation_validation(mass, model):
    key = _model_key(model)
    _check_mass(mass, _validated_model(key))
    return dict(_rate_interpolation(mass, key))


def _interpolated_or_exact_coefficient(parent_mass, mass, key):
    interpolation = _rate_interpolation(mass, key)
    if interpolation['qualified']:
        nodes = interpolation['nodes']
        if nodes[0] <= parent_mass <= nodes[-1]:
            return _local_cubic(parent_mass, nodes, interpolation['coefficients'])
    return _exact_coefficient(parent_mass, mass, key)


@lru_cache(maxsize=64)
def _validated_parent_rate_model(key, context_key):
    """Require the complete, disjoint charged-rho pair from the frozen model.

    A caller cannot supply a larger denominator or switch off a check. The
    companion rate is recomputed from its resolved physical channel, sharing
    the same generated parent and pole as this dipion amplitude.
    """
    inputs = _validated_model(key)
    context = json.loads(context_key)
    parent_pdg = context.get('parent_pdg_id')
    channels = context.get('exclusive_channels')
    if parent_pdg not in (-213, 213) or not isinstance(channels, list) or len(channels) != 2:
        raise ValueError('Native precision needs exactly both owned channels of one charged rho')
    by_type = {channel.get('decay_type'): channel for channel in channels if isinstance(channel, dict)}
    if set(by_type) != {'vector-pseudoscalar', 'charged-vector-dipion'}:
        raise ValueError('Native precision requires disjoint charged-rho pion and dipion channels')
    charged, companion = by_type['charged-vector-dipion'], by_type['vector-pseudoscalar']
    expected_charge = 211 if parent_pdg > 0 else -211
    pole = inputs['reference_parent_mass_gev']
    if (any(channel.get('pdg_id') != parent_pdg for channel in channels)
            or len({channel.get('channel') for channel in channels}) != 2
            or any(not isinstance(channel.get('channel'), str) or not channel['channel'] for channel in channels)
            or any(channel.get('mass_min_gev') != pole or channel.get('mass_max_gev') != pole
                   or channel.get('parent_mass_model') != 'zero-width-pole'
                   or channel.get('parent_polarization') != 'unpolarized' for channel in channels)
            or charged.get('recoil_pdg_ids') != [expected_charge, 111]
            or charged.get('charged_pion_mass_gev') != inputs['charged_pion_mass_gev']
            or charged.get('neutral_pion_mass_gev') != inputs['neutral_pion_mass_gev']
            or charged.get('br_pipi') != inputs['normalization']['branching_fraction']
            or _model_key(charged) != key
            or companion.get('recoil_pdg_id') != expected_charge
            or companion.get('recoil_mass_gev') != inputs['charged_pion_mass_gev']
            or not companion.get('provenance')):
        raise ValueError('Native precision context differs from its charge-matched common-pole models')
    _positive(companion.get('br_p_gamma'), 'companion radiative branching')
    _positive(companion.get('form_factor_abs2'), 'companion resolved form factor')
    if companion['br_p_gamma'] > 1:
        raise ValueError('Companion branching cannot exceed one')
    return context, companion


def native_parent_precision_report(parent_mass, mass, model, *, native_mass_gev,
                                   native_energy_gev, native_mass_tolerance,
                                   native_parent_rate_model, defer_to_full_source=False):
    """Recompute a <=0.1% bound on one physical parent's total contribution.

    The usual isolated-channel relative test remains strict. Near a kinematic
    endpoint a very small channel may exceed it while contributing negligibly
    to the parent total. Here L1(rate shifts) plus that channel's *entire*
    nominal fraction must fit the SAME 0.1% budget. Its entire weight bounds
    any change of its normalized conditional distribution in an arbitrary bin.
    This is deliberately conservative, not an assumed shape-error percentage.
    Neither nominal production probabilities nor native p4 are modified.
    This bounds the added rare-channel contribution; it does not establish
    the missing hadronic model or a general sibling-shape uncertainty.

    An explicitly deferred report does not pass this parent. It provides an
    absolute bound for an independent fixed-source audit: twice the larger
    parent measure bounds their L1 difference and the nominal-weight output's
    conditional variation, even for disjoint support. The runner must retain
    every parent and collision and pass the unchanged full-source 0.1% budget
    before accepting or publishing the sample. Default callers remain strict.
    """
    key = _model_key(model)
    inputs = _validated_model(key)
    _check_mass(mass, inputs)
    _check_parent_mass(parent_mass, inputs, kinematic_energy=native_energy_gev,
                       native_mass_gev=native_mass_gev, native_mass_tolerance=native_mass_tolerance)
    context_key = json.dumps(native_parent_rate_model, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
    context, companion = _validated_parent_rate_model(key, context_key)
    threshold = inputs['charged_pion_mass_gev']+inputs['neutral_pion_mass_gev']
    if ((parent_mass > mass+threshold) != (native_mass_gev > mass+threshold)
            or (parent_mass > mass+companion['recoil_mass_gev']) !=
               (native_mass_gev > mass+companion['recoil_mass_gev'])):
        raise ValueError('Native precision changes whether an owned rare decay is open')
    nominal = _interpolated_or_exact_coefficient(native_mass_gev, mass, key)
    shifted = _interpolated_or_exact_coefficient(parent_mass, mass, key)

    def companion_coefficient(rho_mass):
        return radiative_branching(rho_mass, companion['recoil_mass_gev'], mass, .001,
                    companion['br_p_gamma'], companion['form_factor_abs2'])/1.e-6

    companion_nominal = companion_coefficient(native_mass_gev)
    companion_shifted = companion_coefficient(parent_mass)
    total = nominal+companion_nominal
    if total <= 0:
        raise ValueError('A positive owned parent rate is required for the precision bound')
    relative_channel_shift = shifted/nominal-1 if nominal else 0.
    l1_shift = (abs(shifted-nominal)+abs(companion_shifted-companion_nominal))/total
    channel_fraction = nominal/total
    conditional_bound = (channel_fraction if abs(relative_channel_shift) >
                         NATIVE_KINEMATIC_RATE_PRECISION_LIMIT else 0.)
    combined_bound = l1_shift+conditional_bound
    if type(defer_to_full_source) is not bool:
        raise ValueError('Full-source deferral must be explicitly boolean')
    deferred = combined_bound > NATIVE_KINEMATIC_RATE_PRECISION_LIMIT
    if deferred and not defer_to_full_source:
        raise ValueError('Native charged-rho parent rate plus conditional-shape bound exceeds 0.1%')
    return dict(schema='shift-charged-rho-native-parent-precision-v1',
                parent_pdg_id=context['parent_pdg_id'], signal_mass_gev=mass,
                native_parent_mass_gev=native_mass_gev, kinematic_parent_mass_gev=parent_mass,
                context_sha256=hashlib.sha256(context_key.encode()).hexdigest(),
                nominal_channel_coefficient=nominal, kinematic_channel_coefficient=shifted,
                channel_relative_rate_shift=relative_channel_shift,
                channel_nominal_parent_fraction=channel_fraction,
                parent_l1_relative_rate_shift=l1_shift,
                conditional_distribution_fraction_bound=conditional_bound,
                parent_total_relative_bound=combined_bound,
                deferred_full_source_check=deferred,
                scope='deferred-full-source' if deferred else 'same-parent',
                nominal_parent_coefficient=total,
                conservative_absolute_parent_measure_bound=(2*max(total,shifted+companion_shifted)
                                                            if deferred else 0.),
                required_relative_bound=NATIVE_KINEMATIC_RATE_PRECISION_LIMIT)


def branching_coefficient(parent_mass, mass, model, *, native_mass_gev=None,
                          native_energy_gev=None, native_mass_tolerance=None,
                          native_parent_rate_model=None, defer_to_full_source=False):
    """BR(rho+- -> pi+- pi0 A')/epsilon², excluding the A' dimuon BR.

    Production probability must use the native pole. Explicit native context
    enables only the separate unchanged-four-vector numerical diagnostic,
    limited to 0.1% rate shift, just as in the neutral-rho implementation.
    A fully resolved same-parent context instead requires the conservative
    total-rate plus conditional-distribution bound documented above.
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
    threshold = inputs['charged_pion_mass_gev']+inputs['neutral_pion_mass_gev']
    if context and ((parent_mass > mass+threshold) != (native_mass_gev > mass+threshold)):
        raise ValueError('Native rho precision changes whether the rare decay is open')
    if mass >= parent_mass-threshold:
        return 0.
    _check_mass(mass, inputs)
    coefficient = _interpolated_or_exact_coefficient(parent_mass, mass, key)
    if native_parent_rate_model is not None and not context:
        raise ValueError('Owned parent rate context requires complete native mass and tolerance context')
    if defer_to_full_source and (native_parent_rate_model is None or not context):
        raise ValueError('Full-source deferral requires the complete owned native parent context')
    if context:
        if native_parent_rate_model is not None:
            native_parent_precision_report(parent_mass, mass, model,
                native_mass_gev=native_mass_gev, native_energy_gev=native_energy_gev,
                native_mass_tolerance=native_mass_tolerance,
                native_parent_rate_model=native_parent_rate_model,
                defer_to_full_source=defer_to_full_source)
        else:
            nominal = _interpolated_or_exact_coefficient(native_mass_gev, mass, key)
            if abs(coefficient/nominal-1) > NATIVE_KINEMATIC_RATE_PRECISION_LIMIT:
                raise ValueError('Native rho mass-shell effect exceeds the 0.1% numerical rate target')
    return coefficient


def photon_branching_above_cut(cut_energy_gev, model):
    _positive(cut_energy_gev, 'photon energy cut')
    key = _model_key(model)
    inputs = _validated_model(key)
    parent_mass = inputs['reference_parent_mass_gev']
    return (inputs['normalization']['branching_fraction']*_rate_prefactor(parent_mass, inputs)
            *_integral(parent_mass, 0., key, photon_cut=cut_energy_gev))


def _parent_mass(parent, model, *, signal_mass=None, native_mass_gev=None, native_mass_tolerance=None,
                 native_parent_rate_model=None, defer_to_full_source=False):
    if native_parent_rate_model is not None and (native_mass_gev is None or native_mass_tolerance is None):
        raise ValueError('Owned parent rate context requires complete native mass and tolerance context')
    if len(parent) != 4 or not all(math.isfinite(x) for x in parent) or parent[0] <= 0:
        raise ValueError('A finite timelike parent four-vector is required')
    invariant = _minkowski(parent, parent)
    if invariant <= 0:
        raise ValueError('The charged rho parent must be timelike')
    result = math.sqrt(invariant)
    _check_parent_mass(result, model, kinematic_energy=parent[0],
                       native_mass_gev=native_mass_gev, native_mass_tolerance=native_mass_tolerance)
    if native_mass_gev is not None or native_mass_tolerance is not None:
        branching_coefficient(result, signal_mass, model, native_mass_gev=native_mass_gev,
                              native_energy_gev=parent[0], native_mass_tolerance=native_mass_tolerance,
                              native_parent_rate_model=native_parent_rate_model,
                              defer_to_full_source=defer_to_full_source)
    return result


def mean_lab_beta_gamma(parent, mass, model, *, native_mass_gev=None, native_mass_tolerance=None,
                        native_parent_rate_model=None, defer_to_full_source=False):
    key = _model_key(model)
    inputs = _validated_model(key)
    parent_mass = _parent_mass(parent, inputs, signal_mass=mass,
                              native_mass_gev=native_mass_gev, native_mass_tolerance=native_mass_tolerance,
                              native_parent_rate_model=native_parent_rate_model,
                              defer_to_full_source=defer_to_full_source)
    _check_mass(mass, inputs)
    threshold = inputs['charged_pion_mass_gev']+inputs['neutral_pion_mass_gev']
    if mass <= 2*MUON_MASS_GEV or mass >= parent_mass-threshold:
        raise ValueError('Open dipion and dimuon channels required')
    nodes = _quadrature(inputs['reference_parent_mass_gev'], mass, key)
    if (abs(parent_mass-inputs['reference_parent_mass_gev']) > inputs['pole_mass_numerical_tolerance_gev']
            or nodes[-1][0] >= (parent_mass-mass)**2):
        nodes = _quadrature(parent_mass, mass, key)
    normalization = math.fsum(weight for _, weight in nodes)
    return math.fsum(weight*two_body_mean_boost(parent, math.sqrt(s), mass,
                    polarization='unpolarized') for s, weight in nodes)/normalization


def _cell_bound(low_s, high_s, low_c, high_c, parent_mass, mass, charged_mass, neutral_mass):
    """Conservative analytic bound, not an empirical grid maximum."""
    energy_high = (parent_mass**2+mass**2-low_s)/(2*parent_mass)
    energy_low = (parent_mass**2+mass**2-high_s)/(2*parent_mass)
    momentum_high = two_body_momentum(parent_mass, math.sqrt(low_s), mass)
    momentum_low = two_body_momentum(parent_mass, math.sqrt(high_s), mass)
    pion_high = two_body_momentum(math.sqrt(high_s), charged_mass, neutral_mass)
    beta_high = 2*pion_high/math.sqrt(high_s)
    beta_low = 2*two_body_momentum(math.sqrt(low_s), charged_mass, neutral_mass)/math.sqrt(low_s)
    delta_mass = charged_mass**2-neutral_mass**2
    # A(s)=m²+(1+delta/s)*(M²-m²-s)/2 decreases for physical delta>=0.
    # B(s)=M*K*beta is bounded by independent nonnegative endpoint extrema.
    if delta_mass < 0:
        raise ValueError('Analytic envelope requires the physical charged pion to be heavier')
    constant_low = (parent_mass**2+mass**2-high_s
                    +delta_mass*(parent_mass**2-mass**2)/high_s-delta_mass)/2
    if high_c >= 0:
        pion_denominator_low = constant_low-parent_mass*momentum_high*beta_high*high_c
    else:
        pion_denominator_low = constant_low-parent_mass*momentum_low*beta_low*high_c
    pion_denominator_low = max(mass*mass+2*mass*charged_mass, pion_denominator_low)
    rho_denominator_low = parent_mass**2-high_s
    cosine_high = max(abs(low_c), abs(high_c))
    sine_high = (1. if low_c <= 0 <= high_c else
                 math.sqrt(max(0., 1-min(abs(low_c), abs(high_c))**2)))
    recoil_high = parent_mass-energy_low
    dx = 2*pion_high*sine_high
    dz = 2*recoil_high*pion_high*cosine_high/math.sqrt(low_s)+momentum_high*delta_mass/low_s
    v0 = (recoil_high*delta_mass/low_s+2*momentum_high*pion_high*cosine_high/math.sqrt(low_s)
          +recoil_high*delta_mass/parent_mass**2)
    vz = dz+momentum_high*delta_mass/parent_mass**2
    scalar = (parent_mass+energy_high)*v0+momentum_high*vz
    factor = mass/energy_low
    bounds = (1+dx*dx/pion_denominator_low+scalar/rho_denominator_low,
              factor*(dx*dz/pion_denominator_low+momentum_high*dx/rho_denominator_low),
              1+scalar/rho_denominator_low,
              (dz+momentum_high)*dx/pion_denominator_low+2*momentum_high*dx/rho_denominator_low,
              factor*(1+(dz+momentum_high)*dz/pion_denominator_low
                      +(momentum_high*vz+scalar)/rho_denominator_low))
    return momentum_high*beta_high*math.fsum(value*value for value in bounds)/3*(1+2.e-12)


@lru_cache(maxsize=128)
def _envelope(parent_mass, mass, key):
    inputs = _validated_model(key)
    charged_mass, neutral_mass = inputs['charged_pion_mass_gev'], inputs['neutral_pion_mass_gev']
    low = (charged_mass+neutral_mass)**2
    extent = (parent_mass-mass)**2-low
    if extent <= 0:
        raise ValueError('Closed charged-rho dipion channel')
    cells, cumulative = [], [0.]
    for s_index in range(64):
        low_s, high_s = low+extent*s_index/64, low+extent*(s_index+1)/64
        for c_index in range(32):
            low_c, high_c = -1+c_index/16, -1+(c_index+1)/16
            bound = _cell_bound(low_s, high_s, low_c, high_c, parent_mass, mass, charged_mass, neutral_mass)
            cells.append((low_s, high_s, low_c, high_c, bound))
            cumulative.append(cumulative[-1]+(high_s-low_s)*(high_c-low_c)*bound)
    return tuple(cells), tuple(cumulative)


def _sample_invariants(parent_mass, mass, key, rng):
    cells, cumulative = _envelope(parent_mass, mass, key)
    inputs = _validated_model(key)
    while True:
        index = min(len(cells)-1, bisect.bisect_right(cumulative, rng.random()*cumulative[-1])-1)
        low_s, high_s, low_c, high_c, bound = cells[index]
        s, cosine = low_s+(high_s-low_s)*rng.random(), low_c+(high_c-low_c)*rng.random()
        value = _phase_density(s, cosine, parent_mass, mass,
                              inputs['charged_pion_mass_gev'], inputs['neutral_pion_mass_gev'])
        if value > bound*(1+1.e-10):
            raise RuntimeError('Charged-rho analytic rejection-envelope bound violated')
        if rng.random()*bound <= value:
            return s, cosine


def sample_decay(parent, mass, proper_ctau_mm, rng, *, model, charge=1,
                 native_mass_gev=None, native_mass_tolerance=None, native_parent_rate_model=None,
                 defer_to_full_source=False):
    """Unconditional correlated charged-rho decay, with rho charge ±1."""
    if charge not in (-1, 1):
        raise ValueError('Charged rho must have charge +1 or -1')
    if (native_parent_rate_model is not None
            and native_parent_rate_model.get('parent_pdg_id') != charge*213):
        raise ValueError('Sampled charged-rho charge differs from its native parent precision context')
    key = _model_key(model)
    inputs = _validated_model(key)
    parent_mass = _parent_mass(parent, inputs, signal_mass=mass,
                              native_mass_gev=native_mass_gev, native_mass_tolerance=native_mass_tolerance,
                              native_parent_rate_model=native_parent_rate_model,
                              defer_to_full_source=defer_to_full_source)
    _positive(proper_ctau_mm, 'proper decay length')
    _check_mass(mass, inputs)
    charged_mass, neutral_mass = inputs['charged_pion_mass_gev'], inputs['neutral_pion_mass_gev']
    if not 2*MUON_MASS_GEV < mass < parent_mass-charged_mass-neutral_mass:
        raise ValueError('Open dipion and dimuon channels required')
    s, cosine = _sample_invariants(parent_mass, mass, key, rng)
    bxx, bxz, byy, bzx, bzz = _density_components(s, cosine, parent_mass, mass,
                                                charged_mass, neutral_mass)
    trace = math.fsum(value*value for value in (bxx, bxz, byy, bzx, bzz))
    beta_mu2 = 1-4*MUON_MASS_GEV**2/mass**2
    while True:
        direction = _isotropic(rng)
        projection = ((bxx*direction[0]+bxz*direction[2])**2+byy**2*direction[1]**2
                      +(bzx*direction[0]+bzz*direction[2])**2)
        probability = 1-beta_mu2*projection/trace
        if not 0 <= probability <= 1+1.e-12:
            raise RuntimeError('Nonphysical charged-rho correlated dimuon spin density')
        if rng.random() <= probability:
            break
    charged, neutral, dark = _rest_kinematics(s, cosine, parent_mass, mass, charged_mass, neutral_mass)
    muon_momentum = math.sqrt(mass*mass/4-MUON_MASS_GEV**2)
    dark_beta = (0., 0., dark[3]/dark[0])
    muon = boost((mass/2,)+tuple(muon_momentum*x for x in direction), dark_beta)
    antimuon = boost((mass/2,)+tuple(-muon_momentum*x for x in direction), dark_beta)
    axis = _isotropic(rng)
    auxiliary = (0., 0., 1.) if abs(axis[2]) < .9 else (1., 0., 0.)
    first = _cross(axis, auxiliary)
    length = math.sqrt(dot(first, first))
    first = tuple(value/length for value in first)
    second = _cross(axis, first)
    angle = 2*math.pi*rng.random()
    x_axis = tuple(math.cos(angle)*a+math.sin(angle)*b for a, b in zip(first, second))
    y_axis = _cross(axis, x_axis)
    parent_beta = tuple(value/parent[0] for value in parent[1:])

    def to_lab(vector):
        rotated = (vector[0],)+tuple(vector[1]*a+vector[2]*b+vector[3]*c
                                    for a, b, c in zip(x_axis, y_axis, axis))
        return boost(rotated, parent_beta)

    dark, charged, neutral, muon, antimuon = map(to_lab, (dark, charged, neutral, muon, antimuon))
    proper_length = -proper_ctau_mm*math.log1p(-rng.random())
    return dict(dark_photon_p4=dark, muon_p4=muon, antimuon_p4=antimuon,
                recoil_p4s=[charged, neutral], recoil_pdg_ids=[charge*211, 111],
                recoil_masses_gev=[charged_mass, neutral_mass], dark_photon_mass_gev=mass,
                muon_mass_gev=MUON_MASS_GEV, proper_decay_length_mm=proper_length,
                decay_displacement_mm=tuple(proper_length*x/mass for x in dark[1:]),
                decay_ct_mm=proper_length*dark[0]/mass, unconditional_decay=True,
                recoil_natural_decay_required=True, parent_polarization='unpolarized',
                amplitude_model='gauged-proca-pion-g2-pole',
                polarization_model='full-massive-vector-density')
