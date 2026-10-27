#!/usr/bin/env python3
# Code style: AI-code. AI-generated native DAG preparation for a frozen response test.
"""Prepare, but never submit, primary gates and single-event diagnostic workers.

The old runtime, templates, dispatcher and detector builder remain unchanged.
Only new analysis/preflight helpers and a qualified addon join the old tools
capsule. Benchmark q never replaces the original Nano sampling probability p.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

from run_shift_sm_response_collection import verify_exposure, verify_worker_request


MIB = 1048576
PLUGIN = 'pluginConfigurationShiftSMKinematicsValidationPlugins.so'
REQUIRED_HELPERS = {'run_shift_sm_primary_preflight.py', 'run_shift_sm_response_collection.py',
                    'run_shift_sm_postprocessing.py', 'shift_sm_response_labels.py',
                    'summarize_shift_sm_response.py'}


WRAPPER = '''#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded failure-log adapter; physics entry unchanged.
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

request = Path(sys.argv[1]).resolve()
data = json.loads(request.read_text())
helpers = {'smart_sm_detector': 'run_shift_smart_sm_detector.py',
           'sm_primary_preflight': 'run_shift_sm_primary_preflight.py'}
stage = data['stage']
helper = Path(__file__).resolve().parent/helpers[stage]
names = ([f'step{i}{suffix}' for i in range(1, 5) for suffix in ('.log', '_config.log')]
         if stage == 'smart_sm_detector' else
         ['preflight_0000.configuration.log', 'preflight_0000.cmsRun.log'])
code = 1
try:
    code = subprocess.run([sys.executable, str(helper), str(request)]).returncode
finally:
    if code:
        directory = Path('failure_logs')
        directory.mkdir(exist_ok=True)
        for name in names[:12]:
            path = Path(name)
            if not path.is_file() or path.is_symlink():
                continue
            with path.open('rb') as source:
                source.seek(max(0, path.stat().st_size-128*1024))
                tail = source.read(128*1024)
            (directory/(name+'.tail')).write_bytes(tail)
if code < 0:
    signal.signal(-code, signal.SIG_DFL)
    os.kill(os.getpid(), -code)
sys.exit(code)
'''


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(MIB), b''):
            result.update(block)
    return result.hexdigest()


def read(path):
    with Path(path).open('rb') as source:
        return json.loads(b''.join(iter(lambda: source.read(MIB), b'')))


def read_pin(pin):
    if sha(pin['path']) != pin['sha256']:
        raise ValueError('Frozen preparer input changed: '+pin['path'])
    return read(pin['path'])


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def persistent_directory(path):
    path = Path(path)
    if (not path.is_absolute() or str(path).startswith(('/tmp/', '/var/tmp/', '/pool/', '/run/'))
            or any(c.isspace() or c == ',' for c in str(path))):
        raise ValueError('Require an absolute persistent, whitespace-free submission directory')
    return path


def checked_file(pin):
    path = Path(pin['path'])
    if not path.is_file() or path.is_symlink() or sha(path) != pin['sha256']:
        raise ValueError('Frozen capsule input changed: '+str(path))
    return path


def freeze_tools(config, directory):
    """Preserve every old tools member and add only new qualified helpers."""
    old = checked_file(config['base_tools'])
    bootstrap = checked_file(config['bootstrap'])
    addon = checked_file(config['addon'])
    receipt_path = checked_file(config['build_receipt'])
    receipt = read(receipt_path)
    if addon.name != PLUGIN or receipt['plugin_sha256'] != config['addon']['sha256']:
        raise ValueError('The qualified addon receipt and binary differ')
    frozen = Path(directory)
    tools = frozen/'tools'
    tools.mkdir(parents=True)
    with tarfile.open(old) as archive:
        members = archive.getmembers()
        if any(Path(member.name).is_absolute() or '..' in Path(member.name).parts
               or not (member.isfile() or member.isdir()) for member in members):
            raise ValueError('Unsafe original tools archive member')
        archive.extractall(tools)
    original = {str(path.relative_to(tools)): sha(path) for path in tools.rglob('*') if path.is_file()}
    runtime = read(tools/'runtimes.json')['cmssw']
    if runtime['sha256'] != receipt['runtime_sha256']:
        raise ValueError('Addon was qualified for a different old runtime')
    helpers = {Path(pin['path']).name: pin for pin in config['helper_files']}
    if set(helpers) != REQUIRED_HELPERS:
        raise ValueError('Freeze the complete explicit response-helper closure')
    for name, pin in helpers.items():
        source = checked_file(pin)
        target = tools/'scripts'/name
        if target.exists() and sha(target) != pin['sha256']:
            raise ValueError('A new helper would overwrite an old frozen helper: '+name)
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
    wrapper = tools/'scripts/run_shift_sm_response_worker.py'
    if wrapper.exists():
        raise ValueError('The old capsule already contains a response log adapter')
    wrapper.write_text(WRAPPER)
    plugin = tools/'plugin'/PLUGIN
    plugin.parent.mkdir(exist_ok=True)
    if plugin.exists():
        raise ValueError('The old capsule already contains a preflight addon')
    shutil.copyfile(addon, plugin)
    build = tools/'plugin/sm_primary_preflight_build_receipt.json'
    shutil.copyfile(receipt_path, build)
    for name, checksum in original.items():
        if sha(tools/name) != checksum:
            raise ValueError('An original tools member changed: '+name)
    worker = frozen/'run_shift_stream_worker.sh'
    shutil.copyfile(bootstrap, worker)
    worker.chmod(0o755)
    capsule = frozen/'frozen_tools.tar.gz'
    with tarfile.open(capsule, 'w:gz') as archive:
        for path in sorted(tools.rglob('*')):
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(tools)), recursive=False)
    return dict(capsule=str(capsule), capsule_sha256=sha(capsule), bootstrap=str(worker),
        bootstrap_sha256=sha(worker), runtime=runtime, old_tools_members_unchanged=original,
        preflight_addon=dict(path='plugin/'+PLUGIN, sha256=sha(plugin),
            build_receipt=dict(path='plugin/sm_primary_preflight_build_receipt.json', sha256=sha(build))))


def envelope(stage, plan_pin, reservation):
    return dict(schema='shift-stream-payload-v1', environment='cmssw', stage=stage,
        cohort_id='sm-response-prospective-20261027-v1',
        application_timeout_seconds=10000 if stage == 'smart_sm_detector' else 2700,
        result_path='report.json', return_files=[], status_fields=dict(
            benchmark_plan_sha256=plan_pin['sha256'], response_study_only=True),
        reservation_bytes=reservation)


def detector_request(plan_pin, source_pin, selected, job_number, eos):
    source = copy.deepcopy(source_pin['original_selected_source'])
    source.update(nano_base=eos+'/'+selected['stratum']+'/nanoAOD',
        metadata_base=eos+'/'+selected['stratum']+'/metadata/detector', maximum_publication_bytes=16*MIB)
    request = envelope('smart_sm_detector', plan_pin, 16*MIB)
    request.update(argv=['python3', '@TOOLS@/scripts/run_shift_sm_response_worker.py', 'request.json'],
        detector_source=source, selected_skip=selected['selected_position'], selected_count=1,
        detector_job=job_number, benchmark_inclusion_probability=selected['benchmark_inclusion_probability'],
        failure_return_files=['failure_logs/'+f'step{i}{suffix}.tail'
            for i in range(1,5) for suffix in ('.log','_config.log')])
    verify_worker_request(request, selected)
    unchanged = copy.deepcopy(source)
    for key in ('nano_base', 'metadata_base', 'maximum_publication_bytes'):
        unchanged[key] = source_pin['original_selected_source'][key]
    if unchanged != source_pin['original_selected_source']:
        raise ValueError('Diagnostic request changed the original scientific descriptor')
    return request


def preflight_request(plan_pin, source_pin, frozen):
    descriptor = source_pin['original_selected_source']
    request = envelope('sm_primary_preflight', plan_pin, MIB)
    request.update(argv=['python3', '@TOOLS@/scripts/run_shift_sm_response_worker.py', 'request.json'],
        runtime_sha256=frozen['runtime']['sha256'], preflight_addon=frozen['preflight_addon'],
        sources=[dict(source_id=source_pin['source_id'], gen_path=descriptor['gen'],
            gen_sha256=descriptor['gen_sha256'], full_event_ids=descriptor['event_ids'],
            source_descriptor=descriptor)],
        failure_return_files=['failure_logs/'+name+'.tail' for name in
            ('preflight_0000.configuration.log','preflight_0000.cmsRun.log')])
    return request


def submit_text(directory, request, frozen, priority):
    maximum = 10800 if request['stage']=='smart_sm_detector' else 3600
    memory = 6000 if request['stage']=='smart_sm_detector' else 4000
    return '\n'.join(['# Code style: AI-code. AI-generated frozen response-study worker.',
        'universe = vanilla', 'initialdir = '+str(directory),
        'executable = '+frozen['bootstrap'],
        'arguments = '+sha(directory/'request.json')+' '+frozen['capsule_sha256']+' request.json',
        'should_transfer_files = YES', 'when_to_transfer_output = ON_EXIT',
        'transfer_input_files = '+str(directory/'request.json')+','+frozen['capsule'],
        'transfer_output_files = status.json,result.json,return_payload.tar.gz',
        'getenv = False', 'request_cpus = 1', 'request_memory = '+str(memory),
        'request_disk = 12000000', '+WantIOProxy = True', '+SendCredential = True',
        '+MaxRuntime = '+str(maximum), 'priority = '+str(priority),
        '+JobBatchName = "SM_response_prospective_20261027_v1"',
        '+ShiftSMResponseStudy = True', 'output = worker.out', 'error = worker.err',
        'log = events.log', 'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)',
        'periodic_release = False', 'queue 1', ''])


def choose_canaries(selected):
    result=[]
    for region in ('credible_shallow_pointing','no_both_sides_pointing_pair'):
        candidates=[row for row in selected if row['benchmark_region']==region]
        if not candidates:
            raise ValueError('The draw needs credible and missed-direction canary events')
        result.append(min(candidates, key=lambda row: row['draw_hash']))
    return result


def prepare(config, output):
    output = persistent_directory(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Refuse to overwrite an existing response deployment')
    plan = read_pin(config['benchmark_plan'])
    source_record = read_pin(config['selected_source_pins'])
    exposure = read_pin(config['full_population_exposure'])
    reference = read_pin(config['positive_reference'])
    if (plan.get('draw_frozen') is not True
            or source_record['frozen_benchmark_sha256'] != config['benchmark_plan']['sha256']):
        raise ValueError('Require the unchanged frozen draw and source pins')
    sources = {row['source_id']: row for row in source_record['sources']}
    if set(sources) != {row['source_id'] for row in plan['selected']}:
        raise ValueError('Source pins do not cover exactly the selected full cohorts')
    verify_exposure(exposure, plan)
    for source in sources.values():
        descriptor = source['original_selected_source']
        encoded = json.dumps(descriptor, sort_keys=True, separators=(',', ':')).encode()
        if hashlib.sha256(encoded).hexdigest() != source['selected_descriptor_canonical_json_sha256']:
            raise ValueError('The original selected source descriptor changed')
        authority = read_pin(dict(path=source['original_result_path'],
            sha256=source['original_result_sha256']))
        if (authority.get('complete') is not True or authority.get('exit_code') != 0
                or authority['selected_source'] != descriptor
                or authority['source'] != source['full_original_exposure']):
            raise ValueError('The original preparation authority and pinned descriptor differ')
    job_base=config.get('detector_job_base',1000000)
    if type(job_base) is not int or not 100000 <= job_base < 6000000:
        raise ValueError('Use a fresh bounded detector seed/job-number domain')
    eos = config['output_eos_directory'].rstrip('/')
    if not eos.startswith('/eos/user/j/jniedzie/shift_cmssw_support/') or '..' in Path(eos).parts:
        raise ValueError('Diagnostic outputs must stay in authorized support storage')
    canaries = choose_canaries(plan['selected'])
    canary_keys = {(row['gen_sha256'], tuple(row['event_id'])) for row in canaries}
    canary_sources = {row['source_id'] for row in canaries}
    output.mkdir(parents=True, exist_ok=True)
    frozen = freeze_tools(config, output/'frozen')
    jobs=[];dag=['# Code style: AI-code. AI-generated bounded prospective response DAG.',
                'CONFIG '+str(output/'dagman.config')]

    def add(node, request, priority):
        directory=output/'jobs'/node.lower()
        directory.mkdir(parents=True)
        write_json(directory/'request.json', request)
        (directory/'worker.sub').write_text(submit_text(directory, request, frozen, priority))
        record=dict(node=node, stage=request['stage'], job_directory=str(directory),
            request_sha256=sha(directory/'request.json'), priority=priority,
            reservation_bytes=request['reservation_bytes'])
        jobs.append(record)
        dag.extend(['JOB '+node+' '+str(directory/'worker.sub')+' DIR '+str(directory),
                    'PRIORITY '+node+' '+str(priority)])
        return record

    preflights={}
    for number,(source_id,source) in enumerate(sorted(sources.items()),1):
        node=f'PREF_{number:04d}'
        preflights[source_id]=add(node,preflight_request(config['benchmark_plan'],source,frozen),
            150 if source_id in canary_sources else 60)
    detector_jobs=[]
    for number,selected in enumerate(plan['selected'],1):
        key=(selected['gen_sha256'],tuple(selected['event_id']))
        is_canary=key in canary_keys
        request=detector_request(config['benchmark_plan'],sources[selected['source_id']],
            selected,job_base+number,eos)
        record=add(f'DET_{number:04d}',request,120 if is_canary else 0)
        record.update(selected=selected,detector_job=job_base+number,canary=is_canary)
        detector_jobs.append(record)
        dag.append('PARENT '+preflights[selected['source_id']]['node']+' CHILD '+record['node'])

    def collection(stage, records, reservation, suffix):
        request=envelope(stage,config['benchmark_plan'],reservation)
        request.update(argv=['python3','@TOOLS@/scripts/run_shift_sm_response_collection.py','request.json'],
            benchmark_plan=config['benchmark_plan'],full_population_exposure=config['full_population_exposure'],
            job_records=[{k:row[k] for k in ('job_directory','request_sha256','selected')} for row in records],
            maximum_publication_bytes=reservation,output_directory=eos+'/evidence/'+suffix)
        if stage=='sm_response_canary':request['positive_reference']=reference
        return request

    canary_jobs=[row for row in detector_jobs if row['canary']]
    gate=add('CANARY_VALIDATED',collection('sm_response_canary',canary_jobs,4*MIB,'canary'),200)
    dag.append('PARENT '+' '.join(row['node'] for row in canary_jobs)+' CHILD '+gate['node'])
    for record in detector_jobs:
        if not record['canary']:dag.append('PARENT '+gate['node']+' CHILD '+record['node'])
    final=add('FINAL_COLLECT',collection('sm_response_collect',detector_jobs,124*MIB,'final'),100)
    dag.append('PARENT '+' '.join(row['node'] for row in detector_jobs)+' CHILD '+final['node'])
    dag_path=output/'response.dag'
    dag_path.write_text('\n'.join(dag)+'\n')
    (output/'dagman.config').write_text('\n'.join([
        '# Code style: AI-code. AI-generated submission pacing; running limit is separate.',
        'DAGMAN_MAX_JOBS_SUBMITTED = 400', 'DAGMAN_MAX_JOBS_IDLE = 32',
        'DAGMAN_MAX_SUBMITS_PER_INTERVAL = 1', 'DAGMAN_USER_LOG_SCAN_INTERVAL = 1',
        'DAGMAN_SUBMIT_DELAY = 1', '']) )
    command=['/usr/bin/condor_submit_dag','-no_submit','-maxjobs','400','-maxidle','32',
        '-autorescue','0','-notification','Never','-batch-name','SM_response_prospective_20261027_v1',
        '-append','+MaxRuntime = 172800','-append','initialdir = '+str(output),
        '-append','+SendCredential = True',str(dag_path)]
    manifest=dict(schema='shift-sm-prospective-native-DAG-v1', benchmark_plan=config['benchmark_plan'],
        frozen_tools=frozen, working_directory=str(output),dag_path=str(dag_path),dag_sha256=sha(dag_path),
        jobs=jobs,preflight_jobs=list(preflights.values()),detector_jobs=detector_jobs,
        canary_gate=gate,final_collector=final,native_prepare_command=command,
        maximum_running_jobs=400,maximum_idle_jobs=32,submission_delay_seconds=1,
        scheduler_controls_actual_execution_start_times=True,
        publication_reservation_bytes=len(detector_jobs)*16*MIB+len(preflights)*MIB+128*MIB,
        full_population_exposure=config['full_population_exposure'],
        original_source_cohorts=len(exposure['sources']),old_scientific_descriptor_unchanged=True,
        benchmark_q_separate_from_old_p=True,jobs_submitted=False,execution_verified=False)
    write_json(output/'prepared_dag.json',manifest)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration',type=Path)
    parser.add_argument('output_directory',type=Path)
    args=parser.parse_args()
    result=prepare(read(args.configuration),args.output_directory)
    print(json.dumps(dict(dag=result['dag_path'],preflights=len(result['preflight_jobs']),
        detectors=len(result['detector_jobs']),publication_reservation_bytes=result['publication_reservation_bytes'],
        jobs_submitted=False)))


if __name__=='__main__':
    main()
