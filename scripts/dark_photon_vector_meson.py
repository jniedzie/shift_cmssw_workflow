# Code style: AI-code. AI-generated vector-meson radiative dark-photon kernel.
"""V -> P A', A' -> mu+mu- for an explicitly unpolarized vector parent.

The magnetic VPgamma amplitude is proportional to
  epsilon_{mu nu rho sigma} eps_V^mu eps_A^nu p_V^rho q^sigma.
In the parent rest frame this is M*(eps_V cross eps_A) dot q. It produces
transverse A' states only. Averaging the three parent spin states makes the
A' direction isotropic. A polarized source requires its own spin density
matrix and is deliberately unsupported here.

BR(V -> P A') / BR(V -> P gamma) = epsilon^2 * |F(m_A'^2)/F(0)|^2
                                      * [p*(m_A')/p*(0)]^3.
There is no identical-photon factor two. See KLOE arXiv:1210.3927 Eq.(2).
The massive-muon helicity density is NA60 arXiv:1608.07898 Eq.(1); its omega
phase-space and measured single-pole form factor are Eqs.(5),(7).

All rates, form factors, mass domains and their provenance must be supplied.
Published chiral/vector-dominance benchmarks and measured finite-bin inputs
are explicit model choices; none is substituted for an omitted transition.
The finite widths below regulate the virtual transition propagator. They do
not implement the physical parent's mass spectrum or a spin measurement.
The photon branching input is at the supplied parent mass. Reusing a PDG pole
branching fraction across a broad generated mass spectrum needs a separately
reviewed off-shell partial/total-width prescription.
P's subsequent natural decay belongs to the generator, not this kernel.
Four-vectors are (E,px,py,pz), GeV; decay displacements are (x,y,z,ct), mm.
The collision source applies BR(A' -> mu+mu-) and source exposure accounting.
"""
import math
from copy import deepcopy
from itertools import product

from dark_photon_meson import MUON_MASS_GEV, boost, dot, transverse_muon_density


def two_body_momentum(parent_mass, recoil_mass, mass):
    """sqrt(lambda(M^2,mP^2,mA^2))/(2M), factored at both thresholds."""
    if not all(math.isfinite(x) for x in (parent_mass, recoil_mass, mass)):
        raise ValueError('Finite physical masses are required')
    if parent_mass <= 0 or recoil_mass < 0 or mass < 0:
        raise ValueError('Invalid two-body masses')
    if mass >= parent_mass-recoil_mass:
        return 0.
    factors = (parent_mass-recoil_mass-mass, parent_mass+recoil_mass+mass,
               parent_mass-recoil_mass+mass, parent_mass+recoil_mass-mass)
    return math.sqrt(math.prod(factors))/(2*parent_mass)


def radiative_branching(parent_mass, recoil_mass, mass, epsilon, br_p_gamma,
                        form_factor_abs2):
    """Physical V -> P A' branching fraction; excludes BR(A' -> mu+mu-)."""
    momentum = two_body_momentum(parent_mass, recoil_mass, mass)
    if not all(math.isfinite(x) for x in (epsilon, br_p_gamma, form_factor_abs2)):
        raise ValueError('Finite branching inputs are required')
    if not 0 <= epsilon <= .1 or not 0 <= br_p_gamma <= 1 or form_factor_abs2 < 0:
        raise ValueError('Invalid small mixing, photon branching or squared form factor')
    if momentum == 0:
        return 0.
    photon_momentum = two_body_momentum(parent_mass, recoil_mass, 0.)
    value = epsilon**2*br_p_gamma*form_factor_abs2*(momentum/photon_momentum)**3
    if value > 1:
        raise ValueError('Radiative probability exceeds one; model is invalid')
    return value


def transition_form_factor_abs2(mass, model):
    """Explicit measured or published benchmark transition, F(0)=1.

    The validity domain is a model input, not inferred from a floating native
    parent mass. This prevents broad parent tails from extending a fitted
    omega transition through its artificial single-pole divergence.
    """
    if not math.isfinite(mass) or mass < 0:
        raise ValueError('Finite nonnegative transition virtuality is required')
    if not model.get('reference'):
        raise ValueError('An explicit transition model and reference are required')
    domain = model.get('valid_mass_range_gev')
    if (not isinstance(domain,(list,tuple)) or len(domain) != 2 or
            not all(type(x) in (int,float) and math.isfinite(x) for x in domain) or
            not 0 <= domain[0] < domain[1]):
        raise ValueError('An explicit finite transition-model mass domain is required')
    if mass != 0 and not domain[0] <= mass <= domain[1]:
        raise ValueError('Requested mass lies outside the declared transition-model domain')
    kind = model.get('kind')
    if kind == 'single-pole-slope':
        slope = model.get('slope_gev_minus2')
        if type(slope) not in (int,float) or not math.isfinite(slope) or slope <= 0:
            raise ValueError('An explicit finite positive transition slope is required')
        denominator = 1-slope*mass**2
        if denominator <= 0:
            raise ValueError('Single-pole model cannot be extrapolated through its pole')
        return denominator**-2
    if kind == 'measured-bin-abs2':
        bins = model.get('bins', [])
        if not bins or model.get('interpolation') != 'piecewise-constant-bin-average':
            raise ValueError('Explicit measured bins and their bin-average prescription are required')
        previous = None
        selected = None
        for index, row in enumerate(bins):
            low, high = row['mass_range_gev']
            value, error = row['abs2'], row['uncertainty']
            if (not all(type(x) in (int,float) and math.isfinite(x)
                        for x in (low,high,value,error)) or not 0 <= low < high or
                    value <= 0 or error < 0 or previous is not None and low != previous):
                raise ValueError('Measured bins must be finite, positive and contiguous')
            if low <= mass < high or index == len(bins)-1 and mass == high:
                selected = value
            previous = high
        if mass == 0:
            return 1.
        if selected is None:
            raise ValueError('Requested mass has no measured transition bin')
        return selected
    if kind in ('chiral-contact-vector', 'finite-width-vector-pole', 'running-width-vector-pole'):
        pole = model.get('pole_mass_gev')
        width = model.get('pole_width_gev')
        if (not all(type(x) in (int,float) and math.isfinite(x) and x > 0
                    for x in (pole,width))):
            raise ValueError('Explicit finite positive propagator mass and width are required')
        if kind == 'finite-width-vector-pole':
            # Constant-width VMD must be normalized at zero as a complex
            # amplitude; sum(a_V)=1 alone would not make F(0)=1.
            return pole**2*(pole**2+width**2)/((pole**2-mass**2)**2+pole**2*width**2)
        if kind == 'chiral-contact-vector':
            coefficient = model.get('contact_vector_ratio')
            if type(coefficient) not in (int,float) or not math.isfinite(coefficient) or coefficient <= 0:
                raise ValueError('An explicit positive chiral contact/vector ratio is required')
        mode = model.get('propagator_width')
        if mode == 'constant':
            running = width
        elif mode == 'rho-pipi-pwave':
            # Ivashyn arXiv:1111.1291 Eqs.(11),(12), below the KK threshold.
            pion = model.get('pion_mass_gev')
            kaon = model.get('kaon_mass_gev')
            if type(pion) not in (int,float) or not math.isfinite(pion) or not 0 < 2*pion < pole:
                raise ValueError('Explicit physical pion threshold is required')
            if type(kaon) not in (int,float) or not math.isfinite(kaon) or not kaon > pion:
                raise ValueError('Explicit physical kaon threshold is required')
            if domain[1] >= 2*kaon:
                raise ValueError('The pipi-only rho width cannot omit an open KK channel')
            running = (width*(pole/mass)**2*((mass**2-4*pion**2)/(pole**2-4*pion**2))**1.5
                       if mass > 2*pion else 0.)
        else:
            raise ValueError('An explicit constant or rho-pipi-pwave propagator width is required')
        denominator = complex(mass**2-pole**2, mass*running)
        if denominator == 0:
            raise ValueError('The transition propagator has an unregulated pole')
        if kind == 'running-width-vector-pole':
            # Danilkin et al., arXiv:1409.7708 Eqs.(49),(50).
            return pole**4/abs(denominator)**2
        return abs(1-coefficient*mass**2/denominator)**2
    raise ValueError('Unsupported vector transition-form-factor model')


def transition_form_factor_variations(mass, model):
    """Parameter-box values; never call a model discrepancy a measured error.

    ``parameter_uncertainties`` names existing positive scalar model inputs.
    Missing theory uncertainty stays missing in the model card, rather than
    acquiring an invented percent variation here. Bin errors are the reported
    total KLOE errors; their unavailable covariance is not reconstructed.
    """
    central = transition_form_factor_abs2(mass, model)
    if model['kind'] == 'single-pole-slope':
        errors = {'slope_gev_minus2': math.hypot(model['slope_stat_gev_minus2'],
                                               model['slope_syst_gev_minus2'])}
    elif model['kind'] == 'measured-bin-abs2':
        if mass == 0:
            return [central]
        row = next(row for index,row in enumerate(model['bins'])
                   if row['mass_range_gev'][0] <= mass < row['mass_range_gev'][1] or
                   index == len(model['bins'])-1 and mass == row['mass_range_gev'][1])
        return [central-row['uncertainty'], central+row['uncertainty']]
    else:
        errors = model.get('parameter_uncertainties', {})
    if any(key not in model or type(error) not in (int,float) or
           not math.isfinite(error) or error < 0 for key,error in errors.items()):
        raise ValueError('Transition variations require finite declared parameter errors')
    values = [central]
    for signs in product((-1,1), repeat=len(errors)):
        shifted = deepcopy(model)
        for (key,error),sign in zip(errors.items(), signs):
            shifted[key] += sign*error
        values.append(transition_form_factor_abs2(mass, shifted))
    return values



def mean_lab_beta_gamma(parent, recoil_mass, mass, *, polarization):
    """Exact isotropic two-body angular mean; use this mechanism's LAB anchor."""
    if polarization != 'unpolarized':
        raise ValueError('Only an explicitly unpolarized vector parent is supported')
    if len(parent) != 4 or not all(math.isfinite(x) for x in parent) or parent[0] <= 0:
        raise ValueError('Invalid parent four-vector')
    p2 = dot(parent[1:],parent[1:])
    # Match the dipion kernels' product-based invariant exactly. At high boosts
    # even an ulp from pow(x, 2) can close their final physical quadrature node.
    squared = math.fsum((parent[0]*parent[0], *(-component*component for component in parent[1:])))
    if squared <= 0 or mass <= 0:
        raise ValueError('A timelike parent and positive signal mass are required')
    parent_mass = math.sqrt(squared)
    momentum = two_body_momentum(parent_mass,recoil_mass,mass)
    if momentum == 0:
        raise ValueError('Closed vector-to-pseudoscalar radiative channel')
    a = parent[0]/parent_mass*math.hypot(momentum,mass)
    b = math.sqrt(p2)/parent_mass*momentum
    p = math.sqrt(max(0.,(a-mass)*(a+mass)))
    if b == 0:
        return p/mass
    if b < 1.e-3*p*p/a:
        return (p-mass**2*b*b/(6*p**3))/mass

    def primitive(energy):
        if energy < mass:
            if mass-energy > 8*math.ulp(max(a,mass)):
                raise ValueError('The angular endpoint is below its mass shell')
            energy = mass
        momentum = math.sqrt((energy-mass)*(energy+mass))
        u = math.asinh(momentum/mass)
        if u < 1.e-3:
            return mass**2*u**3*(1/3+u*u*(1/15+u*u*(2/315+u*u/2835)))
        return .5*(energy*momentum-mass**2*u)

    result = (primitive(a+b)-primitive(a-b))/(2*b*mass)
    if not math.isfinite(result) or result < 0:
        raise ValueError('Invalid vector-meson angular mean boost')
    return result


def sample_decay(parent, recoil_mass, mass, proper_ctau_mm, rng, *, polarization):
    """Unconditional V -> P A' -> P mu+mu-, preserving the massive recoil.

    The caller must subsequently decay the recoil through its natural modes,
    preserve spectators, and apply the physical BR_mumu exactly once in the
    source probability. No position, geometry or muon acceptance cut is used.
    """
    if polarization != 'unpolarized':
        raise ValueError('Only an explicitly unpolarized vector parent is supported')
    if len(parent) != 4 or not all(math.isfinite(x) for x in parent) or parent[0] <= 0:
        raise ValueError('Invalid parent four-vector')
    squared = math.fsum((parent[0]**2, -dot(parent[1:],parent[1:])))
    if squared <= 0:
        raise ValueError('The vector parent must be timelike')
    parent_mass = math.sqrt(squared)
    momentum = two_body_momentum(parent_mass,recoil_mass,mass)
    if (momentum == 0 or mass <= 2*MUON_MASS_GEV or
            not math.isfinite(proper_ctau_mm) or proper_ctau_mm <= 0):
        raise ValueError('Closed decay or invalid physical lifetime')
    cosine = 2*rng.random()-1
    azimuth = 2*math.pi*rng.random()
    sine = math.sqrt(1-cosine*cosine)
    axis = (sine*math.cos(azimuth),sine*math.sin(azimuth),cosine)
    auxiliary = (0.,0.,1.) if abs(cosine) < .9 else (1.,0.,0.)
    cross = (axis[1]*auxiliary[2]-axis[2]*auxiliary[1],
             axis[2]*auxiliary[0]-axis[0]*auxiliary[2],
             axis[0]*auxiliary[1]-axis[1]*auxiliary[0])
    norm = math.sqrt(dot(cross,cross))
    first = tuple(x/norm for x in cross)
    second = (axis[1]*first[2]-axis[2]*first[1],
              axis[2]*first[0]-axis[0]*first[2],
              axis[0]*first[1]-axis[1]*first[0])
    maximum = transverse_muon_density(1.,mass)
    while True:
        angle = 2*rng.random()-1
        if rng.random()*maximum <= transverse_muon_density(angle,mass):
            break
    phi = 2*math.pi*rng.random()
    sine = math.sqrt(1-angle*angle)
    direction = tuple(angle*n+sine*(math.cos(phi)*u+math.sin(phi)*v)
                      for n,u,v in zip(axis,first,second))
    muon_momentum = math.sqrt((mass/2-MUON_MASS_GEV)*(mass/2+MUON_MASS_GEV))
    muon = (mass/2,)+tuple(muon_momentum*x for x in direction)
    antimuon = (mass/2,)+tuple(-muon_momentum*x for x in direction)
    energy = math.hypot(momentum,mass)
    recoil_energy = math.hypot(momentum,recoil_mass)
    beta = tuple(momentum*x/energy for x in axis)
    parent_beta = tuple(x/parent[0] for x in parent[1:])
    dark = boost((energy,)+tuple(momentum*x for x in axis),parent_beta)
    recoil = boost((recoil_energy,)+tuple(-momentum*x for x in axis),parent_beta)
    muon = boost(boost(muon,beta),parent_beta)
    antimuon = boost(boost(antimuon,beta),parent_beta)
    length = -proper_ctau_mm*math.log1p(-rng.random())
    return dict(dark_photon_p4=dark,recoil_p4=recoil,muon_p4=muon,antimuon_p4=antimuon,
        dark_photon_mass_gev=mass,recoil_mass_gev=recoil_mass,muon_mass_gev=MUON_MASS_GEV,
        proper_decay_length_mm=length,decay_displacement_mm=tuple(length*p/mass for p in dark[1:]),
        decay_ct_mm=length*dark[0]/mass,unconditional_decay=True,
        parent_polarization=polarization,recoil_natural_decay_required=True)
