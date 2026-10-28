#!/usr/bin/env python3
# Code style: AI-code. SensCalc proton-bremsstrahlung benchmark kernels.
"""Published FR and Baseline prescriptions, with explicit collision conditions.

Ported from SensCalc ca588864742ee83ae8fd3a987b9698ea75c19251, under its
BSD-3-Clause license (config/reference/senscalc/LICENSE). The public complex
form-factor tables and their log-log interpolation are kept unchanged.
This module supplies a production density, not qualified CMSSW events.
"""
import bisect
import cmath
from functools import lru_cache
import hashlib
import math
from pathlib import Path
import random
import json

SENSCALC_COMMIT='ca588864742ee83ae8fd3a987b9698ea75c19251'
REFERENCES=('https://arxiv.org/abs/2409.11096','https://arxiv.org/abs/2409.09123',
    'https://github.com/maksymovchynnikov/SensCalc/tree/'+SENSCALC_COMMIT)
TABLE_HASHES={'FFproton_UAmodel.txt':'472879a314ff0760e48fe01da0e09e651ee85f70812ee0f7f192365c13f12619',
    'FFproton_UAmodel_Uncertainty.txt':'ccbd8ead23261de9e18009f01da4cc9d942f578e1b8701bf0dbad603fd4dcdae'}
ALPHA_EM=1/137.  # SensCalc codes/generic.nb, not a fitted correction.
PROTON_MASS_GEV=.938  # SensCalc's fixed-target collision-energy convention.


@lru_cache(maxsize=4)
def form_factor_table(directory,variation):
    if variation not in ('Central','Lower','Upper'):raise ValueError('Unknown form-factor variation')
    filename='FFproton_UAmodel.txt' if variation=='Central' else 'FFproton_UAmodel_Uncertainty.txt'
    path=Path(directory)/filename
    if hashlib.sha256(path.read_bytes()).hexdigest()!=TABLE_HASHES[filename]:
        raise ValueError('Changed pinned SensCalc proton form-factor table')
    columns=(1,2) if variation=='Central' else (1,3) if variation=='Lower' else (2,4)
    rows=[]
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        fields=line.split();values=[]
        for column in columns:
            value=complex(fields[column].replace('+-','-'))
            # Mathematica removes a zero imaginary part before Log10.
            values.append(complex(value.real,0.) if value.imag==0 else value)
        rows.append((float(fields[0]),*values))
    if (len(rows)<2 or any(a[0]>=b[0] for a,b in zip(rows,rows[1:])) or
            any(not all(math.isfinite(v) for z in row[1:] for v in (z.real,z.imag)) for row in rows)):
        raise ValueError('Malformed proton form-factor table')
    return tuple(rows)


def form_factors(mass,directory=None,variation='Central'):
    """Match ff.nb's first-order interpolation of complex log(F) vs log(m)."""
    if directory is None:directory=Path(__file__).resolve().parents[1]/'config/reference/senscalc'
    rows=form_factor_table(str(Path(directory).resolve()),variation)
    if not math.isfinite(mass) or not rows[0][0]<mass<rows[-1][0]:
        raise ValueError('Mass outside the pinned proton form-factor support; no extrapolation')
    index=bisect.bisect_right([row[0] for row in rows],mass)-1
    low,high=rows[index:index+2]
    fraction=math.log(mass/max(low[0],1.e-90))/math.log(high[0]/max(low[0],1.e-90))
    return tuple(cmath.exp((1-fraction)*cmath.log(a)+fraction*cmath.log(b)) for a,b in zip(low[1:],high[1:]))


def baseline_form_factor_squared(mass):
    """The six-resonance Dirac form factor in the published Baseline option."""
    amplitudes=[]
    for masses,widths,leading in (((.78,1.42,1.65),(.0084,.29,.315),1.011),
                                  ((.77,1.45,1.7),(.15,.4,.25),.616)):
        second=(-leading*masses[0]**2-(.5-leading)*masses[2]**2)/(masses[1]**2-masses[2]**2)
        couplings=(leading,second,.5-leading-second)
        amplitudes.extend(c*m*m/complex(m*m-mass*mass,-m*w) for m,w,c in zip(masses,widths,couplings))
    return abs(sum(amplitudes))**2


def splitting_density(mass,z,pt,*,model='FR',factors=None,lambda_p_gev=1.,
                      proton_mass_gev=PROTON_MASS_GEV,alpha_em=ALPHA_EM):
    """dP/(epsilon^2 dz dpT), including the azimuthal integral and dpT Jacobian."""
    if (not all(math.isfinite(x) for x in (mass,z,pt,lambda_p_gev,proton_mass_gev,alpha_em)) or
            mass<=0 or not 0<z<1 or pt<0 or min(lambda_p_gev,proton_mass_gev,alpha_em)<=0):
        raise ValueError('Finite physical bremsstrahlung coordinates required')
    mp2=proton_mass_gev**2;m2=mass**2;u=1-z;h=pt*pt+u*m2+z*z*mp2
    prefactor=alpha_em/(2*math.pi*h)
    if model=='Baseline':
        shape=(1+u*u)/z-2*z*u*((2*mp2+m2)/h-2*z*z*mp2*mp2/(h*h))
        shape+=2*z*u*(1+u*u)*mp2*m2/(h*h)+2*z*u*u*m2*m2/(h*h)
        value=2*pt*prefactor*shape*baseline_form_factor_squared(mass)
    elif model=='FR':
        f1,f2=form_factors(mass) if factors is None else factors
        w1=prefactor*((1+u*u)/z-z*u*(2*mp2+m2)/h)
        w2=w1+alpha_em/(4*math.pi*mp2)*(1-u*(4*mp2+m2)/h)*(1/z-z*(4*mp2-m2)/(4*h))
        w12=prefactor*(2*z-z*u*3*m2/h)
        virtuality=1/(1+(h/(z*lambda_p_gev**2))**2)
        value=2*pt*virtuality**2*(abs(f1)**2*w1+abs(f2)**2*w2+(f1*f2.conjugate()).real*w12)
    else:raise ValueError('Use the published FR or Baseline prescription')
    if not math.isfinite(value) or value<0:raise ValueError('Nonphysical splitting density')
    return value


def density_pb(mass,z,pt,beam_energy_gev,*,model='FR',factors=None,lambda_p_gev=1.,
               proton_mass_gev=PROTON_MASS_GEV,pt_max_gev=1.):
    """d sigma/(epsilon^2 dz dpT) for a proton on a stationary proton, in pb.

    SensCalc's NSD/total ratio is multiplied by the same total cross section
    here, leaving sigma_NSD(s') once. This component is independent of the
    PYTHIA ND meson/fragmentation cross section. z/pt limits remain explicit
    source-approximation choices; they are not detector selections.
    """
    if model not in ('FR','Baseline'):raise ValueError('Use the published FR or Baseline prescription')
    if not all(math.isfinite(x) for x in (mass,z,pt,beam_energy_gev,lambda_p_gev,proton_mass_gev,pt_max_gev)):
        raise ValueError('Finite source coordinates required')
    if min(mass,lambda_p_gev,proton_mass_gev,pt_max_gev)<=0:
        raise ValueError('Positive physical mass and source scales required')
    if beam_energy_gev<=proton_mass_gev:
        raise ValueError('A fixed-target projectile energy above its rest mass is required')
    if not 0<z<1 or pt<0:raise ValueError('Invalid source coordinates')
    h=pt*pt+(1-z)*mass*mass+z*z*proton_mass_gev**2
    applicability=h/(4*beam_energy_gev**2*z*(1-z)**2)
    if (mass*mass+pt*pt>=(z*beam_energy_gev)**2 or applicability>.1 or
            model=='Baseline' and pt>pt_max_gev):return 0.
    s=2*proton_mass_gev*(beam_energy_gev+proton_mass_gev)
    sigma_nsd_mb=1.76+19.8*((1-z)*s)**.057
    return 1.e9*sigma_nsd_mb*splitting_density(mass,z,pt,model=model,factors=factors,
        lambda_p_gev=lambda_p_gev,proton_mass_gev=proton_mass_gev)


def sampling_profile(mass,*,lambda_p_gev=1.,variation='Central',z_range=(.005,.99)):
    """Explicit fixed-target FR benchmark; cuts are source assumptions, not reco."""
    if (not .211316751<mass<2. or not .5<=lambda_p_gev<=2. or
            variation not in ('Central','Lower','Upper') or
            len(z_range)!=2 or not 0<z_range[0]<z_range[1]<1):
        raise ValueError('Require an open dimuon point below 2 GeV and a published FR variation')
    return dict(schema='shift-senscalc-bremsstrahlung-sampling-v1',model='FR',mass_gev=mass,
        lambda_p_gev=lambda_p_gev,form_factor_variation=variation,z_range=list(z_range),
        beam_energy_gev=6800.,target='stationary proton',beam_direction=-1,
        minimum_source_cross_section_mb=25.,z_strata=64,
        form_factor_table_sha256=TABLE_HASHES,senscalc_commit=SENSCALC_COMMIT,
        kinematics='z is longitudinal beam-momentum fraction; exact on-shell signal momentum',
        scope='Primary beam ISR only; no target cascade, no collision activity, no DY',
        table_directory=str(Path(__file__).resolve().parents[1]/'config/reference/senscalc'))


class SourceSampler:
    """One auxiliary importance draw per original collision, with a proven bound.

    Coordinates are t=logit(z), u=pT^2/(pT^2+a), a=(1-z)m^2+z^2 mp^2.
    FR times the coordinate Jacobian is a quadratic in 1/H, multiplied by
    [1+(H/(z Lambda^2))^2]^-2. Triangle bounds on its three coefficients,
    and analytic maxima in u, give a conservative envelope in each z stratum.
    No fitted or observed maximum is used. Overflow stops the bank.
    """
    def __init__(self,profile,base_directory=None):
        self.profile=dict(profile)
        expected=sampling_profile(profile['mass_gev'],lambda_p_gev=profile['lambda_p_gev'],
            variation=profile['form_factor_variation'],z_range=profile['z_range'])
        expected['table_directory']=profile['table_directory']
        if expected!=profile:raise ValueError('Changed or unsupported bremsstrahlung source prescription')
        directory=Path(profile['table_directory'])
        if not directory.is_absolute():
            if base_directory is None:raise ValueError('Relative form-factor inputs need their frozen directory')
            directory=Path(base_directory)/directory
        self.mass=profile['mass_gev'];self.scale=profile['lambda_p_gev'];self.energy=profile['beam_energy_gev']
        self.factors=form_factors(self.mass,directory,profile['form_factor_variation'])
        low,high=[math.log(z/(1-z)) for z in profile['z_range']]
        self.cells=[];self.cumulative=[];self.bound_pb=0.
        for i in range(profile['z_strata']):
            left=low+(high-low)*i/profile['z_strata'];right=low+(high-low)*(i+1)/profile['z_strata']
            bound=self.stratum_bound(1/(1+math.exp(-left)),1/(1+math.exp(-right)))
            self.cells.append((left,right,bound));self.bound_pb+=bound*(right-left)
            self.cumulative.append(self.bound_pb)
        if not math.isfinite(self.bound_pb) or self.bound_pb<=0:raise ValueError('Invalid analytic FR envelope')

    def stratum_bound(self,zl,zh):
        m2=self.mass**2;mp2=PROTON_MASS_GEV**2;v=1-zl
        amin=(1-zh)*m2+zl*zl*mp2;amax=v*m2+zh*zh*mp2
        f1,f2=self.factors;f=abs(f1)**2+abs(f2)**2;d=abs(f2)**2;interference=abs((f1*f2.conjugate()).real)
        k1=ALPHA_EM/(2*math.pi);k2=ALPHA_EM/(4*math.pi*mp2)
        dc=d*k2/zl
        ac=k1*(f*(1+v*v)/zl+2*zh*interference)+d*k2*(zh*abs(4*mp2-m2)/4+v*(4*mp2+m2)/zl)
        bc=k1*(f*zh*v*(2*mp2+m2)+3*zh*v*m2*interference)+d*k2*zh*v*(4*mp2+m2)*abs(4*mp2-m2)/4
        c=amin/(zh*self.scale**2)
        # Maxima of r^2, r^3 and r^4 divided by (r^2+c^2)^2, 0<r<=1.
        rd=min(c,1.);ra=min(math.sqrt(3)*c,1.)
        fd=rd**2/(rd*rd+c*c)**2;fa=ra**3/(ra*ra+c*c)**2;fb=1/(1+c*c)**2
        s=2*PROTON_MASS_GEV*(self.energy+PROTON_MASS_GEV)
        sigma=1.e9*(1.76+19.8*((1-zl)*s)**.057)
        return sigma*min(.25,zh*(1-zl))*(amax*dc*fd+ac*fa+bc/amin*fb)*(1+1.e-12)

    def coordinates(self,t,u):
        if not 0<u<1:raise ValueError('Open transverse proposal coordinate required')
        z=1/(1+math.exp(-t));a=(1-z)*self.mass**2+z*z*PROTON_MASS_GEV**2
        pt=math.sqrt(a*u/(1-u))
        jacobian=z*(1-z)*math.sqrt(a)/(2*math.sqrt(u)*(1-u)**1.5)
        density=density_pb(self.mass,z,pt,self.energy,factors=self.factors,lambda_p_gev=self.scale)
        return z,pt,density*jacobian

    def candidate(self,trial,seed,sigma_source_mb):
        if sigma_source_mb<self.profile['minimum_source_cross_section_mb']:
            raise ValueError('Source cross section is below the frozen technical unweighting bound')
        key=json.dumps(['proton-bremsstrahlung',seed,trial],separators=(',',':')).encode()
        rng=random.Random(int(hashlib.sha256(key).hexdigest(),16))
        index=bisect.bisect_right(self.cumulative,rng.random()*self.bound_pb)
        left,right,bound=self.cells[min(index,len(self.cells)-1)]
        t=left+(right-left)*rng.random();u=rng.random()
        while u==0:u=rng.random()
        z,pt,g=self.coordinates(t,u)
        if g>bound*(1+1.e-10):raise ValueError('Bremsstrahlung exceeds its analytic importance envelope')
        weight=g*self.bound_pb/(bound*sigma_source_mb*1.e9)
        phi=2*math.pi*rng.random();pz=-z*math.sqrt(self.energy**2-PROTON_MASS_GEV**2)
        return dict(id=32,index=-1,mother_id=2212,mechanism='proton-bremsstrahlung',
            weight_over_epsilon_squared=weight,p4=[math.hypot(self.mass,pt,pz),pt*math.cos(phi),pt*math.sin(phi),pz])

    @property
    def coefficient_envelope(self):
        return self.bound_pb/(self.profile['minimum_source_cross_section_mb']*1.e9)
