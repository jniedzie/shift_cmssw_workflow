#!/usr/bin/env python3
# Code style: AI-code.
"""Prepare an unchanged QCD source, check all primaries, retain compact receipts.

The full source authority is retained on EOS and returned compressed. No source
is allowed into SIM before the actual converter checks its complete GEN census.
"""
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from run_shift_sm_postprocessing import publish


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reuse_prepared(request):
    """Keep the old placed GEN bytes, decays, full IDs and native weights."""
    from run_shift_sm_postprocessing import download
    from run_shift_smart_sm_source import prepared_selection
    pin = request['reuse_prepared_source']
    if sha(pin['path']) != pin['sha256']:
        raise ValueError('Original prepared source changed')
    original = json.loads(Path(pin['path']).read_text())
    if original.get('complete') is not True or original.get('exit_code') != 0:
        raise ValueError('Only completed original preparation can be reused')
    source = original['source']
    if source['source_id'] != request['source_request']['id']:
        raise ValueError('Reused source identity changed')
    if original.get('empty_source'):
        return dict(original, published_storage_bytes=0, reused_prepared_source=pin)
    old = original['selected_source']
    feature = next(p for p in original['prepared_publications'] if p['name']=='qcd_features.json.gz')
    local = Path('original_features.json.gz')
    download(feature['path'], local, feature['sha256'])
    if local.stat().st_size != feature['bytes']:
        raise ValueError('Original full-census feature size changed')
    with gzip.open(local, 'rt') as stream:
        features = json.load(stream)
    prepared = dict(url=old['gen'], gen_sha256=old['gen_sha256'])
    selected, ledger = prepared_selection(request, source, prepared, features,
        old['event_ids'], old['weights'], pin['sha256'])
    local.unlink()
    path = Path('gen_filter_ledger.json.gz')
    with gzip.open(path, 'wt') as stream:
        json.dump(ledger, stream, separators=(',', ':'), allow_nan=False)
    published = publish(path, request['publication_directory'])
    return dict(schema='shift-smart-SM-source-v1', complete=True,
        source_id=source['source_id'], source=source, selected_source=selected,
        selected_events=len(selected['selected_indices']), raw_events=len(selected['event_ids']),
        qcd_gen_filter=selected['qcd_gen_filter'], reused_prepared_source=pin,
        original_GEN_bytes_and_saved_decays_unchanged=True,
        filter_ledger=published, published_storage_bytes=published['bytes']+65536)


def read_source_authority(directory, receipt, *, archive_name='return_payload.tar.gz',
                          cache_name='source_authority.json.gz'):
    """Resolve only this successful worker's exact compressed return member."""
    import tarfile
    directory = Path(directory)
    pin = receipt['source_authority']
    name = 'source_authority.json.gz'
    if pin['name'] != name:
        raise ValueError('Unexpected source authority member')
    if any(Path(value).name != value or value in ('','.', '..')
           for value in (archive_name,cache_name)):
        raise ValueError('Authority cache and archive must be local basenames')
    local = directory/cache_name
    if not local.exists():
        with tarfile.open(directory/archive_name) as archive:
            member = archive.getmember(name)
            if not member.isfile() or member.size != pin['bytes'] or member.size > 4*1024*1024:
                raise ValueError('Invalid compressed source authority size')
            data = archive.extractfile(member).read()
        if hashlib.sha256(data).hexdigest() != pin['sha256']:
            raise ValueError('Returned source authority changed')
        temporary = local.with_suffix('.new')
        temporary.write_bytes(data)
        temporary.replace(local)
    if local.stat().st_size != pin['bytes'] or sha(local) != pin['sha256']:
        raise ValueError('Retained source authority changed')
    with gzip.open(local, 'rb') as stream:
        data = stream.read(16*1024*1024+1)
    if len(data) > 16*1024*1024:
        raise ValueError('Expanded source authority exceeds its bound')
    authority = json.loads(data)
    if (authority.get('complete') is not True
            or authority.get('primary_preflight', {}).get('complete') is not True
            or authority['source'] != receipt['source']):
        raise ValueError('Source authority lacks matching full-census qualification')
    return authority


def array_source_request(request, index):
    """Expand a frozen finite source range; change no generator prescription."""
    spec = request['source_array']
    if type(index) is not int or not 0 <= index < spec['count']:
        raise ValueError('Source array index is outside its frozen range')
    result = copy.deepcopy(request)
    result.pop('source_array')
    number = spec['index_start']+index
    row = result['source_request']
    row.update(id=spec['id_prefix']+str(number).zfill(6),
        run=spec['run_base']+number,seed=spec['seed_base']+50*number)
    if not 0 < row['seed'] < 900000000 or not 30000000 <= row['run'] <= 4294967295:
        raise ValueError('Frozen source array exceeds the GEN seed/run domain')
    result['publication_directory'] = spec['publication_parent']+'/'+row['id']
    result['source_array_index'] = index
    result['source_array_number'] = number
    return result


def main(request_path):
    request_path = Path(request_path).resolve()
    request = json.loads(request_path.read_text())
    outer_request_sha256 = sha(request_path)
    if request.get('source_array') is not None:
        ad = Path(os.environ['_CONDOR_JOB_AD']).read_text()
        indices = re.findall(r'^ShiftQCDSourceIndex\s*=\s*(\d+)\s*$',ad,re.MULTILINE)
        if len(indices) != 1:
            raise ValueError('The source array needs one native scheduler index')
        request = array_source_request(request,int(indices[0]))
        request_path = Path('effective_source_request.json').resolve()
        request_path.write_text(json.dumps(request,indent=2)+'\n')
    if request['source_request']['family'] != 'qcd':
        raise ValueError('This source adapter is qualified only for QCD')
    tools = Path(__file__).resolve().parents[1]
    authority = dict(complete=False)
    try:
        from run_shift_smart_sm_source import main as prepare_source
        if request.get('reuse_prepared_source'):
            authority = reuse_prepared(request)
        else:
            prepare_source(request_path)
            authority = json.loads(Path('source_report.json').read_text())
        selected = authority.get('selected_source')
        if selected is not None:
            directory = Path('primary_preflight')
            directory.mkdir()
            preflight = dict(schema='shift-stream-payload-v1', environment='cmssw',
                stage='sm_primary_preflight', runtime_sha256=request['runtime_sha256'],
                preflight_addon=request['preflight_addon'], sources=[dict(
                    source_id=authority['source_id'], gen_path=selected['gen'],
                    gen_sha256=selected['gen_sha256'], full_event_ids=selected['event_ids'],
                    source_descriptor=selected)])
            path = directory/'request.json'
            path.write_text(json.dumps(preflight, indent=2)+'\n')
            with (directory/'worker.log').open('w') as log:
                result = subprocess.run([sys.executable,
                    str(tools/'scripts/run_shift_sm_primary_preflight.py'), str(path.resolve())],
                    cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=2700)
            proof = json.loads((directory/'report.json').read_text())
            authority['primary_preflight'] = proof
            if result.returncode != 0 or proof.get('complete') is not True:
                raise ValueError('Full original GEN primary conversion failed')
        elif authority.get('empty_source') is True and authority['source']['saved_events'] == 0:
            authority['primary_preflight'] = dict(complete=True, empty_original_GEN=True)
        else:
            raise ValueError('Missing selected descriptor for a nonempty original source')
    except BaseException as error:
        if Path('source_report.json').is_file():
            prior = json.loads(Path('source_report.json').read_text())
            authority = dict(prior, **{k:v for k,v in authority.items() if k != 'complete'})
        authority.update(complete=False, error=repr(error))
        authority['failure_log_tails'] = {}
        for path in Path('primary_preflight').glob('*.log'):
            authority['failure_log_tails'][path.name] = path.read_bytes()[-32768:].decode(errors='replace')
    local = Path('source_authority.json.gz')
    authority['effective_source_request_sha256'] = sha(request_path)
    authority['outer_request_sha256'] = outer_request_sha256
    if 'source_array_index' in request:
        authority['source_array_index'] = request['source_array_index']
        authority['source_array_number'] = request['source_array_number']
    with gzip.open(local, 'wt') as stream:
        json.dump(authority, stream, separators=(',', ':'), allow_nan=False)
    pin = publish(local, request['publication_directory'])
    receipt = {key:authority[key] for key in (
        'complete', 'source_id', 'source', 'empty_source', 'selected_events', 'raw_events',
        'qcd_gen_filter', 'error') if key in authority}
    receipt.update(source_authority=dict(pin, name=local.name),
        published_storage_bytes=authority.get('published_storage_bytes', 0)+pin['bytes']+65536,
        full_original_GEN_preflight_passed=authority['complete'])
    for key in ('source_array_index','source_array_number','effective_source_request_sha256'):
        if key in authority:
            receipt[key] = authority[key]
    Path('report.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return 0 if authority['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
