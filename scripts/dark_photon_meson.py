# Code style: AI-code. AI-generated pseudoscalar dark-photon decay kernel.
"""P -> gamma A', A' -> mu+mu- kinematics and per-parent branching weight.

No absolute parent yield, hadronic source, form factor or detector efficiency
is supplied here. Callers must provide those inputs and their provenance.
The pointlike radiative ratio follows arXiv:2401.09136 Eq.(6); the transition form
factor is supplied explicitly as F(m_A'^2)/F(0). A' is transverse for this
pseudoscalar radiative decay. Four-vectors use (E,px,py,pz), GeV.
"""
import math

MUON_MASS_GEV=.1056583755


def transition_form_factor_abs2(mass, model):
    """BESIII arXiv:2401.09136 Eqs.(3),(5), normalized to F(0)=1.

    Numerical fit inputs and their provenance come from the supplied model
    card. No unmeasured default form factor is inserted here.
    """
    if not math.isfinite(mass) or mass < 0:
        raise ValueError('Finite nonnegative transition virtuality required')
    pole = model.get('lambda_gev')
    if type(pole) not in (int, float) or not math.isfinite(pole) or pole <= 0:
        raise ValueError('Explicit finite positive transition-form-factor pole required')
    if model.get('kind') == 'single-pole':
        denominator = 1-(mass/pole)**2
        if denominator == 0:
            raise ValueError('Single-pole form factor is singular at the requested mass')
        return denominator**-2
    if model.get('kind') == 'finite-width-multipole':
        width = model.get('gamma_gev')
        if type(width) not in (int, float) or not math.isfinite(width) or width <= 0:
            raise ValueError('Explicit finite positive form-factor width required')
        return pole**2*(pole**2+width**2)/((pole**2-mass**2)**2+pole**2*width**2)
    raise ValueError('Unsupported transition-form-factor model')


def radiative_branching(parent_mass,mass,epsilon,br_gamma_gamma,form_factor_ratio):
    if not all(math.isfinite(x) for x in [parent_mass,mass,epsilon,br_gamma_gamma]):
        raise ValueError('Nonfinite radiative-decay input')
    if parent_mass<=0 or mass<=0 or not 0<=epsilon<=.1 or not 0<=br_gamma_gamma<=1:
        raise ValueError('Invalid physical mass, branching fraction or small-mixing epsilon')
    norm=abs(form_factor_ratio)**2
    if not math.isfinite(norm):raise ValueError('Nonfinite transition form factor')
    if mass>=parent_mass:return 0.
    value=2*epsilon**2*br_gamma_gamma*(1-(mass/parent_mass)**2)**3*norm
    if value>1:raise ValueError('Radiative probability exceeds one; model is invalid')
    return value


def transverse_muon_density(cosine,mass):
    if not -1<=cosine<=1 or mass<=2*MUON_MASS_GEV:raise ValueError('Closed dimuon channel or invalid angle')
    r=(MUON_MASS_GEV/mass)**2
    return (1+cosine**2+4*r*(1-cosine**2))/(8*(1+2*r)/3)


def dot(a,b):return math.fsum(x*y for x,y in zip(a,b))


def boost(vector,beta):
    b2=dot(beta,beta)
    if b2>=1 or b2<0:raise ValueError('Invalid Lorentz boost')
    if b2==0:return tuple(vector)
    gamma=1/math.sqrt(1-b2);product=dot(beta,vector[1:])
    shift=(gamma-1)*product/b2+gamma*vector[0]
    return (gamma*(vector[0]+product),)+tuple(p+shift*b for p,b in zip(vector[1:],beta))


def mean_lab_beta_gamma(parent, mass):
    """Exact isotropic P -> gamma A' angular mean of |p_A'| / m_A'."""
    if len(parent) != 4 or not all(math.isfinite(value) for value in parent) or parent[0] <= 0:
        raise ValueError('Invalid parent four-vector')
    parent_m2 = parent[0]**2 - dot(parent[1:], parent[1:])
    if not math.isfinite(mass) or mass <= 0 or parent_m2 <= mass**2:
        raise ValueError('Closed radiative channel')
    parent_mass = math.sqrt(parent_m2)
    recoil = (parent_m2 - mass**2) / (2 * parent_mass)
    rest_energy = (parent_m2 + mass**2) / (2 * parent_mass)
    a = parent[0] / parent_mass * rest_energy
    b = math.sqrt(dot(parent[1:], parent[1:])) / parent_mass * recoil
    p = math.sqrt(max(0., (a-mass)*(a+mass)))
    if b == 0:
        return p / mass
    # The expansion avoids subtracting nearly equal primitive values.
    # Its next term is below 1e-12 relative under this branch condition.
    if b < 1.e-3 * p**2 / a:
        return (p - mass**2*b**2/(6*p**3)) / mass

    def primitive(energy):
        if energy < mass:
            if mass-energy > 8*math.ulp(max(a, mass)):
                raise ValueError('Two-body angular endpoint is below its mass shell')
            energy = mass
        momentum = math.sqrt((energy-mass)*(energy+mass))
        u = math.asinh(momentum/mass)
        if u < 1.e-3:
            return mass**2*u**3*(1/3 + u*u*(1/15 + u*u*(2/315 + u*u/2835)))
        return .5*(energy*momentum - mass**2*u)

    result = (primitive(a+b)-primitive(a-b))/(2*b*mass)
    if not math.isfinite(result) or result < 0:
        raise ValueError('Invalid angular-mean boost')
    return result


def sample_decay(parent,mass,proper_ctau_mm,rng):
    """One unconditional decay; no angular, position or detector acceptance cut."""
    if len(parent)!=4 or not all(math.isfinite(x) for x in parent) or parent[0]<=0:
        raise ValueError('Invalid parent four-vector')
    squared=parent[0]**2-dot(parent[1:],parent[1:])
    if squared<=0:raise ValueError('Parent must be timelike')
    parent_mass=math.sqrt(squared)
    if not 2*MUON_MASS_GEV<mass<parent_mass or not math.isfinite(proper_ctau_mm) or proper_ctau_mm<=0:
        raise ValueError('Decay closed or invalid physical lifetime')
    c=2*rng.random()-1;phi=2*math.pi*rng.random();s=math.sqrt(1-c*c)
    axis=(s*math.cos(phi),s*math.sin(phi),c)
    a=(0.,0.,1.) if abs(c)<.9 else (1.,0.,0.)
    cross=(axis[1]*a[2]-axis[2]*a[1],axis[2]*a[0]-axis[0]*a[2],axis[0]*a[1]-axis[1]*a[0])
    norm=math.sqrt(dot(cross,cross));first=tuple(x/norm for x in cross)
    second=(axis[1]*first[2]-axis[2]*first[1],axis[2]*first[0]-axis[0]*first[2],axis[0]*first[1]-axis[1]*first[0])
    r=(MUON_MASS_GEV/mass)**2
    while True:
        angle=2*rng.random()-1
        if 2*rng.random()<=1+angle*angle+4*r*(1-angle*angle):break
    azimuth=2*math.pi*rng.random();sine=math.sqrt(1-angle*angle)
    direction=tuple(angle*n+sine*(math.cos(azimuth)*u+math.sin(azimuth)*v) for n,u,v in zip(axis,first,second))
    momentum=math.sqrt(mass*mass/4-MUON_MASS_GEV**2)
    muon=(mass/2,)+tuple(momentum*x for x in direction)
    antimuon=(mass/2,)+tuple(-momentum*x for x in direction)
    recoil=(parent_mass*parent_mass-mass*mass)/(2*parent_mass)
    energy=(parent_mass*parent_mass+mass*mass)/(2*parent_mass)
    beta=tuple(recoil*x/energy for x in axis)
    parent_beta=tuple(x/parent[0] for x in parent[1:])
    dark=boost((energy,)+tuple(recoil*x for x in axis),parent_beta)
    photon=boost((recoil,)+tuple(-recoil*x for x in axis),parent_beta)
    muon=boost(boost(muon,beta),parent_beta);antimuon=boost(boost(antimuon,beta),parent_beta)
    length=-proper_ctau_mm*math.log1p(-rng.random())
    return dict(dark_photon_p4=dark,photon_p4=photon,muon_p4=muon,antimuon_p4=antimuon,
        dark_photon_mass_gev=mass,muon_mass_gev=MUON_MASS_GEV,
        proper_decay_length_mm=length,decay_displacement_mm=tuple(length*p/mass for p in dark[1:]),
        decay_ct_mm=length*dark[0]/mass,unconditional_decay=True)
