#!/usr/bin/env python3
# Code style: AI-code. AI-generated full-census SM source preparation worker.
"""Generate one independent SM chunk, prepare jaw decays and freeze admission.

The ordinary GEN is uncensored. Muon-producing natural decays are completed
after jaw placement, before material ranking. Geant4 decides actual survival.
GEN exposure, source proposal weights and detector sampling stay separate.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from run_shift_gen_to_nano import command
from run_shift_weighted_gen import publish
from shift_jaw_source import checksum, source_position
from shift_production_selection import bind_source, prepare_default_selection


TOOLS = Path(__file__).resolve().parents[1]


def proposal_library(family):
    # The HF diagnostic library supports QCD only. J/psi uses its separately
    # qualified generic proposal; both corrections already live in native W.
    name = 'libShiftMpiTailProposalJpsi.so' if family == 'jpsi' else 'libShiftMpiTailProposal.so'
    return TOOLS/'plugin'/name


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            result.update(block)
    return result.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def run(argv, log, *, cwd=None, env=None, timeout=3600):
    with Path(log).open('w') as output:
        command(argv, cwd=cwd, env=env, timeout=timeout,
                stdout=output, stderr=subprocess.STDOUT)


def generate(request, directory):
    """Reuse the existing native generator settings and independent auditors."""
    row = request['source_request']
    family, low, high = row['family'], *row['bounds']
    count, seed, run_number = row['events_or_trials'], row['seed'], row['run']
    if family not in ('qcd', 'jpsi', 'dy') or not 1 <= count <= 1000:
        raise ValueError('Unsupported source family or chunk size')
    if not 30000000 <= run_number <= 4294967295:
        raise ValueError('Use the reserved new-production run namespace')
    directory.mkdir()
    scripts = TOOLS/'scripts'
    weighted = family != 'dy' and low > 0
    if family == 'dy':
        argv = ['cmsRun', str(scripts/'fixed_target_gen_cfg.py'), 'sample=dy',
                'lower='+str(low), 'upper='+str(high), 'maxEvents='+str(count),
                'seed='+str(seed), 'runNumber='+str(run_number), 'outputDir='+str(directory)]
        run(argv, directory/'cmsRun.log', cwd=directory, timeout=3600)
        run(['python3', str(scripts/'audit_fixed_target_gen.py'), str(directory)],
            directory/'audit.log', timeout=600)
        audit = json.loads((directory/'validation.json').read_text())
        config = directory/'resolved_cfg.py'
    else:
        prefix = 'QCD' if family == 'qcd' else 'Charmonium'
        name = prefix+'_SoftMpiPartition_FixedTarget_13p6TeV'
        fragment = TOOLS/'fragments'/(name+'_pythia8_cff.py')
        # cmsDriver imports a fragment through the worker-local SCRAM package.
        package = Path(os.environ['CMSSW_BASE'])/'src/Configuration/GenProduction/python'
        package.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fragment, package/fragment.name)
        run(['cmsDriver.py', 'Configuration/GenProduction/python/'+fragment.name,
             '--step', 'GEN', '--conditions', 'auto:phase1_2023_realistic',
             '--beamspot', 'Realistic25ns13p6TeVEarly2023Collision', '--datatier', 'GEN',
             '--eventcontent', 'FEVTDEBUG', '--geometry', 'DB:Extended', '--era', 'Run3_2023',
             '--fileout', 'file:gen.root', '--python_filename', 'gen_cfg.py', '--no_exec',
             '-n', str(count)], directory/'driver.log', cwd=directory, timeout=600)
        with (directory/'gen_cfg.py').open('a') as target:
            target.write(f'''
from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customiseKeepShiftTruth
process = customiseKeepShiftTruth(process)
process.RandomNumberGeneratorService.generator.initialSeed = cms.untracked.uint32({seed})
process.source.firstRun = cms.untracked.uint32({run_number})
process.source.firstLuminosityBlock = cms.untracked.uint32(1)
process.source.firstEvent = cms.untracked.uint64(1)
process.source.numberEventsInLuminosityBlock = cms.untracked.uint32({count+1})
process.source.numberEventsInRun = cms.untracked.uint32({count+1})
process.options.numberOfThreads = cms.untracked.uint32(1)
process.options.numberOfStreams = cms.untracked.uint32(1)
process.generator.UserCustomization[0].pTHatMin = cms.double({low})
process.generator.UserCustomization[0].pTHatMax = cms.double({high})
''')
            if weighted:
                target.write(f'''
process.generator.UserCustomization[0].pluginName = cms.string('ShiftMpiWeightedProposalHook')
process.generator.UserCustomization[0].proposalTrials = cms.uint32({count})
process.generator.UserCustomization[0].eventRun = cms.uint32({run_number})
process.generator.UserCustomization[0].statisticsFile = cms.string('proposal_ledger.json')
process.generator.PythiaParameters.processParameters.append('Check:abortIfVeto = on')
process.shiftWeightedNormalization = cms.EDProducer('ShiftWeightedGenRunInfoProducer', statisticsFile=cms.string('proposal_ledger.json'))
process.weightedNormalization_step = cms.EndPath(process.shiftWeightedNormalization)
process.schedule.insert(0, process.weightedNormalization_step)
process.FEVTDEBUGoutput.outputCommands.append('keep *_shiftWeightedNormalization_*_*')
''')
        config = directory/'resolved_gen_cfg.py'
        run(['edmConfigDump', 'gen_cfg.py'], config, cwd=directory, timeout=600)
        environment = dict(os.environ)
        if weighted:
            environment['LD_PRELOAD'] = str(proposal_library(family))
        run(['cmsRun', 'gen_cfg.py'], directory/'cmsRun.log', cwd=directory,
            env=environment, timeout=row.get('generation_timeout_seconds', 7200))
        if weighted:
            argv = ['python3', str(scripts/'audit_shift_weighted_gen.py'), '--input', 'gen.root',
                    '--ledger', 'proposal_ledger.json', '--config', config.name,
                    '--generator-profile', 'ordinary', '--output', 'validation.json']
        else:
            argv = ['python3', str(scripts/'audit_generation_chunk.py'), 'gen.root',
                    '--process', name, '--events', str(count), '--chunk', '0',
                    '--run-offset', str(run_number-1), '--config', config.name,
                    '--fragment', str(fragment), '--generator-log', 'cmsRun.log',
                    '--lower', str(low), '--upper', str(high), '--output', 'validation.json']
        run(argv, directory/'audit.log', cwd=directory, timeout=900)
        audit = json.loads((directory/'validation.json').read_text())
    from audit_shift_material_scout import identity, metadata, product
    from DataFormats.FWLite import Events
    gen = directory/'gen.root'
    identities, weights = [], []
    for event in Events(str(gen)):
        identities.append(list(identity(event)))
        _, info = product(event, 'generator', 'GenEventInfoProduct')
        weights.append(float(info.weight()))
    if len(set(map(tuple, identities))) != len(identities):
        raise ValueError('Repeated raw source event')
    exposure = metadata(gen)
    source = dict(source_id=row['id'], stratum=row['stratum'],
        source_family='qcd_ordinary' if family == 'qcd' else family,
        gen_sha256=sha(gen), saved_events=len(identities), generator_profile='ordinary',
        timing_input_label='VtxSmeared' if family == 'dy' else 'generatorSmeared',
        decay_dependent_filter=False, saved_decays_unchanged=True,
        physics_normalization_ready=False, additional_branching_factor_applied=False,
        bounds=row['bounds'], generator_algorithm='native-unfiltered-unit-weight-LO-DY'
        if family == 'dy' else ('independent-geometric-full-support-tail-channel-retry-rb-v4'
                              if weighted else 'native-soft-MPI-class-rejection'),
        forced_decay_branching_convention_ready=False, diagnostic_only=False,
        source_model_scope='Assumed proton target and jaw impacts; normalization gates recorded separately.')
    if weighted:
        ledger = json.loads((directory/'proposal_ledger.json').read_text())
        source.update(denominator_kind='fixed_source_trials', full_source_trials=count,
                      sigma_nd_pb=ledger['sigma_nd_mb']*1.e9)
        if audit['event_ids'] != identities or audit['weights'] != weights:
            raise ValueError('Independent weighted census differs')
    else:
        # Preserve the native accepted-event denominator. Pythia trial counts
        # remain in the audit; they are never substituted for this denominator.
        products = exposure['runs'][0]['products']
        rate = products['generator']
        source.update(denominator_kind='accepted_gen_events', full_source_events=count,
            native_cross_section_pb=rate['internal_xsec_pb'],
            native_cross_section_error_pb=rate['internal_error_pb'])
        if len(identities) != count or weights != [1.]*count:
            raise ValueError('Accepted-event source count or unit weights differ')
    return source, identities, weights, audit, config


def main(request_path):
    request = json.loads(Path(request_path).read_text())
    row = request['source_request']
    report = dict(schema='shift-smart-SM-source-v1', complete=False,
                  source_id=row['id'], started_epoch=time.time())
    save('source_report.json', report)
    try:
        library = Path(os.environ['CMSSW_BASE'])/'lib'/os.environ['SCRAM_ARCH']
        for key in ('EDM_PLUGIN_PATH', 'LD_LIBRARY_PATH'):
            os.environ[key] = ':'.join([str(library)]+[
                p for p in os.environ.get(key, '').split(':') if p and p != str(library)])
        # edmPluginRefresh skips libraries older than the existing cache.
        # Install with a fresh mtime, as the qualified weighted GEN entry does.
        shutil.copyfile(TOOLS/'plugin/pluginShiftMpiWeightedProposalHook.so',
                        library/'pluginShiftMpiWeightedProposalHook.so')
        (library/'ShiftMpiWeightedProposalHook.edmplugin').write_text(
            'module pluginShiftMpiWeightedProposalHook.so\n')
        run(['edmPluginRefresh', str(library)], 'plugins.log', timeout=120,
            env=dict(os.environ, LD_PRELOAD=str(proposal_library(row['family']))))
        cache = (library/'.edmplugincache').read_text()
        report['weighted_plugin_registration'] = dict(library=str(library),
            plugin_path=os.environ['EDM_PLUGIN_PATH'],
            records=[line for line in cache.splitlines() if 'ShiftMpiWeighted' in line
                     or 'ShiftWeightedGenRun' in line])
        if not any('ShiftMpiWeightedProposalHook CustomHookFactory' in line for line in cache.splitlines()):
            raise ValueError('Weighted GEN hook missing from refreshed worker-local plugin cache')
        source, identities, weights, audit, config = generate(request, Path('raw').resolve())
        raw_base = request['publication_directory']+'/raw'
        files = [(Path('raw/gen.root'), 'gen.root'), (config, 'resolved_gen_cfg.py'),
                 (Path('raw/validation.json'), 'validation.json')]
        if Path('raw/proposal_ledger.json').exists():
            files.append((Path('raw/proposal_ledger.json'), 'proposal_ledger.json'))
        if sum(path.stat().st_size for path, _ in files) > request['source_publication_bound_bytes']:
            raise ValueError('Raw source exceeds its reserved publication bound')
        raw_publications = publish(files, raw_base)
        source['url'] = 'root://eosuser.cern.ch/'+raw_base+'/gen.root'
        save('native_source_receipt.json', dict(schema='shift-smart-SM-native-source-v1',
            events=len(identities), event_ids=identities,
            semantic_audit=dict(event_ids=identities, weights=weights, native_audit=audit),
            publications=raw_publications))
        source['original_receipt'] = raw_base+'/source.json'
        # The source audit is separate from the descriptor that refers to it.
        source['original_receipt_sha256'] = sha('native_source_receipt.json')
        publish([(Path('native_source_receipt.json'), 'source.json')], raw_base)
        save('raw_source.json', source)
        if not identities:
            report.update(complete=True, empty_source=True, selected_events=0,
                          source=source, raw_publications=raw_publications)
            save('source_report.json', report)
            return
        profile = json.loads((TOOLS/'profile.json').read_text())
        positions = [source_position(profile, source['gen_sha256'], identity,
                     request['source_position_seed']) for identity in identities]
        containment = []
        for position in positions:
            path = next(p for p in profile['paths'] if p['id'] == position['jaw_id'])
            segment = path['segments'][position['segment_index']]
            containment.append(dict(event_id=position['event_id'],
                actual_volume=segment['volume'], actual_material=segment['material']))
        unit = dict(family=row['family'], source=source, input_label=source['timing_input_label'],
            seed=row['seed']+11, event_ids=identities, native_weights=weights,
            positions=positions, source_inclusion_probability=1., full_saved_source_census=True)
        plan = dict(request['preparation_contract'], units=[unit], event_positions=positions,
            containment_rows=containment, target_map_sha256=checksum(positions),
            source_profile_sha256=checksum(profile), containment_receipt_sha256=checksum(containment),
            test_scope='assumed-jaw-depth-canary', maximum_events_per_unit=1000,
            material_feature_census=True, source_assumption=profile['source_model'],
            publication_directory=request['publication_directory']+'/prepared',
            publication_bound_bytes=request['source_publication_bound_bytes'])
        preparation = Path('preparation').resolve()
        preparation.mkdir()
        save(preparation/'plan.json', plan)
        run(['python3', str(TOOLS/'scripts/run_shift_source_preparation.py'), 'plan.json'],
            'preparation.log', cwd=preparation, timeout=10800)
        from shift_material_response import _prepared_source_descriptor
        prepared = _prepared_source_descriptor(source, (preparation/'summary.json').read_text(),
                                               sha(preparation/'summary.json'))
        if prepared['prepared_event_ids'] != identities or prepared['prepared_native_weights'] != weights:
            raise ValueError('Prepared census differs from independently audited native GEN')
        feature_file = Path('features.json.gz')
        command(['xrdcp', '--silent', '--cksum', 'adler32', 'root://eosuser.cern.ch/'+request['publication_directory']+
                 '/prepared/'+row['family']+'_features.json.gz', str(feature_file)], timeout=900)
        with gzip.open(feature_file, 'rt') as stream:
            features = json.load(stream)
        authority_source = dict(source_id=source['source_id'], original_source_sha256=source['gen_sha256'],
            feature_source_sha256s=[prepared['gen_sha256']], full_gen_exposure=source,
            generator_profile='ordinary', decay_dependent_filter=False)
        authorities = [dict(source_id=source['source_id'], original_source_sha256=source['gen_sha256'],
            feature_source_sha256=prepared['gen_sha256'], event_id=e['event_id'],
            graph_sha256=e['graph_sha256'], native_weight=e['native_weight'],
            sampling_probability=1., previous_selection_domains=[], proposal_kind='ordinary',
            proposal_draw_id='natural-decay:'+str(row['seed']+11), proposal_density_ratio=1.,
            proposal_correction=1., proposal_provenance_sha256=sha(preparation/'summary.json'),
            saved_decays_unchanged=True) for e in features['events']]
        bundle = dict(schema='shift-production-material-inputs-v1',
            cohort_id=request['cohort_id']+':'+row['id'], selection_seed=request['selection_seed'],
            features=features, authority=dict(schema='shift-material-admission-authority-v1',
            population_scope='representative_census', sources=[authority_source], events=authorities))
        ledger = prepare_default_selection(bundle)
        descriptor = dict(gen=prepared['url'].removeprefix('root://eosuser.cern.ch/'),
            gen_sha256=prepared['gen_sha256'], receipt=raw_base+'/source.json',
            receipt_sha256=source['original_receipt_sha256'], event_ids=identities, weights=weights,
            stratum=row['stratum'], normalization_record=source,
            missing_filter_lumi=row['family'] == 'dy',
            storage_contract='shift-process-bin-detector-v1',
            nano_base=request['nano_base'], metadata_base=request['metadata_base'],
            maximum_publication_bytes=16777216)
        selected = bind_source(descriptor, ledger)
        save('selected_source.json', selected)
        with gzip.open('selection_ledger.json.gz', 'wt') as stream:
            json.dump(ledger, stream, allow_nan=False)
        publications = publish([(Path('selected_source.json'), 'selected_source.json'),
            (Path('selection_ledger.json.gz'), 'selection_ledger.json.gz')],
            request['publication_directory'])
        prepared_publications = json.loads((preparation/'summary.json').read_text())['published']
        report.update(complete=True, source=source, selected_source=selected,
            selected_events=len(selected['selected_indices']), raw_events=len(identities),
            raw_publications=raw_publications, publications=publications,
            prepared_publications=prepared_publications,
            published_storage_bytes=sum(x['bytes'] for x in
                raw_publications+prepared_publications+publications)+1048576,
            source_preparation_receipt_sha256=sha(preparation/'summary.json'))
    except BaseException as error:
        report.update(error=repr(error))
        report['log_tails'] = {str(p):p.read_text(errors='replace')[-16000:]
            for p in Path('.').rglob('*.log') if 'runtime' not in p.parts}
        raise
    finally:
        report.update(finished_epoch=time.time(), wall_seconds=time.time()-report['started_epoch'])
        save('source_report.json', report)


if __name__ == '__main__':
    main(sys.argv[1])
