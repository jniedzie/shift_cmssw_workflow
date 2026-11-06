#!/usr/bin/env python3
# Code style: AI-code.
"""Credit retained, terminal peer outputs against their unchanged allocations.

This is a storage reader. It changes no producer, model, selection or exposure.
Unknown layouts and unfinished cohorts retain their entire reservation.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

from run_shift_sm_postprocessing import download
from shift_condor_native import native_env, run_condor
from monitor_shift_sm_response_benchmark import write_snapshot


def read(path):
    path = Path(path)
    if path.stat().st_size > 128*1024*1024:
        raise ValueError('Peer evidence exceeds the bounded reader size: '+str(path))
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pinned(pin):
    path = Path(pin['path'])
    if not path.is_absolute() or '..' in path.parts or sha(path) != pin['sha256']:
        raise ValueError('Peer storage input changed: '+str(path))
    return read(path)


def within(path, root):
    return isinstance(path, str) and (path == str(root) or path.startswith(str(root).rstrip('/')+'/'))


def sealed(directory, name, seal):
    path = directory/name
    if seal['files'].get(str(path)) != sha(path):
        raise ValueError('Peer scientific input lost its original seal: '+str(path))
    return read(path)


def closed_primitive(directory, campaign_prefix, ads):
    """Return only objects owned by a successful, sealed native producer."""
    directory = Path(directory)
    if any(within(ad.get('Iwd'), directory) for ad in ads):
        return None
    required = ['plan.json','seal.json','submission.json','pipeline.dag.dagman.log']
    if not all((directory/name).is_file() for name in required):
        return None
    seal, receipt = read(directory/'seal.json'), read(directory/'submission.json')
    plan = sealed(directory,'plan.json',seal)
    if receipt.get('task') != str(directory) or not plan['campaign'].startswith(campaign_prefix+'-'):
        raise ValueError('Peer producer belongs to another campaign or directory')
    cluster = receipt['cluster']
    blocks = (directory/'pipeline.dag.dagman.log').read_text().split('\n...\n')[:-1]
    terminal = [b for b in blocks if re.match(r'(005|009) \('+str(cluster)+r'\.000\.',b)]
    if not terminal or not terminal[-1].startswith('005 ') or 'Normal termination (return value 0)' not in terminal[-1]:
        return None
    proof = dict(directory=str(directory), cluster=cluster, plan_sha256=sha(directory/'plan.json'),
                 seal_sha256=sha(directory/'seal.json'), submission_sha256=sha(directory/'submission.json'),
                 terminal_event=terminal[-1], producer_receipts={})
    objects=[]
    if str(directory/'gen_publication.json') in seal['files']:
        publication = sealed(directory,'gen_publication.json',seal)
        inventory = sealed(directory,'gen_inventory.json',seal)
        if publication.get('complete') is not True or inventory.get('complete') is not True:
            raise ValueError('Terminal peer has incomplete GEN publication')
        for row in publication.get('inputs',[]):
            expected=inventory['points'][row['point']]
            if (row['path'] != expected['gen_path'] or row['sha256'] != expected['gen_sha256']
                    or row.get('readback_sha256') != row['sha256']):
                raise ValueError('Peer GEN bytes differ from their sealed inventory')
            if any(within(row['path'],point['final_output_base']) for point in plan['points']):
                objects.append(row)  # Reused older GEN is not a new paid write.
    for point in plan['points']:
        name=point['point']; path=directory/'completed_points'/(name+'.json')
        if not path.exists():
            return None
        report=read(path); marker=report.get('main_histograms',{})
        if (report.get('complete') is not True or marker.get('complete') is not True
                or report.get('plan_sha256') != proof['plan_sha256']
                or report.get('seal_sha256') != proof['seal_sha256']
                or report.get('point') != name):
            raise ValueError('Peer output lacks its sealed terminal completion')
        proof['producer_receipts'][str(path)]=sha(path)
        for row in marker.get('published',{}).values():
            if not within(row.get('path'),point['final_output_base']):
                raise ValueError('Peer publication escaped its actual publisher namespace')
            objects.append(row)
    return dict(proof=proof,objects=objects)


def retained_identity(item):
    """An EOS file ID and timestamps detect a changed retained publication."""
    answer=subprocess.run(['/usr/bin/xrdfs','root://eosuser.cern.ch','stat',item['path']],
        env=native_env(),capture_output=True,text=True,timeout=60)
    if answer.returncode:
        if '3011' in answer.stderr or 'No such file' in answer.stderr:
            return None
        raise RuntimeError('Could not stat retained peer output: '+answer.stderr[-1000:])
    fields=dict(re.findall(r'(?m)^\s*(Id|Size|MTime|CTime):\s*(.*?)\s*$',answer.stdout))
    if set(fields) != {'Id','Size','MTime','CTime'} or int(fields['Size']) != item['bytes']:
        raise ValueError('Retained peer output size or identity changed: '+item['path'])
    return fields


def verify_group(group, cache_directory):
    """Read each unchanged retained object independently once, preserving claims on missing groups."""
    unique={}
    for row in group['objects']:
        item={key:row[key] for key in ('path','bytes','sha256')}
        if (not item['path'].startswith('/eos/user/j/jniedzie/shift_cmssw/')
                or '..' in Path(item['path']).parts or type(item['bytes']) is not int
                or item['bytes']<=0 or not re.fullmatch('[0-9a-f]{64}',item['sha256'])):
            raise ValueError('Invalid peer published object')
        if item['path'] in unique and unique[item['path']] != item:
            raise ValueError('Conflicting peer output bytes')
        unique[item['path']]=item
    identities={path:retained_identity(item) for path,item in unique.items()}
    if any(identity is None for identity in identities.values()):
        return []  # Partial/retired groups retain their complete claim.
    evidence=dict(producer=group['proof'],objects=list(unique.values()),retained_identities=identities)
    key=hashlib.sha256(json.dumps(group['proof'],sort_keys=True).encode()).hexdigest()
    cache_directory=Path(cache_directory);cache_directory.mkdir(parents=True,exist_ok=True)
    cache=cache_directory/(key+'.json')
    if cache.exists():
        if read(cache) != evidence:
            raise ValueError('Verified retained peer group changed; preserve its claim')
    else:
        with tempfile.TemporaryDirectory(prefix='SM_peer_readback_') as tmp:
            for index,item in enumerate(unique.values()):
                local=Path(tmp)/str(index)
                download(item['path'],local,item['sha256'])
                local.unlink()  # Scratch holds one verified object at a time.
        if identities != {path:retained_identity(item) for path,item in unique.items()}:
            raise ValueError('Peer output changed during independent readback')
        write_snapshot(cache,evidence)
    return [dict(item,retained_identity=identities[path]) for path,item in unique.items()]


def read_peer_commitments(authority_pin):
    authority=pinned(authority_pin)
    if authority.get('schema') != 'shift-SM-current-peer-storage-v1':
        raise ValueError('Unsupported peer storage authority')
    registration=pinned(authority['registration'])
    amounts={peer['kind']:peer['reserved_bytes'] for peer in authority['peers']}
    if (registration.get('complete') is not True
            or registration.get('own_allocation_bytes')!=amounts.get('meson')
            or registration.get('direct_reserved_via_dynamic_worklist_claim')!=amounts.get('statistics')
            or registration.get('minimum_eos_free_bytes')!=authority['minimum_free_bytes']):
        raise ValueError('Current peer registration changed its total allocations or safety floor')
    endpoint=authority['endpoint']; location=['-name',endpoint['schedd'],'-pool',endpoint['pool']]
    attrs='ClusterId,ProcId,Iwd,JobStatus,DAGManJobId'
    def queue():
        value=run_condor(['/usr/bin/condor_q',*location,'-json','-attributes',attrs],timeout=60)
        return json.loads(value) if value.strip() else []
    ads=queue(); objects={}; identities={}; groups=[]; peers=[]
    for peer in authority['peers']:
        config=pinned(peer['configuration']); root=Path(config['directory'])
        if peer['kind']=='statistics':
            worklist=read(config['worklist'])
            if config['dependencies'].get(config['worklist']) != sha(config['worklist']):
                raise ValueError('Peer worklist changed')
            directories=[root/'waves'/f'w{i:03d}'/'production' for i in range(len(worklist['waves']))]
        elif peer['kind']=='meson':
            directories=[root/'batches'/row['batch']/'production' for row in config['batches']]
        else:
            raise ValueError('Unknown peer layout retains its claim')
        # Current cumulative writers have separate, immutable output namespaces.
        state=read(root/'state.json')
        directories += [Path(row['directory']) for row in state.get('aggregates',{}).values()
                        if isinstance(row,dict) and row.get('directory')]
        paid=0
        for directory in dict.fromkeys(directories):
            if not within(str(directory),root):
                raise ValueError('Peer completion outside its registered controller')
            group=closed_primitive(directory,config['campaign_prefix'],ads)
            if group is None:
                continue
            rows=verify_group(group,authority['cache_directory']); groups.append(group['proof'])
            for item in rows:
                path=item['path']; identity=item['retained_identity']['Id']
                if path in objects:
                    if objects[path] != item:
                        raise ValueError('Peer claims disagree on one retained output')
                    continue
                if identity in identities:
                    continue  # Never count a shared EOS inode twice.
                objects[path]=item;identities[identity]=path;paid+=item['bytes']
        if paid>peer['reserved_bytes']:
            raise ValueError('Verified peer writes exceed their unchanged allocation')
        peers.append(dict(name=peer['name'],reserved_bytes=peer['reserved_bytes'],
                          verified_paid_bytes=paid,remaining_bytes=peer['reserved_bytes']-paid))
    # A restarted producer invalidates this observation before any credit is used.
    final=queue()
    if any(within(ad.get('Iwd'),g['directory']) for ad in final for g in groups):
        raise RuntimeError('Peer writer became active during storage readback')
    total=sum(p['reserved_bytes'] for p in peers)+authority['fixed_uncredited_guard_bytes']
    paid=sum(item['bytes'] for item in objects.values())
    return dict(schema='shift-SM-verified-peer-remaining-storage-v1',complete=True,
        authority_sha256=authority_pin['sha256'],checked_epoch=time.time(),
        total_reserved_bytes=total,verified_paid_bytes=paid,remaining_bytes=total-paid,
        fixed_uncredited_guard_bytes=authority['fixed_uncredited_guard_bytes'],peers=peers,
        verified_groups=groups,objects=list(objects.values()),fresh_native_queue_verified=True,
        unused_headroom_and_unfinished_writes_preserved=True)
