# Code style: AI-code. AI-generated unbiased meson-collision sampling helpers.
"""Unit-weight signal candidates from an explicitly normalized parent source.

Keep every source collision, including those with no eligible parent, in the
source ledger. One chosen physical meson per retained collision is replaced;
all other particles retain their ordinary generated decay histories.

The result describes inclusive single-dark-photon production to leading order
in the rare branching probability. It is not a model of multiple simultaneous
rare decays in the same interaction. Transition form factors and dimuon BRs
must be supplied by a separately pinned physical model card.
"""
import math
from itertools import product

from dark_photon_meson import MUON_MASS_GEV, radiative_branching, transition_form_factor_abs2
import dark_photon_vector_meson as vector_meson

PARENTS = (221, 331, 113, 213, -213, 223, 333, 313, -313, 323, -323,
           130, 310, 321, -321)
PSEUDOSCALAR_DIPION_CORRECTION_LIMIT = 1.e-3


def exclusive_channels(model):
    """Channels share one physical parent inventory, not separate collisions."""
    return model.get('exclusive_channels', [model])


def kaon_channel(model):
    """K_l2 is deliberately not admitted before its source-rate envelope gate."""
    return model.get('decay_type') in ('neutral-kaon-photon','weak-kaon-pion')


def _kaon_group(model):
    return any(kaon_channel(channel) for channel in exclusive_channels(model))


def _kaon_coefficient(parent_mass,signal_mass,model,br_mumu):
    """Fixed on-shell current; invariant-mass calls only check numerical drift."""
    if signal_mass != model['signal_mass_gev']:
        raise ValueError('Resolved kaon current belongs to another fixed signal mass')
    if (not math.isfinite(parent_mass) or parent_mass<=0 or not math.isfinite(br_mumu)
            or not 0<=br_mumu<=1):
        raise ValueError('Physical kaon mass and separately supplied dimuon branching required')
    if signal_mass+recoil_threshold(model) >= parent_mass:
        return 0.
    if model['decay_type']=='neutral-kaon-photon':
        width = (parent_mass**3/(32*math.pi)*(1-(signal_mass/parent_mass)**2)**3
                 *model['radiative_amplitude_abs2_gev_minus2'])
    else:
        from dark_photon_weak_meson import decay_width
        width=decay_width(parent_mass,model['recoil_mass_gev'],signal_mass,.001,
            model['alpha_em'],complex(*model['weak_form_factor']))/1.e-6
    return width/model['total_parent_width_gev']*model['em_mixing_over_epsilon_abs2']*br_mumu


def _resolved_kaon_model(parent,native,signal_mass,epsilon,ecm):
    """Resolve a constant current on exact stable-kaon native mass support.

    All reference inputs are explicit; no photon normalization is inferred
    for the forbidden K->pi gamma transition. The independent weak/KS inputs
    are preserved rather than replaced by a KL or charged-kaon model.
    """
    from dark_photon_kaon import KSDispersiveReference,transition_form_factor
    from dark_photon_model import HBAR_C_GEV_MM,mixing_parameters
    pdg=parent['pdg_id'];mass=parent['mass_gev']
    if (type(mass) not in (int,float) or not math.isfinite(mass) or mass<=0
            or pdg not in (130,310,321,-321) or parent.get('parent_mass_model')!='native-stable-kaon'
            or native.get('mass_width_gev')!=0. or native['mass_min_gev']!=mass
            or native['mass_max_gev']!=mass):
        raise ValueError('Kaon source rates require exact zero-width native mass support')
    if (type(epsilon) not in (int,float) or not math.isfinite(epsilon) or not 0<epsilon<=.01):
        raise ValueError('Resolve kaon currents at the explicit physical epsilon')
    mean=parent.get('mean_proper_ctau_mm');budget=parent.get('relative_numerical_mass_precision_budget')
    if (type(mean) not in (int,float) or not math.isfinite(mean) or mean<=0
            or not parent.get('lifetime_reference') or not parent.get('pdg_reference')
            or type(budget) not in (int,float) or not math.isfinite(budget) or not 0<budget<1
            or not math.isfinite(ecm) or ecm<=mass):
        raise ValueError('Pin kaon lifetime, native mass authority and numerical precision budget')
    width=HBAR_C_GEV_MM/mean
    model=dict(decay_type=parent['decay_type'],mass_min_gev=mass,mass_max_gev=mass,
        parent_mass_model='native-stable-kaon',signal_mass_gev=signal_mass,
        mean_proper_ctau_mm=mean,total_parent_width_gev=width,
        relative_numerical_mass_precision_budget=budget,source_ecm_gev=ecm,
        native_parent_count_bound=math.floor(ecm/mass),
        em_mixing_over_epsilon_abs2=(mixing_parameters(signal_mass,epsilon).em_coefficient/epsilon)**2,
        physical_epsilon=epsilon,channel=parent['channel'],provenance=dict(
            pdg=parent['pdg_reference'],lifetime=parent['lifetime_reference']))
    if parent['decay_type']=='neutral-kaon-photon':
        if pdg not in (130,310):raise ValueError('Only physical KL/KS have the qualified gamma current')
        opened=signal_mass<mass
        if pdg==130:
            branching=parent.get('br_gamma_gamma')
            if (type(branching) not in (int,float) or not math.isfinite(branching)
                    or not 0<branching<=1 or not parent.get('br_gamma_gamma_reference')):
                raise ValueError('KL requires its explicit gamma-gamma branching calibration')
            transition=parent['transition_form_factor']
            transition_form_factor(0.,transition)
            form=transition_form_factor(signal_mass,transition) if opened else 0.
            absolute=64*math.pi*width*branching/mass**3*abs(form)**2
            model['provenance'].update(transition=transition,normalization=parent['br_gamma_gamma_reference'])
        else:
            authority=parent['ks_dispersive_reference']
            reference=KSDispersiveReference(authority['path'],authority['sha256'])
            absolute=abs(reference.amplitude(signal_mass))**2 if opened else 0.
            model['provenance']['transition']=dict(authority,publication=reference.publication)
        model.update(recoil_pdg_id=22,recoil_mass_gev=0.,radiative_amplitude_abs2_gev_minus2=absolute)
    else:
        expected={321:211,-321:-211,310:111}
        if (pdg not in expected or parent.get('recoil_pdg_id')!=expected[pdg]
                or type(parent.get('recoil_mass_gev')) not in (int,float)
                or not 0<parent['recoil_mass_gev']<mass):
            raise ValueError('Qualified Kpi current requires its explicit charge and recoil mass')
        weak=parent['weak_form_factor_model'];alpha=parent.get('alpha_em')
        if (weak.get('kind')!='chpt-p6-kaon-em-vector' or not weak.get('reference')
                or not weak.get('loop_reference') or weak.get('parent_pdg_id')!=pdg
                or type(alpha) not in (int,float) or not math.isfinite(alpha) or alpha<=0):
            raise ValueError('Require the matching charged/KS weak-current model and EM convention')
        from dark_photon_weak_meson import chiral_form_factor
        opened=signal_mass<mass-parent['recoil_mass_gev']
        form=chiral_form_factor(mass,parent['recoil_mass_gev'],signal_mass,weak) if opened else 0j
        model.update(recoil_pdg_id=expected[pdg],recoil_mass_gev=parent['recoil_mass_gev'],
            weak_form_factor=[form.real,form.imag],alpha_em=alpha)
        model['provenance']['transition']=weak
    model['kinematically_open']=opened
    return model


def _validate_kaon_model(pdg,model):
    from dark_photon_model import HBAR_C_GEV_MM,mixing_parameters
    kind=model['decay_type'];mass=model.get('mass_min_gev')
    positive=('mass_min_gev','source_ecm_gev','mean_proper_ctau_mm','total_parent_width_gev',
              'signal_mass_gev','em_mixing_over_epsilon_abs2','physical_epsilon',
              'relative_numerical_mass_precision_budget')
    if (pdg not in (130,310,321,-321) or model.get('parent_mass_model')!='native-stable-kaon'
            or any(type(model.get(k)) not in (int,float) or not math.isfinite(model[k])
                   or model[k]<=0 for k in positive)
            or model.get('mass_max_gev')!=mass or not model.get('provenance')
            or model.get('native_parent_count_bound')!=math.floor(model['source_ecm_gev']/mass)):
        raise ValueError('Require a finite fixed-mass kaon current and native count authority')
    if (model['physical_epsilon']>.01 or model['signal_mass_gev']<=2*MUON_MASS_GEV
            or model['relative_numerical_mass_precision_budget']>=1
            or model['total_parent_width_gev']!=HBAR_C_GEV_MM/model['mean_proper_ctau_mm']
            or model['em_mixing_over_epsilon_abs2']!=(mixing_parameters(
                model['signal_mass_gev'],model['physical_epsilon']).em_coefficient/model['physical_epsilon'])**2):
        raise ValueError('Resolved kaon width/coupling differs from its physical lifetime/epsilon')
    if kind=='neutral-kaon-photon':
        value=model.get('radiative_amplitude_abs2_gev_minus2')
        if (pdg not in (130,310) or model.get('recoil_pdg_id')!=22
                or type(value) not in (int,float) or not math.isfinite(value) or value<0):
            raise ValueError('Require the separately qualified neutral-kaon gamma amplitude')
    else:
        form=model.get('weak_form_factor')
        if (model.get('recoil_pdg_id')!={321:211,-321:-211,310:111}.get(pdg)
                or type(model.get('recoil_mass_gev')) not in (int,float)
                or not 0<model['recoil_mass_gev']<mass
                or type(model.get('alpha_em')) not in (int,float)
                or not math.isfinite(model['alpha_em']) or model['alpha_em']<=0
                or not isinstance(form,list) or len(form)!=2
                or any(type(x) not in (int,float) or not math.isfinite(x) for x in form)):
            raise ValueError('Require a matching charged/KS weak current and pion recoil')


def _kaon_snapshot_authority(parent,model,kinematic_mass):
    """Check the native final-parent antichain and physical COM mass floor.

    The independent double-precision interval encloses products/dot/sqrt with
    gamma_32=32u/(1-32u), u=2^-53. Native COM values use long double; this
    interval deliberately also covers their JSON conversion. Native-vs-p4
    mass drift is recorded separately and must pass the existing rate gate.
    The integer count limit must stay unchanged after those allowances.
    """
    channel=next(c for c in exclusive_channels(model) if kaon_channel(c))
    total=parent.get('source_total_p4');momentum=parent['p4']
    mothers=parent.get('mother_indices');daughters=parent.get('daughter_indices')
    if (parent.get('native_final') is not True or type(parent.get('native_status')) is not int
            or parent['native_status']<=0 or daughters!=[] or not isinstance(mothers,list)
            or any(type(x) is not int or x<=0 for x in mothers) or parent['index'] in mothers
            or not isinstance(total,(tuple,list)) or len(total)!=4
            or any(type(x) not in (int,float) or not math.isfinite(x) for x in total)
            or parent.get('mean_proper_lifetime_mm')!=channel['mean_proper_ctau_mm']):
        raise ValueError('Require the pinned native final-kaon graph, lifetime and actual beam vector')
    gamma=32*2.**-53/(1-32*2.**-53)
    qterms=[x*x for x in total];q2=math.fsum([qterms[0]]+[-x for x in qterms[1:]])
    dq=gamma*math.fsum(qterms)
    products=[a*b for a,b in zip(momentum,total)]
    numerator=math.fsum([products[0]]+[-x for x in products[1:]])
    dn=gamma*math.fsum(abs(x) for x in products)
    if total[0]<=0 or q2<=dq or numerator<=dn:
        raise ValueError('Actual incoming beam vector is not a physical COM authority')
    ecm_low,ecm_high=math.sqrt(q2-dq),math.sqrt(q2+dq)
    low,high=(numerator-dn)/ecm_high,(numerator+dn)/ecm_low
    energy=parent.get('source_com_energy_gev')
    if (type(energy) not in (int,float) or not math.isfinite(energy)
            or not low<=energy<=high or not ecm_low<=channel['source_ecm_gev']<=ecm_high):
        raise ValueError('Native kaon COM energy disagrees with its raw beam/momentum inputs')
    arithmetic=max(energy-low,high-energy)
    native_delta=abs(kinematic_mass-parent['native_mass_gev'])
    if energy+arithmetic+native_delta<parent['native_mass_gev']:
        raise ValueError('Physical native kaon COM energy violates its qualified mass floor')
    mass_floor=parent['native_mass_gev']-native_delta-2*arithmetic
    if (mass_floor<=0 or low<mass_floor
            or math.floor(ecm_high/mass_floor)!=channel['native_parent_count_bound']):
        raise ValueError('Kaon arithmetic allowance changes the fixed integer source-count envelope')
    return dict(source_total_p4=list(total),source_com_energy_gev=energy,
        independent_com_energy_interval_gev=[low,high],
        independent_source_ecm_interval_gev=[ecm_low,ecm_high],
        com_energy_arithmetic_allowance_gev=arithmetic,
        native_invariant_mass_difference_gev=native_delta,
        stable_native_mass_floor_gev=parent['native_mass_gev'],
        roundoff_mass_floor_gev=mass_floor,
        native_parent_count_bound=channel['native_parent_count_bound'],
        authority='Final native parent with no daughters; independent physical COM and unchanged integer-count checks')


def charged_rho_parent_rate_model(pdg, model):
    """Use only the two owned channels of this same physical charged parent."""
    channels = exclusive_channels(model)
    if len(channels) == 2 and any(row.get('decay_type') == 'charged-vector-dipion'
                                  for row in channels):
        return dict(parent_pdg_id=pdg, exclusive_channels=[dict(row, pdg_id=pdg) for row in channels])
    return None


def dipion_channel(model):
    return model.get('decay_type') in ('pseudoscalar-dipion', 'vector-dipion', 'charged-vector-dipion')


def vector_dipion_channel(model):
    return model.get('decay_type') in ('vector-dipion', 'charged-vector-dipion')


def dipion_kernel(model):
    if model.get('decay_type') == 'charged-vector-dipion':
        import dark_photon_charged_rho_radiative
        return dark_photon_charged_rho_radiative
    if model.get('decay_type') == 'vector-dipion':
        import dark_photon_rho_radiative
        return dark_photon_rho_radiative
    import dark_photon_dipion_meson
    return dark_photon_dipion_meson


def recoil_threshold(model):
    if model.get('decay_type')=='weak-kaon-pion':
        return model['recoil_mass_gev']
    if model.get('decay_type') == 'charged-vector-dipion':
        return model['charged_pion_mass_gev']+model['neutral_pion_mass_gev']
    if dipion_channel(model):
        return 2 * model['pion_mass_gev']
    return model.get('recoil_mass_gev', 0.) if vector_parent(model) else 0.


def vector_parent(model):
    return model.get('decay_type', 'pseudoscalar-photon') == 'vector-pseudoscalar'


def production_mechanism(card):
    mechanism = card.get('mechanism', 'eta-etaprime-radiative')
    if mechanism not in ('eta-etaprime-radiative', 'meson-radiative'):
        raise ValueError('Unknown meson production mechanism: ' + str(mechanism))
    if mechanism == 'meson-radiative' and not card.get('channels'):
        raise ValueError('Unified radiative production requires an explicit exclusive channel inventory')
    if mechanism == 'eta-etaprime-radiative' and any(
            vector_parent(channel) or dipion_channel(channel) or kaon_channel(channel)
            for parent in card['parents'] for channel in exclusive_channels(parent)):
        raise ValueError('Vector and dipion channels require the explicitly named meson-radiative mechanism')
    return mechanism


def parent_branching_coefficient(parent_mass, signal_mass, model, br_mumu, *, native_precision=None):
    """One physical exclusive channel, with epsilon squared factored out."""
    if 'exclusive_channels' in model:
        return math.fsum(parent_branching_coefficient(parent_mass, signal_mass, channel, br_mumu,
                         native_precision=native_precision)
                         for channel in exclusive_channels(model))
    if kaon_channel(model):
        return _kaon_coefficient(parent_mass,signal_mass,model,br_mumu)
    if dipion_channel(model):
        context = native_precision if vector_dipion_channel(model) else None
        return dipion_kernel(model).branching_coefficient(parent_mass, signal_mass, model,
                                                          **(context or {})) * br_mumu
    if vector_parent(model):
        value = vector_meson.radiative_branching(parent_mass, model['recoil_mass_gev'],
            signal_mass, .001, model['br_p_gamma'], model['form_factor_abs2'])
    else:
        value = radiative_branching(parent_mass, signal_mass, .001,
            model['br_gamma_gamma'], math.sqrt(model['form_factor_abs2']))
    return value / 1.e-6 * br_mumu


def parent_form_factor_abs2(signal_mass, parent):
    function = (vector_meson.transition_form_factor_abs2 if vector_parent(parent)
                else transition_form_factor_abs2)
    return function(signal_mass, parent['transition_form_factor'])


def resolved_parent_models(card, configuration, mass, *, epsilon=None):
    """Resolve only explicitly declared exclusive channels at native support."""
    if card.get('mechanism') == 'meson-radiative':
        expected = [parent.get('channel') for parent in card['parents']]
        if (any(not isinstance(name, str) or not name for name in expected) or
                len(set(expected)) != len(expected) or card.get('channels') != expected):
            raise ValueError('Explicit channel names do not match the exclusive parent/recoil models')
    models = {}
    native = {row['pdg_id']: row for row in configuration['parents']}
    if len(native) != len(configuration['parents']):
        raise ValueError('Native parent species are repeated')
    for parent in card['parents']:
        pdg = parent['pdg_id']
        if parent.get('decay_type')=='weak-kaon-leptonic':
            raise ValueError('K_l2 source current/envelope dispatch awaits its separate qualification')
        if pdg in (130,310,321,-321) and not kaon_channel(parent):
            raise ValueError('Physical kaons require an explicitly qualified kaon current')
        if pdg not in native:
            raise ValueError('Each declared parent requires unique native mass support')
        minimum, maximum = native[pdg]['mass_min_gev'], native[pdg]['mass_max_gev']
        if not 0 < minimum <= maximum:
            raise ValueError('Generator parent mass support is not finite and bounded')
        if kaon_channel(parent):
            if configuration.get('check_event') is not True:
                raise ValueError('Kaon count authority requires the unchanged native event check')
            model=_resolved_kaon_model(parent,native[pdg],mass,epsilon,configuration['ecm_gev'])
            if pdg not in models:
                models[pdg]=model
            else:
                previous=models[pdg];channels=exclusive_channels(previous)
                if any(channel['decay_type']==model['decay_type'] for channel in channels):
                    raise ValueError('Duplicate exclusive kaon current for one physical parent')
                models[pdg]=dict(mass_min_gev=minimum,mass_max_gev=maximum,
                    exclusive_channels=channels+[model])
            continue
        recoil = recoil_threshold(parent)
        opened = maximum > mass + recoil
        form_factor = parent_form_factor_abs2(mass, parent) if opened and not dipion_channel(parent) else 1.
        model = dict(mass_min_gev=minimum, mass_max_gev=maximum,
            form_factor_abs2=form_factor,
            relative_model_uncertainty=model_precision_budget(parent, mass) if opened else 0.,
            kinematically_open=opened, provenance=dict(pdg=parent['pdg_reference'],
                transition=parent.get('transition_form_factor', parent.get('dipion_model',
                    parent.get('rho_radiative_model', parent.get('charged_rho_radiative_model', {}))))['reference']))
        model['channel'] = parent.get('channel', str(pdg) + '->gamma_Aprime')
        if vector_parent(parent):
            for key in ('decay_type', 'recoil_pdg_id', 'recoil_mass_gev', 'br_p_gamma',
                        'parent_polarization', 'parent_mass_model'):
                model[key] = parent[key]
            if (native[pdg].get('mass_width_gev') != 0. or minimum != parent['mass_gev'] or
                    maximum != parent['mass_gev']):
                raise ValueError('Vector pole branching model requires an unchanged zero-width native source')
        elif dipion_channel(parent):
            keys = (('decay_type', 'charged_pion_mass_gev', 'neutral_pion_mass_gev',
                     'br_pipi', 'charged_rho_radiative_model', 'recoil_pdg_ids',
                     'parent_polarization', 'parent_mass_model')
                    if parent.get('decay_type') == 'charged-vector-dipion' else
                    ('decay_type', 'pion_mass_gev', 'br_pipi', 'rho_radiative_model',
                     'parent_polarization', 'parent_mass_model')
                    if parent.get('decay_type') == 'vector-dipion' else
                    ('decay_type', 'pion_mass_gev', 'br_pipi_gamma', 'dipion_model'))
            for key in keys:
                model[key] = parent[key]
            if vector_dipion_channel(parent):
                model['relative_numerical_mass_precision_budget'] = model.pop('relative_model_uncertainty')
            if (vector_dipion_channel(parent) and
                    (native[pdg].get('mass_width_gev') != 0. or
                     minimum != parent['mass_gev'] or maximum != parent['mass_gev'])):
                raise ValueError('Vector dipion rates require an unchanged zero-width native source')
        else:
            model['br_gamma_gamma'] = parent['br_gamma_gamma']
        if pdg not in models:
            models[pdg] = model
        else:
            previous = models[pdg]
            channels = exclusive_channels(previous)
            identity = lambda row: (row.get('decay_type', 'pseudoscalar-photon'),
                                    row.get('recoil_pdg_id', 22))
            if any(identity(row) == identity(model) for row in channels):
                raise ValueError('Duplicate exclusive channel for one physical parent')
            models[pdg] = dict(mass_min_gev=minimum, mass_max_gev=maximum,
                exclusive_channels=channels + [model])
    if set(models) != set(native):
        raise ValueError('The source inventory includes undeclared parent channels')
    validate_models(models)
    return models


def model_precision_budget(parent, signal_mass):
    """Measured BR/TFF one-sigma parameter-box envelope, not a tuned goal."""
    if vector_dipion_channel(parent):
        # This is a numerical-accuracy target, separate from the experimental
        # photon check and from unknown timelike hadronic-model uncertainties.
        return dipion_kernel(parent).NATIVE_KINEMATIC_RATE_PRECISION_LIMIT
    if dipion_channel(parent):
        normalization = parent['dipion_model']['normalization']
        return math.hypot(normalization['branching_fraction_stat'],
                          normalization['branching_fraction_syst']) / normalization['branching_fraction']
    transition = parent['transition_form_factor']
    if vector_parent(parent):
        if transition['kind'] != 'single-pole-slope':
            central = parent['br_p_gamma'] * parent_form_factor_abs2(signal_mass, parent)
            values = [branch * form for branch in
                (parent['br_p_gamma'] - parent['br_p_gamma_uncertainty'],
                 parent['br_p_gamma'] + parent['br_p_gamma_uncertainty'])
                for form in vector_meson.transition_form_factor_variations(signal_mass, transition)]
            return max(abs(value / central - 1) for value in values)
        central = parent['br_p_gamma'] * parent_form_factor_abs2(signal_mass, parent)
        error = math.hypot(transition['slope_stat_gev_minus2'], transition['slope_syst_gev_minus2'])
        values = []
        for branch_sign, slope_sign in product((-1, 1), repeat=2):
            branching = parent['br_p_gamma'] + branch_sign * parent['br_p_gamma_uncertainty']
            shifted = dict(transition, slope_gev_minus2=transition['slope_gev_minus2'] + slope_sign * error)
            values.append(branching * vector_meson.transition_form_factor_abs2(signal_mass, shifted))
        return max(abs(value / central - 1) for value in values)
    central = parent['br_gamma_gamma']*transition_form_factor_abs2(signal_mass, transition)
    lambdas = [transition['lambda_gev'] + sign*math.hypot(transition['lambda_stat_gev'],
               transition['lambda_syst_gev']) for sign in (-1, 1)]
    widths = ([transition['gamma_gev'] + sign*math.hypot(transition['gamma_stat_gev'],
               transition['gamma_syst_gev']) for sign in (-1, 1)]
              if 'gamma_gev' in transition else [None])
    branchings = [parent['br_gamma_gamma'] + sign*parent['br_gamma_gamma_uncertainty'] for sign in (-1, 1)]
    values = []
    for branching, pole, width in product(branchings, lambdas, widths):
        shifted = dict(transition, lambda_gev=pole)
        if width is not None:
            shifted['gamma_gev'] = width
        values.append(branching*transition_form_factor_abs2(signal_mass, shifted))
    return max(abs(value/central-1) for value in values)


def pseudoscalar_dipion_precision_report(parent, kinematic_mass, signal_mass, model, br_mumu):
    """Bound a tiny exceptional dipion contribution using its own parent.

    Keep the photon sibling's original isolated BR/TFF check. Only the dipion
    rate difference plus its entire nominal conditional-distribution weight
    gets the 0.1% same-parent correction budget. The parent's total L1 rate
    shift must also fit its original nominal-weighted BR/TFF envelope. This
    does not claim 0.1% accuracy for the photon sibling or the full parent.
    """
    channels = exclusive_channels(model)
    kinds = [row.get('decay_type', 'pseudoscalar-photon') for row in channels]
    native = parent['native_mass_gev']
    if (parent['pdg_id'] not in (221, 331) or len(channels) != 2
            or sorted(kinds) != ['pseudoscalar-dipion', 'pseudoscalar-photon']
            or model.get('mass_min_gev') != native or model.get('mass_max_gev') != native):
        raise ValueError('Exceptional dipion precision requires the two owned eta/eta-prime pole channels')
    rates = []
    for channel in channels:
        if ((kinematic_mass > signal_mass+recoil_threshold(channel)) !=
                (native > signal_mass+recoil_threshold(channel))):
            raise ValueError('Native precision changes whether an owned rare decay is open')
        nominal = parent_branching_coefficient(native, signal_mass, channel, br_mumu)
        shifted = parent_branching_coefficient(kinematic_mass, signal_mass, channel, br_mumu)
        budget = channel['relative_model_uncertainty']
        if not math.isfinite(budget) or budget < 0:
            raise ValueError('A finite original BR/TFF precision budget is required')
        relative = shifted/nominal-1 if nominal else 0.
        exceptional = abs(relative) > budget
        if exceptional and channel.get('decay_type') != 'pseudoscalar-dipion':
            raise ValueError('Photon sibling exceeds its unchanged isolated BR/TFF precision budget')
        rates.append(dict(channel=channel['channel'], nominal=nominal, kinematic=shifted,
                          relative_shift=relative, original_budget=budget, exceptional=exceptional))
    total = math.fsum(row['nominal'] for row in rates)
    if total <= 0:
        raise ValueError('A positive same-parent nominal rate is required')
    exceptional = [row for row in rates if row['exceptional']]
    correction = math.fsum(abs(row['kinematic']-row['nominal'])+row['nominal']
                          for row in exceptional)/total
    l1 = math.fsum(abs(row['kinematic']-row['nominal']) for row in rates)/total
    measured_budget = math.fsum(row['nominal']*row['original_budget'] for row in rates)/total
    if correction > PSEUDOSCALAR_DIPION_CORRECTION_LIMIT:
        raise ValueError('Exceptional dipion rate plus conditional-shape bound exceeds 0.1% of its own parent')
    if l1 > measured_budget:
        raise ValueError('Total parent rate shift exceeds its original weighted BR/TFF precision budget')
    return dict(schema='shift-pseudoscalar-native-dipion-precision-v1',
                parent_pdg_id=parent['pdg_id'], signal_mass_gev=signal_mass,
                native_parent_mass_gev=native, kinematic_parent_mass_gev=kinematic_mass,
                channels=rates, exceptional_dipion_rate_plus_shape_bound=correction,
                required_exceptional_dipion_bound=PSEUDOSCALAR_DIPION_CORRECTION_LIMIT,
                parent_l1_relative_rate_shift=l1, parent_original_weighted_model_budget=measured_budget,
                scope='0.1% applies only to the exceptional dipion contribution; sibling BR/TFF checks remain unchanged')


def resolve_parent_kinematics(parents, mass_tolerance, signal_mass, models, br_mumu):
    """Preserve native momenta and bookkept masses; never repair or drop one.

    Pythia ErrorChecks.xml defines Check:mTolErr as the mass deviation divided
    by max(E,1 GeV). Use the actual p4 invariant consistently in the radiative
    decay. The native generated mass defines the branching probability, as in
    native generator mass handling. Record its numerical effect explicitly.
    """
    if not math.isfinite(mass_tolerance) or mass_tolerance <= 0:
        raise ValueError('An explicit native Pythia mass tolerance is required')
    result = []
    kaons=[parent for parent in parents if parent['pdg_id'] in (130,310,321,-321)]
    if kaons:
        indexed={parent['index']:parent for parent in parents}
        if len(indexed)!=len(parents):raise ValueError('A physical source parent is repeated')
        if any(parent.get('source_total_p4')!=kaons[0].get('source_total_p4') for parent in kaons):
            raise ValueError('Physical kaon candidates do not belong to one native beam/collision')
        for parent in kaons:
            for mother in parent.get('mother_indices',[]):
                if mother in indexed and indexed[mother]['pdg_id']==parent['pdg_id']:
                    raise ValueError('Same-species kaon ancestor duplicates a physical final parent')
    for original in parents:
        parent = dict(original)
        parent.pop('native_parent_precision_report', None)
        p4, native = parent['p4'], parent['native_mass_gev']
        if (len(p4) != 4 or not all(math.isfinite(value) for value in p4) or p4[0] <= 0 or
                not math.isfinite(native) or native <= 0):
            raise ValueError('Invalid native parent mass or four-vector')
        squared = math.fsum((p4[0]**2, *(-value*value for value in p4[1:])))
        if squared <= 0:
            raise ValueError('Native parent four-vector is not timelike')
        mass = math.sqrt(squared)
        deviation = abs(mass-native)/max(p4[0], 1.)
        if deviation > mass_tolerance:
            raise ValueError('Native parent violates the unchanged Pythia mass tolerance')
        model = models[parent['pdg_id']]
        if _kaon_group(model):
            parent['kaon_source_count_authority']=_kaon_snapshot_authority(parent,model,mass)
            if native!=model['mass_min_gev'] or native!=model['mass_max_gev']:
                raise ValueError('Native kaon mass differs from its exact stable support')
        native_precision = dict(native_mass_gev=native, native_energy_gev=p4[0],
                                native_mass_tolerance=mass_tolerance)
        owned_rates = charged_rho_parent_rate_model(parent['pdg_id'], model)
        if owned_rates is not None:
            native_precision['native_parent_rate_model'] = owned_rates
        for channel in exclusive_channels(model):
            recoil = recoil_threshold(channel)
            if (mass > signal_mass + recoil) != (native > signal_mass + recoil):
                raise ValueError('Native precision changes whether the rare decay is open')
            nominal = parent_branching_coefficient(native, signal_mass, channel, br_mumu)
            shifted = parent_branching_coefficient(mass, signal_mass, channel, br_mumu,
                                                   native_precision=native_precision)
            relative = shifted / nominal - 1 if nominal else 0.
            budget = channel.get('relative_numerical_mass_precision_budget',
                                 channel.get('relative_model_uncertainty'))
            if abs(relative) > budget:
                if channel.get('decay_type') == 'pseudoscalar-dipion':
                    parent['native_parent_precision_report'] = pseudoscalar_dipion_precision_report(
                        parent, mass, signal_mass, model, br_mumu)
                    continue
                if channel.get('decay_type') != 'charged-vector-dipion' or owned_rates is None:
                    raise ValueError('Native mass precision effect exceeds the declared numerical or BR/TFF precision budget')
                # Near its endpoint this channel is tiny. Keep it, and retain
                # the independently checked bound on the SAME parent's full
                # contribution, including its entire conditional-shape weight.
                parent['native_parent_precision_report'] = dipion_kernel(channel).native_parent_precision_report(
                    mass, signal_mass, channel, **native_precision)
        coefficient = parent_branching_coefficient(native, signal_mass, model, br_mumu)
        kinematic = parent_branching_coefficient(mass, signal_mass, model, br_mumu,
                                                 native_precision=native_precision)
        relative = kinematic/coefficient-1 if coefficient else 0.
        parent.update(mass_gev=native, kinematic_mass_gev=mass, native_mass_shell_deviation=deviation,
                      native_mass_tolerance=mass_tolerance,
                      coefficient_relative_precision_shift=relative,
                      coefficient_difference_from_kinematic_mass=kinematic-coefficient)
        result.append(parent)
    kaon_proofs=[parent['kaon_source_count_authority'] for parent in result
                 if 'kaon_source_count_authority' in parent]
    if kaon_proofs:
        # These final candidates form an antichain. Their independent energy
        # lower bounds must fit inside this same collision's COM energy. A
        # violation stops the source; never clip a probability or drop a row.
        lower=math.fsum(proof['independent_com_energy_interval_gev'][0] for proof in kaon_proofs)
        upper=min(proof['independent_source_ecm_interval_gev'][1] for proof in kaon_proofs)
        if lower>upper:
            raise ValueError('Final-kaon COM energies exceed the same physical collision energy')
    return result


def validate_models(models):
    if not models or set(models) - set(PARENTS):
        raise ValueError('Supply explicit supported exclusive meson parent model cards')
    for pdg, model in models.items():
        if pdg in (130,310,321,-321) and not all(kaon_channel(c) for c in exclusive_channels(model)):
            raise ValueError('Physical kaons require an explicitly qualified kaon current')
        if 'exclusive_channels' in model:
            channels = model['exclusive_channels']
            if not isinstance(channels, list) or len(channels) < 2:
                raise ValueError('A parent channel group requires several explicit channels')
            names = [row.get('channel') for row in channels]
            if any(not name for name in names) or len(set(names)) != len(names):
                raise ValueError('Exclusive channel names must be unique for a physical parent')
            if any(kaon_channel(channel) for channel in channels):
                shared=('mean_proper_ctau_mm','total_parent_width_gev','source_ecm_gev',
                        'physical_epsilon','em_mixing_over_epsilon_abs2','native_parent_count_bound')
                if (not all(kaon_channel(channel) for channel in channels)
                        or any(channel.get(key)!=channels[0].get(key)
                               for channel in channels for key in shared)):
                    raise ValueError('Kaon siblings require one lifetime, coupling and source-count authority')
            for channel in channels:
                if any(channel[key] != model[key] for key in ('mass_min_gev', 'mass_max_gev')):
                    raise ValueError('Channels of one physical parent must share its native support')
                validate_models({pdg: channel})
            continue
        if not model.get('provenance'):
            raise ValueError('Parent branching/form-factor provenance is required')
        if kaon_channel(model):
            _validate_kaon_model(pdg,model)
            continue
        if model.get('decay_type', 'pseudoscalar-photon') not in ('pseudoscalar-photon', 'vector-pseudoscalar', 'pseudoscalar-dipion', 'vector-dipion', 'charged-vector-dipion'):
            raise ValueError('Unsupported exclusive radiative decay type')
        branching_key = ('br_pipi' if vector_dipion_channel(model) else
                         'br_pipi_gamma' if dipion_channel(model) else
                         'br_p_gamma' if vector_parent(model) else 'br_gamma_gamma')
        for key in ('mass_min_gev', branching_key, 'form_factor_abs2'):
            value = model.get(key)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Finite explicit parent input required: ' + key)
        if model['mass_min_gev'] <= 0 or not 0 < model[branching_key] <= 1 or model['form_factor_abs2'] <= 0:
            raise ValueError('Invalid parent mass, branching fraction or form factor')
        if vector_parent(model):
            recoils = {113: (111,221), 213: (211,), -213: (-211,),
                       223: (111,221), 333: (111,221), 313: (311,), -313: (-311,),
                       323: (321,), -323: (-321,)}
            if (abs(pdg) not in (113, 213, 223, 333, 313, 323) or model.get('parent_polarization') != 'unpolarized' or
                    model.get('parent_mass_model') != 'zero-width-pole' or
                    type(model.get('recoil_pdg_id')) is not int or abs(model['recoil_pdg_id']) not in (111, 211, 221, 311, 321) or
                    not math.isfinite(model.get('recoil_mass_gev', float('nan'))) or model['recoil_mass_gev'] <= 0):
                raise ValueError('Require an explicit narrow-width unpolarized vector and physical pseudoscalar recoil')
            if model['recoil_pdg_id'] not in recoils[pdg]:
                raise ValueError('Unsupported exclusive vector transition or charge-conjugate assignment')
            if model.get('mass_max_gev') != model['mass_min_gev']:
                raise ValueError('Pole branching fractions require an explicitly zero-width vector source')
        elif model.get('decay_type') == 'charged-vector-dipion':
            if (abs(pdg) != 213 or model.get('parent_polarization') != 'unpolarized' or
                    model.get('parent_mass_model') != 'zero-width-pole' or
                    model.get('mass_max_gev') != model['mass_min_gev'] or
                    model.get('recoil_pdg_ids') != [211 if pdg > 0 else -211, 111]):
                raise ValueError('Charged rho dipion radiation requires its explicit pole, spin and charged-pion/pi0 assignment')
            inputs = model['charged_rho_radiative_model']
            if (model['charged_pion_mass_gev'] != inputs['charged_pion_mass_gev'] or
                    model['neutral_pion_mass_gev'] != inputs['neutral_pion_mass_gev'] or
                    model['mass_min_gev'] != inputs['reference_parent_mass_gev'] or
                    model['br_pipi'] != inputs['normalization']['branching_fraction']):
                raise ValueError('Charged rho dipion radiation differs from its explicit unequal masses or rate normalization')
            dipion_kernel(model).branching_coefficient(model['mass_min_gev'], .22, model)
        elif model.get('decay_type') == 'vector-dipion':
            if (pdg != 113 or model.get('parent_polarization') != 'unpolarized' or
                    model.get('parent_mass_model') != 'zero-width-pole' or
                    model.get('mass_max_gev') != model['mass_min_gev']):
                raise ValueError('Rho dipion radiation requires an explicit unpolarized neutral pole source')
            inputs = model['rho_radiative_model']
            if (model['pion_mass_gev'] != inputs['pion_mass_gev'] or
                    model['mass_min_gev'] != inputs['reference_parent_mass_gev'] or
                    model['br_pipi'] != inputs['normalization']['branching_fraction']):
                raise ValueError('Rho dipion radiation differs from its explicit mass or rate normalization')
            dipion_kernel(model).branching_coefficient(model['mass_min_gev'], .22, model)
        elif pdg not in (221, 331):
            raise ValueError('Pseudoscalar photon channel has an unsupported parent')
        if ('mass_max_gev' in model and (not math.isfinite(model['mass_max_gev']) or
                                       model['mass_max_gev'] < model['mass_min_gev'])):
            raise ValueError('Invalid generator-supported parent mass interval')


def coefficient_bound(ecm_gev, br_mumu, models, mass_gev=None):
    """Analytic bound on the sum of exclusive radiative probabilities / epsilon^2.

    Each species' physical parent count is at most floor(Ecm/mass_min).
    Separate species bounds allow eta mesons produced by eta-prime decays.
    The phase-space suppression is at most one. No empirical maximum or
    favorable clipping is used to accept a source collision.
    """
    validate_models(models)
    if not math.isfinite(ecm_gev) or ecm_gev <= 0 or not math.isfinite(br_mumu) or not 0 < br_mumu <= 1:
        raise ValueError('Positive source energy and physical dimuon BR are required')
    if mass_gev is not None and (not math.isfinite(mass_gev) or mass_gev <= 2*MUON_MASS_GEV):
        raise ValueError('An open physical dimuon mass is required')
    terms = []
    for model in (channel for group in models.values() for channel in exclusive_channels(group)):
        if kaon_channel(model):
            if mass_gev is None:raise ValueError('A fixed signal mass is required for kaon proposal bounds')
            if ecm_gev!=model['source_ecm_gev']:
                raise ValueError('Kaon count envelope belongs to another native source energy')
            terms.append(model['native_parent_count_bound']*parent_branching_coefficient(
                model['mass_min_gev'],mass_gev,model,br_mumu))
            continue
        if dipion_channel(model):
            if mass_gev is None:
                raise ValueError('Dipion proposal bounds require a fixed signal mass')
            # The narrow eta species have identical min/max support. A broad
            # dipion source needs a separately established envelope.
            if model['mass_min_gev'] != model['mass_max_gev']:
                raise ValueError('Dipion proposal requires an explicit narrow parent source')
            value = parent_branching_coefficient(model['mass_max_gev'], mass_gev, model, br_mumu)
            terms.append(math.floor(ecm_gev/model['mass_min_gev']) * value)
            continue
        if vector_parent(model):
            if mass_gev is None:
                phase = model['br_p_gamma'] * model['form_factor_abs2'] * br_mumu
            else:
                phase = parent_branching_coefficient(model['mass_max_gev'], mass_gev, model, br_mumu)
            terms.append(math.floor(ecm_gev/model['mass_min_gev']) * phase)
            continue
        phase = 1.
        if mass_gev is not None and 'mass_max_gev' in model:
            phase = max(0., 1-(mass_gev/model['mass_max_gev'])**2)**3
        terms.append(math.floor(ecm_gev/model['mass_min_gev']) *
                     2*model['br_gamma_gamma']*model['form_factor_abs2']*br_mumu*phase)
    return math.fsum(terms)


def choose_collision(parents, mass_gev, br_mumu, models, bound, rng, native_weight=1.):
    """Accept a whole source collision, then select exactly one physical parent.

    The acceptance uses epsilon-factored probabilities to avoid underflow.
    Accepted candidate genWeight is one. Physical epsilon^2 is applied once
    when the full-source cross section is calculated, not to genWeight.
    """
    validate_models(models)
    if type(native_weight) not in (int, float) or native_weight != 1.:
        raise ValueError('Unit-weight parent production is required by this unweighting contract')
    if not math.isfinite(mass_gev) or mass_gev <= 2*MUON_MASS_GEV:
        raise ValueError('An open physical dimuon mass is required')
    if not math.isfinite(bound) or bound <= 0 or not math.isfinite(br_mumu) or not 0 < br_mumu <= 1:
        raise ValueError('Invalid analytic unweighting bound or dimuon BR')
    identifiers = set()
    kaon_counts = {}
    weights = []
    for parent in parents:
        identity, pdg = parent['index'], parent['pdg_id']
        if type(identity) is not int or identity <= 0 or identity in identifiers:
            raise ValueError('A physical parent is repeated in the collision inventory')
        identifiers.add(identity)
        if pdg not in models:
            raise ValueError('Parent species has no explicit model card')
        model = models[pdg]
        if _kaon_group(model):
            squared=math.fsum([parent['p4'][0]**2]+[-x*x for x in parent['p4'][1:]])
            if (squared<=0 or parent.get('kaon_source_count_authority')!=
                    _kaon_snapshot_authority(parent,model,math.sqrt(squared))):
                raise ValueError('A kaon candidate lacks its native final-parent count proof')
            kaon_counts[pdg]=kaon_counts.get(pdg,0)+1
            limit=next(c['native_parent_count_bound'] for c in exclusive_channels(model) if kaon_channel(c))
            if kaon_counts[pdg]>limit:
                raise ValueError('Native physical-kaon count violates the deterministic source envelope')
        parent_mass = parent['mass_gev']
        if not math.isfinite(parent_mass) or parent_mass < model['mass_min_gev']:
            raise ValueError('Generated parent mass violates the analytic source bound')
        if parent_mass > model.get('mass_max_gev', math.inf):
            raise ValueError('Generated parent mass violates the analytic phase-space bound')
        # epsilon=1 is not a physical parameter passed to radiative_branching:
        # divide a safely small reference epsilon back out of its exact law.
        for channel in exclusive_channels(model):
            value = parent_branching_coefficient(parent_mass, mass_gev, channel, br_mumu)
            weights.append((parent, channel, value))
    total = math.fsum(value for _, _, value in weights)
    if total > bound*(1+1.e-12):
        raise ValueError('Analytic source bound is violated; refusing biased clipping')
    probability = total/bound
    result = dict(parent_count=len(parents), coefficient_sum=total,
                  acceptance_probability=probability, accepted=False, parent_index=None)
    if total == 0 or rng.random() >= probability:
        return result
    draw = rng.random()*total
    cumulative = 0.
    for parent, channel, weight in weights:
        cumulative += weight
        if weight > 0 and draw < cumulative:
            result.update(accepted=True, parent_index=parent['index'], gen_weight=1.)
            if channel.get('channel'):
                result['selected_channel'] = channel['channel']
            return result
    raise RuntimeError('Weighted parent selection failed despite a finite positive sum')


def source_cross_section_pb(sigma_source_mb, epsilon, coefficient_sum, source_collisions):
    """Source sigma times the all-collision mean rare probability, BRs included."""
    if (not all(math.isfinite(value) for value in (sigma_source_mb, epsilon, coefficient_sum)) or
            sigma_source_mb <= 0 or not 0 < epsilon <= .1 or coefficient_sum < 0 or
            type(source_collisions) is not int or source_collisions <= 0):
        raise ValueError('Invalid complete source exposure or physical cross section')
    return sigma_source_mb*1.e9*epsilon**2*coefficient_sum/source_collisions
