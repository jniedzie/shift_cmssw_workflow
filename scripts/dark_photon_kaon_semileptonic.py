# Code style: AI-code. AI-generated semileptonic-kaon current qualification.
"""K -> pi l nu A' currents, with explicitly separate chiral orders.

The neutral off-shell authority is Tsuji and Sato, arXiv:0708.1576v2,
Eqs14--33 and Appendices A/B. Its real-photon limit is Bijnens, Ecker and
Gasser, hep-ph/9209261 Sec5. A real-photon form factor is not a complete
timelike current. In particular, gauging a measured Kl3 form factor does
not determine transverse structure-dependent terms.

This module is under physics qualification. No source/production integration
is implied by the existence of a leading-order control or a loop backend.
All four-vectors are (E,px,py,pz), GeV, with metric (+---).
"""
import ctypes
import hashlib
import json
import math
from pathlib import Path
import subprocess
import threading
from functools import lru_cache

import numpy as np

from dark_photon_kaon_leptonic import (EPSILON,GAMMA,GAMMA5,IDENTITY,METRIC,
    dot,slash,polarization_basis,equal_mass_h_over_q2)
from dark_photon_meson import MUON_MASS_GEV,boost
from dark_photon_kaon_leptonic import (sample_vector_muon_direction,
    _isotropic_rotation,_boost_from_parent_rest)
from dark_photon_vector_meson import two_body_momentum


REFERENCE='https://arxiv.org/pdf/0708.1576v2'
CHIRAL_LEFT=IDENTITY-GAMMA5
LOOP_FACTOR=16*math.pi**2


COLLIER_WRAPPER='''! Code style: AI-code. AI-generated bounded Collier A/B/C bridge.
subroutine shift_kaon_loop_v2(kind,args,scale2,delta,mode,values,err,acc) bind(C)
 use iso_c_binding
 use collier
 implicit none
 integer(c_int),value :: kind,mode
 real(c_double),intent(in) :: args(6)
 real(c_double),value :: scale2,delta
 real(c_double),intent(out) :: values(24)
 integer(c_int),intent(out) :: err,acc
 complex(c_double_complex) :: a(0:0),auv(0:0),b(0:1,0:2),buv(0:1,0:2)
 complex(c_double_complex) :: c(0:1,0:3,0:3),cuv(0:1,0:3,0:3),out(12)
 complex(c_double_complex) :: z(6)
 logical,save :: initialized=.false.
 integer :: i
 if (.not.initialized) then
   call Init_cll(3,3,'')
   call SwitchOffErrStop_cll()
   call SetReqAcc_cll(1d-8)
   initialized=.true.
 endif
 call InitEvent_cll()
 call SetMuUV2_cll(scale2)
 call SetDeltaUV_cll(delta)
 call SetMode_cll(mode)
 z=cmplx(args,0d0,kind=c_double_complex)
 out=0d0
 select case(kind)
 case(1)
   call A_cll(a,auv,z(1),0)
   out(1)=a(0)
 case(2)
   call B_cll(b,buv,z(1),z(2),z(3),2)
   out(1)=b(0,0)
   out(2)=-b(0,1)
   out(3)=b(0,2)
   out(4)=b(1,0)
 case(3)
   call C_cll(c,cuv,z(1),z(2),z(3),z(4),z(5),z(6),3)
   out(1)=c(0,0,0)
   out(2)=-c(0,1,0)
   out(3)=-c(0,0,1)
   out(4)=c(1,0,0)
   out(5)=c(0,2,0)
   out(6)=c(0,1,1)
   out(7)=c(0,0,2)
   out(8)=-c(1,1,0)
   out(9)=-c(1,0,1)
   out(10)=-c(0,2,1)
   out(11)=-c(0,1,2)
   out(12)=-c(0,0,3)
 case default
   err=-10
   acc=-2
   values=0d0
   return
 end select
 call GetErrFlag_cll(err)
 call GetAccFlag_cll(acc)
 do i=1,12
   values(2*i-1)=real(out(i),kind=c_double)
   values(2*i)=aimag(out(i))
 enddo
end subroutine
'''


def build_collier_adapter(directory,*,collier_base,compiler):
    """Explicit private wrapper build; never called by imports or source runs.

    The caller must resolve this release's Collier and Fortran compiler from
    SCRAM. This compiles only the small wrapper, against the unchanged library.
    Freeze and validate the resulting object separately before any production.
    """
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    base=Path(collier_base);compiler=Path(compiler)
    if not base.is_absolute() or not compiler.is_absolute():
        raise ValueError('Explicit absolute qualified Collier/compiler paths required')
    source=directory/'kaon_collier.f90';source.write_text(COLLIER_WRAPPER)
    library=directory/'libshift_kaon_collier.so'
    subprocess.run([str(compiler),'-shared','-fPIC','-O2','-I'+str(base/'include'),
        str(source),str(base/'lib/libcollier.a'),'-o',str(library)],check=True,
        cwd=directory,timeout=60,capture_output=True,text=True)
    return library


class LoopPrecisionWarning(ValueError):
    def __init__(self,record):
        self.record=record
        super().__init__('Unqualified Collier tensor precision: '+str(record))


class CollierLoops:
    """Primary Appendix-A convention, including GL lambda0 subtraction.

    Collier uses (k+p_i) denominators; the primary uses (k-p_i).
    Odd-rank coefficients therefore change sign. Divide its i*pi²
    normalization by16*pi². Setting delta_UV=-1 gives lambda0=0 in
    the primary AppendixB; an explicit nonzero lambda0 supports UV tests.
    The wrapper serializes calls to the Fortran library's global state.
    """
    _lock=threading.Lock()

    def __init__(self,library,*,scale_gev=.77,lambda0=0.,mode=3,_allow_precision_warning=False):
        self.path=Path(library).resolve()
        self.scale=float(scale_gev);self.lambda0=float(lambda0)
        self.mode=int(mode);self._allow_precision_warning=_allow_precision_warning
        self.precision_warnings=[]
        if self.mode not in (1,2,3):
            raise ValueError('Explicit COLI, DD or comparison mode required')
        if not self.path.is_file() or not math.isfinite(self.scale) or self.scale<=0 or not math.isfinite(self.lambda0):
            raise ValueError('Explicit qualified loop library and finite subtraction parameters required')
        self.library=ctypes.CDLL(str(self.path))
        try:
            self.function=self.library.shift_kaon_loop_v2
        except AttributeError as error:
            raise RuntimeError('Require the matching version2 private Collier wrapper; no automatic rebuild') from error
        self.function.argtypes=[ctypes.c_int,ctypes.POINTER(ctypes.c_double),ctypes.c_double,
            ctypes.c_double,ctypes.c_int,ctypes.POINTER(ctypes.c_double),ctypes.POINTER(ctypes.c_int),ctypes.POINTER(ctypes.c_int)]
        self.function.restype=None
        self.provenance=dict(library=str(self.path),sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
            scale_gev=self.scale,lambda0=self.lambda0,mode=self.mode,
            tensor_requested_relative_accuracy=1.e-8,reference='https://arxiv.org/pdf/1604.06792')

    @lru_cache(maxsize=4096)
    def _evaluate(self,kind,arguments,names):
        if any(not math.isfinite(float(value)) for value in arguments):
            raise ValueError('Finite loop arguments required')
        arguments=(ctypes.c_double*6)(*arguments)
        values=(ctypes.c_double*24)();error=ctypes.c_int();accuracy=ctypes.c_int()
        with self._lock:
            self.function(kind,arguments,self.scale**2,self.lambda0-1.,self.mode,values,
                ctypes.byref(error),ctypes.byref(accuracy))
        result={name:complex(values[2*i],values[2*i+1])/LOOP_FACTOR for i,name in enumerate(names)}
        return result,error.value,accuracy.value

    def _call(self,kind,arguments,names):
        result,error,accuracy=self._evaluate(kind,tuple(arguments),tuple(names))
        if error!=0 or accuracy<-1:
            raise ValueError('Unqualified Collier loop evaluation: '+str((error,accuracy)))
        if accuracy==-1:
            record=dict(mode=self.mode,kind=kind,error=error,accuracy=accuracy,
                arguments=list(map(float,arguments)),requested_relative_accuracy=1.e-8)
            if not self._allow_precision_warning:
                raise LoopPrecisionWarning(record)
            self.precision_warnings.append(record)
        if any(not math.isfinite(value.real) or not math.isfinite(value.imag) for value in result.values()):
            raise ValueError('Nonfinite loop coefficient')
        return result

    def a(self,mass2):
        return self._call(1,[mass2],['A'])['A']

    def b(self,mass1,mass2,invariant):
        return self._call(2,[invariant,mass1,mass2],['B','B1','B21','B22'])

    def c(self,mass1,mass2,mass3,q2,w2,total2):
        return self._call(3,[q2,w2,total2,mass1,mass2,mass3],
            ['C','C1','C2','C00','C11','C12','C22','C001','C002','C112','C122','C222'])


def _external_point(parent,pion,lepton,neutrino,dark):
    parent,pion,lepton,neutrino,dark=[np.asarray(v,dtype=float) for v in (parent,pion,lepton,neutrino,dark)]
    if any(v.shape!=(4,) or not np.all(np.isfinite(v)) or v[0]<=0 for v in (parent,pion,lepton,neutrino,dark)):
        raise ValueError('Finite future-directed external four-vectors required')
    if np.max(np.abs(parent-pion-lepton-neutrino-dark))>128*math.ulp(max(1.,parent[0])):
        raise ValueError('Conserved external four-momentum required')
    return parent,pion,lepton,neutrino,dark


def leading_current(parent,pion,lepton,neutrino,dark):
    """Exact O(p²) neutral current before -epsilon*e*GF*Vus/sqrt(2).

    q_mu J^mu vanishes as a Dirac matrix. The q^mu terms discarded against
    the conserved pair current in primaryEq14 are retained here explicitly.
    This control alone is not the full radiative semileptonic model.
    """
    parent,pion,lepton,neutrino,dark=_external_point(parent,pion,lepton,neutrino,dark)
    q2=dot(dark,dark);pion_denominator=2*dot(pion,dark)+q2
    lepton_denominator=2*dot(lepton,dark)+q2
    if q2<0 or min(pion_denominator,lepton_denominator)<=0:
        raise ValueError('Timelike vector and positive physical propagators required')
    h=slash(parent+pion);q=slash(dark)
    return np.array([GAMMA[mu]@CHIRAL_LEFT-
        (2*pion[mu]+dark[mu])/pion_denominator*(h+q)@CHIRAL_LEFT+
        h@CHIRAL_LEFT@(2*lepton[mu]*IDENTITY+q@GAMMA[mu])/lepton_denominator
        for mu in range(4)])


def leading_charged_current(parent,pion,lepton,neutrino,dark):
    """K+ -> pi0 l+ nu A' O(p²) control, including the 1/sqrt(2) Clebsch.

    Gauge the initial kaon and final positive lepton, with the weak contact.
    The kaon pole has denominator2K.q-q² and weak momentum K+pi-q;
    replacing a final charged pion by an initial kaon without changing those
    signs violates the Ward identity. At q²=0 this equals minus the complete
    Bijnens/Ecker/Gasser Eq5.1 with the Eq5.15 amplitudes (an irrelevant
    common phase). All q^mu terms and the massive-vector longitudinal state
    are retained. This is not the charged O(p4) structure-dependent current.
    """
    parent,pion,lepton,neutrino,dark=_external_point(parent,pion,lepton,neutrino,dark)
    q2=dot(dark,dark)
    kaon_denominator=2*dot(parent,dark)-q2
    lepton_denominator=2*dot(lepton,dark)+q2
    if q2<0 or min(kaon_denominator,lepton_denominator)<=0:
        raise ValueError('Timelike vector and positive physical propagators required')
    h=slash(parent+pion);q=slash(dark)
    return np.array([-GAMMA[mu]@CHIRAL_LEFT-
        (2*parent[mu]-dark[mu])/kaon_denominator*(h-q)@CHIRAL_LEFT+
        h@CHIRAL_LEFT@(2*lepton[mu]*IDENTITY+q@GAMMA[mu])/lepton_denominator
        for mu in range(4)])/math.sqrt(2)


def chiral_parameters(model,loops):
    """Strong one-loop input in a consistent LO-GMO mass/subtraction scheme."""
    if (model.get('kind')!='chpt-p4-neutral-kaon-semileptonic-vector'
            or model.get('loop_mass_scheme')!='LO-GMO'
            or model.get('reference')!=REFERENCE):
        raise ValueError('Explicit neutral O(p4) authority and LO-GMO scheme required')
    names=['parent_mass_gev','pion_mass_gev','fpi_gev','lec_scale_gev','l5','l9','l10']
    values={name:float(model[name]) for name in names}
    if not all(math.isfinite(value) for value in values.values()) or min(values[name] for name in names[:4])<=0:
        raise ValueError('Finite physical chiral parameters required')
    kaon2,pion2=values['parent_mass_gev']**2,values['pion_mass_gev']**2
    eta2=(4*kaon2-pion2)/3
    # L_i = L_i^r(mu) - Gamma_i*lambda0/(32*pi²), in the
    # primary's lambda0 convention. Keep the pole in UV controls.
    for name,gamma in [('l5',3/8),('l9',1/4),('l10',-1/4)]:
        values[name]+=gamma/LOOP_FACTOR*math.log(values['lec_scale_gev']/loops.scale)
        values[name]-=gamma*loops.lambda0/(2*LOOP_FACTOR)
    values.update(kaon2=kaon2,pion2=pion2,eta2=eta2)
    return values


def weak_vertex(r,l,model,loops):
    """Tsuji/Sato Eqs23--24, including the UV-determined B21 correction.

    The last K/eta B in printed Eq24 must be B21. Its uncontracted
    momentum-dependent UV pole otherwise cannot cancel. This correction
    is independently tested with lambda0 variation and the LO-GMO relation.
    """
    par=chiral_parameters(model,loops)
    k,p,e,f=par['kaon2'],par['pion2'],par['eta2'],par['fpi_gev']
    l5,l9=par['l5'],par['l9'];r2,l2,rl=[float(dot(a,b)) for a,b in [(r,r),(l,l),(r,l)]]
    ap,ak,ae=[loops.a(value) for value in (p,k,e)]
    pk,ke=loops.b(p,k,l2),loops.b(k,e,l2)
    g1=(2*l9*l2+3/8*(ae+ap+2*ak)-3/2*(pk['B22']+ke['B22']))/f**2
    g2=(4*(k-p)*l5-2*rl*l9+ae/2-5*ap/12+7*ak/12+
        pk['B']*(-p/4+k/6-5*r2/48+5*l2/16-3*rl/8)+
        ke['B']*(-p/6-k/12-r2/16+3*l2/16-3*rl/8)+
        pk['B1']*(p/2-k/3+5*r2/24-5*l2/24+3*rl/2)+
        ke['B1']*(p/3+k/6+r2/8-l2/8+3*rl/2)+
        pk['B21']*(-5*l2/6-3*rl/2)+ke['B21']*(-l2/2-3*rl/2)-
        5*pk['B22']/6-ke['B22']/2)/f**2
    return g1,g2


def neutral_em_corrections(q2,model,loops):
    par=chiral_parameters(model,loops)
    p,k,f,l9=par['pion2'],par['kaon2'],par['fpi_gev'],par['l9']
    pp,kk=loops.b(p,p,q2),loops.b(k,k,q2)
    return ((2*l9*q2+loops.a(p)+loops.a(k)/2-2*pp['B22']-kk['B22'])/f**2,
        ((loops.a(k)-loops.a(p))/2-kk['B22']+pp['B22'])/f**2)


def neutral_contact_tensors(parent,pion,dark,model,loops):
    """The six printed weak contact tensors, Eqs26--32, before1/Fpi²."""
    parent,pion,dark=[np.asarray(value,dtype=float) for value in (parent,pion,dark)]
    weak=parent-pion-dark;total=dark+weak
    q2,w2,Q2=[float(dot(value,value)) for value in (dark,weak,total)]
    par=chiral_parameters(model,loops)
    p,k,e,f=par['pion2'],par['kaon2'],par['eta2'],par['fpi_gev']
    l5,l9,l10=par['l5'],par['l9'],par['l10']
    ap,ak,ae=[loops.a(value) for value in (p,k,e)]
    pq,Qq,pQ,wQ=[float(dot(a,b)) for a,b in [(pion,dark),(total,dark),(pion,total),(weak,total)]]
    g=np.diag(METRIC);outer=np.outer
    ta=(-ae/8-11*ap/24-5*ak/12+4*(p-k)*l5+
        4*l9*(dot(weak,parent)-dot(dark,pion))+4*l10*dot(weak,dark))*g
    ta+=l9*(-4*outer(weak,weak)-8*outer(weak,pion)+8*outer(pion,dark)+
        4*outer(pion,weak)-4*outer(dark,pion))-4*(l9+l10)*outer(weak,dark)
    tb=(35*ap/12+ae/4+ak)*g
    tc=-10/3*loops.b(p,p,q2)['B22']*g
    kw,pw,kpw=loops.b(k,e,w2),loops.b(p,k,w2),loops.b(k,p,w2)
    td=(-ak/4-2*kw['B22']-4*pw['B22']/3)*g
    td+=(-2*kw['B21']+kpw['B']/2-kpw['B1']-4*pw['B21']/3)*outer(weak,weak)
    td+=(2*kw['B1']+4*pw['B1']/3)*outer(pion+2*weak,weak)
    td+=(-kw['B']/2-pw['B']/3)*outer(2*pion+3*weak,weak)
    kt,pt=loops.b(k,e,Q2),loops.b(p,k,Q2)
    te=(dot(3*pion+total,total)*kt['B1']/2-(pQ+(k+2*p)/6)*kt['B']+
        ak/4-ap/6+dot(parent/2+pion+total/3,total)*pt['B1']+
        ((p-Q2)/2-2*pQ/3)*pt['B'])*g
    routing=model.get('mixed_mass_b1_routing','printed-equation-30')
    if routing=='reversed-kaon-leg':
        # The half-K term arises from the opposite mixed-mass bubble
        # routing. B1(K,pi)=B0(pi,K)-B1(pi,K); B1 is not symmetric.
        # Exact symbolic reduction of the printed contact WTI leaves
        # K.Q*(B1(pi,K)-B0/2)*q^nu. This mass reversal cancels that
        # defect without a fitted counterterm and has zero UV pole.
        te-=dot(parent,total)*(pt['B1']-pt['B']/2)*g
    elif routing!='printed-equation-30':
        raise ValueError('Explicit mixed-mass weak-contact routing required')
    pieces=[]
    configs=[(k,k,e,dot(dark,6*pion+2*total),dot(total,6*pion+2*total),
              -4*pQ-2*(2*p+k)/3,6.),
             (k,k,p,2*dot(parent,dark),2*dot(parent,total),2*(p-Q2),2.),
             (p,p,k,dot(dark,6*pion+2*total),dot(total,6*pion+2*total),-4*pQ,6.)]
    for m1,m2,m3,a,b,c,d in configs:
        loop=loops.c(m1,m2,m3,q2,w2,Q2)
        c00,c001,c002,c112,c122,c222,c22,c12,c2=[loop[name] for name in
            ['C00','C001','C002','C112','C122','C222','C22','C12','C2']]
        tensor=-g*(a*c001+b*c002+c*c00)+outer(pion,dark)*d*(c00-c001-c002)
        tensor+=outer(weak,dark)*(-2*c001-4*c002-a*c112-(a+b)*c122-
            b*c222+(b-c)*c22+(a-c)*c12+2*c00+c*c2)
        tensor+=outer(weak,weak)*(-4*c002-a*c122-b*c222+(b/2-c)*c22+
            a*c12/2+c00+c*c2/2)
        tensor-=outer(weak,pion)*d*c002
        tensor+=outer(pion,weak)*d*(-c002+c00/2)
        pieces.append(tensor)
    tf=pieces[0]+pieces[1]+2/3*(g*loops.b(p,p,q2)['B22']+pieces[2])
    return dict(local=ta,tadpole=tb,pion_bubble=tc,weak_bubble=td,
        total_bubble=te,triangle=tf,total=(ta+tb+tc+td+te+tf)/f**2)


def current_operators(parent,pion,lepton,neutrino,dark,model,loops=None,*,order):
    """Neutral current components; the full O(p4) model is under qualification.

    The printed Eq30 and its distinct, analytically derived reversed-kaon-leg
    convention remain separate. Never use a total-current projection to hide
    that finite, transverse difference.
    """
    parent,pion,lepton,neutrino,dark=[np.asarray(v,dtype=float) for v in (parent,pion,lepton,neutrino,dark)]
    kind=model.get('kind')
    if kind=='chpt-p4-neutral-kaon-semileptonic-vector':
        leading=leading_current(parent,pion,lepton,neutrino,dark)
    elif kind=='chpt-p2-charged-kaon-semileptonic-control':
        if order!='p2-control':
            raise ValueError('Charged semileptonic O(p4) current is not implemented')
        leading=leading_charged_current(parent,pion,lepton,neutrino,dark)
    else:
        raise ValueError('Explicit neutral O(p4) or charged leading-control authority required')
    if order=='p2-control':
        return dict(leading=leading,total=leading)
    if order!='p2-plus-p4' or loops is None:
        raise ValueError('An explicit chiral order and qualified loop backend required')
    if model.get('mixed_mass_b1_routing')!='reversed-kaon-leg':
        raise ValueError('Printed O(p4) contact has an unqualified finite Ward defect')
    q2=float(dot(dark,dark))
    if q2<=0:
        raise ValueError('The full timelike current requires positive vector virtuality')
    par=chiral_parameters(model,loops)
    weak=parent-pion-dark;h=parent+pion;delta=parent-pion
    hc=weak_vertex(h,delta,model,loops);hd=weak_vertex(h+dark,delta-dark,model,loops)
    hcv=hc[0]*h+hc[1]*delta
    hdv=hd[0]*(h+dark)+hd[1]*(delta-dark)
    tensor=neutral_contact_tensors(parent,pion,dark,model,loops)['total']
    target=hdv-hcv
    remainder=METRIC*dark@tensor-target
    # The primary removes q^mu terms against the conserved pair current.
    # After the independently derived finite mass-routing correction, this
    # remainder is O(q²); its completion is finite as q² ->0 and changes no
    # physical helicity amplitude. The small-q² behavior is tested before
    # completion, rather than calling this construction its own Ward test.
    tensor=tensor-np.outer(dark,remainder)/q2
    pion_denominator=2*dot(pion,dark)+q2
    lepton_denominator=2*dot(lepton,dark)+q2
    kaon_denominator=q2-2*dot(parent,dark)
    hcslash=hc[0]*slash(h)+hc[1]*slash(delta)
    hdslash=hd[0]*slash(h+dark)+hd[1]*slash(delta-dark)
    weak_current=np.array([hcslash@CHIRAL_LEFT@
        (2*lepton[mu]*IDENTITY+slash(dark)@GAMMA[mu])/lepton_denominator-
        (2*pion[mu]+dark[mu])/pion_denominator*hdslash@CHIRAL_LEFT
        for mu in range(4)])
    weak_current+=np.einsum('mn,n,nij,jk->mik',tensor,METRIC,GAMMA,CHIRAL_LEFT)
    # Stable H(q²)/q² limits, using the already qualified equal-mass loop.
    renormalized_l9=par['l9']+loops.lambda0/(8*LOOP_FACTOR)
    hpi=equal_mass_h_over_q2(q2,math.sqrt(par['pion2']),par['fpi_gev'],renormalized_l9,loops.scale)
    hk=equal_mass_h_over_q2(q2,math.sqrt(par['kaon2']),par['fpi_gev'],renormalized_l9,loops.scale)
    pion_over_q2,kaon_over_q2=2*hpi+hk,hk-hpi
    electromagnetic=np.array([-pion_over_q2*slash(h+dark)@CHIRAL_LEFT*
        (q2*(2*pion[mu]+dark[mu])/pion_denominator-dark[mu])+
        kaon_over_q2*slash(h-dark)@CHIRAL_LEFT*
        (q2*(2*parent[mu]-dark[mu])/kaon_denominator+dark[mu])
        for mu in range(4)])
    axial=-1j/(8*math.pi**2*par['fpi_gev']**2)*np.einsum('mnrs,r,s->mn',
        EPSILON,METRIC*dark,METRIC*weak)
    anomaly=np.einsum('mn,n,nij,jk->mik',axial,METRIC,GAMMA,CHIRAL_LEFT)
    return dict(leading=leading,weak_p4=weak_current,electromagnetic_p4=electromagnetic,
        anomaly_p4=anomaly,total=leading+weak_current+electromagnetic+anomaly)


def spin_density(parent,pion,lepton,neutrino,dark,model,loops=None,*,order,epsilon_em,
                 polarizations=None):
    """All three massive-vector helicities, before BR(A'->mu mu)."""
    if not math.isfinite(epsilon_em) or not 0<=epsilon_em<=.1:
        raise ValueError('Explicit small finite electromagnetic mixing required')
    operators=current_operators(parent,pion,lepton,neutrino,dark,model,loops,order=order)
    basis=polarization_basis(dark) if polarizations is None else np.asarray(polarizations,dtype=complex)
    coefficient=epsilon_em**2*4*math.pi*model['alpha_em']*model['fermi_constant_gev_minus2']**2*model['v_us']**2/2
    incoming=slash(neutrino);outgoing=slash(lepton)-model['lepton_mass_gev']*IDENTITY
    result={}
    for name,current in operators.items():
        amplitudes=np.einsum('ar,r,rij->aij',basis.conjugate(),METRIC,current)
        conjugates=np.array([GAMMA[0]@matrix.conjugate().T@GAMMA[0] for matrix in amplitudes])
        result[name]=coefficient*np.einsum('ij,ajk,kl,bli->ab',incoming,amplitudes,outgoing,conjugates)
    return result


class NeutralCurrentEvaluator:
    """Explicit physical-matrix qualification of a tensor precision warning.

    A Collier accuracy=-1 result is never silently accepted. Re-evaluate the
    physical current with its independent COLI and DD algorithms and with a
    changed subtraction scale/consistently running LECs. Require agreement
    of the transported helicity operators, full3x3 density and trace, together
    with the current Ward identity. Other errors and critical accuracy=-2
    remain failures. The first warning and both algorithm flags are retained.

    This addresses numerical stability only. Source normalization, rate
    integration, event sampling and native transport qualification are separate.
    """
    physical_matrix_target=1.e-8
    production_qualified=False

    def __init__(self,library,*,scale_gev=.77,lambda0=0.):
        self.primary=CollierLoops(library,scale_gev=scale_gev,lambda0=lambda0,mode=3)
        self.coli=CollierLoops(library,scale_gev=scale_gev,lambda0=lambda0,mode=1,
            _allow_precision_warning=True)
        self.dd=CollierLoops(library,scale_gev=scale_gev,lambda0=lambda0,mode=2,
            _allow_precision_warning=True)
        self.scaled=CollierLoops(library,scale_gev=scale_gev*math.sqrt(2),lambda0=lambda0,mode=1,
            _allow_precision_warning=True)

    @staticmethod
    def _relative(first,second):
        norm=max(float(np.max(np.abs(first))),float(np.max(np.abs(second))))
        difference=float(np.max(np.abs(first-second)))
        if norm==0:
            return 0. if difference==0 else math.inf
        return difference/norm

    @staticmethod
    def _warnings(records):
        # Exact kinematics already belong to the event record. Keep compact
        # per-integral flags and an input hash, rather than copying each full
        # list of loop arguments into every source row.
        unique={json.dumps(row,sort_keys=True) for row in records}
        return [dict(mode=row['mode'],kind=row['kind'],error=row['error'],accuracy=row['accuracy'],
            arguments_sha256=hashlib.sha256(encoded.encode()).hexdigest())
            for encoded in sorted(unique) for row in [json.loads(encoded)]]

    def density(self,parent,pion,lepton,neutrino,dark,model,*,epsilon_em):
        point=(parent,pion,lepton,neutrino,dark)
        try:
            values=spin_density(*point,model,self.primary,order='p2-plus-p4',epsilon_em=epsilon_em)
            return dict(densities=values,numerical_validation=dict(method='COLLIER-COLI-DD-comparison',
                tensor_accuracy_flags=[],physical_matrix_target=self.physical_matrix_target))
        except LoopPrecisionWarning as first:
            initial=first.record
        states=polarization_basis(dark);amplitudes=[];densities=[];flags=[];ward_errors=[]
        for backend in (self.coli,self.dd,self.scaled):
            backend.precision_warnings.clear()
            operators=current_operators(*point,model,backend,order='p2-plus-p4')['total']
            values=spin_density(*point,model,backend,order='p2-plus-p4',epsilon_em=epsilon_em)
            amplitudes.append(np.einsum('ar,r,rij->aij',states.conjugate(),METRIC,operators))
            densities.append(values)
            contracted=np.einsum('r,r,rij->ij',METRIC,dark,operators)
            norm=float(np.max(np.abs(operators)))*max(float(np.max(np.abs(dark))),1.e-30)
            ward_errors.append(float(np.max(np.abs(contracted)))/max(norm,1.e-300))
            flags.extend(backend.precision_warnings)
        amplitude_error=max(self._relative(amplitudes[0],value) for value in amplitudes[1:])
        density_error=max(self._relative(densities[0]['total'],value['total']) for value in densities[1:])
        trace_error=max(self._relative(np.asarray(np.trace(densities[0]['total'])),
            np.asarray(np.trace(value['total']))) for value in densities[1:])
        if max(amplitude_error,density_error,trace_error,*ward_errors)>self.physical_matrix_target:
            raise ValueError('Semileptonic current failed its independent physical precision qualification: '+
                str(dict(amplitude=amplitude_error,density=density_error,trace=trace_error,ward=ward_errors,
                    first_warning=initial)))
        return dict(densities=densities[0],numerical_validation=dict(
            method='COLI-DD-physical-current-and-scale-comparison',
            tensor_accuracy_flags=self._warnings([initial,*flags]),
            physical_matrix_target=self.physical_matrix_target,
            relative_operator_disagreement=amplitude_error,
            relative_density_disagreement=density_error,relative_trace_disagreement=trace_error,
            relative_ward_residuals=ward_errors,reference_scale_gev=self.primary.scale,
            comparison_scale_gev=self.scaled.scale,
            uncertainty_scope='Numerical stability target; not a universal physical or integration error bound.'))


def phase_space_density(parent_mass,pion_mass,lepton_mass,mass,s_hadron_vector,s_weak):
    """dPhi4/(2M dsA dsB dcosA dcosB dphi), including global rotations."""
    first,second=math.sqrt(s_hadron_vector),math.sqrt(s_weak)
    if min(first,second,parent_mass)<=0 or first+second>=parent_mass:
        raise ValueError('Open physical four-body masses required')
    outer=two_body_momentum(parent_mass,first,second)
    inner=two_body_momentum(first,pion_mass,mass)
    weak=(s_weak-lepton_mass**2)/(2*second)
    if weak<=0:
        raise ValueError('An open lepton-neutrino pair required')
    return outer*inner*weak/(4096*math.pi**6*parent_mass**2*first*second)


def differential_width(model,mass,s_hadron_vector,s_weak,cosine_pion,cosine_lepton,azimuth,
                       loops=None,*,order,epsilon_em):
    point=rest_kinematics(model['parent_mass_gev'],model['pion_mass_gev'],model['lepton_mass_gev'],
        mass,s_hadron_vector,s_weak,cosine_pion,cosine_lepton,azimuth)
    density=spin_density(*point,model,loops,order=order,epsilon_em=epsilon_em)['total']
    return float(np.trace(density).real)*phase_space_density(model['parent_mass_gev'],
        model['pion_mass_gev'],model['lepton_mass_gev'],mass,s_hadron_vector,s_weak)


def rest_kinematics(parent_mass,pion_mass,lepton_mass,mass,s_hadron_vector,s_weak,
                    cosine_pion,cosine_lepton,azimuth):
    """Four-body coordinates K -> (pi A')(l nu), with both pair angles."""
    first,second=math.sqrt(s_hadron_vector),math.sqrt(s_weak)
    if (min(parent_mass,pion_mass,mass,first,second)<=0 or lepton_mass<0
            or first<=pion_mass+mass or second<=lepton_mass or first+second>=parent_mass
            or not -1<cosine_pion<1 or not -1<cosine_lepton<1):
        raise ValueError('Open physical four-body coordinates required')
    outer=two_body_momentum(parent_mass,first,second)
    energy_first,energy_second=math.hypot(first,outer),math.hypot(second,outer)
    def pair_boost(vector,energy,pair_mass,direction):
        # E_pair/M_pair and p_pair/M_pair are known directly. Recovering
        # gamma from1-beta² loses precision for a light lepton-neutrino pair.
        gamma=energy/pair_mass;motion=direction*outer/pair_mass
        return (math.fsum((gamma*vector[0],motion*vector[3])),vector[1],vector[2],
            math.fsum((gamma*vector[3],motion*vector[0])))
    internal=two_body_momentum(first,pion_mass,mass)
    transverse=math.sqrt(1-cosine_pion*cosine_pion)
    pion=(math.hypot(pion_mass,internal),internal*transverse,0.,internal*cosine_pion)
    dark=(math.hypot(mass,internal),-pion[1],0.,-pion[3])
    pion,dark=[pair_boost(v,energy_first,first,1.) for v in (pion,dark)]
    internal=(s_weak-lepton_mass**2)/(2*second)
    transverse=internal*math.sqrt(1-cosine_lepton*cosine_lepton)
    lepton=(math.hypot(lepton_mass,internal),transverse*math.cos(azimuth),
        transverse*math.sin(azimuth),internal*cosine_lepton)
    neutrino=(internal,-lepton[1],-lepton[2],-lepton[3])
    lepton,neutrino=[pair_boost(v,energy_second,second,-1.) for v in (lepton,neutrino)]
    return tuple(np.asarray(v) for v in ((parent_mass,0.,0.,0.),pion,lepton,neutrino,dark))


def uniform_production_point(model,mass,coordinates):
    """Map five independent U(0,1) values onto the entire four-body domain.

    The pairs have masses a=m(pi A') and b=m(l nu). Use a in its full
    interval, b in [ml,M-a], both helicity cosines and the relative azimuth.
    Global rotations are already integrated in phase_space_density. The
    returned Jacobian is dsA dsB dcosA dcosB dphi / d^5u, not a normalized
    decay probability. In particular uniform coordinates are not unit-weight
    physical decays. No current, rare branching ratio or dimuon BR enters it.
    """
    if len(coordinates)!=5 or any(not math.isfinite(v) or not 0<v<1 for v in coordinates):
        raise ValueError('Five finite open unit-cube coordinates required')
    M,pi,ml=[float(model[name]) for name in
        ('parent_mass_gev','pion_mass_gev','lepton_mass_gev')]
    if not all(math.isfinite(v) for v in (M,pi,ml,mass)) or min(M,pi,mass)<=0 or ml<0:
        raise ValueError('Finite physical masses required')
    span=M-pi-mass-ml
    if span<=0:
        raise ValueError('Open semileptonic four-body channel required')
    a=pi+mass+span*coordinates[0]
    weak_span=M-a-ml
    b=ml+weak_span*coordinates[1]
    point=rest_kinematics(M,pi,ml,mass,a*a,b*b,
        2*coordinates[2]-1,2*coordinates[3]-1,2*math.pi*coordinates[4])
    jacobian=32*math.pi*a*b*span*weak_span
    return dict(point=point,coordinate_jacobian=jacobian,
        phase_space_weight=jacobian*phase_space_density(M,pi,ml,mass,a*a,b*b),
        hadron_vector_mass_squared=a*a,weak_mass_squared=b*b)


def weighted_production_point(model,mass,coordinates,loops=None,*,order,epsilon_em):
    """Exact positive production measure for a uniform proposal, in GeV.

    Averaging width_weight_gev gives Gamma(K0 -> pi- l+ nu A') at the
    specified epsilon_EM. It excludes BR(A'->mumu), kaon source abundance
    and parent flight. Keeping the exact proposal weight avoids inventing
    an unproven rejection maximum or calling a finite grid unit weight.
    Integration uncertainty and native-source qualification remain external.
    """
    row=uniform_production_point(model,mass,coordinates)
    if isinstance(loops,NeutralCurrentEvaluator):
        if order!='p2-plus-p4':
            raise ValueError('The qualified evaluator supplies the full neutral O(p4) current')
        result=loops.density(*row['point'],model,epsilon_em=epsilon_em)
        rho=result['densities']['total'];validation=result['numerical_validation']
    else:
        rho=spin_density(*row['point'],model,loops,order=order,epsilon_em=epsilon_em)['total']
        validation=dict(method='explicit-current-backend',
            tensor_precision_warning_policy='Backend exceptions are not caught or suppressed')
    trace=float(np.trace(rho).real)
    tolerance=128*np.finfo(float).eps*max(trace,1.e-300)
    if (not np.all(np.isfinite(rho)) or trace<=0 or
            float(np.max(np.abs(rho-rho.conjugate().T)))>tolerance or
            float(np.linalg.eigvalsh(rho).min()) < -tolerance):
        raise ValueError('Finite positive Hermitian production density required')
    row.update(density=rho,width_weight_gev=trace*row['phase_space_weight'],
        numerical_validation=validation,order=order,epsilon_em=epsilon_em,
        production_qualified=False)
    return row


def sample_weighted_rest_decay(model,mass,proper_ctau_mm,rng,loops=None,*,order,epsilon_em):
    """Weighted spin-zero kaon-rest pi l+ nu A' -> pi l+ nu mu- mu+ probe.

    This returns one uniform production proposal with its exact differential
    width weight. The dimuon conditional angle and exponential proper flight
    are sampled from normalized physical distributions and add no extra
    weight. No rejection/unweighting of the four-body production is implied.
    This development API must not enter the unit-weight meson source runner.

    The model kind distinguishes the neutral full current from the charged
    leading control. Parent flavour/charge labels, the KL/KS charge mixture,
    native parent flight and material
    transport are deliberately not attached here. Their qualification must
    precede any production integration. The output kaon is at rest, so no
    inherited native momentum precision prescription is needed by this API.
    """
    if not math.isfinite(mass) or mass<=2*MUON_MASS_GEV:
        raise ValueError('Open dimuon channel required')
    if not math.isfinite(proper_ctau_mm) or proper_ctau_mm<0:
        raise ValueError('Finite nonnegative physical proper lifetime required')
    # Validate physical support before consuming the event RNG.
    uniform_production_point(model,mass,(.5,)*5)
    row=weighted_production_point(model,mass,tuple(rng.random() for _ in range(5)),
        loops,order=order,epsilon_em=epsilon_em)
    parent,pion,lepton,neutrino,dark=row['point']
    direction,trials=sample_vector_muon_direction(row['density'],mass,rng)
    basis=polarization_basis(dark)
    dark_rest=np.array([_boost_from_parent_rest(state,
        (dark[0],*(-v for v in dark[1:])),mass)[1:] for state in basis])
    momentum=math.sqrt((mass/2-MUON_MASS_GEV)*(mass/2+MUON_MASS_GEV))
    spatial=momentum*(direction@dark_rest)
    muon,antimuon=[_boost_from_parent_rest((mass/2,*(sign*spatial)),dark,mass)
        for sign in (1.,-1.)]
    rotation=_isotropic_rotation(rng)
    moved=[(float(v[0]),*(float(x) for x in rotation@v[1:]))
        for v in (pion,lepton,neutrino,dark,np.asarray(muon),np.asarray(antimuon))]
    pion,lepton,neutrino,dark,muon,antimuon=moved
    proper=-proper_ctau_mm*math.log1p(-rng.random())
    return dict(parent_p4=tuple(parent),pion_p4=pion,charged_lepton_p4=lepton,
        neutrino_p4=neutrino,dark_photon_p4=dark,muon_p4=muon,antimuon_p4=antimuon,
        width_weight_gev=row['width_weight_gev'],epsilon_em=epsilon_em,current_model_kind=model['kind'],
        density_matrix_real=row['density'].real.tolist(),density_matrix_imaginary=row['density'].imag.tolist(),
        density_basis='canonical-kaon-rest-before-isotropic-rotation',
        numerical_validation=row['numerical_validation'],angular_proposal_trials=trials,
        proper_decay_length_mm=proper,decay_ct_mm=proper*dark[0]/mass,
        decay_displacement_mm=tuple(proper*v/mass for v in dark[1:]),
        proposal='uniform-five-dimensional-production; exact width weight',
        dimuon_branching_included=False,parent_flight_included=False,
        material_transport_qualified=False,production_qualified=False,
        output_gen_weight_convention='Nonunit differential-width weight; never a unit-weight source event',
        unconditional_decay=True)
