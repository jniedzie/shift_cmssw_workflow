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
import json
from functools import lru_cache
from itertools import product
from pathlib import Path

from dark_photon_meson import MUON_MASS_GEV, radiative_branching, transition_form_factor_abs2
import dark_photon_vector_meson as vector_meson

PARENTS = (221, 331, 113, 213, -213, 223, 333, 313, -313, 323, -323,
           130, 310, 321, -321, 441, 10441, 20443, 445, 10443, 100443)
PSEUDOSCALAR_DIPION_CORRECTION_LIMIT = 1.e-3
FULL_SOURCE_NATIVE_PRECISION_POLICY = dict(
    kind='full-fixed-source-conservative-parent-measure-v1',required_relative_bound=1.e-3)


def validate_native_precision_policy(policy):
    """The optional full-source guard has one fixed, explicit numerical target."""
    if policy is None:return False
    if (not isinstance(policy,dict) or policy!=FULL_SOURCE_NATIVE_PRECISION_POLICY or
            type(policy.get('required_relative_bound')) not in (int,float)):
        raise ValueError('Require the exact frozen full-source native-precision policy')
    return True


def deferred_native_precision_measure(parents,br_mumu,policy):
    """Sum conservative exceptional-parent measures, with BRμμ applied once."""
    enabled=validate_native_precision_policy(policy)
    if not math.isfinite(br_mumu) or not 0<=br_mumu<=1:raise ValueError('Physical dimuon branching required')
    terms=[];count=0
    for parent in parents:
        reports=[parent.get('native_parent_precision_report',{})]+parent.get('native_channel_precision_reports',[])
        exceptional=False
        for report in reports:
            if report.get('deferred_full_source_check') is not True:continue
            value=report.get('conservative_absolute_parent_measure_bound')
            if (not enabled or report.get('scope')!='deferred-full-source' or
                    type(value) not in (int,float) or not math.isfinite(value) or value<0 or
                    type(report.get('nominal_parent_coefficient')) not in (int,float) or
                    not math.isfinite(report['nominal_parent_coefficient']) or report['nominal_parent_coefficient']<0):
                raise ValueError('Invalid deferred native-parent measure or undeclared full-source policy')
            terms.append(value*br_mumu);exceptional=True
        count+=int(exceptional)
    return math.fsum(terms),count


def full_source_native_precision_summary(policy,coefficient_sum,measure_sum,exceptional_parents):
    """Completion gate over every source collision; never clips a contribution."""
    if not validate_native_precision_policy(policy):raise ValueError('Full-source policy must be explicit')
    if (any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in (coefficient_sum,measure_sum)) or
            type(exceptional_parents) is not int or exceptional_parents<0):
        raise ValueError('Finite nonnegative full-source precision accounting required')
    relative=measure_sum/coefficient_sum if coefficient_sum else (0. if measure_sum==0 else math.inf)
    passed=math.isfinite(relative) and relative<=policy['required_relative_bound']
    return dict(schema='shift-meson-full-source-native-precision-v1',
        full_source_coefficient_sum=coefficient_sum,
        conservative_absolute_measure_bound_sum=measure_sum,relative_bound=relative,
        required_relative_bound=policy['required_relative_bound'],exceptional_parent_count=exceptional_parents,
        complete=passed)


def exclusive_channels(model):
    """Channels share one physical parent inventory, not separate collisions."""
    return model.get('exclusive_channels', [model])


def kaon_channel(model):
    """Qualified fixed-native-mass kaon electromagnetic current families."""
    return model.get('decay_type') in ('neutral-kaon-photon','weak-kaon-pion','weak-kaon-leptonic',
                                      'weak-kaon-pion-photon','neutral-kaon-dipion')


def _kaon_group(model):
    return any(kaon_channel(channel) for channel in exclusive_channels(model))


def _kaon_mass_support(pdg,reference,support,authorities):
    """Finite native bookkeeping masses, independently pinned to table rows.

    A neutral flavor311 record can become130/310 without replacing its mass.
    Those two initialized table values are distinct authorities, not floating
    point roundoff or a continuous physical line shape.
    """
    if (not isinstance(support,list) or not support or
            any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in support)
            or support!=sorted(set(support)) or reference not in support):
        raise ValueError('Declare a finite sorted unique native kaon mass support')
    if pdg not in (130,310):
        if support!=[reference]:raise ValueError('Charged kaon native support must remain its exact table mass')
        return support,[]
    if not isinstance(authorities,list):
        raise ValueError('Neutral kaon support requires initialized physical/flavor mass authorities')
    rows=[row for row in authorities if row.get('pdg_id') in (pdg,311)]
    if (len(rows)!=2 or {row['pdg_id'] for row in rows}!={pdg,311} or
            any(type(row.get('mass_gev')) not in (int,float) or not math.isfinite(row['mass_gev'])
                or row['mass_gev']<=0 or row.get('mass_min_gev')!=row['mass_gev']
                or row.get('mass_max_gev')!=row['mass_gev'] or row.get('mass_width_gev')!=0.
                for row in rows) or
            next(row['mass_gev'] for row in rows if row['pdg_id']==pdg)!=reference or
            support!=sorted({row['mass_gev'] for row in rows})):
        raise ValueError('Neutral native mass support must equal its zero-width physical/flavor table union')
    return support,[dict(row) for row in sorted(rows,key=lambda row:row['pdg_id'])]


@lru_cache(maxsize=128)
def _leptonic_rate_benchmark(current_json,signal_mass,order,check_order):
    """One immutable O(p4) numeric rate authority, shared by event C and B.

    The refined quadrature value defines this benchmark's fixed-M0 rate.
    Order comparison is a convergence diagnostic, not a rigorous error bound
    on integration or on unknown higher chiral orders. The reference coupling
    factors out exactly and does not alter any physical epsilon or lifetime.
    """
    from dark_photon_kaon_leptonic import decay_width,form_factors
    current=json.loads(current_json)
    form_factors(0.,(current['parent_mass_gev']**2+current['lepton_mass_gev']**2)/2,current)
    coarse=decay_width(current,signal_mass,epsilon=.001,quadrature_order=order)['total']/1.e-6
    refined=decay_width(current,signal_mass,epsilon=.001,quadrature_order=check_order)['total']/1.e-6
    if any(not math.isfinite(x) or x<0 for x in (coarse,refined)):
        raise ValueError('Finite nonnegative full IB+SD K_l2 rates required')
    difference=abs(coarse/refined-1) if refined else (0. if coarse==0 else math.inf)
    return coarse,refined,difference


def _leptonic_rate_settings(current,settings):
    if (not isinstance(settings,dict) or
            any(type(settings.get(key)) is not int for key in ('order','check_order')) or
            not 8<=settings['order']<settings['check_order']<=128 or
            type(settings.get('relative_convergence_tolerance')) not in (int,float) or
            not math.isfinite(settings['relative_convergence_tolerance']) or
            not 0<settings['relative_convergence_tolerance']<=1.e-3):
        raise ValueError('Pin K_l2 quadrature orders and a separate numeric convergence tolerance')
    return json.dumps(current,sort_keys=True,separators=(',',':'),allow_nan=False)


def _leptonic_mass_gate(parent_mass,signal_mass,model,*,defer_to_full_source=False):
    """Separate numerical p4/endpoint guard; never infer shape closure from C."""
    nominal=model['mass_min_gev'];settings=model['native_kinematic_precision']
    delta=abs(parent_mass-nominal)
    gap=abs(nominal-signal_mass-model['charged_lepton_mass_gev'])
    mass_shift=delta/nominal
    endpoint_shift=delta/gap if gap else (0. if delta==0 else math.inf)
    threshold=signal_mass+model['charged_lepton_mass_gev']
    if (parent_mass>threshold)!=(nominal>threshold):
        raise ValueError('Native precision changes whether the leptonic-kaon decay is open')
    closed=nominal<=threshold
    exceptional=(not closed and (mass_shift>settings['relative_mass_limit'] or
                 endpoint_shift>settings['relative_endpoint_limit']))
    if type(defer_to_full_source) is not bool:
        raise ValueError('Full-source deferral requires an explicit boolean')
    if exceptional and not defer_to_full_source:
        raise ValueError('K_l2 native/invariant mass or endpoint numerical precision gate failed')
    report=dict(channel=model['channel'],coefficient_authority='fixed-native-M0',
        conditional_sampler_mass_authority='unchanged-p4-invariant',
        relative_mass_shift=mass_shift,relative_endpoint_shift=endpoint_shift,
        limits=dict(settings),scope='Numerical mass/endpoint gate; not a universal conditional-shape error bound')
    if exceptional:
        coefficient=(model['leptonic_width_over_em_coupling_squared_gev']/model['total_parent_width_gev']
                     *model['em_mixing_over_epsilon_abs2'])
        report.update(deferred_full_source_check=True,scope='deferred-full-source',
            nominal_parent_coefficient=coefficient,
            conservative_absolute_parent_measure_bound=2*coefficient,
            bound_scope='Entire nominal-weight conditional channel; BR mumu applied once by full-source accounting')
    if closed:report['scope']='unchanged-closed-channel-zero-measure'
    return report


def _kaon_coefficient(parent_mass,signal_mass,model,br_mumu,*,native_precision=None):
    """Fixed on-shell current; invariant-mass calls only check numerical drift."""
    if signal_mass != model['signal_mass_gev']:
        raise ValueError('Resolved kaon current belongs to another fixed signal mass')
    if (not math.isfinite(parent_mass) or parent_mass<=0 or not math.isfinite(br_mumu)
            or not 0<=br_mumu<=1):
        raise ValueError('Physical kaon mass and separately supplied dimuon branching required')
    if model['decay_type']=='weak-kaon-leptonic':
        _leptonic_mass_gate(parent_mass,signal_mass,model,
            defer_to_full_source=(native_precision or {}).get('defer_to_full_source',False))
        width=model['leptonic_width_over_em_coupling_squared_gev']
        return width/model['total_parent_width_gev']*model['em_mixing_over_epsilon_abs2']*br_mumu
    if signal_mass+recoil_threshold(model) >= parent_mass:
        return 0.
    if model['decay_type']=='neutral-kaon-dipion':
        from dark_photon_weak_meson import neutral_dipion_branching_coefficient
        return (neutral_dipion_branching_coefficient(parent_mass,signal_mass,model,
                    native_energy_gev=(native_precision or {}).get('native_energy_gev'))
                *model['em_mixing_over_epsilon_abs2']*br_mumu)
    if model['decay_type']=='weak-kaon-pion-photon':
        coefficient=_weak_radiative_coefficient(parent_mass,signal_mass,
            json.dumps(dict(weak_radiative_model=model['weak_radiative_model'],
                total_parent_width_gev=model['total_parent_width_gev']),sort_keys=True),
            (native_precision or {}).get('native_energy_gev'))
        return coefficient*model['em_mixing_over_epsilon_abs2']*br_mumu
    if model['decay_type']=='neutral-kaon-photon':
        width = (parent_mass**3/(32*math.pi)*(1-(signal_mass/parent_mass)**2)**3
                 *model['radiative_amplitude_abs2_gev_minus2'])
    else:
        from dark_photon_weak_meson import decay_width
        form=_kaon_weak_form_factor(parent_mass,model['recoil_mass_gev'],signal_mass,
            model['weak_form_factor_model'])
        width=decay_width(parent_mass,model['recoil_mass_gev'],signal_mass,.001,
            model['alpha_em'],form)/1.e-6
    return width/model['total_parent_width_gev']*model['em_mixing_over_epsilon_abs2']*br_mumu


@lru_cache(maxsize=512)
def _weak_radiative_coefficient(parent_mass,signal_mass,inputs,native_energy_gev=None):
    from dark_photon_weak_radiative import branching_coefficient
    return branching_coefficient(parent_mass,signal_mass,json.loads(inputs),native_energy_gev=native_energy_gev)


def _kaon_weak_form_factor(parent_mass,recoil_mass,signal_mass,current):
    """Evaluate the unchanged fit, restricting its domain to this endpoint.

    A declared native bookkeeping mass need not equal the publication's
    reference mass. Shrinking the fit domain changes no coefficient and
    never extends it beyond the published range.
    """
    from dark_photon_weak_meson import chiral_form_factor,kl_cpv_form_factor
    domain=current['valid_mass_range_gev']
    restricted=dict(current,valid_mass_range_gev=[domain[0],min(domain[1],parent_mass-recoil_mass)])
    if current.get('kind')=='hypercharge-kl-pi0-cpv':
        return kl_cpv_form_factor(parent_mass,recoil_mass,signal_mass,restricted)
    return chiral_form_factor(parent_mass,recoil_mass,signal_mass,restricted)


def _resolved_kaon_model(parent,native,signal_mass,epsilon,ecm,model_directory=None,mass_authorities=None):
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
    support,authorities=_kaon_mass_support(pdg,mass,
        parent.get('native_record_mass_support_gev',[mass]),mass_authorities)
    if pdg in (130,310) and not parent.get('native_record_mass_support_reference'):
        raise ValueError('Declare the physical/flavor native mass support provenance')
    mean=parent.get('mean_proper_ctau_mm');budget=parent.get('relative_numerical_mass_precision_budget')
    if (type(mean) not in (int,float) or not math.isfinite(mean) or mean<=0
            or not parent.get('lifetime_reference') or not parent.get('pdg_reference')
            or type(budget) not in (int,float) or not math.isfinite(budget) or not 0<budget<1
            or not math.isfinite(ecm) or ecm<=mass):
        raise ValueError('Pin kaon lifetime, native mass authority and numerical precision budget')
    width=HBAR_C_GEV_MM/mean
    model=dict(decay_type=parent['decay_type'],mass_min_gev=min(support),mass_max_gev=max(support),
        reference_parent_mass_gev=mass,native_record_mass_support_gev=list(support),
        native_record_mass_authorities=authorities,
        parent_mass_model='native-stable-kaon',signal_mass_gev=signal_mass,
        mean_proper_ctau_mm=mean,total_parent_width_gev=width,
        relative_numerical_mass_precision_budget=budget,source_ecm_gev=ecm,
        native_parent_count_bound=math.floor(ecm/min(support)),
        em_mixing_over_epsilon_abs2=(mixing_parameters(signal_mass,epsilon).em_coefficient/epsilon)**2,
        physical_epsilon=epsilon,channel=parent['channel'],provenance=dict(
            pdg=parent['pdg_reference'],lifetime=parent['lifetime_reference'],
            native_mass_support=parent.get('native_record_mass_support_reference',parent['pdg_reference'])))
    if parent['decay_type']=='neutral-kaon-photon':
        if pdg not in (130,310):raise ValueError('Only physical KL/KS have the qualified gamma current')
        opened=signal_mass<max(support)
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
            path=Path(authority['path'])
            if not path.is_absolute():
                if model_directory is None:
                    raise ValueError('A relative KS reference requires the explicit model directory')
                path=Path(model_directory)/path
            reference=KSDispersiveReference(path,authority['sha256'])
            absolute=abs(reference.amplitude(signal_mass))**2 if opened else 0.
            model['provenance']['transition']=dict(authority,publication=reference.publication)
        model.update(recoil_pdg_id=22,recoil_mass_gev=0.,radiative_amplitude_abs2_gev_minus2=absolute)
    elif parent['decay_type']=='weak-kaon-pion':
        expected={321:211,-321:-211,310:111,130:111}
        if (pdg not in expected or parent.get('recoil_pdg_id')!=expected[pdg]
                or type(parent.get('recoil_mass_gev')) not in (int,float)
                or not 0<parent['recoil_mass_gev']<mass):
            raise ValueError('Qualified Kpi current requires its explicit charge and recoil mass')
        weak=dict(parent['weak_form_factor_model']);alpha=parent.get('alpha_em')
        kind='hypercharge-kl-pi0-cpv' if pdg==130 else 'chpt-p6-kaon-em-vector'
        if (weak.get('kind')!=kind or not weak.get('reference')
                or pdg!=130 and not weak.get('loop_reference') or weak.get('parent_pdg_id')!=pdg
                or type(alpha) not in (int,float) or not math.isfinite(alpha) or alpha<=0):
            raise ValueError('Require the matching kaon weak-current model and EM convention')
        if pdg==130:
            from dark_photon_model import ElectroweakInputs
            if ElectroweakInputs(**weak.get('electroweak_inputs',{}))!=ElectroweakInputs():
                raise ValueError('KL matched current and shared source must use the same electroweak convention')
            for key,value in (('physical_epsilon',epsilon),('signal_mass_gev',signal_mass)):
                if weak.get(key) is not None and weak[key]!=value:
                    raise ValueError('KL CPV current belongs to a different physical signal point')
                weak[key]=value
            indirect=weak.get('indirect_ks_form_factor_model')
            if indirect is not None and signal_mass<mass-parent['recoil_mass_gev']:
                if indirect.get('parent_pdg_id')!=310 or indirect.get('kind')!='chpt-p6-kaon-em-vector':
                    raise ValueError('KL indirect CPV requires the separately attributed KS pion current')
                form=_kaon_weak_form_factor(mass,parent['recoil_mass_gev'],signal_mass,indirect)
                evaluated=[form.real,form.imag]
                if weak.get('indirect_ks_form_factor_at_signal_mass') not in (None,evaluated):
                    raise ValueError('KL indirect CPV scalar differs from its declared KS current')
                weak['indirect_ks_form_factor_at_signal_mass']=evaluated
        opened=signal_mass<max(support)-parent['recoil_mass_gev']
        form=_kaon_weak_form_factor(mass,parent['recoil_mass_gev'],signal_mass,weak) if signal_mass<mass-parent['recoil_mass_gev'] else 0j
        model.update(recoil_pdg_id=expected[pdg],recoil_mass_gev=parent['recoil_mass_gev'],
            weak_form_factor=[form.real,form.imag],weak_form_factor_model=dict(weak),alpha_em=alpha)
        model['provenance']['transition']=weak
    elif parent['decay_type']=='neutral-kaon-dipion':
        current=json.loads(json.dumps(parent.get('neutral_kaon_dipion_model',{}),allow_nan=False))
        pion=current.get('pion_mass_gev')
        if (pdg not in (130,310) or current.get('parent_pdg_id')!=pdg
                or current.get('reference_parent_mass_gev')!=mass
                or current.get('total_width_gev')!=width
                or current.get('kind')!='neutral-kaon-dipion-finite-current'
                or type(pion) not in (int,float) or not 0<2*pion<mass
                or parent.get('recoil_pdg_ids')!=[111,111]
                or parent.get('recoil_masses_gev')!=[pion,pion]):
            raise ValueError('Require an attributed KL/KS neutral-dipion current with the same native pole and lifetime')
        current['pole_mass_numerical_tolerance_gev']=max(abs(value-mass) for value in support)
        model.update(neutral_kaon_dipion_model=current,recoil_pdg_ids=[111,111],recoil_masses_gev=[pion,pion])
        opened=signal_mass<max(support)-2*pion
        model['provenance']['transition']=current
    elif parent['decay_type']=='weak-kaon-pion-photon':
        current=json.loads(json.dumps(parent.get('weak_radiative_model',{}),allow_nan=False))
        recoil=parent.get('recoil_mass_gev')
        if (pdg!=130 or parent.get('recoil_pdg_id')!=111
                or type(recoil) not in (int,float) or not 0<recoil<mass
                or current.get('parent_pdg_id')!=130
                or current.get('parent_mass_gev')!=mass
                or current.get('recoil_pion_mass_gev')!=recoil or not current.get('reference')):
            raise ValueError('Require the separately sourced KL pi0 photon current and native masses')
        # Only the independently certified native table masses enlarge the
        # pole allowance; the subsequent rate-drift gate remains unchanged.
        current['pole_mass_numerical_tolerance_gev']=max(abs(value-mass) for value in support)
        opened=signal_mass<max(support)-recoil
        model.update(recoil_pdg_id=111,recoil_mass_gev=recoil,weak_radiative_model=current)
        model['provenance']['transition']=current
    else:
        lepton=parent.get('charged_lepton_pdg_id');neutrino=parent.get('neutrino_pdg_id')
        current=parent.get('leptonic_form_factor_model',{})
        expected_mass={11:.00051099895,13:MUON_MASS_GEV}.get(abs(lepton) if type(lepton) is int else 0)
        sign=1 if pdg>0 else -1
        precision=parent.get('native_kinematic_precision',{})
        if (abs(pdg)!=321 or expected_mass is None or type(neutrino) is not int or lepton*sign>=0 or
                neutrino!=sign*(abs(lepton)+1) or
                current.get('parent_pdg_id')!=321 or current.get('parent_mass_gev')!=mass or
                current.get('lepton_mass_gev')!=expected_mass or
                current.get('kind')!='chpt-p4-charged-kaon-leptonic-vector' or
                current.get('order')!='p4-one-loop' or
                any(type(precision.get(key)) not in (int,float) or not math.isfinite(precision[key])
                    or not 0<precision[key]<=1.e-5 for key in ('relative_mass_limit','relative_endpoint_limit'))):
            raise ValueError('K_l2 requires its charged-kaon flavor/current and separate numerical mass/endpoint gates')
        current=json.loads(json.dumps(current,allow_nan=False))
        settings=dict(parent.get('leptonic_rate_quadrature',{}))
        key=_leptonic_rate_settings(current,settings)
        coarse,refined,difference=_leptonic_rate_benchmark(key,signal_mass,settings['order'],settings['check_order'])
        if difference>settings['relative_convergence_tolerance']:
            raise ValueError('K_l2 fixed-mass rate failed its declared quadrature convergence benchmark')
        opened=signal_mass<mass-expected_mass
        model.update(charged_lepton_pdg_id=lepton,neutrino_pdg_id=neutrino,
            charged_lepton_mass_gev=expected_mass,recoil_pdg_ids=[lepton,neutrino],
            recoil_masses_gev=[expected_mass,0.],leptonic_form_factor_model=current,
            native_kinematic_precision=dict(precision),
            leptonic_width_over_em_coupling_squared_gev=refined,
            leptonic_rate_numerics=dict(settings,coarse_width_over_em_coupling_squared_gev=coarse,
                relative_order_difference=difference,rate_authority='refined-fixed-native-M0-quadrature',
                scope='Conditional O(p4) numeric benchmark; no universal physical uncertainty envelope'))
        model['provenance']['transition']=current
    model['kinematically_open']=opened
    model['native_mass_rate_coefficients']=[dict(native_mass_gev=value,
        branching_over_epsilon_squared=_kaon_coefficient(value,signal_mass,model,1.)) for value in support]
    return model


def _validate_kaon_model(pdg,model):
    from dark_photon_model import HBAR_C_GEV_MM,mixing_parameters
    kind=model['decay_type'];mass=model.get('reference_parent_mass_gev')
    positive=('mass_min_gev','source_ecm_gev','mean_proper_ctau_mm','total_parent_width_gev',
              'signal_mass_gev','em_mixing_over_epsilon_abs2','physical_epsilon',
              'relative_numerical_mass_precision_budget')
    if (pdg not in (130,310,321,-321) or model.get('parent_mass_model')!='native-stable-kaon'
            or any(type(model.get(k)) not in (int,float) or not math.isfinite(model[k])
                   or model[k]<=0 for k in positive)
            or not model.get('provenance')):
        raise ValueError('Require a finite fixed-mass kaon current and native count authority')
    support,_=_kaon_mass_support(pdg,mass,model.get('native_record_mass_support_gev'),
        model.get('native_record_mass_authorities'))
    if (model['mass_min_gev']!=min(support) or model.get('mass_max_gev')!=max(support) or
            model.get('native_parent_count_bound')!=math.floor(model['source_ecm_gev']/min(support))):
        raise ValueError('Kaon finite support differs from its deterministic source-count authority')
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
    elif kind=='weak-kaon-pion':
        form=model.get('weak_form_factor');current=model.get('weak_form_factor_model',{})
        if (model.get('recoil_pdg_id')!={321:211,-321:-211,310:111,130:111}.get(pdg)
                or type(model.get('recoil_mass_gev')) not in (int,float)
                or not 0<model['recoil_mass_gev']<mass
                or type(model.get('alpha_em')) not in (int,float)
                or not math.isfinite(model['alpha_em']) or model['alpha_em']<=0
                or not isinstance(form,list) or len(form)!=2
                or any(type(x) not in (int,float) or not math.isfinite(x) for x in form)
                or current.get('parent_pdg_id')!=pdg or current.get('kind')!=
                    ('hypercharge-kl-pi0-cpv' if pdg==130 else 'chpt-p6-kaon-em-vector')
                or pdg==130 and (current.get('physical_epsilon')!=model['physical_epsilon'] or
                    current.get('signal_mass_gev')!=model['signal_mass_gev'])):
            raise ValueError('Require a matching kaon weak current and pion recoil')
    elif kind=='neutral-kaon-dipion':
        current=model.get('neutral_kaon_dipion_model',{});pion=current.get('pion_mass_gev')
        if (pdg not in (130,310) or current.get('parent_pdg_id')!=pdg
                or current.get('reference_parent_mass_gev')!=mass
                or current.get('total_width_gev')!=model['total_parent_width_gev']
                or current.get('kind')!='neutral-kaon-dipion-finite-current'
                or type(pion) not in (int,float) or not 0<2*pion<mass
                or model.get('recoil_pdg_ids')!=[111,111] or model.get('recoil_masses_gev')!=[pion,pion]
                or current.get('pole_mass_numerical_tolerance_gev')!=max(abs(value-mass) for value in support)):
            raise ValueError('Neutral-dipion current differs from its attributed kaon pole and lifetime')
    elif kind=='weak-kaon-pion-photon':
        current=model.get('weak_radiative_model',{})
        if (pdg!=130 or model.get('recoil_pdg_id')!=111
                or current.get('parent_pdg_id')!=130
                or current.get('parent_mass_gev')!=mass
                or current.get('recoil_pion_mass_gev')!=model.get('recoil_mass_gev')
                or current.get('pole_mass_numerical_tolerance_gev')!=max(abs(value-mass) for value in support)
                or not current.get('reference')):
            raise ValueError('KL pi0 photon rate differs from its native-current authority')
    elif kind=='weak-kaon-leptonic':
        lepton=model.get('charged_lepton_pdg_id');neutrino=model.get('neutrino_pdg_id')
        current=model.get('leptonic_form_factor_model',{})
        expected={11:.00051099895,13:MUON_MASS_GEV}.get(abs(lepton) if type(lepton) is int else 0)
        sign=1 if pdg>0 else -1;precision=model.get('native_kinematic_precision',{})
        settings=model.get('leptonic_rate_numerics',{})
        if (abs(pdg)!=321 or expected is None or type(neutrino) is not int or lepton*sign>=0 or
                neutrino!=sign*(abs(lepton)+1) or current.get('parent_pdg_id')!=321 or
                current.get('parent_mass_gev')!=mass or current.get('lepton_mass_gev')!=expected or
                current.get('kind')!='chpt-p4-charged-kaon-leptonic-vector' or current.get('order')!='p4-one-loop' or
                model.get('charged_lepton_mass_gev')!=expected or
                model.get('recoil_pdg_ids')!=[lepton,neutrino] or model.get('recoil_masses_gev')!=[expected,0.] or
                any(type(precision.get(key)) not in (int,float) or not math.isfinite(precision[key])
                    or not 0<precision[key]<=1.e-5 for key in ('relative_mass_limit','relative_endpoint_limit'))):
            raise ValueError('Require the matching K_l2 current, charges and independent numerical kinematic gates')
        key=_leptonic_rate_settings(current,settings)
        coarse,refined,difference=_leptonic_rate_benchmark(key,model['signal_mass_gev'],
            settings['order'],settings['check_order'])
        if (model.get('leptonic_width_over_em_coupling_squared_gev')!=refined or
                settings.get('coarse_width_over_em_coupling_squared_gev')!=coarse or
                settings.get('relative_order_difference')!=difference or
                difference>settings['relative_convergence_tolerance']):
            raise ValueError('K_l2 rate authority or quadrature benchmark changed after resolution')
    else:
        raise ValueError('Unsupported fixed-mass kaon current')
    expected=[dict(native_mass_gev=value,branching_over_epsilon_squared=
        _kaon_coefficient(value,model['signal_mass_gev'],model,1.)) for value in support]
    if model.get('native_mass_rate_coefficients')!=expected:
        raise ValueError('Kaon per-native-mass rate authority changed after resolution')


def _kaon_snapshot_authority(parent,model,kinematic_mass):
    """Check the native final-parent antichain and physical COM mass floor.

    The independent double-precision interval encloses products/dot/sqrt with
    gamma_32=32u/(1-32u), u=2^-53. Native COM values use long double; this
    interval deliberately also covers their JSON conversion. Native-vs-p4
    mass drift is recorded separately and must pass the existing rate gate.
    The integer count limit must stay unchanged after those allowances. The
    actual COM energy interval includes momentum drift already; subtracting
    the invariant-mass discrepancy again would count that effect twice.
    Every candidate must certify E_COM > ECM_upper/(count_limit+1), so one
    extra final parent would exceed the same collision's total COM energy.
    """
    channel=next(c for c in exclusive_channels(model) if kaon_channel(c))
    if parent.get('native_mass_gev') not in channel['native_record_mass_support_gev']:
        raise ValueError('Native kaon mass is outside the finite declared table support')
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
    # Each counted parent must independently exceed ECM/(count_limit+1).
    # Use its measured COM lower bound, capped at the declared mass floor.
    # This also rejects a genuinely low-energy parent that invalidates the
    # fixed count limit; no mass, momentum or rate is repaired here.
    mass_floor=min(channel['mass_min_gev'],low)-2*arithmetic
    if (mass_floor<=0 or low<mass_floor
            or math.floor(ecm_high/mass_floor)!=channel['native_parent_count_bound']):
        raise ValueError('Kaon arithmetic allowance changes the fixed integer source-count envelope')
    return dict(source_total_p4=list(total),source_com_energy_gev=energy,
        independent_com_energy_interval_gev=[low,high],
        independent_source_ecm_interval_gev=[ecm_low,ecm_high],
        com_energy_arithmetic_allowance_gev=arithmetic,
        native_invariant_mass_difference_gev=native_delta,
        stable_native_mass_floor_gev=parent['native_mass_gev'],
        finite_support_minimum_mass_gev=channel['mass_min_gev'],
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
    return model.get('decay_type') in ('pseudoscalar-dipion', 'vector-dipion', 'charged-vector-dipion','neutral-vector-dipion')


def vector_dipion_channel(model):
    return model.get('decay_type') in ('vector-dipion', 'charged-vector-dipion','neutral-vector-dipion')


def dipion_kernel(model):
    if model.get('decay_type')=='neutral-vector-dipion':
        import dark_photon_neutral_vector_dipion
        return dark_photon_neutral_vector_dipion
    if model.get('decay_type') == 'charged-vector-dipion':
        import dark_photon_charged_rho_radiative
        return dark_photon_charged_rho_radiative
    if model.get('decay_type') == 'vector-dipion':
        import dark_photon_rho_radiative
        return dark_photon_rho_radiative
    import dark_photon_dipion_meson
    return dark_photon_dipion_meson


def recoil_threshold(model):
    if model.get('decay_type')=='neutral-kaon-dipion':
        return 2*model['neutral_kaon_dipion_model']['pion_mass_gev']
    if model.get('decay_type')=='weak-kaon-leptonic':
        return model.get('charged_lepton_mass_gev',model['leptonic_form_factor_model']['lepton_mass_gev'])
    if model.get('decay_type') in ('weak-kaon-pion','weak-kaon-pion-photon'):
        return model['recoil_mass_gev']
    if model.get('decay_type') == 'charged-vector-dipion':
        return model['charged_pion_mass_gev']+model['neutral_pion_mass_gev']
    if dipion_channel(model):
        return 2 * model['pion_mass_gev']
    if current_channel(model):
        return model['recoil_mass_gev']
    return model.get('recoil_mass_gev', 0.) if vector_parent(model) else 0.


def vector_parent(model):
    return model.get('decay_type', 'pseudoscalar-photon') == 'vector-pseudoscalar'


def current_channel(model):
    return model.get('decay_type') in ('pseudoscalar-double-radiative', 'quarkonium-radiative')


def current_kernel(model):
    if model.get('decay_type') == 'pseudoscalar-double-radiative':
        import dark_photon_double_radiative
        return dark_photon_double_radiative
    if model.get('decay_type') == 'quarkonium-radiative':
        import dark_photon_quarkonium
        return dark_photon_quarkonium
    raise ValueError('Unsupported correlated electromagnetic current')


def _validate_current_model(pdg, model):
    """Validate declared narrow-source rates without inventing model errors."""
    from dark_photon_model import mixing_parameters
    minimum, maximum = model.get('mass_min_gev'), model.get('mass_max_gev')
    budget = model.get('relative_numerical_mass_precision_budget')
    epsilon, mass = model.get('physical_epsilon'), model.get('signal_mass_gev')
    if (type(minimum) not in (int,float) or not math.isfinite(minimum) or minimum<=0
            or maximum!=minimum or model.get('parent_mass_model')!='zero-width-pole'
            or type(budget) not in (int,float) or not math.isfinite(budget) or not 0<budget<=1.e-3
            or type(epsilon) not in (int,float) or not math.isfinite(epsilon) or not 0<epsilon<=.01
            or type(mass) not in (int,float) or not math.isfinite(mass) or mass<=2*MUON_MASS_GEV
            or model.get('em_mixing_over_epsilon_abs2')!=(mixing_parameters(mass,epsilon).em_coefficient/epsilon)**2):
        raise ValueError('Correlated current requires its explicit native pole, physical coupling and numerical budget')
    if model['decay_type']=='pseudoscalar-double-radiative':
        vectors=model.get('coherent_replaced_vector_pdgs')
        if (pdg not in (221,331) or model.get('recoil_pdg_id') not in (111,221)
                or (pdg==221 and model['recoil_pdg_id']!=111)
                or not isinstance(vectors,list) or not vectors or len(set(vectors))!=len(vectors)
                or set(vectors)-{113,223,333}):
            raise ValueError('Double-radiative source requires explicit coherent vector-cascade ownership')
        scalar=model.get('double_radiative_model',{}).get('scalar_current')
        if scalar is not None and (scalar.get('parent_pdg_id')!=pdg
                or scalar.get('recoil_pdg_id')!=model['recoil_pdg_id']
                or scalar.get('parent_mass_gev')!=minimum
                or scalar.get('recoil_mass_gev')!=model.get('recoil_mass_gev')):
            raise ValueError('Scalar current and source must share their physical species and pole masses')
    else:
        names={441:'eta_c',10441:'chi_c0',20443:'chi_c1',445:'chi_c2',10443:'h_c',100443:'psi_2S'}
        if pdg not in names or not model.get('channel','').startswith(names[pdg]+'->'):
            raise ValueError('Quarkonium current does not match the physical parent species')
        current=model.get('quarkonium_model',{})
        if (current.get('reference_parent_mass_gev')!=minimum
                or current.get('reference_recoil_mass_gev')!=model.get('recoil_mass_gev')):
            raise ValueError('Quarkonium current and native source must share their attributed pole masses')
    coefficient=current_kernel(model).branching_coefficient(minimum,mass,model)
    if not math.isfinite(coefficient) or coefficient<0:
        raise ValueError('Correlated current produced an invalid source rate')


def _resolved_current_model(parent, native, signal_mass, epsilon):
    from dark_photon_model import mixing_parameters
    if (type(epsilon) not in (int,float) or not math.isfinite(epsilon) or not 0<epsilon<=.01
            or native.get('mass_width_gev')!=0.
            or native['mass_min_gev']!=parent['mass_gev'] or native['mass_max_gev']!=parent['mass_gev']):
        raise ValueError('Correlated current requires the exact declared zero-width native source and physical epsilon')
    model=json.loads(json.dumps(parent,allow_nan=False))
    model.update(mass_min_gev=parent['mass_gev'],mass_max_gev=parent['mass_gev'],
        signal_mass_gev=signal_mass,physical_epsilon=epsilon,
        em_mixing_over_epsilon_abs2=(mixing_parameters(signal_mass,epsilon).em_coefficient/epsilon)**2,
        kinematically_open=parent['mass_gev']>signal_mass+recoil_threshold(parent),
        provenance=dict(pdg=parent['pdg_reference'],
            transition=parent['double_radiative_model' if parent['decay_type']=='pseudoscalar-double-radiative'
                              else 'quarkonium_model']['reference']))
    _validate_current_model(parent['pdg_id'],model)
    return model


def radiative_cascade_owner(parent, channel, models, parents):
    """Own only an explicitly replaced, matching P -> gamma V -> gamma P'A'.

    Other rare channels of that vector and vectors from other source histories
    remain independent contributions. Missing ancestry is not evidence that a
    candidate is disjoint from the coherent parent amplitude.
    """
    if not vector_parent(channel):
        return None
    candidates=[]
    for pdg,group in models.items():
        for owner in exclusive_channels(group):
            if (owner.get('decay_type')=='pseudoscalar-double-radiative'
                    and parent['pdg_id'] in owner.get('coherent_replaced_vector_pdgs',[])
                    and owner.get('recoil_pdg_id')==channel.get('recoil_pdg_id')):
                candidates.append((pdg,owner))
    if not candidates:
        return None
    if 'radiative_ancestor' not in parent:
        raise ValueError('Coherent radiative replacement requires native ancestry for every competing vector')
    ancestor=parent['radiative_ancestor']
    if ancestor is None:
        return None
    if (not isinstance(ancestor,dict) or type(ancestor.get('index')) is not int
            or ancestor['index']<=0 or ancestor.get('sibling_pdg_id')!=22
            or ancestor.get('vector_pdg_id')!=parent['pdg_id']):
        raise ValueError('Invalid coherent radiative-parent ancestry')
    owners=[owner for pdg,owner in candidates if pdg==ancestor.get('pdg_id')]
    if len(owners)>1:
        raise ValueError('Several coherent amplitudes claim one exclusive vector cascade')
    if not owners:
        return None
    if not any(row.get('index')==ancestor['index'] and row.get('pdg_id')==ancestor['pdg_id']
               for row in parents):
        raise ValueError('Coherent radiative ancestor is absent from the native source inventory')
    return owners[0]['channel']


def production_mechanism(card):
    mechanism = card.get('mechanism', 'eta-etaprime-radiative')
    if mechanism not in ('eta-etaprime-radiative', 'meson-radiative'):
        raise ValueError('Unknown meson production mechanism: ' + str(mechanism))
    if mechanism == 'meson-radiative' and not card.get('channels'):
        raise ValueError('Unified radiative production requires an explicit exclusive channel inventory')
    if mechanism == 'eta-etaprime-radiative' and any(
            vector_parent(channel) or dipion_channel(channel) or kaon_channel(channel) or current_channel(channel)
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
        return _kaon_coefficient(parent_mass,signal_mass,model,br_mumu,native_precision=native_precision)
    if current_channel(model):
        if signal_mass!=model['signal_mass_gev']:
            raise ValueError('Resolved correlated current belongs to another signal mass')
        return (current_kernel(model).branching_coefficient(parent_mass,signal_mass,model)
                *model['em_mixing_over_epsilon_abs2']*br_mumu)
    if dipion_channel(model):
        context = dict(native_precision or {}) if vector_dipion_channel(model) else {}
        if model.get('decay_type') not in ('neutral-vector-dipion','charged-vector-dipion'):
            context.pop('defer_to_full_source',None)
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


def resolved_parent_models(card, configuration, mass, *, epsilon=None, model_directory=None):
    """Resolve only explicitly declared exclusive channels at native support."""
    if card.get('mechanism') == 'meson-radiative':
        expected = [parent.get('channel') for parent in card['parents']]
        if (any(not isinstance(name, str) or not name for name in expected) or
                len(set(expected)) != len(expected) or card.get('channels') != expected):
            raise ValueError('Explicit channel names do not match the exclusive parent/recoil models')
    if any(parent.get('decay_type')=='neutral-vector-dipion' for parent in card['parents']):
        if not validate_native_precision_policy(card.get('native_precision_policy')):
            raise ValueError('Neutral-dipion source requires the explicit complete-source precision policy')
    models = {}
    native = {row['pdg_id']: row for row in configuration['parents']}
    if len(native) != len(configuration['parents']):
        raise ValueError('Native parent species are repeated')
    for parent in card['parents']:
        pdg = parent['pdg_id']
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
            model=_resolved_kaon_model(parent,native[pdg],mass,epsilon,configuration['ecm_gev'],
                model_directory,configuration.get('native_kaon_mass_authorities'))
            if pdg not in models:
                models[pdg]=model
            else:
                previous=models[pdg];channels=exclusive_channels(previous)
                if any((channel['decay_type'],channel.get('charged_lepton_pdg_id'))==
                       (model['decay_type'],model.get('charged_lepton_pdg_id')) for channel in channels):
                    raise ValueError('Duplicate exclusive kaon current for one physical parent')
                models[pdg]=dict(mass_min_gev=model['mass_min_gev'],mass_max_gev=model['mass_max_gev'],
                    exclusive_channels=channels+[model])
            continue
        if current_channel(parent):
            model=_resolved_current_model(parent,native[pdg],mass,epsilon)
            if pdg not in models:
                models[pdg]=model
            else:
                channels=exclusive_channels(models[pdg])
                if any(row['channel']==model['channel'] for row in channels):
                    raise ValueError('Duplicate correlated current for one physical parent')
                models[pdg]=dict(mass_min_gev=minimum,mass_max_gev=maximum,exclusive_channels=channels+[model])
            continue
        recoil = recoil_threshold(parent)
        opened = maximum > mass + recoil
        form_factor = parent_form_factor_abs2(mass, parent) if opened and not dipion_channel(parent) else 1.
        model = dict(mass_min_gev=minimum, mass_max_gev=maximum,
            form_factor_abs2=form_factor,
            relative_model_uncertainty=model_precision_budget(parent, mass) if opened else 0.,
            kinematically_open=opened, provenance=dict(pdg=parent['pdg_reference'],
                transition=parent.get('transition_form_factor', parent.get('dipion_model',
                    parent.get('rho_radiative_model', parent.get('charged_rho_radiative_model',
                    parent.get('neutral_dipion_model',{})))))['reference']))
        model['channel'] = parent.get('channel', str(pdg) + '->gamma_Aprime')
        if vector_parent(parent):
            for key in ('decay_type', 'recoil_pdg_id', 'recoil_mass_gev', 'br_p_gamma',
                        'parent_polarization', 'parent_mass_model'):
                model[key] = parent[key]
            if (native[pdg].get('mass_width_gev') != 0. or minimum != parent['mass_gev'] or
                    maximum != parent['mass_gev']):
                raise ValueError('Vector pole branching model requires an unchanged zero-width native source')
        elif dipion_channel(parent):
            keys = (('decay_type','pion_mass_gev','neutral_dipion_model','recoil_pdg_ids',
                     'parent_polarization','parent_mass_model')
                    if parent.get('decay_type')=='neutral-vector-dipion' else
                    ('decay_type', 'charged_pion_mass_gev', 'neutral_pion_mass_gev',
                     'br_pipi', 'charged_rho_radiative_model', 'recoil_pdg_ids',
                     'parent_polarization', 'parent_mass_model')
                    if parent.get('decay_type') == 'charged-vector-dipion' else
                    ('decay_type', 'pion_mass_gev', 'br_pipi', 'rho_radiative_model',
                     'parent_polarization', 'parent_mass_model')
                    if parent.get('decay_type') == 'vector-dipion' else
                    ('decay_type', 'pion_mass_gev', 'br_pipi_gamma', 'dipion_model'))
            for key in keys:
                model[key] = parent[key]
            if parent.get('decay_type')=='neutral-vector-dipion':
                from dark_photon_model import mixing_parameters
                if type(epsilon) not in (int,float) or not math.isfinite(epsilon) or not 0<epsilon<=.01:
                    raise ValueError('Neutral dipion current requires the unchanged physical epsilon')
                model['neutral_dipion_model']=dict(parent['neutral_dipion_model'],
                    em_mixing_over_epsilon_abs2=(mixing_parameters(mass,epsilon).em_coefficient/epsilon)**2)
                model['numerical_cdf_rate_authority']=dipion_kernel(model).numerical_rate_authority(model,mass)
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
    if parent.get('decay_type')=='neutral-vector-dipion':return 1.e-3
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
    all_channels = exclusive_channels(model)
    channels = [row for row in all_channels if row.get('decay_type','pseudoscalar-photon')
                in ('pseudoscalar-dipion','pseudoscalar-photon')]
    additional = [row for row in all_channels if row not in channels]
    kinds = [row.get('decay_type', 'pseudoscalar-photon') for row in channels]
    native = parent['native_mass_gev']
    if (parent['pdg_id'] not in (221, 331) or len(channels) != 2
            or sorted(kinds) != ['pseudoscalar-dipion', 'pseudoscalar-photon']
            or any(row.get('decay_type')!='pseudoscalar-double-radiative' for row in additional)
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
    report=dict(schema='shift-pseudoscalar-native-dipion-precision-v1',
                parent_pdg_id=parent['pdg_id'], signal_mass_gev=signal_mass,
                native_parent_mass_gev=native, kinematic_parent_mass_gev=kinematic_mass,
                channels=rates, exceptional_dipion_rate_plus_shape_bound=correction,
                required_exceptional_dipion_bound=PSEUDOSCALAR_DIPION_CORRECTION_LIMIT,
                parent_l1_relative_rate_shift=l1, parent_original_weighted_model_budget=measured_budget,
                scope='0.1% applies only to the exceptional dipion contribution; sibling BR/TFF checks remain unchanged')
    if additional:
        checked=[]
        for channel in additional:
            if ((kinematic_mass>signal_mass+recoil_threshold(channel))!=
                    (native>signal_mass+recoil_threshold(channel))):
                raise ValueError('Native precision changes whether an additional current is open')
            nominal=parent_branching_coefficient(native,signal_mass,channel,br_mumu)
            shifted=parent_branching_coefficient(kinematic_mass,signal_mass,channel,br_mumu)
            relative=shifted/nominal-1 if nominal else 0.
            budget=channel['relative_numerical_mass_precision_budget']
            if not math.isfinite(budget) or not 0<budget<=1.e-3 or abs(relative)>budget:
                raise ValueError('Additional current exceeds its independent numerical precision budget')
            checked.append(dict(channel=channel['channel'],relative_shift=relative,numerical_budget=budget))
        # Preserve the original two-channel denominator. A new large current
        # must not dilute the exceptional dipion's previously qualified bound.
        report['precision_normalization_channels']=[row['channel'] for row in rates]
        report['independently_checked_additional_channels']=checked
    return report


def resolve_parent_kinematics(parents, mass_tolerance, signal_mass, models, br_mumu,*,native_precision_policy=None):
    """Preserve native momenta and bookkept masses; never repair or drop one.

    Pythia ErrorChecks.xml defines Check:mTolErr as the mass deviation divided
    by max(E,1 GeV). Use the actual p4 invariant consistently in the radiative
    decay. The native generated mass defines the branching probability, as in
    native generator mass handling. Record its numerical effect explicitly.
    """
    if not math.isfinite(mass_tolerance) or mass_tolerance <= 0:
        raise ValueError('An explicit native Pythia mass tolerance is required')
    deferred_policy=validate_native_precision_policy(native_precision_policy)
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
        parent.pop('native_channel_precision_reports',None)
        parent.pop('leptonic_kinematic_precision', None)
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
            if native not in exclusive_channels(model)[0]['native_record_mass_support_gev']:
                raise ValueError('Native kaon mass differs from its exact stable support')
        native_precision = dict(native_mass_gev=native, native_energy_gev=p4[0],
                                native_mass_tolerance=mass_tolerance)
        if deferred_policy and _kaon_group(model):
            native_precision['defer_to_full_source']=True
        if deferred_policy and any(c.get('decay_type')=='neutral-vector-dipion' for c in exclusive_channels(model)):
            native_precision['defer_to_full_source']=True
        owned_rates = charged_rho_parent_rate_model(parent['pdg_id'], model)
        if owned_rates is not None:
            native_precision['native_parent_rate_model'] = owned_rates
            if deferred_policy:native_precision['defer_to_full_source']=True
        for channel in exclusive_channels(model):
            channel_precision=dict(native_precision)
            if channel.get('decay_type')=='neutral-vector-dipion':
                if not deferred_policy:raise ValueError('Neutral-dipion source requires explicit full-source precision policy')
                channel_precision['defer_to_full_source']=True
                report=dipion_kernel(channel).native_channel_precision_report(mass,signal_mass,channel,**channel_precision)
                parent.setdefault('native_channel_precision_reports',[]).append(report)
            recoil = recoil_threshold(channel)
            if (mass > signal_mass + recoil) != (native > signal_mass + recoil):
                raise ValueError('Native precision changes whether the rare decay is open')
            if channel.get('decay_type')=='weak-kaon-leptonic':
                report=_leptonic_mass_gate(mass,signal_mass,channel,defer_to_full_source=deferred_policy)
                parent.setdefault('leptonic_kinematic_precision',[]).append(report)
                if report.get('deferred_full_source_check'):
                    parent.setdefault('native_channel_precision_reports',[]).append(report)
            nominal = parent_branching_coefficient(native, signal_mass, channel, br_mumu)
            shifted = parent_branching_coefficient(mass, signal_mass, channel, br_mumu,
                                                   native_precision=channel_precision)
            relative = shifted / nominal - 1 if nominal else 0.
            budget = channel.get('relative_numerical_mass_precision_budget',
                                 channel.get('relative_model_uncertainty'))
            if abs(relative) > budget:
                if kaon_channel(channel) and deferred_policy:
                    # The native proposal keeps its original coefficient. Bound
                    # the entire exceptional conditional channel, including a
                    # possible rate change, in the fixed full-source measure.
                    parent.setdefault('native_channel_precision_reports',[]).append(dict(
                        schema='shift-kaon-native-channel-precision-v1',channel=channel['channel'],
                        deferred_full_source_check=True,scope='deferred-full-source',
                        nominal_parent_coefficient=nominal/br_mumu,
                        conservative_absolute_parent_measure_bound=2*max(nominal,shifted)/br_mumu,
                        native_relative_rate_shift=relative,
                        original_channel_precision_budget=budget))
                    continue
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
                        'physical_epsilon','em_mixing_over_epsilon_abs2','native_parent_count_bound',
                        'reference_parent_mass_gev','native_record_mass_support_gev',
                        'native_record_mass_authorities')
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
        if current_channel(model):
            _validate_current_model(pdg,model)
            continue
        if model.get('decay_type', 'pseudoscalar-photon') not in ('pseudoscalar-photon', 'vector-pseudoscalar', 'pseudoscalar-dipion', 'vector-dipion', 'charged-vector-dipion','neutral-vector-dipion'):
            raise ValueError('Unsupported exclusive radiative decay type')
        if model.get('decay_type')=='neutral-vector-dipion':
            inputs=model['neutral_dipion_model']
            if (pdg not in (113,223) or model.get('parent_polarization')!='unpolarized' or
                    model.get('parent_mass_model')!='zero-width-pole' or
                    model.get('mass_max_gev')!=model['mass_min_gev'] or
                    inputs['reference_parent_mass_gev']!=model['mass_min_gev'] or
                    inputs['pion_mass_gev']!=model['pion_mass_gev'] or model.get('recoil_pdg_ids')!=[111,111] or
                    type(inputs.get('total_width_gev')) not in (int,float) or
                    not math.isfinite(inputs['total_width_gev']) or inputs['total_width_gev']<=0):
                raise ValueError('Neutral dipion benchmark requires its explicit native pole, width, pi0 pair and spin')
            continue
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
            maximum=max(parent_branching_coefficient(value,mass_gev,model,br_mumu)
                for value in model['native_record_mass_support_gev'])
            terms.append(model['native_parent_count_bound']*maximum)
            continue
        if current_channel(model):
            if mass_gev is None:
                raise ValueError('Correlated current bounds require a fixed signal mass')
            value=parent_branching_coefficient(model['mass_min_gev'],mass_gev,model,br_mumu)
            terms.append(math.floor(ecm_gev/model['mass_min_gev'])*value)
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
    replaced = []
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
            owner=radiative_cascade_owner(parent,channel,models,parents)
            if owner is not None:
                replaced.append(dict(parent_index=identity,channel=channel['channel'],owner=owner))
                continue
            value = parent_branching_coefficient(parent_mass, mass_gev, channel, br_mumu)
            weights.append((parent, channel, value))
    total = math.fsum(value for _, _, value in weights)
    if total > bound*(1+1.e-12):
        raise ValueError('Analytic source bound is violated; refusing biased clipping')
    probability = total/bound
    result = dict(parent_count=len(parents), coefficient_sum=total,
                  acceptance_probability=probability, accepted=False, parent_index=None)
    if replaced:
        result['coherently_replaced_cascades']=replaced
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
