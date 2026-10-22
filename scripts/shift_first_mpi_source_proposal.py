#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded native first-MPI source audit.
"""Audit the private upstream heavy-flavour proposal before GEN publication.

The original fixed-trial ledger keeps its native base-sampler identity. This
separate audit checks the additional incoming/group/channel path likelihood,
including unsuccessful candidates and intrinsic retries. Decays are untouched.
The ordinary profile saves every native accepted event without a decay or
muon filter; natural muon-parent completion happens after source placement.
The legacy cylinder profile remains a separate conditional population.
This audit does not qualify differential physics rates or detector acceptance.
"""
import argparse
import ast
import gzip
import hashlib
import json
import math
from pathlib import Path

BASE_ALGORITHM = 'independent-geometric-full-support-tail-channel-retry-rb-v4'
ALGORITHM = BASE_ALGORITHM+'+native-first-mpi-upstream-heavy-v1'
PROFILE = 'shift-qcd-mu-enriched-cylinder-v1'
SCHEMA = 'shift-native-first-mpi-source-proposal-v1'
HISTORY = 'hard_channel_history.jsonl'
AUDIT = 'source_proposal_audit.json'
MASK = (1 << 64)-1


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(actual, expected, label, absolute=1.e-300):
    require(math.isfinite(actual) and math.isfinite(expected)
            and math.isclose(actual, expected, rel_tol=3.e-11, abs_tol=absolute),
            label+' differs: '+repr(actual)+' != '+repr(expected))


def validate(manifest, row):
    """Validate an opt-in, natural-decay pilot; never admit production nodes."""
    proposal = manifest.get('source_proposal')
    if proposal is None:
        return None
    required = {'schema', 'algorithm', 'base_sampler_algorithm', 'mode', 'target',
                'component_seed', 'overlay_sha256', 'natural_decays', 'maximum_history_bytes'}
    require(set(proposal) == required, 'Missing or undeclared first-MPI proposal setting')
    require(proposal['schema'] == SCHEMA and proposal['algorithm'] == ALGORITHM
            and manifest['algorithm'] == ALGORITHM
            and proposal['base_sampler_algorithm'] == BASE_ALGORITHM,
            'Unsupported composite first-MPI source algorithm')
    require(row.get('stage') == 'pilot' and row['sample'] == 'qcd'
            and manifest.get('generator_profile') in ('ordinary', PROFILE),
            'First-MPI HF source requires a declared ordinary or cylinder QCD pilot')
    require(type(row['trials']) is int and 2 <= row['trials'] <= 2000,
            'Source pilot requires 2--2000 fixed source slots')
    require(proposal['mode'] in ('disabled', 'mixture')
            and proposal['target'] in ('charm', 'bottom', 'heavy')
            and proposal['natural_decays'] is True, 'Only native-decay HF pilots are supported')
    seed = proposal['component_seed']
    require(type(seed) is int and 0 <= seed <= MASK, 'Component seed must be an unsigned 64-bit integer')
    bound = proposal['maximum_history_bytes']
    require(type(bound) is int and 1024 <= bound <= 512*1024*1024,
            'Source history needs an explicit bound between 1 KiB and 512 MiB')
    overlay = proposal['overlay_sha256']
    require(isinstance(overlay, str) and len(overlay) == 64
            and set(overlay) <= set('0123456789abcdef')
            and overlay == manifest['inputs'].get('libShiftMpiTailProposal.so'),
            'First-MPI proposal must pin its actual frozen overlay')
    require('shift_first_mpi_source_proposal.py' in manifest['inputs'],
            'Freeze the source auditor with its worker')
    publication_bound = manifest.get('maximum_publication_bytes_per_worker')
    require(type(publication_bound) is int and publication_bound > 0,
            'Source pilot needs an explicit per-worker publication bound')
    return proposal


def generation_environment(environment, proposal):
    """Remove ambient source/decay switches, then set only declared GEN knobs."""
    result = {key: value for key, value in environment.items()
              if not key.startswith(('SHIFT_MPI_', 'SHIFT_HEAVY_'))}
    # The existing hook itself sets its tail lower bound in the GEN process.
    result['SHIFT_MPI_HARD_CHANNEL_AUDIT'] = HISTORY
    if proposal['mode'] == 'mixture':
        result.update(SHIFT_MPI_BIAS_FIRST_HEAVY='1', SHIFT_MPI_UPSTREAM_HEAVY='1',
                      SHIFT_MPI_UPSTREAM_TARGET=proposal['target'],
                      SHIFT_MPI_UPSTREAM_COIN_SEED=str(proposal['component_seed']))
    return result


def coin_flags(seed, count):
    """Replay std::mt19937_64 and libstdc++'s one-draw uniform<double> coin."""
    state = [seed]
    for index in range(1, 312):
        last = state[-1]
        state.append((6364136223846793005*(last ^ (last >> 62))+index) & MASK)
    position = 312
    for _ in range(count):
        if position == 312:
            for index in range(312):
                value = (state[index] & 0xffffffff80000000) | (state[(index+1) % 312] & 0x7fffffff)
                state[index] = state[(index+156) % 312] ^ (value >> 1)
                if value & 1:
                    state[index] ^= 0xb5026f5aa96619e9
            position = 0
        value = state[position]
        position += 1
        value ^= (value >> 29) & 0x5555555555555555
        value ^= (value << 17) & 0x71d67fffeda60000
        value ^= (value << 37) & 0xfff7eee000000000
        value ^= value >> 43
        # libstdc++ converts the integer to double before division by 2**64.
        yield min(float(value)/float(1 << 64), math.nextafter(1., 0.)) >= .1


def crossing_probabilities(values, total, final_rounding=False):
    """Use native first-crossing intervals, preserving signed raw cache/PDF terms."""
    require(math.isfinite(total) and total > 0 and all(math.isfinite(x) for x in values),
            'Invalid native first-crossing distribution')
    cumulative, highest, result = 0., 0., []
    for value in values:
        cumulative += value
        boundary = min(1., max(0., cumulative/total))
        result.append(max(0., boundary-highest))
        highest = max(highest, boundary)
    if final_rounding:
        result[-1] += 1.-highest
    return result


def sequential_sum(values):
    """Use native double-addition order, independent of Python's sum version."""
    result = 0.
    for value in values:
        result += value
    return result


def incoming_ratio(row):
    entries = row['PDF_entries']
    ids = [entry[0] for entry in entries]
    require(ids and ids == list(range(min(ids), max(ids)+1)) and 0 in ids,
            'Incomplete native incoming PDF cache')
    probabilities = []
    for side in (0, 1):
        raw = [entry[side+1] for entry in entries]
        total = row['PDF_sums'][side]
        close(sequential_sum(raw), total, 'Incoming PDF sum', 1.e-300)
        values = crossing_probabilities(raw, total, True)
        for entry, value in zip(entries, values):
            close(value, entry[side+3], 'Native incoming interval')
        close(math.fsum(values), 1., 'Incoming normalization')
        probabilities.append(values)
    pa, pb = probabilities
    eligible = math.fsum(pa[i]*pb[j] for i, a in enumerate(ids) for j, b in enumerate(ids)
                         if a == b == 0 or a != 0 and a == -b)
    close(eligible, row['eligible_probability'], 'Eligible incoming probability')
    a, b = (0 if row[key] == 21 else row[key] for key in ('id1', 'id2'))
    require(a in ids and b in ids, 'Selected incoming flavour absent from cache')
    p = pa[ids.index(a)]*pb[ids.index(b)]
    q = .1*p+(.9*p/eligible if a == b == 0 or a != 0 and a == -b else 0.) if eligible else p
    close(p, row['native_probability'], 'Selected incoming probability')
    close(q, row['biased_probability'], 'Proposed incoming probability')
    require(p > 0 and q > 0, 'Selected incoming flavour has zero support')
    return q/p


def channel_ratio(row, target, enabled):
    cache = row['cache']
    require(cache and all(len(entry) == 7 for entry in cache), 'Malformed native channel cache')
    by_side = [[entry for entry in cache if entry[0] == side] for side in (0, 1)]
    require(sum(map(len, by_side)) == len(cache) and all(entries for entries in by_side),
            'Incomplete native t/u channel cache')
    require(all([entry[1] for entry in entries] == list(range(len(entries))) for entries in by_side),
            'Repeated or missing native channel index')
    totals = row.get('native_side_sums')
    require(isinstance(totals, list) and len(totals) == 2
            and all(type(value) in (int, float) and math.isfinite(value) for value in totals),
            'Native t/u side sums must be explicitly recorded')
    for entries, total in zip(by_side, totals):
        close(sequential_sum(entry[5] for entry in entries), total,
              'Raw native channel side sum', 1.e-300)
    total = totals[0]+totals[1]
    require(math.isfinite(total) and total > 0, 'Nonpositive native channel cache')
    close(total, row['cache_total'], 'Native channel cache sum', 1.e-300)
    probability_u = min(1., max(0., totals[1]/total))
    probabilities = {}
    for side, entries in enumerate(by_side):
        values = crossing_probabilities([entry[5] for entry in entries], totals[side]) if totals[side] > 0 else [0.]*len(entries)
        for entry, value in zip(entries, values):
            probability = (probability_u if side else 1.-probability_u)*value
            probabilities[(side, entry[1])] = probability
            close(probability, entry[6], 'Native channel interval')
    close(math.fsum(probabilities.values()), 1., 'Native channel normalization')
    codes = {'charm': {121, 122}, 'bottom': {123, 124}, 'heavy': {121, 122, 123, 124}}[target]
    heavy = math.fsum(probabilities[tuple(entry[:2])] for entry in cache if entry[2] in codes)
    close(heavy, row['native_heavy_probability'], 'Native heavy-channel probability')
    close(math.fsum(entry[5] for entry in cache if entry[2] in codes), row['heavy_cache_total'],
          'Raw signed heavy-channel sum', 1.e-300)
    key = (row['selected_side'], row['selected_index'])
    selected = next((entry for entry in cache if tuple(entry[:2]) == key), None)
    require(selected is not None and selected[2] == row['selected_code']
            and selected[3:5] == row['selected_mass_ids'], 'Selected native channel differs from cache')
    p = probabilities[key]
    applied = enabled and heavy > 0
    q = .1*p+(.9*p/heavy if selected[2] in codes else 0.) if applied else p
    require(row['bias_enabled'] is enabled and row['proposal_applied'] is applied,
            'Channel proposal mode differs from frozen policy')
    require(row['has_negative_raw_cache_terms'] == any(entry[5] < 0 for entry in cache),
            'Signed native-channel declaration differs')
    close(p, row['native_probability'], 'Selected native channel probability')
    close(q, row['proposal_probability'], 'Proposed channel probability')
    require(p > 0 and q > 0, 'Selected native channel has zero support')
    close(row['f_over_q'], 1., 'Marginalized channel factor')
    ratio = q/p
    close(row['upstream_biased_density_ratio'], ratio, 'Upstream channel ratio')
    return ratio


def audit_history(rows, ledger, semantic, proposal, prefilter, *, generator_profile=PROFILE):
    """Replay every source likelihood; GEN snapshot audit remains independent."""
    require(ledger['initializations'] == 1, 'Source coin audit requires one native initialization')
    require(generator_profile in ('ordinary', PROFILE), 'Unsupported source population profile')
    enabled = proposal['mode'] == 'mixture'
    logs, begins, incoming, groups, selects, zeros, outcomes = {}, {}, {}, {}, {}, {}, {}
    completed, call_slots, slot_weights, slots_last, flags = {}, {}, {}, {}, []
    handled_failures = 0
    for row in rows:
        kind, call = row['type'], row['first_call']
        require(type(call) is int and call > 0, 'Invalid first-scatter call identity')
        require(call == len(completed)+1, 'Source first-scatter calls are out of order')
        require(call not in completed, 'Source history follows a completed first-scatter call')
        logs.setdefault(call, 0.)
        slot = row['slot']
        require(type(slot) is int and 1 <= slot <= ledger['tried'], 'Source history has invalid framework slot')
        if call not in call_slots:
            previous_slot = call_slots.get(call-1, 1)
            require(slot in (previous_slot, previous_slot+1) and (call != 1 or slot == 1),
                    'Source calls skip or reverse framework slots')
            call_slots[call] = slot
        require(call_slots[call] == slot, 'First-scatter call crosses framework slots')
        candidate = row.get('candidate')
        if enabled and kind not in ('candidate_begin', 'first_complete'):
            require(begins.get(candidate) == (call, slot), 'Candidate crosses source call or slot')
        if kind == 'candidate_begin':
            require(enabled and type(candidate) is int and candidate > 0 and candidate not in begins,
                    'Repeated or unexpected candidate beginning')
            begins[candidate] = (call, slot)
            close(logs[call], row['log_path_ratio'], 'Initial candidate path ratio', 2.e-13)
        elif kind == 'candidate_zero':
            if enabled:
                require(begins.get(candidate) == (call, slot) and candidate not in zeros
                        and candidate not in incoming, 'Invalid native zero-candidate history')
                zeros[candidate] = row
                close(logs[call], row['log_path_ratio'], 'Zero-candidate path ratio', 2.e-13)
        elif kind == 'incoming':
            require(enabled and begins.get(candidate) == (call, slot) and candidate not in incoming,
                    'Repeated or unbound incoming draw')
            incoming[candidate] = row
            logs[call] += math.log(incoming_ratio(row))
            close(logs[call], row['log_path_ratio'], 'Incoming cumulative path ratio', 2.e-13)
        elif kind == 'cache_group':
            require(enabled and candidate in incoming and candidate not in groups,
                    'Repeated or unbound channel-group draw')
            groups[candidate] = row
            p, q = (.2, .92) if row['picked_other'] else (.8, .08)
            close(p, row['native_probability'], 'Native channel-group probability')
            close(q, row['biased_probability'], 'Proposed channel-group probability')
            logs[call] += math.log(q/p)
            close(logs[call], row['log_path_ratio'], 'Group cumulative path ratio', 2.e-13)
        elif kind == 'select':
            if enabled:
                require(candidate in groups and candidate not in selects
                        and groups[candidate]['picked_other'] == row['picked_other'], 'Invalid channel selection history')
            ratio = channel_ratio(row, proposal['target'] if enabled else 'heavy', enabled)
            selects[candidate] = (row, ratio)
        elif kind == 'outcome':
            require(math.isfinite(row['WT']) and not (row['kinematics_succeeded'] and not row['kinematics_attempted']),
                    'Invalid native candidate outcome')
            if enabled:
                require(begins.get(candidate) == (call, slot) and candidate not in outcomes,
                        'Repeated or unbound candidate outcome')
                require(candidate in zeros or candidate in selects, 'Candidate has neither zero nor selected channel')
                if candidate in zeros:
                    require(row['WT'] == 0 and not row['kinematics_attempted'], 'Zero candidate ran kinematics')
                outcomes[candidate] = row
            ratio = selects[candidate][1] if row['kinematics_attempted'] else 1.
            if row['kinematics_attempted']:
                require(row['WT_kept'], 'Rejected candidate ran kinematics')
                logs[call] += math.log(ratio)
                handled_failures += not row['kinematics_succeeded']
            close(row['channel_factor_in_path'], 1., 'No separate channel correction')
            close(row['upstream_channel_ratio_in_path'], ratio, 'Outcome channel path factor')
            close(logs[call], row['upstream_log_path_ratio'], 'Outcome cumulative path ratio', 2.e-13)
        elif kind == 'first_complete':
            require(type(row['normalizer_geometric_count']) is int and row['normalizer_geometric_count'] > 0,
                    'Missing original geometric normalizer')
            completed[call] = row
            require(row['upstream_enabled'] is (enabled and row['component'] == 'tail'),
                    'Frozen upstream mode differs from actual source')
            if row['component'] == 'original_below_L':
                expected = 10. if row['raw_first_pT_GeV'] < ledger['lower'] else 0.
                require(not row['upstream_biased_component'], 'Ordinary component used upstream bias')
            else:
                require(row['component'] == 'tail', 'Unknown source component')
                correction = 1.
                if enabled:
                    require(row['upstream_component_coin_seed'] == proposal['component_seed']
                            and row['upstream_target_quark'] == {'charm': 4, 'bottom': 5, 'heavy': 0}[proposal['target']],
                            'Source component seed or heavy target changed')
                    close(logs[call], row['upstream_log_path_ratio'], 'Completed upstream path ratio', 2.e-13)
                    maximum = max(math.log(.1), math.log(.9)+logs[call])
                    correction = math.exp(-maximum)/(math.exp(math.log(.1)-maximum)
                                                       + math.exp(math.log(.9)+logs[call]-maximum))
                    flags.append(row['upstream_biased_component'])
                else:
                    require(not row['upstream_biased_component'], 'Disabled source used biased component')
                close(row['upstream_correction'], correction, 'Whole-path mixture correction')
                close(row['failed_kinematics_channel_product'], 1., 'No independent failed-channel factor')
                close(row['final_channel_factor'], 1., 'No independent final-channel factor')
                successful = [outcome for outcome in outcomes.values()
                              if outcome['first_call'] == call and outcome['kinematics_succeeded']] if enabled else []
                if enabled:
                    require(len(successful) == int(row['accepted_tail_candidate']), 'Tail success history differs')
                if row['accepted_tail_candidate']:
                    expected = row['normalizer_geometric_count']*row['tail_occurrence_probability']*row['thinning_factor']*correction/.9
                    if enabled:
                        selected = selects[successful[0]['candidate']][0]
                        require(selected['selected_code'] == row['selected_code'], 'Final hard-channel identity changed')
                        if successful[0]['rao_blackwell_thinning']:
                            close(row['thinning_factor'], min(1., successful[0]['WT']), 'Rao-Blackwell thinning')
                else:
                    expected = 0.
            close(row['first_call_weight'], expected, 'First-scatter weight', 1.e-300)
            expected *= slot_weights.get(slot, 1.)
            close(row['full_intrinsic_retry_path_weight'], expected, 'Full intrinsic retry-path weight', 1.e-300)
            slot_weights[slot], slots_last[slot] = expected, row
        else:
            raise ValueError('Unknown source-history record: '+str(kind))
    require(sorted(completed) == list(range(1, ledger['proposal_calls']+1))
            and sorted(slot_weights) == list(range(1, ledger['tried']+1)), 'Source history misses calls or full framework slots')
    if enabled:
        require(set(begins) == set(outcomes) and set(begins) == set(zeros) | set(selects),
                'Source history misses rejected or zero candidates')
        require(flags == list(coin_flags(proposal['component_seed'], len(flags))),
                'Recorded upstream coin sequence differs from its independent seed')
    require(sum(row['normalizer_geometric_count'] for row in completed.values()) == ledger['normalizer_trials'],
            'Source geometric normalizer total differs from native ledger')
    require(handled_failures == ledger['handled_kinematic_failures'], 'Source kinematic failure count differs')
    require(prefilter['events'] == ledger['accepted']
            and len(prefilter['event_ids']) == len(prefilter['weights']) == ledger['accepted'],
            'Prefilter original count differs from full native acceptance')
    accepted = {}
    for identity, weight in zip(prefilter['event_ids'], prefilter['weights']):
        key = tuple(identity)
        require(key not in accepted and identity[:2] == [ledger['run'], 1]
                and identity[2] in slot_weights, 'Invalid or repeated prefilter GEN identity')
        close(weight, slot_weights[identity[2]], 'Actual prefilter GEN source weight', 1.e-300)
        accepted[key] = weight
    close(math.fsum(accepted.values()), ledger['sumw'], 'Full prefilter accepted source sumw', 1.e-300)
    close(math.fsum(weight*weight for weight in accepted.values()), ledger['sumw2'],
          'Full prefilter accepted source sumw2', 1.e-300)
    identities, counts, sums = set(), {}, {}
    require(len(semantic['event_ids']) == len(semantic['weights']), 'GEN semantic arrays differ')
    for identity, weight in zip(semantic['event_ids'], semantic['weights']):
        key = tuple(identity)
        require(key not in identities and key in accepted, 'Saved GEN is not an exact prefilter subset')
        identities.add(key)
        close(weight, slot_weights[identity[2]], 'Actual saved GEN source weight', 1.e-300)
        code = str(slots_last[identity[2]]['selected_code'])
        counts[code] = counts.get(code, 0)+1
        sums[code] = sums.get(code, 0.)+weight
    if generator_profile == 'ordinary':
        require(identities == set(accepted), 'Ordinary source must save every native accepted event without a muon filter')
    return dict(complete=True, passed=True, full_source_trials=ledger['tried'],
                first_scatter_calls=len(completed), upstream_component_draws=len(flags),
                all_intrinsic_retry_paths_checked=True, source_only_native_weight_checked=True,
                prefilter_accepted_events=len(accepted), saved_events=len(identities),
                saved_events_by_last_first_hard_code=counts,
                saved_weight_by_last_first_hard_code=sums, natural_decays=True,
                physics_valid=False, normalization_ready=False)


def validate_uncensored_config(source, ledger):
    """Bind the ordinary Pythia model and reject any extra GEN event filter."""
    from audit_shift_weighted_gen import validate_config, _keyword, _strings
    validate_config(source, ledger)
    modules = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'process':
                    modules[target.attr] = node.value
    require(all(name in modules for name in ('ProductionFilterSequence','generation_step','FEVTDEBUGoutput')),
            'Ordinary source is missing its resolved generation or output path')
    require(ast.unparse(modules['ProductionFilterSequence']) == 'cms.Sequence(process.generator)',
            'Ordinary source production sequence contains an additional filter')
    require(ast.unparse(modules['generation_step']) == 'cms.Path(process.ProductionFilterSequence + process.pgen)',
            'Ordinary source generation path contains an additional filter')
    output_selection = _keyword(_keyword(modules['FEVTDEBUGoutput'], 'SelectEvents'), 'SelectEvents')
    require(_strings(output_selection) == ['generation_step'], 'Ordinary source output uses a filtered path')
    for name, module in modules.items():
        if not isinstance(module, ast.Call) or not isinstance(module.func, ast.Attribute) or module.func.attr != 'EDFilter' or name == 'generator':
            continue
        # cmsDriver's summary filter counts the native generation decision;
        # it is not a separate particle or decay selection.
        require(name == 'genstepfilter' and ast.literal_eval(module.args[0]) == 'TriggerResultsFilter'
                and _strings(_keyword(module, 'triggerConditions')) == ['generation_step'],
                'Ordinary source contains an undeclared event or decay filter')


def audit_files(manifest, row, history=HISTORY, output=AUDIT):
    proposal = validate(manifest, row)
    require(proposal is not None, 'No declared first-MPI source proposal')
    path = Path(history)
    require(path.stat().st_size <= proposal['maximum_history_bytes'], 'Source history exceeds its frozen storage bound')
    ledger = json.loads(Path('proposal_ledger.json').read_text())
    semantic = json.loads(Path('semantic_audit.json').read_text())
    require(ledger['requested_trials'] == row['trials'] and ledger['run'] == row['run']
            and [ledger['lower'], ledger['upper']] == row['bounds'] and ledger['sample'] == 'qcd',
            'Native source ledger differs from frozen pilot request')
    require(digest('libShiftMpiTailProposal.so') == proposal['overlay_sha256']
            and digest(__file__) == manifest['inputs']['shift_first_mpi_source_proposal.py'],
            'Frozen first-MPI overlay or auditor changed')
    require(semantic['complete'] and semantic['healthy'] and semantic['ledger'] == ledger
            and semantic['algorithm'] == BASE_ALGORITHM and ledger['algorithm'] == BASE_ALGORITHM
            and semantic['generator_profile'] == manifest['generator_profile'], 'Missing native GEN audit or changed base sampler')
    require(semantic['input_sha256'] == digest('gen.root')
            and semantic['ledger_sha256'] == digest('proposal_ledger.json')
            and semantic['config_sha256'] == digest('resolved_gen_cfg.py'), 'Native GEN audit inputs changed')
    if manifest['generator_profile'] == 'ordinary':
        validate_uncensored_config(Path('resolved_gen_cfg.py').read_text(), ledger)
    from audit_shift_weighted_gen import read_edm, validate_snapshot
    prefilter_path = Path('prefilter.root')
    prefilter_before = digest(prefilter_path)
    records, embedded, weighted_run, native_run, beams = read_edm(prefilter_path, ledger['accepted'])
    prefilter = validate_snapshot(ledger, embedded, records, weighted_run)
    before = digest(path)
    def rows():
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rt') as stream:
            for line in stream:
                yield json.loads(line)
    result = audit_history(rows(), ledger, semantic, proposal, prefilter,
                           generator_profile=manifest['generator_profile'])
    require(digest(path) == before and digest(prefilter_path) == prefilter_before,
            'Source history or prefilter GEN changed during audit')
    result.update(schema='shift-native-first-mpi-source-audit-v1', algorithm=ALGORITHM,
                  base_sampler_algorithm=BASE_ALGORITHM, source_proposal=proposal,
                  history_sha256=before, history_bytes=path.stat().st_size,
                  prefilter_GEN_sha256=prefilter_before,
                  prefilter_GEN_bytes=prefilter_path.stat().st_size,
                  GEN_sha256=semantic['input_sha256'], ledger_sha256=semantic['ledger_sha256'],
                  semantic_audit_sha256=digest('semantic_audit.json'), auditor_sha256=digest(__file__))
    if manifest['generator_profile'] == 'ordinary':
        result.update(generator_profile='ordinary', decay_dependent_filter=False,
                      saved_events_equal_native_acceptance=True, requires_full_muon_completion=True)
    Path(output).write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--node', required=True)
    args = parser.parse_args()
    worklist = json.loads(args.manifest.read_text())
    request = next(row for row in worklist['jobs'] if row['id'] == args.node)
    report = audit_files(worklist, request)
    print(json.dumps(report, allow_nan=False))
