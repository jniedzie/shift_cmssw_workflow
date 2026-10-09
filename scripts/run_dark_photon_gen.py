#!/usr/bin/env python3
"""Generate a new bounded, audited native-Pythia dark-photon GEN pilot.

Requires an already built CMSSW runtime; never builds, submits or overwrites.
Native pure DY is restricted to >=12 GeV until spectral/low-mass sources exist.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from dark_photon_generation import contract, production_settings


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mass', type=float, required=True)
    parser.add_argument('--epsilon', type=float, required=True)
    parser.add_argument('--decay-mode', choices=('mumu', 'inclusive'), default='mumu')
    parser.add_argument('--sampling-epsilon', type=float,
                        help='Resolvable common amplitude scale; physical epsilon and lifetime stay unchanged')
    parser.add_argument('--events', type=int, default=100)
    parser.add_argument('--seed', type=int, default=24681357)
    parser.add_argument('--run-number', type=int)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    production_settings(args.mass, args.epsilon)
    scale = 1. if args.sampling_epsilon is None else args.sampling_epsilon/abs(args.epsilon)
    production_settings(args.mass, args.epsilon, scale)
    if not 1 <= args.events <= 10000 or not 1 <= args.seed <= 899999900 or args.timeout <= 0:
        parser.error('Require bounded events, seed and positive timeout')
    if not shutil.which('cmsRun') or not os.environ.get('CMSSW_BASE'):
        parser.error('Enter the built CMSSW runtime first; no build is performed')
    os.environ['LD_LIBRARY_PATH'] = ':'.join(p for p in os.environ.get('LD_LIBRARY_PATH', '').split(':')
                                          if '/biglib/' not in p)
    from Configuration.Generator.Pythia8CommonSettings_cfi import pythia8CommonSettingsBlock
    from Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi import pythia8CP5SettingsBlock
    from dark_photon_pythia import measure_widths
    from dark_photon_model import validate_native_widths, validate_native_proposal_support
    common = list(pythia8CommonSettingsBlock.pythia8CommonSettings)
    cp5 = list(pythia8CP5SettingsBlock.pythia8CP5Settings)
    cms_environment = os.environ.copy()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = dict(schema='shift-dark-photon-pilot-run-v1', status='preflight',
                    physics_valid=False, normalization_ready=False)
    path = out / 'manifest.json'
    here = Path(__file__).resolve().parent
    for name in ('dark_photon_gen_cfg.py', 'dark_photon_generation.py', 'dark_photon_model.py',
                 'dark_photon_pythia.py', 'audit_dark_photon_gen.py',
                 'pythia_generation_ledger.py', 'fixed_target_generation.py'):
        shutil.copy2(here / name, out / name)
    manifest['source_sha256'] = {p.name: digest(p) for p in out.glob('*.py')}
    base = Path(os.environ['CMSSW_BASE'])
    manifest['cmssw_git_head'] = subprocess.check_output(
        ['git', '-C', str(base / 'src'), 'rev-parse', 'HEAD'], text=True).strip()
    manifest['cmssw_git_status'] = subprocess.check_output(
        ['git', '-C', str(base / 'src'), 'status', '--porcelain'], text=True)
    manifest['pythia_tool'] = subprocess.check_output(['scram', 'tool', 'info', 'pythia8'],
                                                    cwd=base / 'src', text=True)
    path.write_text(json.dumps(manifest, indent=2) + '\n')
    try:
        # Preflight uses all natural signal channels before any forcing.
        width = measure_widths(common + cp5 + production_settings(args.mass, args.epsilon)
                               + ['Beams:frameType = 2', 'Beams:eA = 0.', 'Beams:eB = 6800.'])
        width.update(epsilon=args.epsilon, total_width_gev=width['m_width_gev'],
                     ctau_mm=width['proper_length_mm'],
                     br_mumu=sum(channel['branching_fraction'] for channel in width['channels']
                                 if sorted(channel['products']) == [-13, 13]))
        width['independent_fermion_width_closure'] = validate_native_widths(width, args.epsilon)
        if scale == 1:
            sampling_width = width
        else:
            sampling_width = measure_widths(common + cp5 + production_settings(args.mass, args.epsilon, scale)
                + ['Beams:frameType = 2', 'Beams:eA = 0.', 'Beams:eB = 6800.'])
            sampling_width.update(total_width_gev=sampling_width['m_width_gev'])
        sampling_width['pole_support_audit'] = validate_native_proposal_support(
            sampling_width, .99*args.mass, 1.01*args.mass)
        data = contract(args.mass, args.epsilon, width, args.decay_mode, args.events, args.seed,
                        args.run_number or args.seed, common, cp5, sampling_width, scale)
        (out / 'contract.json').write_text(json.dumps(data, indent=2) + '\n')
        command = ['cmsRun', str(out / 'dark_photon_gen_cfg.py'), 'pilotDirectory=' + str(out)]
        manifest.update(command=command, status='generating')
        path.write_text(json.dumps(manifest, indent=2) + '\n')
        with (out / 'cmsRun.log').open('w') as log:
            subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=args.timeout, env=cms_environment)
        with (out / 'audit.log').open('w') as log:
            subprocess.run([sys.executable, str(out / 'audit_dark_photon_gen.py'), str(out)],
                           cwd=out, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180,
                           env=cms_environment)
        manifest.update(status='GEN runtime audit passed; physics provisional',
                        output_sha256=digest(out / 'gen.root'),
                        validation=json.loads((out / 'validation.json').read_text()))
    except Exception as error:
        manifest.update(status='failed', error=repr(error))
        raise
    finally:
        path.write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(events=args.events, mass_gev=args.mass, epsilon=args.epsilon,
                         output=str(out), status=manifest['status'])))


def reaudit(directory):
    """Revalidate a closed GEN file, retaining original failed audit evidence."""
    out=Path(directory).resolve()
    path=out/'manifest.json';previous=json.loads(path.read_text())
    if previous['status'].startswith('GEN runtime audit passed'):
        raise ValueError('Already validated; no repeat audit requested')
    gen=out/'gen.root'
    if not gen.is_file():
        raise ValueError('No closed signal GEN to audit')
    from run_shift_gen_to_nano import command
    revisions=out/'audit_revisions';revisions.mkdir(exist_ok=True)
    revision=revisions/f'revision{len(list(revisions.iterdir()))+1:04d}'
    revision.mkdir()
    shutil.copy2(path,revision/'previous_manifest.json')
    script=revision/'audit_dark_photon_gen.py'
    shutil.copy2(Path(__file__).resolve().parent/'audit_dark_photon_gen.py',script)
    ledger_script=revision/'pythia_generation_ledger.py'
    shutil.copy2(Path(__file__).resolve().parent/'pythia_generation_ledger.py',ledger_script)
    before=digest(gen)
    with (revision/'audit.log').open('w') as log:
        command([sys.executable,str(script),str(out)],stdout=log,stderr=subprocess.STDOUT,timeout=180)
    if digest(gen)!=before:
        raise ValueError('Signal GEN changed during independent audit')
    previous.update(status='GEN runtime audit passed; physics provisional',output_sha256=before,
                    validation=json.loads((out/'validation.json').read_text()),
                    audit_recovery=dict(script=str(script),sha256=digest(script),
                                        ledger_script_sha256=digest(ledger_script),
                                        previous_manifest_sha256=digest(revision/'previous_manifest.json')))
    path.write_text(json.dumps(previous,indent=2)+'\n')
    print(json.dumps(dict(status=previous['status'],events=previous['validation']['events'],output=str(out))))


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='audit-existing':
        reaudit(sys.argv[2])
    else:
        main()
