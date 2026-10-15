# Code style: AI-code. AI-generated explicit mu-enriched QCD source profile.
"""Restore the named SHIFT decay/filter prescription without changing old GEN.

The prefilter fixed-trial ledger remains the denominator. Saved event weights
already account for external filtering through zero rejected contributions;
never multiply their rate by the filter efficiency again.
"""
import ast
import math

PROFILE = 'shift-qcd-mu-enriched-cylinder-v1'
SETTINGS = (
    'ParticleDecays:limitTau0 = off',
    'ParticleDecays:limitCylinder = on',
    'ParticleDecays:xyMax = 8000.',
    'ParticleDecays:zMax = 151000.',
    '130:mayDecay = on',
    '211:mayDecay = on',
    '321:mayDecay = on',
)
FILTER = dict(ParticleID=[13, -13], Status=[1, 1], MinP=[0., 0.],
              MinPt=[0., 0.], MinEta=[-10., -10.], MaxEta=[0., 0.],
              MinDecayRadius=[0., 0.], MaxDecayRadius=[8000., 8000.],
              MinDecayZ=[0., 0.], MaxDecayZ=[151000., 151000.])


def configure(process):
    import FWCore.ParameterSet.Config as cms
    if process.generator.UserCustomization[0].eventClass.value() != 'qcd':
        raise ValueError('Muon-enriched profile is only defined for QCD')
    process.generator.PythiaParameters.processParameters.extend(SETTINGS)
    parameters = {name: (cms.untracked.vint32(values) if name in ('ParticleID', 'Status')
                         else cms.untracked.vdouble(values))
                  for name, values in FILTER.items()}
    process.mugenfilter = cms.EDFilter('MCSmartSingleParticleFilter', **parameters)
    process.generation_step += process.mugenfilter
    return process


def ordinary_source_config(source, ledger):
    """Check exactly the declared additions, then let the ordinary audit check CP5."""
    from audit_shift_weighted_gen import _keyword, _strings
    if ledger['sample'] != 'qcd':
        raise ValueError('Muon-enriched profile cannot change direct-Jpsi decays')
    tree = ast.parse(source)
    modules = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'process':
                    modules[target.attr] = node.value
    process_parameters = _keyword(_keyword(modules['generator'], 'PythiaParameters'), 'processParameters')
    if _strings(process_parameters)[-len(SETTINGS):] != list(SETTINGS):
        raise ValueError('Muon-enriched decay settings differ from the named profile')
    process_parameters.args = process_parameters.args[:-len(SETTINGS)]
    muon_filter = modules.get('mugenfilter')
    if not isinstance(muon_filter, ast.Call) or not muon_filter.args or ast.literal_eval(muon_filter.args[0]) != 'MCSmartSingleParticleFilter':
        raise ValueError('Muon-enriched profile needs its stable-muon filter')
    if {keyword.arg for keyword in muon_filter.keywords} != set(FILTER):
        raise ValueError('Muon filter has missing or undeclared settings')
    for name, expected in FILTER.items():
        parameter = _keyword(muon_filter, name)
        values = [ast.literal_eval(value) for value in parameter.args]
        if len(values) == 1 and isinstance(values[0], (list, tuple)):
            values = list(values[0])
        if values != expected:
            raise ValueError('Changed stable-muon filter setting: ' + name)
    path = ast.unparse(modules.get('generation_step'))
    if path != 'cms.Path(process.ProductionFilterSequence + process.pgen + process.mugenfilter)':
        raise ValueError('Stable-muon filter must follow the preserved GEN path')
    return ast.unparse(tree)


def filtered_snapshot(ledger, embedded, records, weighted_run, filters):
    """Validate the saved subset against full weighted before/after filter counters."""
    from audit_shift_weighted_gen import close, validate_ledger, validate_snapshot
    trials = validate_ledger(ledger)
    if ledger != embedded:
        raise ValueError('Named prefilter ledger differs from the JSON sidecar')
    if not filters or any(row['total_negative'] or row['pass_negative'] for row in filters):
        raise ValueError('Missing or negative-weight filter counters')
    total = sum(row['total_positive'] for row in filters)
    passed = sum(row['pass_positive'] for row in filters)
    if total != ledger['accepted'] or passed != len(records) or passed > total:
        raise ValueError('Filter counts differ from source accepted/saved counts')
    before = math.fsum(row['sum_weights'] for row in filters)
    before2 = math.fsum(row['sum_weights2'] for row in filters)
    after = math.fsum(row['sum_pass_weights'] for row in filters)
    after2 = math.fsum(row['sum_pass_weights2'] for row in filters)
    close(before, ledger['sumw'], 'Filter input weights')
    close(before2, ledger['sumw2'], 'Filter squared input weights')
    close(ledger['sigma_mb'], ledger['sigma_nd_mb']*before/trials,
          'Prefilter fixed-trial cross section')
    close(ledger['sigma_error_mb'], ledger['sigma_nd_mb']*math.sqrt(
        max(0., before2-before*before/trials)/(trials*(trials-1))),
        'Prefilter fixed-trial cross-section error')
    if not 0 <= after <= before or not 0 <= after2 <= before2:
        raise ValueError('Filtered positive weights exceed the full source')
    close(math.fsum(row['weight'] for row in records), after, 'Filtered saved weights')
    close(math.fsum(row['weight']**2 for row in records), after2, 'Filtered saved squared weights')
    # Retain all source slots as zero or saved contributions in the selected
    # estimator. Validate ordinary identities/ownership/weights using that
    # estimator, while separately checking the unchanged prefilter run product.
    selected = dict(ledger, accepted=passed, accumulated_accepted=passed, sumw=after, sumw2=after2)
    selected['status_counts'] = [passed] + [0]*7 + [trials-passed] + [0]*7
    selected['sigma_mb'] = ledger['sigma_nd_mb']*after/trials
    selected['sigma_error_mb'] = ledger['sigma_nd_mb']*math.sqrt(max(0., after2-after*after/trials)/(trials*(trials-1)))
    expected_prefilter_xsec = ledger['sigma_mb']*1e9
    close(weighted_run['internal_xsec_pb'], expected_prefilter_xsec, 'Prefilter named run cross section')
    close(weighted_run['error_pb'], ledger['sigma_error_mb']*1e9, 'Prefilter named run cross section error')
    close(weighted_run['filter_efficiency'], 1., 'Prefilter named run filter efficiency')
    selected_run = dict(internal_xsec_pb=selected['sigma_mb']*1e9,
                        error_pb=selected['sigma_error_mb']*1e9, filter_efficiency=1.)
    result = validate_snapshot(selected, selected, records, selected_run)
    result.update(source_accepted=total, raw_filter_efficiency=passed/total if total else 0.,
                  weighted_filter_efficiency=after/before if before else 0.,
                  full_source_trial_denominator=trials, no_second_filter_efficiency=True)
    return result


def receipt_estimator(record, expected_profile=PROFILE):
    """Check filtered publication arithmetic while retaining its source ledger."""
    from audit_shift_weighted_gen import close
    semantic, ledger = record['semantic_audit'], record['ledger']
    if ledger['sample']!='qcd':
        raise ValueError('Filtered QCD receipt cannot use a different process class')
    if (expected_profile != PROFILE or record.get('generator_profile') != PROFILE
            or semantic.get('generator_profile') != PROFILE):
        raise ValueError('Filtered receipt generator profile differs from its manifest')
    count = semantic['events']
    if (type(count) is not int or count < 0 or record.get('event_count') != count
            or any(not isinstance(semantic[name], list) or len(semantic[name]) != count
                   for name in ('event_ids', 'weights', 'scale', 'charged'))):
        raise ValueError('Filtered receipt saved-event arrays differ')
    records = [dict(event_id=identity, weight=weight, hepmc_weight=weight,
                    process=101, pthat=scale, direct_scales=[], charged=charged)
               for identity, weight, scale, charged in zip(
                   semantic['event_ids'], semantic['weights'], semantic['scale'], semantic['charged'])]
    checked = filtered_snapshot(ledger, semantic['ledger'], records,
                                semantic['weighted_run_info'], semantic['filter_records'])
    for name in ('sigma_weighted_mb', 'error_weighted_mb'):
        close(semantic[name], checked[name], 'Filtered receipt '+name)
    for name in ('source_accepted', 'full_source_trial_denominator', 'no_second_filter_efficiency'):
        if semantic.get(name) != checked[name]:
            raise ValueError('Filtered receipt exposure differs: '+name)
    return checked
