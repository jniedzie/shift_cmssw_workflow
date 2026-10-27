#!/usr/bin/env python3
# Code style: AI-code. Adapter to the published SensCalc-associated PYTHIA model.
"""Build and sample the unchanged published DPMixing production prescription.

This produces stable dark-photon source spectra, not CMSSW GEN or reconstructed
samples. Collision settings are explicit inputs; never reuse an LHC or SPS
spectrum as the SHIFT spectrum. Weights follow the upstream seed-probability
prescription and exclude epsilon squared, the dimuon branching and acceptance.
The existing detailed meson model remains a separate alternative.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

PYTHIA_COMMIT = '805f5bd0223dfe8d218481c82ad6bc2f03868bb8'
ARCHIVE_SHA256 = '3b6255e81e3130dcfc974d971470dd57ca34aef0d3eaac93a98257f63125f69a'
SENSCALC_COMMIT = 'ca588864742ee83ae8fd3a987b9698ea75c19251'
WIDTH_SHA256 = '2f2582830fcd6a6e97d9a8264e3b4ca9e267a668fba6d7ffa2b4f7ea0f7322da'
CHANNELS = {4900111:'pi0-decay', 4900221:'eta-decay', 4900331:'eta-prime-decay',
            4900113:'rho-fragmentation', 4900223:'omega-fragmentation',
            4900333:'phi-fragmentation'}
REFERENCES = ['https://arxiv.org/abs/2409.11096', 'https://arxiv.org/abs/2504.06828',
              'https://gitlab.com/YehorKyselyov/pythia-mixing/-/tree/'+PYTHIA_COMMIT]


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def write_json(path,data):
    Path(path).write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')


def shift_source_settings(model_card,output):
    """Use the existing fixed-target/CP5 source, with the upstream QED option.

    The old release supplies ParticleDecays:allowPhotonRadiation rather than
    HadronLevel:QED. Record that generator prescription explicitly. Detector
    settings and the existing detailed-model card are not edited.
    """
    from run_dark_photon_meson_gen import source_settings
    card=json.loads(Path(model_card).read_text())
    if ({row['pdg_id'] for row in card['parents']}!={221,331} or
            any(row.get('decay_type') is not None for row in card['parents'])):
        raise ValueError('Use the eta/eta-prime source card, without additional model-specific source overrides')
    resolved=source_settings(card)
    commands=[command.replace('HadronLevel:QED','ParticleDecays:allowPhotonRadiation')
              for command in resolved['source_settings']]
    output=Path(output).resolve()
    with output.open('x') as stream:stream.write('\n'.join(commands)+'\n')
    return dict(model_card_sha256=digest(model_card),settings_sha256=digest(output),
        beam_settings=resolved['beam_settings'],qed_prescription='Upstream PYTHIA8.311 ParticleDecays:allowPhotonRadiation')


def build_runtime(archive,output,*,jobs=8,lhapdf=None):
    """Private build from the pinned public archive; never rebuild CMSSW."""
    if digest(archive)!=ARCHIVE_SHA256:raise ValueError('Unexpected upstream archive SHA-256')
    if not 1<=jobs<=32:raise ValueError('Specify 1--32 build workers')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    receipt=dict(schema='shift-senscalc-source-runtime-v1',complete=False,
        upstream_commit=PYTHIA_COMMIT,archive_sha256=ARCHIVE_SHA256,references=REFERENCES)
    try:
        with tarfile.open(archive) as source:
            if sum(member.size for member in source.getmembers())>600*1024**2:
                raise ValueError('Unexpected upstream archive expansion')
            source.extractall(output,filter='data')
        upstream=output/('pythia-mixing-'+PYTHIA_COMMIT)
        worker=Path(__file__).with_name('dark_photon_senscalc_source.cc')
        shutil.copy2(worker,output/worker.name)
        configure=['./configure','--cxx='+shutil.which('g++'),
                   '--cxx-common=-O2 -std=c++17 -fPIC -pthread']
        if lhapdf is not None:configure.append('--with-lhapdf6='+str(Path(lhapdf).resolve()))
        with (output/'build.log').open('w') as log:
            subprocess.run(configure,cwd=upstream,stdout=log,stderr=subprocess.STDOUT,check=True)
            subprocess.run(['make','-j'+str(jobs)],cwd=upstream,stdout=log,stderr=subprocess.STDOUT,check=True)
            subprocess.run(['g++','-std=c++17','-O2','-pthread','-Iinclude','-Iexamples',
                str(output/worker.name),'examples/MixingPattern.cc','examples/Interpolation.cc',
                '-Llib','-Wl,-rpath,'+str(upstream/'lib'),'-lpythia8','-o',str(output/'source')],
                cwd=upstream,stdout=log,stderr=subprocess.STDOUT,check=True)
        # Freeze the actual deployed bytes, including XML settings and PDF plugin.
        paths=[output/'source',output/worker.name]+list((upstream/'lib').glob('*.so'))
        for folder in ('xmldoc','pdfdata','settings'):
            paths+=[path for path in (upstream/'share/Pythia8'/folder).rglob('*') if path.is_file()]
        receipt.update(complete=True,upstream_directory=upstream.name,
            files={str(path.relative_to(output)):digest(path) for path in paths},
            lhapdf_directory=str(lhapdf) if lhapdf is not None else None)
    finally:write_json(output/'runtime.json',receipt)
    return receipt


def checked_runtime(directory):
    directory=Path(directory).resolve();receipt=json.loads((directory/'runtime.json').read_text())
    if (receipt.get('schema')!='shift-senscalc-source-runtime-v1' or
            receipt.get('complete') is not True or receipt.get('upstream_commit')!=PYTHIA_COMMIT or
            receipt.get('archive_sha256')!=ARCHIVE_SHA256):
        raise ValueError('Require the completed pinned SensCalc-associated source runtime')
    root=receipt.get('upstream_directory','')
    required={'source',root+'/lib/libpythia8.so'}
    required.update(root+'/share/Pythia8/xmldoc/'+name for name in
                    ('Index.xml','ParticleData.xml','HadronWidths.dat','NucleonExcitations.dat'))
    if (not root or not required.issubset(receipt['files']) or
            not any(name.startswith(root+'/share/Pythia8/pdfdata/') for name in receipt['files'])):
        raise ValueError('Incomplete runtime: include the PYTHIA library, XML, hadron tables and auxiliary PDF data')
    for name,expected in receipt['files'].items():
        path=(directory/name).resolve()
        if not path.is_relative_to(directory) or digest(path)!=expected:
            raise ValueError('Changed frozen reference runtime: '+name)
    return directory,receipt


def source_summary(rows,sigma_mb,*,mass=None):
    """Count independent source collisions, including zero-candidate trials.

    Several candidates from one collision share one exposure. The error on
    the inclusive rate uses collision sums, not independent-candidate errors.
    Failed generator calls are retained and prevent rate qualification.
    """
    totals=[];sum_squares=0.;boost_sum=0.;count=0;failed=0
    by_channel=defaultdict(lambda:dict(candidates=0,sum_weights_over_epsilon_squared=0.))
    for trial,row in enumerate(rows,1):
        if row.get('trial')!=trial:raise ValueError('Require every original source trial in order')
        candidates=row.get('candidates')
        if not isinstance(candidates,list):raise ValueError('Missing source candidate inventory')
        if row.get('generator_success') is not True:
            if row.get('generator_success') is not False or candidates:
                raise ValueError('Invalid failed generator receipt')
            failed+=1;continue
        if row.get('native_weight')!=1.:raise ValueError('Expected unit-weight source collisions')
        weights=[]
        for candidate in candidates:
            channel=CHANNELS.get(candidate['id']);weight=candidate['weight_over_epsilon_squared']
            p4=candidate['p4']
            if (channel is None or not math.isfinite(weight) or weight<0 or len(p4)!=4 or
                    not all(math.isfinite(value) for value in p4) or p4[0]<=0):
                raise ValueError('Invalid reference candidate or weight')
            momentum=math.hypot(*p4[1:]);mass2=(p4[0]-momentum)*(p4[0]+momentum)
            if mass2<=0:raise ValueError('Non-timelike reference signal')
            if mass is not None:
                tolerance=64*math.ulp(1.)*(p4[0]**2+momentum**2)+1.e-9*mass**2
                if abs(mass2-mass**2)>tolerance:raise ValueError('Reference signal mass differs from requested point')
            weights.append(weight);sum_squares+=weight**2
            boost_sum+=weight*momentum/(mass if mass is not None else math.sqrt(mass2));count+=1
            by_channel[channel]['candidates']+=1
            by_channel[channel]['sum_weights_over_epsilon_squared']+=weight
        totals.append(math.fsum(weights))
    n=len(totals)
    if n<2 or not math.isfinite(sigma_mb) or sigma_mb<=0:
        raise ValueError('At least two source collisions and a positive source cross section required')
    total=math.fsum(totals);mean=total/n
    error=math.sqrt(math.fsum((value-mean)**2 for value in totals)/(n*(n-1)))
    factor=sigma_mb*1.e9/n
    for row in by_channel.values():
        row['sigma_over_epsilon_squared_pb']=factor*row['sum_weights_over_epsilon_squared'] if not failed else None
    return dict(source_calls=n+failed,source_collisions=n,generator_failures=failed,
        rate_qualified=failed==0,candidates=count,
        sum_weights_over_epsilon_squared=total,
        sigma_source_mb=sigma_mb,sigma_over_epsilon_squared_pb=factor*total if not failed else None,
        sigma_statistical_error_over_epsilon_squared_pb=sigma_mb*1.e9*error if not failed and count else None,
        mean_beta_gamma=boost_sum/total if total else None,
        candidate_effective_statistics=total**2/sum_squares if sum_squares else 0.,
        channels=dict(by_channel),dimuon_branching_included=False,decay_acceptance_included=False,
        scope='Upstream seed-probability production benchmark; generator-statistical errors only')


def sample_source(runtime,settings,mass,trials,seed,output,*,probability=.01):
    """Run a bounded stable-LLP cohort with a full original-collision ledger."""
    if (not math.isfinite(mass) or not .211316751<mass<1. or not 2<=trials<=100000 or
            not 1<=seed<=899999999 or not 1.e-6<=probability<=.01 or
            not math.isclose(probability*1.e6,round(probability*1.e6),abs_tol=1.e-8)):
        raise ValueError('Open sub-GeV dimuon point, bounded trials, seed and representable proposal required')
    runtime,receipt=checked_runtime(runtime)
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    shutil.copy2(settings,output/'source_settings.cmnd')
    manifest=dict(schema='shift-senscalc-source-sample-v1',complete=False,
        mass_gev=mass,seed=seed,source_trials=trials,proposal_probability=probability,
        runtime_receipt_sha256=digest(runtime/'runtime.json'),
        settings_sha256=digest(output/'source_settings.cmnd'),references=REFERENCES,
        stage='stable-dark-photon-source',cmssw_gen_produced=False,detector_simulated=False)
    try:
        environment=dict(os.environ)
        environment.pop('PYTHIA8DATA',None)
        environment['LD_LIBRARY_PATH']=str(runtime/receipt['upstream_directory']/'lib')+':'+environment.get('LD_LIBRARY_PATH','')
        with (output/'source.log').open('w') as log:
            subprocess.run([str(runtime/'source'),str(runtime/receipt['upstream_directory']/'share/Pythia8/xmldoc'),
                str(output/'source_settings.cmnd'),str(mass),str(trials),str(seed),
                str(output/'source_ledger.jsonl'),str(probability)],
                stdout=log,stderr=subprocess.STDOUT,timeout=1800,check=True,cwd=runtime,env=environment)
        records=[line.removeprefix('RESULT ') for line in (output/'source.log').read_text().splitlines()
                 if line.startswith('RESULT ')]
        if len(records)!=1:raise ValueError('Missing unique terminal source statistics')
        native=json.loads(records[0])
        with (output/'source_ledger.jsonl').open() as stream:
            summary=source_summary((json.loads(line) for line in stream),native['sigma_source_mb'],mass=mass)
        if (summary['source_calls']!=trials or summary['source_collisions']!=native['source_collisions'] or
                summary['candidates']!=native['candidates'] or
                summary['generator_failures']!=native['generator_failures'] or
                not math.isclose(summary['sum_weights_over_epsilon_squared'],
                    native['sum_weights_over_epsilon_squared'],rel_tol=1.e-12,abs_tol=1.e-12)):
            raise ValueError('Terminal source counters disagree with full source ledger')
        manifest.update(complete=True,native=native,summary=summary,
            ledger_sha256=digest(output/'source_ledger.jsonl'))
    finally:write_json(output/'manifest.json',manifest)
    return manifest


def total_width(table,mass,epsilon):
    """SensCalc DP.nb: log-log linear interpolation of Gamma/epsilon²."""
    if digest(table)!=WIDTH_SHA256:raise ValueError('Unexpected pinned SensCalc width table')
    if not math.isfinite(mass) or not math.isfinite(epsilon) or not 0<epsilon<=.01:
        raise ValueError('Finite mass and small positive physical epsilon required')
    rows=[tuple(map(float,line.split())) for line in Path(table).read_text().splitlines() if line.strip()]
    if not rows[0][0]<=mass<=rows[-1][0]:raise ValueError('Mass outside pinned width-table coverage')
    for (low,a),(high,b) in zip(rows,rows[1:]):
        if low<=mass<=high:
            fraction=math.log(mass/low)/math.log(high/low)
            return epsilon**2*math.exp(math.log(a)+fraction*math.log(b/a))
    raise ValueError('Missing width interpolation bracket')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    build=commands.add_parser('build')
    build.add_argument('--archive',type=Path,required=True)
    build.add_argument('--output',type=Path,required=True)
    build.add_argument('--jobs',type=int,default=8)
    build.add_argument('--lhapdf',type=Path)
    sample=commands.add_parser('sample')
    sample.add_argument('--runtime',type=Path,required=True)
    sample.add_argument('--settings',type=Path,required=True)
    sample.add_argument('--mass',type=float,required=True)
    sample.add_argument('--trials',type=int,required=True)
    sample.add_argument('--seed',type=int,required=True)
    sample.add_argument('--output',type=Path,required=True)
    sample.add_argument('--probability',type=float,default=.01)
    settings=commands.add_parser('settings')
    settings.add_argument('--model-card',type=Path,required=True)
    settings.add_argument('--output',type=Path,required=True)
    args=vars(parser.parse_args());command=args.pop('command')
    if command=='build':result=build_runtime(**args)
    elif command=='settings':result=shift_source_settings(**args)
    else:result=sample_source(**args)
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=='__main__':main()
