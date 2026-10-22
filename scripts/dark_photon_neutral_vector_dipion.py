# Code style: AI-code. AI-generated coherent neutral-vector dipion rate kernel.
"""Rate-only V -> pi0 pi0 A' in a declared gauged leading model.

The real-photon authority is Bramon et al., hep-ph/0105179, Eqs4--18,21--22.
The charged scalar is minimally coupled to both vector sources. Two triangle
orientations AND the mixed vector seagull give a finite loop. Both transverse
tensors are retained at nonzero A' mass; the virtual coefficient is calculated
from that Lagrangian, never inferred from a real-photon branching fraction.

For all-outgoing p=-P,q=Q, z=1-x-y and dipion invariant s, the denominator is
Delta=m_loop^2-x*z*P^2-y*z*Q^2-x*y*s-i0. After factoring i/(8*pi^2), the
triangle nonmetric part is -integral a^mu b^nu/Delta, where
a=(1-2x)p+2yq, b=-2xp+(2y-1)q. The metric term, including the seagull, is
G=integral_0^1 log[m_loop^2-x(1-x)s-i0] dx-2 integral_triangle log Delta.
In decay conventions the two coefficients are F1=4 integral xy/Delta and
Q^2 F2=-2 integral x(1-2x)/Delta. Thus G=F1(P.Q)+F2 P^2 Q^2.
These independent forms permit actual Ward-cancellation controls.

The scalar rescattering function is a SINGLE ChPT or linear-sigma-model
amplitude; an extra sigma-pole rate must not be added. Symmetrized local VVP
exchange and external/internal rho-omega mixing are coherent with the loop.
Local timelike vertices, the selected rescattering model and its pole/width
prescription are assumptions. Additional derivative currents, counterterms
and higher chiral orders remain unknown. Model comparisons are not universal
rate bounds. No sampler, production adapter, source weights or transport are
provided. All rates exclude BR(A' -> mu+mu-).

Four-vectors are (E,px,py,pz), GeV; the metric is (+---). Identical pi0s
receive one explicit 1/2! phase-space factor.
"""
import cmath
from functools import lru_cache
import math

from dark_photon_dipion_meson import _gauss_legendre
from dark_photon_double_radiative import _dot, _vertex, polarizations
from dark_photon_meson import boost
from dark_photon_vector_meson import two_body_momentum

REFERENCE='https://arxiv.org/pdf/hep-ph/0105179'


def _positive(value,name,zero=False):
    if (type(value) not in (int,float) or not math.isfinite(value) or
            (value<0 if zero else value<=0)):
        raise ValueError('Explicit finite physical '+name+' required')
    return float(value)


def real_photon_loop(parent_squared,pair_squared,loop_mass):
    """Independent primary Eq5 I(a,b), with its physical absorptive sign."""
    a,b=parent_squared/loop_mass**2,pair_squared/loop_mass**2
    if min(a,b)<=0 or abs(a-b)<1.e-6*max(a,b):
        raise ValueError('Use the integral representation near the soft/equal-invariant limit')
    def functions(z):
        if z>=.25:
            angle=math.asin(1/(2*math.sqrt(z)))
            return -angle**2,math.sqrt(max(0.,4*z-1))*angle
        root=math.sqrt(1-4*z)
        logarithm=math.log((1+root)/(1-root))-1j*math.pi
        return logarithm**2/4,root*logarithm/2
    fa,ga=functions(1/a);fb,gb=functions(1/b)
    return 1/(2*(a-b))-2*(fb-fa)/(a-b)**2+a*(gb-ga)/(a-b)**2


def _complex_log1p(value):
    if abs(value)>=.125:return cmath.log(1+value)
    total=0j;power=value
    for n in range(1,40):
        term=power/n
        total+=term
        if abs(term)<2.e-17*abs(total):break
        power*=-value
    return total


def _inner_moments(x,parent_squared,vector_squared,pair_squared,loop_squared):
    """Analytic y integrals of 1,y,y² and log Delta on [0,1-x].

    A negative infinitesimal only chooses the Feynman sheet; it is not a
    numerical width or a physical regulator fitted to the result.
    """
    length=1-x;A=vector_squared
    B=x*(parent_squared+vector_squared-pair_squared)-vector_squared
    C=complex(loop_squared-x*length*parent_squared,-1.e-30*max(loop_squared,parent_squared))
    if A==0:
        if B==0:
            return length/C,length**2/(2*C),length**3/(3*C),length*cmath.log(C)
        first=_complex_log1p(B*length/C)/B
        second=(length-C*first)/B
        third=(length**2/2-C*second)/B
    else:
        discriminant=cmath.sqrt(B*B-4*A*C)
        # Compute the small quadratic root from the product, and use log1p
        # when the other root is far away. This matters in the Q²->0 check.
        if B>=0:
            lower=(-B-discriminant)/(2*A);upper=C/(A*lower)
        else:
            upper=(-B+discriminant)/(2*A);lower=C/(A*upper)
        first=(_complex_log1p(-length/upper)-_complex_log1p(-length/lower))/discriminant
        second=(_complex_log1p((A*length**2+B*length)/C)-B*first)/(2*A)
        third=(length-B*second-C*first)/A
    logarithm=length*cmath.log(A*length**2+B*length+C)-2*A*third-B*second
    return first,second,third,logarithm


def _threshold_panels(parent_squared,vector_squared,pair_squared,loop_squared):
    edges={0.,1.}
    for invariant in (parent_squared,pair_squared):
        if invariant>4*loop_squared:
            root=math.sqrt(1-4*loop_squared/invariant)
            edges.update(((1-root)/2,(1+root)/2))
    if vector_squared:
        a=(parent_squared+vector_squared-pair_squared)**2-4*parent_squared*vector_squared
        b=2*vector_squared*(parent_squared-vector_squared+pair_squared)
        c=vector_squared**2-4*vector_squared*loop_squared
        discriminant=b*b-4*a*c
        if a!=0 and discriminant>=0:
            for root in ((-b-math.sqrt(discriminant))/(2*a),(-b+math.sqrt(discriminant))/(2*a)):
                if 0<root<1:edges.add(root)
    values=sorted(edges)
    return list(zip(values,values[1:]))


@lru_cache(maxsize=4096)
def _loop_integrals(parent_squared,vector_squared,pair_squared,loop_squared,order):
    sums=[0j]*6
    for low,high in _threshold_panels(parent_squared,vector_squared,pair_squared,loop_squared):
        for node,weight in _gauss_legendre(order):
            # Endpoint square-root/log branch points are integrable. This
            # transformation resolves both endpoints without evaluating one.
            angle=(node+1)*math.pi/4
            x=low+(high-low)*math.sin(angle)**2
            jacobian=(high-low)*math.sin(2*angle)*math.pi/4
            i0,i1,i2,logarithm=_inner_moments(x,parent_squared,vector_squared,pair_squared,loop_squared)
            bubble=cmath.log(complex(loop_squared-x*(1-x)*pair_squared,
                -1.e-30*max(loop_squared,parent_squared)))
            terms=(x*i1,x*(1-2*x)*i0,i1-2*i2,(1-2*x)*(2*i1-i0),logarithm,bubble)
            for i,value in enumerate(terms):sums[i]+=weight*jacobian*value
    return tuple(sums)


def scalar_loop_form_factors(parent_squared,vector_squared,pair_squared,loop_mass,*,order=96):
    """Both massive-current coefficients and independent seagull audit terms.

    F2 is returned only for Q²>0. At Q²=0 its tensor does not contribute;
    returning None makes that representation choice explicit. It is NOT an
    assumption that the massive coefficient vanishes. No spurious Q^-2 pole
    occurs: with y=(1-x)u and x<->1-x, its finite integral is
    -int dxdu x(1-x)(1-2x)^2 u(1-u)/[Delta(x,u)Delta(1-x,u)].
    Compare orders for every new threshold/domain; no rigorous quadrature
    error is claimed from a single fixed-order evaluation.
    """
    _positive(parent_squared,'parent invariant');_positive(pair_squared,'pair invariant')
    _positive(vector_squared,'vector invariant',zero=True);_positive(loop_mass,'loop mass')
    if type(order) is not int or not 8<=order<=384:
        raise ValueError('Explicit loop quadrature order between8 and384 required')
    if math.sqrt(pair_squared)+math.sqrt(vector_squared)>=math.sqrt(parent_squared):
        raise ValueError('Open decay invariants required')
    xy,xx,yy,pq,logarithm,bubble=_loop_integrals(
        parent_squared,vector_squared,pair_squared,loop_mass**2,order)
    return dict(f1=4*xy,f2=-2*xx/vector_squared if vector_squared else None,
        f2_times_q2=-2*xx,metric_from_triangle_and_seagull=bubble-2*logarithm,
        all_outgoing_pp_coefficient=2*xx,all_outgoing_qq_coefficient=2*yy,
        all_outgoing_pq_coefficient=-pq)


def loop_polarized_amplitude(parent,dark,parent_polarization,dark_polarization,factors):
    """Tensor contraction before -e*g_vector*A_rescattering/(8*pi²)."""
    P2,Q2=_dot(parent,parent),_dot(dark,dark)
    product=_dot(parent,dark);ep,eq=parent_polarization,dark_polarization
    first=product*_dot(ep,eq)-_dot(ep,dark)*_dot(eq,parent)
    second=(Q2*P2*_dot(ep,eq)-Q2*_dot(ep,parent)*_dot(eq,parent)
        -P2*_dot(ep,dark)*_dot(eq,dark)+product*_dot(ep,parent)*_dot(eq,dark))
    return factors['f1']*first+(factors['f2']*second if factors['f2'] is not None else 0.)


def rescattering_amplitude(pair_squared,model):
    """One on-shell-factorized scalar amplitude, with its Adler zero intact."""
    f=_positive(model['fpi_gev'],'pion decay constant')
    pion=_positive(model['pion_mass_gev'],'rescattering pion mass')
    if model['kind']=='chpt-pion-leading':return (pair_squared-pion**2)/f**2
    if model['kind']=='chpt-kaon-to-neutral-pions-leading':
        return pair_squared/(4*f*_positive(model['fkaon_gev'],'kaon decay constant'))
    if model['kind']!='linear-sigma-pion':raise ValueError('Explicit qualified scalar rescattering prescription required')
    sigma=_positive(model['sigma_mass_gev'],'sigma mass');width=_positive(model['sigma_width_gev'],'sigma width')
    if model.get('width_prescription')!='constant-primary-2001':
        raise ValueError('Keep the declared primary scalar propagator convention')
    # This is Eq11 at zero scalar mixing. The constant and pole terms form
    # ONE amplitude; adding a separate sigma diagram would double count it.
    return (pair_squared-pion**2)/f**2*(pion**2-sigma**2)/complex(pair_squared-sigma**2,sigma*width)


def rest_kinematics(parent_mass,pion_mass,mass,pair_squared,cosine):
    if not -1<=cosine<=1 or not 4*pion_mass**2<pair_squared<(parent_mass-mass)**2:
        raise ValueError('Open neutral dipion phase space required')
    pair=math.sqrt(pair_squared);outer=two_body_momentum(parent_mass,pair,mass)
    inner=math.sqrt(pair_squared/4-pion_mass**2);energy=math.hypot(outer,pair)
    first=boost((pair/2,inner*math.sqrt(1-cosine*cosine),0.,inner*cosine),(0.,0.,-outer/energy))
    second=boost((pair/2,-inner*math.sqrt(1-cosine*cosine),0.,-inner*cosine),(0.,0.,-outer/energy))
    return (parent_mass,0.,0.,0.),(math.hypot(outer,mass),0.,0.,outer),first,second


def reference_model(inputs,parent_pdg_id,*,scalar_model,include_kaon_loop=True,include_mixing=True):
    """Explicit SU(3) local benchmark; no photon BR normalization or source.

    Scalar choices reproduce the published photon comparisons. They do not
    span unknown timelike operators. Kaon rescattering here is its declared
    leading ChPT term; scalar SU(3) breaking in that small term remains open.
    """
    if (inputs.get('schema')!='shift-neutral-vector-dipion-leading-reference-v1' or
            parent_pdg_id not in (113,223) or scalar_model not in inputs['scalar_scenarios'] or
            type(include_kaon_loop) is not bool or type(include_mixing) is not bool):
        raise ValueError('Explicit maintained reference, parent and scalar scenario required')
    common=inputs['conventions'];g=common['g_primary'];f=common['fpi_gev']
    e=math.sqrt(4*math.pi*common['alpha_em']);G=3*g*g/(4*math.pi**2*f)
    coefficient=G*G*e/(math.sqrt(2)*g)
    rho=dict(inputs['rho']);omega=dict(inputs['omega'])
    scalar=dict(inputs['scalar_scenarios'][scalar_model],fpi_gev=f,pion_mass_gev=common['charged_pion_mass_gev'])
    kaon=dict(mass_gev=common['charged_kaon_mass_gev'],vector_coupling=g/math.sqrt(2),
        rescattering=dict(kind='chpt-kaon-to-neutral-pions-leading',fpi_gev=f,
            fkaon_gev=common['fkaon_gev'],pion_mass_gev=common['charged_pion_mass_gev']))
    base=dict(kind='gauged-neutral-vector-dipion-leading',reference=inputs['publications']['primary_loop_scalar_vmd'],
        virtual_vertex_prescription=inputs['virtual_vertex_prescription'],
        on_shell_rescattering_factorization=True,physical_production_ready=False,
        alpha_em=common['alpha_em'])
    rho_model=dict(base,vector_exchanges=[dict(omega,coupling_product_gev_inverse2=coefficient)],
        charged_scalar_loops=[dict(mass_gev=common['charged_pion_mass_gev'],
            vector_coupling=math.sqrt(2)*g,rescattering=scalar)]+([dict(kaon)] if include_kaon_loop else []))
    if parent_pdg_id==113:return rho_model
    exchange=dict(rho,coupling_product_gev_inverse2=coefficient/3)
    result=dict(base,vector_exchanges=[exchange],charged_scalar_loops=[dict(kaon)] if include_kaon_loop else [])
    if include_mixing:
        theta=inputs['mixing_mass_squared_gev2']
        exchange.update(mixing_partner=dict(omega),mixing_coupling_ratio=3.,mixing_mass_squared_gev2=theta)
        denominator=complex(omega['mass_gev']**2-rho['mass_gev']**2,
            -(omega['mass_gev']*omega['width_gev']-rho['mass_gev']*rho['width_gev']))
        mixing=theta/denominator
        result['external_mixing']=dict(coefficient=[mixing.real,mixing.imag],partner_model=rho_model)
    return result


def _vector_propagator(invariant,row):
    mass,width=row['mass_gev'],row['width_gev']
    if row['width_prescription']=='constant-primary-2001':pass
    elif row['width_prescription']=='rho-pipi-running-primary-2003':
        threshold=4*row['pion_mass_gev']**2
        width*=mass/math.sqrt(invariant)*((invariant-threshold)/(mass**2-threshold))**1.5 if invariant>threshold else 0.
    else:raise ValueError('Explicit vector width prescription required')
    return 1/complex(mass**2-invariant,-mass*width)


def sequential_amplitude(parent,dark,first,second,ep,eq,rows):
    """Two local epsilon-tensor VVP vertices, with Bose exchange coherent."""
    result=0j
    for pion in (first,second):
        internal=tuple(a+b for a,b in zip(dark,pion));invariant=_dot(internal,internal)
        tensor=-_dot(_vertex(ep,parent,internal),_vertex(eq,dark,internal))
        for row in rows:
            propagator=_vector_propagator(invariant,row)
            if 'mixing_partner' in row:
                partner=row['mixing_partner']
                propagator*=1+row['mixing_coupling_ratio']*row['mixing_mass_squared_gev2']*(-_vector_propagator(invariant,partner))
            result+=row['coupling_product_gev_inverse2']*propagator*tensor
    return result


def amplitude_components(parent,dark,first,second,ep,eq,*,model,loop_order=96):
    """Coherent components BEFORE the common EM dark-photon mixing factor."""
    if (model.get('kind')!='gauged-neutral-vector-dipion-leading' or
            model.get('virtual_vertex_prescription')!='constant-local-lagrangian' or
            model.get('on_shell_rescattering_factorization') is not True or
            not model.get('reference') or model.get('physical_production_ready') is not False):
        raise ValueError('Explicit rate-only leading model and its off-shell assumptions required')
    pair=tuple(a+b for a,b in zip(first,second));s=_dot(pair,pair)
    P2,Q2=_dot(parent,parent),_dot(dark,dark)
    # Dark vector mass is an input physical invariant, not a rounded sqrt.
    if abs(Q2)<64*math.ulp(max(abs(x) for x in dark)**2):Q2=0.
    e=math.sqrt(4*math.pi*model['alpha_em'])
    vector=sequential_amplitude(parent,dark,first,second,ep,eq,model['vector_exchanges'])
    loop=0j
    for row in model['charged_scalar_loops']:
        factors=scalar_loop_form_factors(P2,Q2,s,row['mass_gev'],order=loop_order)
        loop-=e*row['vector_coupling']*rescattering_amplitude(s,row['rescattering'])/(8*math.pi**2)*loop_polarized_amplitude(parent,dark,ep,eq,factors)
    mixing=0j
    if model.get('external_mixing'):
        row=model['external_mixing'];other=row['partner_model']
        amplitudes=amplitude_components(parent,dark,first,second,ep,eq,model=other,loop_order=loop_order)
        mixing=complex(*row['coefficient'])*amplitudes['total']
    return dict(vector=vector,loop=loop,mixing=mixing,total=vector+loop+mixing)


def differential_width(parent_mass,pion_mass,mass,pair_squared,cosine,*,model,loop_order=96):
    """dGamma/(ds dcos theta), GeV^-1, with 1/2! for the two neutral pions."""
    parent,dark,first,second=rest_kinematics(parent_mass,pion_mass,mass,pair_squared,cosine)
    sums=dict(total=0.,vector=0.,loop=0.,mixing=0.,interference=0.)
    for ep in polarizations(parent,parent_mass):
        for eq in polarizations(dark,mass):
            amplitudes=amplitude_components(parent,dark,first,second,ep,eq,model=model,loop_order=loop_order)
            for name in ('total','vector','loop','mixing'):sums[name]+=abs(amplitudes[name])**2/3
    sums['interference']=sums['total']-sums['vector']-sums['loop']-sums['mixing']
    outer=two_body_momentum(parent_mass,math.sqrt(pair_squared),mass)
    inner=math.sqrt(pair_squared/4-pion_mass**2)
    phase=outer*inner/(256*math.pi**3*parent_mass**2*math.sqrt(pair_squared))
    return {name:value*phase for name,value in sums.items()}


def decay_width(parent_mass,pion_mass,mass,*,model,epsilon_em,order=24,loop_order=96):
    """Rates in GeV; exact EM mixing multiplies once, dimuon BR is excluded."""
    for value,name in ((parent_mass,'parent mass'),(pion_mass,'pion mass')):_positive(value,name)
    _positive(mass,'dark-vector mass',zero=True);_positive(epsilon_em,'EM mixing',zero=True)
    if type(order) is not int or not 8<=order<=96:raise ValueError('Explicit phase quadrature order between8 and96 required')
    sums=dict(total=0.,vector=0.,loop=0.,mixing=0.,interference=0.)
    if mass>=parent_mass-2*pion_mass:return sums
    edges={4*pion_mass**2,(parent_mass-mass)**2}
    models=[model]
    if model.get('external_mixing'):models.append(model['external_mixing']['partner_model'])
    for current in models:
        for row in current['charged_scalar_loops']:
            if min(edges)<4*row['mass_gev']**2<max(edges):edges.add(4*row['mass_gev']**2)
    points=sorted(edges)
    for low,high in zip(points,points[1:]):
        for node,weight in _gauss_legendre(order):
            s=(low+high)/2+(high-low)*node/2
            for cosine,angular_weight in _gauss_legendre(order):
                row=differential_width(parent_mass,pion_mass,mass,s,cosine,model=model,loop_order=loop_order)
                for name in sums:sums[name]+=(high-low)/2*weight*angular_weight*row[name]*epsilon_em**2
    return sums
