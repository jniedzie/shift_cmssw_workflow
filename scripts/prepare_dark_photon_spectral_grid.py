#!/usr/bin/env python3
# Code style: AI-code. AI-generated spectral-width GEN and detector grid preparation.
"""Freeze a complete 2--12 GeV spectral-width DY grid without submitting jobs."""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import prepare_dark_photon_grid as grid
from fixed_target_generation import beam_settings, canonical_settings
from run_dark_photon_spectral_gen import (
    build_contract, freeze_spectral_inputs, native_proposal_settings,
    physical_width_table, validate_point,
)


def plan_points(reference, masses, flights, events, seed_base, detector_job_base,
                sampling_epsilon=.01, prompt_epsilon=.001, epsilons=None):
    if reference.get('schema') != grid.REFERENCE_SCHEMA or reference.get('sample_kind') != 'simulation_only':
        raise ValueError('Require explicit simulation width/boost references')
    refs = {}
    for row in reference['references']:
        mass = float(row['mass_gev'])
        validate_point(mass, float(row['epsilon']), sampling_epsilon)
        if mass in refs or not math.isfinite(row['mean_beta_gamma']) or row['mean_beta_gamma'] <= 0:
            raise ValueError('Require distinct reference masses and positive unfiltered GEN boosts')
        if row['width_authority']['schema'] != 'shift-dark-photon-spectral-width-v1' or not row.get('boost_source'):
            raise ValueError('Require a spectral width authority and complete boost provenance')
        refs[mass] = row
    if not masses or len(set(masses)) != len(masses) or not set(masses) <= set(refs):
        raise ValueError('Requested unique masses must be covered by the spectral references')
    if epsilons is not None:
        epsilons = tuple(map(float, epsilons))
        if not epsilons or len(set(epsilons)) != len(epsilons):
            raise ValueError('Require distinct explicit epsilon columns')
        columns = [('epsilon_' + format(e, '.8g').replace('.', 'p'), e, None) for e in epsilons]
        mode = 'cartesian-mass-epsilon'
    else:
        if not flights or len(set(flights)) != len(flights) or any(not math.isfinite(x) or x <= 0 for x in flights):
            raise ValueError('Require distinct positive mean lab flights')
        columns = [('prompt', prompt_epsilon, None)] + [
            ('lab_' + format(flight, '.8g').replace('.', 'p') + 'm', None, flight)
            for flight in flights]
        mode = 'mass-by-approximate-mean-lab-flight'
    count = len(masses) * len(columns)
    if type(events) is not int or not 1 <= events <= 10000 or count > 30 or not 1 <= seed_base <= 899999823-count+1:
        raise ValueError('Require bounded point budgets and valid unique seeds')
    rows = []
    for mass in masses:
        ref = refs[mass]
        for name, epsilon, target in columns:
            if target is not None:
                epsilon = ref['epsilon'] * math.sqrt(
                    ref['width_authority']['ctau_mm'] * ref['mean_beta_gamma'] / (1000 * target))
            validate_point(mass, epsilon, sampling_epsilon)
            index = len(rows)
            rows.append(dict(point='m'+format(mass,'.8g').replace('.','p')+'_'+name,
                mass_gev=mass, epsilon=epsilon, target_mean_lab_flight_m=target,
                mean_beta_gamma_reference=ref['mean_beta_gamma'], boost_reference_source=ref['boost_source'],
                sampling_epsilon=sampling_epsilon, events=events, detector_count=min(2, events),
                seed=seed_base+index, run_number=seed_base+index, detector_job=detector_job_base+index))
    if len({row['point'] for row in rows}) != count:
        raise ValueError('Spectral point names collide')
    return dict(grid_mode=mode, points=rows,
                lab_flight_is_fixed=False, event_budget_mode='outputAccepted',
                reference_content_sha256=grid.content_digest(reference),
                width_provider='DarkCast2.1-visible-spectral',
                prompt_definition='physical epsilon '+format(prompt_epsilon,'.8g')+'; full spectral lifetime',
                assumption='Displaced columns are unfiltered reference mean lab flights; actual boosts and flights fluctuate')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--references',type=Path,required=True)
    parser.add_argument('--templates',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--darkcast-path',type=Path,required=True)
    parser.add_argument('--darkcast-source-receipt',type=Path,required=True)
    parser.add_argument('--masses',nargs='+',type=float,default=[2.,5.,10.])
    columns=parser.add_mutually_exclusive_group()
    columns.add_argument('--mean-lab-flights-m',nargs='+',type=float)
    columns.add_argument('--epsilons',nargs='+',type=float)
    parser.add_argument('--sampling-epsilon',type=float,default=.01)
    parser.add_argument('--events',type=int,default=50)
    parser.add_argument('--gen-timeout',type=int,default=600)
    parser.add_argument('--reuse-physical-plan',type=Path)
    parser.add_argument('--seed-base',type=int,required=True)
    parser.add_argument('--detector-job-base',type=int,required=True)
    parser.add_argument('--campaign',required=True)
    parser.add_argument('--eos-base',required=True)
    parser.add_argument('--events-per-job',type=int,default=5)
    parser.add_argument('--group-events',type=int,default=2000)
    args=parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Refuse to overwrite an existing spectral preparation')
    if not 1 <= args.gen_timeout <= 21600:
        parser.error('GEN timeout must be 1..21600 seconds')
    reference=json.loads(args.references.read_text())
    plan=plan_points(reference,args.masses,args.mean_lab_flights_m or [5.,30.,100.,1000.],
        args.events,args.seed_base,args.detector_job_base,args.sampling_epsilon,epsilons=args.epsilons)
    if args.reuse_physical_plan is not None:
        plan=grid.reuse_physical_points(plan,json.loads(args.reuse_physical_plan.read_text()),args.reuse_physical_plan)
    plan['gen_timeout_seconds']=args.gen_timeout
    from Configuration.Generator.Pythia8CommonSettings_cfi import pythia8CommonSettingsBlock
    from Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi import pythia8CP5SettingsBlock
    common=list(pythia8CommonSettingsBlock.pythia8CommonSettings)
    tune=list(pythia8CP5SettingsBlock.pythia8CP5Settings)
    if canonical_settings(common)!=canonical_settings(reference['common']) or canonical_settings(tune)!=canonical_settings(reference['cp5']):
        raise ValueError('Reference common/CP5 settings differ from current runtime')
    os.environ['LD_LIBRARY_PATH']=':'.join(p for p in os.environ.get('LD_LIBRARY_PATH','').split(':') if '/biglib/' not in p)
    sys.path.insert(0,str(args.darkcast_path.resolve()))
    import darkcast
    model=darkcast.Model('dark_photon')
    authority=copy.deepcopy(reference['references'][0]['width_authority']['spectral_source'])
    from dark_photon_pythia import _native_inspect
    for row in plan['points']:
        options=SimpleNamespace(mass=row['mass_gev'],epsilon=row['epsilon'],sampling_epsilon=row['sampling_epsilon'],
                                events=row['events'],seed=row['seed'],run_number=row['run_number'])
        width=physical_width_table(model,options.mass,options.epsilon,authority)
        grid.check_reused_lifetime(row,width['ctau_mm'])
        native=_native_inspect(common+tune+native_proposal_settings(options.mass,options.epsilon,options.sampling_epsilon)+beam_settings(6800.))
        contract=build_contract(options,width,native,common,tune)
        lab=row['mean_beta_gamma_reference']*width['ctau_mm']/1000
        target=row['target_mean_lab_flight_m']
        if target is not None and not math.isclose(lab,target,rel_tol=1.e-12):
            raise ValueError('Spectral width does not close the requested reference mean lab flight')
        row.update(signal_contract=contract,proper_ctau_mm=width['ctau_mm'],br_mumu=width['br_mumu'],
                   mean_lab_flight_reference_estimate_m=lab,lifetime_estimate_only=False,
                   lifetime_derived_from_native_width=False,lifetime_derived_from_spectral_width=True,
                   mean_lab_flight_is_estimate=True)
    plan['native_width_preflight_passed']=True
    plan['spectral_width_preflight_passed']=True
    plan=grid.plan_detector_stage(plan,args.campaign,args.eos_base,args.events_per_job,args.group_events,args.detector_job_base)
    # Extend the frozen source list only in this process. The shared native
    # planning code and its physics-domain guard stay unchanged on disk.
    grid.SOURCES=(*grid.SOURCES,'run_dark_photon_spectral_gen.py','prepare_dark_photon_spectral_grid.py')
    here=Path(__file__).resolve().parent
    base=Path(os.environ['CMSSW_BASE'])
    runtime=dict(cmssw_base=str(base),cmssw_version=os.environ.get('CMSSW_VERSION'),scram_arch=os.environ.get('SCRAM_ARCH'),
        python=sys.version,python_executable=sys.executable,source_preflight_sha256={name:grid.digest(here/name) for name in grid.SOURCES},
        cmssw_git_head=subprocess.check_output(['git','-C',str(base/'src'),'rev-parse','HEAD'],text=True).strip(),
        pythia_tool=subprocess.check_output(['scram','tool','info','pythia8'],cwd=base/'src',text=True),
        width_preflight='native natural DY proposal plus external complete physical spectral widths; no GEN generated')
    result=grid.write_plan(args.output,args.references,args.templates,plan,runtime)
    frozen_authority=freeze_spectral_inputs(args.darkcast_path.resolve(),args.darkcast_source_receipt.resolve(),args.output.resolve())
    if authority!=frozen_authority:
        raise ValueError('Pinned spectral provider differs from reference/preflight provider')
    external=args.output.resolve()/'width_inputs'
    result['frozen_dependencies']['spectral_width_source']=frozen_authority
    for row in result['points']:
        row['gen_argv'][1]=str(Path(row['gen_argv'][1]).with_name('run_dark_photon_spectral_gen.py'))
        if '--accepted-events' in row['gen_argv']:
            row['gen_argv'].remove('--accepted-events')
        row['gen_argv'].extend(['--darkcast-path',str(external),'--darkcast-source-receipt',str(external/'source_receipt.json')])
        row['gen_command']=shlex.join(row['gen_argv'])
    (args.output/'plan.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(output=str(args.output.resolve()),points=len(result['points']),events=result['events'],
                         detector_chunks=result['detector_chunks'],spectral_width_preflight_passed=True)))


if __name__=='__main__':
    main()
