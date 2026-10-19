#!/usr/bin/env python3
# Code style: AI-code. AI-generated fixed-exposure meson GEN production runner.
"""Generate an audited exclusive meson-radiative source without a CMSSW build.

The source exposure is fixed before collision or rare-decay sampling. Zero-
parent and rejected collisions remain in the full ledger. This is an ND-only
meson mechanism, separate from DY and other low-mass production mechanisms.
"""
import argparse
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

from dark_photon_meson import MUON_MASS_GEV, mean_lab_beta_gamma, radiative_branching, sample_decay, transition_form_factor_abs2
from dark_photon_meson_source import (coefficient_bound, choose_collision, source_cross_section_pb,
    model_precision_budget, resolve_parent_kinematics, resolved_parent_models,
    parent_branching_coefficient, vector_parent, dipion_channel, exclusive_channels, production_mechanism)
import dark_photon_vector_meson as vector_meson
from dark_photon_meson_native import native_source, inject_decay, cmssw_native_libraries
from fixed_target_generation import beam_settings
from run_dark_photon_spectral_gen import digest, freeze_spectral_inputs, physical_width_table

SOURCES = ('run_dark_photon_meson_gen.py', 'dark_photon_meson.py', 'dark_photon_meson_source.py',
           'dark_photon_vector_meson.py', 'dark_photon_dipion_meson.py',
           'dark_photon_meson_native.py', 'dark_photon_meson_gen_cfg.py', 'fixed_target_generation.py',
           'run_dark_photon_spectral_gen.py', 'dark_photon_model.py', 'audit_dark_photon_gen.py',
           'pythia_generation_ledger.py', 'export_dark_photon_cross_sections.py')


def run(args):
    if not 2*MUON_MASS_GEV < args.mass < .958 or not 0 < args.epsilon <= .01:
        raise ValueError('Require an open visible dimuon channel below the eta-prime endpoint')
    if not 2 <= args.source_collisions <= 10000000 or not 1 <= args.seed <= 899999800:
        raise ValueError('Require bounded fixed source exposure and a valid unused seed')
    if args.timeout <= 0 or not 1 <= args.run_number <= 4294967295:
        raise ValueError('Require a positive deadline and reserved CMS run number')
    if not shutil.which('cmsRun') or not os.environ.get('CMSSW_BASE'):
        raise RuntimeError('Enter the prepared CMSSW runtime; no build is performed')
    os.environ['LD_LIBRARY_PATH'] = ':'.join(path for path in os.environ.get('LD_LIBRARY_PATH', '').split(':')
                                          if '/biglib/' not in path)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = dict(schema='shift-dark-photon-pilot-run-v1', status='preflight',
        production_mechanism='eta-etaprime-radiative', physics_valid=False, normalization_ready=False,
        mass_gev=args.mass, epsilon=args.epsilon, fixed_source_collisions_requested=args.source_collisions,
        seed=args.seed, run_number=args.run_number)
    path = out/'manifest.json'
    path.write_text(json.dumps(manifest, indent=2)+'\n')
    started = time.monotonic()
    try:
        here = Path(__file__).resolve().parent
        for name in SOURCES:
            shutil.copy2(here/name, out/name)
        manifest['source_sha256'] = {name: digest(out/name) for name in SOURCES}
        release = Path(os.environ['CMSSW_BASE'])
        runtime_provenance = getattr(args, 'runtime_provenance', None)
        runtime_archive = getattr(args, 'runtime_archive', None)
        if runtime_provenance is not None or runtime_archive is not None:
            if runtime_provenance is None or runtime_archive is None:
                raise ValueError('Archived runtime requires both frozen provenance and the actual local archive')
            provenance = json.loads(runtime_provenance.read_text())
            if (provenance.get('schema') != 'shift-bsm-gen-runtime-provenance-v1' or
                    runtime_archive.stat().st_size != provenance.get('archive_bytes') or
                    digest(runtime_archive) != provenance.get('archive_sha256') or
                    os.environ.get('SCRAM_ARCH') != provenance.get('scram_arch')):
                raise ValueError('Worker-local GEN runtime differs from its frozen archive authority')
            # A runtime archive excludes .git. An unknown historical revision
            # remains unknown; never attach the current checkout's revision.
            manifest['cmssw_git_head'] = provenance.get('cmssw_git_head')
            manifest['cmssw_git_status'] = provenance.get('cmssw_git_status')
            manifest['worker_local_runtime'] = provenance
            shutil.copy2(runtime_provenance, out/'runtime_provenance.json')
            manifest['runtime_provenance_sha256'] = digest(out/'runtime_provenance.json')
        else:
            manifest['cmssw_git_head'] = subprocess.check_output(
                ['git', '-C', str(release / 'src'), 'rev-parse', 'HEAD'], text=True).strip()
            manifest['cmssw_git_status'] = subprocess.check_output(
                ['git', '-C', str(release / 'src'), 'status', '--short'], text=True).splitlines()
        card = json.loads(args.model_card.read_text())
        if card['schema'] != 'shift-dark-photon-meson-model-v1' or card['source']['source_class'] != 'SoftQCD:nonDiffractive':
            raise ValueError('Require the explicit ND meson model card')
        mechanism = production_mechanism(card)
        manifest['production_mechanism'] = mechanism
        shutil.copy2(args.model_card, out/'meson_model.json')
        authority = freeze_spectral_inputs(args.darkcast_path.resolve(), args.darkcast_source_receipt.resolve(), out)
        sys.path.insert(0, str(out/'width_inputs'))
        import darkcast
        width = physical_width_table(darkcast.Model('dark_photon'), args.mass, args.epsilon, authority)
        from Configuration.Generator.Pythia8CommonSettings_cfi import pythia8CommonSettingsBlock
        from Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi import pythia8CP5SettingsBlock
        common = list(pythia8CommonSettingsBlock.pythia8CommonSettings)
        tune = list(pythia8CP5SettingsBlock.pythia8CP5Settings)
        beam = beam_settings(card['source']['beam_energy_gev'])
        settings = common+tune+beam+['SoftQCD:nonDiffractive=on',
            f'13:m0={MUON_MASS_GEV:.17g}', '13:mayDecay=off']
        masses = {}
        for parent in card['parents']:
            species = abs(parent['pdg_id'])
            if species in masses and masses[species] != parent['mass_gev']:
                raise ValueError('Channels and charge conjugates must share one physical parent mass')
            masses[species] = parent['mass_gev']
        settings.extend(f'{pdg}:m0={mass:.17g}' for pdg,mass in masses.items())
        settings.extend(card['source'].get('additional_pythia_settings', []))
        for parent in card['parents']:
            if vector_parent(parent):
                if parent.get('parent_mass_model') != 'zero-width-pole':
                    raise ValueError('No reviewed off-shell vector branching prescription is available')
                pdg, pole = abs(parent['pdg_id']), parent['mass_gev']
                settings.extend([f'{pdg}:mWidth=0', f'{pdg}:mMin={pole:.17g}', f'{pdg}:mMax={pole:.17g}'])
                settings.append(f"{abs(parent['recoil_pdg_id'])}:m0={parent['recoil_mass_gev']:.17g}")
            elif dipion_channel(parent):
                pdg, pole = parent['pdg_id'], parent['mass_gev']
                settings.extend([f'{pdg}:mWidth=0', f'{pdg}:mMin={pole:.17g}', f'{pdg}:mMax={pole:.17g}'])
                settings.append(f"211:m0={parent['pion_mass_gev']:.17g}")
        source = native_source(settings, args.seed, list(dict.fromkeys(parent['pdg_id'] for parent in card['parents'])))
        native_libraries = [dict(path=library, sha256=digest(Path(library)))
                            for library in cmssw_native_libraries()]
        manifest['native_libraries'] = native_libraries
        import ROOT
        configuration = json.loads(str(source.configuration()))
        if not configuration['check_event']:
            raise ValueError('The native Pythia event quality check must remain enabled')
        models = resolved_parent_models(card, configuration, args.mass)
        precision_doc = Path(os.environ['PYTHIA8DATA']) / 'ErrorChecks.xml'
        precision = dict(configuration, reference=str(precision_doc), reference_sha256=digest(precision_doc),
            prescription='native generated mass for branching and bound; unchanged p4 invariant for decay',
            numerical_budget='measured BR_gamma_gamma and TFF one-sigma parameter-box envelope')
        ecm = configuration['ecm_gev']
        bound = coefficient_bound(ecm, width['br_mumu'], models, args.mass)
        if bound <= 0:
            raise ValueError('No parent channel is open for this signal mass')
        writer = ROOT.ShiftMesonNative.Output(str(out/'signal.hepmc'))
        rng = random.Random(args.seed)
        collisions, slots, zero, boosts = 0, 0, 0, []
        sums = {name: [0., 0.] for name in ('coefficient_sum', 'coefficient_sum_squared',
                'physical_probability_sum', 'physical_probability_sum_squared',
                'coefficient_beta_gamma_sum', 'coefficient_beta_gamma_sum_squared',
                'coefficient_beta_gamma_cross_sum', 'coefficient_precision_difference_sum')}
        max_precision_shift = 0.

        def accumulate(name, value):
            total, lost = sums[name]
            adjusted = value - lost
            updated = total + adjusted
            sums[name] = [updated, (updated - total) - adjusted]

        with (out/'meson_source_ledger.jsonl').open('x') as ledger:
            for trial in range(1, 10*args.source_collisions+1):
                if time.monotonic()-started > args.timeout:
                    raise TimeoutError('Fixed source generation exceeded its operational deadline')
                success = bool(source.next())
                record = dict(schema='shift-meson-source-trial-v1', trial=trial,
                    generator_success=success, native_weight=None, parents=[], coefficient_sum=0.,
                    physical_probability=0., acceptance_probability=0., accepted=False,
                    parent_index=None, accepted_slot=None)
                if success:
                    collisions += 1
                    snapshot = json.loads(str(source.snapshot()))
                    if snapshot['ecm_gev'] != ecm:
                        raise ValueError('Parent source center-of-mass energy changed')
                    snapshot['parents'] = resolve_parent_kinematics(snapshot['parents'],
                        precision['mass_tolerance'], args.mass, models, width['br_mumu'])
                    decision = choose_collision(snapshot['parents'], args.mass, width['br_mumu'], models,
                                                bound, rng, snapshot['native_weight'])
                    record.update(decision, native_weight=snapshot['native_weight'], parents=snapshot['parents'])
                    record['physical_probability'] = args.epsilon**2*decision['coefficient_sum']
                    accumulate('coefficient_sum', decision['coefficient_sum'])
                    accumulate('coefficient_sum_squared', decision['coefficient_sum']**2)
                    accumulate('physical_probability_sum', record['physical_probability'])
                    accumulate('physical_probability_sum_squared', record['physical_probability']**2)
                    boost_sum = 0.
                    for parent in snapshot['parents']:
                        max_precision_shift = max(max_precision_shift, abs(parent['coefficient_relative_precision_shift']))
                        accumulate('coefficient_precision_difference_sum', parent['coefficient_difference_from_kinematic_mass'])
                        model = models[parent['pdg_id']]
                        for channel in exclusive_channels(model):
                            coefficient = parent_branching_coefficient(parent['mass_gev'], args.mass, channel, width['br_mumu'])
                            if coefficient == 0:
                                continue
                            if dipion_channel(channel):
                                import dark_photon_dipion_meson as dipion
                                mean = dipion.mean_lab_beta_gamma(parent['p4'], args.mass, channel)
                            elif vector_parent(channel):
                                mean = vector_meson.mean_lab_beta_gamma(parent['p4'], channel['recoil_mass_gev'],
                                    args.mass, polarization=channel['parent_polarization'])
                            else:
                                mean = mean_lab_beta_gamma(parent['p4'], args.mass)
                            boost_sum += coefficient*mean
                    record['coefficient_beta_gamma_sum'] = boost_sum
                    accumulate('coefficient_beta_gamma_sum', boost_sum)
                    accumulate('coefficient_beta_gamma_sum_squared', boost_sum**2)
                    accumulate('coefficient_beta_gamma_cross_sum', decision['coefficient_sum']*boost_sum)
                    zero += int(decision['coefficient_sum'] == 0.)
                    if decision['accepted']:
                        slots += 1
                        parent, = [row for row in snapshot['parents'] if row['index'] == decision['parent_index']]
                        event = source.convert(slots)
                        record['selected_parent_graph'] = json.loads(str(source.selected_parent_record(parent['index'])))
                        channels = exclusive_channels(models[parent['pdg_id']])
                        model, = [row for row in channels if row['channel'] == decision['selected_channel']]
                        if vector_parent(model):
                            decay = vector_meson.sample_decay(parent['p4'], model['recoil_mass_gev'],
                                args.mass, width['ctau_mm'], rng, polarization=model['parent_polarization'])
                            decay['recoil_pdg_id'] = model['recoil_pdg_id']
                        elif dipion_channel(model):
                            import dark_photon_dipion_meson as dipion
                            decay = dipion.sample_decay(parent['p4'], args.mass, width['ctau_mm'], rng, model=model)
                        else:
                            decay = sample_decay(parent['p4'], args.mass, width['ctau_mm'], rng)
                        inject_decay(source, parent['index'], decay)
                        writer.write(event)
                        record.update(accepted_slot=slots, selected_parent_pdg=parent['pdg_id'],
                                      sampled_decay=decay)
                        boosts.append(math.sqrt(math.fsum(value**2 for value in decay['dark_photon_p4'][1:]))/args.mass)
                ledger.write(json.dumps(record, allow_nan=False)+'\n')
                if collisions == args.source_collisions:
                    break
        writer.close()
        if collisions != args.source_collisions:
            raise ValueError('Fixed unfiltered source exposure was not completed')
        native = json.loads(str(source.summary()))
        statistics = str(source.statistics())
        (out/'source_statistics.log').write_text(statistics)
        if native['native_n_accepted'] != collisions:
            raise ValueError('Native Pythia accepted interactions differ from the full source ledger')
        exposure = dict(schema='shift-meson-source-exposure-v1', fixed_source_collisions=collisions,
            **native, zero_eligible_parent_collisions=zero, sum_native_weights=float(collisions),
            sum_native_weights_squared=float(collisions), **{name: value[0] for name, value in sums.items()},
            accepted_signal_events=slots,
            analytic_coefficient_bound=bound, coefficient_includes_br_mumu=True,
            maximum_coefficient_relative_precision_shift=max_precision_shift,
            coefficient_excludes_epsilon_squared=True, unit_output_gen_weights=True,
            source_ledger_sha256=digest(out/'meson_source_ledger.jsonl'))
        exposure['relative_rate_precision_shift'] = (sums['coefficient_precision_difference_sum'][0] /
            sums['coefficient_sum'][0] if sums['coefficient_sum'][0] else None)
        (out/'source_exposure.json').write_text(json.dumps(exposure, indent=2)+'\n')
        reference, reference_error = None, None
        if sums['coefficient_sum'][0]:
            reference = sums['coefficient_beta_gamma_sum'][0]/sums['coefficient_sum'][0]
            residual = math.fsum([sums['coefficient_beta_gamma_sum_squared'][0],
                -2*reference*sums['coefficient_beta_gamma_cross_sum'][0],
                reference**2*sums['coefficient_sum_squared'][0]])
            if residual < -1.e-12*sums['coefficient_beta_gamma_sum_squared'][0]:
                raise ValueError('Full-source boost-reference covariance is inconsistent')
            reference_error = math.sqrt(collisions/(collisions-1)*max(0.,residual))/sums['coefficient_sum'][0]
        contract = dict(schema='shift-dark-photon-gen-v1', model='visible-photon-like-dark-photon',
            production_mechanism=mechanism, mechanism=mechanism,
            mass_gev=args.mass, epsilon=args.epsilon, seed=args.seed, run_number=args.run_number,
            requested_events=slots, maximum_framework_events=slots, decay_mode='mumu', filtering='none',
            source_z_mm=148000., source_sigma_z_mm=500., beam_energy_GeV=6800.,
            beam_settings=beam, common=common, cp5=tune, source_settings=settings,
            native_libraries=native_libraries,
            worker_local_runtime=manifest.get('worker_local_runtime'),
            source_model=card['source'].get('cross_section_model', 'SoftQCD:nonDiffractive; unfiltered CP5 fixed-target pp'),
            generated_mass_support_gev=[args.mass*(1-1.e-6), args.mass*(1+1.e-6)],
            width_authority=authority, physical_width_table=width,
            meson_model_card=card, meson_model_card_sha256=digest(out/'meson_model.json'),
            exclusive_channels=card.get('channels', ['eta->gamma Aprime', 'eta-prime->gamma Aprime']),
            source_exposure=exposure, source_exposure_sha256=digest(out/'source_exposure.json'),
            source_ledger_sha256=digest(out/'meson_source_ledger.jsonl'),
            resolved_parent_models=models,
            source_precision=precision,
            mean_beta_gamma_reference=reference,
            mean_beta_gamma_reference_standard_error=reference_error,
            mean_beta_gamma_reference_error_method='Fixed-source collision covariance; first-order delta method for ratio of means',
            mean_beta_gamma_reference_method='Full-source exclusive-rate-weighted isotropic angular mean; dipion spectrum quadrature where applicable',
            mean_beta_gamma_accepted_closure=math.fsum(boosts)/len(boosts) if boosts else None,
            mean_lab_flight_reference_estimate_m=reference*width['ctau_mm']/1000. if reference is not None else None,
            target_cross_section_pb=source_cross_section_pb(native['sigma_source_mb'],args.epsilon,sums['coefficient_sum'][0],collisions),
            event_hepmc_cross_section_convention='Unfiltered ND source cross section; the physical conditional signal rate is defined only by full-source accounting in contract/audit target_cross_sections',
            cross_section_convention='ND source sigma times full-source mean parent radiative probability and physical dimuon BR once; accepted signal genWeight=1',
            selection_applied=False, detector_simulated=False, physics_valid=False, normalization_ready=False,
            signal_ascii_sha256=digest(out/'signal.hepmc'))
        (out/'contract.json').write_text(json.dumps(contract, indent=2)+'\n')
        if slots == 0:
            raise ValueError('The fixed source exposure produced no signal proposal; retain its zero-yield accounting')
        manifest.update(status='importing audited meson proposal', signal_events=slots,
                        source_collisions=collisions, model_card_sha256=contract['meson_model_card_sha256'])
        with (out/'cmsRun.log').open('w') as log:
            result = subprocess.run(['cmsRun', str(out/'dark_photon_meson_gen_cfg.py'), 'pilotDirectory='+str(out)],
                cwd=out, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout, check=False)
            log.write('\nOriginal native parent-source terminal statistics follow:\n')
            log.write(statistics)
        result.check_returncode()
        with (out/'audit.log').open('w') as log:
            subprocess.run([sys.executable, str(out/'audit_dark_photon_gen.py'), str(out)],
                cwd=out, stdout=log, stderr=subprocess.STDOUT, timeout=600, check=True)
        manifest.update(status='GEN runtime audit passed; meson production physics provisional',
                        output_sha256=digest(out/'gen.root'), validation=json.loads((out/'validation.json').read_text()))
    except Exception as error:
        manifest.update(status='failed', error=repr(error))
        raise
    finally:
        manifest['wall_seconds'] = time.monotonic()-started
        path.write_text(json.dumps(manifest, indent=2)+'\n')
    return dict(events=slots, source_collisions=collisions, output=str(out), status=manifest['status'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mass', type=float, required=True)
    parser.add_argument('--epsilon', type=float, required=True)
    parser.add_argument('--source-collisions', type=int, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--run-number', type=int, required=True)
    parser.add_argument('--model-card', type=Path, required=True)
    parser.add_argument('--darkcast-path', type=Path, required=True)
    parser.add_argument('--darkcast-source-receipt', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime-provenance', type=Path)
    parser.add_argument('--runtime-archive', type=Path)
    print(json.dumps(run(parser.parse_args())))


if __name__ == '__main__':
    main()
