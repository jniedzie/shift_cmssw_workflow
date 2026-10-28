#!/usr/bin/env python3
# Code style: AI-code. Controlled muon-only GEN response probes.
"""Write and qualify two independently specified, already-timed OS muons.

These probes measure conditional detector response. They contain no hadronic
event, parent survival, physical cross section or additional SM exposure.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import resource
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_probe(probe):
    if probe.get('schema')!='shift-two-muon-response-probe-v1':
        raise ValueError('Require the explicit muon-only response-probe schema')
    if probe.get('timing')!='already-timed-CMS-frame':
        raise ValueError('Birth fourth coordinates must already be physical CMS ct in mm')
    if (probe.get('diagnostic_only') is not True or probe.get('new_GEN_exposure')!=0
            or probe.get('physical_cross_section_pb') is not None or probe.get('probe_weight')!=1.):
        raise ValueError('Require a unit-weight diagnostic with zero SM exposure and no physical cross section')
    identity=probe['event_id']
    if (len(identity)!=3 or any(type(v) is not int or not 1<=v<=limit
            for v,limit in zip(identity,(2**32-1,2**32-1,2**64-1)))):
        raise ValueError('Require a complete positive EDM event identity')
    muons=probe['muons']
    if (len(muons)!=2 or any(type(m['pdg']) is not int for m in muons)
            or sorted(m['pdg'] for m in muons)!=[-13,13]):
        raise ValueError('Require exactly two opposite-sign muons')
    if len({m['barcode'] for m in muons})!=2:
        raise ValueError('Muon barcodes must differ')
    for muon in muons:
        if type(muon['barcode']) is not int or not 0<muon['barcode']<2**31:
            raise ValueError('Require positive signed32 HepMC particle barcodes')
        for key,count in [('momentum_GeV',3),('birth_mm',4)]:
            values=muon[key]
            if len(values)!=count or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
                raise ValueError('Require finite '+key)
        energy=muon['energy_GeV'];mass=muon['generated_mass_GeV']
        if (not math.isfinite(energy) or not math.isfinite(mass) or mass<=0
                or energy<math.hypot(*muon['momentum_GeV']) or math.hypot(*muon['momentum_GeV'])<=0):
            raise ValueError('Require positive moving, timelike muon input without repairing its p4')
        squares=[energy*energy]+[p*p for p in muon['momentum_GeV']]+[mass*mass]
        residual=math.fsum([squares[0]]+[-x for x in squares[1:]])
        # Allow cancellation from a correctly generated boosted on-shell p4,
        # using the same propagated-double-precision principle as preflight.
        unit_roundoff=.5*sys.float_info.epsilon
        gamma8=8.*unit_roundoff/(1.-8.*unit_roundoff)
        if not all(math.isfinite(x) for x in squares) or abs(residual)>gamma8*math.fsum(squares):
            raise ValueError('Muon p4 and generated mass disagree beyond propagated precision')
    return probe


def require_probe_units(hep):
    from shift_gen_material_features import hepmc_unit_names
    momenta,lengths=hepmc_unit_names()
    if (momenta.get(int(hep.momentum_unit()))!='GEV'
            or lengths.get(int(hep.length_unit()))!='MM'):
        raise ValueError('Exact probe extraction and readback require native GEV/MM HepMC units')


def write_hepmc(path,probe):
    """Use stock HepMC2 IO_GenEvent text, with one independent vertex per muon."""
    validate_probe(probe)
    number=probe['event_id'][2]
    if number>=2**31:
        raise ValueError('The stock ASCII event number is signed32; use a small explicit probe ID')
    lines=['HepMC::Version 2.06.11','HepMC::IO_GenEvent-START_EVENT_LISTING',
           f'E {number} -1 -1 -1 -1 20 -1 2 0 0 0 1 1','U GEV MM']
    encode=lambda values:' '.join(format(float(v),'.17g') for v in values)
    for index,muon in enumerate(probe['muons'],1):
        lines.append(f'V {-index} 0 '+encode(muon['birth_mm'])+' 0 1 0')
        lines.append(f"P {muon['barcode']} {muon['pdg']} "+encode(muon['momentum_GeV']+
            [muon['energy_GeV'],muon['generated_mass_GeV']])+' 1 0 0 0 0')
    lines.append('HepMC::IO_GenEvent-END_EVENT_LISTING')
    Path(path).write_text('\n'.join(lines)+'\n')


def probe_from_gen(path,event_id,barcodes,expected_sha256):
    """Extract exact selected HepMC p4/births, including the existing physical ct."""
    if digest(path)!=expected_sha256:
        raise ValueError('Retained full-event GEN checksum changed')
    from DataFormats.FWLite import Events,Handle
    matches=[];events=Events(str(path))
    for index,event in enumerate(events):
        aux=event.eventAuxiliary();identity=[int(aux.run()),int(aux.luminosityBlock()),int(aux.event())]
        if identity==event_id:matches.append(index)
    if len(matches)!=1:
        raise ValueError('Require one exact original event in the supplied GEN file')
    events.to(matches[0]);event=events
    handle=Handle('edm::HepMCProduct');event.getByLabel('shiftEventTime',handle)
    if not handle.isValid():raise ValueError('Require retained already-timed shiftEventTime HepMC')
    hep=handle.product().GetEvent();require_probe_units(hep);muons=[]
    for barcode in barcodes:
        p=hep.barcode_to_particle(barcode)
        if not p or p.status()!=1 or abs(p.pdg_id())!=13 or not p.production_vertex():
            raise ValueError('Selected barcode is not an original stable muon with a birth vertex')
        q=p.momentum();v=p.production_vertex().position()
        muons.append(dict(barcode=barcode,pdg=int(p.pdg_id()),momentum_GeV=[q.px(),q.py(),q.pz()],
            energy_GeV=q.e(),generated_mass_GeV=p.generated_mass(),birth_mm=[v.x(),v.y(),v.z(),v.t()]))
    timing_metadata={}
    for name in ['sourceXmm','sourceYmm','sourceZmm','sourceCtBeforeMm','appliedShiftCtMm','appliedShiftNs','phaseNs']:
        value=Handle('double');event.getByLabel('shiftEventTime',name,value)
        timing_metadata[name]=float(value.product()[0]) if value.isValid() else None
    weight=Handle('GenEventInfoProduct');event.getByLabel('generator',weight)
    if not weight.isValid():raise ValueError('Original GEN event weight is unavailable')
    probe=dict(schema='shift-two-muon-response-probe-v1',event_id=list(event_id),muons=muons,
        timing='already-timed-CMS-frame',diagnostic_only=True,new_GEN_exposure=0,
        physical_cross_section_pb=None,probe_weight=1.,original_native_weight=float(weight.product().weight()),
        source_reference=dict(path=str(Path(path).resolve()),sha256=expected_sha256,
            event_id=list(event_id),selected_barcodes=list(barcodes),timing_metadata=timing_metadata),
        scope='Muon-only conditional response; no parent survival or full-QCD occupancy closure')
    return validate_probe(probe)


def build_process(probe,hepmc_path,output_path):
    """Use native stock input/copy/truth modules; apply no timing or vertex shift."""
    validate_probe(probe)
    import FWCore.ParameterSet.Config as cms
    from Configuration.Eras.Era_Run3_2023_cff import Run3_2023
    process=cms.Process('SHIFTMUPROBE',Run3_2023)
    process.load('Configuration.StandardSequences.Services_cff')
    process.load('SimGeneral.HepPDTESSource.pythiapdt_cfi')
    run,lumi,event=probe['event_id']
    process.source=cms.Source('MCFileSource',fileNames=cms.untracked.vstring('file:'+str(Path(hepmc_path).resolve())),
        firstRun=cms.untracked.uint32(run),firstLuminosityBlock=cms.untracked.uint32(lumi),
        firstLuminosityBlockForEachRun=cms.untracked.VLuminosityBlockID(),firstEvent=cms.untracked.uint64(event))
    process.maxEvents=cms.untracked.PSet(input=cms.untracked.int32(1))
    process.options=cms.untracked.PSet(numberOfThreads=cms.untracked.uint32(1),numberOfStreams=cms.untracked.uint32(1))
    # The same source/weight aliases are used by the established meson importer.
    process.generator=cms.EDAlias(source=cms.VPSet(
        cms.PSet(type=cms.string('GenEventInfoProduct'),fromProductInstance=cms.string('generator'),toProductInstance=cms.string(''))))
    process.shiftEventTime=cms.EDAlias(source=cms.VPSet(
        cms.PSet(type=cms.string('edmHepMCProduct'),fromProductInstance=cms.string('generator'),toProductInstance=cms.string(''))))
    process.generatorSmeared=cms.EDProducer('GeneratorSmearedProducer',
        currentTag=cms.untracked.InputTag('shiftEventTime'),previousTag=cms.untracked.InputTag('unusedProbeInput'))
    process.load('PhysicsTools.HepMCCandAlgos.genParticles_cfi')
    process.genParticles.src=cms.InputTag('generatorSmeared')
    process.gen=cms.Path(process.generatorSmeared+process.genParticles)
    process.output=cms.OutputModule('PoolOutputModule',fileName=cms.untracked.string(str(Path(output_path).resolve())),
        outputCommands=cms.untracked.vstring('drop *','keep GenEventInfoProduct_generator_*_*',
            'keep edmHepMCProduct_shiftEventTime_*_*','keep *_generatorSmeared_*_*','keep *_genParticles_*_*'))
    process.write=cms.EndPath(process.output);process.schedule=cms.Schedule(process.gen,process.write)
    return process


def readback(path,probe):
    from DataFormats.FWLite import Events,Handle,Lumis,Runs
    events=Events(str(path));rows=[]
    for event in events:
        aux=event.eventAuxiliary();identity=[int(aux.run()),int(aux.luminosityBlock()),int(aux.event())]
        if identity!=probe['event_id']:raise ValueError('Native probe EDM identity changed')
        copies={}
        for label in ['shiftEventTime','generatorSmeared']:
            handle=Handle('edm::HepMCProduct');event.getByLabel(label,handle)
            if not handle.isValid():raise ValueError('Missing native probe HepMC '+label)
            hep=handle.product().GetEvent();require_probe_units(hep)
            it,end=hep.particles_begin(),hep.particles_end();seen=[]
            while it!=end:
                p=it.__deref__();it.__preinc__();q=p.momentum();v=p.production_vertex().position()
                seen.append(dict(barcode=int(p.barcode()),pdg=int(p.pdg_id()),momentum_GeV=[q.px(),q.py(),q.pz()],
                    energy_GeV=q.e(),generated_mass_GeV=p.generated_mass(),birth_mm=[v.x(),v.y(),v.z(),v.t()]))
            if sorted(seen,key=lambda m:m['barcode'])!=sorted(probe['muons'],key=lambda m:m['barcode']):
                raise ValueError('Native HepMC changed an exact input p4, barcode, birth or physical time')
            copies[label]=dict(exact_double_p4_births_and_barcodes=True,muons=seen,hepmc_weights=list(hep.weights()))
        weight=Handle('GenEventInfoProduct');event.getByLabel('generator',weight)
        if not weight.isValid() or weight.product().weight()!=1.:raise ValueError('Unit probe weight alias failed')
        truth=Handle('std::vector<reco::GenParticle>');event.getByLabel('genParticles',truth)
        if not truth.isValid() or len(truth.product())!=2:raise ValueError('Native truth must contain exactly two muons')
        particles=[dict(pdg=int(p.pdgId()),status=int(p.status()),p4_GeV=[p.px(),p.py(),p.pz(),p.energy()],
            birth_cm=[p.vx(),p.vy(),p.vz()]) for p in truth.product()]
        rows.append(dict(event_id=identity,weight=weight.product().weight(),copies=copies,genParticles=particles))
    if len(rows)!=1:raise ValueError('Native output must contain exactly one response-probe event')
    metadata={}
    for name,cls,kind in [('GenRunInfoProduct',Runs,'GenRunInfoProduct'),('GenLumiInfoProduct',Lumis,'GenLumiInfoProduct'),
                          ('GenFilterInfo',Lumis,'GenFilterInfo')]:
        present=[]
        for item in cls(str(path)):
            value=Handle(kind);item.getByLabel('generator',value);present.append(value.isValid())
        metadata[name]=dict(present=present,required_for_conditional_probe=False,
            interpretation='Native MCFileSource does not fabricate production cross sections or source-trial counters')
    return dict(complete=True,diagnostic_only=True,new_GEN_exposure=0,physical_cross_section_pb=None,
        events=rows,optional_metadata=metadata,no_SIM_or_detector_settings_changed=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe',type=Path);parser.add_argument('directory',type=Path)
    args=parser.parse_args();probe=validate_probe(json.loads(args.probe.read_text()))
    args.directory.mkdir(parents=True,exist_ok=False)
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    hepmc=args.directory/'probe.hepmc';root=args.directory/'gen.root';config=args.directory/'gen_cfg.py'
    write_hepmc(hepmc,probe);config.write_text(build_process(probe,hepmc,root).dumpPython())
    start=time.monotonic()
    result=dict(complete=False,diagnostic_only=True,new_GEN_exposure=0,physical_cross_section_pb=None,
        probe_sha256=digest(args.probe),hepmc_sha256=digest(hepmc),config_sha256=digest(config))
    try:
        with (args.directory/'cmsRun.log').open('w') as log:
            subprocess.run(['cmsRun',str(config.resolve())],cwd=args.directory,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
        result.update(readback(root,probe),root_sha256=digest(root))
    except Exception as error:
        result['error']=repr(error)
        raise
    finally:
        result['wall_seconds']=time.monotonic()-start
        (args.directory/'readback.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
