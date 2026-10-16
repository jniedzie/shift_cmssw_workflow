#!/usr/bin/env python3
# Code style: AI-code. AI-generated reusable immutable BSM production preparation.
"""Freeze a qualified GEN-to-merged-histogram deployment; never submit jobs."""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil

from dark_photon_publication_quota import sha256, external_chunk_reservations
from audit_dark_photon_gen import validate_event_ids
from dark_photon_production_support import (
    PROFILES, validate_complete_groups, validate_publication_canary,
)


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    with Path(path).open('x') as output:
        json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
        output.write('\n')


def check_gen(plan, inventory, publication, prior_inventories):
    """Bind exact audited full GEN populations and reject reused identities."""
    names = {row['point'] for row in plan['points']}
    published = {row['point']: row for row in publication['inputs']}
    if (inventory.get('complete') is not True or publication.get('complete') is not True or
            set(inventory['points']) != names or set(published) != names or
            len(publication['inputs']) != len(names)):
        raise ValueError('Complete GEN inventory/publication must cover exactly the new points')
    identities, runs, seeds, previous_ids, previous_runs, previous_seeds = set(), set(), set(), set(), set(), set()
    for path in prior_inventories:
        previous = read(path)
        for row in previous['points'].values():
            previous_ids.update(map(tuple, row['event_ids']))
            previous_runs.update(event[0] for event in row['event_ids'])
            if 'seed' in row:
                previous_seeds.add(row['seed'])
    for row in plan['points']:
        point, count = row['point'], row['events']
        parent, payload = inventory['points'][point], published[point]
        actual = list(map(tuple, parent['event_ids']))
        gen = Path(row['gen_directory'])
        audit, contract, manifest = [read(gen / name) for name in
                                    ('validation.json', 'contract.json', 'manifest.json')]
        validate_event_ids(audit['event_ids'], contract)
        if (parent['event_ids'] != audit['event_ids'] or audit.get('runtime_validated') is not True or
                audit['events'] != count or
                audit['sum_weights'] != count or audit['sum_weights_squared'] != count or
                parent['events'] != count or parent['native_sumw'] != count or parent['native_sumw2'] != count or
                any(item['weight'] != 1. for item in audit['event_rows'])):
            raise ValueError('GEN population or native weights changed: ' + point)
        if (identities.intersection(actual) or previous_ids.intersection(actual) or
                row['run_number'] in runs | previous_runs or row['seed'] in seeds | previous_seeds):
            raise ValueError('GEN identities/runs/seeds overlap another population: ' + point)
        identities.update(actual); runs.add(row['run_number']); seeds.add(row['seed'])
        if (contract != read(row['signal_contract']) or contract['requested_events'] != count or
                any(contract[key] != row[key] for key in ('mass_gev', 'epsilon', 'seed', 'run_number')) or
                any(parent[key] != row[key] for key in ('mass_gev', 'epsilon', 'seed', 'run_number'))):
            raise ValueError('Runtime GEN contract differs from native preflight: ' + point)
        for name, key in [('manifest.json', 'manifest_sha256'), ('contract.json', 'contract_sha256'),
                          ('validation.json', 'validation_sha256')]:
            if parent[key] != sha256(gen / name):
                raise ValueError('GEN metadata changed: ' + point + '/' + name)
        recovery = manifest.get('audit_recovery')
        if recovery:
            revision_script = Path(recovery['script'])
            if not revision_script.is_relative_to(gen / 'audit_revisions'):
                raise ValueError('GEN audit revision is outside its retained evidence directory')
            revision = revision_script.parent
            for name, expected in [('audit_dark_photon_gen.py', recovery['sha256']),
                                   ('pythia_generation_ledger.py', recovery['ledger_script_sha256']),
                                   ('previous_manifest.json', recovery['previous_manifest_sha256'])]:
                if sha256(revision / name) != expected:
                    raise ValueError('Recovered GEN audit evidence changed: ' + point + '/' + name)
        destination = row['final_output_base'] + '/GEN/gen.root'
        if (parent['gen_path'] != destination or row.get('gen_remote_path', destination) != destination or
                payload['path'] != destination or payload['sha256'] != parent['gen_sha256'] or
                payload['readback_sha256'] != parent['gen_sha256'] or
                manifest['output_sha256'] != parent['gen_sha256'] or payload['bytes'] <= 0):
            raise ValueError('GEN publication is not the audited canonical payload: ' + point)
    return dict(complete=True, events=len(identities), distinct_parent_events=len(identities),
        overlapping_event_ids=[], overlapping_runs=[], overlapping_seeds=[],
        new_runs=sorted(runs), new_seeds=sorted(seeds),
        prior_gen_authorities=[dict(path=str(Path(path).resolve()), sha256=sha256(path))
                               for path in prior_inventories])


def dag_text(plan, output, gates, detector_cap, finalizer_cap):
    gate_nodes = ['D_' + point + '_000' for point in gates]
    lines, finalizers = [], []
    for row in plan['points']:
        parents = []
        for chunk in row['chunks']:
            node = f"D_{row['point']}_{chunk['chunk']:03d}"
            lines.extend([f'JOB {node} {output}/detector.sub',
                f'VARS {node} point="{row["point"]}" chunk="{chunk["chunk"]}" kind="detector" seal="SEAL_DIGEST"',
                f'CATEGORY {node} DETECTOR'])
            if node not in gate_nodes:
                lines.append(f'PARENT {" ".join(gate_nodes)} CHILD {node}')
            parents.append(node)
        finalizer = 'F_' + row['point']
        finalizers.append(finalizer)
        lines.extend([f'JOB {finalizer} {output}/finalizer.sub',
            f'VARS {finalizer} point="{row["point"]}" chunk="group" kind="finalizer" seal="SEAL_DIGEST"',
            f'CATEGORY {finalizer} FINALIZER',
            f'PARENT {" ".join(sorted(set(parents + gate_nodes)))} CHILD {finalizer}'])
    lines.extend([f'JOB COLLECT {output}/collect.sub',
        'VARS COLLECT point="all" chunk="all" kind="collect" seal="SEAL_DIGEST"',
        f'PARENT {" ".join(finalizers)} CHILD COLLECT',
        f'MAXJOBS DETECTOR {detector_cap}', f'MAXJOBS FINALIZER {finalizer_cap}'])
    return '\n'.join(lines) + '\n'


def extend_worker(text, publication_profile):
    """Version the qualified worker's publication contract in its new copy."""
    old = "    paths = {tier: str(base / tier / name)\n             for tier in ('GEN_SIM', 'DIGI_RAW_HLT', 'AODSIM', 'nanoAOD')}\n"
    replacement = ("    profile = read(B/'plan.json')['publication_profile']\n"
                   "    if profile == 'nano-metadata-v1':\n"
                   "        tiers = ('nanoAOD',)\n"
                   "    elif profile == 'all-tiers-v1':\n"
                   "        tiers = ('GEN_SIM', 'DIGI_RAW_HLT', 'AODSIM', 'nanoAOD')\n"
                   "    else:\n"
                   "        raise ValueError('Unknown signal publication profile')\n"
                   "    paths = {tier:str(base/tier/name) for tier in tiers}\n")
    start, stop = text.index('def publication_paths(chunk):'), text.index('def valid_digest(value):')
    if old not in text[start:stop]:
        raise ValueError('Qualified publication namespace changed; review the new worker contract')
    text = text[:start] + text[start:stop].replace(old, replacement) + text[stop:]
    validation = "    destinations = publication_paths(chunk)\n    published = status.get('published', {})\n"
    if text.count(validation) != 1:
        raise ValueError('Qualified chunk receipt validation changed')
    text = text.replace(validation,
        "    profile = plan.get('publication_profile', 'all-tiers-v1')\n"
        "    if status.get('publication_profile') != profile:\n"
        "        raise ValueError('Detector receipt publication profile changed')\n"
        "    if profile == 'nano-metadata-v1':\n"
        "        scratch = status.get('audited_scratch_tiers', {})\n"
        "        if set(scratch) != {'GEN_SIM', 'DIGI_RAW_HLT', 'AODSIM'}:\n"
        "            raise ValueError('Nano-only receipt lacks full-chain scratch evidence')\n"
        "        for item in scratch.values():\n"
        "            if (not valid_digest(item.get('sha256')) or type(item.get('bytes')) is not int or item['bytes'] <= 0):\n"
        "                raise ValueError('Audited scratch-tier evidence is incomplete')\n"
        + validation)
    files = "        files['metadata'] = bundle\n"
    if text.count(files) != 1:
        raise ValueError('Qualified full-chain publication inventory changed')
    text = text.replace(files,
        "        tiers = plan['publication_tiers']\n"
        "        receipt.update(publication_profile=plan['publication_profile'],\n"
        "            audited_scratch_tiers={tier:dict(sha256=sha(local),bytes=local.stat().st_size)\n"
        "                                  for tier,local in files.items() if tier not in tiers})\n"
        "        files = {tier:local for tier,local in files.items() if tier in tiers}\n"
        + files)
    quota = '    minimum_bytes, minimum_files = quota_requirements(budget, count)\n'
    if text.count(quota) != 1:
        raise ValueError('Qualified worker quota hook changed; review before extending')
    # Extension/recovery templates can already have this hook. Freeze it once.
    if 'from dark_photon_publication_quota import external_chunk_reservations' not in text:
        text = text.replace(quota, quota +
            '    from dark_photon_publication_quota import external_chunk_reservations\n'
            '    extra_bytes, extra_files, external = external_chunk_reservations(budget)\n'
            '    minimum_bytes += extra_bytes\n'
            '    minimum_files += extra_files\n')
        text = text.replace('result.update(credited_complete_chunks=count,',
                            'result.update(external_reservations=external, credited_complete_chunks=count,')
    detector_limit = "'--timeout','21600'"
    process_limit = 'timeout=22000,check=True'
    if text.count(detector_limit) != 1 or text.count(process_limit) != 1:
        raise ValueError('Qualified detector timeout invocation changed; review before extending')
    text = text.replace(detector_limit, "'--timeout',str(plan['detector_timeout_seconds'])")
    text = text.replace(process_limit, "timeout=plan['detector_timeout_seconds']+600,check=True")
    return text


def deployment_wrapper(kind):
    """Keep deployed entry points small; freeze their reusable implementation."""
    if kind == 'finalizer':
        return '''#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable grouped signal finalizer.
import argparse
import json
from pathlib import Path
from dark_photon_production_support import finalize as run

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('point')
    parser.add_argument('seal')
    parser.add_argument('--runtime-cache', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.point, args.seal, args.runtime_cache)))
'''
    return '''#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable grouped signal collector.
import argparse
from dark_photon_production_support import collect

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('seal')
    collect(parser.parse_args().seal)
'''


def detector_timeout_settings(args):
    """Keep longer operational deadlines separate from unchanged physics."""
    limits = dict(event_stall_timeout_seconds=getattr(args, 'event_stall_timeout', 1800),
                  first_event_stall_timeout_seconds=getattr(args, 'first_event_stall_timeout', 3600),
                  detector_timeout_seconds=getattr(args, 'detector_timeout', 21600))
    if any(type(value) is not int or not 0 < value <= 72000 for value in limits.values()):
        raise ValueError('Detector deadlines must be positive integer seconds up to72000')
    if limits['first_event_stall_timeout_seconds'] < limits['event_stall_timeout_seconds']:
        raise ValueError('First-event deadline cannot be shorter than the event-stall deadline')
    if limits['detector_timeout_seconds'] <= limits['first_event_stall_timeout_seconds']:
        raise ValueError('Detector deadline must exceed the first-event deadline')
    return limits


def prepare(args):
    timeouts = detector_timeout_settings(args)
    template, output = args.template.resolve(), args.output.resolve()
    if output.exists():
        raise FileExistsError('Refuse to overwrite an existing BSM deployment')
    if not re.fullmatch(r'/eos/user/[A-Za-z0-9_./-]+', args.failure_base) or '..' in Path(args.failure_base).parts:
        raise ValueError('Failure evidence requires an explicit canonical EOS directory')
    template_seal = read(template / 'seal.json')
    for path, expected in template_seal['files'].items():
        if sha256(path) != expected:
            raise ValueError('Qualified template dependency changed: ' + path)
    plan = copy.deepcopy(read(args.preparation))
    if plan.get('native_width_preflight_passed') is not True or plan.get('detector_stage_prepared') is not True:
        raise ValueError('Production requires a prepared complete native-width-qualified detector grid')
    names = [row['point'] for row in plan['points']]
    if (len(names) != len(set(names)) or not args.canary_points or
            len(set(args.canary_points)) != len(args.canary_points) or not set(args.canary_points) <= set(names)):
        raise ValueError('Require distinct physical points and existing distinct detector gates')
    template_jobs = {chunk['job'] for row in read(template / 'plan.json')['points'] for chunk in row['chunks']}
    jobs = [chunk['job'] for row in plan['points'] for chunk in row['chunks']]
    if len(jobs) != len(set(jobs)) or template_jobs.intersection(jobs):
        raise ValueError('New detector RNG job namespace overlaps its qualified predecessor')
    inventory, publication = read(args.gen_inventory), read(args.gen_publication)
    independence = check_gen(plan, inventory, publication, args.prior_inventories)
    for row in plan['points']:
        validate_complete_groups(dict(row, group_events=plan['group_events']),
                                 inventory['points'][row['point']]['event_ids'])
        row['gen_remote_path'] = inventory['points'][row['point']]['gen_path']
    budget = copy.deepcopy(read(args.storage_budget))
    if budget['detector_chunks'] != plan['detector_chunks'] or budget['events'] != plan['events']:
        raise ValueError('Reviewed storage budget differs from the new complete partition')
    external_chunk_reservations(budget)  # Verify external authorities before freezing.
    profile = getattr(args, 'publication_profile', 'all-tiers-v1')
    canary_path = getattr(args, 'publication_canary', None)
    if profile not in PROFILES:
        raise ValueError('Unknown signal publication profile')
    qualification = None
    if profile == 'nano-metadata-v1':
        if canary_path is None:
            raise ValueError('Nano-only publication requires a pinned measured canary')
        qualification = validate_publication_canary(canary_path, budget,
            max(chunk['count'] for row in plan['points'] for chunk in row['chunks']))
    elif budget.get('publication_profile', profile) != profile:
        raise ValueError('Reviewed budget publication profile differs from the requested worker')
    if not 1 <= args.detector_workers <= 100 or not 1 <= args.finalizers <= 3:
        raise ValueError('Require bounded concurrency up to100detectorworkers and3finalizers')
    output.mkdir(parents=True, exist_ok=False)
    for directory in ('logs', 'failures', 'statuses', 'completed_points'):
        (output / directory).mkdir()
    for directory in ('canary', 'frozen/scoring'):
        shutil.copytree(template / directory, output / directory,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    frozen = output / 'frozen'
    shutil.copy2(template / 'frozen/runtime_fingerprint.json', frozen / 'runtime_fingerprint.json')
    dependencies = {}
    for category in ('sources', 'templates'):
        (frozen / category).mkdir()
        for name, item in plan['frozen_dependencies'][category].items():
            source = Path(item['frozen_copy'])
            if sha256(source) != item['sha256']:
                raise ValueError('Native-qualified frozen dependency changed: ' + str(source))
            target = frozen / category / name
            shutil.copy2(source, target)
            dependencies[str(target)] = dict(source=str(source), sha256=sha256(target))
    for name in ('chunk_worker.py', 'finalize_point.py', 'collect_inputs.py', 'preserve_failure.py',
                 'detector_worker.sh', 'finalizer_worker.sh', 'collect_worker.sh',
                 'detector.sub', 'finalizer.sub', 'collect.sub'):
        text = (template / name).read_text().replace(str(template), str(output))
        if name.endswith('.sub'):
            text = re.sub(r'^\+JobBatchName = .*$', '+JobBatchName = "SHIFT_BSM_' + plan['campaign'] + '"',
                          text, flags=re.MULTILINE)
        if name == 'preserve_failure.py':
            text = re.sub(r'^REMOTE_BASE = .*$', 'REMOTE_BASE = ' + repr(args.failure_base), text,
                          flags=re.MULTILINE)
        if name == 'chunk_worker.py':
            text = extend_worker(text, profile)
        elif name == 'finalize_point.py':
            # Retain the exact qualified stage/weight/scoring validators in the
            # new namespace; the new entry point supports all bounded groups.
            (output / 'qualified_finalizer.py').write_text(text)
            text = deployment_wrapper('finalizer')
        elif name == 'collect_inputs.py':
            text = deployment_wrapper('collector')
        if name == 'detector.sub' and timeouts['detector_timeout_seconds'] > 21600:
            # Set the matching request before submission; a running job cannot
            # safely acquire a longer enforced lifetime through a later qedit.
            text = re.sub(r'^\+JobFlavour = .*$', '+JobFlavour = "tomorrow"', text, flags=re.MULTILINE)
            text = text.replace('\nqueue', '\n+MaxRuntime = 86400\nqueue')
        (output / name).write_text(text)
        if name.endswith('.sh'):
            (output / name).chmod(0o750)
    shutil.copy2(Path(__file__).with_name('dark_photon_publication_quota.py'),
                 output / 'dark_photon_publication_quota.py')
    shutil.copy2(Path(__file__).with_name('dark_photon_production_support.py'),
                 output / 'dark_photon_production_support.py')
    if qualification is not None:
        write(output / 'publication_qualification.json', qualification)
    for name in ('postprocess_canary.json', 'shared_dependency_gate.json'):
        shutil.copy2(template / name, output / name)
    score = (template / 'scoring_components.json').read_text().replace(str(template), str(output))
    (output / 'scoring_components.json').write_text(score)
    plan.update(schema='shift-bdt-independent-signal-stage-v1', prepared=True, submitted=False,
        sample_role='validation_only', new_worker_cap=args.detector_workers,
        finalizer_concurrency=args.finalizers, detector_gate_points=args.canary_points,
        detector_gate_chunk=0, **timeouts,
        frozen_dependencies=dependencies, selection_applied=False, model_training_on_signals=False,
        reference_preparation=str(args.preparation.resolve()), reference_preparation_sha256=sha256(args.preparation),
        runtime_fingerprint=str(frozen / 'runtime_fingerprint.json'),
        runtime_fingerprint_sha256=sha256(frozen / 'runtime_fingerprint.json'),
        publication_profile=profile, publication_tiers=list(PROFILES[profile]),
        detector_intermediates_policy=('Audited in worker scratch; retain all non-ROOT audit metadata and tier SHA-256/bytes'
                                      if profile == 'nano-metadata-v1' else 'Retain all audited detector ROOT tiers'),
        finalizer_contract='Complete ordered GEN union; all bounded Nano groups precede histogramming; validated histogram sum')
    write(output / 'plan.json', plan)
    write(output / 'gen_inventory.json', inventory)
    write(output / 'gen_publication.json', publication)
    budget['stage2_plan_sha256'] = sha256(output / 'plan.json')
    budget['stage2_gen_inventory_sha256'] = sha256(output / 'gen_inventory.json')
    write(output / 'storage_budget.json', budget)
    independence.update(plan_sha256=sha256(output / 'plan.json'),
                        actual_gen_inventory_sha256=sha256(output / 'gen_inventory.json'))
    write(output / 'independence_gate.json', independence)
    write(output / 'historical_qualification.json', dict(qualification_role='Earlier recipe qualification; new detector gates are required',
        template_directory=str(template), template_seal_sha256=sha256(template / 'seal.json'),
        new_detector_gates_complete=False, dag_gates=args.canary_points,
        final_recording_acceptance_validated=False))
    dag = dag_text(plan, output, args.canary_points, args.detector_workers, args.finalizers)
    (output / 'pipeline.dag.template').write_text(dag)
    common = [path for path in output.iterdir() if path.is_file()]
    common.extend(path for path in (output / 'canary').rglob('*') if path.is_file())
    common.extend(path for category in ('sources', 'templates')
                  for path in (frozen / category).rglob('*') if path.is_file())
    common.extend([frozen / 'runtime_fingerprint.json', args.preparation.resolve()])
    if qualification is not None:
        common.extend(Path(item['path']) for item in qualification['dependencies'])
    scoring = [output / 'scoring_components.json']
    scoring.extend(path for path in (frozen / 'scoring').rglob('*') if path.is_file())
    gen = {row['point']: sorted(str(path) for path in Path(row['gen_directory']).rglob('*')
                               if path.is_file() and path.suffix != '.root' and '__pycache__' not in path.parts)
           for row in plan['points']}
    paths = set(common + scoring)
    paths.update(Path(path) for values in gen.values() for path in values)
    seal = dict(schema='shift-independent-signal-production-seal-v1', campaign=plan['campaign'],
        created_utc=datetime.now(timezone.utc).isoformat(), files={str(path): sha256(path) for path in sorted(paths)},
        verification_groups=dict(common=sorted(set(map(str, common))), gen=gen,
                                 scoring=sorted(set(map(str, scoring)))),
        events=plan['events'], signal_points=len(names), detector_jobs=plan['detector_chunks'], finalizer_jobs=len(names),
        selection_applied=False, sample_role='validation_only',
        detector_bulk_gated_by_new_native_canaries=True,
        canonical_GEN_payloads_pinned_by_inventory_and_verified_on_worker_stage_in=True)
    write(output / 'seal.json', seal)
    (output / 'pipeline.dag').write_text(dag.replace('SEAL_DIGEST', sha256(output / 'seal.json')))
    return dict(output=str(output), campaign=plan['campaign'], events=plan['events'], points=len(names),
        detector_jobs=plan['detector_chunks'], seal_sha256=sha256(output / 'seal.json'), submitted=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('preparation', 'template', 'gen-inventory', 'gen-publication', 'storage-budget', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--prior-inventories', required=True, nargs='+', type=Path)
    parser.add_argument('--canary-points', required=True, nargs='+')
    parser.add_argument('--failure-base', required=True)
    parser.add_argument('--detector-workers', type=int, default=100)
    parser.add_argument('--finalizers', type=int, default=3)
    parser.add_argument('--event-stall-timeout', type=int, default=1800)
    parser.add_argument('--first-event-stall-timeout', type=int, default=3600)
    parser.add_argument('--detector-timeout', type=int, default=21600,
                        help='Total detector worker deadline in seconds, at most72000; physics is unchanged')
    parser.add_argument('--publication-profile', choices=tuple(PROFILES), default='all-tiers-v1')
    parser.add_argument('--publication-canary', type=Path,
                        help='Pinned measured full-chain Nano/metadata canary required for nano-metadata-v1')
    print(json.dumps(prepare(parser.parse_args())))


if __name__ == '__main__':
    main()
