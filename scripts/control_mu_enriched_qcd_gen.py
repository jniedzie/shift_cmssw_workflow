#!/usr/bin/env python3
# Code style: AI-code. AI-generated fixed-cohort ordinary GEN accounting gate.
"""Check complete ordinary chunks and preserve trial-pooled muon-filter rates."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from qcd_mu_enrichment import PROFILE


def cohort(manifest, expected, base, stage):
    grouped, identities = {}, set()
    for request in manifest['jobs']:
        if request['stage'] != stage:
            continue
        record = json.loads((base/'results'/(request['id']+'.json')).read_text())
        audit = record['semantic_audit']
        if (record.get('schema') != 'shift-mu-enriched-ordinary-gen-receipt-v1'
                or not record.get('complete') or not record.get('publication_complete')
                or type(record.get('exit_status')) is not int or record['exit_status'] != 0
                or record.get('manifest_sha256') != expected or record.get('request') != request
                or record.get('source_inputs') != manifest['inputs']
                or record.get('generator_profile') != PROFILE or audit.get('generator_profile') != PROFILE
                or audit.get('source_model_settings_sha256') != manifest['source_model_settings_sha256']
                or not audit.get('complete') or not audit.get('healthy')
                or audit.get('source_accepted') != request['trials']
                or audit.get('events') != record.get('event_count')
                or audit.get('no_second_filter_efficiency') is not True):
            raise ValueError('Invalid completed ordinary receipt: '+request['id'])
        if len(audit['event_ids']) != audit['events'] or len(audit['weights']) != audit['events']:
            raise ValueError('Saved-event arrays differ')
        for identity in audit['event_ids']:
            key = tuple(identity)
            if key in identities:
                raise ValueError('Duplicate ordinary original identity')
            identities.add(key)
        grouped.setdefault(request['stratum'], []).append(record)
    return grouped


def aggregate(records):
    audits = [row['semantic_audit'] for row in records]
    if (len({tuple(row['bounds']) for row in audits}) != 1
            or any(row['generator_profile'] != PROFILE for row in audits)
            or any(not math.isclose(row['sigma_nd_mb'], audits[0]['sigma_nd_mb'], rel_tol=1.e-9)
                   for row in audits)):
        raise ValueError('Ordinary chunks use different bins, profiles or ND source cross sections')
    tried = sum(row['full_source_trial_denominator'] for row in audits)
    source = sum(row['source_accepted'] for row in audits)
    saved = sum(row['events'] for row in audits)
    # Native ordinary sigma is the accepted-source estimate. Native source
    # sigma * Pythia tried / source accepted restores each chunk's ND exposure.
    exposure = math.fsum(row['native_generator_run_info']['internal_xsec_pb']*
                         row['full_source_trial_denominator'] for row in audits)
    event_weights = {record['request']['id']:
        audit['native_generator_run_info']['internal_xsec_pb']*
        audit['full_source_trial_denominator']/(tried*audit['source_accepted'])
        for record, audit in zip(records, audits)}
    selected = math.fsum(event_weights[record['request']['id']]*audit['events']
                         for record, audit in zip(records, audits))
    return dict(chunks=len(records), framework_source_events=source,
        full_source_trial_denominator=tried, saved_filtered_events=saved,
        selected_cross_section_pb=selected,
        selected_cross_section_poisson_error_pb=math.sqrt(math.fsum(
            event_weights[record['request']['id']]**2*audit['events']
            for record, audit in zip(records, audits))),
        source_cross_section_pb=exposure/tried,
        event_weight_pb_by_chunk=event_weights,
        raw_filter_efficiency=saved/source, no_second_filter_efficiency=True,
        normalization='pool full Pythia source trials; per-event pb weight=chunk native source sigma * chunk trials / (all trials * chunk source accepted)',
        physics_valid=False, normalization_ready=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('stage', choices=('canary', 'production'))
    args = parser.parse_args()
    base = Path(args.directory).resolve()
    manifest_path = base/'manifest.json'
    expected = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    manifest = json.loads(manifest_path.read_text())
    result = dict(schema='shift-mu-enriched-ordinary-accounting-v1', complete=False,
                  manifest_sha256=expected, generator_profile=PROFILE, failures=[])
    try:
        grouped = cohort(manifest, expected, base, args.stage)
        if len(grouped) != 5:
            raise ValueError('Expected all five ordinary low bins')
        result['strata'] = {key:aggregate(rows) for key, rows in grouped.items()}
        if args.stage == 'canary':
            estimates = {}
            for key, rows in grouped.items():
                source = sum(row['request']['trials'] for row in rows)
                seconds = sum(row['generation_seconds'] for row in rows)
                payload = sum(row['artifacts']['gen.root']['bytes'] for row in rows)
                chunks = [row for row in manifest['jobs'] if row['stage']=='production' and row['stratum']==key]
                chunk_sources = max(row['trials'] for row in chunks)
                projected_seconds = 1.3*seconds/source*chunk_sources
                projected_bytes = 1.3*payload/source*chunk_sources
                if projected_seconds > .8*3600 or projected_bytes*2+32000000 > manifest['peak_bytes_per_worker']:
                    raise ValueError('Ordinary chunk exceeds bounded time/storage: '+key)
                estimates[key] = dict(projected_generation_seconds=projected_seconds,
                                      projected_root_bytes=projected_bytes)
            result['production_chunk_estimates'] = estimates
        result.update(complete=True, stage=args.stage, pilots_excluded_from_production=True)
    except Exception as error:
        result['failures'].append(repr(error))
    path = base/('canary_gate.json' if args.stage == 'canary' else 'production_complete.json')
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, indent=2)+'\n')
    temporary.replace(path)
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
