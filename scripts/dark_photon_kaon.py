# Code style: AI-code. AI-generated neutral-kaon electromagnetic rate kernel.
"""K_L/S -> gamma A' amplitudes and explicit singly-virtual form factors.

The dominant CP-conserving K_L amplitude is the antisymmetric tensor in
D'Ambrosio, Isidori and Portoles, hep-ph/9708326 Eq.(10). The K_S amplitude
uses the CP-even tensor in Colangelo, Stucki and Tunstall, arXiv:1609.03574
Eqs.(15),(16). They give the same unpolarized two-body phase-space ratio,
but have different weak amplitudes and form factors. Small CP-violating
admixtures, initial kaon coherence and matter regeneration are not supplied.

The K_L DIP and BMS shapes follow KTeV, hep-ex/0702039 Eqs.(1),(2). All fit
parameters, pole masses, normalization and domains are explicit caller inputs.
K_S requires its own dispersive amplitude: no eta/K_L shape is substituted.
Real-photon branching fractions fix q2=0, not a bound at finite dark mass.

Four-vectors are (E,px,py,pz), GeV, with metric (+---). The radiative weight
excludes BR(A' -> mu mu), parent yields, kaon flight and detector acceptance.
No source or complete production model is qualified by this library alone.
"""
import cmath
import bisect
import hashlib
from itertools import permutations
import json
import math
from pathlib import Path

from dark_photon_meson import radiative_branching as spin_zero_radiative_branching
from dark_photon_meson import sample_decay as spin_zero_radiative_sample


REFERENCES = {
    'kl_amplitude': 'https://arxiv.org/pdf/hep-ph/9708326#page=4',
    'kl_measured_shapes': 'https://arxiv.org/pdf/hep-ex/0702039#page=1',
    'ks_dispersive_amplitude': 'https://arxiv.org/pdf/1609.03574#page=3',
}

KS_REFERENCE_FIGURE_SHA256 = '3026ba71009e40a849fca5220f61a14656a47b82bc4bf1f2be3d57202fc2e6c0'


class KSDispersiveReference:
    """Explicit, hash-pinned complex KS amplitude from published Figure 8.

    The source is the original vector drawing, not a newly solved dispersion
    relation. Bands retain the paper's pointwise systematic variation; their
    correlations and statistical coverage are unavailable. In particular, a
    rectangle enclosing these bands is not a model-independent upper bound.
    The table stops before the physical endpoint and is never extrapolated.
    """
    def __init__(self, path, expected_sha256):
        payload = Path(path).read_bytes()
        if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
                or hashlib.sha256(payload).hexdigest() != expected_sha256):
            raise ValueError('An exact pinned KS reference-table SHA-256 is required')
        table = json.loads(payload)
        if (table.get('schema') != 'shift-ks-dispersive-figure-reference-v1'
                or table.get('source', {}).get('figure_sha256') != KS_REFERENCE_FIGURE_SHA256
                or table.get('units') != dict(q2='GeV^2', amplitude='GeV^-1')
                or table.get('columns') != ['q2', 'real', 'imaginary', 'real_low',
                                           'real_high', 'imaginary_low', 'imaginary_high']):
            raise ValueError('Unsupported KS reference-table identity or units')
        rows = table.get('rows', [])
        if len(rows) < 2 or any(len(row) != 7 or any(type(value) not in (int, float)
                or not math.isfinite(value) for value in row) for row in rows):
            raise ValueError('A finite KS amplitude and its pointwise bands are required')
        if (rows[0][0] != 0. or any(a[0] >= b[0] for a, b in zip(rows, rows[1:]))
                or table.get('domain_q2_gev2') != [rows[0][0], rows[-1][0]]
                or any(not row[3] <= row[1] <= row[4] or not row[5] <= row[2] <= row[6]
                       for row in rows)):
            raise ValueError('KS grid, domain or published-band ordering is invalid')
        self.rows = tuple(tuple(row) for row in rows)
        self.q2 = tuple(row[0] for row in rows)
        self.sha256 = expected_sha256
        self.publication = table['publication']

    def _at_mass(self, mass):
        _real(mass, 'dark-photon mass')
        q2 = mass**2
        if mass < 0 or not self.q2[0] <= q2 <= self.q2[-1]:
            raise ValueError('Requested mass lies outside the drawn KS reference domain')
        right = min(max(bisect.bisect_right(self.q2, q2), 1), len(self.q2)-1)
        first, second = self.rows[right-1:right+1]
        fraction = (q2-first[0])/(second[0]-first[0])
        return tuple(a+fraction*(b-a) for a, b in zip(first[1:], second[1:]))

    def amplitude(self, mass):
        """Full CP-even coefficient A_gamma_gamma*(m^2), in GeV^-1."""
        row = self._at_mass(mass)
        return complex(row[0], row[1])

    def form_factor(self, mass):
        """Central A(m^2)/A(0), retaining the complex phase."""
        return self.amplitude(mass)/self.amplitude(0.)

    def amplitude_abs2_range(self, mass):
        """Extrema over the rectangle of published Re/Im bands, GeV^-2.

        These may vary the two components independently. They enclose the
        plotted band and do not assign it a probability or unknown covariance.
        """
        _, _, real_low, real_high, imaginary_low, imaginary_high = self._at_mass(mass)
        minimum = sum(0. if low <= 0 <= high else min(low**2, high**2)
                      for low, high in [(real_low, real_high), (imaginary_low, imaginary_high)])
        maximum = max(real_low**2, real_high**2)+max(imaginary_low**2, imaginary_high**2)
        return minimum, maximum

    def gamma_gamma_width(self, parent_mass):
        """Gamma(KS -> gamma gamma), GeV, from the same absolute amplitude."""
        _real(parent_mass, 'parent mass', positive=True)
        return parent_mass**3*abs(self.amplitude(0.))**2/(64*math.pi)

    def radiative_width(self, parent_mass, mass, epsilon, *, band=False):
        """Leading Gamma(KS -> gamma A'), GeV; excludes A' decay and flight.

        If band=True, return the pointwise published rectangle envelope.
        No measured photon branching ratio is used to rescale this amplitude.
        """
        _real(parent_mass, 'parent mass', positive=True)
        _real(mass, 'dark-photon mass')
        _real(epsilon, 'kinetic mixing')
        if mass < 0 or epsilon < 0:
            raise ValueError('Nonnegative mass and kinetic mixing are required')
        if mass >= parent_mass:
            return (0., 0.) if band else 0.
        factor = epsilon**2*parent_mass**3/(32*math.pi)*(1-mass**2/parent_mass**2)**3
        if band:
            return tuple(factor*value for value in self.amplitude_abs2_range(mass))
        return factor*abs(self.amplitude(mass))**2


def _kaon(parent_pdg_id):
    if type(parent_pdg_id) is not int or parent_pdg_id not in (130, 310):
        raise ValueError('Use an explicit physical K_L (130) or K_S (310) state')


def _real(value, name, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or positive and value <= 0:
        raise ValueError('Finite '+('positive ' if positive else '')+name+' is required')
    return value


def native_source_mass_precision(parent_mass, native_context, *, expected_p4=None,
                                 expected_pdg_id=None):
    """Propagate the checked source-frame boost arithmetic into a mass bound.

    A low laboratory energy can be the difference of two large boosted
    components. Its rounding scale is therefore not E_lab alone. The raw
    source beam vector and independently certified COM intervals bound each
    intermediate component by gamma_source*(1+beta_source)*E_COM. Use the
    existing64-operation binary64 budget for Lorentz-coefficient construction
    and the four-term matrix products (Pythia8.317 Basics.cc, Vec4::rotbst),
    then propagate component errors through the invariant mass explicitly.

    This validates an immutable native record, not arbitrary off-shell
    inputs. The original finite source-table support, COM/count authority,
    p4 and physical current/precision gates remain required. The bound says
    nothing about physical parent-width or hadronic-model uncertainties.
    """
    _real(parent_mass,'requested native mass',positive=True)
    if not isinstance(native_context,dict):
        raise ValueError('Require a complete checked native source context')
    record=native_context.get('native_source_record');authority=native_context.get('authority')
    if not isinstance(record,dict) or not isinstance(authority,dict):
        raise ValueError('Require the native source record and independent COM authority')
    pdg=record.get('pdg_id');index=record.get('index')
    if (type(pdg) is not int or pdg not in (130,310,321,-321)
            or expected_pdg_id is not None and pdg!=expected_pdg_id
            or type(index) is not int or index<=0 or record.get('native_final') is not True
            or record.get('native_end_vertex_present') is not False
            or type(record.get('native_status')) is not int or record['native_status']<=0
            or record.get('daughter_indices')!=[]):
        raise ValueError('Require the original physical final-kaon record')
    def vector(name):
        value=record.get(name)
        if (not isinstance(value,(list,tuple)) or len(value)!=4
                or any(type(x) not in (int,float) or not math.isfinite(x) for x in value)):
            raise ValueError('Require the complete finite native '+name)
        return list(value)
    p=vector('p4');q=vector('source_total_p4')
    vector('production_position_mm');vector('stored_decay_position_mm')
    _real(record.get('selected_proper_lifetime_mm'),'stored native proper flight')
    _real(record.get('mean_proper_lifetime_mm'),'native mean lifetime',positive=True)
    if record['selected_proper_lifetime_mm']<0 or p[0]<=0 or q[0]<=0:
        raise ValueError('Require physical native flight and future-directed source momenta')
    mothers=record.get('mother_indices')
    if (not isinstance(mothers,list) or any(type(x) is not int or x<=0 for x in mothers)
            or index in mothers):
        raise ValueError('Invalid immutable native kaon ancestry')
    if expected_p4 is not None and tuple(expected_p4)!=tuple(p):
        raise ValueError('Requested momentum differs from the immutable native source record')
    native=_real(record.get('native_mass_gev'),'native table mass',positive=True)
    support=native_context.get('native_record_mass_support_gev')
    if (not isinstance(support,list) or not support
            or any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in support)
            or support!=sorted(set(support)) or native not in support):
        raise ValueError('Native mass is outside its exact finite source-table support')
    source_ecm=_real(native_context.get('source_ecm_gev'),'declared source ECM',positive=True)
    if authority.get('source_total_p4')!=q or authority.get('stable_native_mass_floor_gev')!=native:
        raise ValueError('Native record differs from its independent source authority')
    unit=2.**-53
    gamma32=32*unit/(1-32*unit)
    qterms=[x*x for x in q];q2=math.fsum([qterms[0]]+[-x for x in qterms[1:]])
    dq=gamma32*math.fsum(qterms)
    products=[a*b for a,b in zip(p,q)]
    numerator=math.fsum([products[0]]+[-x for x in products[1:]])
    dn=gamma32*math.fsum(abs(x) for x in products)
    if q2<=dq or numerator<=dn:
        raise ValueError('Native source frame has no certified timelike COM interval')
    ecm_low,ecm_high=math.sqrt(q2-dq),math.sqrt(q2+dq)
    low,high=(numerator-dn)/ecm_high,(numerator+dn)/ecm_low
    com_energy=_real(record.get('source_com_energy_gev'),'native COM energy',positive=True)
    arithmetic=max(com_energy-low,high-com_energy)
    if (not low<=com_energy<=high or not ecm_low<=source_ecm<=ecm_high
            or authority.get('source_com_energy_gev')!=com_energy
            or authority.get('independent_com_energy_interval_gev')!=[low,high]
            or authority.get('independent_source_ecm_interval_gev')!=[ecm_low,ecm_high]
            or authority.get('com_energy_arithmetic_allowance_gev')!=arithmetic):
        raise ValueError('Native source COM certificate disagrees with its raw momentum inputs')
    variants=[]
    for terms in ([p[0]**2]+[-x*x for x in p[1:]],
                  [p[0]**2]+[-x**2 for x in p[1:]],
                  [p[0]*p[0]]+[-x*x for x in p[1:]]):
        squared=math.fsum(terms)
        if squared<=0:raise ValueError('Native kaon invariant has no resolved timelike mass')
        variants.append(math.sqrt(squared))
    if parent_mass not in [native,*variants]:
        raise ValueError('Requested mass differs from the immutable native source record')
    native_delta=abs(variants[0]-native)
    floor=min(min(support),low)-2*arithmetic
    count=authority.get('native_parent_count_bound')
    if (authority.get('native_invariant_mass_difference_gev')!=native_delta
            or authority.get('finite_support_minimum_mass_gev')!=min(support)
            or authority.get('roundoff_mass_floor_gev')!=floor or floor<=0
            or type(count) is not int or count<=0
            or count!=math.floor(ecm_high/floor) or count!=math.floor(source_ecm/min(support))):
        raise ValueError('Native mass/COM arithmetic changes its original source-count certificate')
    source_gamma=q[0]/ecm_low
    source_beta=min(1.,math.sqrt(math.fsum(qterms[1:]))/q[0]*(1+gamma32))
    scale=max(source_gamma*(1+source_beta)*high,*map(abs,p))
    gamma64=64*unit/(1-64*unit)
    component_error=gamma64*scale
    gamma8=8*unit/(1-8*unit)
    invariant_error=(2*component_error*math.fsum(abs(x) for x in p)+4*component_error**2+
        gamma8*math.fsum(x*x for x in p)+2*unit*native*native)*(1+gamma64)
    allowance=invariant_error/(native+min(variants))+8*max(math.ulp(x) for x in [native,*variants])
    if max(abs(value-native) for value in variants)>allowance:
        raise ValueError('Native invariant mass exceeds its propagated source-frame roundoff bound')
    return dict(native_mass_gev=native,kinematic_mass_gev=variants[0],
        mass_allowance_gev=allowance,source_boost_scale_gev=scale,
        component_arithmetic_allowance_gev=component_error,
        source_gamma_upper=source_gamma,source_beta_upper=source_beta,
        operation_budget=64,requested_mass_gev=parent_mass)


def _four_vector(vector):
    if len(vector) != 4 or not all(cmath.isfinite(value) for value in vector):
        raise ValueError('Finite four-vectors are required')


def _minkowski_dot(first, second):
    return first[0]*second[0]-sum(a*b for a, b in zip(first[1:], second[1:]))


def _hermitian_rate_matrix(matrix, name, *, positive_definite=False):
    if len(matrix) != 2 or any(len(row) != 2 for row in matrix):
        raise ValueError(name+' must be a two-state Hermitian rate matrix')
    if any(not cmath.isfinite(value) for row in matrix for value in row):
        raise ValueError(name+' must contain finite rates')
    a, z, other, d = map(complex, [matrix[0][0], matrix[0][1], matrix[1][0], matrix[1][1]])
    if a.imag != 0. or d.imag != 0. or z != other.conjugate():
        raise ValueError(name+' must be exactly Hermitian')
    a, d = a.real, d.real
    determinant = a*d-abs(z)**2
    if a < 0 or d < 0 or determinant < 0:
        raise ValueError(name+' must be positive semidefinite')
    if positive_definite and (a <= 0 or determinant <= 0):
        raise ValueError(name+' must be positive definite')
    return a, z, d


def amplitude_rate_matrix(amplitudes, *, authority):
    """Sum a_i* a_j over explicitly orthogonal final-state amplitudes.

    Each supplied row is (A_CP_even,A_CP_odd), including its common phase
    space and units so |A|^2 is a partial rate. Do not add interfering final
    states as separate rows or substitute branching fractions for rates.
    Authority is an explicit reference to the caller's amplitude/rate inputs;
    this algebra does not establish that they enclose the physical model.
    """
    if not isinstance(authority, str) or not authority.strip() or not amplitudes:
        raise ValueError('Explicit nonempty amplitude rows and rate authority are required')
    if any(len(row) != 2 or any(not cmath.isfinite(value) for value in row) for row in amplitudes):
        raise ValueError('Finite amplitudes for both orthonormal kaon states are required')
    first = math.fsum(abs(row[0])**2 for row in amplitudes)
    second = math.fsum(abs(row[1])**2 for row in amplitudes)
    off_diagonal = sum(complex(row[0]).conjugate()*row[1] for row in amplitudes)
    return ((first, off_diagonal), (off_diagonal.conjugate(), second))


def positive_operator_rate_ceiling(signal, total_decay, *, authority):
    """Smallest B with Gamma_signal <= B Gamma_decay, for two states.

    Both matrices use the same orthonormal basis, proper-time units, and
    physical normalization. Gamma_decay must be strictly positive. A Cholesky
    congruence reduces the inequality to the analytic largest eigenvalue of
    a 2x2 Hermitian matrix; no numerical eigensolver is required.

    For any positive density matrix evolving under arbitrary coherent mixing
    and additional trace-decreasing absorption, dTr(rho)/dt <=
    -Tr(Gamma_decay rho). Thus its integrated signal probability is <=
    min(1,B)*Tr(rho_initial), independently of the material mixing history.
    NEW SECONDARY KAONS ARE EXCLUDED: an injected parent adds new trace and
    needs its own source bound. A regenerated component of the same surviving
    parent is covered. Signal/total-rate authority and its uncertainties are
    caller inputs, not conclusions of this algebraic bound.
    """
    if not isinstance(authority, str) or not authority.strip():
        raise ValueError('An explicit signal and total-decay rate authority is required')
    s, z, t = _hermitian_rate_matrix(signal, 'Signal')
    a, w, d = _hermitian_rate_matrix(total_decay, 'Total decay', positive_definite=True)
    if s == 0. and t == 0.:
        return 0.
    # H = L^-1 Gamma_signal L^-dagger, Gamma_decay=L L^dagger.
    remainder = d-abs(w)**2/a
    h00 = s/a
    h01 = (z-s*w/a)/(math.sqrt(a)*math.sqrt(remainder))
    h11 = (t-2*(w.conjugate()*z).real/a+s*abs(w)**2/a**2)/remainder
    ceiling = .5*(h00+h11+math.hypot(h00-h11, 2*abs(h01)))
    if not math.isfinite(ceiling) or ceiling < 0:
        raise ValueError('Rate matrices are too ill-conditioned for a finite ceiling')
    # Round upward until the original matrix inequality also passes, instead
    # of accepting a slightly low eigenvalue from floating-point arithmetic.
    for _ in range(128):
        first, last = ceiling*a-s, ceiling*d-t
        cross = ceiling*w-z
        if first >= 0 and last >= 0 and first*last >= abs(cross)**2:
            return ceiling
        ceiling = math.nextafter(ceiling, math.inf)
    raise ValueError('Could not verify the rounded positive-operator inequality')


def cp_diagonal_rate_ceiling(br_short, br_long):
    """CP-conserving shortcut: max of the two physical branching ceilings."""
    for value in (br_short, br_long):
        _real(value, 'branching ceiling')
        if not 0 <= value <= 1:
            raise ValueError('A branching ceiling must lie between zero and one')
    return max(br_short, br_long)


def cpt_mixing_decay_matrix(width_short, width_long, delta_mass, epsilon_mixing):
    """Total decay operator in the orthonormal (CP-even, CP-odd) basis.

    This CPT-conserving mixing model uses eigenvectors (1,epsilon) and
    (epsilon,1) and physical eigenvalues -i*GammaS/2, DeltaM-i*GammaL/2.
    Gamma=i*(H-H^dagger). All widths and DeltaM must use the same units.
    No measured pion-decay epsilon is silently promoted to epsilon_mixing:
    that identification and any direct CP violation are caller authorities.
    """
    for value in (width_short, width_long):
        _real(value, 'physical kaon width', positive=True)
    _real(delta_mass, 'kaon mass difference')
    if not cmath.isfinite(epsilon_mixing) or abs(epsilon_mixing) >= 1:
        raise ValueError('Finite CPT mixing epsilon with magnitude below one is required')
    eigen_short = -.5j*width_short
    eigen_long = delta_mass-.5j*width_long
    denominator = 1-epsilon_mixing**2
    h00 = (eigen_short-epsilon_mixing**2*eigen_long)/denominator
    h11 = (eigen_long-epsilon_mixing**2*eigen_short)/denominator
    h01 = epsilon_mixing*(eigen_long-eigen_short)/denominator
    cross = 2j*h01.real
    result = ((-2*h00.imag, cross), (cross.conjugate(), -2*h11.imag))
    _hermitian_rate_matrix(result, 'CPT-mixing total decay', positive_definite=True)
    return result


def cpt_mixing_decay_lower_factor(width_short, width_long, delta_mass, epsilon_abs_ceiling):
    """Uniform Gamma_decay >= c*diag(GammaS,GammaL), all mixing phases.

    The supplied radius |epsilon_mixing|<=r is an explicit conditional input,
    not a confidence limit inferred by this function. Bound diagonal H shifts
    by r^2*|lambdaL-lambdaS|/(1-r^2), and the decay off-diagonal by
    2*r*|lambdaL-lambdaS|/(1-r^2). A Hermitian operator-norm bound after
    diagonal whitening supplies c. Direct CP-violating SIGNAL amplitudes,
    CPT violation, new production, and uncertain form factors are not covered.
    """
    for value in (width_short, width_long):
        _real(value, 'physical kaon width', positive=True)
    _real(delta_mass, 'kaon mass difference')
    _real(epsilon_abs_ceiling, 'mixing-epsilon magnitude ceiling')
    if not 0 <= epsilon_abs_ceiling < 1:
        raise ValueError('Mixing-epsilon magnitude ceiling must lie in [0,1)')
    r = epsilon_abs_ceiling
    delta_eigenvalue = math.hypot(delta_mass, .5*(width_short-width_long))
    diagonal = 2*r*r*delta_eigenvalue/(1-r*r)
    off_diagonal = 2*r*delta_eigenvalue/(1-r*r)
    change = diagonal/min(width_short,width_long)+off_diagonal/math.sqrt(width_short*width_long)
    if change >= 1:
        raise ValueError('Supplied mixing domain gives no strictly positive lower decay operator')
    return 1-change


def conditional_event_ceiling(source_cross_section_pb, parent_mean_ceiling,
                              signal_probability_ceiling, luminosity_fb_inverse):
    """Primary-source count ceiling; no secondary kaons or detector claim.

    A source confidence interval and the rate authority remain separate
    conditions. BR(A'->mumu)<=1 and acceptance<=1 require no favorable decay
    or detector assumption. fb^-1 is converted to pb^-1 explicitly.
    """
    for value, name in ((source_cross_section_pb, 'source cross section'),
                        (parent_mean_ceiling, 'parent yield ceiling'),
                        (signal_probability_ceiling, 'signal probability ceiling'),
                        (luminosity_fb_inverse, 'effective luminosity')):
        _real(value, name)
        if value < 0:
            raise ValueError('Nonnegative '+name+' is required')
    if signal_probability_ceiling > 1:
        raise ValueError('A signal probability ceiling cannot exceed one')
    return source_cross_section_pb*parent_mean_ceiling*signal_probability_ceiling*1000*luminosity_fb_inverse


def conditional_grid_rate_ceiling(inventory, points, ks_reference, rate_inputs, *,
                                  source_authority, confidence=.95,
                                  luminosities=(.03, .3, 3.)):
    """Regenerate a conditional leading-EM kaon worksheet from a new census.

    The inventory may be a single or strictly pooled independent-collision
    census. Its source verification, physical-parent moments and native table
    must already be established. A preselected empirical Bernstein interval
    gets half the total failure budget for each of KL and KS. These same two
    intervals cover all deterministic grid/luminosity scalings simultaneously.

    Rate inputs supply the fixed KL photon normalization and all qualified
    shape-envelope candidates; KS supplies its separately hash-pinned complex
    amplitude and published band. The result's confidence describes SOURCE
    COUNTS only, conditional on those source and rate authorities. It does not
    convert model bands into a confidence interval or include secondary kaons.
    Physical epsilon/proper lifetime are copied unchanged from each point.
    """
    from audit_dark_photon_meson_coverage import final_parent_yield_bound
    from dark_photon_model import fermion_couplings, mixing_parameters, HBAR_C_GEV_MM

    if not isinstance(source_authority, str) or not source_authority.strip():
        raise ValueError('A persistent census authority and exact digest are required')
    if (inventory.get('source_class') != 'SoftQCD:nonDiffractive'
            or inventory.get('source_sampling_verified') is not True
            or inventory.get('resolved_source_configuration') is not True):
        raise ValueError('Require a verified fixed-exposure non-diffractive source census')
    if not 0 < confidence < 1 or not luminosities or len(set(luminosities)) != len(luminosities):
        raise ValueError('A total count confidence and distinct luminosities are required')
    for lumi in luminosities:
        _real(lumi, 'effective luminosity', positive=True)
    br_gamma_gamma = _real(rate_inputs.get('kl_br_gamma_gamma'), 'KL photon branching fraction')
    if not 0 <= br_gamma_gamma <= 1 or not rate_inputs.get('kl_shape_candidates') or not rate_inputs.get('authority'):
        raise ValueError('Explicit KL shape candidates and rate-envelope authority are required')
    selected = [row for row in points if not row.get('operational_canary')]
    if not selected or len({row['point'] for row in selected}) != len(selected):
        raise ValueError('A nonempty unique physical point grid is required')
    particles = {row['pdg_id']: row for row in inventory['particle_data']}
    species = {row['pdg_id']: row for row in inventory['species']}
    sigma = _real(inventory['native']['sigma_source_mb'], 'source cross section', positive=True)*1.e9
    bounds = {}
    widths = {}
    for pdg in (130, 310):
        record = species[pdg]
        bounds[str(pdg)] = final_parent_yield_bound(particles[pdg],
            inventory['resolved_lifetime_settings'], inventory['source_configuration'],
            inventory['resolved_beams'], inventory['collision_exposure'],
            record['mean_parents_per_collision'], sigma, confidence=1-(1-confidence)/2,
            method='empirical_bernstein', sum_count_squared=record['sum_collision_count_squared'])
        if bounds[str(pdg)] is None:
            raise ValueError('Native final-parent source does not support a finite count ceiling')
        widths[pdg] = HBAR_C_GEV_MM/_real(particles[pdg]['mean_proper_lifetime_mm'],
                                        'native proper mean flight', positive=True)
    parent_mean_upper = sum(row['mean_parents_per_collision_upper'] for row in bounds.values())
    total_decay = ((widths[310], 0.), (0., widths[130]))
    rows = []
    for point in selected:
        mass = _real(point['mass_gev'], 'dark-photon mass', positive=True)
        epsilon = _real(point['epsilon'], 'physical epsilon', positive=True)
        _real(point['proper_ctau_mm'], 'physical proper lifetime', positive=True)
        mix = mixing_parameters(mass, epsilon)
        br_long = br_short = 0.
        form_factor_max = None
        if mass < particles[130]['mass_gev']:
            form_factor_max = max(abs(transition_form_factor(mass, candidate))**2
                                  for candidate in rate_inputs['kl_shape_candidates'])
            br_long = radiative_branching(130, particles[130]['mass_gev'], mass,
                mix.em_coefficient, br_gamma_gamma, math.sqrt(form_factor_max))
        if mass < particles[310]['mass_gev']:
            br_short = ks_reference.radiative_width(particles[310]['mass_gev'], mass,
                mix.em_coefficient, band=True)[1]/widths[310]
        ceiling = positive_operator_rate_ceiling(
            ((br_short*widths[310], 0.), (0., br_long*widths[130])), total_decay,
            authority=rate_inputs['authority'])
        muon = fermion_couplings(mass, epsilon, -1., -.5)
        ratio = abs(muon.axial/muon.vector)
        r_muon = (.1056583755/mass)**2
        beta = math.sqrt(max(0., 1-4*r_muon))
        rows.append(dict(point=point['point'], mass_gev=mass, epsilon=epsilon,
            proper_ctau_mm=point['proper_ctau_mm'],
            reference_mean_lab_m=point['target_mean_lab_flight_m'], prompt=point['prompt'],
            kl_form_factor_abs2_envelope=form_factor_max,
            kl_branching_envelope=br_long, ks_branching_envelope=br_short,
            arbitrary_mixing_probability_ceiling=ceiling,
            source_count_conditional_event_ceiling={str(lumi):conditional_event_ceiling(
                sigma, parent_mean_upper, min(1., ceiling), lumi) for lumi in luminosities},
            exact_hypercharge_muon_budget=dict(abs_axial_over_vector=ratio,
                axial_over_vector_partial_width=ratio**2*beta**2/(1+2*r_muon),
                maximum_transverse_parity_odd_relative_term=2*ratio*beta,
                em_coefficient_over_epsilon=mix.em_coefficient/epsilon)))
    return dict(schema='conditional-neutral-kaon-grid-calculation-v1',
        source_authority=source_authority, source_count_confidence=confidence,
        source_count_bounds=bounds, source_cross_section_pb=sigma,
        total_primary_kaon_mean_ceiling=parent_mean_upper,
        total_decay_widths_gev={str(key):value for key,value in widths.items()},
        rate_authority=rate_inputs['authority'], ks_reference_sha256=ks_reference.sha256,
        scope='Leading electromagnetic CP tensors; fixed source/model/rate envelopes. Primary-parent regeneration and absorption covered; new secondary kaons excluded. BRmumu<=1, acceptance<=1.',
        confidence_scope='Two preselected whole-collision Bernstein bounds, Bonferroni across physical KL/KS. All point and luminosity scalings share those same two bounds. No statistical CL assigned to rate/model envelopes.',
        rows=rows)


def amplitude(parent_pdg_id, photon, dark_photon, photon_polarization,
              dark_polarization, form_factor=1.):
    """Dominant CP tensor contracted with the supplied polarization vectors.

    The form factor is the full coefficient in GeV^-1 here. Polarization
    conjugation belongs to the caller. Replacing either polarization by its
    momentum tests the corresponding electromagnetic Ward identity.
    """
    _kaon(parent_pdg_id)
    vectors = (photon_polarization, dark_polarization, photon, dark_photon)
    for vector in vectors:
        _four_vector(vector)
    if not cmath.isfinite(form_factor):
        raise ValueError('Finite complex amplitude coefficient is required')
    if parent_pdg_id == 310:
        return form_factor*(
            _minkowski_dot(photon, dark_photon)*_minkowski_dot(photon_polarization, dark_polarization)
            - _minkowski_dot(dark_photon, photon_polarization)*_minkowski_dot(photon, dark_polarization))
    # The determinant is epsilon_mu_nu_rho_sigma e1^mu e2^nu k^rho q^sigma.
    result = 0.
    for order in permutations(range(4)):
        inversions = sum(order[i] > order[j] for i in range(4) for j in range(i+1, 4))
        result += (-1)**inversions*math.prod(vector[index] for vector, index in zip(vectors, order))
    return 1j*form_factor*result


def transition_form_factor(mass, model):
    """K_L f(0,mass^2), normalized to f(0,0)=1, with a declared domain.

    DIP needs alpha_DIP. BMS needs the fitted product C*alpha_Kstar rather
    than alpha_Kstar alone. The second DIP parameter multiplies both photon
    virtualities and vanishes for this singly-virtual amplitude.
    """
    _real(mass, 'dark-photon mass')
    if mass < 0:
        raise ValueError('Nonnegative dark-photon mass is required')
    if model.get('parent_pdg_id') == 310:
        raise ValueError('K_S requires a separately qualified dispersive timelike form factor')
    if model.get('parent_pdg_id') != 130 or not model.get('reference'):
        raise ValueError('An explicit K_L form-factor model and reference are required')
    domain = model.get('valid_mass_range_gev')
    if (not isinstance(domain, (list, tuple)) or len(domain) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in domain)
            or not 0 <= domain[0] < domain[1]
            or mass != 0 and not domain[0] <= mass <= domain[1]):
        raise ValueError('Requested mass lies outside an explicit finite form-factor domain')
    rho = _real(model.get('rho_mass_gev'), 'rho pole mass', positive=True)
    if domain[1] >= rho:
        raise ValueError('A singly-virtual kaon fit cannot be extended through a vector pole')
    q2 = mass**2
    kind = model.get('kind')
    if kind == 'kl-dip-single-virtual':
        alpha = _real(model.get('alpha_dip'), 'DIP alpha')
        return 1+alpha*q2/(q2-rho**2)
    if kind == 'kl-bms-single-virtual':
        coefficient = _real(model.get('c_alpha_kstar'), 'fitted BMS C*alpha_Kstar')
        omega = _real(model.get('omega_mass_gev'), 'omega pole mass', positive=True)
        phi = _real(model.get('phi_mass_gev'), 'phi pole mass', positive=True)
        kstar = _real(model.get('kstar_mass_gev'), 'Kstar pole mass', positive=True)
        if domain[1] >= min(omega, phi, kstar):
            raise ValueError('BMS validity cannot cross a fitted vector pole')
        if mass == 0:
            return 1.
        rho_term = 1/(1-q2/rho**2)
        return rho_term+coefficient/(1-q2/kstar**2)*(
            4/3-rho_term-1/(9*(1-q2/omega**2))-2/(9*(1-q2/phi**2)))
    raise ValueError('Unsupported explicit neutral-kaon form-factor model')


def radiative_branching(parent_pdg_id, parent_mass, mass, epsilon,
                        br_gamma_gamma, form_factor_ratio):
    """Leading electromagnetic-mixing branching fraction for either CP tensor.

    The factor two is the identical-photon normalization of the measured
    gamma-gamma width. The common spin-zero kernel supplies phase space,
    not the kaon's weak amplitude, form factor, lifetime or material model.
    A supplied K_S coefficient must have its own validated authority.
    """
    _kaon(parent_pdg_id)
    return spin_zero_radiative_branching(parent_mass, mass, epsilon,
                                         br_gamma_gamma, form_factor_ratio)


def sample_radiative_decay(parent_pdg_id, parent, mass, proper_ctau_mm, rng):
    """Unconditional KL/KS -> gamma A' -> gamma mu+mu- with pure-vector A'.

    Both CP tensors produce the same transverse density after the real-photon
    polarization is unobserved. Their rates/form factors remain separate.
    The common sampler supplies ONLY the relative A' flight. The caller must
    preserve the parent's previously sampled native lifetime and decay point.
    This neither implements matter evolution nor qualifies its source rate.
    """
    _kaon(parent_pdg_id)
    result = spin_zero_radiative_sample(parent,mass,proper_ctau_mm,rng)
    result.update(parent_pdg_id=parent_pdg_id,recoil_pdg_id=22,
        parent_polarization='spin-zero',dark_photon_polarization='transverse',
        kaon_cp_tensor='odd' if parent_pdg_id == 130 else 'even',
        parent_flight_included=False)
    return result


def sample_weak_decay(parent_pdg_id, parent, recoil_mass, mass, proper_ctau_mm, rng):
    """Unconditional K+/-/KS/KL -> pion A' using the longitudinal sampler.

    Rates and their separately qualified weak amplitudes are caller inputs.
    The source must supply the explicit KL CPV current for that parent. The
    pion recoil's ordinary decay is the native bridge's responsibility.
    """
    if type(parent_pdg_id) is not int or parent_pdg_id not in (321,-321,310,130):
        raise ValueError('Explicit charged-kaon, KS or KL weak-current parent required')
    # Sampler-only dependency: rate-only neutral-kaon deployments do not use
    # this API. Any new GEN freeze must include the weak helper coherently.
    from dark_photon_weak_meson import sample_decay
    result = sample_decay(parent,recoil_mass,mass,proper_ctau_mm,rng)
    result.update(parent_pdg_id=parent_pdg_id,
        recoil_pdg_id={321:211,-321:-211,310:111,130:111}[parent_pdg_id],
        parent_flight_included=False)
    return result


def normalized_dalitz_density(parent_mass, mass, lepton_mass, alpha_em,
                              form_factor_ratio):
    """(1/Gamma_gg) dGamma(K -> gamma ll)/d(q2), in GeV^-2.

    hep-ph/9708326 Eq.(18), with the spin-zero electromagnetic conversion
    factor. It also applies to the dominant CP-even tensor with its own form
    factor. This is the virtual-photon continuum, not an added A' population.
    """
    for value, name in ((parent_mass, 'parent mass'), (mass, 'dilepton mass'),
                        (lepton_mass, 'lepton mass'), (alpha_em, 'alpha_em')):
        _real(value, name, positive=True)
    if not cmath.isfinite(form_factor_ratio):
        raise ValueError('Finite complex form-factor ratio is required')
    if mass >= parent_mass or mass <= 2*lepton_mass:
        return 0.
    q2 = mass**2
    lepton_factor = math.sqrt(1-4*lepton_mass**2/q2)*(1+2*lepton_mass**2/q2)
    return (2*alpha_em/(3*math.pi*q2)*(1-q2/parent_mass**2)**3
            *abs(form_factor_ratio)**2*lepton_factor)
