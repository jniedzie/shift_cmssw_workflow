#!/usr/bin/env python3
"""Inventory frozen GEN receipts and prepare disjoint detector-chain slices."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import struct


DEFAULT_TEMPLATE_DIRECTORY = Path(
    '/eos/user/j/jniedzie/shift_cmssw/jpsi/lssPaired_materialField_10k_2023_cms_v1/configs')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def slices(events, size):
    if events < 1 or size < 1:
        raise ValueError('Positive event and shard sizes required')
    return [(skip, min(size, events - skip)) for skip in range(0, events, size)]


def event_key(identity):
    """Store exact EDM run/lumi/event IDs compactly for full-inventory checks."""
    bounds = (1 << 32, 1 << 32, 1 << 64)
    if (not isinstance(identity, (list, tuple)) or len(identity) != 3 or
            any(type(value) is not int or not 0 <= value < bound
                for value, bound in zip(identity, bounds))):
        raise ValueError('Invalid source event identity')
    return struct.pack('>IIQ', *identity)


def descriptor(path, manifest_sha, output_base):
    record = json.loads(path.read_text())
    if not record.get('complete') or record['manifest_sha256'] != manifest_sha:
        raise ValueError('Incomplete or mismatched frozen receipt: ' + str(path))
    if record['schema'] == 'shift-dag-receipt-v1':
        ids = record['event_ids']
        artifacts = record['artifacts']
        stratum = record['stratum']
        expected = record['events']
        weights, normalization = None, None
    elif record['schema'] == 'shift-weighted-gen-receipt-v3':
        if not record.get('publication_complete') or record['request']['stage'] != 'production':
            raise ValueError('Weighted receipt is not a published production input')
        ids = record['semantic_audit']['event_ids']
        weights = record['semantic_audit']['weights']
        artifacts = record['publication']
        stratum = record['request']['stratum']
        expected = record['event_count']
        normalization = next(a for a in artifacts if a.get('name') == 'proposal_ledger.json')
    else:
        raise ValueError('Unsupported source receipt schema')
    if len(ids) != expected or len({event_key(identity) for identity in ids}) != expected or expected < 1:
        raise ValueError('Missing or duplicate source event identities')
    if weights is not None and len(weights) != expected:
        raise ValueError('Missing accepted-event weights')
    roots = [a for a in artifacts if a['path'].endswith('.root')]
    if len(roots) != 1:
        raise ValueError('Exactly one frozen GEN file required')
    gen = roots[0]
    if not Path(gen['path']).is_file() or Path(gen['path']).stat().st_size != gen['bytes']:
        raise ValueError('Missing or resized source GEN')
    result = {'receipt': str(path), 'receipt_sha256': digest(path), 'stratum': stratum,
              'gen': gen['path'], 'gen_sha256': gen['sha256'], 'gen_bytes': gen['bytes'],
              'event_ids': ids, 'output_base': output_base + '/' + stratum,
              'normalization_record': normalization}
    if weights is not None:
        result['weights'] = weights
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ordinary', type=Path, required=True)
    parser.add_argument('--weighted', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--eos-output', required=True)
    parser.add_argument('--events-per-job', type=int, default=50)
    parser.add_argument('--template-directory', type=Path, default=DEFAULT_TEMPLATE_DIRECTORY,
                        help='Archived config directory containing step1 through step4 subdirectories')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(exist_ok=False, parents=True)
    (out / 'sources').mkdir()
    (out / 'templates').mkdir()
    ordinary = json.loads(args.ordinary.read_text())
    weighted = json.loads(args.weighted.read_text())
    ordinary_sha, weighted_sha = digest(args.ordinary), digest(args.weighted)
    inputs = []
    for stratum in ordinary['plan']['strata']:
        for chunk in range(stratum['jobs']):
            inputs.append((Path(ordinary['receipt_base']) / 'receipts' / stratum['id'] / f'part{chunk:05d}.json', ordinary_sha))
    accounting_path = args.weighted.parent / 'production_complete.json'
    accounting = json.loads(accounting_path.read_text())
    if not accounting.get('complete') or accounting['manifest_sha256'] != weighted_sha:
        raise ValueError('Weighted production needs final frozen-manifest accounting')
    for node in weighted['jobs']:
        if node['stage'] == 'production' and node['chunk_index'] < accounting['strata'][node['stratum']]['chunks']:
            inputs.append((Path(weighted['eos_base']) / node['id'] / 'publication_receipt.json', weighted_sha))
    totals, counts, source_summary = {}, {}, []
    rows, job, canaries, seen = [], 0, [], set()
    seen_ids = set()
    def read(item):
        return descriptor(item[0], item[1], args.eos_output)
    with ThreadPoolExecutor(max_workers=8) as executor:
        for index, source in enumerate(executor.map(read, inputs)):
            if source['gen'] in seen:
                raise ValueError('Duplicate source GEN in inventory')
            seen.add(source['gen'])
            for identity in source['event_ids']:
                key = event_key(identity)
                if key in seen_ids:
                    raise ValueError('Duplicate event identity across source GEN files: ' + str(identity))
                seen_ids.add(key)
            (out / 'sources' / f'source{index:05d}.json').write_text(json.dumps(source) + '\n')
            stratum = source['stratum']
            n = len(source['event_ids'])
            if stratum not in totals:
                canaries.append({'source': index, 'skip': 0, 'count': min(2, n), 'job': 800000 + index, 'stratum': stratum})
            totals[stratum] = totals.get(stratum, 0) + n
            counts[stratum] = counts.get(stratum, 0) + 1
            source_summary.append({'index': index, 'gen': source['gen'], 'events': n, 'stratum': stratum})
            for skip, count in slices(n, args.events_per_job):
                rows.append(f'{index} {skip} {count} {job}\n')
                job += 1
    reference = args.template_directory.resolve()
    for stratum, summary in accounting['strata'].items():
        if totals.get(stratum) != summary['accepted'] or counts.get(stratum) != summary['chunks']:
            raise ValueError('Weighted inventory differs from complete campaign accounting')
    templates = {}
    for stage in range(1, 5):
        files = sorted((reference / f'step{stage}').glob('*part*0000*cfg.py'))
        if len(files) != 1:
            raise ValueError('Ambiguous archived production config')
        target = out / 'templates' / f'step{stage}.py'
        shutil.copy2(files[0], target)
        templates[str(stage)] = {'source': str(files[0]), 'sha256': digest(target)}
    (out / 'jobs.txt').write_text(''.join(rows))
    (out / 'canaries.json').write_text(json.dumps(canaries, indent=2) + '\n')
    manifest = {'schema': 'shift-gen-to-nano-plan-v1', 'stage': 'SIM,DIGI,HLT,RECO,NANO',
                'eos_output': args.eos_output, 'events_per_job': args.events_per_job,
                'sources': source_summary, 'jobs': job, 'events': sum(totals.values()), 'strata': totals,
                'source_chunks': counts, 'templates': templates,
                'ordinary_manifest': str(args.ordinary.resolve()), 'ordinary_manifest_sha256': digest(args.ordinary),
                'weighted_manifest': str(args.weighted.resolve()), 'weighted_manifest_sha256': digest(args.weighted),
                'physics_valid': False, 'normalization_ready': False,
                'preparation_basis': 'Exact published GEN receipt inventory and archived detector configurations; preparation does not establish campaign authorization or physics readiness.',
                'intermediate_policy': 'Keep published parent GEN unchanged; detector intermediates are local scratch; publish verified NanoAOD, config/log archive and exact event/weight receipt.',
                'canary_passed': False}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'sources': len(source_summary), 'jobs': job, 'events': manifest['events'], 'strata': totals}))


if __name__ == '__main__':
    main()
