# Code style: AI-code. AI-generated rate-only heavy-quarkonium current controls.
"""Conditional electromagnetic rates, not a source model or GEN adapter.

chi_c1 uses the E1, M2, C1 lattice form factors of arXiv:2604.15256v2,
Eqs50,55,56 and TableIV. Dividing the Born dilepton spectrum by the
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
BR(A'->mumu), parent-source normalization and detector acceptance are absent.
Masses/widths are GeV; alpha_em is the Thomson-limit EM convention.
"""
import math

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
