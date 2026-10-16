#!/usr/bin/env python3
# Code style: AI-code. AI-generated fixed-M meson GEN and production supervisor.
"""Keep fixed source M separate from random signal N through published plots.

Each source runs once, with its complete ledger retained on EOS. A successful
audit determines the detector budget afterwards. Both operational canaries
must reach merged histograms and publication before bulk source admission.
"""
import argparse
import copy
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import traceback

import run_dark_photon_statistics as durable


def read(path):
    return json.loads(Path(path).read_text())


def verify(directory, seal_digest):
    directory = Path(directory)
    if durable.sha(directory / 'seal.json') != seal_digest:
        raise ValueError('Meson deployment seal changed')
    seal = read(directory / 'seal.json')
    for path, expected in seal['files'].items():
        if durable.sha(path) != expected:
            raise ValueError('Frozen meson dependency changed: ' + path)
    config = read(directory / 'config.json')
    if config.get('schema') != 'shift-dp-meson-production-v1' or config['directory'] != str(directory):
        raise ValueError('Wrong meson deployment configuration')
    for batch in config['batches']:
        path=batch_directory(config,batch)
        if (path/'source.sub').read_text()!=(path/'source.sub.in').read_text().replace('SEAL_DIGEST',seal_digest):
            raise ValueError('Source submit description differs from its frozen template')
    for path, record in read(config['runtime_fingerprint'])['files'].items():
        if durable.sha(path) != record['sha256']:
            raise ValueError('Shared qualified CMSSW runtime changed: ' + path)
    return config


def batch_directory(config, batch):
    return Path(config['directory']) / 'batches' / batch['batch']


def operational_configuration(original,path,expected):
    """Adopt a separately sealed controller without changing live GEN sources."""
    path=Path(path)
    seal_path=path.parent/'seal.json'
    if durable.sha(seal_path)!=expected:
        raise ValueError('Replacement controller seal changed')
    seal=read(seal_path)
    if seal['files'].get(str(path))!=durable.sha(path):
        raise ValueError('Replacement configuration is outside its seal')
    for name,digest in seal['files'].items():
        if durable.sha(name)!=digest:
            raise ValueError('Replacement controller dependency changed: '+name)
    config=read(path)
    immutable=set(original)-{'tools'}
    if any(config.get(key)!=original[key] for key in immutable):
        raise ValueError('Replacement controller changed frozen production settings')
    if config.get('physics_tools')!=original['tools']:
        raise ValueError('Replacement controller lost the original GEN source authority')
    return config


def adopted_row(batch, gen):
    """Adopt all conditional signal events; do not reinterpret source M as N."""
    gen = Path(gen)
    contract, audit = read(gen / 'contract.json'), read(gen / 'validation.json')
    count = contract['requested_events']
    if (contract.get('production_mechanism') != 'eta-etaprime-radiative'
            or type(count) is not int or count <= 0 or audit.get('runtime_validated') is not True
            or audit.get('events') != count or len(audit.get('event_ids', [])) != count
            or any(contract[key] != batch[key] for key in ('mass_gev', 'epsilon', 'seed', 'run_number'))
            or contract['source_exposure']['fixed_source_collisions'] != batch['source_collisions']
            or contract['source_exposure']['accepted_signal_events'] != count):
        raise ValueError('Meson source audit or fixed-M/random-N boundary changed')
    return dict(point=batch['point'], mass_gev=batch['mass_gev'], epsilon=batch['epsilon'],
        seed=batch['seed'], run_number=batch['run_number'], events=count,
        production_mechanism='eta-etaprime-radiative',
        proper_ctau_mm=contract['physical_width_table']['ctau_mm'], sampling_epsilon=batch['epsilon'],
        target_mean_lab_flight_m=batch.get('target_mean_lab_flight_m'),
        mean_beta_gamma_reference=contract['mean_beta_gamma_reference'],
        mean_lab_flight_reference_estimate_m=contract['mean_lab_flight_reference_estimate_m'],
        signal_contract=str(gen / 'contract.json'), signal_contract_sha256=durable.sha(gen / 'contract.json'),
        gen_directory=str(gen), final_output_base=batch['final_output_base'])


def compact_files(gen):
    """The full source ledger and generated payloads never enter an AFS seal."""
    result = []
    for path in sorted(Path(gen).rglob('*')):
        if (not path.is_file() or '__pycache__' in path.parts or path.suffix in ('.root', '.hepmc', '.pyc')
                or path.name in ('meson_source_ledger.jsonl', 'meson_source_ledger.jsonl.gz')):
            continue
        if path.stat().st_size > 10000000:
            raise ValueError('Unexpected large file in compact GEN metadata: ' + str(path))
        result.append(path)
    if sum(path.stat().st_size for path in result) > 64000000:
        raise ValueError('Compact GEN metadata exceeds its reviewed AFS allowance')
    return result


def source(config, seal_digest, batch):
    from export_dark_photon_cross_sections import publish_file_rpc
    import run_dark_photon_gen_stage as stage
    directory = batch_directory(config, batch)
    status_path = directory / 'source_status.json'
    if status_path.exists():
        previous = read(status_path)
        if previous.get('complete') and previous.get('seal_sha256') == seal_digest:
            for path, expected in previous['metadata_sha256'].items():
                if durable.sha(path) != expected:
                    raise ValueError('Completed source metadata changed: ' + path)
            return previous
        raise RuntimeError('This source already started; preserve its intent and failure for review')
    status = dict(schema='shift-dp-meson-source-status-v1', complete=False, batch=batch['batch'],
        point=batch['point'], source_collisions=batch['source_collisions'], seal_sha256=seal_digest,
        started_utc=datetime.now(timezone.utc).isoformat())
    durable.save(status_path, status)
    stop = threading.Event()
    probe = {'gen': None, 'phase': 'starting'}

    def heartbeat():
        while not stop.is_set():
            gen = probe['gen']
            data = dict(batch=batch['batch'], heartbeat_utc=datetime.now(timezone.utc).isoformat(),
                        worker_pid=os.getpid(), phase=probe['phase'])
            if gen is not None:
                data['source_ledger_bytes'] = ((gen/'meson_source_ledger.jsonl').stat().st_size
                    if (gen/'meson_source_ledger.jsonl').is_file() else 0)
                if (gen/'manifest.json').is_file():
                    try:
                        data['GEN_status'] = read(gen/'manifest.json').get('status')
                    except (ValueError, OSError):
                        pass  # A concurrent atomic status write can be incomplete.
            durable.save(directory/'source_heartbeat.json',data)
            stop.wait(30)

    observer = threading.Thread(target=heartbeat,daemon=True)
    observer.start()
    scratch_parent = os.environ.get('_CONDOR_SCRATCH_DIR', '/tmp')
    try:
        stage.check_afs_margin(dict(config, workspace=config['workspace'],
                                   retain_local_gen_root=False, afs_metadata_reservation_mb=128))
        with tempfile.TemporaryDirectory(prefix='dp-meson-source-', dir=scratch_parent) as temporary:
            scratch = Path(temporary)
            os.environ['TMPDIR'] = str(scratch)
            gen = scratch / 'gen'
            probe.update(gen=gen,phase='GEN')
            runner = Path(config['tools']) / 'run_dark_photon_meson_gen.py'
            argv = ['python3', str(runner), '--mass', str(batch['mass_gev']), '--epsilon', str(batch['epsilon']),
                '--source-collisions', str(batch['source_collisions']), '--seed', str(batch['seed']),
                '--run-number', str(batch['run_number']), '--model-card', config['model_card'],
                '--darkcast-path', config['darkcast_path'], '--darkcast-source-receipt', config['darkcast_source_receipt'],
                '--timeout', str(config['gen_timeout_seconds']), '--output', str(gen)]
            shell = ('source /cvmfs/cms.cern.ch/cmsset_default.sh && cd ' +
                shlex.quote(str(Path(config['cmssw_base']) / 'src')) +
                ' && eval "$(scram runtime -sh)" && cd ' + shlex.quote(str(scratch)) +
                ' && export PYTHONDONTWRITEBYTECODE=1 && exec ' + shlex.join(argv))
            with (directory / 'logs/GEN.log').open('x') as log:
                result = subprocess.run(['/bin/bash', '-c', shell], cwd=directory,
                    env=stage.native_env(), stdout=log, stderr=subprocess.STDOUT,
                    timeout=config['gen_timeout_seconds'] + 900, check=False)
            # Keep the complete source accounting even if a fixed exposure
            # returns zero signals. A zero source is never rerun automatically.
            ledger = gen / 'meson_source_ledger.jsonl'
            if ledger.is_file():
                probe['phase']='ledger_publication'
                compressed = scratch / 'meson_source_ledger.jsonl.gz'
                with ledger.open('rb') as incoming, compressed.open('xb') as raw:
                    with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as outgoing:
                        shutil.copyfileobj(incoming, outgoing, 1048576)
                publication = publish_file_rpc(compressed,
                    batch['final_output_base'] + '/metadata/GEN/meson_source_ledger.jsonl.gz')
                publication.update(schema='shift-meson-source-ledger-publication-v1', encoding='gzip',
                    uncompressed_bytes=ledger.stat().st_size, uncompressed_sha256=durable.sha(ledger))
                # Independent streamed readback of the original, uncompressed bytes.
                digest, size = hashlib.sha256(), 0
                with gzip.open(compressed, 'rb') as incoming:
                    for block in iter(lambda: incoming.read(1048576), b''):
                        digest.update(block); size += len(block)
                if size != publication['uncompressed_bytes'] or digest.hexdigest() != publication['uncompressed_sha256']:
                    raise ValueError('Lossless source ledger compression changed the full source exposure')
                durable.new(gen / 'meson_ledger_publication.json', publication)
                status['ledger_publication'] = publication
                durable.save(status_path, status)
            # Always retain compact source evidence before raising a GEN failure.
            if (gen / 'manifest.json').exists():
                archive = scratch / 'gen_metadata.tar.gz'
                with tarfile.open(archive, 'w:gz') as output:
                    for path in compact_files(gen):
                        output.add(path, arcname=str(path.relative_to(gen)), recursive=False)
                status['metadata_archive'] = publish_file_rpc(archive,
                    batch['final_output_base'] + '/metadata/GEN/gen_metadata.tar.gz')
                durable.save(status_path, status)
            result.check_returncode()
            row = adopted_row(batch, gen)
            probe['phase']='GEN_publication'
            row['gen_argv'] = ['python3', str(runner)]
            publication, parent = stage.publish_gen(row, dict(config, scratch_base=str(scratch),
                retain_local_gen_root=False, eos_quota_space='/eos/user/j/jniedzie/'))
            final_gen = directory / 'gen'
            pending_gen = directory / 'gen.next'
            pending_gen.mkdir()
            for path in compact_files(gen):
                target = pending_gen / path.relative_to(gen)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            proof = read(pending_gen / 'root_publication.json')
            proof['local_path'] = str(final_gen / 'gen.root')
            durable.save(pending_gen / 'root_publication.json', proof)
            os.rename(pending_gen, final_gen)
            status.update(complete=True, events=row['events'], publication=publication, parent=parent,
                metadata_sha256={str(path): durable.sha(path) for path in compact_files(final_gen)},
                finished_utc=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        status.update(error=repr(error), traceback=traceback.format_exc()[-10000:])
        raise
    finally:
        stop.set()
        observer.join(timeout=10)
        durable.save(status_path, status)
    return status


def adopt(config, batch):
    import prepare_dark_photon_grid as grid
    directory = batch_directory(config, batch)
    status = read(directory / 'source_status.json')
    if status.get('complete') is not True:
        raise ValueError('Detector adoption requires a complete audited published source')
    for path, expected in status['metadata_sha256'].items():
        if durable.sha(path) != expected:
            raise ValueError('Published GEN metadata changed before adoption')
    row = adopted_row(batch, directory / 'gen')
    chunk_size=batch.get('events_per_job',config['events_per_job'])
    if math.ceil(row['events']/chunk_size) + 1 > batch['detector_job_capacity']:
        raise ValueError('Actual N needs a separately reviewed larger detector namespace; do not truncate it')
    physical = dict(points=[row], production_mechanism='eta-etaprime-radiative',
        physical_width_preflight_passed=True, meson_width_qualification=grid.meson_width_qualification([row]))
    plan = grid.plan_detector_stage(physical, batch['campaign'], config['eos_base'],
        events_per_job=chunk_size, group_events=config['group_events'],
        detector_job_base=batch['detector_job_base'])
    plan['runtime'] = dict(cmssw_base=config['cmssw_base'])
    from run_dark_photon_meson_gen import SOURCES as MESON_SOURCES
    sources=set(grid.SOURCES)|set(MESON_SOURCES)
    plan['frozen_dependencies'] = dict(sources={name: dict(frozen_copy=str(Path(config['tools'])/name),
        sha256=durable.sha(Path(config['tools'])/name)) for name in sorted(sources)},
        templates={name:dict(frozen_copy=str(path),sha256=durable.sha(path))
            for path in sorted((Path(config['directory'])/'templates').glob('step*.py')) for name in [path.name]})
    # Generic downstream workers consume the real audited GEN source directly.
    row = plan['points'][0]
    row['gen_argv'] = ['python3', str(Path(config.get('physics_tools',config['tools']))/'run_dark_photon_meson_gen.py')]
    preparation = directory / 'preparation.json'
    if preparation.exists():
        if read(preparation) != plan:
            raise ValueError('An earlier post-GEN detector adoption differs')
    else:
        durable.new(preparation, plan)
    inventory = dict(schema='shift-independent-signal-GEN-inventory-v1', complete=True,
        points={batch['point']:status['parent']}, each_parent_ledger_counted_once=True,
        technical_trials_are_not_collision_exposure=True, source_exposure_fixed_before_signal_sampling=True,
        fixed_unfiltered_source_collisions={batch['point']:batch['source_collisions']})
    publication = dict(schema='shift-stage-GEN-publication-v1', complete=True, inputs=[status['publication']])
    for name, value in [('gen_inventory.json', inventory), ('gen_publication.json', publication)]:
        path = directory / name
        if path.exists():
            if read(path) != value:
                raise ValueError('Previous meson handoff inventory changed')
        else:
            durable.new(path, value)
    return plan


def handoff(config, batch):
    directory = batch_directory(config, batch)
    plan = adopt(config, batch)
    production = directory / 'production'
    if not production.exists():
        budget = copy.deepcopy(read(config['storage_budget_template']))
        budget.update(events=plan['events'], detector_chunks=plan['detector_chunks'], points=1,
            external_reservations=[], fixed_reservation_bytes=config['minimum_eos_free_bytes'],
            gen_group_file_allowance=4000, safety_free_floor_files=5000)
        durable.new(directory / 'storage_budget.json', budget)
        durable.run_logged([os.sys.executable, Path(config['tools'])/'prepare_dark_photon_production.py',
            '--preparation', directory/'preparation.json', '--template', config['qualified_template'],
            '--gen-inventory', directory/'gen_inventory.json', '--gen-publication', directory/'gen_publication.json',
            '--storage-budget', directory/'storage_budget.json', '--output', production,
            '--prior-inventories', *config['prior_inventories'], '--canary-points', batch['point'],
            '--failure-base', config['failure_base'], '--detector-workers', '100', '--finalizers', '2',
            '--publication-profile', 'nano-metadata-v1', '--publication-canary', config['publication_canary'],
            '--event-stall-timeout','7200','--first-event-stall-timeout','7200','--detector-timeout','43200'],
            directory/'prepare_production.log', timeout=1800)
    receipt = (existing_receipt(production, production/'pipeline.dag.condor.sub')
               if (production/'submission.json').is_file() else durable.submit_dag(config, production, 102))
    rates = directory / 'rates.json'
    if not rates.exists():
        durable.run_logged([os.sys.executable, Path(config['tools'])/'export_dark_photon_cross_sections.py',
            '--plan', production/'plan.json', '--output', directory/'rates', '--publish', '--eos-rpc'],
            directory/'export_rates.log', timeout=1800)
        durable.new(rates, dict(complete=True, production_plan_sha256=durable.sha(production/'plan.json')))
    return receipt


def adaptive_reservation(config):
    root = Path(config['adaptive_directory'])
    worklist, state = read(root/'worklist.json'), read(root/'state.json')
    if ((not state.get('complete') and
            time.time() - datetime.fromisoformat(state['checked_utc']).timestamp() > 1200) or state.get('error')):
        raise ValueError('Adaptive reservation authority is stale or reports an error')
    # Only closed, published new populations release future storage claims.
    credited=0
    for wave in worklist['waves']:
        plan_path=root/'waves'/f'w{wave["wave_index"]:03d}'/'production/plan.json'
        if not all(str(plan_path) in state.get('published',{}).get(row['point'],{}).get('source_plans',[])
                   for row in wave['batches']):
            continue
        actual={row['point']:row for row in read(plan_path)['points']}
        for row in wave['batches']:
            if any(actual[row['point']][key]!=row[key] for key in ('events','seed','run_number')):
                raise ValueError('Adaptive published primitive differs from its frozen worklist')
        credited+=sum(row['events'] for row in wave['batches'])
    total = worklist['total_additional_events']
    if type(credited) is not int or not 0 <= credited <= total:
        raise ValueError('Adaptive published-event credit is inconsistent')
    return 2000000000 + (total - credited)*326000


def storage_gate(config, observation, bulk=False, state=None):
    reservation = adaptive_reservation(config)
    additional = 500000000 if not bulk else None
    if bulk:
        path = config.get('bulk_storage_gate')
        if not path or not Path(path).is_file():
            return False, dict(reason='waiting_for_reviewed_bulk_storage_and_seven_mass_GEN_gates')
        gate = read(path)
        if (gate.get('complete') is not True or gate.get('all_seven_mass_GEN_audits_passed') is not True
                or gate.get('model_card_sha256') != durable.sha(config['model_card'])
                or gate.get('worklist_sha256') != durable.sha(Path(config['directory'])/'config.json')
                or type(gate.get('total_meson_forecast_bytes')) is not int
                or not 0 < gate['total_meson_forecast_bytes'] <= 30000000000):
            raise ValueError('Bulk storage/physics gate differs from this frozen meson worklist')
        forecasts=gate.get('per_batch_forecast_bytes',{})
        if (set(forecasts)!={row['batch'] for row in config['batches']}
                or any(type(value) is not int or value<=0 for value in forecasts.values())
                or sum(forecasts.values())!=gate['total_meson_forecast_bytes']):
            raise ValueError('Bulk storage forecast does not cover every fixed source batch')
        published=(state or {}).get('batches',{})
        additional=sum(value for batch,value in forecasts.items() if not published.get(batch,{}).get('published'))
    required = config['minimum_eos_free_bytes'] + reservation + additional
    return observation['free_bytes'] >= required and observation['free_files'] >= config['minimum_eos_free_files'], dict(
        free_bytes=observation['free_bytes'], protected_adaptive_bytes=reservation,
        meson_forecast_bytes=additional, required_free_bytes=required)


def cumulative_result(config,state,batch,transport,primitive):
    """Keep the independent canary population when the bulk batch completes."""
    point=batch['point']
    condition=config['conditions']['sha256']
    sources=[]
    for other in config['batches']:
        production=batch_directory(config,other)/'production'
        report=production/'completed_points'/(point+'.json')
        if other['point']!=point or not report.is_file() or read(report).get('complete') is not True:
            continue
        sources.append(durable.authority(production,point,condition))
    if len(sources)<2:
        return primitive
    pending=durable.reconcile_aggregate(config,state,point,sources)
    signature=hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest()
    if state.get('published',{}).get(point,{}).get('sources_sha256')==signature:
        return state['published'][point]
    if pending is None:
        root=Path(config['directory'])/'aggregates'/point
        version=max((int(path.name[1:]) for path in root.glob('v*')),default=0)+1
        directory=root/f'v{version:04d}'
        guard=batch_directory(config,next(row for row in config['batches'] if row['operational_canary']))/'production'
        durable.aggregate_plan(dict(config,aggregation_guard=str(guard)),point,sources,directory)
        pending=durable.aggregate_record(directory,point,sources)
        durable.new(directory/'aggregate_intent.json',pending)
        state.setdefault('aggregates',{})[point]=pending
        durable.save(Path(config['directory'])/'state.json',state)
    directory=Path(pending['directory'])
    receipt=(existing_receipt(directory,directory/'aggregate.sub') if (directory/'submission.json').is_file()
             else durable.submission(config,directory,directory/'aggregate.sub'))
    pending['cluster']=receipt['cluster']
    durable.save(Path(config['directory'])/'state.json',state)
    if native_status(config,receipt)!='complete':
        return None
    from watch_dark_photon_production import completed_histogram
    plan=read(directory/'plan.json')
    result=completed_histogram(dict(source=dict(directory=directory,campaign=plan['campaign'],
        plan_sha256=durable.sha(directory/'plan.json'),seal_sha256=durable.sha(directory/'seal.json')),
        row=plan['points'][0],output=Path(plan['points'][0]['final_output_base'])),transport)
    if result is None:
        raise ValueError('Cumulative meson manager exited without its complete histogram receipt')
    result.update(sources_sha256=pending['sources_sha256'],source_plans=pending['source_plans'])
    return result


def publish_batch(config, state, batch):
    from watch_dark_photon_production import EosRpc, completed_histogram
    directory = batch_directory(config, batch)
    production = directory/'production'
    if not (production/'plan.json').is_file() or not (directory/'rates.json').is_file():
        return None
    plan = read(production/'plan.json')
    cache = directory/'readback'
    transport = EosRpc('root://eosuser.cern.ch', cache)
    try:
        result = completed_histogram(dict(source=dict(directory=production,campaign=plan['campaign'],
            plan_sha256=durable.sha(production/'plan.json'),seal_sha256=durable.sha(production/'seal.json')),
            row=plan['points'][0],output=Path(plan['points'][0]['final_output_base'])),transport)
        if result is None:
            return None
        result=cumulative_result(config,state,batch,transport,result)
        if result is None:
            return None
        from export_dark_photon_cross_sections import publish_file_rpc
        contract=read(directory/'gen/contract.json')
        selector=batch.get('target_mean_lab_flight_m')
        sample=dict(schema='shift-dark-photon-sample-v1', point=batch['point'], original_point=batch['point'],
            naming='mass-and-mean-lab-flight',selector_kind='mean_lab_flight',mass_gev=batch['mass_gev'],
            epsilon=batch['epsilon'],proper_ctau_mm=contract['physical_width_table']['ctau_mm'],
            sampling_epsilon=batch['epsilon'],target_mean_lab_flight_m=selector,mean_lab_flight_m=selector,
            reference_mean_lab_flight_m=batch['calibration_mean_beta_gamma']*contract['physical_width_table']['ctau_mm']/1000.,
            calibration_mean_beta_gamma=batch['calibration_mean_beta_gamma'],
            mean_lab_flight_is_estimate=selector is not None,
            production_mechanism='eta-etaprime-radiative',
            calibration_sha256=config['calibration_authority']['sha256'])
        sample_path=directory/'sample.json'
        if sample_path.exists() and read(sample_path)!=sample:
            raise ValueError('Existing meson sample parameters changed')
        if not sample_path.exists():
            durable.new(sample_path,sample)
        publish_file_rpc(sample_path,config['eos_base']+'/darkphoton_'+batch['point']+'/sample.json')
        parameters=dict(sample,mean_lab_flight_reference_estimate_m=contract['mean_lab_flight_reference_estimate_m'],
            mean_beta_gamma_reference=contract['mean_beta_gamma_reference'],
            mean_beta_gamma_reference_standard_error=contract['mean_beta_gamma_reference_standard_error'],
            reference_definition='Independent fixed-M source estimate; shared LAB selector is the calibration anchor',
            fixed_source_collisions=batch['source_collisions'],accepted_signal_events=contract['requested_events'])
        parameters_path=directory/'sample_parameters.json'
        if not parameters_path.exists():
            durable.new(parameters_path,parameters)
        elif read(parameters_path)!=parameters:
            raise ValueError('Per-source meson parameters changed')
        publish_file_rpc(parameters_path,batch['final_output_base']+'/sample_parameters.json')
        # Replacing another production mechanism requires an explicit reviewed
        # alias target in the request, rather than silently combining models.
        alias = config['eos_base']+'/darkphoton_'+batch['point']+'/latest'
        old = transport.alias_target(alias)
        allowed = list(config.get('allowed_previous_aliases',{}).get(batch['point'], []))
        allowed.extend(row['campaign'] for row in config['batches'] if row['point']==batch['point'])
        if old is not None and old != result['campaign'] and old not in allowed:
            raise ValueError('Existing latest target needs reviewed ownership: '+str(old))
        transport.publish_alias(alias,result['campaign'],[None,result['campaign'],*allowed])
        result.update(published_utc=datetime.now(timezone.utc).isoformat(),batch=batch['batch'])
        durable.save(directory/'publication.json',result)
        if result.get('sources_sha256'):
            state.setdefault('published',{})[batch['point']]=result
            state.setdefault('aggregates',{}).pop(batch['point'],None)
        return result
    finally:
        for path in cache.glob('*'):
            if path.is_file():
                path.unlink()


def native_status(config, receipt):
    rows=durable.jobs(config,'ClusterId == '+str(receipt['cluster'])+' || DAGManJobId == '+str(receipt['cluster']))
    if rows:
        managers=[row for row in rows if row['ClusterId']==receipt['cluster']]
        children=[row for row in rows if row['ClusterId']!=receipt['cluster']]
        if len(managers)!=1 or managers[0].get('Iwd')!=receipt['task'] or managers[0].get('ProcId')!=0:
            raise RuntimeError('Meson manager has an unexpected native identity')
        if any(row['JobStatus'] in (3,5) for row in rows):
            raise RuntimeError('Meson manager or child is removed or held: '+str(receipt['cluster']))
        if managers[0]['JobStatus']==4:
            if managers[0].get('ExitBySignal') or managers[0].get('ExitCode')!=0:
                raise RuntimeError('Meson manager completed unsuccessfully; no automatic retry')
            return 'complete'
        if any(row.get('Iwd')!=receipt['task'] for row in children):
            raise RuntimeError('Meson child has an unexpected persistent working directory')
        directory=Path(receipt['task'])
        health_path=directory/'manager_health.json'
        health=read(health_path) if health_path.exists() else {}
        if (directory/'pipeline.dag').is_file():
            from watch_dark_photon_statistics import execute_events
            health.update(executed_children=execute_events(directory),children=len(children),
                          checked_utc=datetime.now(timezone.utc).isoformat())
            if managers[0]['JobStatus']==2 and not children:
                since=health.setdefault('no_children_since_epoch',time.time())
                if time.time()-since>3600:
                    raise RuntimeError('Running meson DAG has no child jobs for over an hour')
            else:
                health.pop('no_children_since_epoch',None)
            durable.save(health_path,health)
        return 'active'
    output=durable.run_condor(['/usr/bin/condor_history',*durable.scheduler_arguments(config),
        str(receipt['cluster'])+'.0','-limit','1','-json'],timeout=45)
    rows=durable.parse_ads(output)
    if (len(rows)!=1 or rows[0].get('Iwd')!=receipt['task'] or rows[0].get('ProcId')!=0
            or rows[0].get('ClusterId')!=receipt['cluster']
            or rows[0].get('ExitBySignal') or rows[0].get('ExitCode')!=0):
        raise RuntimeError('Meson manager is missing or failed; no automatic retry: '+str(receipt['cluster']))
    return 'complete'


def existing_receipt(directory, description):
    receipt=read(Path(directory)/'submission.json')
    if (receipt.get('task')!=str(directory) or receipt.get('submit_sha256')!=durable.sha(description)
            or type(receipt.get('cluster')) is not int or receipt['cluster']<=0):
        raise ValueError('Existing meson submission receipt changed')
    return receipt


def step(config,state):
    state.update(checked_utc=datetime.now(timezone.utc).isoformat(),heartbeat=state.get('heartbeat',0)+1)
    entries=state.setdefault('batches',{})
    source_active, detector_active=0,0
    for batch in config['batches']:
        entry=entries.setdefault(batch['batch'],{})
        directory=batch_directory(config,batch)
        if entry.get('published'):
            continue
        source_receipt=directory/'submission.json'
        if not source_receipt.exists() and not (directory/'submission_intent.json').exists():
            continue
        source_job=(existing_receipt(directory,directory/'source.sub') if source_receipt.is_file()
                    else durable.submission(config,directory,directory/'source.sub'))
        status=native_status(config,source_job)
        entry.update(source_cluster=source_job['cluster'],stage='GEN')
        if status=='active':
            heartbeat=directory/'source_heartbeat.json'
            if heartbeat.is_file():
                age=time.time()-datetime.fromisoformat(read(heartbeat)['heartbeat_utc']).timestamp()
                if age>600:
                    raise RuntimeError('Executing source heartbeat is stale: '+batch['batch'])
                entry['source_progress']=read(heartbeat)
            elif time.time()-datetime.fromisoformat(source_job['utc']).timestamp()>21600:
                raise RuntimeError('Source has no execution heartbeat after six hours: '+batch['batch'])
            source_active+=1
            continue
        if not (directory/'source_status.json').is_file() or not read(directory/'source_status.json').get('complete'):
            raise RuntimeError('Native source exited without complete source publication')
        production=directory/'production'
        if not (production/'submission.json').exists() and not (production/'submission_intent.json').exists():
            if detector_active>=config['simultaneous_detector_points']:
                continue
        detector_job=handoff(config,batch)
        entry.update(detector_cluster=detector_job['cluster'],stage='detector')
        if native_status(config,detector_job)=='active':
            detector_active+=1
        published=publish_batch(config,state,batch)
        if published:
            entry.update(stage='published',published=published)
            durable.save(Path(config['directory'])/'state.json',state)
    canaries=[row for row in config['batches'] if row['operational_canary']]
    canary_ready=all(entries[row['batch']].get('published') for row in canaries)
    state['canary_published']=canary_ready
    observation=durable.quota(config)
    for batch in config['batches']:
        entry=entries[batch['batch']]
        if entry.get('source_cluster'):
            continue
        if not batch['operational_canary'] and not canary_ready:
            continue
        if source_active>=config['simultaneous_sources']:
            break
        allowed,evidence=storage_gate(config,observation,bulk=not batch['operational_canary'],state=state)
        state['storage_admission']=evidence
        if not allowed:
            continue
        directory=batch_directory(config,batch)
        receipt=durable.submission(config,directory,directory/'source.sub')
        entry.update(source_cluster=receipt['cluster'],stage='GEN')
        durable.save(Path(config['directory'])/'state.json',state)
        source_active+=1
    state['complete']=all(item.get('published') for item in entries.values())
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--seal',required=True)
    parser.add_argument('--action',choices=('source','controller'),required=True)
    parser.add_argument('--batch')
    parser.add_argument('--controller-config',type=Path)
    parser.add_argument('--controller-seal')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    config=verify(args.directory.resolve(),args.seal)
    if args.controller_config is not None:
        if args.action!='controller' or args.controller_seal is None:
            raise ValueError('Only the controller may adopt a separately sealed operational configuration')
        config=operational_configuration(config,args.controller_config.resolve(),args.controller_seal)
    if args.action=='source':
        rows=[row for row in config['batches'] if row['batch']==args.batch]
        if len(rows)!=1:
            raise ValueError('Source batch has no unique frozen owner')
        source(config,args.seal,rows[0])
        return
    path=args.directory/'state.json'
    with (args.directory/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=read(path) if path.exists() else {}
        while True:
            try:
                state.pop('error',None)
                state=step(config,state)
            except Exception as error:
                state.update(error=repr(error),checked_utc=datetime.now(timezone.utc).isoformat())
            durable.save(path,state)
            if args.once or state.get('complete'):
                return
            time.sleep(60)


if __name__=='__main__':
    main()
