# Code style: AI-code. AI-generated gauge-invariant leptonic-kaon rate kernel.
"""K+ -> l+ nu A' at leading mixing order with O(p^4) chiral currents.

Bijnens, Ecker and Gasser, hep-ph/9209261 Eqs.(4.8)--(4.22), Appendix A.
The amplitude is epsilon*e*GF*Vus times (FK*L^rho-H^{rho mu}*l_mu).
Both charged external legs occur in IB. The transverse SD tensor includes
the virtual-only A2,A4 terms and the complex charged-kaon EM form factor.
Replacing a real-photon branching fraction would omit these contributions.

This standalone library does not attach native parent endvertices or
simulate material. It retains the full three-polarization density, IB, SD and
their interference in both rates and the unit-weight event sampler.
Production rates exclude BR(A' -> mu+mu-). The continuum
conversion helper applies only to a distinguishable virtual dilepton pair;
identical final positive leptons require the exchange amplitude separately.

All four-vectors are (E,px,py,pz), GeV; g=(+---), epsilon^{0123}=-1.
This relative tensor/gamma5 convention reproduces the primary Eq.(3.16)
separately for IB, SD and interference, fixing its physically relevant sign.
Fpi/FK use the ~93/~114 MeV convention of the primary paper, not sqrt(2)*F.
The supplied historical O(p^4) input card is a benchmark, with explicit LEC
and truncation limitations. It is not a precision fit or a transport model.
"""
import itertools
import math
import bisect
import json
from functools import lru_cache

import numpy as np

from dark_photon_meson import MUON_MASS_GEV, boost
from dark_photon_vector_meson import two_body_momentum


REFERENCE = 'https://arxiv.org/pdf/hep-ph/9209261'
METRIC = np.array([1., -1., -1., -1.])
IDENTITY = np.eye(4, dtype=complex)
_two = np.eye(2, dtype=complex)
_zero = np.zeros((2, 2), dtype=complex)
_pauli = (np.array([[0, 1], [1, 0]]), np.array([[0, -1j], [1j, 0]]),
          np.array([[1, 0], [0, -1]]))
GAMMA = np.array([np.block([[_two, _zero], [_zero, -_two]])] +
    [np.block([[_zero, matrix], [-matrix, _zero]]) for matrix in _pauli])
GAMMA5 = 1j*GAMMA[0]@GAMMA[1]@GAMMA[2]@GAMMA[3]
EPSILON = np.zeros((4, 4, 4, 4))
for _indices in itertools.permutations(range(4)):
    _inversions = sum(_indices[i] > _indices[j] for i in range(4) for j in range(i+1, 4))
    EPSILON[_indices] = -(-1)**_inversions


def _number(value, name, *, positive=False, nonnegative=False):
    if (type(value) not in (int, float) or not math.isfinite(value) or
            positive and value <= 0 or nonnegative and value < 0):
        raise ValueError('Explicit finite physical '+name+' required')
    return float(value)


def _p4(value):
    result = np.asarray(value, dtype=float)
    if result.shape != (4,) or not np.all(np.isfinite(result)):
        raise ValueError('Four finite momentum components required')
    return result


def dot(first, second):
    return np.sum(METRIC*first*second)


def slash(momentum):
    return np.einsum('r,rij->ij', METRIC*_p4(momentum), GAMMA)


def equal_mass_h_over_q2(q2, mass, fpi, l9, scale):
    """H_mm(q^2)/q^2 with its finite q^2=0 limit, Appendix A.9--A.11.

    The series treats the equal-mass limit before cancellation. Above the
    two-meson threshold the positive imaginary part is retained explicitly.
    """
    q2 = _number(q2, 'timelike virtuality', nonnegative=True)
    for value, name in ((mass, 'loop mass'), (fpi, 'Fpi'), (scale, 'LEC scale')):
        _number(value, name, positive=True)
    _number(l9, 'L9')
    x = q2/mass**2
    k = (math.log(mass**2/scale**2)+1)/(32*math.pi**2)
    if x < .1:
        # Jbar=1/(16pi^2) sum c_n x^n, c_n=n!^2/[n(2n+1)!].
        # The constant of (1-4/x)Jbar/12 cancels 1/(288pi^2).
        correction = 0.
        for n in range(1, 15):
            coefficient = math.factorial(n)**2/(n*math.factorial(2*n+1))
            following = math.factorial(n+1)**2/((n+1)*math.factorial(2*n+3))
            correction += (coefficient-4*following)*x**n/(192*math.pi**2)
        mr = correction-k/6
    else:
        if x < 4:
            jbar = (2-2*math.sqrt(4/x-1)*math.asin(math.sqrt(x)/2))/(16*math.pi**2)
        elif x == 4:
            jbar = 2/(16*math.pi**2)
        else:
            beta = math.sqrt(1-4/x)
            jbar = (2+beta*(math.log(4/x)-2*math.log1p(beta)+1j*math.pi))/(16*math.pi**2)
        mr = (1-4/x)*jbar/12+1/(288*math.pi**2)-k/6
    return ((2/3)*l9+mr)/fpi**2


def form_factors(q2, w2, model):
    """Explicit O(p^4) V1,A1,A2,A4; no on-shell-photon substitution."""
    if (model.get('kind') != 'chpt-p4-charged-kaon-leptonic-vector' or
            model.get('parent_pdg_id') != 321 or not model.get('reference')):
        raise ValueError('Explicit K+ chiral current authority required')
    q2 = _number(q2, 'timelike virtuality', nonnegative=True)
    w2 = _number(w2, 'lepton-neutrino virtuality', positive=True)
    parent = _number(model.get('parent_mass_gev'), 'parent mass', positive=True)
    domain = model.get('valid_q2_range_gev2')
    if (not isinstance(domain, (tuple, list)) or len(domain) != 2 or
            any(type(value) not in (int, float) or not math.isfinite(value) for value in domain) or
            not 0 <= domain[0] < domain[1] or not domain[0] <= q2 <= domain[1] or
            w2 >= parent**2):
        raise ValueError('Virtuality outside the explicit current domain')
    fpi = _number(model.get('fpi_gev'), 'Fpi', positive=True)
    fk = _number(model.get('fk_gev'), 'FK', positive=True)
    l9, l10 = [_number(model.get(name), name) for name in ('l9', 'l10')]
    scale = _number(model.get('lec_scale_gev'), 'LEC scale', positive=True)
    pion = _number(model.get('charged_pion_mass_gev'), 'charged pion mass', positive=True)
    # The IB-only control retains the caller's FK. A strict O(p2) scenario
    # must additionally set FK=F; a renormalized IB subset keeps measured FK.
    if model.get('order') == 'IB-only-control':
        return dict(V1=0., A1=0., A2=0., A4=0., kaon_em_form_factor=1.)
    if model.get('order') != 'p4-one-loop':
        raise ValueError('Explicit chiral order required')
    fv_subtracted_over_q2 = (equal_mass_h_over_q2(q2, pion, fpi, l9, scale)+
        2*equal_mass_h_over_q2(q2, parent, fpi, l9, scale))
    return dict(V1=-1/(8*math.pi**2*fpi), A1=-4*(l9+l10)/fpi,
        A2=-2*fk*fv_subtracted_over_q2,
        A4=2*fk*fv_subtracted_over_q2/(parent**2-w2),
        kaon_em_form_factor=1+q2*fv_subtracted_over_q2)


def current_operators(parent, lepton, neutrino, dark, model):
    """Spinor operators J^rho for IB and SD, before epsilon*e*GF*Vus.

    q_rho J^rho=0 is checked independently in tests. The virtual photon/DP
    current index is contravariant; a polarization contracts with g_rr.
    """
    parent, lepton, neutrino, dark = [_p4(p) for p in (parent, lepton, neutrino, dark)]
    if min(parent[0], lepton[0], neutrino[0], dark[0]) <= 0:
        raise ValueError('Physical future-directed external momenta required')
    scale = max(parent[0], 1.)
    if np.max(np.abs(parent-lepton-neutrino-dark)) > 64*math.ulp(scale):
        raise ValueError('External four-momentum conservation required')
    parent_mass = _number(model.get('parent_mass_gev'), 'parent mass', positive=True)
    mass = _number(model.get('lepton_mass_gev'), 'charged lepton mass', nonnegative=True)
    for vector, target in ((parent, parent_mass), (lepton, mass), (neutrino, 0.)):
        # A W-rest daughter inherits the two boost operations at the kaon
        # energy scale. Child-only ULPs undercount cancellation for an electron
        # or a soft neutrino; retain the parent's arithmetic scale explicitly.
        tolerance = 64*math.ulp(max(parent[0]**2,
            float(np.max(np.abs(vector)))**2, target**2))
        if abs(dot(vector, vector)-target**2) > tolerance:
            raise ValueError('External momentum disagrees with its explicit mass')
    q2, weak = dot(dark, dark), parent-dark
    if q2 < -64*math.ulp(dark[0]**2):
        raise ValueError('Timelike or real-photon current required')
    q2 = max(0., float(q2))
    factors = form_factors(q2, float(dot(weak, weak)), model)
    fk = model['fk_gev']
    first, second = 2*dot(parent, dark)-q2, 2*dot(lepton, dark)+q2
    if first <= 0 or second <= 0:
        raise ValueError('Physical external-leg propagators required')
    ib = np.array([fk*mass*(IDENTITY+GAMMA5)@
        ((2*parent[rho]-dark[rho])/first*IDENTITY-
         (2*lepton[rho]*IDENTITY+slash(dark)@GAMMA[rho])/second)
        for rho in range(4)])
    tensor = (1j*factors['V1']*np.einsum('rmab,a,b->rm', EPSILON,
                  METRIC*dark, METRIC*parent)
        -factors['A1']*(dot(dark, weak)*np.diag(METRIC)-np.outer(weak, dark))
        -factors['A2']*(q2*np.diag(METRIC)-np.outer(dark, dark))
        -factors['A4']*np.outer(dot(dark, weak)*dark-q2*weak, weak))
    sd = -np.einsum('rm,m,mij,jk->rik', tensor, METRIC, GAMMA, IDENTITY-GAMMA5)
    return dict(IB=ib, SD=sd, total=ib+sd)


def polarization_basis(dark):
    """Two linear transverse states and, for a massive vector, one longitudinal."""
    dark = _p4(dark)
    norm = float(np.linalg.norm(dark[1:]))
    mass2 = float(dot(dark, dark))
    if dark[0] <= 0 or mass2 < -64*math.ulp(dark[0]**2):
        raise ValueError('Future-directed physical vector required')
    axis = dark[1:]/norm if norm else np.array([0., 0., 1.])
    auxiliary = np.array([0., 0., 1.]) if abs(axis[2]) < .9 else np.array([1., 0., 0.])
    first = np.cross(axis, auxiliary); first /= np.linalg.norm(first)
    second = np.cross(axis, first)
    states = [np.r_[0., first], np.r_[0., second]]
    if mass2 > 64*math.ulp(dark[0]**2):
        mass = math.sqrt(mass2)
        states.append(np.r_[norm/mass, dark[0]*axis/mass])
    return np.array(states)


def spin_density(parent, lepton, neutrino, dark, model, *, epsilon,
                 polarizations=None):
    """Full vector polarization density after charged-lepton/neutrino spin sum.

    Returns IB, SD, interference and total density matrices. Off-diagonal
    entries are retained; their units are those of the full squared amplitude.
    """
    epsilon = _number(epsilon, 'mixing', nonnegative=True)
    if epsilon > .1:
        raise ValueError('Require small perturbative mixing')
    alpha = _number(model.get('alpha_em'), 'alpha', positive=True)
    gf = _number(model.get('fermi_constant_gev_minus2'), 'GF', positive=True)
    vus = _number(model.get('v_us'), '|Vus|', positive=True)
    operators = current_operators(parent, lepton, neutrino, dark, model)
    states = polarization_basis(dark) if polarizations is None else np.asarray(polarizations, dtype=complex)
    if states.ndim != 2 or states.shape[1] != 4 or not np.all(np.isfinite(states)):
        raise ValueError('Finite explicit polarization vectors required')
    incoming = slash(neutrino)
    outgoing = slash(lepton)-model['lepton_mass_gev']*IDENTITY
    coefficient = epsilon**2*4*math.pi*alpha*gf**2*vus**2
    densities = {}
    for name, current in operators.items():
        amplitudes = np.einsum('ar,r,rij->aij', states.conjugate(), METRIC, current)
        conjugates = np.array([GAMMA[0]@matrix.conjugate().T@GAMMA[0] for matrix in amplitudes])
        densities[name] = coefficient*np.einsum('ij,ajk,kl,bli->ab',
            incoming, amplitudes, outgoing, conjugates)
    densities['interference'] = densities['total']-densities['IB']-densities['SD']
    return densities


def rest_kinematics(parent_mass, lepton_mass, mass, weak_mass_squared, cosine):
    """Three-body coordinates, with q along +z and lepton angle in W rest."""
    for value, name in ((parent_mass, 'parent mass'), (weak_mass_squared, 'W mass squared')):
        _number(value, name, positive=True)
    _number(lepton_mass, 'lepton mass', nonnegative=True)
    _number(mass, 'vector mass', nonnegative=True)
    _number(cosine, 'helicity cosine')
    weak_mass = math.sqrt(weak_mass_squared)
    if not -1 <= cosine <= 1 or not lepton_mass < weak_mass < parent_mass-mass:
        raise ValueError('Open three-body phase space required')
    momentum = two_body_momentum(parent_mass, weak_mass, mass)
    dark = (math.hypot(momentum, mass), 0., 0., momentum)
    weak_energy = math.hypot(momentum, weak_mass)
    internal = (weak_mass_squared-lepton_mass**2)/(2*weak_mass)
    transverse = internal*math.sqrt(1-cosine**2)
    fraction = 1-lepton_mass**2/weak_mass_squared
    # Analytic W boost avoids gamma=1/sqrt(1-beta²) cancellation for a
    # nearly massless W at the electron endpoint. Construct complementary
    # daughters together so total energy/momentum does not lose precision.
    neutrino_energy = fraction*(weak_energy+momentum*cosine)/2
    neutrino_z = -fraction*(weak_energy*cosine+momentum)/2
    neutrino = (neutrino_energy, -transverse, 0., neutrino_z)
    lepton = (weak_energy-neutrino_energy, transverse, 0., -momentum-neutrino_z)
    return (parent_mass, 0., 0., 0.), lepton, neutrino, dark


def differential_width(model, mass, weak_mass_squared, cosine, *, epsilon):
    """dGamma/(dW^2 dcos theta), including its polarization density, GeV^-1."""
    parent, lepton, neutrino, dark = rest_kinematics(model['parent_mass_gev'],
        model['lepton_mass_gev'], mass, weak_mass_squared, cosine)
    densities = spin_density(parent, lepton, neutrino, dark, model, epsilon=epsilon)
    weak_mass = math.sqrt(weak_mass_squared)
    internal = (weak_mass_squared-model['lepton_mass_gev']**2)/(2*weak_mass)
    phase = dark[3]*internal/(128*math.pi**3*model['parent_mass_gev']**2*weak_mass)
    return {name: phase*value for name, value in densities.items()}


def decay_width(model, mass, *, epsilon, quadrature_order=24):
    """Gauss-Legendre three-body rate components; no A' dimuon branching.

    Compare quadrature orders for the actual requested mass/cut. The returned
    longitudinal and transverse rates are spin-density integrals in the kaon
    rest-frame polarization basis, rather than invented polarization fractions.
    """
    _number(mass, 'vector mass', positive=True)
    _number(epsilon, 'mixing', nonnegative=True)
    if epsilon > .1:
        raise ValueError('Require small perturbative mixing')
    if mass**2 <= 64*math.ulp(model['parent_mass_gev']**2):
        raise ValueError('Vector mass is below the resolved massive-polarization precision')
    if type(quadrature_order) is not int or quadrature_order < 4:
        raise ValueError('Explicit quadrature order of at least four required')
    low, high = model['lepton_mass_gev']**2, (model['parent_mass_gev']-mass)**2
    if mass >= model['parent_mass_gev']-model['lepton_mass_gev']:
        return dict(total=0., IB=0., SD=0., interference=0., transverse=0., longitudinal=0.)
    nodes, weights = np.polynomial.legendre.leggauss(quadrature_order)
    sums = {name: np.zeros((3, 3), dtype=complex) for name in ('total', 'IB', 'SD', 'interference')}
    for node, weight in zip(nodes, weights):
        weak_mass_squared = float((high+low)/2+(high-low)*node/2)
        for cosine, angular_weight in zip(nodes, weights):
            row = differential_width(model, mass, weak_mass_squared, float(cosine), epsilon=epsilon)
            for name in sums:
                sums[name] += weight*angular_weight*(high-low)/2*row[name]
    result = {name: float(np.trace(value).real) for name, value in sums.items()}
    result['transverse'] = float(sums['total'][0, 0].real+sums['total'][1, 1].real)
    result['longitudinal'] = float(sums['total'][2, 2].real)
    return result


@lru_cache(maxsize=128)
def _normalized_boost_spectrum(model_json,mass,quadrature_order):
    """Nominal-M0 O(p4) rest-spectrum authority; no parent-specific quadrature."""
    model=json.loads(model_json)
    low,high=model['lepton_mass_gev']**2,(model['parent_mass_gev']-mass)**2
    nodes,weights=np.polynomial.legendre.leggauss(quadrature_order)
    spectrum=[]
    for node,weight in zip(nodes,weights):
        fraction=float((node+1)/2)
        squared=low+(high-low)*fraction
        angular=math.fsum(float(angular_weight*np.trace(differential_width(
            model,mass,squared,float(cosine),epsilon=.001)['total']).real)
            for cosine,angular_weight in zip(nodes,weights))
        value=float(weight*(high-low)/2*angular)
        if not math.isfinite(value) or value<0:
            raise ValueError('Finite nonnegative full IB+SD rest-spectrum weight required')
        spectrum.append((fraction,value))
    total=math.fsum(value for _,value in spectrum)
    if not math.isfinite(total) or total<=0:
        raise ValueError('Open positive leptonic-kaon spectrum required')
    return tuple((fraction,value/total) for fraction,value in spectrum)


def mean_lab_beta_gamma(parent,mass,*,model,quadrature_order=24,
                        native_kinematic_precision=None,defer_to_full_source=False):
    """Rate-weighted LAB mean for the full three-body IB+SD spectrum.

    The spin-zero parent's overall direction is isotropic. At each W² node
    its angular mean is analytic; the W² weights integrate the full lepton
    angle and interference. The reference rest spectrum is cached at the
    declared model M0. The actual unchanged p4 invariant sets the boost and
    each node's physical endpoint. This tiny numerical mass continuation is
    accepted only within separately declared relative mass/endpoint limits;
    it is not a new source-rate or physical model-uncertainty prescription.
    """
    from dark_photon_vector_meson import mean_lab_beta_gamma as angular_mean
    parent=_p4(parent)
    _number(mass,'vector mass',positive=True)
    if type(quadrature_order) is not int or not 4<=quadrature_order<=128:
        raise ValueError('Explicit quadrature order between four and128 required')
    squared=math.fsum([parent[0]**2]+[-component**2 for component in parent[1:]])
    if parent[0]<=0 or squared<=0:
        raise ValueError('Future-directed timelike kaon required')
    event_mass=math.sqrt(squared);nominal=model['parent_mass_gev'];ml=model['lepton_mass_gev']
    limits=(dict(relative_mass_limit=1.e-7,relative_endpoint_limit=1.e-6)
        if native_kinematic_precision is None else native_kinematic_precision)
    if any(type(limits.get(key)) not in (int,float) or not math.isfinite(limits[key])
           or not 0<limits[key]<=1.e-5 for key in ('relative_mass_limit','relative_endpoint_limit')):
        raise ValueError('Explicit small numerical mass/endpoint continuation limits required')
    gap=nominal-mass-ml
    if gap<=0 or event_mass<=mass+ml:
        raise ValueError('Open leptonic-kaon channel required')
    delta=abs(event_mass-nominal)
    if type(defer_to_full_source) is not bool:raise ValueError('Full-source deferral requires an explicit boolean')
    if not defer_to_full_source and (delta/nominal>limits['relative_mass_limit'] or delta/gap>limits['relative_endpoint_limit']):
        raise ValueError('Mean-boost native/invariant mass or endpoint precision gate failed')
    key=json.dumps(model,sort_keys=True,separators=(',',':'),allow_nan=False)
    spectrum=_normalized_boost_spectrum(key,mass,quadrature_order)
    low,high=ml**2,(event_mass-mass)**2
    return math.fsum(weight*angular_mean(parent,math.sqrt(low+(high-low)*fraction),mass,
        polarization='unpolarized') for fraction,weight in spectrum)


def distinguishable_dilepton_differential_width(model, pair_mass, pair_lepton_mass,
                                               *, quadrature_order=24, component='total'):
    """dGamma(K -> l nu l'+l'-)/dq^2 for l != l', before QED corrections.

    This universal conserved-EM-current conversion is a continuum control,
    not a normalization replacement. Identical-lepton exchange is absent.
    """
    _number(pair_mass, 'pair mass', positive=True)
    _number(pair_lepton_mass, 'pair lepton mass', nonnegative=True)
    if pair_mass <= 2*pair_lepton_mass:
        return 0.
    if pair_lepton_mass == model['lepton_mass_gev']:
        raise ValueError('Identical leptons require the exchange amplitude')
    if component not in ('total', 'IB', 'SD', 'interference', 'transverse', 'longitudinal'):
        raise ValueError('Explicit rate component required')
    epsilon = .001
    width = decay_width(model, pair_mass, epsilon=epsilon,
        quadrature_order=quadrature_order)[component]/epsilon**2
    ratio = (pair_lepton_mass/pair_mass)**2
    return width*model['alpha_em']/(3*math.pi*pair_mass**2)*math.sqrt(1-4*ratio)*(1+2*ratio)


def production_cell_bound(model, mass, weak_interval, cosine_interval, *, _factors=None):
    """Analytic upper bound on dGamma/(dW² dcosθ), for epsilon=0.001.

    This is an amplitude bound, not a scanned maximum. In the kaon rest
    frame the positive spin projectors have operator norms 2Eν and 2Eℓ.
    Thus Trρ <= 4Eν Eℓ sum_a ||J_a||_F² <= EW² sum_a ||J_a||_F².
    The IB norm below uses its exact projected Dirac decomposition; SD uses
    its explicit transverse and longitudinal tensor components. The triangle
    inequality then bounds their coherent sum, including interference.

    Cell minima/maxima follow monotonic two-body energies and positive
    propagators. The longitudinal IB cancellation is performed algebraically
    before taking absolute values. Roundoff padding covers the scalar bound
    arithmetic; the sampler also rejects any observed envelope violation.
    """
    parent, ml = model['parent_mass_gev'], model['lepton_mass_gev']
    low, high = weak_interval
    c_low, c_high = cosine_interval
    if not (ml**2 <= low < high <= (parent-mass)**2 and -1 <= c_low < c_high <= 1):
        raise ValueError('Open physical production cell required')
    r_low = ml**2/high
    r_high = ml**2/low if low else 0.
    pmax = two_body_momentum(parent, math.sqrt(low), mass)
    pmin = two_body_momentum(parent, math.sqrt(high), mass)
    eqmax = (parent**2+mass**2-low)/(2*parent)
    ewmax = (parent**2+high-mass**2)/(2*parent)
    amax, amin = (parent**2-low-mass**2)/2, (parent**2-high-mass**2)/2
    bmax, bmin = parent*pmax, parent*pmin
    first = (1+r_low)*amin
    second = ((1-r_low)*bmax*c_high if c_high >= 0 else
              (1-r_high)*bmin*c_high)
    # Dℓ = q²+2ℓ.q >= mA²+2mℓmA independently of the cell.
    error = 64*np.finfo(float).eps*(abs(first)+abs(second)+mass**2)
    d_lepton = max((mass**2+2*ml*mass)*(1-64*np.finfo(float).eps),
                  first-second+mass**2-error)
    d_parent = (parent**2-high)*(1-64*np.finfo(float).eps)
    internal_max = (high-ml**2)/(2*math.sqrt(high))
    closest_cosine = 0. if c_low <= 0 <= c_high else min(abs(c_low), abs(c_high))
    lxmax = internal_max*math.sqrt(max(0., 1-closest_cosine**2))
    # C_L = mA(1-r)[MK p+(2W²+q.W)c]/(DP Dℓ).
    longitudinal_ib = (mass*(1-r_low)*(bmax+(2*high+amax)*max(abs(c_low), abs(c_high)))/
                       (d_parent*d_lepton))
    ib_norm_squared = 8*(model['fk_gev']*ml)**2*(
        (4*lxmax**2+2*(eqmax**2+pmax**2)+mass**2)/d_lepton**2+longitudinal_ib**2)
    factors = form_factors(mass**2, (low+high)/2, model) if _factors is None else _factors
    v, a1, a2 = [complex(factors[name]) for name in ('V1', 'A1', 'A2')]
    # A4=-A2/(MK²-W²), including the IB-only zero-current control.
    a4max = abs(a2)/d_parent
    transverse = abs(v)**2*parent**2*pmax**2+(abs(a1)*amax+abs(a2)*mass**2)**2
    temporal = mass*pmax*(abs(a1-a2)+a4max*parent*ewmax)
    spatial = mass*(abs(a1)*ewmax+abs(a2)*eqmax+a4max*parent*pmax**2)
    sd_norm_squared = 8*(2*transverse+temporal**2+spatial**2)
    current_bound = (math.sqrt(ib_norm_squared)+math.sqrt(sd_norm_squared))**2
    phase = pmax*(1-r_low)/(256*math.pi**3*parent**2)
    coefficient = (1.e-6*4*math.pi*model['alpha_em']*
                   model['fermi_constant_gev_minus2']**2*model['v_us']**2)
    bound = phase*coefficient*ewmax**2*current_bound
    return math.nextafter(bound*(1+512*np.finfo(float).eps), math.inf)


class LeptonicProductionProposal:
    """Finite analytic envelopes for exact unit-weight rejection sampling.

    Cells are selected with probability proportional to their area times
    bound, then sampled uniformly and rejected against the full spin trace.
    The accepted density is therefore the target differential width divided
    by its integral. No rate rescaling, event weights or acceptance cut enters.
    Changing the partition changes speed only. Endpoints have zero measure.
    """

    def __init__(self, model, mass, *, weak_cells=12, angular_cells=8):
        self.model, self.mass = dict(model), _number(mass, 'vector mass', positive=True)
        parent, ml = model['parent_mass_gev'], model['lepton_mass_gev']
        if mass <= 2*MUON_MASS_GEV or mass >= parent-ml:
            raise ValueError('Open leptonic-kaon and dimuon channels required')
        if any(type(count) is not int or count < 1 for count in (weak_cells, angular_cells)):
            raise ValueError('Positive integer proposal cell counts required')
        low, high = ml**2, (parent-mass)**2
        # V1,A1,A2 depend only on q² at this order; A4's W² denominator is
        # bounded explicitly in each cell. Avoid repeating the same loops.
        factors = form_factors(mass**2, (low+high)/2, model)
        self.cells, self.cumulative = [], []
        total = 0.
        for i in range(weak_cells):
            weak = (low if i == 0 else low+(high-low)*i/weak_cells,
                    high if i+1 == weak_cells else low+(high-low)*(i+1)/weak_cells)
            for j in range(angular_cells):
                angle = (-1+2*j/angular_cells, -1+2*(j+1)/angular_cells)
                bound = production_cell_bound(model, mass, weak, angle, _factors=factors)
                total += bound*(weak[1]-weak[0])*(angle[1]-angle[0])
                self.cells.append((weak, angle, bound))
                self.cumulative.append(total)
        if not math.isfinite(total) or total <= 0:
            raise ValueError('Finite positive leptonic proposal normalization required')
        self.envelope_integral = total

    def sample(self, rng, *, maximum_trials=1000000):
        """Return a rest-frame production point and its full spin density."""
        if type(maximum_trials) is not int or maximum_trials < 1:
            raise ValueError('Explicit positive rejection trial limit required')
        for trial in range(1, maximum_trials+1):
            index = min(bisect.bisect_right(self.cumulative,
                rng.random()*self.envelope_integral), len(self.cells)-1)
            weak, angle, bound = self.cells[index]
            # An RNG may legally return zero. Do not evaluate the singular
            # zero-measure W endpoint or a zero-energy neutrino there.
            squared = weak[0]+(weak[1]-weak[0])*rng.random()
            if not self.model['lepton_mass_gev']**2 < squared < (self.model['parent_mass_gev']-self.mass)**2:
                continue
            cosine = angle[0]+(angle[1]-angle[0])*rng.random()
            point = rest_kinematics(self.model['parent_mass_gev'],
                self.model['lepton_mass_gev'], self.mass, squared, cosine)
            density = spin_density(*point, self.model, epsilon=.001)['total']
            weak_mass = math.sqrt(squared)
            internal = (squared-self.model['lepton_mass_gev']**2)/(2*weak_mass)
            phase = point[3][3]*internal/(128*math.pi**3*self.model['parent_mass_gev']**2*weak_mass)
            target = phase*float(np.trace(density).real)
            if not math.isfinite(target) or target < -64*math.ulp(bound):
                raise RuntimeError('Invalid leptonic production density')
            if target > bound:
                raise RuntimeError('Analytic leptonic production envelope violated')
            if rng.random()*bound < target:
                return dict(point=point, density=density, weak_mass_squared=squared,
                    lepton_helicity_cosine=cosine, proposal_trials=trial,
                    envelope_integral=self.envelope_integral)
        raise RuntimeError('Leptonic production rejection trial limit exhausted')


def sample_vector_muon_direction(density, mass, rng, *, maximum_trials=10000):
    """Exact conditional vector-current dimuon angle in the density basis.

    After the muon spin sum D_ij is proportional to δ_ij-β²n_i n_j.
    For positive ρ, 0 <= n^T Reρ n <= Trρ, so Trρ is a proven rejection
    envelope. Its mean efficiency is 1-β²/3, at least two thirds. Imaginary
    circular polarization cannot be measured by this parity-even decay;
    real off-diagonal entries retain the azimuthal correlations.
    """
    _number(mass, 'vector mass', positive=True)
    if type(maximum_trials) is not int or maximum_trials < 1:
        raise ValueError('Explicit positive angular trial limit required')
    rho = np.asarray(density, dtype=complex)
    if rho.shape != (3, 3) or not np.all(np.isfinite(rho)) or mass <= 2*MUON_MASS_GEV:
        raise ValueError('Finite massive-vector density and open dimuon channel required')
    trace = float(np.trace(rho).real)
    tolerance = 128*np.finfo(float).eps*trace
    if trace <= 0 or np.max(np.abs(rho-rho.conjugate().T)) > tolerance:
        raise ValueError('Positive Hermitian production density required')
    # Pivoted Schur complement: its first pivot is at least Trρ/3, so no
    # small diagonal is divided out. A stable 2x2 eigenvalue then tests PSD.
    # A LAPACK call per decay can start an expensive threaded BLAS team.
    normalized = rho/trace
    pivot = int(np.argmax(normalized.diagonal().real))
    first, second = [index for index in range(3) if index != pivot]
    denominator = normalized[pivot, pivot].real
    d1 = (normalized[first, first]-abs(normalized[first, pivot])**2/denominator).real
    d2 = (normalized[second, second]-abs(normalized[second, pivot])**2/denominator).real
    off = normalized[first, second]-normalized[first, pivot]*normalized[pivot, second]/denominator
    minimum = (d1+d2-math.hypot(d1-d2, 2*abs(off)))/2
    if minimum < -128*np.finfo(float).eps:
        raise ValueError('Positive Hermitian production density required')
    beta_squared = 1-4*(MUON_MASS_GEV/mass)**2
    for trial in range(1, maximum_trials+1):
        cosine, phi = 2*rng.random()-1, 2*math.pi*rng.random()
        sine = math.sqrt(max(0., 1-cosine*cosine))
        direction = np.array([sine*math.cos(phi), sine*math.sin(phi), cosine])
        acceptance = 1-beta_squared*float(direction@rho.real@direction)/trace
        if not -128*np.finfo(float).eps <= acceptance <= 1+128*np.finfo(float).eps:
            raise RuntimeError('Vector dimuon angular envelope violated')
        if rng.random() < acceptance:
            return direction, trial
    raise RuntimeError('Vector dimuon angular rejection trial limit exhausted')


def _isotropic_rotation(rng):
    """Haar SO(3): isotropic third axis and uniform rotation around it."""
    cosine, phi, psi = 2*rng.random()-1, 2*math.pi*rng.random(), 2*math.pi*rng.random()
    sine = math.sqrt(max(0., 1-cosine*cosine))
    third = np.array([sine*math.cos(phi), sine*math.sin(phi), cosine])
    first = np.array([cosine*math.cos(phi), cosine*math.sin(phi), -sine])
    second = np.array([-math.sin(phi), math.cos(phi), 0.])
    return np.column_stack((math.cos(psi)*first+math.sin(psi)*second,
        -math.sin(psi)*first+math.cos(psi)*second, third))


def _boost_from_parent_rest(vector, parent, rest_mass):
    """Use γ=E/M directly, preserving a highly boosted native parent sum.

    Reconstructing γ from 1-β² discards the small rest mass at native kaon
    boosts. This is the same Lorentz transformation with the supplied parent
    invariant mass, without repairing/rescaling its four-vector.
    """
    product = math.fsum(float(p)*float(v) for p, v in zip(parent[1:], vector[1:]))
    energy = (parent[0]*vector[0]+product)/rest_mass
    shift = vector[0]/rest_mass+product/(rest_mass*(parent[0]+rest_mass))
    return (float(energy), *(float(v+shift*p) for v, p in zip(vector[1:], parent[1:])))


def sample_decay(parent, mass, proper_ctau_mm, rng, *, model, parent_pdg_id=321,
                 maximum_trials=1000000, parent_mass_relative_tolerance=0.):
    """K± -> ℓ±ν A′ -> ℓ±ν μ+μ− with full correlated polarization.

    K− uses CP reflection and charge exchange of the whole K+ chain, assuming
    real Vus and CP-conserving strong/electromagnetic currents. Absorptive
    strong form factors are not conjugated. The source must apply BRμμ once.

    Exact daughter closure uses the incoming four-vector's invariant mass.
    The model's physical parent mass remains separately recorded. A native
    source must qualify any additional inherited mass precision budget before
    passing parent_mass_relative_tolerance. The default covers only the
    scalar boost/invariant arithmetic at the incoming energy scale. This
    helper neither repairs the parent nor attaches its native endvertex.
    """
    parent = _p4(parent)
    if parent[0] <= 0 or parent_pdg_id not in (321, -321):
        raise ValueError('Future-directed charged kaon required')
    squared = math.fsum([parent[0]**2]+[-component**2 for component in parent[1:]])
    if squared <= 0:
        raise ValueError('Timelike kaon required')
    proper_ctau_mm = _number(proper_ctau_mm, 'dark-photon proper lifetime', positive=True)
    _number(parent_mass_relative_tolerance, 'inherited relative mass-squared precision', nonnegative=True)
    event_mass = math.sqrt(squared)
    shell_tolerance = (64*math.ulp(max(float(np.max(np.abs(parent)))**2,
        model['parent_mass_gev']**2))+parent_mass_relative_tolerance*model['parent_mass_gev']**2)
    if abs(squared-model['parent_mass_gev']**2) > shell_tolerance:
        raise ValueError('Incoming kaon mass is outside the explicit arithmetic precision budget')
    ml = model['lepton_mass_gev']
    if math.isclose(ml, MUON_MASS_GEV, rel_tol=1.e-12):
        lepton_abs_pdg = 13
    elif math.isclose(ml, .00051099895, rel_tol=1.e-12):
        lepton_abs_pdg = 11
    else:
        raise ValueError('The event sampler requires the physical electron or muon mass')
    event_model = dict(model, parent_mass_gev=event_mass)
    proposal = LeptonicProductionProposal(event_model, mass)
    event = proposal.sample(rng, maximum_trials=maximum_trials)
    _, lepton, neutrino, dark = event['point']
    direction, angular_trials = sample_vector_muon_direction(event['density'], mass, rng)
    basis = polarization_basis(dark)
    dark_beta = tuple(component/dark[0] for component in dark[1:])
    rest_axes = np.array([boost(state, tuple(-value for value in dark_beta))[1:] for state in basis])
    muon_momentum = math.sqrt((mass/2-MUON_MASS_GEV)*(mass/2+MUON_MASS_GEV))
    spatial = muon_momentum*(direction@rest_axes)
    muon, antimuon = [boost((mass/2, *(sign*spatial)), dark_beta) for sign in (1., -1.)]
    rotation = _isotropic_rotation(rng)
    rest_vectors = [np.asarray(vector) for vector in (lepton, neutrino, dark, muon, antimuon)]
    # Parity reverses every spatial momentum; charge conjugation exchanges
    # the DP muon labels and the primary charged lepton/neutrino identities.
    if parent_pdg_id == -321:
        rest_vectors = [vector*METRIC for vector in rest_vectors]
        rest_vectors[3], rest_vectors[4] = rest_vectors[4], rest_vectors[3]
    moved = [_boost_from_parent_rest((vector[0], *(rotation@vector[1:])), parent, event_mass)
             for vector in rest_vectors]
    proper = -proper_ctau_mm*math.log1p(-rng.random())
    lepton, neutrino, dark, muon, antimuon = moved
    sign = 1 if parent_pdg_id == 321 else -1
    return dict(dark_photon_p4=dark, charged_lepton_p4=lepton, neutrino_p4=neutrino,
        muon_p4=muon, antimuon_p4=antimuon, charged_lepton_pdg_id=-sign*lepton_abs_pdg,
        neutrino_pdg_id=sign*(lepton_abs_pdg+1), parent_pdg_id=parent_pdg_id,
        recoil_p4s=[lepton, neutrino], recoil_pdg_ids=[-sign*lepton_abs_pdg, sign*(lepton_abs_pdg+1)],
        recoil_masses_gev=[model['lepton_mass_gev'], 0.], recoil_natural_decay_required=True,
        dark_photon_mass_gev=mass, muon_mass_gev=MUON_MASS_GEV,
        charged_lepton_mass_gev=model['lepton_mass_gev'],
        physical_parent_mass_gev=model['parent_mass_gev'], event_parent_mass_gev=event_mass,
        parent_mass_squared_precision_budget_gev2=shell_tolerance,
        proper_decay_length_mm=proper, decay_displacement_mm=tuple(proper*p/mass for p in dark[1:]),
        decay_ct_mm=proper*dark[0]/mass, weak_mass_squared=event['weak_mass_squared'],
        lepton_helicity_cosine=event['lepton_helicity_cosine'],
        muon_direction_in_kplus_proposal_basis=tuple(float(value) for value in direction),
        production_density_matrix_real=event['density'].real.tolist(),
        production_density_matrix_imaginary=event['density'].imag.tolist(),
        density_basis='canonical-K+-rest-linear-polarizations-before-rotation-and-CP',
        production_proposal_trials=event['proposal_trials'], angular_proposal_trials=angular_trials,
        dark_photon_polarization='full-three-state-density', parent_polarization='spin-zero',
        cp_conjugate=parent_pdg_id == -321, unconditional_decay=True,
        parent_flight_included=False, native_parent_flight_attached=False,
        material_transport_qualified=False)
