#!/usr/bin/env python3
"""Replay at most 20 audited signal GEN events with the frozen background chain.

No build, scheduler submission, publication or signal-dependent reco tuning.
This is a bounded software/transport pilot, not an efficiency denominator.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from run_shift_gen_to_nano import sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gen_directory',type=Path)
    parser.add_argument('--count',type=int,default=2)
    parser.add_argument('--skip',type=int,default=0)
    parser.add_argument('--job',type=int,default=900001)
    parser.add_argument('--templates',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=10800)
    args=parser.parse_args()
    if not 1 <= args.count <= 20 or args.skip < 0 or args.timeout <= 0:
        parser.error('Require 1..20 events, nonnegative skip and positive timeout')
    if not shutil.which('cmsRun') or not os.environ.get('CMSSW_BASE'):
        parser.error('Enter the prepared CMSSW runtime; no build is performed')
    gen_dir=args.gen_directory.resolve()
    manifest=json.loads((gen_dir/'manifest.json').read_text())
    contract=json.loads((gen_dir/'contract.json').read_text())
    audit=json.loads((gen_dir/'validation.json').read_text())
    if not audit.get('runtime_validated') or not manifest['status'].startswith('GEN runtime audit passed'):
        raise ValueError('Signal source lacks completed GEN runtime validation')
    if args.skip+args.count > audit['events'] or sha(gen_dir/'gen.root') != manifest['output_sha256']:
        raise ValueError('Invalid slice or changed GEN payload')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    scripts=Path(__file__).resolve().parent
    for name in ('run_shift_gen_to_nano.py','shift_muon_decay_replay.py','audit_dark_photon_gen.py',
                 'pythia_generation_ledger.py'):
        shutil.copy2(scripts/name,out/name)
    templates=out/'templates';templates.mkdir()
    frozen_templates={}
    for stage in range(1,5):
        source=args.templates.resolve()/f'step{stage}.py'
        shutil.copy2(source,templates/source.name)
        frozen_templates[str(stage)]=dict(source=str(source),sha256=sha(source))
    descriptor=dict(schema='shift-bsm-pilot-source-v1',gen_transport='local',
        gen=str(gen_dir/'gen.root'),gen_sha256=manifest['output_sha256'],
        receipt=str(gen_dir/'manifest.json'),receipt_sha256=sha(gen_dir/'manifest.json'),
        stratum='darkphoton_dy_m'+str(contract['mass_gev']),event_ids=audit['event_ids'],
        weights=[row['weight'] for row in audit['event_rows']],
        output_base=str(out),complete_muon_decays=False,missing_filter_lumi=True,
        signal_contract_sha256=sha(gen_dir/'contract.json'),
        source_full_graph_sha256=[row['full_graph_sha256'] for row in audit['event_rows']])
    (out/'source.json').write_text(json.dumps(descriptor,indent=2)+'\n')
    wrapper=dict(schema='shift-dark-photon-detector-pilot-v1',complete=False,
        physics_valid=False,normalization_ready=False,signal_contract=contract,
        source_manifest_sha256=sha(gen_dir/'manifest.json'),templates=frozen_templates,
        requested_event_ids=audit['event_ids'][args.skip:args.skip+args.count],
        scope='bounded no-pileup/Fake2-HLT background-chain replay; no final recorded-event efficiency')
    path=out/'signal_report.json';path.write_text(json.dumps(wrapper,indent=2)+'\n')
    command=[sys.executable,str(out/'run_shift_gen_to_nano.py'),str(out/'source.json'),
        '--skip',str(args.skip),'--count',str(args.count),'--job',str(args.job),
        '--templates',str(templates)]
    try:
        with (out/'worker.log').open('w') as log:
            subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT,
                           check=True,timeout=args.timeout)
        report=json.loads((out/'report.json').read_text())
        if not report['complete']:
            raise ValueError('Common detector worker did not complete')
        # The separate audit runs in a fresh interpreter to preserve CMS loader
        # state and compares the full source graph through all EDM tiers.
        with (out/'signal_audit.log').open('w') as log:
            subprocess.run([sys.executable,str(Path(__file__).resolve()),'audit-replay',str(out)],
                           cwd=out,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
        wrapper.update(complete=True,common_report_sha256=sha(out/'report.json'),
                       signal_audit=json.loads((out/'signal_audit.json').read_text()))
    except Exception as error:
        wrapper.update(error=repr(error))
        raise
    finally:
        path.write_text(json.dumps(wrapper,indent=2)+'\n')
    print(json.dumps(dict(complete=True,events=args.count,output=str(out))))


def audit_replay(directory):
    import ROOT
    from DataFormats.FWLite import Events
    from audit_dark_photon_gen import graph_hash, genparticle_hash, read_product
    out=Path(directory)
    source=json.loads((out/'source.json').read_text())
    report=json.loads((out/'signal_report.json').read_text())
    expected=report['requested_event_ids']
    known={tuple(i):h for i,h in zip(source['event_ids'],source['source_full_graph_sha256'])}
    known_particles={}
    for event in Events(str(out/'gen.root')):
        aux=event.eventAuxiliary()
        identity=(int(aux.run()),int(aux.luminosityBlock()),int(aux.event()))
        if list(identity) in expected:
            known_particles[identity]=genparticle_hash(event)
    tiers={}
    for stage in (1,2,3):
        identities=[];hep_checked=0;particles_checked=0
        for event in Events(str(out/f'step{stage}.root')):
            aux=event.eventAuxiliary()
            identity=[int(aux.run()),int(aux.luminosityBlock()),int(aux.event())]
            hep=None
            for label in ('shiftEventTime','generatorSmeared'):
                try:
                    hep=read_product(event,label,'edm::HepMCProduct').GetEvent()
                    break
                except ValueError as error:
                    if str(error)!='Missing product: '+label:
                        raise
            if hep is not None:
                if graph_hash(hep)!=known[tuple(identity)]:
                    raise ValueError('Detector replay changed signal graph/spacetime/weights')
                hep_checked+=1
            elif stage==1:
                raise ValueError('Simulation input tier lacks the complete signal graph')
            if genparticle_hash(event)!=known_particles[tuple(identity)]:
                raise ValueError('Detector tier changed generated ancestry/momenta/vertices')
            particles_checked+=1
            identities.append(identity)
        if identities!=expected:
            raise ValueError('Signal graph tier identity mismatch')
        tiers[str(stage)]=dict(events=len(identities),full_hepmc_graph_checked=hep_checked,
                              genparticle_graph_checked=particles_checked,
                              standard_content_scope='Full HepMC at SIM; retained exact GenParticle graph downstream')
    root=ROOT.TFile.Open(str(out/'nano.root'))
    if not root or root.IsZombie() or root.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Invalid signal NanoAOD')
    tree=root.Get('Events');parents=0;muon_links=0
    if not tree.GetBranch('GenPart_pdgId') or not tree.GetBranch('GenPart_genPartIdxMother'):
        raise ValueError('Signal NanoAOD lacks truth ancestry')
    for event in tree:
        pdgs=list(event.GenPart_pdgId);mothers=list(event.GenPart_genPartIdxMother)
        current=sum(abs(pdg)==32 for pdg in pdgs)
        if current==0:
            raise ValueError('Nano pruning removed the signal parent')
        parents+=current
        current_links=sum(abs(pdg)==13 and 0<=mother<len(pdgs) and abs(pdgs[mother])==32
                          for pdg,mother in zip(pdgs,mothers))
        if report['signal_contract']['decay_mode']=='mumu' and current_links<2:
            raise ValueError('NanoAOD lacks the two direct dimuon-to-signal ancestry links')
        muon_links+=current_links
    root.Close()
    result=dict(edm_tiers=tiers,nano_signal_parent_rows=parents,
                nano_direct_muon_parent_links=muon_links,physics_valid=False)
    (out/'signal_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    if (out/'report.json').is_file() and json.loads((out/'report.json').read_text()).get('complete'):
        if not report.get('complete') and not (out/'signal_report_previous_audit.json').exists():
            shutil.copy2(out/'signal_report.json',out/'signal_report_previous_audit.json')
        if 'error' in report:
            report['prior_audit_error']=report.pop('error')
        report.update(complete=True,common_report_sha256=sha(out/'report.json'),signal_audit=result,
                      audit_script_sha256=sha(Path(__file__).resolve()),
                      graph_audit_script_sha256=sha(Path(__file__).resolve().parent/'audit_dark_photon_gen.py'))
        (out/'signal_report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='audit-replay':
        audit_replay(sys.argv[2])
    else:
        main()
