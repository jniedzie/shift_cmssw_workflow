# Code style: AI-code. AI-generated placed direct-DY source provenance.
"""Freeze jaw-position draws independently of signal lifetime and detector work."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from shift_jaw_source import source_position

DEPENDENCIES=('dark_photon_source_placement.py','shift_jaw_source.py','shift_source_placement.py',
              'shift_material_preselection.py')


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):
            value.update(block)
    return value.hexdigest()


def place_contract(contract, profile_path):
    """Cover every framework slot, including rejected native-generator slots."""
    if 'source_placement' in contract:
        raise ValueError('A source contract must not be placed twice')
    result=copy.deepcopy(contract)
    profile_path=Path(profile_path)
    profile=json.loads(profile_path.read_text())
    preplacement=hashlib.sha256(json.dumps(contract,sort_keys=True,separators=(',',':'),
                                          allow_nan=False).encode()).hexdigest()
    slots=contract.get('maximum_framework_events',contract['requested_events'])
    if type(slots) is not int or slots < contract['requested_events']:
        raise ValueError('Invalid full framework namespace for source placement')
    seed='dp-jaw-source-'+str(contract['seed'])
    positions=[]
    for index in range(1,slots+1):
        draw=source_position(profile,preplacement,[contract['run_number'],1,index],seed)
        # The profile, source key and seed reproduce the full jaw/depth draw.
        # Keep only the positions needed by the native producer in its config.
        positions.append(dict(event_id=draw['event_id'],
                              target_position_cm=draw['target_position_cm']))
    result['source_placement']=dict(schema='shift-dp-assumed-jaw-source-v1',
        profile=profile,profile_sha256=digest(profile_path),selection_seed=seed,
        preplacement_source_model=contract.get('source_model'),
        original_source_sha256=preplacement,original_source_kind='preplacement-DY-contract',
        event_positions=positions,material_or_depth_weight_applied=False,
        signal_lifetime_and_rate_unchanged=True)
    result['source_model']='assumed jaw mixture and first-interaction depth; source law is provisional'
    return result


def record_runtime(manifest, base, output, provenance_path=None, archive_path=None):
    """Record a real frozen archive, or the actual ordinary Git runtime."""
    if provenance_path is None and archive_path is None:
        manifest['cmssw_git_head']=subprocess.check_output(
            ['git','-C',str(base/'src'),'rev-parse','HEAD'],text=True).strip()
        manifest['cmssw_git_status']=subprocess.check_output(
            ['git','-C',str(base/'src'),'status','--porcelain'],text=True)
        return
    if provenance_path is None or archive_path is None:
        raise ValueError('Frozen GEN runtime requires both provenance and its actual archive')
    provenance=json.loads(provenance_path.read_text())
    if (provenance.get('schema') != 'shift-bsm-gen-runtime-provenance-v1'
            or archive_path.stat().st_size != provenance.get('archive_bytes')
            or digest(archive_path) != provenance.get('archive_sha256')
            or os.environ.get('SCRAM_ARCH') != provenance.get('scram_arch')):
        raise ValueError('Worker GEN archive differs from frozen provenance')
    manifest.update(cmssw_git_head=provenance.get('cmssw_git_head'),
        cmssw_git_status=provenance.get('cmssw_git_status'),worker_local_runtime=provenance)
    shutil.copy2(provenance_path,output/'runtime_provenance.json')
    manifest['runtime_provenance_sha256']=digest(output/'runtime_provenance.json')
