# Code style: AI-code. AI-generated finite-bank decay sampling mathematics.
"""Choose one valid decay state of one original, with its exact correction.

For N generated states x_i~q_i and choice probabilities s_i>0, weighting the
chosen state by (f/q_i)/(N*s_i) estimates the native law f. Conditional on the
bank, the choice expectation is the average of all N importance estimates.
No particles from different originals are combined; bank members are not
independent source exposure or independent training events.
"""
import itertools
import math
import random

from shift_sm_response_features import _station_misses, MASS_STOPPING_POWER_GEV_CM2_G


def validate_settings(settings):
    kernel=settings.get('base_kernel','smooth_lifetime')
    maximum=256 if kernel=='native_ordinary' else 32
    if (kernel not in ('smooth_lifetime','native_ordinary') or type(settings['size']) is not int
            or not 2<=settings['size']<=maximum
            or type(settings['control_fraction']) not in (int,float)
            or not 0.<settings['control_fraction']<=1.):
        raise ValueError('Require a bounded declared decay kernel and positive bank support')
    return kernel


def audit_native_member(audit):
    """Ordinary native draws need no channel BR or lifetime likelihood factor."""
    from audit_shift_weighted_gen import DEFERRED_CHARM_PROFILE
    source=audit['original_full_source']
    if (audit.get('base_kernel')!='native_ordinary'
            or source.get('generator_profile')!=DEFERRED_CHARM_PROFILE
            or source.get('source_family')!='qcd_ordinary'
            or source.get('decay_dependent_filter') is not False
            or audit['proposal_definition']!=dict(ordinary_fraction=1.,components=[])
            or any(type(audit[k]) not in (int,float) or audit[k]!=1. for k in
                   ('ordinary_fraction','proposal_density_ratio','proposal_correction'))
            or type(audit['proposal_component']) is not int or audit['proposal_component']!=0
            or audit['selected_parent_barcodes']!=[]
            or audit.get('deferred_charm_completion',{}).get('all_original_D_roots_completed') is not True
            or audit.get('deferred_charm_completion',{}).get('no_nested_new_D') is not True):
        raise ValueError('Native ordinary bank member lacks its unchanged unit-density decay audit')
    census=source.get('deferred_charm_frontiers',[])
    matches=[row for row in census if row['event_id']==audit['original_event_id']]
    if len(census)!=source.get('saved_events') or len(matches)!=1:
        raise ValueError('Native ordinary member lacks its complete original frontier census')
    parents=sorted(matches[0]['parents'],key=lambda p:p['barcode'])
    completed=audit['deferred_charm_completion']
    if (completed['parent_barcodes']!=[p['barcode'] for p in parents]
            or completed['parent_pdg_ids']!=[p['pdg'] for p in parents]):
        raise ValueError('Native ordinary completion changed the frozen D frontier')


def probabilities(scores, control_fraction):
    if (not scores or not 0.<control_fraction<=1.
            or any(not math.isfinite(x) or x<0. for x in scores)):
        raise ValueError('Need finite nonnegative scores and positive uniform bank support')
    total=math.fsum(scores);size=len(scores)
    values=[control_fraction/size+(1.-control_fraction)*x/total for x in scores] if total else [1./size]*size
    if not math.isclose(math.fsum(values),1.,rel_tol=2.e-15) or any(x<=0. for x in values):
        raise ValueError('Bank probabilities lost normalized positive support')
    return values


def choose(scores, control_fraction, seed):
    values=probabilities(scores,control_fraction);u=random.Random(seed).random();cumulative=0.
    for index,p in enumerate(values):
        cumulative+=p
        if u<cumulative or index==len(values)-1:
            return dict(index=index,probabilities=values,uniform_draw=u,
                        selection_correction=1./(len(values)*p))


def audit_record(record,event_id):
    if (record['same_original_event_id']!=event_id or record['size']!=len(record['members'])
            or [r['index'] for r in record['members']]!=list(range(record['size']))
            or len({r['seed'] for r in record['members']})!=record['size']):
        raise ValueError('Decay bank mixes originals, omits members or repeats seeds')
    reference=choose([r['score']['score'] for r in record['members']],
                     record['control_fraction'],record['selection_seed'])
    if reference!=record['selection']:
        raise ValueError('Bank choice differs from its complete frozen score distribution')
    if record['selected_GEN_sha256']!=record['members'][reference['index']]['GEN_sha256']:
        raise ValueError('Chosen bank GEN is not the declared member')
    kernel=validate_settings(record)
    if kernel=='native_ordinary':
        if any(r.get('base_kernel')!=kernel or r['lifetime_density_ratio']!=1.
               or r['lifetime_correction']!=1. for r in record['members']):
            raise ValueError('Native ordinary bank contains a nonunit base decay likelihood')
        if record.get('material_survival_correction_applied') is not False:
            raise ValueError('A native ordinary bank must not apply a physical survival correction')
    return reference


def muon_score(muons,parents=None):
    """Soft target ranking on the same pair; no hard pT or survival cut.

    Straight station rays and total momentum predict opportunities only.
    Geant4 still decides parent survival and muon transport. A uniform bank
    component keeps every state, including same-sign/missed-direction states.
    """
    best=0.;description=None
    for a,b in itertools.combinations(muons,2):
        if a['pdg']*b['pdg']>=0:
            continue
        rays=[_station_misses(p) for p in (a,b)]
        if any(r is None or r['two_station_miss'] is None for r in rays):
            continue
        miss=max(r['two_station_miss'] for r in rays)
        direction=math.exp(-min(750.,(miss/50.)**2))
        momentum=[x+y for x,y in zip(a['momentum_GeV'],b['momentum_GeV'])]
        energy=a['energy_GeV']+b['energy_GeV']
        mass=math.sqrt(max(0.,energy*energy-sum(x*x for x in momentum)))
        pt=math.hypot(*momentum[:2])
        minimum_p=min(math.sqrt(sum(x*x for x in p['momentum_GeV'])) for p in (a,b))
        depth=0.;unknown=False;column_priority=1.
        if parents is not None:
            numbers=set(a['transport_parent_barcodes'])|set(b['transport_parent_barcodes'])
            if not numbers.issubset(parents):
                raise ValueError('Candidate pair references an absent parent flight')
            for number in numbers:
                material=parents[number]['material'];value=material.get('interaction_depth_proxy')
                if value is not None and material.get('complete') is True:
                    if not math.isfinite(value) or value<0.:
                        raise ValueError('Invalid known parent-prefix depth')
                    depth+=value
                else:
                    unknown=True
            for muon in (a,b):
                material=muon['material_to_CMS_entry'];column=material.get('column_density_g_cm2')
                if column is not None and material.get('complete') is True:
                    if not math.isfinite(column) or column<0.:
                        raise ValueError('Invalid measured muon material column')
                    p=math.sqrt(sum(x*x for x in muon['momentum_GeV']))
                    column_priority*=p/(p+MASS_STOPPING_POWER_GEV_CM2_G*column) if p or column else 1.
                else:
                    unknown=True
        # Attenuation and ionization proxies rank choices only. They never
        # multiply event physics weights. Unknown coverage is explicit.
        score=(direction*(-math.expm1(-minimum_p/20.))*math.exp(-min(700.,depth))
               *column_priority*(1.+(mass/20.)**4+(pt/10.)**4))
        if not math.isfinite(score):
            raise ValueError('Nonfinite actual-muon bank score')
        if score>best:
            best=score;description=dict(muon_barcodes=[a['barcode'],b['barcode']],
                mass_GeV=mass,pt_GeV=pt,minimum_total_momentum_GeV=minimum_p,
                two_station_miss_cm=miss,known_parent_prefix_depth=depth,
                material_scored=parents is not None,unknown_material_coverage=unknown,
                muon_column_priority=column_priority)
    return dict(score=best,pair=description,not_a_measured_efficiency=True)
