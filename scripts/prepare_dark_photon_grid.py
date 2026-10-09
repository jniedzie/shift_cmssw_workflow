#!/usr/bin/env python3
"""Freeze a bounded physical mass/epsilon pilot grid; never launch jobs.

Default columns are prompt, approximately 5 m and 30 m mean LAB flight.
The estimate is <beta*gamma>*c*tau, with boosts from an explicit MC reference.
Every decay still follows its full exponential; the lab flight is not fixed.
Lifetime is derived from the natural native physical width, never overridden.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from dark_photon_generation import check_width_table, contract, production_settings
from dark_photon_model import validate_native_proposal_support, validate_native_widths


REFERENCE_SCHEMA = 'shift-dark-photon-grid-reference-v1'
SOURCES = ('prepare_dark_photon_grid.py', 'run_dark_photon_gen.py', 'dark_photon_gen_cfg.py',
           'dark_photon_generation.py', 'dark_photon_model.py', 'dark_photon_pythia.py',
           'fixed_target_generation.py', 'audit_dark_photon_gen.py', 'run_dark_photon_detector.py',
           'run_shift_gen_to_nano.py', 'shift_muon_decay_replay.py', 'pythia_generation_ledger.py')
VALIDATION_REQUIREMENTS = (
    'GEN audit must pass with all generated identities, weights and out-of-envelope decays retained.',
    'Compare measured <beta gamma> and lab-flight distributions with the reference estimate; do not condition on detector acceptance.',
    'Check physical and sampling width/BR closure, pole support and sampling-rate convergence independently.',
    'Replay bounded events through the exact frozen V10 templates; verify timed graphs/ancestry/weights through every tier.',
    'Keep fixed electronics, trigger, reconstruction and pair-retention acceptance separate from classifier efficiency.',
    'Score the frozen SM classifier using reconstructed quantities only; physically strip truth and verify identical scores.',
    'Include zero-pair Nano events and all accepted GEN events; keep technical Pythia trial counts separate from luminosity.',
    'Require supported mass/lifetime acceptance closure before normalization transfer; two-event detector pilots cannot establish it.',
    'Scale beyond bounded pilots only after event-level runtime/configuration and physical-model checks; no final rate is approved.',
)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def content_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def normalized_width(native, mass, epsilon):
    table = copy.deepcopy(native)
    if not math.isclose(float(table['mass_gev']), mass, rel_tol=1e-12):
        raise ValueError('Native width mass differs from physical point')
    table.update(epsilon=epsilon, total_width_gev=float(table['m_width_gev']),
                 ctau_mm=float(table['proper_length_mm']),
                 br_mumu=math.fsum(float(row['branching_fraction']) for row in table['channels']
                                  if sorted(row['products']) == [-13, 13]))
    check_width_table(table, mass, epsilon)
    table['independent_fermion_width_closure'] = validate_native_widths(table, epsilon)
    return table


def validate_references(reference):
    if reference.get('schema') != REFERENCE_SCHEMA or reference.get('sample_kind') != 'simulation_only':
        raise ValueError('Require an explicit simulation native-width/boost reference')
    for key in ('common', 'cp5'):
        if not isinstance(reference.get(key), list) or not all(isinstance(x, str) for x in reference[key]):
            raise ValueError('Reference must freeze common and CP5 setting lists')
    result = {}
    for row in reference.get('references', []):
        mass, epsilon, boost = (float(row[k]) for k in ('mass_gev', 'epsilon', 'mean_beta_gamma'))
        production_settings(mass, epsilon)
        if mass in result or not math.isfinite(boost) or boost <= 0 or not row.get('boost_source'):
            raise ValueError('Require unique masses, positive measured boosts and their provenance')
        table = row['width_authority']
        check_width_table(table, mass, epsilon)
        if float(table['pythia_version']) != 8.317:
            raise ValueError('Width reference must use pinned Pythia 8.317')
        validate_native_widths(table, epsilon)
        result[mass] = row
    if not result:
        raise ValueError('No native width references')
    return result


def plan_points(reference, masses=(15., 30., 50.), mean_lab_flights_m=(5., 30.),
                epsilons=None, prompt_epsilon=.001, sampling_epsilon=.005,
                events=100, detector_count=2, seed_base=24693357, detector_job_base=910001):
    """Select physical epsilon; scaling is only an initial native-width estimate."""
    refs = validate_references(reference)
    masses = tuple(map(float, masses))
    if not masses or len(set(masses)) != len(masses) or any(m not in refs for m in masses):
        raise ValueError('Require unique masses covered by the frozen references')
    if type(events) is not int or not 1 <= events <= 1000:
        raise ValueError('Bounded grid accepts 1..1000 GEN events per point')
    if type(detector_count) is not int or not 1 <= detector_count <= min(20, events):
        raise ValueError('Detector canary accepts 1..20 events and cannot exceed GEN count')
    if epsilons is not None:
        epsilons = tuple(map(float, epsilons))
        if not epsilons or len(set(epsilons)) != len(epsilons):
            raise ValueError('Require unique explicit epsilon columns')
        columns = [('epsilon_' + format(e, '.8g').replace('.', 'p'), e, None) for e in epsilons]
        mode = 'cartesian-mass-epsilon'
    else:
        flights = tuple(map(float, mean_lab_flights_m))
        if not flights or len(set(flights)) != len(flights) or any(not math.isfinite(x) or x <= 0 for x in flights):
            raise ValueError('Require unique positive target mean lab flights')
        columns = [('prompt', prompt_epsilon, None)] + [('lab_' + format(x, '.8g').replace('.', 'p') + 'm', None, x) for x in flights]
        mode = 'mass-by-approximate-mean-lab-flight'
    n_points = len(masses) * len(columns)
    if n_points > 30 or type(seed_base) is not int or not 1 <= seed_base <= 899999900 - n_points + 1:
        raise ValueError('Require at most 30 points and a valid unique seed namespace')
    if type(detector_job_base) is not int or detector_job_base < 1:
        raise ValueError('Detector job namespace must be positive')
    points = []
    for mass in masses:
        ref = refs[mass]
        for name, epsilon, target in columns:
            if target is not None:
                desired_ctau = target * 1000 / float(ref['mean_beta_gamma'])
                epsilon = abs(float(ref['epsilon'])) * math.sqrt(float(ref['width_authority']['ctau_mm']) / desired_ctau)
            epsilon = float(epsilon)
            if epsilon <= 0 or not math.isfinite(sampling_epsilon) or epsilon > sampling_epsilon:
                raise ValueError('Physical epsilon must be positive and no larger than the sampling amplitude')
            production_settings(mass, epsilon, sampling_epsilon / epsilon)
            i = len(points)
            points.append(dict(point='m' + format(mass, '.8g').replace('.', 'p') + '_' + name,
                               mass_gev=mass, epsilon=epsilon, target_mean_lab_flight_m=target,
                               mean_beta_gamma_reference=float(ref['mean_beta_gamma']),
                               boost_reference_source=ref['boost_source'],
                               proper_ctau_reference_estimate_mm=float(ref['width_authority']['ctau_mm']) * (float(ref['epsilon']) / epsilon) ** 2,
                               lifetime_estimate_only=True, sampling_epsilon=sampling_epsilon,
                               events=events, detector_count=detector_count, seed=seed_base + i,
                               run_number=seed_base + i, detector_job=detector_job_base + i))
    if len({row['point'] for row in points}) != len(points):
        raise ValueError('Grid points collide in their eight-digit display names; choose distinct pilot columns')
    return dict(grid_mode=mode, points=points, lab_flight_is_fixed=False,
                reference_content_sha256=content_digest(reference),
                prompt_definition='physical epsilon ' + format(prompt_epsilon, '.8g') + '; lifetime derived from native width',
                assumption='Default displaced columns mean approximately 5 m and 30 m LAB flight; configurable before GEN')


def preflight_points(plan, reference, native_probe, relative_flight_tolerance=1e-4):
    """Probe natural physical widths and common-amplitude sampling before launch."""
    if not math.isfinite(relative_flight_tolerance) or not 0 < relative_flight_tolerance < 1:
        raise ValueError('Require finite relative lab-flight preflight tolerance')
    result = copy.deepcopy(plan)
    for row in result['points']:
        mass, epsilon = row['mass_gev'], row['epsilon']
        width = normalized_width(native_probe(mass, epsilon, 1.), mass, epsilon)
        scale = row['sampling_epsilon'] / epsilon
        sampling = copy.deepcopy(native_probe(mass, epsilon, scale)) if scale != 1 else copy.deepcopy(width)
        sampling['total_width_gev'] = float(sampling['m_width_gev'])
        sampling['pole_support_audit'] = validate_native_proposal_support(sampling, .99 * mass, 1.01 * mass)
        data = contract(mass, epsilon, width, 'mumu', row['events'], row['seed'], row['run_number'],
                        reference['common'], reference['cp5'], sampling, scale)
        lab = row['mean_beta_gamma_reference'] * width['ctau_mm'] / 1000
        target = row['target_mean_lab_flight_m']
        if target is not None and not math.isclose(lab, target, rel_tol=relative_flight_tolerance):
            raise ValueError('Native width does not close the requested approximate lab-flight target')
        row.update(proper_ctau_mm=width['ctau_mm'], mean_lab_flight_reference_estimate_m=lab,
                   total_native_physical_width_gev=width['total_width_gev'], br_mumu=width['br_mumu'],
                   lifetime_estimate_only=False, lifetime_derived_from_native_width=True,
                   mean_lab_flight_is_estimate=True, signal_contract=data)
    result['native_width_preflight_passed'] = True
    result['relative_lab_flight_preflight_tolerance'] = relative_flight_tolerance
    return result


def write_plan(output, reference_path, template_directory, plan, runtime, scripts=None, executable=None):
    """Freeze new sources/templates/contracts without creating runtime directories."""
    output, template_directory = Path(output).resolve(), Path(template_directory).resolve()
    if output.exists():
        raise FileExistsError('Refuse to overwrite an existing grid preparation')
    if plan.get('native_width_preflight_passed') is not True:
        raise ValueError('Cannot freeze executable commands without native-width preflight')
    scripts = Path(scripts or Path(__file__).resolve().parent)
    sources = {name: scripts / name for name in SOURCES}
    templates = {f'step{i}.py': template_directory / f'step{i}.py' for i in range(1, 5)}
    for path in list(sources.values()) + list(templates.values()) + [Path(reference_path)]:
        if not path.is_file():
            raise ValueError('Missing frozen dependency: ' + str(path))
    if content_digest(json.loads(Path(reference_path).read_text())) != plan['reference_content_sha256']:
        raise ValueError('Width/boost reference changed during planning')
    for name, expected in runtime.get('source_preflight_sha256', {}).items():
        if name not in sources or digest(sources[name]) != expected:
            raise ValueError('Native preflight source changed before freezing: ' + name)
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / 'frozen_workflow' / 'scripts'
    frozen.mkdir(parents=True)
    frozen_templates = output / 'templates'
    frozen_templates.mkdir()
    pinned = {}
    for category, items, target in (('sources', sources, frozen), ('templates', templates, frozen_templates)):
        pinned[category] = {}
        for name, source in items.items():
            destination = target / name
            shutil.copy2(source, destination)
            if digest(source) != digest(destination):
                raise ValueError('Frozen copy differs: ' + name)
            pinned[category][name] = dict(source=str(source.resolve()), frozen_copy=str(destination), sha256=digest(destination))
    reference_copy = output / 'references.json'
    shutil.copy2(reference_path, reference_copy)
    if digest(reference_copy) != digest(reference_path):
        raise ValueError('Frozen width/boost reference copy differs')
    pinned['references'] = dict(source=str(Path(reference_path).resolve()), frozen_copy=str(reference_copy), sha256=digest(reference_copy))
    result = copy.deepcopy(plan)
    result.update(schema='shift-dark-photon-grid-plan-v1', prepared=True, launched=False,
                  physics_valid=False, production_rate_validated=False, normalization_ready=False,
                  detector_acceptance_validated=False, classifier_transfer_validated=False,
                  frozen_dependencies=pinned, runtime=runtime,
                  validation_requirements=list(VALIDATION_REQUIREMENTS),
                  detector_scope='Bounded unchanged V10 no-pileup/Fake2-HLT background-chain replay; no final recorded-event acceptance')
    python = str(executable or sys.executable)
    contracts = output / 'points'
    contracts.mkdir()
    for row in result['points']:
        point = contracts / row['point']
        point.mkdir()
        contract_path = point / 'contract.json'
        contract_path.write_text(json.dumps(row.pop('signal_contract'), indent=2, allow_nan=False) + '\n')
        gen = output / 'runs' / row['point'] / 'gen'
        detector = output / 'runs' / row['point'] / 'detector'
        row.update(signal_contract=str(contract_path), signal_contract_sha256=digest(contract_path),
                   gen_directory=str(gen), detector_directory=str(detector))
        row['gen_argv'] = [python, str(frozen / 'run_dark_photon_gen.py'), '--mass', repr(row['mass_gev']),
                           '--epsilon', repr(row['epsilon']), '--sampling-epsilon', repr(row['sampling_epsilon']),
                           '--events', str(row['events']), '--seed', str(row['seed']),
                           '--run-number', str(row['run_number']), '--output', str(gen)]
        row['detector_argv'] = [python, str(frozen / 'run_dark_photon_detector.py'), str(gen),
                                '--count', str(row['detector_count']), '--job', str(row['detector_job']),
                                '--templates', str(frozen_templates), '--output', str(detector)]
        row['gen_command'] = shlex.join(row['gen_argv'])
        row['detector_command'] = shlex.join(row['detector_argv'])
        row['launch_order'] = 'GEN and full-GEN audit first; detector canary only after completed GEN validation'
    (output / 'plan.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--references', required=True, type=Path)
    parser.add_argument('--templates', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--masses', nargs='+', type=float, default=[15., 30., 50.])
    columns = parser.add_mutually_exclusive_group()
    columns.add_argument('--mean-lab-flights-m', nargs='+', type=float)
    columns.add_argument('--epsilons', nargs='+', type=float)
    parser.add_argument('--prompt-epsilon', type=float, default=.001)
    parser.add_argument('--sampling-epsilon', type=float, default=.005)
    parser.add_argument('--events', type=int, default=100)
    parser.add_argument('--detector-count', type=int, default=2)
    parser.add_argument('--seed-base', type=int, default=24693357)
    parser.add_argument('--detector-job-base', type=int, default=910001)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refuse to overwrite an existing grid preparation')
    if not os.environ.get('CMSSW_BASE') or not shutil.which('cmsRun'):
        parser.error('Enter the existing built CMSSW runtime; no build or generation is performed')
    reference = json.loads(args.references.read_text())
    plan = plan_points(reference, args.masses, args.mean_lab_flights_m or (5., 30.), args.epsilons,
                       args.prompt_epsilon, args.sampling_epsilon, args.events, args.detector_count,
                       args.seed_base, args.detector_job_base)
    from Configuration.Generator.Pythia8CommonSettings_cfi import pythia8CommonSettingsBlock
    from Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi import pythia8CP5SettingsBlock
    from fixed_target_generation import canonical_settings
    current_common = list(pythia8CommonSettingsBlock.pythia8CommonSettings)
    current_cp5 = list(pythia8CP5SettingsBlock.pythia8CP5Settings)
    for current, expected in ((current_common, reference['common']), (current_cp5, reference['cp5'])):
        if canonical_settings(current) != canonical_settings(expected):
            raise ValueError('Frozen reference tune differs from the current CMSSW runtime')
    # Match the existing GEN runner's native loader isolation; no shared files change.
    os.environ['LD_LIBRARY_PATH'] = ':'.join(p for p in os.environ.get('LD_LIBRARY_PATH', '').split(':') if '/biglib/' not in p)
    from dark_photon_pythia import measure_widths
    source_preflight = {name: digest(Path(__file__).resolve().parent / name) for name in SOURCES}
    def native_probe(mass, epsilon, scale):
        return measure_widths(current_common + current_cp5 + production_settings(mass, epsilon, scale)
                              + ['Beams:frameType = 2', 'Beams:eA = 0.', 'Beams:eB = 6800.'])
    plan = preflight_points(plan, reference, native_probe)
    base = Path(os.environ['CMSSW_BASE'])
    runtime = dict(cmssw_base=str(base), cmssw_version=os.environ.get('CMSSW_VERSION'),
                   scram_arch=os.environ.get('SCRAM_ARCH'), python=sys.version, python_executable=sys.executable,
                   source_preflight_sha256=source_preflight,
                   cmssw_git_head=subprocess.check_output(['git', '-C', str(base / 'src'), 'rev-parse', 'HEAD'], text=True).strip(),
                   pythia_tool=subprocess.check_output(['scram', 'tool', 'info', 'pythia8'], cwd=base / 'src', text=True),
                   width_preflight='native initialized Pythia widths with ProcessLevel:all=off; zero generated events')
    result = write_plan(args.output, args.references, args.templates, plan, runtime)
    print(json.dumps(dict(output=str(args.output.resolve()), points=len(result['points']),
                          grid_mode=result['grid_mode'], native_width_preflight_passed=True,
                          launched=False, physics_valid=False, normalization_ready=False)), flush=True)


if __name__ == '__main__':
    main()
