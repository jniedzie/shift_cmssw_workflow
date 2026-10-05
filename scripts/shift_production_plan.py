"""Immutable sample and resource plan for the autonomous SHIFT DAG."""
import math
import re

BINS = ((0, 1), (1, 2), (2, 5), (5, 10), (10, 20), (20, -1))
TARGETS = {'qcd': 100000, 'jpsi': 10000, 'dy': 10000}
DY_BINS = ((0.211317, 0.5), (0.5, 1), (1, 2), (2, 5), (5, 10), (10, 20), (20, -1))


def make_plan(tag, output_base, dy_definition='mass', max_workers=50, include_strata=None):
    if not re.fullmatch(r'[A-Za-z0-9_]+', tag):
        raise ValueError('Campaign tag must contain only letters, digits and underscores')
    if not output_base.startswith('/eos/user/') or '..' in output_base.split('/'):
        raise ValueError('Require a canonical /eos/user output base')
    if not 1 <= max_workers <= 50:
        raise ValueError('Use 1..50 total SHIFT workers until measured load is reviewed')
    if dy_definition != 'mass':
        raise ValueError('DY uses mass bins independently of QCD/Jpsi pT bins')
    strata = []
    for sample_index, (sample, target) in enumerate(TARGETS.items()):
        for bin_index, bounds in enumerate(DY_BINS if sample == 'dy' else BINS):
            label = f'{bounds[0]}to{bounds[1]}'
            # J/psi rejection is rare: deliberately start with tiny timing pilots.
            events_per_job = {'qcd': 1000, 'jpsi': 20, 'dy': 100}[sample]
            strata.append(dict(
                id=f'{sample}_{label}', sample=sample, bounds=list(bounds),
                target_events=target, events_per_job=events_per_job,
                jobs=math.ceil(target/events_per_job),
                pilot_events={'qcd': 100, 'jpsi': 3, 'dy': 20}[sample],
                seed_base=181000000+sample_index*10000000+bin_index*100000,
                run_offset=(sample_index*6+bin_index)*100000,
                campaign=f'{output_base}/{sample}/{tag}_{sample}_{label}_GEN',
                bin_variable=('hardest_MPI_pThat' if sample == 'qcd' else
                              'maximum_direct_hard_Jpsi_pT' if sample == 'jpsi' else
                              'gammaZ_invariant_mass'),
                mass_bounds=(list(bounds) if sample == 'dy' else None),
                adapter=('soft_mpi_gen' if sample != 'dy' else
                         'dy_mass_gen'),
            ))
    if include_strata is not None:
        available = {stratum['id'] for stratum in strata}
        if (not include_strata or len(include_strata) != len(set(include_strata)) or
                set(include_strata) - available):
            raise ValueError('Require unique, known strata in a nonempty partial plan')
        selected = set(include_strata)
        strata = [stratum for stratum in strata if stratum['id'] in selected]
    plan = dict(schema='shift-production-plan-v1', tag=tag, strata=strata,
                max_workers=max_workers, dy_definition=dy_definition,
                targets=dict(TARGETS), stage='GEN', year=2023,
                physics_valid=False, normalization_ready=False,
                detector_production_ready=False,
                detector_gates=['validated CMS IR5 material and field',
                                'resolved unchanged Run-3 detector configuration',
                                'bounded end-to-end and eviction/restart validation'],
                normalization_gates=['all 12 complementary QCD/Jpsi strata',
                                     'independent inclusive non-diffractive closure',
                                     'DY inclusive closure across the mass bins',
                                     'sub-GeV DY continuum and hadronic-resonance model review',
                                     'explicit forced-decay branching convention'],
                retention='retain all old samples; deletion is a separate reviewed action',
                retry_policy='no automatic physics-job retries; preserve evidence and rescue DAG')
    if include_strata is not None:
        plan['included_strata'] = [stratum['id'] for stratum in strata]
        plan['full_partition_complete'] = False
    return plan


def validate_plan(plan):
    included = plan.get('included_strata')
    expected = make_plan(plan['tag'], plan['strata'][0]['campaign'].rsplit('/', 2)[0],
                         plan['dy_definition'], plan['max_workers'], included)
    if plan != expected:
        raise ValueError('Plan differs from the supported immutable generation contract')
    return plan
