# Code style: AI-code. AI-generated neutral pseudoscalar radiative-current model.
"""Rate and correlated decay kernel for the specified local P -> P' gamma A' current.

The real-photon authority is Escribano et al., PhysRevD102,034026 (2020),
doi:10.1103/PhysRevD.102.034026, Eqs8-11,23 and TablesI-II. We retain both
crossed diagrams built from two anomalous VP-current epsilon tensors. This
avoids using their real-photon simplified numerator at nonzero virtuality.
The complete massive-vector propagator's longitudinal term vanishes because
each vertex is transverse to the internal momentum. All three physical A'
polarizations are included; a three-body decay need not be transverse-only.

The CONSTANT local VP-current couplings at timelike q2 are an explicit model
prescription, not a measurement or a general bound on an unknown form factor.
The rho running width is exactly their Eq10, not another model's convention.
An optional charged-scalar loop includes either the paper's leading ChPT
strong amplitude or its complete threshold-aware linear-sigma prescription.
It replaces the scalar-free control; ChPT and the linear-sigma amplitude are
never added together. The unequal-virtuality scalar-QED loop is retained,
including its pion cut, and interferes coherently with the vector diagrams.
Additional timelike operators remain unmeasured. Even the paper's combined
model underpredicts the eta photon rate by about a factor two.

An optional absolute-amplitude envelope encloses the supplied coupling
magnitudes and all interference phases within this fixed local VMD model.
It may conservatively overcount already represented eta'->gamma V feed-down.
Do not add the inclusive eta' mode to existing V->P'A' production: coherent
replacement and an ancestry overlap audit are required first. BR(A'->mumu),
source yields, parent flight and detector acceptance are excluded here.
Four-vectors are (E,px,py,pz), masses/widths GeV, VP couplings GeV^-1.
"""
import cmath
import bisect
import functools
import json
import math

from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_meson import MUON_MASS_GEV, boost
from dark_photon_vector_meson import two_body_momentum


REFERENCE = 'https://doi.org/10.1103/PhysRevD.102.034026'


def _dot(a, b):
    return a[0]*b[0]-sum(x*y for x, y in zip(a[1:], b[1:]))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _vertex(polarization, photon, internal):
    """epsilon^{alpha mu rho sigma} e_mu q_rho R_sigma, covariant inputs."""
    a, b, c = polarization, photon, internal
    ab, ac, bc = _cross(a[1:], b[1:]), _cross(a[1:], c[1:]), _cross(b[1:], c[1:])
    return (-sum(x*y for x, y in zip(ab, c[1:])),)+tuple(
        -a[0]*x+b[0]*y-c[0]*z for x, y, z in zip(bc, ac, ab))


def polarizations(momentum, mass):
    """Two massless or three massive physical linear polarizations."""
    length = math.sqrt(sum(x*x for x in momentum[1:]))
    if length == 0:
        if mass > 0:
            return [(0., 1., 0., 0.), (0., 0., 1., 0.), (0., 0., 0., 1.)]
        raise ValueError('A real photon needs nonzero momentum')
    direction = tuple(x/length for x in momentum[1:])
    axis = (1., 0., 0.) if abs(direction[2]) > .8 else (0., 0., 1.)
    first = _cross(direction, axis)
    norm = math.sqrt(sum(x*x for x in first))
    first = tuple(x/norm for x in first)
    second = _cross(direction, first)
    result = [(0.,)+first, (0.,)+second]
    if mass > 0:
        result.append((length/mass,)+tuple(momentum[0]*x/mass for x in direction))
    return result


def quark_model_couplings(g, z_non_strange, z_strange_mass_ratio, phi_p, phi_v):
    """Primary Eq11; angles in radians, all fit inputs explicit."""
    if not all(math.isfinite(x) for x in (g,z_non_strange,z_strange_mass_ratio,phi_p,phi_v)):
        raise ValueError('Finite quark-model parameters are required')
    c, s, cv, sv = math.cos(phi_p), math.sin(phi_p), math.cos(phi_v), math.sin(phi_v)
    n, strange = z_non_strange, z_strange_mass_ratio
    return {
        113:{111:g/3,221:g*n*c,331:g*n*s},
        223:{111:g*cv,221:g/3*(n*c*cv-2*strange*s*sv),331:g/3*(n*s*cv+2*strange*c*sv)},
        333:{111:g*sv,221:g/3*(n*c*sv+2*strange*s*cv),331:g/3*(n*s*sv-2*strange*c*cv)}}


def empirical_reference_model(inputs, parent_pdg_id, recoil_pdg_id, *, coupling_errors,scalar_current=None):
    """Build an explicit channel card from attributed TableI reference inputs.

    coupling_errors is the caller's rectangular error multiplier, not a CL.
    The primary central signs are distinct from its measured magnitudes; the
    absolute envelope subsequently covers arbitrary interference phases.
    """
    if inputs.get('schema') != 'shift-neutral-pseudoscalar-local-vmd-reference-v1':
        raise ValueError('An explicit maintained local-VMD reference is required')
    if (type(coupling_errors) not in (int,float) or not math.isfinite(coupling_errors)
            or coupling_errors < 0 or (parent_pdg_id,recoil_pdg_id) not in
            ((221,111),(331,111),(331,221))):
        raise ValueError('Supported pseudoscalar transition and nonnegative error multiplier required')
    parent=inputs['pseudoscalars'][str(parent_pdg_id)]
    recoil=inputs['pseudoscalars'][str(recoil_pdg_id)]
    vectors=[]
    for row in inputs['vectors']:
        first,error_first=row['couplings_gev_inverse'][str(parent_pdg_id)]
        second,error_second=row['couplings_gev_inverse'][str(recoil_pdg_id)]
        if not all(math.isfinite(x) for x in (first,second,error_first,error_second)) or min(error_first,error_second)<0:
            raise ValueError('Finite coupling magnitudes and nonnegative quoted errors required')
        vectors.append(dict(mass_gev=row['mass_gev'],width_gev=row['width_gev'],
            coupling_product_gev_inverse2=first*second,
            absolute_product_ceiling_gev_inverse2=(abs(first)+coupling_errors*error_first)*
                (abs(second)+coupling_errors*error_second),
            width_prescription=row['width_prescription'],pion_mass_gev=inputs['pion_mass_gev']))
    result=dict(kind='local-vp-field-strength-vmd',reference=inputs['publication']['url'],
        virtual_vertex_prescription=inputs['virtual_vertex_prescription'],
        omitted_scalar_and_chiral_terms=inputs['omitted_scalar_and_chiral_terms'],
        valid_mass_range_gev=[0.,parent['mass_gev']-recoil['mass_gev']],vectors=vectors)
    if scalar_current is not None:
        result.update(kind='local-vp-field-strength-vmd-plus-scalar-loop',
            omitted_scalar_and_chiral_terms=False,scalar_current=scalar_current)
    return result


class LocalVMDCurrent:
    def __init__(self, model):
        self.kind=model.get('kind')
        scalar=model.get('scalar_current')
        if (self.kind not in ('local-vp-field-strength-vmd','local-vp-field-strength-vmd-plus-scalar-loop')
                or not model.get('reference')
                or model.get('virtual_vertex_prescription') != 'constant-local-couplings'
                or (scalar is None and (self.kind!='local-vp-field-strength-vmd'
                    or model.get('omitted_scalar_and_chiral_terms') is not True))
                or (scalar is not None and (self.kind!='local-vp-field-strength-vmd-plus-scalar-loop'
                    or model.get('omitted_scalar_and_chiral_terms') is not False))
                or not model.get('vectors')):
            raise ValueError('Require an explicit local-VMD model, virtual prescription and omissions')
        self.scalar=None
        if scalar is not None:
            from dark_photon_scalar_meson import validate_scalar_model
            validate_scalar_model(scalar)
            self.scalar=json.loads(json.dumps(scalar,allow_nan=False))
        domain = model.get('valid_mass_range_gev')
        if (not isinstance(domain,(tuple,list)) or len(domain) != 2
                or any(type(x) not in (int,float) or not math.isfinite(x) for x in domain)
                or not 0 <= domain[0] < domain[1]):
            raise ValueError('An explicit finite virtual-current mass domain is required')
        self.domain = tuple(domain)
        self.vectors = []
        for row in model['vectors']:
            for name in ('mass_gev','width_gev','absolute_product_ceiling_gev_inverse2'):
                value = row.get(name)
                if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
                    raise ValueError('Explicit positive finite vector '+name+' required')
            product = row.get('coupling_product_gev_inverse2')
            if not cmath.isfinite(product) or abs(product) > row['absolute_product_ceiling_gev_inverse2']:
                raise ValueError('The declared coupling envelope must contain its complex central product')
            if row.get('width_prescription') == 'rho-pipi-primary-eq10':
                pion = row.get('pion_mass_gev')
                if type(pion) not in (int,float) or not 0 < 2*pion < row['mass_gev']:
                    raise ValueError('A physical pion threshold is required')
            elif row.get('width_prescription') != 'constant':
                raise ValueError('Only explicit primary Eq10 rho or constant widths are supported')
            self.vectors.append(dict(row))
        self.reference = model['reference']

    def vector_only(self):
        return LocalVMDCurrent(dict(kind='local-vp-field-strength-vmd',reference=self.reference,
            virtual_vertex_prescription='constant-local-couplings',omitted_scalar_and_chiral_terms=True,
            valid_mass_range_gev=self.domain,vectors=self.vectors))

    def scalar_reduced(self, invariant, virtuality):
        """Sum A_strong * S(s,q²), before -alpha/(2pi) and unit T1.

        At q²=0, S=-2(s/m_loop²)L(s/m_loop²). Consequently this exactly
        recovers the primary 2alpha/(pi*m_loop²)*L*A_strong*T1, including
        its relative sign to Eq8 VMD. No weak-current overall phase enters.
        """
        if self.scalar is None:
            return 0j
        from dark_photon_scalar_meson import scalar_strong_amplitudes
        from dark_photon_weak_radiative import scalar_loop
        return sum(amplitude*scalar_loop(invariant,virtuality,loop_mass)
            for loop_mass,amplitude in scalar_strong_amplitudes(invariant,self.scalar))

    def scalar_amplitude(self, photon, dark, first, second):
        if self.scalar is None:
            return 0j
        product=_dot(photon,dark)
        if product<=0:
            raise ValueError('An open future-directed real-photon current is required')
        virtuality=max(0.,_dot(dark,dark))
        reduced=self.scalar_reduced(virtuality+2*product,virtuality)
        tensor=_dot(first,second)-_dot(first,dark)*_dot(second,photon)/product
        return -self.scalar['alpha_em']/(2*math.pi)*reduced*tensor

    def scalar_spin_sum(self, invariant, virtuality):
        if self.scalar is None:
            return 0.
        return self.scalar['alpha_em']**2/(2*math.pi**2)*abs(self.scalar_reduced(invariant,virtuality))**2

    def _coefficients(self, invariant):
        central, envelope = 0j, 0.
        for row in self.vectors:
            pole, width = row['mass_gev'], row['width_gev']
            if row['width_prescription'] == 'rho-pipi-primary-eq10':
                threshold = 4*row['pion_mass_gev']**2
                width *= ((invariant-threshold)/(pole*pole-threshold))**1.5 if invariant > threshold else 0.
            denominator = complex(pole*pole-invariant,-pole*width)
            central += row['coupling_product_gev_inverse2']/denominator
            envelope += row['absolute_product_ceiling_gev_inverse2']/abs(denominator)
        return central, envelope

    def _kinematic_terms(self, parent, photon, dark, first, second):
        r = tuple(x-y for x,y in zip(parent,photon))
        s = tuple(x-y for x,y in zip(parent,dark))
        t_term = _dot(_vertex(first,photon,r),_vertex(second,dark,r))
        u_term = _dot(_vertex(first,photon,s),_vertex(second,dark,s))
        return t_term, u_term

    def amplitude(self, parent, photon, dark, first, second):
        """Gauge-complete amplitude before an overall EM mixing factor."""
        vectors = (parent,photon,dark,first,second)
        if any(len(v) != 4 or any(not cmath.isfinite(x) for x in v) for v in vectors):
            raise ValueError('Finite four-vectors and polarizations are required')
        r = tuple(x-y for x,y in zip(parent,photon))
        s = tuple(x-y for x,y in zip(parent,dark))
        a,b = self._kinematic_terms(parent,photon,dark,first,second)
        return (self._coefficients(_dot(r,r))[0]*a+self._coefficients(_dot(s,s))[0]*b
                +self.scalar_amplitude(photon,dark,first,second))

    def spin_sum(self, parent, photon, dark, mass, *, envelope=False):
        r = tuple(x-y for x,y in zip(parent,photon))
        s = tuple(x-y for x,y in zip(parent,dark))
        ct,et = self._coefficients(_dot(r,r))
        cu,eu = self._coefficients(_dot(s,s))
        result = 0.
        for first in polarizations(photon,0.):
            for second in polarizations(dark,mass):
                a,b = self._kinematic_terms(parent,photon,dark,first,second)
                scalar=self.scalar_amplitude(photon,dark,first,second)
                result += (et*abs(a)+eu*abs(b)+abs(scalar))**2 if envelope else abs(ct*a+cu*b+scalar)**2
        return result

    def _panels(self, low, high, transform=lambda x:x, *, additional=()):
        boundaries = {low,high,*[point for point in additional if low<point<high]}
        for row in self.vectors:
            pole, scale = row['mass_gev']**2, row['mass_gev']*row['width_gev']
            for offset in (0.,-1.,1.,-4.,4.,-16.,16.):
                point = transform(pole+offset*scale)
                if low < point < high:
                    boundaries.add(point)
        values = sorted(boundaries)
        return list(zip(values,values[1:]))

    def integrated_width(self, parent_mass, recoil_mass, mass, epsilon_em, *,
                         photon_reference=False, envelope=False, order=12, moment=None):
        """Deterministic ds dcos integration; output GeV.

        photon_reference=True computes P->P'gamma gamma, including 1/2!.
        Otherwise the distinct gamma/A' final state has no symmetry factor.
        Pole-aware panels resolve t/u resonances; compare successive orders
        before relying on a new mass point. The envelope is pointwise within
        the supplied fixed-pole local-coupling model, not a universal bound.
        """
        if (not all(math.isfinite(x) for x in (parent_mass,recoil_mass,mass,epsilon_em))
                or parent_mass <= 0 or min(recoil_mass,mass,epsilon_em) < 0
                or type(order) is not int or not 4 <= order <= 80
                or photon_reference and mass != 0):
            raise ValueError('Explicit physical masses, mixing and bounded quadrature order required')
        if mass >= parent_mass-recoil_mass:
            return 0.
        if not self.domain[0] <= mass <= self.domain[1]:
            raise ValueError('Virtual-current mass lies outside the explicit local-VMD domain')
        parent = (parent_mass,0.,0.,0.)
        result = 0.
        for low,high in self._panels((recoil_mass+mass)**2,parent_mass**2):
            for node,weight in _gauss_legendre(order):
                invariant = (low+high)/2+(high-low)/2*node
                pair_mass = math.sqrt(invariant)
                outer = (parent_mass**2-invariant)/(2*parent_mass)
                inner = two_body_momentum(pair_mass,recoil_mass,mass)
                pair_energy = (parent_mass**2+invariant)/(2*parent_mass)
                dark_energy = (invariant+mass**2-recoil_mass**2)/(2*pair_mass)
                gamma = pair_energy/pair_mass
                a = parent_mass**2+mass**2-2*parent_mass*gamma*dark_energy
                b = 2*parent_mass*outer/pair_mass*inner
                photon = (outer,0.,0.,outer)
                angular = 0.
                scalar_cuts=[]
                if self.scalar is not None:
                    from dark_photon_scalar_meson import scalar_thresholds
                    scalar_cuts=[(parent_mass**2+recoil_mass**2+mass**2-invariant-cut-a)/b
                                 for cut in scalar_thresholds(self.scalar)]
                for first,last in self._panels(-1.,1.,lambda value:(value-a)/b,additional=scalar_cuts):
                    for cosine_node,cosine_weight in _gauss_legendre(order):
                        cosine = (first+last)/2+(last-first)/2*cosine_node
                        dark = boost((dark_energy,inner*math.sqrt(1-cosine*cosine),0.,inner*cosine),
                                     (0.,0.,-outer/pair_energy))
                        value = self.spin_sum(parent,photon,dark,mass,envelope=envelope)
                        if moment is not None:
                            factor = moment(dark)
                            if not math.isfinite(factor) or factor < 0:
                                raise ValueError('A rate moment must be finite and nonnegative')
                            value *= factor
                        angular += (last-first)/2*cosine_weight*value
                density = outer*inner/(128*math.pi**3*parent_mass**2*pair_mass)
                result += (high-low)/2*weight*density*angular
        return result*epsilon_em**2/(2 if photon_reference else 1)


def _isotropic(rng):
    cosine = 2*rng.random()-1
    azimuth = 2*math.pi*rng.random()
    sine = math.sqrt(max(0., 1-cosine*cosine))
    return sine*math.cos(azimuth), sine*math.sin(azimuth), cosine


def _rotation(rng):
    axis = _isotropic(rng)
    reference = (0., 0., 1.) if abs(axis[2]) < .9 else (1., 0., 0.)
    first = _cross(axis, reference)
    norm = math.sqrt(sum(x*x for x in first))
    first = tuple(x/norm for x in first)
    second = _cross(axis, first)
    angle = 2*math.pi*rng.random()
    rotated = tuple(math.cos(angle)*x+math.sin(angle)*y for x, y in zip(first, second))
    return rotated, _cross(axis, rotated), axis


def _rotate(vector, basis):
    return (vector[0],)+tuple(sum(vector[j+1]*basis[j][i] for j in range(3)) for i in range(3))


class LocalVMDSampler:
    """Unweighted central-current Dalitz sampler with a proven rejection bound.

    Let t=(p-k)^2 and u=(p-q)^2, where k is the real photon. The density is
    epsilon_em^2 * spin_sum/(256*pi^3*M^3) on the physical Dalitz domain.
    A mixture of truncated Lorentzians resolves both crossed vector poles.
    Cauchy bounds the sum of N diagram/vector amplitudes by N times the sum
    of their norms. Each norm has the following physical-domain bounds:
      S_t <= (M^2-t)^2 * lambda(t,m^2,r^2)/8,
      S_u <= lambda(M^2,m^2,u) * (u-r^2)^2/8.
    These follow by evaluating the two transverse epsilon vertices in the
    internal-vector rest frame: S=|a|^2|b|^2+(a.b)^2 times its invariant^2.
    No pilot maximum, phase-space clipping or favourable interference enters
    the envelope. Every event is accepted with the full coherent spin sum.
    """
    def __init__(self, current, parent_mass, recoil_mass, mass):
        if (not isinstance(current, LocalVMDCurrent)
                or not all(math.isfinite(x) for x in (parent_mass,recoil_mass,mass))
                or parent_mass <= 0 or min(recoil_mass,mass) < 0
                or mass >= parent_mass-recoil_mass
                or not current.domain[0] <= mass <= current.domain[1]):
            raise ValueError('An explicit current and open physical three-body masses are required')
        self.current, self.parent_mass, self.recoil_mass, self.mass = current, parent_mass, recoil_mass, mass
        self.t_range = ((recoil_mass+mass)**2, parent_mass**2)
        self.u_range = (recoil_mass**2, (parent_mass-mass)**2)
        active = [row for row in current.vectors if abs(row['coupling_product_gev_inverse2']) > 0]
        if not active:
            raise ValueError('A zero coherent current cannot generate decays')
        self.pieces, self.cumulative = [], [0.]
        count = 2*len(active)
        for coordinate, limits in enumerate((self.t_range, self.u_range)):
            other = self.u_range if coordinate == 0 else self.t_range
            for low, high in current._panels(*limits):
                if coordinate == 0:
                    lam = (high-(recoil_mass+mass)**2)*(high-(recoil_mass-mass)**2)
                    numerator = (parent_mass**2-low)**2*lam/8
                else:
                    lam = ((parent_mass-mass)**2-low)*((parent_mass+mass)**2-low)
                    numerator = lam*(high-recoil_mass**2)**2/8
                for vector in active:
                    pole = vector['mass_gev']**2
                    width = vector['width_gev']
                    if vector['width_prescription'] == 'rho-pipi-primary-eq10':
                        threshold = 4*vector['pion_mass_gev']**2
                        width *= ((low-threshold)/(pole-threshold))**1.5 if low > threshold else 0.
                    scale = vector['mass_gev']*width
                    if scale == 0 and low <= pole <= high:
                        raise ValueError('Pole panels must resolve the positive-width neighbourhood')
                    # Directed analytic inequalities need only this rounding margin.
                    strength = count*abs(vector['coupling_product_gev_inverse2'])**2*numerator*(1+1.e-12)
                    if strength == 0:
                        continue
                    if scale:
                        first, last = math.atan((low-pole)/scale), math.atan((high-pole)/scale)
                        integral = (last-first)/scale
                    else:
                        first, last = 1/(pole-low), 1/(pole-high)
                        integral = last-first
                    if not math.isfinite(integral) or integral <= 0:
                        raise ValueError('Invalid analytic Lorentzian proposal integral')
                    self.pieces.append(dict(coordinate=coordinate,low=low,high=high,pole=pole,
                        scale=scale,first=first,last=last,strength=strength))
                    self.cumulative.append(self.cumulative[-1]+strength*integral*(other[1]-other[0]))
        self.envelope_integral = self.cumulative[-1]
        if not math.isfinite(self.envelope_integral) or self.envelope_integral <= 0:
            raise ValueError('The current has no finite rejection envelope')

    def envelope(self, t, u):
        return math.fsum(piece['strength']/((piece['pole']-(t,u)[piece['coordinate']])**2+piece['scale']**2)
            for piece in self.pieces if piece['low'] <= (t,u)[piece['coordinate']] <= piece['high'])

    def rest_event(self, t, u):
        """Construct the canonical event; return None outside its physical domain."""
        if not self.t_range[0] < t < self.t_range[1] or not self.u_range[0] < u < self.u_range[1]:
            return None
        parent, recoil, mass = self.parent_mass, self.recoil_mass, self.mass
        pair = math.sqrt(t)
        outer = (parent*parent-t)/(2*parent)
        pair_energy = (parent*parent+t)/(2*parent)
        inner = two_body_momentum(pair,recoil,mass)
        energy = (t+mass*mass-recoil*recoil)/(2*pair)
        a = parent*parent+mass*mass-2*parent*pair_energy/pair*energy
        b = 2*parent*outer/pair*inner
        if b <= 0:
            return None
        cosine = (u-a)/b
        if abs(cosine) > 1:
            return None
        photon = (outer,0.,0.,outer)
        dark = boost((energy,inner*math.sqrt(max(0.,1-cosine*cosine)),0.,inner*cosine),
                     (0.,0.,-outer/pair_energy))
        p = (parent,0.,0.,0.)
        daughter = tuple(x-y-z for x,y,z in zip(p,photon,dark))
        return daughter, photon, dark

    def sample_rest(self, rng, *, maximum_trials=1000000):
        """Generate unit-weight P' gamma A' in a uniformly rotated parent frame."""
        for trial in range(1,maximum_trials+1):
            index = bisect.bisect_right(self.cumulative,rng.random()*self.envelope_integral)-1
            piece = self.pieces[min(index,len(self.pieces)-1)]
            parameter = piece['first']+rng.random()*(piece['last']-piece['first'])
            value = piece['pole']+piece['scale']*math.tan(parameter) if piece['scale'] else piece['pole']-1/parameter
            other = self.u_range if piece['coordinate'] == 0 else self.t_range
            second = other[0]+rng.random()*(other[1]-other[0])
            t,u = (value,second) if piece['coordinate'] == 0 else (second,value)
            event = self.rest_event(t,u)
            if event is None:
                continue
            daughter,photon,dark = event
            density = self.current.spin_sum((self.parent_mass,0.,0.,0.),photon,dark,self.mass)
            upper = self.envelope(t,u)
            if not math.isfinite(density) or density < 0 or density > upper*(1+2.e-12):
                raise RuntimeError('Coherent double-radiative rejection bound violated')
            if rng.random()*upper <= density:
                return dict(recoil_p4=daughter,photon_p4=photon,dark_photon_p4=dark,
                            t_gev2=t,u_gev2=u,proposal_trials=trial)
        raise RuntimeError('Double-radiative sampler exhausted its bounded trial budget')

    def spin_density(self, photon, dark):
        """Hermitian density in the three physical linear A' polarizations."""
        if self.mass <= 0:
            raise ValueError('A massive vector is required for its dimuon spin density')
        matrix = [[0j]*3 for _ in range(3)]
        basis = polarizations(dark,self.mass)
        for first in polarizations(photon,0.):
            amplitudes = [self.current.amplitude((self.parent_mass,0.,0.,0.),photon,dark,first,second)
                          for second in basis]
            for i in range(3):
                for j in range(3):
                    matrix[i][j] += amplitudes[i]*amplitudes[j].conjugate()
        return matrix, basis

    def sample(self, parent, proper_ctau_mm, rng):
        """Full coherent decay and correlated A'->mu-mu+; no acceptance cuts."""
        if (len(parent) != 4 or not all(math.isfinite(x) for x in parent) or parent[0] <= 0
                or not math.isfinite(proper_ctau_mm) or proper_ctau_mm < 0
                or self.mass <= 2*MUON_MASS_GEV):
            raise ValueError('Finite timelike parent, nonnegative lifetime and open dimuons required')
        actual = _dot(parent,parent)
        if not math.isclose(actual,self.parent_mass**2,rel_tol=2.e-10,abs_tol=1.e-14):
            raise ValueError('Parent four-vector differs from the frozen sampler mass')
        event = self.sample_rest(rng)
        photon, dark = event['photon_p4'], event['dark_photon_p4']
        matrix,basis = self.spin_density(photon,dark)
        trace = math.fsum(matrix[i][i].real for i in range(3))
        if not math.isfinite(trace) or trace <= 0:
            raise RuntimeError('Accepted decay has no finite physical spin density')
        inverse = tuple(-x/dark[0] for x in dark[1:])
        rest_basis = [boost(vector,inverse)[1:] for vector in basis]
        beta_mu2 = 1-4*MUON_MASS_GEV**2/self.mass**2
        for _ in range(10000):
            direction = _isotropic(rng)
            components = [sum(x*y for x,y in zip(direction,vector)) for vector in rest_basis]
            projection = math.fsum(matrix[i][j].real*components[i]*components[j] for i in range(3) for j in range(3))
            probability = 1-beta_mu2*projection/trace
            if probability < -2.e-12 or probability > 1+2.e-12:
                raise RuntimeError('Nonpositive or unbounded dimuon spin density')
            if rng.random() <= probability:
                break
        else:
            raise RuntimeError('Correlated dimuon sampler exhausted its bounded trial budget')
        momentum = math.sqrt(self.mass*self.mass/4-MUON_MASS_GEV**2)
        muon = (self.mass/2,)+tuple(momentum*x for x in direction)
        antimuon = (self.mass/2,)+tuple(-momentum*x for x in direction)
        dark_beta = tuple(x/dark[0] for x in dark[1:])
        basis = _rotation(rng)
        parent_beta = tuple(x/parent[0] for x in parent[1:])
        lab = lambda vector: boost(_rotate(vector,basis),parent_beta)
        dark_lab = lab(dark)
        length = -proper_ctau_mm*math.log1p(-rng.random())
        return dict(dark_photon_p4=dark_lab,muon_p4=lab(boost(muon,dark_beta)),
            antimuon_p4=lab(boost(antimuon,dark_beta)),
            recoil_p4s=[lab(event['recoil_p4']),lab(photon)],
            dark_photon_mass_gev=self.mass,muon_mass_gev=MUON_MASS_GEV,
            proper_decay_length_mm=length,decay_displacement_mm=tuple(length*x/self.mass for x in dark_lab[1:]),
            decay_ct_mm=length*dark_lab[0]/self.mass,unconditional_decay=True,
            amplitude_model='coherent-'+self.current.kind,
            polarization_model='full-coherent-massive-vector-density-matrix',
            scalar_chiral_and_additional_timelike_terms_omitted=self.current.scalar is None,
            additional_timelike_operators_omitted=True,
            scalar_current_model=self.current.scalar['kind'] if self.current.scalar else None,
            coherent_parent_replacement_required=True,
            t_gev2=event['t_gev2'],u_gev2=event['u_gev2'],proposal_trials=event['proposal_trials'])


class ScalarPairCDF:
    """Positive scalar-component proposal with controlled numerical inversion.

    Its spin sum depends only on s=(gamma+A')². Threshold-split adaptive
    quadrature establishes each panel's integral at relative 2e-8 (absolute
    2e-11 of the total). CDF inversion recomputes the partial panel integral;
    a tabulated interpolation or a sampled maximum never sets the shape.
    This numerical budget is separate from the exact coherent Cauchy bound.
    """
    def __init__(self,current,parent_mass,recoil_mass,mass):
        from dark_photon_scalar_meson import scalar_thresholds
        self.current,self.parent_mass,self.recoil_mass,self.mass=current,parent_mass,recoil_mass,mass
        low,high=mass**2,(parent_mass-recoil_mass)**2
        edges=sorted({low,high,*[value for value in scalar_thresholds(current.scalar) if low<value<high]})
        initial=[(first,last,self.integral(first,last,24)) for first,last in zip(edges,edges[1:])]
        scale=math.fsum(value for _,_,value in initial)
        if not math.isfinite(scale) or scale<=0:
            raise ValueError('The declared scalar current has zero or nonfinite phase-space measure')
        self.panels=[]
        def refine(first,last,coarse,depth=0):
            center=(first+last)/2
            left,right=self.integral(first,center,24),self.integral(center,last,24)
            fine=left+right
            if abs(fine-coarse)<=max(2.e-8*fine,2.e-11*scale):
                self.panels.extend([(first,center,left),(center,last,right)])
            elif depth<22:
                refine(first,center,left,depth+1);refine(center,last,right,depth+1)
            else:
                raise ValueError('Scalar proposal CDF failed its quadrature accuracy budget')
        for first,last,value in initial:
            refine(first,last,value)
        self.cumulative=[0.]
        for _,_,value in self.panels:
            self.cumulative.append(self.cumulative[-1]+value)
        self.width=self.cumulative[-1]

    def density(self,invariant):
        momentum=two_body_momentum(self.parent_mass,self.recoil_mass,math.sqrt(invariant))
        phase=momentum*(invariant-self.mass**2)/(128*math.pi**3*self.parent_mass**2*invariant)
        return phase*self.current.scalar_spin_sum(invariant,self.mass**2)

    def integral(self,low,high,order):
        return (high-low)/2*math.fsum(weight*self.density((low+high)/2+(high-low)/2*node)
                                     for node,weight in _gauss_legendre(order))

    def sample(self,rng):
        target=rng.random()*self.width
        index=min(bisect.bisect_right(self.cumulative,target)-1,len(self.panels)-1)
        low,high,area=self.panels[index]
        target-=self.cumulative[index]
        first,last=low,high
        point=low+(high-low)*target/area
        for _ in range(64):
            residual=self.integral(low,point,24)-target
            if abs(residual)<=max(2.e-10*area,2.e-14*self.width):
                return point
            if residual>0:last=point
            else:first=point
            derivative=self.density(point)
            proposal=point-residual/derivative if derivative>0 else (first+last)/2
            point=proposal if first<proposal<last else (first+last)/2
        raise RuntimeError('Scalar proposal inverse CDF failed its numerical accuracy budget')


class CoherentScalarSampler(LocalVMDSampler):
    """Positive VMD/scalar mixture followed by exact coherent rejection.

    q=(dGamma_V+dGamma_S)/(Gamma_V+Gamma_S). Cauchy gives
    |A_V+A_S|² <= 2(|A_V|²+|A_S|²) for every Dalitz point and physical
    polarization. Accepting that ratio samples the interfering current.
    Scalar proposal integration/inversion is independently accuracy-gated;
    the positive VMD integral converges to relative 2e-6 before mixing.
    no unknown phase-space maximum is estimated from a pilot point scan.
    """
    def __init__(self,current,parent_mass,recoil_mass,mass):
        self.vector_sampler=LocalVMDSampler(current.vector_only(),parent_mass,recoil_mass,mass)
        self.current,self.parent_mass,self.recoil_mass,self.mass=current,parent_mass,recoil_mass,mass
        self.t_range,self.u_range=self.vector_sampler.t_range,self.vector_sampler.u_range
        self.scalar_cdf=ScalarPairCDF(current,parent_mass,recoil_mass,mass)
        vector=self.vector_sampler.current
        previous=vector.integrated_width(parent_mass,recoil_mass,mass,1.,order=16)
        for order in (24,40,64,80):
            width=vector.integrated_width(parent_mass,recoil_mass,mass,1.,order=order)
            if abs(width-previous)<=2.e-6*max(width,previous):break
            previous=width
        else:
            raise ValueError('VMD mixture normalization failed its numerical convergence budget')
        self.vector_width=width
        self.vector_probability=width/(width+self.scalar_cdf.width)

    def sample_rest(self,rng,*,maximum_trials=1000000):
        for trial in range(1,maximum_trials+1):
            if rng.random()<self.vector_probability:
                event=self.vector_sampler.sample_rest(rng,maximum_trials=maximum_trials)
            else:
                invariant=self.scalar_cdf.sample(rng)
                pair=math.sqrt(invariant)
                momentum=two_body_momentum(self.parent_mass,self.recoil_mass,pair)
                energy=(self.parent_mass**2+invariant-self.recoil_mass**2)/(2*self.parent_mass)
                inner=(invariant-self.mass**2)/(2*pair)
                cosine=2*rng.random()-1
                photon_energy=energy/pair*inner-momentum/pair*inner*cosine
                dark_energy=energy/pair*(invariant+self.mass**2)/(2*pair)+momentum/pair*inner*cosine
                t=self.parent_mass**2-2*self.parent_mass*photon_energy
                u=self.parent_mass**2+self.mass**2-2*self.parent_mass*dark_energy
                rest=self.vector_sampler.rest_event(t,u)
                if rest is None:
                    raise RuntimeError('Scalar CDF generated a nonphysical Dalitz point')
                daughter,photon,dark=rest
                event=dict(recoil_p4=daughter,photon_p4=photon,dark_photon_p4=dark,t_gev2=t,u_gev2=u)
            photon,dark=event['photon_p4'],event['dark_photon_p4']
            parent=(self.parent_mass,0.,0.,0.)
            invariant=self.mass**2+2*_dot(photon,dark)
            positive=(self.vector_sampler.current.spin_sum(parent,photon,dark,self.mass)+
                      self.current.scalar_spin_sum(invariant,self.mass**2))
            density=self.current.spin_sum(parent,photon,dark,self.mass)
            probability=density/(2*positive)
            if not math.isfinite(probability) or probability<0 or probability>1+2.e-10:
                raise RuntimeError('Coherent VMD/scalar Cauchy acceptance bound violated')
            if rng.random()<=probability:
                event['component_proposal_trials']=event.get('proposal_trials',1)
                event['proposal_trials']=trial
                return event
        raise RuntimeError('Coherent VMD/scalar sampler exhausted its bounded trial budget')


def _source_inputs(model):
    if (model.get('decay_type') != 'pseudoscalar-double-radiative'
            or model.get('recoil_pdg_id') not in (111,221)):
        raise ValueError('An explicit neutral pseudoscalar double-radiative source is required')
    recoil, width = model.get('recoil_mass_gev'), model.get('total_parent_width_gev')
    if any(type(value) not in (int,float) or not math.isfinite(value) or value <= 0 for value in (recoil,width)):
        raise ValueError('Explicit positive recoil mass and parent total width are required')
    current = LocalVMDCurrent(model['double_radiative_model'])
    key = (current.domain,current.reference,tuple(tuple(sorted(row.items())) for row in current.vectors),
           json.dumps(current.scalar,sort_keys=True,allow_nan=False),current.kind)
    return recoil,width,key


@functools.lru_cache(maxsize=64)
def _sampler(parent_mass,recoil_mass,mass,key):
    domain,reference,vectors,scalar,kind = key
    scalar=json.loads(scalar)
    current = LocalVMDCurrent(dict(kind=kind,reference=reference,
        virtual_vertex_prescription='constant-local-couplings',omitted_scalar_and_chiral_terms=scalar is None,
        scalar_current=scalar,valid_mass_range_gev=domain,vectors=[dict(row) for row in vectors]))
    sampler=CoherentScalarSampler if scalar is not None else LocalVMDSampler
    return sampler(current,parent_mass,recoil_mass,mass)


@functools.lru_cache(maxsize=128)
def _unit_width(parent_mass,recoil_mass,mass,key):
    current = _sampler(parent_mass,recoil_mass,mass,key).current
    previous = current.integrated_width(parent_mass,recoil_mass,mass,1.,order=16)
    for order in (24,40,64,80):
        value = current.integrated_width(parent_mass,recoil_mass,mass,1.,order=order)
        if abs(value-previous) <= 2.e-5*max(value,previous):
            return value
        previous = value
    raise ValueError('Double-radiative production width failed its numerical convergence budget')


def branching_coefficient(parent_mass,mass,model):
    """BR(P->P'gamma A')/epsilon_em^2; no A' dimuon BR or source yield."""
    recoil,width,key = _source_inputs(model)
    if not math.isfinite(parent_mass) or not math.isfinite(mass) or parent_mass <= 0 or mass < 0:
        raise ValueError('Finite physical parent and dark masses required')
    if mass >= parent_mass-recoil:
        return 0.
    return _unit_width(parent_mass,recoil,mass,key)/width


def sample_decay(parent,mass,proper_ctau_mm,rng,*,model):
    recoil,_,key = _source_inputs(model)
    if len(parent) != 4 or not all(math.isfinite(x) for x in parent) or parent[0] <= 0:
        raise ValueError('Finite timelike parent required')
    squared = _dot(parent,parent)
    if squared <= 0:
        raise ValueError('Timelike parent required')
    result = _sampler(math.sqrt(squared),recoil,mass,key).sample(parent,proper_ctau_mm,rng)
    result.update(recoil_pdg_ids=[model['recoil_pdg_id'],22],recoil_masses_gev=[recoil,0.],
                  recoil_natural_decay_required=True)
    return result


def _angular_mean_lab_boost(parent,energy,mass):
    parent_mass = math.sqrt(_dot(parent,parent))
    gamma = parent[0]/parent_mass
    beta_gamma = math.sqrt(sum(x*x for x in parent[1:]))/parent_mass
    momentum = math.sqrt(max(0.,(energy-mass)*(energy+mass)))
    center,half_range = gamma*energy,beta_gamma*momentum
    radial = math.sqrt(max(0.,(center-mass)*(center+mass)))
    if half_range == 0:
        return radial/mass
    if half_range < 1.e-3*radial*radial/center:
        return (radial-mass*mass*half_range*half_range/(6*radial**3))/mass
    def primitive(value):
        value = max(value,mass)
        return .5*(value*math.sqrt((value-mass)*(value+mass))-mass*mass*math.acosh(value/mass))
    return (primitive(center+half_range)-primitive(center-half_range))/(2*half_range*mass)


def mean_lab_beta_gamma(parent,mass,model):
    """Coherent Dalitz and isotropic-parent angular mean of |p_A'|/m_A'."""
    recoil,_,key = _source_inputs(model)
    if (len(parent) != 4 or not all(math.isfinite(x) for x in parent) or parent[0] <= 0
            or _dot(parent,parent) <= 0 or not math.isfinite(mass) or mass <= 0):
        raise ValueError('Finite timelike parent and positive dark mass required')
    parent_mass = math.sqrt(_dot(parent,parent))
    sampler = _sampler(parent_mass,recoil,mass,key)
    denominator = _unit_width(parent_mass,recoil,mass,key)
    moment = lambda dark: _angular_mean_lab_boost(parent,dark[0],mass)
    previous = None
    for order in (24,40,64,80):
        numerator = sampler.current.integrated_width(parent_mass,recoil,mass,1.,order=order,moment=moment)
        if previous is not None and abs(numerator-previous) <= 2.e-5*max(numerator,previous):
            return numerator/denominator
        previous = numerator
    raise ValueError('Double-radiative mean boost failed its numerical convergence budget')
