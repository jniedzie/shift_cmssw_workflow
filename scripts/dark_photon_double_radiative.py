# Code style: AI-code. AI-generated neutral pseudoscalar radiative-current model.
"""Rate-only local VMD model of P -> P' gamma A', not a production adapter.

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
Scalar/chiral amplitudes and extra timelike vertex structures are omitted.
Photon predictions do not fix those omissions: this paper underpredicts the
eta photon rate by about a factor two. Its eta'-eta scalar term is substantial.

An optional absolute-amplitude envelope encloses the supplied coupling
magnitudes and all interference phases within this fixed local VMD model.
It may conservatively overcount already represented eta'->gamma V feed-down.
Do not add the inclusive eta' mode to existing V->P'A' production: coherent
replacement and an ancestry overlap audit are required first. BR(A'->mumu),
source yields, parent flight and detector acceptance are excluded here.
Four-vectors are (E,px,py,pz), masses/widths GeV, VP couplings GeV^-1.
"""
import cmath
import math

from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_meson import boost
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


def empirical_reference_model(inputs, parent_pdg_id, recoil_pdg_id, *, coupling_errors):
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
    return dict(kind='local-vp-field-strength-vmd',reference=inputs['publication']['url'],
        virtual_vertex_prescription=inputs['virtual_vertex_prescription'],
        omitted_scalar_and_chiral_terms=inputs['omitted_scalar_and_chiral_terms'],
        valid_mass_range_gev=[0.,parent['mass_gev']-recoil['mass_gev']],vectors=vectors)


class LocalVMDCurrent:
    def __init__(self, model):
        if (model.get('kind') != 'local-vp-field-strength-vmd' or not model.get('reference')
                or model.get('virtual_vertex_prescription') != 'constant-local-couplings'
                or not model.get('omitted_scalar_and_chiral_terms')
                or not model.get('vectors')):
            raise ValueError('Require an explicit local-VMD model, virtual prescription and omissions')
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
        return self._coefficients(_dot(r,r))[0]*a+self._coefficients(_dot(s,s))[0]*b

    def spin_sum(self, parent, photon, dark, mass, *, envelope=False):
        r = tuple(x-y for x,y in zip(parent,photon))
        s = tuple(x-y for x,y in zip(parent,dark))
        ct,et = self._coefficients(_dot(r,r))
        cu,eu = self._coefficients(_dot(s,s))
        result = 0.
        for first in polarizations(photon,0.):
            for second in polarizations(dark,mass):
                a,b = self._kinematic_terms(parent,photon,dark,first,second)
                result += (et*abs(a)+eu*abs(b))**2 if envelope else abs(ct*a+cu*b)**2
        return result

    def _panels(self, low, high, transform=lambda x:x):
        boundaries = {low,high}
        for row in self.vectors:
            pole, scale = row['mass_gev']**2, row['mass_gev']*row['width_gev']
            for offset in (0.,-1.,1.,-4.,4.,-16.,16.):
                point = transform(pole+offset*scale)
                if low < point < high:
                    boundaries.add(point)
        values = sorted(boundaries)
        return list(zip(values,values[1:]))

    def integrated_width(self, parent_mass, recoil_mass, mass, epsilon_em, *,
                         photon_reference=False, envelope=False, order=12):
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
                for first,last in self._panels(-1.,1.,lambda value:(value-a)/b):
                    for cosine_node,cosine_weight in _gauss_legendre(order):
                        cosine = (first+last)/2+(last-first)/2*cosine_node
                        dark = boost((dark_energy,inner*math.sqrt(1-cosine*cosine),0.,inner*cosine),
                                     (0.,0.,-outer/pair_energy))
                        angular += (last-first)/2*cosine_weight*self.spin_sum(parent,photon,dark,mass,envelope=envelope)
                density = outer*inner/(128*math.pi**3*parent_mass**2*pair_mass)
                result += (high-low)/2*weight*density*angular
        return result*epsilon_em**2/(2 if photon_reference else 1)
