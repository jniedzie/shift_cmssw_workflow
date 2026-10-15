#!/usr/bin/env python3
# Code style: AI-code. AI-generated frozen six-bin mu-enriched GEN campaign.
"""Prepare all six preserved QCD bins with ordinary low and weighted high GEN.

The common DAG owns one global worker limit. Real publication canaries precede
ordinary production; independent same-profile closure gates weighted high GEN.
This preparer never submits and never modifies an existing deployment.
"""
import argparse
import json
from pathlib import Path
import shutil

from qcd_mu_enrichment import PROFILE
from run_shift_weighted_gen import sha

SCRIPTS = Path(__file__).resolve().parent
ALGORITHM = 'independent-geometric-full-support-tail-channel-retry-rb-v4'
MODEL = 'a6bb0fd762c5805f1cb2e08c50e713d53ae41a20399adb9c748ea91fb7f8e50e'
BINS = ((0, 1), (1, 2), (2, 5), (5, 10), (10, 20), (20, -1))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2)+'\n')


def worker_submit(base, manifest_sha, ordinary):
    script = 'run_mu_enriched_qcd_gen.py' if ordinary else 'run_shift_weighted_gen.py'
    manifest = json.loads((base/'manifest.json').read_text())
    shared = ','.join(str(base/name) for name in manifest['inputs'])+','+str(base/'manifest.json')
    if not ordinary:
        shared += ',$(gate_input)'
    return '\n'.join(['universe = vanilla', f'initialdir = {base}',
        'executable = /usr/bin/python3',
        f'arguments = {script} --manifest manifest.json --expected-sha256 {manifest_sha} --node $(node_id)',
        'should_transfer_files = YES', 'when_to_transfer_output = ON_EXIT',
        f'transfer_input_files = {shared}', 'transfer_output_files = result.json,artifacts.zip',
        'transfer_output_remaps = "result.json=results/$(node_id).json; artifacts.zip=results/$(node_id).zip"',
        'getenv = False', 'request_cpus = 1', 'request_memory = 4000', 'request_disk = 3000000',
        'priority = 100000',
        '+MaxRuntime = 7200', '+ShiftProductionSuite = true',
        '+ShiftSuiteTag = "qcd_mu_enriched_20261015_v1"', '+ShiftMuEnrichedQCD = true',
        '+ShiftMuEnrichedNodeId = "$(node_id)"',
        '+ShiftMuEnrichedStage = "$(stage)"', '+ShiftPeakBytesPerWorker = 1000000000',
        'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)', 'periodic_release = False',
        'output = logs/$(node_id).out', 'error = logs/$(node_id).err', 'log = events.log', 'queue 1', ''])


def control_submit(base, arguments):
    return '\n'.join(['universe = scheduler', f'initialdir = {base}',
        'executable = /usr/bin/python3', f'arguments = {arguments}', 'getenv = False',
        '+MaxRuntime = 604800', '+ShiftMuEnrichedQCDControl = true',
        '+ShiftSuiteTag = "qcd_mu_enriched_20261015_v1"',
        'output = logs/$(Cluster).$(Process).out', 'error = logs/$(Cluster).$(Process).err',
        'log = events.log', 'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)', 'queue 1', ''])


def resume_completed(previous, directory):
    """Reuse exact completed publications when replacing only frozen control code."""
    from control_mu_enriched_qcd_gen import cohort
    from run_shift_production_health_audit import weighted_receipt
    previous = Path(previous).resolve()
    reused, identities = [], set()
    for name in ('ordinary', 'weighted'):
        old, new = previous/name, directory/name
        expected = sha(new/'manifest.json')
        if sha(old/'manifest.json') != expected:
            raise ValueError('A control-only restart cannot change scientific manifests: '+name)
        manifest = json.loads((new/'manifest.json').read_text())
        rows = {row['id']:row for row in manifest['jobs']}
        for path in sorted((old/'results').glob('*.json')):
            record = json.loads(path.read_text())
            request = rows.get(path.stem)
            if request is None or record.get('exit_status') != 0:
                raise ValueError('Cannot silently retry an unknown or failed prior node: '+path.stem)
            if name == 'ordinary':
                cohort(dict(manifest,jobs=[request]), expected, old, request['stage'])
            else:
                if request['stage'] == 'production':
                    raise ValueError('Production-node restart needs its completed scientific gate')
                weighted_receipt(record, {'manifest_sha256':expected}, manifest, request)
            for identity in record['semantic_audit']['event_ids']:
                key = tuple(identity)
                if key in identities:
                    raise ValueError('Duplicate original identity in reused publications')
                identities.add(key)
            shutil.copy2(path, new/'results'/path.name)
            reused.append(dict(group=name, id=path.stem, events=record['event_count'],
                previous_receipt=str(path), receipt_sha256=sha(path),
                publication_directory=record['publication_directory']))
    dag_path = directory/'production.dag'
    lines = dag_path.read_text().splitlines()
    node_by_id = {}
    for line in lines:
        if line.startswith('VARS '):
            fields = line.split()
            node_by_id[fields[2].split('=',1)[1].strip('"')] = fields[1]
    completed_nodes = {node_by_id[row['id']] for row in reused}
    remaining = []
    for line in lines:
        fields = line.split()
        if fields[0] in ('JOB','VARS','CATEGORY','PRE_SKIP') and fields[1] in completed_nodes:
            continue
        if fields[0] == 'SCRIPT':
            node_index = 5 if fields[1] == 'DEFER' else 2
            if fields[node_index] in completed_nodes:
                continue
        if fields[0] == 'PARENT':
            split = fields.index('CHILD')
            parents = [node for node in fields[1:split] if node not in completed_nodes]
            children = [node for node in fields[split+1:] if node not in completed_nodes]
            if not parents or not children:
                continue
            line = 'PARENT '+' '.join(parents)+' CHILD '+' '.join(children)
        remaining.append(line)
    dag_path.write_text('\n'.join(remaining)+'\n')
    write_json(directory/'resumed_publications.json', dict(
        schema='shift-mu-enriched-control-only-resumption-v1', previous_directory=str(previous),
        scientific_manifests_unchanged=True, reused_nodes=reused,
        reused_events=sum(row['events'] for row in reused), no_science_rerun=True,
        completed_nodes_omitted_from_execution=sorted(completed_nodes)))
    return reused


def prepare(directory, assets, reference, output_base, workers=200, resume_from=None,
            admission_policy=None):
    directory, assets, reference = map(lambda path:Path(path).resolve(), (directory, assets, reference))
    if directory.exists() or any(char.isspace() for char in str(directory)):
        raise ValueError('Require a new persistent deployment directory without whitespace')
    if not 1 <= workers <= 500:
        raise ValueError('One shared DAG worker ceiling must be 1..500')
    ref = json.loads(reference.read_text())
    if ref.get('generator_profile') != PROFILE or {row['stratum'] for row in ref['samples']} != {'qcd_5to10'}:
        raise ValueError('Require independent mu-enriched ordinary5--10 reference')
    directory.mkdir(parents=True)
    for name in ('run_mu_enriched_qcd_admission.py', 'run_shift_ntuple_controller.py',
                 'shift_condor_native.py', 'read_shift_external_storage_commitments.py'):
        shutil.copy2(SCRIPTS/name, directory/name)
    # This repaired auditor observes every account worker and excludes the
    # scheduler's local queue, which the PRE helper reads through its address.
    shutil.copy2(SCRIPTS/'run_shift_night_quota_audit.py', directory/'run_shift_quota_audit.py')
    (directory/'admission').mkdir()
    policy = dict(account_owner='jniedzie',
        account_pool='tweetybird04.cern.ch', schedd_name='bigbird26.cern.ch',
        suite_tag='qcd_mu_enriched_20261015_v1', worker_ceiling=workers, account_ceiling=1000,
        minimum_free_bytes=53687091200, minimum_free_files=2000,
        minimum_AFS_free_bytes=536870912, reserved_bytes_per_worker=1000000000,
        reserved_files_per_worker=8, observation_max_age_seconds=600,
        pre_reservation_seconds=900)
    if admission_policy is not None:
        policy = json.loads(Path(admission_policy).read_text())
        from run_mu_enriched_qcd_admission import validate_policy
        validate_policy(policy)
        if policy['worker_ceiling'] != workers or policy['suite_tag'] != 'qcd_mu_enriched_20261015_v1':
            raise ValueError('Admission policy differs from this campaign worker ceiling or tag')
    write_json(directory/'admission/policy.json', policy)
    for name in ('ordinary', 'weighted'):
        base = directory/name
        base.mkdir()
        (base/'logs').mkdir()
        (base/'results').mkdir()
        for asset in ('runtime.tar.gz', 'pluginShiftMpiWeightedProposalHook.so', 'libShiftMpiTailProposal.so'):
            shutil.copy2(assets/asset, base/asset)
        for script in ('run_shift_weighted_gen.py', 'audit_shift_weighted_gen.py',
                       'qcd_mu_enrichment.py', 'generation_publication.py', 'soft_mpi_model.py'):
            shutil.copy2(SCRIPTS/script, base/script)
    ordinary, weighted = directory/'ordinary', directory/'weighted'
    for script in ('run_mu_enriched_qcd_gen.py', 'audit_mu_enriched_qcd_gen.py', 'control_mu_enriched_qcd_gen.py'):
        shutil.copy2(SCRIPTS/script, ordinary/script)
    for script in ('gate_shift_weighted_gen.py', 'shift_weighted_gen_pre.py', 'finalize_shift_weighted_gen.py'):
        shutil.copy2(SCRIPTS/script, weighted/script)
    shutil.copy2(reference, weighted/'ordinary_reference.json')
    dag, low_pilots, low_production, high_pilots, high_production = [], [], [], [], []
    low_jobs, high_jobs = [], []
    def campaign(bounds):
        lower, upper = bounds
        label = str(lower)+'to'+('inf' if upper == -1 else str(upper))
        return output_base.rstrip('/')+'/qcd_pThat'+label+'GeV/20261015_mu_enriched_v1/GEN'
    for group, bounds in enumerate(BINS[:-1]):
        key = 'qcd_'+str(bounds[0])+'to'+str(bounds[1])
        chunk_sources = 200 if bounds == (10,20) else 1000
        chunk_count = 100000//chunk_sources
        for index in range(-1, chunk_count):
            stage = 'canary' if index == -1 else 'production'
            row = dict(id=stage+'_'+key+'_'+str(max(0,index)).zfill(4), stratum=key,
                sample='qcd', bounds=list(bounds),
                trials=(20 if bounds == (10,20) else 200) if index == -1 else chunk_sources,
                run=42000000+group*100000+(99999 if index == -1 else index),
                seed=241000001+group*100000+(99999 if index == -1 else index),
                stage=stage, chunk_index=index, output_base=campaign(bounds), timeout_seconds=3600)
            low_jobs.append(row)
    def high_row(bounds, trials, stage, index, offset):
        key = 'qcd_'+str(bounds[0])+'to'+('inf' if bounds[1] == -1 else str(bounds[1]))
        return dict(id=stage+'_'+key+'_'+str(index).zfill(4), stratum=key, sample='qcd',
            bounds=list(bounds), trials=trials, run=43000000+offset+index,
            seed=251000001+offset+index, stage=stage, chunk_index=index, publish=True,
            timeout_seconds=3600, output_base=campaign(bounds))
    high_jobs.extend(high_row((5,10),20000,'closure',index,0) for index in range(3))
    high_jobs.extend(high_row((20,-1),20000,'pilot',index,100000) for index in range(2))
    high_jobs.extend(high_row((20,-1),20000,'production',index,200000) for index in range(1000))
    for base, jobs in ((ordinary, low_jobs), (weighted, high_jobs)):
        inputs = {path.name:sha(path) for path in sorted(base.iterdir()) if path.is_file() and
                  path.name not in ('gate_shift_weighted_gen.py','shift_weighted_gen_pre.py',
                                    'finalize_shift_weighted_gen.py','control_mu_enriched_qcd_gen.py','ordinary_reference.json')}
        manifest = dict(schema='shift-mu-enriched-ordinary-manager-v1' if base == ordinary else 'shift-weighted-high-bin-manager-v2',
            tag='qcd_mu_enriched_20261015_v1', generator_profile=PROFILE,
            source_model_settings_sha256=MODEL, algorithm=ALGORITHM, inputs=inputs,
            jobs=jobs, eos_base='', peak_bytes_per_worker=1000000000,
            physics_valid=False, normalization_ready=False, scheduling_ceiling=workers)
        if base == weighted:
            manifest.update(ordinary_reference_sha256=sha(base/'ordinary_reference.json'),
                closure_strata=['qcd_5to10'], effective_event_targets={'qcd_20toinf':100000},
                budget_safety_factor=1.25, chunk_trials=20000, max_chunks_per_stratum=1000,
                max_worker_hours_per_stratum=600, max_projected_production_bytes=40000000000,
                max_root_bytes_per_chunk=480000000, independent_pilots_excluded_from_production_normalization=True)
            if resume_from is not None:
                # A control restart keeps the original scientific budget;
                # the default below applies only to a new production.
                previous_manifest = json.loads((Path(resume_from)/'weighted/manifest.json').read_text())
                manifest['budget_safety_factor'] = previous_manifest['budget_safety_factor']
        write_json(base/'manifest.json', manifest)
        checksum = sha(base/'manifest.json')
        (base/'worker.sub').write_text(worker_submit(base, checksum, base == ordinary))
        for index, row in enumerate(jobs):
            node = ('L' if base == ordinary else 'H')+str(index).zfill(4)
            dag.extend([f'JOB {node} {base.name}/worker.sub',
                f'VARS {node} node_id="{row["id"]}" stage="{row["stage"]}"'+
                (f' gate_input="{base / ("scientific_gate.json" if row["stage"] == "production" else "ordinary_reference.json")}"' if base == weighted else ''),
                f'CATEGORY {node} workers'])
            admission = (f'SCRIPT DEFER 75 60 PRE {node} /usr/bin/python3 '
                         f'{directory}/run_mu_enriched_qcd_admission.py {directory} --node {row["id"]}')
            if base == weighted and row['stage'] == 'production':
                admission += f' --weighted-manifest {base}/manifest.json --expected-sha256 {checksum}'
            dag.append(admission)
            if base == ordinary:
                (low_pilots if row['stage'] == 'canary' else low_production).append(node)
                if row['stage'] == 'production':
                    dag.append(f'PARENT LOW_GATE CHILD {node}')
            else:
                (high_pilots if row['stage'] != 'production' else high_production).append(node)
                if row['stage'] == 'production':
                    dag.extend([f'PARENT HIGH_GATE CHILD {node}',
                        f'PRE_SKIP {node} 99'])
        write_json(base/'freeze.json', {path.name:sha(path) for path in base.iterdir() if path.is_file()})
    (ordinary/'gate.sub').write_text(control_submit(ordinary, f'{ordinary}/control_mu_enriched_qcd_gen.py {ordinary} canary'))
    (ordinary/'final.sub').write_text(control_submit(ordinary, f'{ordinary}/control_mu_enriched_qcd_gen.py {ordinary} production'))
    (weighted/'gate.sub').write_text(control_submit(weighted, f'{weighted}/gate_shift_weighted_gen.py {weighted}'))
    (weighted/'final.sub').write_text(control_submit(weighted, f'{weighted}/finalize_shift_weighted_gen.py {weighted}'))
    dag.extend(['JOB LOW_GATE ordinary/gate.sub', 'PARENT '+' '.join(low_pilots)+' CHILD LOW_GATE',
        'ABORT-DAG-ON LOW_GATE 1 RETURN 1', 'JOB HIGH_GATE weighted/gate.sub',
        'PARENT '+' '.join(high_pilots)+' CHILD HIGH_GATE',
        'JOB LOW_COMPLETE ordinary/final.sub', 'PARENT '+' '.join(low_production)+' CHILD LOW_COMPLETE',
        'JOB HIGH_COMPLETE weighted/final.sub', 'PARENT '+' '.join(high_production)+' CHILD HIGH_COMPLETE',
        f'MAXJOBS workers {workers}', 'NODE_STATUS_FILE nodes.status 30 ALWAYS-UPDATE'])
    (directory/'production.dag').write_text('\n'.join(dag)+'\n')
    reused = resume_completed(resume_from,directory) if resume_from is not None else []
    (directory/'logs').mkdir()
    summary = dict(schema='shift-mu-enriched-qcd-production-plan-v1', generator_profile=PROFILE,
        directory=str(directory), bins=[list(bounds) for bounds in BINS],
        fixed_ordinary_framework_source_events_per_bin=100000,
        high_bin_effective_target=100000, high_fixed_budget_derived_only_from_independent_pilots=True,
        ordinary_production_jobs=sum(row['stage']=='production' for row in low_jobs), weighted_maximum_production_jobs=1000,
        independent_publication_canaries=10, worker_ceiling=workers,
        manager_runtime_seconds=604800, mixing_enabled=False,
        manifest_sha256={name:sha(directory/name/'manifest.json') for name in ('ordinary','weighted')},
        submitted=False, physics_valid=False, normalization_ready=False)
    summary['reused_completed_nodes'] = len(reused)
    write_json(directory/'production_plan.json', summary)
    write_json(directory/'freeze.json', {str(path.relative_to(directory)):sha(path)
        for path in directory.rglob('*') if path.is_file() and path.name != 'freeze.json'})
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--assets', required=True)
    parser.add_argument('--reference', required=True)
    parser.add_argument('--output-base', default='/eos/user/j/jniedzie/shift_cmssw')
    parser.add_argument('--workers', type=int, default=200)
    parser.add_argument('--resume-from')
    parser.add_argument('--admission-policy')
    args = parser.parse_args()
    print(json.dumps(prepare(args.directory, args.assets, args.reference, args.output_base, args.workers,
                             args.resume_from, args.admission_policy), indent=2))
