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
