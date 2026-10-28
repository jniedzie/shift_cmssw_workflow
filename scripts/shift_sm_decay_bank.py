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
