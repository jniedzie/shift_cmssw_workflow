#!/usr/bin/env python3
# Code style: AI-code. AI-generated explicit bounded weighted-GEN preparation.
"""Freeze the restored mu-enriched profile for the normal weighted GEN worker.

Never builds, submits, or overwrites. The source draws and prefilter trial
denominator stay distinct from the externally filtered saved GEN events.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from qcd_mu_enrichment import PROFILE, SETTINGS


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(output, assets, profile_path, trials, run, seed, lower, upper):
    profile_path, assets = Path(profile_path).resolve(), Path(assets).resolve()
    profile = json.loads(profile_path.read_text())
    if (profile.get('generator_profile') != PROFILE or profile.get('sample') != 'qcd'
            or profile.get('decay_settings') != list(SETTINGS) or profile.get('requires_scientific_gate') is not True):
        raise ValueError('Unsupported or modified mu-enriched source profile')
    if not 2 <= trials <= 20000 or not 30000000 <= run <= 4294967295 or not 1 <= seed <= 899999999:
        raise ValueError('Invalid bounded trial budget, run or seed')
    if lower <= 0 or not (upper == -1 or upper > lower):
        raise ValueError('Weighted source requires a positive lower edge and a half-open bin')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    scripts = Path(__file__).resolve().parent
    for name in ('runtime.tar.gz', 'pluginShiftMpiWeightedProposalHook.so', 'libShiftMpiTailProposal.so'):
        shutil.copy2(assets/name, output/name)
    for name in ('run_shift_weighted_gen.py', 'audit_shift_weighted_gen.py', 'qcd_mu_enrichment.py',
                 'generation_publication.py', 'soft_mpi_model.py'):
        shutil.copy2(scripts/name, output/name)
    shutil.copy2(profile_path, output/'generator_profile.json')
    inputs = {path.name: sha(path) for path in sorted(output.iterdir()) if path.is_file()}
    row = dict(id='mu_enriched_qcd_pilot', stratum='qcd_mu_enriched', sample='qcd',
               bounds=[lower, upper], trials=trials, run=run, seed=seed,
               stage='pilot', chunk_index=0, publish=False, timeout_seconds=600)
    manifest = dict(schema='shift-weighted-high-bin-manager-v2',
        generator_profile=PROFILE, tag='qcd_mu_enriched_bounded_gen',
        source_model_settings_sha256='a6bb0fd762c5805f1cb2e08c50e713d53ae41a20399adb9c748ea91fb7f8e50e',
        algorithm='independent-geometric-full-support-tail-channel-retry-rb-v4',
        inputs=inputs, jobs=[row], eos_base='', peak_bytes_per_worker=1000000000,
        physics_valid=False, normalization_ready=False, prepared_stage='pilot')
    manifest_path = output/'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    command = ['python3', 'run_shift_weighted_gen.py', '--manifest', 'manifest.json',
               '--expected-sha256', sha(manifest_path), '--node', row['id']]
    receipt = dict(initialdir=str(output), command=command, submitted=False,
                   generator_profile=PROFILE, manifest_sha256=sha(manifest_path))
    (output/'prepared.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--assets', required=True)
    parser.add_argument('--profile', default=str(Path(__file__).resolve().parents[1]/'config/campaigns/qcd_mu_enriched_soft_mpi_gen_v1.json'))
    parser.add_argument('--trials', type=int, default=2000)
    parser.add_argument('--run', type=int, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--lower', type=float, default=20.)
    parser.add_argument('--upper', type=float, default=-1.)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, args.assets, args.profile, args.trials,
                             args.run, args.seed, args.lower, args.upper), indent=2))
