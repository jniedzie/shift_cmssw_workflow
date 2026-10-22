#!/usr/bin/env python3
# Code style: AI-code. AI-generated frozen material-response detector worker.
"""Reconstruct an independent random GEN subset, without applying the candidate gate.

Saved decays and source weights are unchanged. The corrected incoming-parent
overlay is applied only to SIM. All tiers must preserve identities, graphs and
full source exposure before compact evidence and Nano are published.
"""
import gzip
import json
import math
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tarfile
import time

from audit_shift_material_scout import audit, digest, metadata
from run_shift_gen_to_nano import audit_edm, audit_nano, command, event_progress, progress, signatures
from shift_gen_material_features import event_features, hepmc_unit_names, read_hepmc
from shift_material_preselection import admission_probability, pair_priority
from shift_material_preselection import sha256 as checked_sha256
from shift_material_response import (retained_tail_counts, sampling_probabilities,
                                     source_normalization, source_rate_weight, source_timing_input,
                                     source_origin_sha256, validate_prepared_source, response_source)
from shift_event_weights import annotate_proposal_weights


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def cpu():
    values = [resource.getrusage(which) for which in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)]
    return sum(x.ru_utime+x.ru_stime for x in values)


def install_prepared_hepmc2_copy(request, tools):
    """Install one pinned plugin into this Condor worker's private runtime."""
    expected = checked_sha256(request.get('hepmc2_copy_plugin_sha256'), 'prepared HepMC2 copy plugin')
    plugin = tools/'plugin/pluginShiftHepMC2CopyProducer.so'
    if digest(plugin) != expected:
        raise ValueError('Prepared HepMC2 copy plugin SHA differs')
    if not all(os.environ.get(key) for key in ('_CONDOR_SCRATCH_DIR', 'CMSSW_BASE', 'SCRAM_ARCH')):
        raise ValueError('Prepared HepMC2 copy requires an explicit worker-local Condor runtime')
    scratch = Path(os.environ['_CONDOR_SCRATCH_DIR']).resolve()
    library = (Path(os.environ['CMSSW_BASE'])/'lib'/os.environ['SCRAM_ARCH']).resolve()
    target = (library/plugin.name).resolve()
    if not library.is_relative_to(scratch) or not target.is_relative_to(scratch):
        raise ValueError('Prepared HepMC2 copy target is outside worker-local Condor scratch')
    library.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(plugin, target)
    if digest(target) != expected:
        raise ValueError('Installed prepared HepMC2 copy plugin SHA differs')
    command(['edmPluginRefresh', str(library)], timeout=120)
    return dict(type='ShiftHepMC2CopyProducer', sha256=expected, input='shiftEventTime::SHIFTTIMING')


TRANSPORT_LIBRARIES = ('libSimG4CoreGenerators.so', 'libSimG4CoreApplication.so',
                       'pluginSimG4CoreApplicationPlugins.so')


def install_hadron_transport(request, tools):
    """Install a complete, receipt-bound private build for an explicit canary."""
    transport = request.get('hadron_transport')
    if transport is None:
        return None
    if not isinstance(transport, dict) or transport.get('track_all_hadron_decays') is not True:
        raise ValueError('Hadron transport requires an explicit TrackAllHadronDecays opt-in')
    reference = transport.get('build_receipt', {})
    expected = checked_sha256(reference.get('sha256'), 'hadron transport build receipt')
    relative = Path(reference.get('path', ''))
    tools = tools.resolve()
    receipt = (tools/relative).resolve()
    if relative.is_absolute() or not receipt.is_relative_to(tools) or not receipt.is_file():
        raise ValueError('Hadron transport build receipt is outside frozen tools')
    if digest(receipt) != expected:
        raise ValueError('Hadron transport build receipt SHA differs')
    inventory = json.loads(receipt.read_text()).get('libraries', {})
    if not isinstance(inventory, dict) or set(inventory) != set(TRANSPORT_LIBRARIES):
        raise ValueError('Hadron transport requires the complete three-library build')
    if not all(os.environ.get(key) for key in ('_CONDOR_SCRATCH_DIR', 'CMSSW_BASE', 'SCRAM_ARCH')):
        raise ValueError('Hadron transport requires an explicit worker-local Condor runtime')
    scratch = Path(os.environ['_CONDOR_SCRATCH_DIR']).resolve()
    library = (Path(os.environ['CMSSW_BASE'])/'lib'/os.environ['SCRAM_ARCH']).resolve()
    if not library.is_relative_to(scratch):
        raise ValueError('Hadron transport target is outside worker-local Condor scratch')
    checked = []
    for name in TRANSPORT_LIBRARIES:
        item = inventory[name]
        if not isinstance(item, dict):
            raise ValueError('Hadron transport library entry must bind its SHA and size')
        expected_sha = checked_sha256(item.get('sha256'), 'hadron transport library')
        source, target = (receipt.parent/name).resolve(), (library/name).resolve()
        if (not source.is_relative_to(receipt.parent) or target.parent != library
                or target.name != name):
            raise ValueError('Hadron transport library escapes its frozen or worker-local directory')
        size = item.get('bytes')
        if (type(size) is not int or size <= 0 or not source.is_file()
                or source.stat().st_size != size or digest(source) != expected_sha):
            raise ValueError('Hadron transport library differs from its bound build receipt')
        checked.append((source, target, item))
    # Validate the complete inventory before replacing any worker-local library.
    library.mkdir(parents=True, exist_ok=True)
    for source, target, item in checked:
        shutil.copyfile(source, target)
        if digest(target) != item['sha256']:
            raise ValueError('Installed hadron transport library SHA differs')
    os.environ.pop('LD_PRELOAD', None)
    paths = [p for p in os.environ.get('LD_LIBRARY_PATH', '').split(':')
             if p and '/biglib/' not in p and p != str(library)]
    os.environ['LD_LIBRARY_PATH'] = ':'.join([str(library)]+paths)
    command(['edmPluginRefresh', str(library)], timeout=120)
    return dict(track_all_hadron_decays=True, build_receipt_sha256=expected,
                libraries=inventory, installed_directory=str(library), baseline_preload=False)


def material_sim_config_script(prepared, track_all_hadron_decays=False):
    """Use the fixed HepMC2 copy only after the prepared-source proof passes."""
    script = '''import json,runpy,sys
import FWCore.ParameterSet.Config as cms
p=runpy.run_path(sys.argv[1])['process']
p.source.fileNames=cms.untracked.vstring('file:'+sys.argv[2])
p.source.eventsToProcess=cms.untracked.VEventRange([':'.join(map(str,x))+'-'+':'.join(map(str,x)) for x in json.load(open('event_ids.json'))])
p.maxEvents.input=cms.untracked.int32(int(sys.argv[4]))
p.RandomNumberGeneratorService.g4SimHits.initialSeed=cms.untracked.uint32(int(sys.argv[5]))
p.FEVTDEBUGoutput.fileName=cms.untracked.string('file:'+sys.argv[3])
'''
    if prepared:
        script += "p.generatorSmeared=cms.EDProducer('ShiftHepMC2CopyProducer',src=cms.InputTag('shiftEventTime','','SHIFTTIMING'))\n"
    if track_all_hadron_decays:
        script += 'p.g4SimHits.Generator.TrackAllHadronDecays=cms.untracked.bool(True)\n'
    return script+"open('sim_cfg.py','w').write(p.dumpPython())\n"


def diagnostic_edm_outputs(request):
    """Validate optional retained tiers before any GEN download or detector work."""
    tiers = request.get('diagnostic_edm_tiers', [])
    files = {'SIM': ('step1.root', 'sim.root'), 'DIGIHLT': ('step2.root', 'digi.root'),
             'RECO': ('step3.root', 'reco.root')}
    if (not isinstance(tiers, list) or any(not isinstance(tier, str) or tier not in files for tier in tiers)
            or len(set(tiers)) != len(tiers)):
        raise ValueError('diagnostic_edm_tiers must be a list of distinct SIM/DIGIHLT/RECO tiers')
    return [dict(tier=tier, local_filename=files[tier][0], published_filename=files[tier][1])
            for tier in tiers]


def nano_missing_filter_lumi(source, exposure):
    """Unfiltered accepted-event DY has no optional GenFilterInfo table."""
    kind, _, _ = source_normalization(source)
    if kind != 'accepted_gen_events' or source.get('source_family') != 'dy':
        return False
    if (source.get('generator_algorithm') != 'native-unfiltered-unit-weight-LO-DY'
            or source.get('decay_dependent_filter') is not False):
        raise ValueError('Cannot omit the Nano filter table for an unqualified DY source')
    if ('original_full_Run_Lumi_metadata' in source
            and exposure != source['original_full_Run_Lumi_metadata']):
        raise ValueError('Nano filter-table decision changed bound source Run/Lumi accounting')
    lumis = exposure.get('lumis', [])
    if not lumis or any(row.get('original_GenLumiInfoProduct_present') is not True for row in lumis):
        raise ValueError('Nano filter-table decision requires native DY luminosity accounting')
    present = ['GenFilterInfo' in row for row in lumis]
    if any(present) and not all(present):
        raise ValueError('Cannot omit the Nano filter table with mixed GenFilterInfo presence')
    return not any(present)


def publish_outputs(publication, request, existing=()):
    """Apply one registered bound and SHA readback to every retained output."""
    if sum(Path(local).stat().st_size for local, _ in publication)+65536 > request['publication_bound_bytes']:
        raise ValueError('Publication exceeds registered storage allocation')
    base = request['publication_directory']
    previous = {item['url']:item for item in existing}
    if (len(previous) != len(existing)
            or not set(previous).issubset({'root://eosuser.cern.ch/'+base+'/'+remote for _, remote in publication})):
        raise ValueError('Retained checkpoint is not part of the final publication')
    command(['xrdfs', 'root://eosuser.cern.ch', 'mkdir', '-p', base])
    published = []
    for local, remote in publication:
        url = 'root://eosuser.cern.ch/'+base+'/'+remote
        item = dict(url=url, bytes=Path(local).stat().st_size, sha256=digest(local))
        if url in previous:
            if previous[url] != item:
                raise ValueError('Audited checkpoint differs from final local tier')
        else:
            command(['xrdcp', '--silent', '--posc', '--cksum', 'adler32', local, url])
        command(['xrdcp', '--silent', '--cksum', 'adler32', url, 'readback.bin'])
        if digest('readback.bin') != digest(local):
            raise ValueError('Publication readback differs')
        Path('readback.bin').unlink()
        published.append(item)
    return published


def response(path):
    import ROOT
    f = ROOT.TFile.Open(str(path))
    tree = f.Get('Events')
    rows = []
    for entry in tree:
        pairs = [{name: float(getattr(entry, 'ShiftDimuonVertex_'+name)[i])
                  for name in ('mass', 'pt', 'isOS', 'topologyMin', 'topologyMax', 'muonIdx1', 'muonIdx2')}
                 for i in range(int(entry.nShiftDimuonVertex))]
        for pair in pairs:
            pair['nonfinite_fields'] = [key for key,value in pair.items() if not math.isfinite(value)]
            for key in pair['nonfinite_fields']:
                pair[key] = None
        rows.append(dict(event_id=[int(entry.run), int(entry.luminosityBlock), int(entry.event)],
                         retained_pairs=pairs, tail_counts=retained_tail_counts(pairs)))
    f.Close()
    return rows


def main():
    request_path = Path(sys.argv[1])
    request = json.loads(request_path.read_text())
    diagnostic_outputs = diagnostic_edm_outputs(request)
    tools = Path(__file__).resolve().parent.parent
    source, sample = response_source(request, tools), request['sample']
    validate_prepared_source(source, sample)
    if source.get('requires_full_muon_completion', False):
        raise ValueError('Raw ordinary QCD requires audited full natural muon completion before detector response')
    timing_input = source_timing_input(source)
    prepared = 'source_preparation_receipt_json' in source
    original_sha = source_origin_sha256(source)
    source_normalization(source)
    inclusion = sampling_probabilities(sample)
    report = dict(schema='shift-material-response-worker-v1', complete=False,
                  request_sha256=digest(request_path), source_id=source['source_id'],
                  original_source_sha256=original_sha, prepared_input_GEN_sha256=source['gen_sha256'], stages=[],
                  source_stratum=source['stratum'],
                  saved_decays_unchanged=True, material_survival_weight_applied=False,
                  physics_normalization_ready=False, started_epoch=time.time())
    report['sampling_design'] = request.get('sampling_design', 'uniform within saved source')
    report['diagnostic_edm_outputs'] = diagnostic_outputs
    if 'source_preparation_receipt_sha256' in source:
        report['source_preparation_receipt_sha256'] = source['source_preparation_receipt_sha256']

    def save():
        dump('report.json', report)

    def run_stage(name, argv, limit, env=None):
        started, used = time.monotonic(), cpu()
        report.update(current_stage=name, progress_epoch=time.time())
        report.update(stage_started_epoch=time.time(), stage_deadline_epoch=time.time()+limit,
                      stage_timeout_seconds=limit, stage_records_started=0)
        save()
        progress(report, name, {})
        print(json.dumps(dict(stage=name, epoch=time.time(), sample=sample['id'])), flush=True)
        with open(name+'.log', 'w') as stream:
            observe = event_progress(Path(name+'.log'), report, name, {}, 3600,
                expected_records=len(sample['indices']), first_event_stall_seconds=7200)
            command(argv, timeout=limit, env=env, stdout=stream, stderr=subprocess.STDOUT, on_poll=observe)
        report['stages'].append(dict(stage=name, wall_seconds=time.monotonic()-started,
                                     cpu_seconds=cpu()-used))
        save()

    save()
    try:
        transport = install_hadron_transport(request, tools)
        if transport:
            report['hadron_transport'] = transport
            save()
        if prepared:
            report['prepared_hepmc2_copy_plugin'] = install_prepared_hepmc2_copy(request, tools)
            save()
        command(['xrdcp', '--silent', '--cksum', 'adler32', source['url'], 'gen.root'])
        if digest('gen.root') != source['gen_sha256']:
            raise ValueError('Source GEN hash changed')
        for path, expected in request['private_input_hashes'].items():
            if digest(path) != expected:
                raise ValueError('Private input changed: '+path)
        exposure = metadata('gen.root')
        if ('source_preparation_receipt_json' in source
                and exposure != source['original_full_Run_Lumi_metadata']):
            raise ValueError('Prepared input changed full original source Run/Lumi accounting')
        report['full_source_exposure'] = exposure
        missing_filter_lumi = nano_missing_filter_lumi(source, exposure)
        report['nano_optional_filter_table_omitted'] = missing_filter_lumi
        # Reproduce the nominal source time once from its native unshifted
        # graph. Ordinary DY already timed generatorSmeared, so its adapter
        # explicitly selects the preserved original VtxSmeared product.
        # This lets the material audit compare exact timed graphs end to end.
        selected_rows = [dict(id=eid, weight=w) for eid,w in zip(sample['event_ids'], sample['native_weights'])]
        dump('event_ids.json', sample['event_ids'])
        timing = '''import json,sys
import FWCore.ParameterSet.Config as cms
p=cms.Process('SHIFTTIMING')
p.load('IOMC.ShiftEventTiming.shiftEventTime_cfi')
p.shiftEventTime.src=cms.InputTag(sys.argv[4], '', sys.argv[5])
p.source=cms.Source('PoolSource',fileNames=cms.untracked.vstring('file:'+sys.argv[1]),eventsToProcess=cms.untracked.VEventRange([':'.join(map(str,x))+'-'+':'.join(map(str,x)) for x in json.load(open('event_ids.json'))]))
p.maxEvents=cms.untracked.PSet(input=cms.untracked.int32(int(sys.argv[3])))
p.path=cms.Path(p.shiftEventTime)
p.out=cms.OutputModule('PoolOutputModule',fileName=cms.untracked.string('file:'+sys.argv[2]),outputCommands=cms.untracked.vstring('keep *'))
p.end=cms.EndPath(p.out)
open('timing_cfg.py','w').write(p.dumpPython())
'''
        command([sys.executable, '-c', timing, str(Path('gen.root').resolve()),
                 str(Path('timed_gen.root').resolve()), str(len(selected_rows)), timing_input,
                 source.get('timing_input_process', '')], timeout=600)
        before = signatures(Path('gen.root'), 0, len(selected_rows), indices=sample['indices'])
        run_stage('TIMING', ['cmsRun', 'timing_cfg.py'], 1800)
        after = signatures(Path('timed_gen.root'), 0, len(selected_rows))
        if [(x['id'], x['weight'], x['hepmc_sha256']) for x in before] != [(x['id'], x['weight'], x['hepmc_sha256']) for x in after]:
            raise ValueError('Nominal timing translation changed particle graph or weights')
        audit_edm('timed_gen.root', selected_rows)
        if metadata('timed_gen.root') != exposure:
            raise ValueError('Timing-only step changed full source exposure')
        import ROOT
        from DataFormats.FWLite import Events
        from audit_shift_material_scout import identity
        ROOT.TGeoManager.LockDefaultUnits(False)
        ROOT.TGeoManager.SetDefaultUnits(ROOT.TGeoManager.kRootUnits)
        ROOT.TGeoManager.LockDefaultUnits(True)
        started = cpu()
        geometry = ROOT.TGeoManager.Import(request['geometry_path'])
        if not geometry:
            raise ValueError('Cannot load material geometry')
        hepmc_unit_names()
        stream = Events('timed_gen.root')
        if stream.size() != len(selected_rows):
            raise ValueError('Timed source population changed')
        features, original_rows = [], []
        for selected_index, index in enumerate(sample['indices']):
            stream.to(selected_index)
            eid = list(identity(stream))
            if eid != sample['event_ids'][selected_index]:
                raise ValueError('GEN identity differs from source receipt')
            particles, weight, weights, units = read_hepmc(stream, 'shiftEventTime')
            if weight != sample['native_weights'][selected_index]:
                raise ValueError('Native source weight changed')
            feat = event_features(particles, eid, weight, weights, geometry, request['frame'])
            ranking = pair_priority(feat, request['policy']['momentum_scale_GeV'],
                                    request['policy'].get('require_opposite_sign', True),
                                    request['policy'].get('rank_partial_parent_paths', True),
                                    request['policy'].get('two_endcap_pointing'),
                                    request['policy'].get('muon_column_priority'))
            p = admission_probability(ranking, weight, source['mean_abs_native_weight'], request['policy'])
            feat.update(candidate_probability=p, ranking=ranking, original_source_sha256=original_sha,
                        prepared_input_GEN_sha256=source['gen_sha256'])
            features.append(feat)
            original_rows.append(dict(id=eid, weight=weight))
        feature_cpu = cpu()-started
        with gzip.open('features.json.gz', 'wt') as out:
            json.dump(dict(source=source, sample=sample, events=features,
                           policy=request['policy'], feature_cpu_seconds=feature_cpu), out, allow_nan=False)
        report['feature_cpu_seconds'] = feature_cpu
        dump('event_ids.json', [x['id'] for x in original_rows])
        # Generate every stage in a fresh interpreter; CMSSW era modifiers are global.
        # The SIM template already schedules the saved, complete HepMC directly.
        script = material_sim_config_script(prepared, bool(transport))
        command([sys.executable, '-c', script, str(tools/'templates/material_sim.py'),
                 str(Path('timed_gen.root').resolve()), str(Path('step1.root').resolve()),
                 str(len(original_rows)), str(sample['seed'])], timeout=600)
        env = dict(os.environ)
        if transport:
            env.pop('LD_PRELOAD', None)
        else:
            env['LD_PRELOAD'] = str(tools/'plugin/libShiftCompleteIncomingBaseline.so')
        run_stage('SIM', ['cmsRun', 'sim_cfg.py'], request['stage_timeout_seconds'], env)
        audit_edm('step1.root', original_rows)
        measured = audit(Path('timed_gen.root'), Path('step1.root'), minimum_stations=1,
                         expected_event_ids=set(tuple(x['id']) for x in original_rows))
        if not measured['passed']:
            raise ValueError('SIM graph, weight or exposure audit failed')
        with gzip.open('material_audit.json.gz', 'wt') as out:
            json.dump(measured, out, allow_nan=False)
        previous = Path('step1.root').resolve()
        for stage in (2, 3, 4):
            output = Path(f'step{stage}.root').resolve()
            command([sys.executable, str(tools/'scripts/run_shift_gen_to_nano.py'), 'make-config',
                     str(tools/f'templates/step{stage}.py'), str(stage), str(previous), str(output),
                     '0', str(len(original_rows)), str(sample['seed']+stage), '1',
                     str(int(missing_filter_lumi)), f'step{stage}_cfg.py'], timeout=600)
            run_stage(f'step{stage}', ['cmsRun', f'step{stage}_cfg.py'], request['stage_timeout_seconds'])
            check = audit_nano(output, original_rows) if stage == 4 else audit_edm(output, original_rows)
            report['stages'][-1]['audit'] = check
            if stage < 4 and metadata(output) != measured['full_original_run_lumi_metadata']:
                raise ValueError('Detector tier changed source exposure')
            if stage == 3 and any(item['tier'] == 'RECO' for item in diagnostic_outputs):
                # Retain the audited input before Nano can fail. Final publication
                # uses the same path, so this checkpoint adds no duplicate tier.
                report['validated_reco_checkpoint'] = dict(audit=check,
                    published=publish_outputs([(str(output), 'reco.root')], request))
            save()
            previous = output
        answers = response('step4.root')
        if [x['event_id'] for x in answers] != [x['id'] for x in original_rows]:
            raise ValueError('Response identity/order mismatch')
        weight_rows = [dict(source_id=source['source_id'], original_source_sha256=original_sha,
            event_id=x['id'], proposal_draw_id=sample['id']+':'+str(x['id']), native_weight=x['weight'],
            sampling_probability=p, sampling_gen_weight=x['weight']/p,
            proposal_density_ratio=1., proposal_correction=1.,
            proposal_gen_weight=x['weight']/p, admitted=True) for x, p in zip(original_rows, inclusion)]
        kind, denominator, _ = source_normalization(source)
        source_exposure = dict(source_id=source['source_id'], original_source_sha256=original_sha,
                               original_full_Run_Lumi_metadata=exposure)
        if kind == 'fixed_source_trials':
            source_exposure['full_source_trials'] = denominator
        else:
            source_exposure.update(denominator_kind=kind, full_source_events=denominator)
        report['weight_annotation'] = annotate_proposal_weights(Path('step4.root'), weight_rows,
            [source_exposure],
            expected_nano_sha256=digest('step4.root'))
        detector_cpu = sum(x['cpu_seconds'] for x in report['stages'])
        for answer, feat, original, p in zip(answers, features, original_rows, inclusion):
            answer.update(original_source_sha256=original_sha, prepared_input_GEN_sha256=source['gen_sha256'],
                source_id=source['source_id'],
                stratum=source['stratum'], native_weight=original['weight'],
                benchmark_probability=p, candidate_probability=feat['candidate_probability'],
                rate_weight=source_rate_weight(source, original['weight']),
                detector_cpu_seconds=detector_cpu/len(answers), feature_cpu_seconds=feature_cpu/len(answers))
        dump('response.json', dict(schema='shift-material-response-v1', events=answers,
             source_id=source['source_id'], source_GEN_sha256=original_sha,
             prepared_input_GEN_sha256=source['gen_sha256'],
             normalization=source, full_source_exposure=exposure,
             no_second_filter_efficiency=True, saved_decays_unchanged=True,
             sampling_design=report['sampling_design'],
             candidate_gate_applied_to_detector_sample=False))
        report.update(events=len(answers), tail_counts={key:sum(a['tail_counts'][key] for a in answers)
                      for key in answers[0]['tail_counts']})
        for item in diagnostic_outputs:
            path = Path(item['local_filename'])
            item.update(bytes=path.stat().st_size, sha256=digest(path))
        save()
        with tarfile.open('evidence.tar.gz', 'w:gz') as archive:
            for name in ('response.json', 'features.json.gz', 'material_audit.json.gz', 'report.json'):
                archive.add(name, arcname=name)
        publication = [('step4.root', 'nano.root'), ('evidence.tar.gz', 'evidence.tar.gz')]
        publication.extend((item['local_filename'], item['published_filename']) for item in diagnostic_outputs)
        published = publish_outputs(publication, request,
            existing=report.get('validated_reco_checkpoint', {}).get('published', []))
        base = request['publication_directory']
        report.update(complete=True, published=published, finished_epoch=time.time(),
                      wall_seconds=time.time()-report['started_epoch'])
        save()
        command(['xrdcp', '--silent', '--posc', '--cksum', 'adler32', 'report.json',
                 'root://eosuser.cern.ch/'+base+'/complete.json'])
        # Scratch belongs only to this worker. All retained outputs were audited/read back.
        report['removed_scratch_inputs'] = []
        for name in ('gen.root', 'timed_gen.root', 'step1.root', 'step2.root', 'step3.root'):
            path = Path(name)
            report['removed_scratch_inputs'].append(dict(path=name, bytes=path.stat().st_size, sha256=digest(path)))
            path.unlink()
        save()
    except BaseException as error:
        report.update(complete=False, error=repr(error), failed_epoch=time.time())
        report['log_tails'] = {p.name:p.read_text(errors='replace')[-12000:] for p in Path('.').glob('*.log')}
        save()
        raise


if __name__ == '__main__':
    main()
