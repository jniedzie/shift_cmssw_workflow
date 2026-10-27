# Code style: AI-code. AI-generated published scalar-meson current kernel.
"""Strong amplitudes in Escribano et al., Phys. Rev. D102, 034026 (2020).

This implements the leading ChPT expressions (4)--(7), or their replacement
by the linear-sigma model (16)--(21), with the first-sheet one-loop scalar
propagators (A1)--(A7). The crossed scalar exchanges use the paper's explicit
chiral-matched infinite-mass approximation, not an additional fitted term.

The returned amplitudes multiply the charged-scalar electromagnetic loop.
The caller owns that loop, its off-shell continuation, interference and phase
space. This phenomenological model is not a bound on omitted QCD operators.
All dimensionful inputs are GeV; the strong amplitudes are dimensionless.
Primary: https://doi.org/10.1103/PhysRevD.102.034026
"""

import math
from functools import lru_cache

from dark_photon_dipion_meson import _gauss_legendre


CHPT_KIND = 'escribano-2020-chpt'
SIGMA_KIND = 'escribano-2020-linear-sigma'
_MODES = {(221, 111), (331, 111), (331, 221)}
_SMALL_S_NODES = tuple(((node+1)/2, weight/2) for node, weight in _gauss_legendre(24))


def _positive(model, name):
    value = float(model[name])
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be finite and positive')
    return value


def validate_scalar_model(model):
    """Require an explicit input card and the declared theory convention."""
    if model.get('kind') not in (CHPT_KIND, SIGMA_KIND):
        raise ValueError('An explicit Escribano-2020 ChPT or linear-sigma model is required')
    mode = (model.get('parent_pdg_id'), model.get('recoil_pdg_id'))
    if mode not in _MODES or model.get('isospin_limit') is not True:
        raise ValueError('Supported neutral transition in the isospin limit required')
    if not isinstance(model.get('reference'), str) or not model['reference']:
        raise ValueError('An explicit primary reference is required')
    for name in ('parent_mass_gev', 'recoil_mass_gev', 'pion_mass_gev',
                 'kaon_mass_gev', 'eta_mass_gev', 'eta_prime_mass_gev',
                 'f_pi_gev', 'alpha_em'):
        _positive(model, name)
    if model['parent_mass_gev'] <= model['recoil_mass_gev']:
        raise ValueError('Parent mass must exceed recoil mass')
    for name in ('phi_p_deg',):
        if not math.isfinite(float(model[name])):
            raise ValueError(f'{name} must be finite')
    # The primary treats masses as external inputs, rather than deriving them
    # from a chosen scalar mass or mixing fit. Keep the transition consistent.
    expected_parent = model['eta_mass_gev' if mode[0] == 221 else 'eta_prime_mass_gev']
    if not math.isclose(model['parent_mass_gev'], expected_parent, rel_tol=1e-12):
        raise ValueError('Parent mass and explicit pseudoscalar mass disagree')
    if mode[1] == 221 and not math.isclose(
            model['recoil_mass_gev'], model['eta_mass_gev'], rel_tol=1e-12):
        raise ValueError('Eta recoil mass and explicit eta mass disagree')
    if model['kind'] == SIGMA_KIND:
        _positive(model, 'f_k_gev')
        if not math.isfinite(float(model['phi_s_deg'])):
            raise ValueError('phi_s_deg must be finite')
        for name in ('sigma', 'f0', 'a0'):
            _positive(model['scalar_masses_gev'], name)
        if model.get('crossed_scalar_prescription') != 'chiral-matched-infinite-mass':
            raise ValueError('The published crossed-scalar approximation must be explicit')
    return model


def _bubble(s, first_mass, second_mass):
    """Dimensionless first-sheet two-point function, Appendix A2/A6.

    Its imaginary part is negative above the physical two-body threshold.
    The real constant is the primary convention; it cancels in A1. Below
    pseudothreshold the last logarithm in A6 is essential. The small-s
    evaluation is an equivalent smooth Feynman-parameter integral, avoiding
    cancellation of terms proportional to 1/s in the unequal-mass formula.
    """
    if not math.isfinite(s) or s < 0:
        raise ValueError('A nonnegative finite invariant is required')
    high = max(first_mass, second_mass)
    low = min(first_mass, second_mass)
    threshold = (high + low)**2
    if high == low:
        if s == 0:
            return 0j
        if s < 1e-5 * threshold:
            z = s / high**2
            return complex(z / 6 + z*z / 60 + z**3 / 420 + z**4 / 2520)
        if s < threshold:
            beta = math.sqrt(threshold / s - 1)
            return complex(2 - 2*beta*math.atan(1/beta))
        beta = math.sqrt(max(0., 1 - threshold/s))
        return complex(2-beta*math.log1p(2*beta/(1-beta)), -math.pi*beta)
    delta = high*high - low*low
    log_ratio = math.log(high/low)
    if s < 1e-4 * threshold:
        at_zero = 1 - (high*high + low*low)/delta * log_ratio
        correction = math.fsum(weight*math.log1p(-s*x*(1-x)/(x*high*high+(1-x)*low*low))
                               for x, weight in _SMALL_S_NODES)
        return complex(at_zero-correction)
    pseudo = (high-low)**2
    base = 2 - delta/s * log_ratio
    if s >= threshold:
        plus = math.sqrt(max(0., 1-threshold/s))
        minus = math.sqrt(max(0., 1-pseudo/s))
        logarithm = math.log((plus+minus)**2*s/(4*high*low))
        return complex(base-plus*minus*logarithm, -math.pi*plus*minus)
    if s >= pseudo:
        plus = math.sqrt(threshold/s-1)
        minus = math.sqrt(max(0., 1-pseudo/s))
        return complex(base-2*plus*minus*math.atan2(minus, plus))
    plus = math.sqrt(threshold/s-1)
    minus = math.sqrt(pseudo/s-1)
    logarithm = math.log((plus+minus)**2*s/(4*high*low))
    return complex(base+plus*minus*logarithm)


@lru_cache(maxsize=128)
def _scalar_couplings(pion, kaon, eta, f_pi, f_k, phi_p, phi_s, sigma, f0, a0):
    """Squared total-isospin couplings in Appendix A, not charged-only ones."""
    cp, sp = math.cos(phi_p), math.sin(phi_p)
    cs, ss = math.cos(phi_s), math.sin(phi_s)
    root_two = math.sqrt(2)
    return {
        'sigma': ((pion, pion, 1.5*((pion*pion-sigma*sigma)/f_pi*cs)**2),
                  (kaon, kaon, .5*((kaon*kaon-sigma*sigma)/f_k*(cs-root_two*ss))**2)),
        'f0': ((pion, pion, 1.5*((pion*pion-f0*f0)/f_pi*ss)**2),
               (kaon, kaon, .5*((kaon*kaon-f0*f0)/f_k*(ss+root_two*cs))**2)),
        'a0': ((kaon, kaon, .5*((kaon*kaon-a0*a0)/f_k)**2),
               (pion, eta, ((eta*eta-a0*a0)/f_pi*cp)**2)),
    }


def _parameters(model):
    return (model['pion_mass_gev'], model['kaon_mass_gev'], model['eta_mass_gev'],
            model['f_pi_gev'], model['f_k_gev'], math.radians(model['phi_p_deg']),
            math.radians(model['phi_s_deg']), model['scalar_masses_gev']['sigma'],
            model['scalar_masses_gev']['f0'], model['scalar_masses_gev']['a0'])


def scalar_propagator(s, resonance, model):
    """Inverse propagator D_R(s) of A1, including its on-shell subtraction."""
    if resonance not in ('sigma', 'f0', 'a0'):
        raise ValueError('Unsupported scalar resonance')
    parameters = _parameters(model)
    mass = model['scalar_masses_gev'][resonance]
    loops = _scalar_couplings(*parameters)[resonance]
    self_energy = sum(coupling * _bubble(s, first, second)
                      for first, second, coupling in loops)/(16*math.pi**2)
    at_pole = sum(coupling * _bubble(mass*mass, first, second).real
                  for first, second, coupling in loops)/(16*math.pi**2)
    return complex(s-mass*mass+self_energy.real-at_pole, self_energy.imag)


def scalar_thresholds(model):
    """All two-body loop cuts, unequal-mass pseudothresholds and pole scales."""
    validate_scalar_model(model)
    pion, kaon, eta = (model[name] for name in
                      ('pion_mass_gev', 'kaon_mass_gev', 'eta_mass_gev'))
    result = {4*pion*pion, 4*kaon*kaon}
    if model['kind'] == SIGMA_KIND:
        result.update(((eta-pion)**2, (eta+pion)**2))
        result.update(mass*mass for mass in model['scalar_masses_gev'].values())
    return sorted(result)


def _chiral_kaon(s, model):
    """Equations 4--6, with the eta-prime replacements in Eq5."""
    pion2 = model['pion_mass_gev']**2
    kaon2 = model['kaon_mass_gev']**2
    eta2, prime2 = model['eta_mass_gev']**2, model['eta_prime_mass_gev']**2
    angle = math.radians(model['phi_p_deg'])
    cp, sp = math.cos(angle), math.sin(angle)
    if model['recoil_pdg_id'] == 111:
        mass2 = model['parent_mass_gev']**2
        first, second = ((cp+math.sqrt(2)*sp, cp-sp/math.sqrt(2))
                         if model['parent_pdg_id'] == 221 else
                         (sp-math.sqrt(2)*cp, sp+cp/math.sqrt(2)))
        return ((s-mass2/3-8*kaon2/9-pion2/9)*first
                +4/9*(2*kaon2+pion2)*second)/(4*model['f_pi_gev']**2)
    return -((s-(eta2+prime2)/3-8*kaon2/9-2*pion2/9)
             *(math.sqrt(2)*math.cos(2*angle)+math.sin(2*angle)/2)
             +4/9*(2*kaon2-pion2)*(2*math.sin(2*angle)-math.cos(2*angle)/math.sqrt(2))
             )/(4*model['f_pi_gev']**2)


def scalar_strong_amplitudes(s, model):
    """Return (charged-loop mass, strong amplitude), Eqs4--7 or16--21.

    The caller validates its frozen card once; each evaluation checks only
    the physical invariant. The electromagnetic loop is not included here.
    """
    s = float(s)
    if not math.isfinite(s) or s < 0:
        raise ValueError('A nonnegative finite invariant is required')
    pion, kaon, eta, prime = (model[name] for name in
        ('pion_mass_gev', 'kaon_mass_gev', 'eta_mass_gev', 'eta_prime_mass_gev'))
    f_pi = model['f_pi_gev']
    phi_p = math.radians(model['phi_p_deg'])
    cp, sp = math.cos(phi_p), math.sin(phi_p)
    sin_two, cos_two = math.sin(2*phi_p), math.cos(2*phi_p)
    if model['kind'] == CHPT_KIND:
        result = [(kaon, complex(_chiral_kaon(s, model)))]
        if model['recoil_pdg_id'] == 221:
            result.append((pion, complex(pion*pion/(2*f_pi*f_pi)*sin_two)))
        return result
    if model['kind'] != SIGMA_KIND:
        raise ValueError('Unsupported scalar model kind')
    f_k = model['f_k_gev']
    a0 = model['scalar_masses_gev']['a0']
    if model['recoil_pdg_id'] == 111:
        mass2 = model['parent_mass_gev']**2
        if model['parent_pdg_id'] == 221:
            first, second = cp, -sp
        else:
            first, second = sp, cp
        amplitude = ((s-mass2)*(kaon*kaon-a0*a0)/scalar_propagator(s, 'a0', model)*first
                     +((5*mass2+pion*pion-3*s)*first
                       +math.sqrt(2)*(mass2+4*kaon*kaon+pion*pion-3*s)*second)/6)/(2*f_pi*f_k)
        return [(kaon, amplitude)]
    phi_s = math.radians(model['phi_s_deg'])
    cs, ss = math.cos(phi_s), math.sin(phi_s)
    common = eta*eta*cp*cp+prime*prime*sp*sp-a0*a0
    splitting = prime*prime-eta*eta
    # Eqs20--21: the prefactor sin(2 phi_P)/(2 f_pi) multiplies
    # the entire brace, including the mass-splitting terms.
    g_sigma = sin_two/(2*f_pi)*(common*(cs+math.sqrt(2)*ss*(2*f_k/f_pi-1))
                               -splitting*(cs*cos_two-ss*sin_two/2))
    g_f0 = sin_two/(2*f_pi)*(common*(ss-math.sqrt(2)*cs*(2*f_k/f_pi-1))
                            -splitting*(ss*cos_two+cs*sin_two/2))
    sigma_part = g_sigma/scalar_propagator(s, 'sigma', model)
    f0_part = g_f0/scalar_propagator(s, 'f0', model)
    kaon_amplitude = ((s-kaon*kaon)/(2*f_k)
                      *(sigma_part*(cs-math.sqrt(2)*ss)+f0_part*(ss+math.sqrt(2)*cs))
                      -(s-kaon*kaon)/(4*f_pi*f_k)*(1-2*(2*f_k/f_pi-1))*sin_two
                      +_chiral_kaon(s, model))
    pion_amplitude = ((s-pion*pion)/f_pi*(sigma_part*cs+f0_part*ss)
                      +(2*pion*pion-s)/(2*f_pi*f_pi)*sin_two)
    return [(kaon, kaon_amplitude), (pion, pion_amplitude)]
