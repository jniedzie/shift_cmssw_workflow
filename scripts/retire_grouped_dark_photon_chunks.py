#!/usr/bin/env python3
# Code style: AI-code. AI-generated exact-manifest grouped Nano retirement helper.
"""Review raw Nano duplicates only after full point grouping and histogram closure.

Dry runs retain every payload. Execution additionally requires the reviewed plan
SHA, fresh account-wide scheduler coverage and unchanged retained readbacks.
GEN, merged Nano, histograms, metadata, ledgers and frozen source files are never
deletion candidates. A failed check stops retirement without widening scope.

--prepare-controller freezes the exact adaptive and meson source worklists.
Its detached worker waits for a reviewed successful deletion canary before
retiring further complete points or advertising verified freed storage.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import getpass
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import time

import dark_photon_production_support as support
import retire_dark_photon_intermediates as guards
import shift_condor_native as native


SCHEMA = 'shift-grouped-dark-photon-raw-nano-retirement-v1'
EOS_BASE = '/eos/user/j/jniedzie/shift_cmssw'
HOST = 'root://eosuser.cern.ch'


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.next')
    with temporary.open('w') as stream:
        stream.write(json.dumps(value, indent=1, sort_keys=True)+'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def artifact(path):
    path = Path(path)
    return dict(path=str(path), bytes=path.stat().st_size, sha256=support.sha256(path))


def checked_local(path, expected):
    require(support.sha256(path) == expected, 'Frozen local authority changed: '+str(path))
    return read(path)


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=300, env=native.native_env())
    require(result.returncode == 0 and not result.stderr.strip(), 'Native storage command failed: '+result.stderr[-2000:])
    return result.stdout


class Transport:
    def fetch(self, item, destination):
        run(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32', HOST+'/'+item['path'], str(destination)])
        observed = artifact(destination)
        require(observed['bytes'] == item['bytes'] and observed['sha256'] == item['sha256'],
                'Retirement source or retained readback changed: '+item['path'])
        return Path(destination)

    def remove(self, path):
        run(['/usr/bin/xrdfs', HOST, 'rm', path])

    def absent(self, path):
        result = subprocess.run(['/usr/bin/xrdfs', HOST, 'stat', path], capture_output=True,
                                text=True, timeout=60, env=native.native_env())
        if result.returncode == 0:
            return False
        message = (result.stdout+'\n'+result.stderr).lower()
        require('no such file or directory' in message or '[3011]' in message,
                'Unknown storage state while checking absence: '+path)
        return True


def semantic_inventory(paths):
    """Cover every branch; preserve undecodable EDM metadata as exact baskets.

    Events, Runs and luminosity rows are hashed as ordered typed values across
    file boundaries. EDM ProcessHistory/ParameterSets cannot be decoded by
    uproot, so their ordered uncompressed basket payloads and offsets must be
    byte-identical. A semantically valid rewrite with different baskets is
    conservatively rejected rather than losing metadata evidence.
    """
    import awkward as ak
    import uproot
    inventory, hashes, classes = {}, {}, None
    for path in paths:
        with uproot.open(path, handler=uproot.source.file.MemmapSource) as source:
            observed = source.classnames(cycle=False)
            if classes is None:
                classes = observed
            require(classes == observed, 'Nano ROOT key classes differ')
            for name, classname in observed.items():
                if classname == 'TObjString':
                    value = str(source[name])
                    if name in inventory:
                        require(inventory[name]['value'] == value, 'Nano string metadata changed')
                    inventory[name] = dict(classname=classname, value=value)
                    continue
                require(classname == 'TTree', 'Unsupported Nano object class: '+classname)
                tree = source[name]
                types = tree.typenames()
                if name not in inventory:
                    inventory[name] = dict(classname=classname, entries=0, branches={})
                    for branch, typename in types.items():
                        mode = 'ordered-uncompressed-baskets' if name in ('MetaData','ParameterSets') else 'ordered-values'
                        inventory[name]['branches'][branch] = dict(typename=typename, mode=mode)
                        hashes[name,branch] = hashlib.sha256()
                require(types == {key: value['typename'] for key,value in inventory[name]['branches'].items()},
                        'Nano branch schema changed: '+name)
                inventory[name]['entries'] += tree.num_entries
                if name in ('MetaData','ParameterSets'):
                    for branch in types:
                        node = tree[branch]
                        for index in range(node.num_baskets):
                            basket = node.basket(index)
                            offsets = basket.byte_offsets
                            record = dict(entries=basket.num_entries,
                                payload_sha256=hashlib.sha256(basket.data.tobytes()).hexdigest(),
                                offsets_sha256=(hashlib.sha256(offsets.tobytes()).hexdigest()
                                                if offsets is not None else None))
                            hashes[name,branch].update(json.dumps(record,sort_keys=True).encode()+b'\n')
                    continue
                # Resolve branch objects once. The general expression parser
                # repeatedly scans all1894 Nano branch names for each field.
                # Direct reads preserve identical values without that quadratic
                # lookup or loading every jagged column into memory together.
                nodes=dict(tree.iteritems(recursive=True))
                require(set(nodes) == set(types), 'Nano branch objects differ from the typed schema')
                for branch,node in nodes.items():
                    values=node.array(library='ak',array_cache=None)
                    for value in ak.to_list(values):
                        encoded = json.dumps(value, separators=(',',':'), allow_nan=True).encode()
                        hashes[name,branch].update(encoded+b'\n')
    require(inventory and 'Events' in inventory, 'No Nano Events tree')
    for (name,branch), digest in hashes.items():
        inventory[name]['branches'][branch]['sha256'] = digest.hexdigest()
    return inventory


def native_metadata_inventory(paths):
    """Use native CMSSW dictionaries for every persisted EDM metadata member."""
    import ROOT
    require(ROOT.gSystem.Load('libFWCoreFWLite') >= 0, 'Cannot load native EDM dictionaries')
    ROOT.FWLiteEnabler.enable()
    if not hasattr(ROOT,'shift_retirement_metadata_hex'):
        require(ROOT.gInterpreter.Declare(r'''
            #include "TBufferJSON.h"
            #include <string>
            std::string shift_retirement_metadata_hex(const void* value, TClass* klass) {
                const auto text = TBufferJSON::ConvertToJSON(value, klass);
                static const char digits[] = "0123456789abcdef";
                std::string result;
                result.reserve(text.Length()*2);
                for (int index=0; index<text.Length(); ++index) {
                    const unsigned char byte = text[index];
                    result += digits[byte >> 4];
                    result += digits[byte & 15];
                }
                return result;
            }
        '''),'Cannot declare the native metadata byte serializer')
    result, digests = {}, {}
    for path in paths:
        source=ROOT.TFile.Open(str(path))
        require(source and not source.IsZombie(), 'Native metadata ROOT read failed')
        try:
            for name in ('MetaData','ParameterSets'):
                tree=source.Get(name)
                require(tree, 'Missing retained EDM metadata tree: '+name)
                branches=[branch.GetName() for branch in tree.GetListOfBranches()]
                if name not in result:
                    result[name]=dict(entries=0,branches={branch:dict(mode='native-ordered-values') for branch in branches})
                    for branch in branches:digests[name,branch]=hashlib.sha256()
                require(branches == list(result[name]['branches']), 'Native metadata branch inventory changed')
                result[name]['entries'] += int(tree.GetEntries())
                for index in range(tree.GetEntries()):
                    require(tree.GetEntry(index) > 0, 'Cannot read native EDM metadata entry')
                    for branch in branches:
                        value=getattr(tree,branch)
                        klass=ROOT.TClass.GetClass(type(value).__cpp_name__)
                        require(klass, 'Native metadata class dictionary is missing')
                        # EDM hash strings may contain arbitrary bytes, including
                        # control characters. Hex preserves the complete native
                        # serialization without a lossy Unicode/JSON conversion.
                        encoded=str(ROOT.shift_retirement_metadata_hex(value,klass))
                        digests[name,branch].update(encoded.encode('ascii')+b'\n')
        finally:
            source.Close()
    for (name,branch),digest in digests.items():
        result[name]['branches'][branch]['sha256']=digest.hexdigest()
    return result


def native_metadata_comparison(inputs, merged, cmssw_base):
    with tempfile.TemporaryDirectory(prefix='dp-edm-metadata-',dir=Path(merged).parent) as temporary:
        request,result=Path(temporary)/'request.json',Path(temporary)/'result.json'
        write(request,dict(inputs=list(map(str,inputs)),merged=str(merged)))
        command=['/bin/bash','-c',
            'set -eo pipefail; source /cvmfs/cms.cern.ch/cmsset_default.sh; export SCRAM_ARCH=el9_amd64_gcc13; '
            'cd "$1/src"; eval "$(scram runtime -sh)"; exec python3 "$2" --metadata-request "$3" --metadata-result "$4"',
            'native-edm-metadata',str(cmssw_base),str(Path(__file__).resolve()),str(request),str(result)]
        completed=subprocess.run(command,env=native.native_env(),capture_output=True,text=True,timeout=1200)
        require(completed.returncode == 0 and result.is_file(),
                'Native EDM metadata inspection failed: '+completed.stderr[-2000:])
        comparison=read(result)
        require(comparison['expected'] == comparison['actual'], 'Native EDM metadata values changed during merging')
        return comparison['actual']


def compare_all_branches(inputs, merged, cmssw_base=None):
    expected, actual = semantic_inventory(inputs), semantic_inventory([merged])
    metadata=None
    if cmssw_base is not None:
        # ROOT may repack the two EDM object trees. Compare all persisted object
        # members with their native dictionaries, rather than requiring baskets
        # to retain the incidental input compression/layout.
        metadata=native_metadata_comparison(inputs,merged,cmssw_base)
        for name in ('MetaData','ParameterSets'):
            require(expected[name]['entries'] == actual[name]['entries'] == metadata[name]['entries'],
                    'Native metadata count differs from its uproot inventory')
            for branch in expected[name]['branches']:
                for value in (expected,actual):
                    value[name]['branches'][branch].update(metadata[name]['branches'][branch])
    require(expected == actual, 'Merged Nano changed branch values, schema or metadata baskets')
    compact = json.dumps(actual, sort_keys=True, separators=(',',':')).encode()
    return dict(complete=True, events=actual['Events']['entries'],
        trees=sum(value['classname']=='TTree' for value in actual.values()),
        event_branches=len(actual['Events']['branches']),
        all_branch_inventory_sha256=hashlib.sha256(compact).hexdigest(),
        edm_metadata_baskets_preserved=metadata is None,
        edm_metadata_values_preserved=True, native_edm_dictionaries=metadata is not None)


def scheduler_guard(proof, directory, prefix):
    # Any still-live DAG at this deployment blocks retirement, including work
    # on another point in the same wave. Future aggregations use retained groups.
    if Path(proof).suffix != '.gz':
        return guards.scheduler_guard(proof, dict(gen_directory=prefix+'/nanoAOD',
                                                 detector_directory=str(directory)))
    with gzip.open(proof,'rt') as stream:
        value=json.load(stream)
    validate_account_proof(value)
    scope=dict(gen_directory=prefix+'/nanoAOD',detector_directory=str(directory))
    require(not any(guards.point_referenced(list(ad.values()),ad['Iwd'],scope) for ad in value['ads']),
            'Live scheduler reference to retirement point')
    return artifact(proof)


def validate_account_proof(value):
    account=getpass.getuser()
    query=value.get('query',{})
    constraint=f'Owner == "{account}" || AccountingGroupUser == "{account}"'
    stamp=value.get('captured_epoch')
    legacy=('-global' in query.get('command',[]) and query.get('constraint') == constraint)
    discovered=query.get('discovered_schedds_before',[])
    account_route=(query.get('method') == 'shift_condor_native.query_account'
        and query.get('owner_constraint') == 'Owner == '+json.dumps(account)+' && (true)'
        and isinstance(discovered,list) and bool(discovered) and len(set(discovered)) == len(discovered)
        and discovered == query.get('discovered_schedds_after')
        and query.get('all_discovered_schedds_queried_successfully') is True
        and query.get('native_helper_sha256') == support.sha256(native.__file__))
    require(value.get('schema') == guards.PROOF_SCHEMA and value.get('complete') is True
            and value.get('account_wide') is True and value.get('account') == account
            and type(stamp) in (int,float) and 0 <= time.time()-stamp <= 300
            and query.get('returncode') == 0 and query.get('stderr') == ''
            and (legacy or account_route),
            'Failed, stale or unknown account-wide scheduler coverage')
    require(isinstance(value.get('ads'),list),'Missing account-wide scheduler ads')
    for ad in value['ads']:
        require(isinstance(ad,dict) and (ad.get('Owner') == account or ad.get('AccountingGroupUser') == account)
                and isinstance(ad.get('Iwd'),str) and ('Cmd' in ad or 'Args' in ad or 'Arguments' in ad),
                'Incomplete account-wide reference-audit attributes')
        if account_route:
            require(ad.get('Owner') == account and ad.get('_queried_schedd') in discovered,
                    'Account ad escaped its verified owner/scheduler scope')


def capture_account_proof(path):
    account=getpass.getuser()
    attributes=['ClusterId','ProcId','Owner','AccountingGroupUser','Iwd','Cmd','Args','Arguments','JobStatus','DAGManJobId']
    pool='tweetybird04.cern.ch'
    # Retrying a read-only query cannot submit or remove a job. Never treat
    # partial global output as coverage when any schedd authentication failed.
    for attempt in range(3):
        try:
            before=native._account_schedds(account,timeout=30,local_schedd=None,pool=pool)
            ads=native.query_account(account,'true',attributes,timeout=60,pool=pool)
            after=native._account_schedds(account,timeout=30,local_schedd=None,pool=pool)
            if before != after:
                raise native.NativeCondorError('Account scheduler inventory changed across the audited query')
            break
        except native.NativeCondorError:
            if attempt == 2:
                raise
            time.sleep(10)
    proof=dict(schema=guards.PROOF_SCHEMA,account=account,account_wide=True,complete=True,
        captured_epoch=time.time(),query=dict(returncode=0,stderr='',method='shift_condor_native.query_account',
            owner_constraint='Owner == '+json.dumps(account)+' && (true)',collector=pool,
            discovery_scope='Ordinary and accounting-group submitter names',
            discovered_schedds_before=sorted(before),discovered_schedds_after=sorted(after),
            all_discovered_schedds_queried_successfully=True,native_helper_sha256=support.sha256(native.__file__)),ads=ads)
    validate_account_proof(proof)
    with gzip.open(path,'wt',compresslevel=6) as stream:
        json.dump(proof,stream,sort_keys=True,separators=(',',':'))
    return proof


def local_scratch(path):
    path = Path(path).resolve()
    worker = os.environ.get('_CONDOR_SCRATCH_DIR')
    require(str(path).startswith('/tmp/') or (worker and path == Path(worker).resolve()),
            'Heavy retirement checks require local /tmp or the actual Condor scratch directory')
    require(path.is_dir(), 'Local retirement scratch directory is missing')
    return path


def source_authority(directory, point, expected_seal):
    directory = Path(directory).resolve()
    seal = checked_local(directory/'seal.json', expected_seal)
    plan = checked_local(directory/'plan.json', seal['files'][str(directory/'plan.json')])
    require(plan['campaign'].startswith('20261016-'), 'Only the newly reviewed October 16 campaigns qualify')
    rows = [row for row in plan['points'] if row['point'] == point]
    require(len(rows) == 1, 'Missing or duplicate point')
    row = dict(rows[0], group_events=plan['group_events'])
    prefix = row['final_output_base']
    require(prefix == EOS_BASE+'/darkphoton_'+point+'/'+plan['campaign'] and '..' not in Path(prefix).parts,
            'Foreign or aliased sample namespace')
    inventory = checked_local(directory/'gen_inventory.json', seal['files'][str(directory/'gen_inventory.json')])
    parent = inventory['points'][point]
    report_path = directory/'completed_points'/(point+'.json')
    report = read(report_path)
    require(report.get('complete') is True and report['point'] == point and report['events'] == row['events']
            and report['plan_sha256'] == support.sha256(directory/'plan.json')
            and report['seal_sha256'] == expected_seal and report['final_output_base'] == prefix,
            'Full point histogram completion is missing or changed')
    union = report['union']
    require(union['complete'] is True and union['parent_gen'] == parent
            and union['event_ids'] == parent['event_ids'] and union['events'] == row['events']
            and union['native_sumw'] == union['native_sumw2'] == row['events']
            and union['histogramming_started_after_complete_grouping'] is True,
            'Full GEN population is not covered exactly')
    support.validate_complete_groups(row, parent['event_ids'])
    require([item['path'] for item in union['inputs']] == [chunk['nano'] for chunk in row['chunks']],
            'Raw candidates differ from the exact completed partition')
    for item, chunk in zip(union['inputs'],row['chunks']):
        require(support.valid_artifact(item, prefix+'/nanoAOD/chunk_'+f'{chunk["chunk"]:03d}.root'),
                'Candidate is not an exact raw Nano chunk')
    local = [artifact(directory/'seal.json'), artifact(directory/'plan.json'),
             artifact(directory/'gen_inventory.json'), artifact(report_path)]
    gen = Path(row['gen_directory'])
    for name,key in [('contract.json','contract_sha256'),('manifest.json','manifest_sha256'),
                     ('validation.json','validation_sha256')]:
        checked_local(gen/name,parent[key])
    for path in sorted(gen.rglob('*')):
        if path.is_file() and path.suffix not in ('.root','.hepmc','.pyc') and '__pycache__' not in path.parts:
            local.append(artifact(path))
    gen_publication = read(gen/'root_publication.json')
    require(gen_publication['path'] == parent['gen_path'] and gen_publication['sha256'] == parent['gen_sha256'],
            'Retained full GEN publication differs')
    protected = [dict(path=gen_publication['path'],bytes=gen_publication['bytes'],sha256=parent['gen_sha256'])]
    for name in ('meson_ledger_publication.json',):
        pointer = gen/name
        if pointer.exists():
            publication = read(pointer)
            protected.append({key:publication[key] for key in ('path','bytes','sha256')})
    return directory, plan, row, report, local, protected


def prepare(directory, point, expected_seal, proof, scratch, transport=None):
    transport = transport or Transport()
    directory, source_plan, row, report, local, protected = source_authority(directory, point, expected_seal)
    scheduler = scheduler_guard(proof,directory,row['final_output_base'])
    scratch = local_scratch(scratch)
    require(shutil.disk_usage(scratch).free >= 2_000_000_000, 'Insufficient local scratch for bounded group checks')
    equivalence, histograms = [], []
    with tempfile.TemporaryDirectory(prefix='dp-raw-nano-review-',dir=scratch) as temporary:
        work = Path(temporary)
        for index,item in enumerate(protected):
            retained=transport.fetch(item,work/('retained_'+str(index)))
            retained.unlink()
        chunks = {chunk['chunk']:item for chunk,item in zip(row['chunks'],report['union']['inputs'])}
        scored = {group['group']:group['score_histograms']['marker'] for group in report['groups']}
        require(len(scored) == len(row['groups']), 'Scored group coverage is incomplete')
        for group, completed in zip(row['groups'],report['union']['groups']):
            require(group['group'] == completed['group'] and group['count'] == completed['events']
                    and completed['grouped']['path'] == group['nano'], 'Retained group partition changed')
            inputs = [transport.fetch(chunks[number],work/('raw_'+str(number)+'.root')) for number in group['chunks']]
            merged = transport.fetch(completed['grouped'],work/'unscored.root')
            equivalence.append(dict(group=group['group'],**compare_all_branches(inputs,merged,source_plan['runtime']['cmssw_base'])))
            protected.append(completed['grouped'])
            marker = scored[group['group']]
            require(marker.get('complete') is True and marker['events'] == group['count'], 'Scoring did not complete the full group')
            for name in ('nano','histograms'):
                item = marker['published'][name]
                require(item['path'].startswith(row['final_output_base']+'/'+('nanoAOD_merged/' if name=='nano' else 'histograms/')),
                        'Scored retained output escaped its canonical namespace')
                path = transport.fetch(item,work/(name+'_'+str(group['group'])+'.root'))
                protected.append(item)
                if name == 'histograms':
                    histograms.append(path)
            for path in [*inputs,merged]:
                path.unlink()
        final_marker = report['score_histograms']['marker']
        require(final_marker.get('complete') is True and final_marker['events'] == row['events'], 'Full histogram marker is incomplete')
        merged_item = final_marker['published']['histograms']
        require(merged_item['path'] == row['merged_histograms'], 'Foreign merged histogram output')
        merged_hist = transport.fetch(merged_item,work/'histograms_merged.root')
        histogram_check = support.validate_histogram_merge(histograms,merged_hist)
        protected.append(merged_item)
    result = dict(schema=SCHEMA,complete=True,production=str(directory),point=point,
        campaign=source_plan['campaign'],prefix=row['final_output_base'],production_seal_sha256=expected_seal,
        runner_sha256=support.sha256(__file__),events=row['events'],candidates=report['union']['inputs'],
        total_candidate_bytes=sum(item['bytes'] for item in report['union']['inputs']),
        protected_local=local,protected_remote=protected,all_branch_equivalence=equivalence,
        histogram_merge=histogram_check,scheduler_review=scheduler,
        retention='Keep full GEN, both merged Nano versions, all histograms, ledgers, audit and provenance',
        deletion_scope='Exact nanoAOD/chunk_NNN.root files only; no directory deletion')
    require(not {item['path'] for item in result['candidates']} & {item['path'] for item in protected},
            'A canonical retained output became a deletion candidate')
    return result


def execute(plan_path, expected_plan_sha256, proof, scratch, transport=None):
    transport = transport or Transport()
    plan_path = Path(plan_path).resolve()
    plan = checked_local(plan_path,expected_plan_sha256)
    require(plan.get('schema') == SCHEMA and plan.get('complete') is True
            and plan['runner_sha256'] == support.sha256(__file__), 'Retirement plan or runner is unreviewed')
    directory,source_plan,row,report,local,protected = source_authority(plan['production'],plan['point'],plan['production_seal_sha256'])
    require(local == plan['protected_local'] and report['union']['inputs'] == plan['candidates'], 'Reviewed scientific authority changed')
    require(len(plan['all_branch_equivalence']) == len(row['groups'])
            and all(item.get('complete') is True and item.get('edm_metadata_values_preserved') is True
                    for item in plan['all_branch_equivalence'])
            and sum(item['events'] for item in plan['all_branch_equivalence']) == plan['events'],
            'Reviewed all-branch equivalence is incomplete')
    scratch = local_scratch(scratch)
    for item in plan['protected_local']:
        require(artifact(item['path']) == item, 'Protected local evidence changed')
    receipt_path = plan_path.with_name(plan_path.stem+'_deletion_receipt.json')
    receipt = read(receipt_path) if receipt_path.exists() else dict(schema=SCHEMA+'-deletion-receipt',
        plan_sha256=expected_plan_sha256,plan_path=str(plan_path),point=plan['point'],
        production=plan['production'],complete=False,deleted=[])
    require(receipt['plan_sha256'] == expected_plan_sha256, 'Previous deletion attempt belongs to a different plan')
    deleted = {item['path'] for item in receipt['deleted']}
    intents=receipt.setdefault('deletion_intents',{})
    candidates={item['path']:item for item in plan['candidates']}
    require(set(intents) <= set(candidates),'A deletion intent escaped the reviewed manifest')
    for path,intent in intents.items():
        require(intent.get('artifact') == candidates[path] and intent.get('plan_sha256') == expected_plan_sha256,
                'A durable deletion intent differs from the exact reviewed artifact')
    with tempfile.TemporaryDirectory(prefix='dp-raw-nano-retire-',dir=scratch) as temporary:
        work = Path(temporary)
        for index,item in enumerate(plan['protected_remote']):
            path=transport.fetch(item,work/('retained_'+str(index)))
            path.unlink()
        for index,item in enumerate(plan['candidates']):
            require(item['path'].startswith(plan['prefix']+'/nanoAOD/chunk_') and item['path'].endswith('.root'),
                    'Candidate escaped its reviewed raw directory')
            if item['path'] in deleted:
                require(transport.absent(item['path']), 'A retired candidate reappeared')
            elif item['path'] in intents and transport.absent(item['path']):
                # The previous worker may have died after remove but before
                # recording success. Full retained readbacks above and the
                # fresh native guard below precede closing this exact intent.
                continue
            else:
                path=transport.fetch(item,work/('raw_'+str(index)))
                path.unlink()
        receipt['scheduler_review'] = scheduler_guard(proof,directory,plan['prefix'])
        write(receipt_path,receipt)
        for item in plan['candidates']:
            if item['path'] in deleted:
                continue
            if item['path'] not in intents:
                intents[item['path']]=dict(artifact=item,plan_sha256=expected_plan_sha256,created_epoch=time.time())
                write(receipt_path,receipt)
            recovered=transport.absent(item['path'])
            if not recovered:
                transport.remove(item['path'])
            require(transport.absent(item['path']), 'Removed raw chunk is still present')
            receipt['deleted'].append(dict(**item,absence_verified=True,deleted_epoch=time.time(),
                reconciled_after_durable_intent=recovered))
            write(receipt_path,receipt)
        for index,item in enumerate(plan['protected_remote']):
            path=transport.fetch(item,work/('readback_'+str(index)))
            path.unlink()
    require(all(transport.absent(item['path']) for item in plan['candidates']), 'Retired candidates are not all absent')
    receipt.update(complete=True,retained_readbacks_verified=True,removed_bytes=plan['total_candidate_bytes'],
                   closed_epoch=time.time())
    write(receipt_path,receipt)
    return receipt


def controller_targets(adaptive,meson):
    """Authorize exact existing source worklists, including their future DAGs."""
    adaptive,meson=Path(adaptive).resolve(),Path(meson).resolve()
    worklist,meson_config=read(adaptive/'worklist.json'),read(meson/'config.json')
    targets=[]
    for wave in worklist['waves']:
        for row in wave['batches']:
            targets.append(dict(key=f'adaptive_w{wave["wave_index"]:03d}_'+row['point'],
                production=str(adaptive/'waves'/f'w{wave["wave_index"]:03d}'/'production'),point=row['point'],
                expected={key:row[key] for key in ('seed','run_number','events')}))
    for batch in meson_config['batches']:
        targets.append(dict(key='meson_'+batch['batch']+'_'+batch['point'],
            production=str(meson/'batches'/batch['batch']/'production'),point=batch['point'],
            expected={key:batch[key] for key in ('seed','run_number','mass_gev','epsilon')},
            fixed_source_collisions=batch['source_collisions']))
    require(len({row['key'] for row in targets}) == len(targets),'Duplicate authorized retirement target')
    authorities=[artifact(path) for path in [adaptive/'worklist.json',adaptive/'seal.json',
                                            meson/'config.json',meson/'seal.json']]
    for path in (adaptive/'worklist.json',meson/'config.json'):
        seal=read(path.parent/'seal.json')
        require(seal['files'].get(str(path)) == support.sha256(path),'Worklist is outside its original source seal')
    return targets,authorities


def validate_target(target):
    directory=Path(target['production'])
    plan=read(directory/'plan.json')
    rows=[row for row in plan['points'] if row['point'] == target['point']]
    require(len(rows) == 1 and all(rows[0].get(key) == value for key,value in target['expected'].items()),
            'Prepared retirement point differs from its original source worklist')
    if 'fixed_source_collisions' in target:
        contract=read(Path(rows[0]['gen_directory'])/'contract.json')
        require(contract['source_exposure']['fixed_source_collisions'] == target['fixed_source_collisions'],
                'Meson fixed source exposure changed')
    return directory,rows[0]


def native_closed_manager(config,directory):
    receipt=read(directory/'submission.json')
    require(receipt['task'] == str(directory),'DAG manager working directory changed')
    local=Path(native.SCHEDD_ADDRESS_FILE).is_file() and Path(native.SCHEDD_AD_FILE).is_file()
    scheduler=[] if local else ['-name',config['condor_schedd'],'-pool',config['condor_pool']]
    rows=native.parse_ads(native.run_condor(['/usr/bin/condor_history',*scheduler,
        str(receipt['cluster'])+'.0','-limit','1','-json'],timeout=60))
    require(len(rows) == 1 and rows[0].get('ClusterId') == receipt['cluster']
            and rows[0].get('ProcId') == 0 and rows[0].get('Iwd') == str(directory)
            and rows[0].get('ExitCode') == 0 and not rows[0].get('ExitBySignal'),
            'Native DAG completion is missing, failed or unknown')
    return dict(cluster=receipt['cluster'],native_exit_code=0,Iwd=str(directory))


def publish_freed_gate(config,state,seal):
    receipts=[]
    for key,entry in state.get('targets',{}).items():
        if not entry.get('retired'):
            continue
        item=artifact(entry['receipt'])
        value=read(item['path'])
        require(value.get('complete') is True and value.get('retained_readbacks_verified') is True
                and type(value.get('closed_epoch')) in (float,int) and math.isfinite(value['closed_epoch'])
                and 0 < value['closed_epoch'] <= time.time()+60
                and value['removed_bytes'] == sum(row['bytes'] for row in value['deleted'])
                and all(row.get('absence_verified') is True for row in value['deleted']),
                'Freed storage credit lacks closed per-file deletion/readback evidence')
        receipts.append(dict(key=key,receipt=item,removed_bytes=value['removed_bytes'],closed_epoch=value['closed_epoch']))
    removed=sum(row['removed_bytes'] for row in receipts)
    require(removed <= config['maximum_retired_bytes'],'Retired volume exceeds the reviewed complete-grid bound')
    gate=dict(schema='shift-dp-raw-nano-retirement-storage-gate-v1',complete=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),controller_seal_sha256=seal,
        verified_removed_bytes=removed,receipts=receipts,
        quota_credit_policy='Evidence of actual removals only; current quota already includes the freed space')
    write(Path(config['directory'])/'freed_storage_gate.json',gate)
    return gate


def controller_step(config,state,seal,scratch):
    state.update(checked_utc=datetime.now(timezone.utc).isoformat())
    for item in config['authorities']:
        require(artifact(item['path']) == item,'Original authorized source worklist changed')
    gate_path=Path(config['activation_gate'])
    if not gate_path.is_file():
        state['phase']='waiting_for_root_reviewed_deletion_canary'
        return state
    gate=read(gate_path)
    require(gate.get('complete') is True and gate.get('controller_seal_sha256') == seal
            and gate.get('schema') == 'shift-dp-raw-nano-retirement-activation-v1'
            and isinstance(gate.get('initial_receipts'),dict) and gate['initial_receipts'],
            'Retirement activation does not match the reviewed controller')
    entries=state.setdefault('targets',{})
    # The manual first canary is adopted exactly once after its full readback.
    for key,item in gate.get('initial_receipts',{}).items():
        require(key in {target['key'] for target in config['targets']},'Canary outside the authorized target grid')
        require(artifact(item['path']) == item,'Reviewed canary deletion receipt changed')
        receipt=read(item['path'])
        target=next(target for target in config['targets'] if target['key'] == key)
        require(receipt.get('complete') is True and receipt.get('retained_readbacks_verified') is True,
                'Initial deletion canary is incomplete')
        require(receipt.get('point') == target['point'] and receipt.get('production') == target['production']
                and support.sha256(receipt['plan_path']) == receipt['plan_sha256']
                and gate.get('reviewed_canary_plan_sha256') == receipt['plan_sha256'],
                'Initial canary does not match the reviewed source point and manifest')
        entries.setdefault(key,dict(retired=True,receipt=item['path']))
    root=Path(config['directory'])
    for target in config['targets']:
        entry=entries.setdefault(target['key'],{})
        if entry.get('retired'):
            continue
        directory=Path(target['production'])
        if not (directory/'completed_points'/(target['point']+'.json')).is_file():
            continue
        directory,row=validate_target(target)
        records=root/'records'/target['key']
        records.mkdir(parents=True,exist_ok=True)
        proof=records/'scheduler_prepare.json.gz'
        observed=capture_account_proof(proof)
        scope=dict(gen_directory=row['final_output_base']+'/nanoAOD',detector_directory=str(directory))
        if any(guards.point_referenced(list(ad.values()),ad['Iwd'],scope) for ad in observed['ads']):
            entry['phase']='waiting_for_all_native_jobs_in_deployment_to_finish'
            proof.unlink()
            continue
        entry['native_manager']=native_closed_manager(config,directory)
        manifest=records/'retirement_plan.json'
        if not manifest.exists():
            state.update(phase='reviewing_all_branches_and_retained_outputs',current_target=target['key'])
            write(root/'state.json',state)
            value=prepare(directory,target['point'],support.sha256(directory/'seal.json'),proof,scratch)
            write(manifest,value)
        state.update(phase='retiring_verified_raw_duplicates',current_target=target['key'])
        write(root/'state.json',state)
        already_removed=sum(read(value['receipt'])['removed_bytes'] for value in entries.values() if value.get('retired'))
        require(already_removed+read(manifest)['total_candidate_bytes'] <= config['maximum_retired_bytes'],
                'Next exact manifest exceeds the reviewed complete-grid retirement bound')
        fresh=records/'scheduler_execute.json.gz'
        capture_account_proof(fresh)
        receipt=execute(manifest,support.sha256(manifest),fresh,scratch)
        entry.update(retired=True,phase='retired',receipt=str(manifest.with_name('retirement_plan_deletion_receipt.json')))
        publish_freed_gate(config,state,seal)
        write(root/'state.json',state)
    state.update(phase='watching_for_complete_points',complete=all(entries.get(target['key'],{}).get('retired') for target in config['targets']))
    publish_freed_gate(config,state,seal)
    return state


def run_controller(directory,seal,scratch):
    directory=Path(directory).resolve()
    authority=checked_local(directory/'seal.json',seal)
    for name,digest in authority['files'].items():
        require(support.sha256(name) == digest,'Frozen retirement controller dependency changed: '+name)
    config=read(directory/'config.json')
    require(config['directory'] == str(directory),'Controller working directory changed')
    scratch=local_scratch(scratch)
    stop=threading.Event()
    def heartbeat():
        while not stop.is_set():
            write(directory/'heartbeat.json',dict(checked_utc=datetime.now(timezone.utc).isoformat(),pid=os.getpid()))
            stop.wait(60)
    observer=threading.Thread(target=heartbeat,daemon=True)
    observer.start()
    try:
        with (directory/'controller.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            state=read(directory/'state.json') if (directory/'state.json').exists() else {}
            while True:
                try:
                    state=controller_step(config,state,seal,scratch)
                except Exception as error:
                    state.update(error=repr(error),checked_utc=datetime.now(timezone.utc).isoformat())
                    write(directory/'state.json',state)
                    raise
                write(directory/'state.json',state)
                if state.get('complete'):
                    return
                time.sleep(config['poll_seconds'])
    finally:
        stop.set()
        observer.join(timeout=5)


def prepare_controller(output,adaptive,meson):
    output=Path(output).resolve()
    require(not output.exists(),'Preserve the existing immutable retirement controller')
    targets,authorities=controller_targets(adaptive,meson)
    output.mkdir(parents=True)
    tools=output/'tools'
    tools.mkdir()
    paths=[]
    for name in [Path(__file__).name,'dark_photon_production_support.py',
                 'retire_dark_photon_intermediates.py','shift_condor_native.py']:
        target=tools/name
        shutil.copy2(Path(__file__).with_name(name),target)
        paths.append(target)
    config=dict(schema='shift-dp-raw-nano-retirement-controller-v1',directory=str(output),
        targets=targets,authorities=authorities,maximum_retired_bytes=50_000_000_000,poll_seconds=120,
        condor_schedd='bigbird26.cern.ch',condor_pool='tweetybird04.cern.ch',
        activation_gate=str(output/'activation.json'),
        scientific_policy='All source worklists and exposures unchanged; only exact closed-point raw Nano duplicates',
        scheduler_policy='Every discovered account schedd queried successfully; whole source DAG closed with native zero exit',
        metadata_policy='All Events, Runs and Lumi branches; all persisted EDM metadata members in native CMSSW')
    write(output/'config.json',config)
    wrapper='\n'.join(['#!/bin/bash','# Code style: AI-code. AI-generated frozen raw Nano retirement controller.',
        'set -eo pipefail','unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD ROOTSYS',
        'export PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1',
        'source /cvmfs/sft.cern.ch/lcg/views/LCG_108/x86_64-el9-gcc13-opt/setup.sh',
        'exec python3 '+shlex.quote(str(tools/Path(__file__).name))+' --controller-directory '+shlex.quote(str(output))+
        ' --controller-seal "$1" --scratch "$_CONDOR_SCRATCH_DIR"',''])
    (output/'controller.sh').write_text(wrapper)
    (output/'controller.sh').chmod(0o755)
    descriptor='\n'.join(['# Code style: AI-code. AI-generated persistent raw Nano retention supervisor.',
        'universe = vanilla','executable = '+str(output/'controller.sh'),'arguments = CONTROLLER_SEAL_DIGEST',
        'initialdir = '+str(output),'getenv = False','should_transfer_files = NO',
        'request_cpus = 1','request_memory = 8000MB','request_disk = 8000MB',
        '+JobFlavour = "nextweek"','+MaxRuntime = 604800','+SendCredential = True','+WantIOProxy = True',
        '+JobBatchName = "SHIFT_DP_raw_nano_retirement"',
        '+ShiftDPStatisticsTask = "'+str(output)+'"',
        'output = '+str(output/'controller.$(ClusterId).$(ProcId).out'),
        'error = '+str(output/'controller.$(ClusterId).$(ProcId).err'),
        'log = '+str(output/'controller.events.log'),
        'on_exit_hold = (ExitBySignal == True) || (ExitCode != 0)',
        'on_exit_remove = (ExitBySignal == False) && (ExitCode == 0)','periodic_release = False','queue',''])
    (output/'controller.sub.in').write_text(descriptor)
    paths.extend([output/'config.json',output/'controller.sh',output/'controller.sub.in'])
    files={str(path):support.sha256(path) for path in paths}
    files.update({item['path']:item['sha256'] for item in authorities})
    write(output/'seal.json',dict(schema='shift-raw-nano-retirement-controller-seal-v1',files=files))
    seal=support.sha256(output/'seal.json')
    (output/'controller.sub').write_text(descriptor.replace('CONTROLLER_SEAL_DIGEST',seal))
    return dict(complete=True,directory=str(output),seal_sha256=seal,targets=len(targets),
                inactive_until_reviewed_deletion_canary=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata-request',type=Path)
    parser.add_argument('--metadata-result',type=Path)
    parser.add_argument('--controller-directory',type=Path)
    parser.add_argument('--controller-seal')
    parser.add_argument('--prepare-controller',type=Path)
    parser.add_argument('--adaptive-directory',type=Path)
    parser.add_argument('--meson-directory',type=Path)
    parser.add_argument('--production',type=Path)
    parser.add_argument('--point')
    parser.add_argument('--production-seal')
    parser.add_argument('--scheduler-proof',type=Path)
    parser.add_argument('--scratch',type=Path)
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--approved-plan-sha256')
    args=parser.parse_args()
    if args.metadata_request:
        require(args.metadata_result,'Native metadata requires a result path')
        request=read(args.metadata_request)
        write(args.metadata_result,dict(expected=native_metadata_inventory(request['inputs']),
                                        actual=native_metadata_inventory([request['merged']])))
        return
    if args.prepare_controller:
        require(args.adaptive_directory and args.meson_directory,'Controller preparation needs the two frozen source worklists')
        print(json.dumps(prepare_controller(args.prepare_controller,args.adaptive_directory,args.meson_directory)))
        return
    if args.controller_directory:
        require(args.controller_seal and args.scratch,'Controller needs its seal and actual local scratch')
        run_controller(args.controller_directory,args.controller_seal,args.scratch)
        return
    require(args.scheduler_proof and args.scratch and args.plan,'Retirement needs a plan, scratch and scheduler proof')
    if args.execute:
        require(args.approved_plan_sha256,'Execution needs the reviewed exact plan SHA')
        result=execute(args.plan,args.approved_plan_sha256,args.scheduler_proof,args.scratch)
    else:
        require(args.production and args.point and args.production_seal,'Dry run needs a sealed production and point')
        require(not args.plan.exists(),'Preserve the existing review plan')
        result=prepare(args.production,args.point,args.production_seal,args.scheduler_proof,args.scratch)
        write(args.plan,result)
    print(json.dumps(dict(complete=result['complete'],point=result.get('point'),
        bytes=result.get('total_candidate_bytes',result.get('removed_bytes')),plan=str(args.plan))))


if __name__=='__main__':
    main()
