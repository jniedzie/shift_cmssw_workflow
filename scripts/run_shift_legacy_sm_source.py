#!/usr/bin/env python3
# Code style: AI-code.
"""Reuse a pinned, unit-weight legacy GEN chunk through the ordinary source path."""
import ast
import copy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from shift_eos_storage import eos_endpoint, xrootd_url


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generator_digest(text):
    """Resolved configs have one complete generator assignment; seeds are separate."""
    nodes = [node.value for node in ast.parse(text).body if isinstance(node, ast.Assign)
             and any(ast.unparse(target) == 'process.generator' for target in node.targets)]
    if len(nodes) != 1:
        raise ValueError('Legacy resolved config needs one complete generator assignment')
    return hashlib.sha256(ast.dump(nodes[0], include_attributes=False).encode()).hexdigest()


def download(path, target, expected=None):
    subprocess.run(['xrdcp', '--silent', '--cksum', 'adler32', xrootd_url(path), str(target)],
                   check=True, timeout=900)
    digest = sha(target)
    if expected is not None and digest != expected:
        raise ValueError('Legacy immutable input changed: '+str(path))
    return dict(path=path, bytes=target.stat().st_size, sha256=digest,
                independent_sha256_readback=True)


def stage_native_receipt(request):
    """No GEN is generated or copied to new EOS storage; preserve its native audit."""
    from audit_shift_material_scout import identity, metadata, product
    from DataFormats.FWLite import Events
    from run_shift_sm_postprocessing import publish
    row, old = request['source_request'], request['legacy_input']
    if row['family'] not in ('dy', 'jpsi') or row.get('generation_sampling') != 'native_class':
        raise ValueError('Legacy reuse supports only native DY and Jpsi source classes')
    directory = Path('legacy_inputs');directory.mkdir()
    artifacts = []
    for name, path, expected in [
            ('gen.root', old['GEN_path'], old.get('GEN_sha256')),
            ('validation.json', old['metadata_pin']['path'], old['metadata_pin']['sha256'])]:
        target = directory/name
        artifacts.append(dict(download(path, target, expected), name=name))
    audit = json.loads((directory/'validation.json').read_text())
    config_path = old.get('config_path')
    if old.get('config_directory'):
        parent = old['config_directory']
        # Each legacy chunk keeps its original part number in the GEN leaf
        # and config basename; neither its part nor its seed resets to zero.
        stem = Path(old['GEN_path']).name.split('__')[-1].removesuffix('.root')
        if not stem.startswith('events_step1_part') or not stem[17:].isdigit():
            raise ValueError('Legacy step1 GEN basename has no original part number')
        listing = subprocess.run(['xrdfs', eos_endpoint(parent), 'ls', parent],
            check=True, capture_output=True, text=True, timeout=120).stdout
        candidates = [path.strip() for path in listing.splitlines()
                      if Path(path.strip()).name.startswith(stem+'_seed')
                      and Path(path.strip()).name.endswith('_cfg.py')]
        if len(candidates) != 1:
            raise ValueError('Legacy chunk needs exactly one native step1 config')
        config_path = candidates[0]
    if not config_path:
        raise ValueError('Missing original legacy config path')
    artifacts.append(dict(download(config_path, directory/'resolved_gen_cfg.py',
        audit.get('config_sha256', old.get('config_sha256'))), name='resolved_gen_cfg.py'))
    if generator_digest((directory/'resolved_gen_cfg.py').read_text()) != old['generator_ast_sha256']:
        raise ValueError('Legacy generator prescription differs from the reviewed bin')
    identities, weights = [], []
    for event in Events(str(directory/'gen.root')):
        identities.append(list(identity(event)))
        _, info = product(event, 'generator', 'GenEventInfoProduct')
        weights.append(float(info.weight()))
    count = row['events_or_trials']
    if (len(identities) != count or len(set(map(tuple, identities))) != count
            or weights != [1.]*count or len({i[0] for i in identities}) != 1):
        raise ValueError('Legacy GEN lost its complete unique unit-weight census')
    if old.get('prior_census_ref'):
        reference = old['prior_census_ref']
        staged = Path(Path(reference['path']).name)
        census = staged if staged.is_file() else Path(reference['path'])
        if census.stat().st_size > 4*1024**2 or sha(census) != reference['sha256']:
            raise ValueError('Prior source census changed or exceeds its bound')
        with gzip.open(census, 'rb') as stream:
            data = stream.read(16*1024**2+1)
        if len(data) > 16*1024**2:
            raise ValueError('Expanded prior source census exceeds its bound')
        prior = json.loads(data)
        if (artifacts[0]['sha256'] in prior['GEN_sha256s']
                or set(map(tuple, identities)).intersection(map(tuple, prior['event_ids']))
                or set(i[0] for i in identities).intersection(prior.get('reserved_source_runs', []))):
            raise ValueError('Legacy GEN overlaps the protected prior source census')
    if (audit.get('input_sha256', artifacts[0]['sha256']) != artifacts[0]['sha256']
            or audit.get('config_sha256', artifacts[2]['sha256']) != artifacts[2]['sha256']):
        raise ValueError('Legacy native audit hashes differ from its actual GEN/config')
    exposure = metadata(directory/'gen.root')
    rates = [run['products']['generator'] for run in exposure['runs']]
    expected_rates = old['native_RunInfo']
    if (len(rates) != len(expected_rates) or any(
            actual['internal_xsec_pb'] != expected['internal_xsec_pb']
            or actual['internal_error_pb'] != expected['error_pb']
            for actual, expected in zip(rates, expected_rates))):
        raise ValueError('Legacy native RunInfo differs from its pinned source accounting')
    effective = copy.deepcopy(request)
    effective['source_request']['run'] = identities[0][0]
    receipt = dict(schema='shift-smart-SM-native-source-v1', events=count, event_ids=identities,
        semantic_audit=dict(event_ids=identities, weights=weights, native_audit=audit),
        publications=artifacts, legacy_receipt_adapter=dict(new_GEN_exposure=0,
            original_GEN_path=old['GEN_path'], original_RunInfo=expected_rates,
            original_audit_sha256=old['metadata_pin']['sha256'],
            generator_and_forced_decay_settings_unchanged=True))
    local = Path('legacy_native_receipt.json')
    local.write_text(json.dumps(receipt, separators=(',', ':'))+'\n')
    pin = publish(local, request['publication_directory']+'/legacy_receipt')
    effective['resume_raw'] = dict(url=xrootd_url(pin['path']), sha256=pin['sha256'])
    return effective, pin


def main(request_path):
    from run_shift_smart_sm_source import main as prepare_source
    from run_shift_sm_postprocessing import publish
    request_path = Path(request_path).resolve()
    request = json.loads(request_path.read_text())
    authority = dict(complete=False, source_id=request['source_request']['id'], new_GEN_exposure=0)
    try:
        effective, receipt = stage_native_receipt(request)
        effective_path = Path('effective_source_request.json').resolve()
        effective_path.write_text(json.dumps(effective, indent=2)+'\n')
        prepare_source(effective_path)
        authority = json.loads(Path('source_report.json').read_text())
        authority['legacy_native_receipt'] = receipt
        selected = authority['selected_source']
        maximum = request.get('detector_maximum_publication_bytes')
        if maximum is not None:
            if type(maximum) is not int or maximum <= 65536:
                raise ValueError('Invalid legacy detector publication bound')
            selected['maximum_publication_bytes'] = maximum
        directory = Path('primary_preflight');directory.mkdir()
        preflight = dict(schema='shift-stream-payload-v1', environment='cmssw',
            stage='sm_primary_preflight', runtime_sha256=request['runtime_sha256'],
            preflight_addon=request['preflight_addon'], sources=[dict(
                source_id=authority['source_id'], gen_path=selected['gen'],
                gen_sha256=selected['gen_sha256'], full_event_ids=selected['event_ids'],
                source_descriptor=selected)])
        path = (directory/'request.json').resolve()
        path.write_text(json.dumps(preflight, indent=2)+'\n')
        with (directory/'worker.log').open('w') as log:
            process = subprocess.run([sys.executable,
                str(Path(__file__).parent/'run_shift_sm_primary_preflight.py'), str(path)],
                cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=2700)
        authority['primary_preflight'] = json.loads((directory/'report.json').read_text())
        if process.returncode or authority['primary_preflight'].get('complete') is not True:
            raise ValueError('Full original legacy GEN primary conversion failed')
        authority.update(legacy_reuse_qualified=True, new_GEN_exposure=0)
    except BaseException as error:
        authority.update(complete=False, error=repr(error), new_GEN_exposure=0)
    authority['effective_source_request_sha256'] = sha('effective_source_request.json') if Path(
        'effective_source_request.json').exists() else None
    authority['outer_request_sha256'] = sha(request_path)
    local = Path('source_authority.json.gz')
    with gzip.open(local, 'wt') as stream:
        json.dump(authority, stream, separators=(',', ':'), allow_nan=False)
    pin = publish(local, request['publication_directory'])
    # References to original GEN/config/audit files are not new storage writes.
    # A failed preparation retains its entire reservation in the controller.
    writes = authority.get('prepared_publications', [])+authority.get('publications', [])
    writes += [authority['legacy_native_receipt']] if authority.get('legacy_native_receipt') else []
    writes += [pin]
    if len({p['path'] for p in writes}) != len(writes):
        raise ValueError('Repeated publication path in legacy source write accounting')
    if any(not p['path'].startswith(request['publication_directory']+'/') for p in writes):
        raise ValueError('Legacy new-write accounting includes an original input reference')
    result = {key:authority[key] for key in ('complete','source_id','source','selected_events',
        'raw_events','error','new_GEN_exposure') if key in authority}
    result.update(source_authority=dict(pin, name=local.name),
        full_original_GEN_preflight_passed=authority['complete'],
        published_storage_bytes=sum(p['bytes'] for p in writes)+1048576,
        original_GEN_inputs_are_references_not_new_writes=True)
    Path('report.json').write_text(json.dumps(result, indent=2)+'\n')
    return 0 if authority['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
