# Code style: AI-code. AI-generated gauge-invariant leptonic-kaon rate kernel.
"""K+ -> l+ nu A' at leading mixing order with O(p^4) chiral currents.

Bijnens, Ecker and Gasser, hep-ph/9209261 Eqs.(4.8)--(4.22), Appendix A.
The amplitude is epsilon*e*GF*Vus times (FK*L^rho-H^{rho mu}*l_mu).
Both charged external legs occur in IB. The transverse SD tensor includes
the virtual-only A2,A4 terms and the complex charged-kaon EM form factor.
Replacing a real-photon branching fraction would omit these contributions.

This standalone rate library does not attach native parent endvertices or
simulate material. It retains the full three-polarization density, IB, SD and
their interference, so a future event sampler must not replace it by an
isotropic A' decay. Production rates exclude BR(A' -> mu+mu-). The continuum
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

import numpy as np

from dark_photon_meson import boost
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
    lepton_rest = (math.hypot(internal, lepton_mass),
        internal*math.sqrt(1-cosine**2), 0., internal*cosine)
    neutrino_rest = (internal, -lepton_rest[1], 0., -lepton_rest[3])
    beta = (0., 0., -momentum/weak_energy)
    lepton, neutrino = boost(lepton_rest, beta), boost(neutrino_rest, beta)
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
