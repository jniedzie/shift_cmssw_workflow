#!/usr/bin/env python3
"""Read-only worker audit; scheduler hosts deliberately never access EOS.

Only immutable completion receipts are counted. Inspect fresh representative
GEN payloads without reading masses or reconstruction-level observables.
"""
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def run(command):
    return subprocess.run(command, check=True, text=True, capture_output=True, timeout=180).stdout


WEIGHTED_CONTRACT = 'shift-weighted-gen-receipt-v3'
WEIGHTED_ARTIFACTS = ('gen.root', 'proposal_ledger.json', 'resolved_gen_cfg.py', 'semantic_audit.json')


def weighted_receipt(record, manager, manifest, row, gate_sha=None):
    """Validate terminal immutable metadata before counting any high-bin trial."""
    from audit_shift_weighted_gen import ALGORITHM, SOURCE_MODEL_SHA256, close, validate_ledger
    if (record.get('schema') != WEIGHTED_CONTRACT or record.get('complete') is not True or
            record.get('generated_edm') is not True or record.get('publication_complete') is not True or
            record.get('manifest_sha256') != manager['manifest_sha256'] or
            record.get('node_id') != row['id'] or record.get('request') != row or
            record.get('source_inputs') != manifest['inputs'] or
            record.get('algorithm') != manifest['algorithm'] or manifest['algorithm'] != ALGORITHM or
            record.get('physics_valid') is not False or record.get('normalization_ready') is not False):
        raise ValueError('Invalid frozen weighted completion receipt: '+row['id'])
    if row['stage'] == 'production' and record.get('scientific_gate_sha256') != gate_sha:
        raise ValueError('Production receipt has a different scientific gate: '+row['id'])
    ledger, semantic = record['ledger'], record['semantic_audit']
    trials = validate_ledger(ledger)
    event_class = 'direct_jpsi' if row['sample'] == 'jpsi' else row['sample']
    if (ledger['requested_trials'] != row['trials'] or ledger['run'] != row['run'] or
            ledger['sample'] != event_class or [ledger['lower'], ledger['upper']] != row['bounds']):
        raise ValueError('Weighted trial ledger differs from frozen request: '+row['id'])
    if (semantic.get('schema') != 'shift-weighted-gen-semantic-audit-v1' or
            semantic.get('complete') is not True or semantic.get('healthy') is not True or
            semantic.get('algorithm') != ALGORITHM or semantic.get('ledger') != ledger or
            semantic.get('source_model_settings_sha256') != SOURCE_MODEL_SHA256 or
            manifest['source_model_settings_sha256'] != SOURCE_MODEL_SHA256 or
            semantic.get('auditor_sha256') != manifest['inputs']['audit_shift_weighted_gen.py'] or
            semantic.get('events') != ledger['accepted'] or
            semantic.get('requested') != trials or semantic.get('tried') != trials or
            record.get('event_count') != ledger['accepted']):
        raise ValueError('Weighted semantic audit is not bound to fixed trials: '+row['id'])
    count = ledger['accepted']
    ids, weights = semantic['event_ids'], semantic['weights']
    if (type(record['event_count']) is not int or
            any(not isinstance(semantic[key], list) or len(semantic[key]) != count
                for key in ('event_ids', 'weights', 'scale', 'charged'))):
        raise ValueError('Weighted event-array counts differ: '+row['id'])
    identities = set()
    for identity, weight, scale, charged in zip(ids, weights, semantic['scale'], semantic['charged']):
        if (not isinstance(identity, list) or len(identity) != 3 or any(type(x) is not int for x in identity) or
                identity[0] != row['run'] or identity[1] != 1 or not 1 <= identity[2] <= trials or
                tuple(identity) in identities):
            raise ValueError('Duplicate or out-of-budget weighted event identity: '+row['id'])
        identities.add(tuple(identity))
        if (type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0 or
                type(scale) not in (int, float) or not math.isfinite(scale) or scale < row['bounds'][0] or
                (row['bounds'][1] != -1 and scale >= row['bounds'][1]) or
                type(charged) is not int or charged < 0):
            raise ValueError('Invalid weighted likelihood/scale/shape metadata: '+row['id'])
    sumw, sumw2 = math.fsum(weights), math.fsum(w*w for w in weights)
    close(ledger['sumw'], sumw, 'Receipt sum of weights')
    close(ledger['sumw2'], sumw2, 'Receipt sum of squared weights')
    sigma = ledger['sigma_nd_mb']*sumw/trials
    error = ledger['sigma_nd_mb']*math.sqrt(max(0., sumw2-sumw*sumw/trials)/(trials*(trials-1)))
    for actual in (ledger['sigma_mb'], semantic['sigma_weighted_mb']):
        close(actual, sigma, 'Receipt fixed-trial cross section')
    for actual in (ledger['sigma_error_mb'], semantic['error_weighted_mb']):
        close(actual, error, 'Receipt fixed-trial cross-section error')
    run_info = semantic['weighted_run_info']
    close(run_info['internal_xsec_pb'], sigma*1e9, 'Receipt named run cross section')
    close(run_info['error_pb'], error*1e9, 'Receipt named run cross-section error')
    close(run_info['filter_efficiency'], 1., 'Receipt named run filter efficiency')
    directory = manifest['eos_base'].rstrip('/')+'/'+row['id']
    if record.get('publication_directory') != directory:
        raise ValueError('Weighted publication directory differs: '+row['id'])
    publications = record['publication']
    if (not isinstance(publications, list) or len(publications) != len(WEIGHTED_ARTIFACTS) or
            any(not isinstance(item, dict) for item in publications)):
        raise ValueError('Malformed weighted publication manifest: '+row['id'])
    artifacts = {item['name']: item for item in publications}
    if set(artifacts) != set(WEIGHTED_ARTIFACTS):
        raise ValueError('Missing or duplicate weighted publication artifact: '+row['id'])
    for name, item in artifacts.items():
        original = record['artifacts'][name]
        if (item.get('path') != directory+'/'+name or item.get('independent_sha256_readback') is not True or
                type(item.get('bytes')) is not int or item['bytes'] <= 0 or
                not isinstance(item.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', item['sha256']) or
                item['bytes'] != original['bytes'] or item['sha256'] != original['sha256']):
            raise ValueError('Invalid immutable weighted artifact metadata: '+row['id']+'/'+name)
    for name, key in [('gen.root', 'input_sha256'), ('proposal_ledger.json', 'ledger_sha256'),
                      ('resolved_gen_cfg.py', 'config_sha256')]:
        if semantic[key] != artifacts[name]['sha256']:
            raise ValueError('Weighted semantic digest differs from publication: '+row['id']+'/'+name)
    return dict(trials=trials, events=count, identities=identities, artifacts=artifacts, sumw=sumw, sumw2=sumw2)


def readback_weighted(record, manager, manifest, row, gate_sha, temporary):
    """Fresh EOS bytes, then the same fail-closed EDM audit used by the worker."""
    from audit_shift_weighted_gen import audit as semantic_audit
    checked = weighted_receipt(record, manager, manifest, row, gate_sha)
    directory = Path(temporary)/row['id']
    directory.mkdir()
    for name, item in checked['artifacts'].items():
        path = directory/name
        run(['xrdcp', '--silent', 'root://eosuser.cern.ch/'+item['path'], str(path)])
        if path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
            raise ValueError('Published weighted artifact changed: '+item['path'])
    published_audit = json.loads((directory/'semantic_audit.json').read_text())
    if published_audit != record['semantic_audit']:
        raise ValueError('Published semantic report differs from weighted receipt: '+row['id'])
    fresh = semantic_audit(directory/'gen.root', directory/'proposal_ledger.json', directory/'resolved_gen_cfg.py')
    if fresh != published_audit:
        raise ValueError('Fresh weighted EDM audit differs from publication: '+row['id'])
    return dict(manager=manager['name'], stratum=row['stratum'], node_id=row['id'],
                chunk=row['chunk_index'], events=fresh['events'], fixed_trials=fresh['tried'],
                sha256=checked['artifacts']['gen.root']['sha256'],
                ledger_sha256=checked['artifacts']['proposal_ledger.json']['sha256'],
                config_sha256=checked['artifacts']['resolved_gen_cfg.py']['sha256'],
                auditor_sha256=fresh['auditor_sha256'], healthy=True)


def audit_weighted_manager(manager, temporary, all_ids):
    manifest_path = Path(manager['manifest'])
    if digest(manifest_path) != manager['manifest_sha256']:
        raise ValueError('Weighted scientific manifest changed: '+manager['name'])
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema') != 'shift-weighted-high-bin-manager-v2':
        raise ValueError('Unsupported weighted manager manifest')
    base, results = Path(manifest['eos_base']), manifest_path.parent/'results'
    if not base.is_dir() or not results.is_dir():
        raise OSError('Weighted EOS/receipt namespace unavailable: '+str(base))
    rows = {row['id']: row for row in manifest['jobs']}
    if len(rows) != len(manifest['jobs']):
        raise ValueError('Duplicate weighted manifest job ID')
    gate_path = manifest_path.parent/'scientific_gate.json'
    gate, gate_sha = None, None
    if gate_path.exists():
        gate = json.loads(gate_path.read_text())
        if (gate.get('schema') != 'shift-weighted-high-bin-scientific-gate-v2' or
                gate.get('manifest_sha256') != manager['manifest_sha256'] or gate.get('pass') is not True):
            raise ValueError('Weighted scientific gate failed or differs from manifest')
        gate_sha = digest(gate_path)
    counts, events, trials = 0, 0, 0
    identities, per_stratum, newest = set(), {}, {}
    for path in sorted(results.glob('*.json')):
        local = json.loads(path.read_text())
        row = rows.get(path.stem)
        if row is None or local.get('node_id') != row['id'] or local.get('manifest_sha256') != manager['manifest_sha256']:
            raise ValueError('Unexpected weighted terminal receipt: '+str(path))
        if local.get('complete') is not True:
            raise ValueError('Weighted worker failed: '+row['id']+' '+str(local.get('error', 'incomplete receipt')))
        if row['stage'] != 'production':
            continue  # Independent pilots/closure jobs never enter production totals.
        if gate is None:
            raise OSError('Weighted production receipt has no accessible scientific gate')
        allocation = gate['production_plan'][row['stratum']]
        if (type(allocation['chunks']) is not int or not 0 <= row['chunk_index'] < allocation['chunks'] or
                allocation['trials'] != allocation['chunks']*manifest['chunk_trials']):
            raise ValueError('Weighted production receipt lies outside the independent fixed budget')
        checked_local = weighted_receipt(local, manager, manifest, row, gate_sha)
        # The terminal transferred receipt discovers a finished publication;
        # count only its immutable EOS marker, written before job termination.
        publication_path = base/row['id']/'publication_receipt.json'
        published = json.loads(publication_path.read_text())
        checked = weighted_receipt(published, manager, manifest, row, gate_sha)
        if (checked_local != checked or local['ledger'] != published['ledger'] or
                local['semantic_audit'] != published['semantic_audit']):
            raise ValueError('Transferred weighted receipt differs from immutable EOS publication')
        if checked['identities'] & (all_ids | identities):
            raise ValueError('Duplicate production event identity across low/high managers')
        identities.update(checked['identities'])
        counts += 1; events += checked['events']; trials += checked['trials']
        subtotal = per_stratum.setdefault(row['stratum'], dict(chunks=0, events=0, fixed_trials=0))
        subtotal['chunks'] += 1; subtotal['events'] += checked['events']; subtotal['fixed_trials'] += checked['trials']
        if row['stratum'] not in newest or row['chunk_index'] > newest[row['stratum']][0]['chunk_index']:
            newest[row['stratum']] = (row, published)
    representatives = [readback_weighted(record, manager, manifest, row, gate_sha, temporary)
                       for row, record in newest.values()]
    return dict(stage='weighted_gen', completed_chunks=counts, completed_events=events,
                completed_fixed_trials=trials, pilots_excluded=True, strata=per_stratum), representatives, identities


def audit(request):
    result = dict(schema='shift-capacity-health-v1', nonce=request['nonce'],
                  registry_sha256=request['registry_sha256'],
                  checked_at_epoch=time.time(), healthy=False, completed_chunks=0,
                  completed_events=0, completed_fixed_trials=0, managers={}, manager_failures={},
                  failed_receipts=[], representatives=[])
    from shift_condor_native import query_account
    jobs = query_account('jniedzie', '(ShiftProductionSuite == true || ShiftProductionController == true)',
        attributes=['ClusterId', 'ProcId', 'JobStatus', 'JobUniverse', 'Cmd', 'Arguments',
                    'ShiftSuiteTag', 'ShiftProductionSuite', 'ShiftProductionController', 'DAGManJobId'], timeout=30,
        pool=request['collector_pool'])
    result['account_queue'] = dict(verified=True, checked_at_epoch=time.time(), ads=jobs)
    text = run(['eos', '-b', 'root://eoshome-j.cern.ch', 'quota', 'ls', '-m', '/eos/user/j/jniedzie'])
    quota_rows = [dict(re.findall(r'(\w+)=([^\s]+)', line)) for line in text.splitlines()]
    row = next(r for r in quota_rows if r.get('space') == '/eos/user/j/jniedzie/')
    result['quota'] = dict(free_bytes=int(row['maxlogicalbytes'])-int(row['usedlogicalbytes']),
                           free_files=int(row['maxfiles'])-int(row['usedfiles']))
    all_ids = set()
    import ROOT
    from DataFormats.FWLite import Events, Handle
    with tempfile.TemporaryDirectory(prefix='shift_health_readback_') as temporary:
        # A confirmed high-bin problem cannot contaminate valid low receipts.
        for manager in sorted(request['managers'], key=lambda m: m.get('audit_contract') != 'frozen_gen_receipts'):
            if manager.get('audit_contract') == WEIGHTED_CONTRACT:
                try:
                    summary, representatives, identities = audit_weighted_manager(manager, temporary, all_ids)
                except (ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
                    result['manager_failures'][manager['name']] = dict(verified=True, error=str(error),
                        error_type=type(error).__name__, contract=WEIGHTED_CONTRACT)
                    result['managers'][manager['name']] = dict(stage='weighted_gen', healthy=False,
                        completed_chunks=0, completed_events=0, completed_fixed_trials=0, pilots_excluded=True)
                else:
                    result['managers'][manager['name']] = dict(summary, healthy=True)
                    result['representatives'].extend(representatives)
                    all_ids.update(identities)
                    result['completed_chunks'] += summary['completed_chunks']
                    result['completed_events'] += summary['completed_events']
                    result['completed_fixed_trials'] += summary['completed_fixed_trials']
                continue
            if manager.get('audit_contract') != 'frozen_gen_receipts':
                # Probe managers own their scientific gates; they do not
                # contribute production counts until a supported contract is registered.
                result['managers'][manager['name']] = dict(stage='probe', completed_chunks=0)
                continue
            manifest_path = Path(manager['manifest'])
            if digest(manifest_path) != manager['manifest_sha256']:
                raise ValueError('Scientific manifest changed: '+manager['name'])
            manifest = json.loads(manifest_path.read_text())
            base = Path(manifest['receipt_base'])/'receipts'
            if not base.is_dir():
                raise OSError('EOS receipt namespace unavailable: '+str(base))
            expected = {s['id']: s for s in manifest['plan']['strata']}
            counts, events = 0, 0
            per_stratum = {}
            for label, stratum in expected.items():
                directory = base/label
                if not directory.is_dir():
                    raise OSError('Missing receipt stratum namespace: '+str(directory))
                failures = [str(x) for x in directory.glob('*.failure.json')]
                result['failed_receipts'].extend(failures)
                records = []
                seen_chunks = set()
                for path in sorted(directory.glob('part*.json')):
                    if '.failure.' in path.name:
                        continue
                    record = json.loads(path.read_text())
                    if (record.get('complete') is not True or
                            record['manifest_sha256'] != manager['manifest_sha256'] or
                            record['stratum'] != label or record['pilot'] is not False or
                            not 0 <= record['chunk'] < stratum['jobs']):
                        raise ValueError('Invalid completion receipt: '+str(path))
                    if (path.name != f"part{record['chunk']:05d}.json" or record['chunk'] in seen_chunks
                            or not any(a['path'].endswith('.root') for a in record['artifacts'])):
                        raise ValueError('Invalid receipt chunk or artifacts: '+str(path))
                    seen_chunks.add(record['chunk'])
                    count = record['events']
                    if not 0 < count <= record['requested_events'] or count != len(record['event_ids']):
                        raise ValueError('Invalid event count: '+str(path))
                    expected_request = min(stratum['events_per_job'], stratum['target_events']-record['chunk']*stratum['events_per_job'])
                    if record['requested_events'] != expected_request:
                        raise ValueError('Requested count changed: '+str(path))
                    for identity in record['event_ids']:
                        if len(identity) != 3 or any(type(x) is not int or x < 0 for x in identity):
                            raise ValueError('Malformed event identity: '+str(path))
                        key = tuple(identity)
                        if key in all_ids:
                            raise ValueError('Duplicate production event identity')
                        all_ids.add(key)
                    counts += 1
                    events += count
                    records.append(record)
                if not records:
                    continue
                per_stratum[label] = dict(chunks=len(records), events=sum(r['events'] for r in records))
                # A completed worker has already hashed all artifacts. Reopen
                # the newest published ROOT chunk in each bin independently.
                selected = max(records, key=lambda r: r['chunk'])
                artifact = next(a for a in selected['artifacts'] if a['path'].endswith('.root'))
                local = Path(temporary)/'gen.root'
                run(['xrdcp', '--silent', '--force', 'root://eosuser.cern.ch/'+artifact['path'], str(local)])
                if local.stat().st_size != artifact['bytes'] or digest(local) != artifact['sha256']:
                    raise ValueError('Published ROOT changed: '+artifact['path'])
                root = ROOT.TFile.Open(str(local))
                if not root or root.IsZombie() or root.TestBit(ROOT.TFile.kRecovered):
                    raise ValueError('Invalid ROOT: '+artifact['path'])
                tree = root.Get('Events')
                if not tree or int(tree.GetEntries()) != selected['events']:
                    raise ValueError('ROOT event count differs from receipt')
                root.Close()
                identities = []
                for event in Events(str(local)):
                    auxiliary = event.eventAuxiliary()
                    identities.append([int(auxiliary.run()), int(auxiliary.luminosityBlock()), int(auxiliary.event())])
                    info = Handle('GenEventInfoProduct')
                    event.getByLabel('generator', info)
                    if not info.isValid() or float(info.product().weight()) != 1.:
                        raise ValueError('Unit-weight GEN contract failed')
                    particles = Handle('std::vector<reco::GenParticle>')
                    event.getByLabel('genParticles', particles)
                    if not particles.isValid() or not len(particles.product()):
                        raise ValueError('Missing generated particles')
                if identities != selected['event_ids']:
                    raise ValueError('ROOT event identities differ from receipt')
                result['representatives'].append(dict(manager=manager['name'], stratum=label,
                    chunk=selected['chunk'], events=selected['events'], sha256=artifact['sha256'], healthy=True))
            result['managers'][manager['name']] = dict(completed_chunks=counts, completed_events=events, strata=per_stratum)
            result['completed_chunks'] += counts
            result['completed_events'] += events
    result['healthy'] = not result['failed_receipts']
    result['checked_at_epoch'] = time.time()
    return result


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text())
    try:
        result = audit(request)
    except BaseException as error:
        result = dict(schema='shift-capacity-health-v1', nonce=request['nonce'],
            registry_sha256=request['registry_sha256'], checked_at_epoch=time.time(), healthy=False,
            integrity_failure=isinstance(error, ValueError), error=str(error))
    Path('health_result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)
    if not result['healthy']:
        sys.exit(1)
