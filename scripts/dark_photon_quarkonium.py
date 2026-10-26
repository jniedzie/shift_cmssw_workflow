# Code style: AI-code. AI-generated heavy-quarkonium current and decay controls.
"""Attributed quarkonium electromagnetic rates and correlated decay sampling.

chi_c1 uses the E1, M2, C1 lattice form factors of arXiv:2604.15256v2,
Eqs8,53,57-59 and TableIV. Dividing the Born dilepton spectrum by the
universal photon-conversion factor gives the massive-vector width. C1 is
longitudinal and cannot be inferred from the real-photon branching fraction.

The heavy-spin E1 expression is a separate leading-multipole approximation
to arXiv:0812.0716 Eq2.8 (v_nu F^{mu nu}); its rest-frame electric fields
are omega for each transverse mode and m_A for the longitudinal mode.
Recoil corrections, higher multipoles and unknown timelike form factors
are not bounded by calling this a benchmark.

BESIII arXiv:1901.06627 defines its effective chi_c2 pole form factor relative
to Luchinsky arXiv:1709.02444 Eq2, which already supplies k/k_gamma.
Do not multiply that effective fit by an additional E1 kinematic factor.

eta_c -> gamma A' is annihilation, not an M1 transition to J/psi. The two
timelike form-factor alternatives below are hep-ph/0004112 Eqs14-16.
Their spread is not a rigorous bound on all QCD amplitudes. In particular
the light-rho alternative has different low-mass behavior from the charm
pole. All calibrations and domains must be supplied with their references.
The h_c current is Eqs9,11,70-72 and TableVII of the same lattice paper.
Its absolute electron-rate normalization differs from existing experimental
results; that tension is retained, not removed by fitting a photon BR.

Sampling assumes an explicitly unpolarized parent and returns the conditional
recoil spin density and its transported polarization basis. A J/psi recoil
must not subsequently be decayed isotropically while claiming full cascade
spin correlations. The source/native adapter must consume that density or
retain a separately qualified approximation. Existing natural Pythia recoil
decays do not acquire this information automatically.

BR(A'->mumu), parent-source normalization and detector acceptance are absent.
Masses/widths are GeV; alpha_em is the Thomson-limit EM convention.
"""
import math
import json
from functools import lru_cache
from itertools import permutations

from dark_photon_meson import MUON_MASS_GEV, boost, dot
from dark_photon_vector_meson import two_body_momentum


def _physical(value, name, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (
            value <= 0 if positive else value < 0):
        raise ValueError('Finite physical '+name+' required')
    return value


def _domain(model, kind, mass):
    _physical(mass, 'virtual mass')
    limits = model.get('valid_mass_range_gev')
    if (model.get('kind') != kind or not model.get('reference') or
            not isinstance(limits, (list, tuple)) or len(limits) != 2 or
            not all(type(x) in (int,float) and math.isfinite(x) for x in limits) or
            not 0 <= limits[0] < limits[1] or not limits[0] <= mass <= limits[1]):
        raise ValueError('Explicit model authority and physical mass domain required')


def heavy_spin_e1_ratio(parent_mass, recoil_mass, mass):
    """Gamma(A')/[epsilon_em^2 Gamma(gamma)], leading E1 with constant coupling.

    This includes the longitudinal electric field. It is proportional to k
    at the massive endpoint, unlike the inapplicable real-photon k^3 rule.
    The unpolarized leading-heavy-spin sum is the same for chi_cJ, J=0,1,2.
    It is not a qualified uncertainty envelope for any of those channels.
    """
    _physical(parent_mass, 'parent mass', positive=True)
    _physical(recoil_mass, 'recoil mass', positive=True)
    _physical(mass, 'vector mass')
    if parent_mass <= recoil_mass:
        raise ValueError('Open real-photon calibration required')
    if mass >= parent_mass-recoil_mass:
        return 0.
    momentum = two_body_momentum(parent_mass,recoil_mass,mass)
    photon = two_body_momentum(parent_mass,recoil_mass,0.)
    energy = (parent_mass**2-recoil_mass**2+mass**2)/(2*parent_mass)
    return momentum/photon*(2*energy**2+mass**2)/(2*photon**2)


def effective_pole_ratio(parent_mass, recoil_mass, mass, model):
    """Inclusive effective fit relative to k/k_gamma; no extra E1 factor."""
    _domain(model,'effective-dalitz-pole',mass)
    _physical(parent_mass,'parent mass',positive=True)
    _physical(recoil_mass,'recoil mass',positive=True)
    pole = _physical(model.get('lambda_gev'),'effective pole',positive=True)
    if not 0 < recoil_mass < parent_mass or pole <= parent_mass-recoil_mass:
        raise ValueError('Pole-free complete physical fit range required')
    if mass >= parent_mass-recoil_mass:
        return 0.
    momentum = two_body_momentum(parent_mass,recoil_mass,mass)
    photon = two_body_momentum(parent_mass,recoil_mass,0.)
    return momentum/photon/(1-(mass/pole)**2)**2


def eta_c_form_factor(mass, model):
    """Normalized timelike annihilation current: stated NR or two-pole model."""
    kind = model.get('kind')
    if kind not in ('eta-c-nr-charm-pole','eta-c-rho-jpsi-two-pole'):
        raise ValueError('Explicit supported eta_c current required')
    _domain(model,kind,mass)
    if kind == 'eta-c-nr-charm-pole':
        pole = _physical(model.get('charm_pole_gev'),'charm pole',positive=True)
        if pole <= model['valid_mass_range_gev'][1]:
            raise ValueError('NR propagator pole must be outside the requested domain')
        return 1/(1-(mass/pole)**2)
    result = 1+0j
    for name in ('rho','jpsi'):
        pole = _physical(model.get(name+'_mass_gev'),name+' pole',positive=True)
        width = _physical(model.get(name+'_width_gev'),name+' width',positive=True)
        squared_pole = pole**2-1j*pole*width
        # Complex-pole continuation of Eq14, explicitly normalized at q2=0.
        result *= squared_pole/(squared_pole-mass**2)
    return result


def chi1_lattice_width(parent_mass, recoil_mass, mass, model, *, alpha_em,
                       charge=2/3, parameter_errors=0., envelope='central'):
    """Electromagnetic Gamma(chi_c1 -> J/psi gamma*) in GeV, before mixing.

    A' has this width times epsilon_em^2. 'upper'/'lower' enclose independent
    rectangular CF,DF intervals, each given error times parameter_errors.
    They are conditional parameter boxes, not a statistical confidence level;
    unpublished inter-form-factor covariances are not invented. The upper
    sum of positive terms is conservative for every parameter in the box.
    """
    _domain(model,'chi-c1-lattice-e1-m2-c1',mass)
    for value,name in ((parent_mass,'parent mass'),(recoil_mass,'recoil mass'),
                       (alpha_em,'alpha_em')):
        _physical(value,name,positive=True)
    _physical(parameter_errors,'parameter error multiplier')
    if (envelope not in ('central','lower','upper') or
            type(charge) not in (int,float) or not math.isfinite(charge) or
            parent_mass <= recoil_mass):
        raise ValueError('Physical current inputs and explicit envelope choice required')
    if mass >= parent_mass-recoil_mass:
        return 0.
    q2 = mass**2
    momentum = two_body_momentum(parent_mass,recoil_mass,mass)
    photon = two_body_momentum(parent_mass,recoil_mass,0.)
    values = {}
    for name in ('E1','M2_scaled','C1'):
        fit = model['form_factors'][name]
        center,slope = fit['CF'],fit['DF_gev_minus2']
        dc,ds = parameter_errors*fit['CF_error'],parameter_errors*fit['DF_error_gev_minus2']
        if not all(type(x) in (int,float) and math.isfinite(x) for x in (center,slope,dc,ds)) or dc < 0 or ds < 0:
            raise ValueError('Finite fit parameters and nonnegative errors required')
        if envelope == 'central':
            value = abs(center*(1+slope*q2))
        else:
            products = [(center+c)*(1+(slope+d)*q2) for c in (-dc,dc) for d in (-ds,ds)]
            if envelope == 'upper':
                value = max(abs(x) for x in products)
            else:
                value = 0. if min(products) <= 0 <= max(products) else min(abs(x) for x in products)
        if name == 'M2_scaled':
            value *= (momentum/photon)**2
        values[name] = value
    transverse = values['E1']**2+values['M2_scaled']**2
    spin_sum = q2*values['C1']**2+2*parent_mass**2*transverse
    return alpha_em*charge**2*(2*parent_mass*momentum)*spin_sum/(12*parent_mass**3)


def hc_lattice_form_factors(parent_mass, recoil_mass, mass, model):
    """F1 and FL of arXiv:2604.15256v2 Eqs9,11,55 and TableVII.

    The expanded FL polynomial removes the removable q2=0 cancellation.
    Parameters share one normalization, as in the primary joint fit.
    """
    _domain(model, 'h-c-lattice-f1-f2', mass)
    for value,name in ((parent_mass,'parent mass'),(recoil_mass,'recoil mass')):
        _physical(value,name,positive=True)
    if parent_mass <= recoil_mass:
        raise ValueError('Open h_c radiative calibration required')
    fit=model['form_factors']
    c,d1,d2=(fit[key] for key in ('C','DF1_gev_minus2','DF2_gev_minus2'))
    if not all(type(value) in (int,float) and math.isfinite(value) for value in (c,d1,d2)):
        raise ValueError('Finite shared h_c form-factor fit required')
    q2=mass*mass;delta=parent_mass**2-recoil_mass**2;s=parent_mass**2+recoil_mass**2
    f1=c*(1+d1*q2)
    longitudinal=c/2*(delta*(d2-d1)-2*s/delta-1
        +q2*(1/delta-2*s*d2/delta-d1)+q2*q2*d2/delta)
    return f1,longitudinal


def hc_lattice_width(parent_mass, recoil_mass, mass, model, *, alpha_em, charge=2/3):
    """Gamma(h_c -> eta_c gamma*) before epsilon_em squared, in GeV.

    This is the connected lattice-current benchmark, not a physical omission
    bound; its photon-normalized electron ratio has measured tension.
    """
    f1,longitudinal=hc_lattice_form_factors(parent_mass,recoil_mass,mass,model)
    _physical(alpha_em,'alpha_em',positive=True)
    if type(charge) not in (int,float) or not math.isfinite(charge):
        raise ValueError('Finite charm charge required')
    k=two_body_momentum(parent_mass,recoil_mass,mass)
    return 2*alpha_em*charge**2*k/(3*parent_mass**2)*(
        mass**2*longitudinal**2+2*parent_mass**2*f1**2)


CHANNELS={
    'eta_c->gamma_Aprime': (22,0),
    'chi_c0->Jpsi_Aprime': (443,0),
    'chi_c1->Jpsi_Aprime': (443,1),
    'chi_c2->Jpsi_Aprime': (443,2),
    'h_c->eta_c_Aprime': (441,1),
    'psi_2S->eta_c_Aprime': (441,1),
}


def _channel(model):
    name=model.get('channel')
    if (model.get('decay_type')!='quarkonium-radiative' or name not in CHANNELS
            or model.get('parent_polarization')!='unpolarized'):
        raise ValueError('Explicit supported quarkonium channel and unpolarized parent required')
    recoil_pdg,spin=CHANNELS[name]
    if model.get('recoil_pdg_id')!=recoil_pdg:
        raise ValueError('Quarkonium recoil species differs from its current')
    recoil=_physical(model.get('recoil_mass_gev'),'recoil mass')
    if (recoil_pdg==22 and recoil!=0) or (recoil_pdg!=22 and recoil<=0):
        raise ValueError('On-shell photon or massive quarkonium recoil required')
    current=model.get('quarkonium_model')
    if not isinstance(current,dict) or not current.get('reference'):
        raise ValueError('Explicit attributed quarkonium current required')
    return name,recoil,current


def branching_coefficient(parent_mass, mass, model):
    """BR(parent -> recoil A') / epsilon_em^2, before BR(A' -> mumu).

    Supply exactly one radiative BR or total-width normalization for the
    lattice currents. The former retains the lattice shape while using the
    caller's explicitly identified real-photon calibration. The latter uses
    the absolute lattice normalization and requires alpha_em explicitly.
    """
    channel,recoil,current=_channel(model)
    _physical(parent_mass,'parent mass',positive=True);_physical(mass,'signal mass')
    if parent_mass<=recoil:
        raise ValueError('Open photon calibration required')
    if mass>=parent_mass-recoil:
        return 0.
    if channel=='eta_c->gamma_Aprime':
        br=_physical(model.get('br_gamma_gamma'),'two-photon branching')
        if br>1:raise ValueError('Physical two-photon branching required')
        ratio=2*(1-(mass/parent_mass)**2)**3*abs(eta_c_form_factor(mass,current))**2
    elif channel=='psi_2S->eta_c_Aprime':
        _domain(current,'constant-heavy-quark-m1',mass)
        br=_physical(model.get('br_p_gamma'),'photon branching')
        ratio=(two_body_momentum(parent_mass,recoil,mass)/
               two_body_momentum(parent_mass,recoil,0.))**3
    elif channel in ('chi_c1->Jpsi_Aprime','h_c->eta_c_Aprime'):
        width=chi1_lattice_width if channel.startswith('chi_c1') else hc_lattice_width
        have_br='br_p_gamma' in model;have_total='total_parent_width_gev' in model
        if have_br==have_total:
            raise ValueError('Choose one photon-BR or absolute total-width normalization')
        if have_total:
            total=_physical(model['total_parent_width_gev'],'total parent width',positive=True)
            alpha=_physical(model.get('alpha_em'),'alpha_em',positive=True)
            result=width(parent_mass,recoil,mass,current,alpha_em=alpha)/total
            if not math.isfinite(result) or result<0:raise ValueError('Invalid absolute quarkonium rate')
            return result
        br=_physical(model['br_p_gamma'],'photon branching')
        ratio=width(parent_mass,recoil,mass,current,alpha_em=1.)/width(
            parent_mass,recoil,0.,current,alpha_em=1.)
    else:
        br=_physical(model.get('br_p_gamma'),'photon branching')
        if channel.startswith('chi_c0'):
            _domain(current,'chi-c0-leading-heavy-spin-e1',mass)
            ratio=heavy_spin_e1_ratio(parent_mass,recoil,mass)
        else:
            ratio=effective_pole_ratio(parent_mass,recoil,mass,current)
    if br>1:raise ValueError('Physical radiative branching required')
    result=br*ratio
    if not math.isfinite(result) or result<0:raise ValueError('Invalid quarkonium branching coefficient')
    return result


def _minkowski(a,b):
    return a[0]*b[0]-dot(a[1:],b[1:])


_EPSILON_TERMS=tuple((indices,(-1)**sum(indices[i]>indices[j] for i in range(4) for j in range(i+1,4)))
                    for indices in permutations(range(4)))


def _epsilon(a,b,c,d):
    # Four lowered vectors contracted with epsilon^{0123}=+1.
    vectors=[(v[0],-v[1],-v[2],-v[3]) for v in (a,b,c,d)]
    return math.fsum(sign*math.prod(vectors[i][index] for i,index in enumerate(indices))
                     for indices,sign in _EPSILON_TERMS)


def chi1_current_amplitude(parent_mass,recoil_mass,mass,current,parent_polarization,
                          recoil_polarization,vector_polarization):
    """The covariant Eq8 current, with a common i*Qc factor removed.

    Vectors are in the parent rest frame, with A' along +z. Replacing the
    vector polarization by q tests the Ward identity independently of rates.
    The signed E1/M2 terms retain recoil-polarization interference.
    """
    _domain(current,'chi-c1-lattice-e1-m2-c1',mass)
    k=two_body_momentum(parent_mass,recoil_mass,mass)
    if k<=0:raise ValueError('Open current phase space required')
    k0=two_body_momentum(parent_mass,recoil_mass,0.)
    values={name:fit['CF']*(1+fit['DF_gev_minus2']*mass**2)
            for name,fit in current['form_factors'].items()}
    e1,m2,c1=values['E1'],values['M2_scaled']*(k/k0)**2,values['C1']
    omega=math.hypot(k,mass);energy=math.hypot(k,recoil_mass)
    p=(parent_mass,0.,0.,0.);recoil=(energy,0.,0.,-k)
    q=(omega,0.,0.,k);s=(parent_mass+energy,0.,0.,-k)
    eta,eps,vector=parent_polarization,recoil_polarization,vector_polarization
    a,b=_minkowski(eta,recoil),_minkowski(eps,p)
    delta=parent_mass**2-recoil_mass**2;lam=4*parent_mass**2*k*k
    value=(2*parent_mass**2*(e1+m2)*a*_epsilon(vector,eps,s,q)
        +2*parent_mass*recoil_mass*(e1-m2)*b*_epsilon(vector,eta,s,q)
        +c1*(-lam*_epsilon(vector,eta,eps,q)
            +(delta+mass**2)*a*_epsilon(vector,eps,s,q)
            +(delta-mass**2)*b*_epsilon(vector,eta,s,q)))
    return value/(math.sqrt(2)*lam)


@lru_cache(maxsize=128)
def _spin_amplitudes(parent_mass,recoil_mass,mass,channel,current_json):
    """A[parent spin][recoil Cartesian spin][A' Cartesian spin]."""
    current=json.loads(current_json);k=two_body_momentum(parent_mass,recoil_mass,mass)
    omega=math.hypot(k,mass)
    if channel=='eta_c->gamma_Aprime':
        return (((0.,1.,0.),(-1.,0.,0.)),)
    if channel=='psi_2S->eta_c_Aprime':
        return (((0.,1.,0.),),((-1.,0.,0.),),((0.,0.,0.),))
    if channel=='h_c->eta_c_Aprime':
        f1,longitudinal=hc_lattice_form_factors(parent_mass,recoil_mass,mass,current)
        return (((parent_mass*f1,0.,0.),),((0.,parent_mass*f1,0.),),((0.,0.,mass*longitudinal),))
    if channel=='chi_c1->Jpsi_Aprime':
        parents=((0.,1.,0.,0.),(0.,0.,1.,0.),(0.,0.,0.,1.))
        recoils=parents[:2]+((-k/recoil_mass,0.,0.,math.hypot(k,recoil_mass)/recoil_mass),)
        vectors=parents[:2]+((k/mass,0.,0.,omega/mass),)
        return tuple(tuple(tuple(chi1_current_amplitude(parent_mass,recoil_mass,mass,current,p,r,a)
                                 for a in vectors) for r in recoils) for p in parents)
    # Leading heavy-spin E1 tensors from the v_nu F^{mu nu} coupling.
    # Recoil spin is defined in its own rest frame; recoil corrections are
    # deliberately absent from this declared approximation.
    if channel=='chi_c0->Jpsi_Aprime':
        tensors=(((1/math.sqrt(3),0.,0.),(0.,1/math.sqrt(3),0.),(0.,0.,1/math.sqrt(3))),)
    else:
        u=1/math.sqrt(2);v=1/math.sqrt(6)
        tensors=(((u,0.,0.),(0.,-u,0.),(0.,0.,0.)),((v,0.,0.),(0.,v,0.),(0.,0.,-2*v)),
                 ((0.,u,0.),(u,0.,0.),(0.,0.,0.)),((0.,0.,u),(0.,0.,0.),(u,0.,0.)),
                 ((0.,0.,0.),(0.,0.,u),(0.,u,0.)))
    fields=(omega,omega,mass)
    return tuple(tuple(tuple(tensor[b][a]*fields[a] for a in range(3)) for b in range(3)) for tensor in tensors)


def _validated_spin_amplitudes(parent_mass,mass,model):
    channel,recoil,current=_channel(model)
    # Rate-only fits do not measure a helicity density. Require the production
    # to name its separate angular approximation explicitly.
    if channel=='chi_c2->Jpsi_Aprime' and model.get('angular_model')!='leading-heavy-spin-e1':
        raise ValueError('chi_c2 effective rate needs an explicit leading-heavy-spin-e1 angular approximation')
    branching_coefficient(parent_mass,mass,model)
    return _spin_amplitudes(parent_mass,recoil,mass,channel,json.dumps(current,sort_keys=True))


def mean_lab_beta_gamma(parent,mass,model):
    """Exact two-body angular mean for the explicitly unpolarized parent."""
    from dark_photon_vector_meson import mean_lab_beta_gamma as two_body_mean
    _,recoil,_=_channel(model)
    return two_body_mean(parent,recoil,mass,polarization='unpolarized')


def sample_decay(parent,mass,proper_ctau_mm,rng,*,model):
    """Unconditional parent -> recoil A' -> recoil mu+mu- with spin transfer.

    Recoil density is conditional on the generated A' muons and expressed in
    the returned covariant polarization basis, not a lab-helicity guess.
    No parent lifetime, source normalization or physical epsilon is resampled.
    """
    if len(parent)!=4 or not all(math.isfinite(x) for x in parent) or parent[0]<=0:
        raise ValueError('Physical parent four-vector required')
    squared=math.fsum((parent[0]*parent[0],*(-value*value for value in parent[1:])))
    if squared<=0:raise ValueError('Timelike parent required')
    M=math.sqrt(squared);channel,recoil_mass,_=_channel(model)
    _physical(proper_ctau_mm,'proper lifetime',positive=True)
    k=two_body_momentum(M,recoil_mass,mass)
    if k<=0 or mass<=2*MUON_MASS_GEV:raise ValueError('Open dimuon/quarkonium decay required')
    amplitudes=_validated_spin_amplitudes(M,mass,model)
    recoil_dimension=len(amplitudes[0])
    weights=[math.fsum(amplitudes[r][b][a]**2 for r in range(len(amplitudes))
                       for b in range(recoil_dimension)) for a in range(3)]
    total=math.fsum(weights)
    if not math.isfinite(total) or total<=0:raise ValueError('Nonzero finite spin current required')
    cosine,phi=2*rng.random()-1,2*math.pi*rng.random();sine=math.sqrt(1-cosine*cosine)
    axis=(sine*math.cos(phi),sine*math.sin(phi),cosine)
    auxiliary=(0.,0.,1.) if abs(cosine)<.9 else (1.,0.,0.)
    cross=(axis[1]*auxiliary[2]-axis[2]*auxiliary[1],axis[2]*auxiliary[0]-axis[0]*auxiliary[2],axis[0]*auxiliary[1]-axis[1]*auxiliary[0])
    norm=math.sqrt(dot(cross,cross));first=tuple(value/norm for value in cross)
    second=(axis[1]*first[2]-axis[2]*first[1],axis[2]*first[0]-axis[0]*first[2],axis[0]*first[1]-axis[1]*first[0])
    beta2=1-4*(MUON_MASS_GEV/mass)**2
    while True:
        angle,azimuth=2*rng.random()-1,2*math.pi*rng.random();sine=math.sqrt(1-angle*angle)
        local=(sine*math.cos(azimuth),sine*math.sin(azimuth),angle)
        density=math.fsum(weight*(1-beta2*component*component) for weight,component in zip(weights,local))
        if rng.random()*total<=density:break
    direction=tuple(local[0]*u+local[1]*v+local[2]*n for u,v,n in zip(first,second,axis))
    lepton_k=math.sqrt((mass/2-MUON_MASS_GEV)*(mass/2+MUON_MASS_GEV))
    muon=(mass/2,)+tuple(lepton_k*x for x in direction)
    antimuon=(mass/2,)+tuple(-lepton_k*x for x in direction)
    omega,energy=math.hypot(k,mass),math.hypot(k,recoil_mass)
    parent_beta=tuple(x/parent[0] for x in parent[1:]);dark_beta=tuple(k*n/omega for n in axis)
    dark=boost((omega,)+tuple(k*n for n in axis),parent_beta)
    recoil=boost((energy,)+tuple(-k*n for n in axis),parent_beta)
    muon,antimuon=[boost(boost(v,dark_beta),parent_beta) for v in (muon,antimuon)]
    lepton=[[float(a==b)-beta2*local[a]*local[b] for b in range(3)] for a in range(3)]
    density_matrix=[[math.fsum(amplitudes[r][b][a]*amplitudes[r][c][d]*lepton[a][d]
        for r in range(len(amplitudes)) for a in range(3) for d in range(3))
        for c in range(recoil_dimension)] for b in range(recoil_dimension)]
    trace=math.fsum(density_matrix[b][b] for b in range(recoil_dimension))
    density_matrix=[[value/trace for value in row] for row in density_matrix]
    basis=[(0.,)+first,(0.,)+second]
    if recoil_dimension==3:basis.append((-k/recoil_mass,)+tuple(energy*n/recoil_mass for n in axis))
    recoil_basis=[boost(vector,parent_beta) for vector in basis] if recoil_dimension>1 else []
    proper=-proper_ctau_mm*math.log1p(-rng.random())
    result=dict(dark_photon_p4=dark,recoil_p4=recoil,muon_p4=muon,antimuon_p4=antimuon,
        dark_photon_mass_gev=mass,recoil_mass_gev=recoil_mass,muon_mass_gev=MUON_MASS_GEV,
        recoil_pdg_id=model['recoil_pdg_id'],proper_decay_length_mm=proper,
        decay_displacement_mm=tuple(proper*p/mass for p in dark[1:]),decay_ct_mm=proper*dark[0]/mass,
        helicity_cosine=angle,longitudinal_fraction=weights[2]/total,parent_polarization='unpolarized',
        recoil_spin_density_matrix=[[[value,0.] for value in row] for row in density_matrix] if recoil_dimension>1 else None,
        recoil_spin_density_complex_layout='rho[row][column]=[real,imaginary]',
        recoil_polarization_basis_p4=recoil_basis,recoil_spin_density_frame='transported Cartesian recoil-rest basis',
        recoil_spin_correlations_required=model['recoil_pdg_id']==443,
        recoil_natural_decay_required=model['recoil_pdg_id']!=22,unconditional_decay=True,
        current_reference=model['quarkonium_model']['reference'])
    if channel=='eta_c->gamma_Aprime':result['photon_p4']=recoil
    return result
