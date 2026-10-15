#!/usr/bin/env python3
# Code style: AI-code. AI-generated ordinary mu-enriched GEN publication worker.
"""Generate the preserved ordinary low-bin source, filter and publish exactly once."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import zipfile

from run_shift_weighted_gen import atomic_json, command, publish, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--node', required=True)
    args = parser.parse_args()
    started = time.monotonic()
    result = dict(schema='shift-mu-enriched-ordinary-gen-receipt-v1', complete=False,
                  publication_complete=False, manifest_sha256=args.expected_sha256)
    status = 1
    try:
        if sha(args.manifest) != args.expected_sha256:
            raise ValueError('Frozen ordinary manifest changed')
        manifest = json.loads(Path(args.manifest).read_text())
        request = next(row for row in manifest['jobs'] if row['id'] == args.node)
        result.update(request=request, generator_profile=manifest['generator_profile'],
                      source_inputs=manifest['inputs'])
        for name, digest in manifest['inputs'].items():
            if sha(name) != digest:
                raise ValueError('Frozen ordinary input changed: '+name)
        destination = request['output_base']+'/'+args.node
        check = subprocess.run(['xrdfs', 'root://eosuser.cern.ch', 'stat',
                                destination+'/publication_receipt.json'], capture_output=True, text=True, timeout=120)
        if check.returncode == 0:
            raise ValueError('Existing publication requires audited recovery, never duplicate generation')
        if '[3011]' not in check.stderr+check.stdout and 'No such file' not in check.stderr+check.stdout:
            raise ValueError('Unknown existing publication state: '+check.stderr)
        work = Path('runtime').resolve()
        work.mkdir()
        with tarfile.open('runtime.tar.gz', 'r:gz') as archive:
            archive.extractall(work)
        Path('bootstrap.sh').write_text('''set -euo pipefail
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd runtime/CMSSW_17_0_0_pre4
scram b ProjectRename > ../../relocate.log 2>&1
eval "$(scram runtime -sh)"
cd ../..
python3 -c 'import json,os;json.dump(dict(os.environ),open("runtime_env.json","w"))'
''')
        command(['bash', 'bootstrap.sh'], 'bootstrap.log', timeout=600)
        env = json.loads(Path('runtime_env.json').read_text())
        env.pop('LD_PRELOAD', None)
        driver = ['cmsDriver.py', 'Configuration/GenProduction/python/QCD_SoftMpiPartition_FixedTarget_13p6TeV_pythia8_cff.py',
                  '--step', 'GEN', '--conditions', 'auto:phase1_2023_realistic',
                  '--beamspot', 'Realistic25ns13p6TeVEarly2023Collision', '--datatier', 'GEN',
                  '--eventcontent', 'FEVTDEBUG', '--geometry', 'DB:Extended', '--era', 'Run3_2023',
                  '--fileout', 'file:gen.root', '--python_filename', 'gen_cfg.py', '--no_exec',
                  '-n', str(request['trials'])]
        command(driver, 'driver.log', env, 600)
        low, high = request['bounds']
        count, run, seed = request['trials'], request['run'], request['seed']
        with Path('gen_cfg.py').open('a') as stream:
            stream.write(f'''
from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customiseKeepShiftTruth
process = customiseKeepShiftTruth(process)
process.RandomNumberGeneratorService.generator.initialSeed = cms.untracked.uint32({seed})
process.source.firstRun = cms.untracked.uint32({run})
process.source.firstLuminosityBlock = cms.untracked.uint32(1)
process.source.firstEvent = cms.untracked.uint64(1)
process.source.numberEventsInLuminosityBlock = cms.untracked.uint32({count+1})
process.source.numberEventsInRun = cms.untracked.uint32({count+1})
process.options.numberOfThreads = cms.untracked.uint32(1)
process.options.numberOfStreams = cms.untracked.uint32(1)
process.MessageLogger.cerr.FwkReport.reportEvery = cms.untracked.int32(10000)
process.generator.UserCustomization[0].pTHatMin = cms.double({low})
process.generator.UserCustomization[0].pTHatMax = cms.double({high})
from qcd_mu_enrichment import configure
process = configure(process)
''')
        atomic_json('request.json', request)
        command(['edmConfigDump', 'gen_cfg.py'], 'resolved_gen_cfg.py', env, 600)
        then = time.monotonic()
        command(['cmsRun', 'gen_cfg.py'], 'cmsRun.log', env, request.get('timeout_seconds', 3600))
        result['generation_seconds'] = time.monotonic()-then
        command(['python3', 'audit_mu_enriched_qcd_gen.py', '--input', 'gen.root',
                 '--config', 'resolved_gen_cfg.py', '--log', 'cmsRun.log',
                 '--request', 'request.json', '--output', 'semantic_audit.json'], 'audit.log', env, 900)
        audit = json.loads(Path('semantic_audit.json').read_text())
        result.update(generated_edm=True, event_count=audit['events'], semantic_audit=audit,
                      physics_valid=False, normalization_ready=False)
        result['artifacts'] = {name:dict(sha256=sha(name), bytes=Path(name).stat().st_size)
                               for name in ('gen.root', 'resolved_gen_cfg.py', 'semantic_audit.json', 'cmsRun.log')}
        if Path('gen.root').stat().st_size*2+32000000 > manifest['peak_bytes_per_worker']:
            raise ValueError('Measured ROOT/readback peak exceeds frozen storage reservation')
        result['publication'] = publish([(Path(name), name) for name in
            ('gen.root', 'resolved_gen_cfg.py', 'semantic_audit.json', 'cmsRun.log')], destination)
        result.update(complete=True, publication_complete=True, publication_directory=destination)
        atomic_json('publication_receipt.json', result)
        publish([(Path('publication_receipt.json'), 'publication_receipt.json')], destination)
        status = 0
    except Exception as error:
        result.update(complete=False, publication_complete=False,
                      error=repr(error), error_type=type(error).__name__)
        print('Ordinary mu-enriched GEN failed:', repr(error), file=sys.stderr)
    finally:
        result.update(exit_status=status, wall_seconds=time.monotonic()-started)
        atomic_json('result.json', result)
        with zipfile.ZipFile('artifacts.zip', 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for pattern in ('*.log', '*_cfg.py', '*.json'):
                for path in sorted(Path('.').glob(pattern)):
                    if path.is_file():
                        archive.write(path, path.name)
            if Path('gen.root').is_file() and not result.get('publication_complete'):
                archive.write('gen.root')
    return status


if __name__ == '__main__':
    sys.exit(main())
