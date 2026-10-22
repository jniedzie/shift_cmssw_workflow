#!/usr/bin/env python3
# Code style: AI-code. AI-generated bounded ordinary-source preparation worker.
"""Complete natural decays at six placed originals; rebuild native GEN truth."""
import copy,hashlib,json,math,os,pathlib,re,shutil,subprocess,sys,tempfile,time
from audit_shift_material_scout import metadata,product,items,identity
from shift_material_response import source_timing_input,source_normalization
from run_shift_weighted_gen import publish
from shift_source_placement import placement_contract

P=pathlib.Path
TOOLS=P(__file__).resolve().parents[1]

def sha(p):return hashlib.sha256(P(p).read_bytes()).hexdigest()
def require(ok,message):
    if not ok:raise ValueError(message)
def dump(p,x):P(p).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def product(event,label,kind):
    from DataFormats.FWLite import Handle
    handle=Handle(kind);event.getByLabel(label,handle)
    require(handle.isValid(),'Missing native product '+repr(label))
    return handle,handle.product()

def anchor(hep):return hep.signal_process_vertex() or hep.vertices_begin().__deref__()
def xyz(v):return [float(v.x()),float(v.y()),float(v.z()),float(v.t())]

def graph(event,label):
    handle,hep_product=product(event,label,'edm::HepMCProduct');hep=hep_product.GetEvent()
    vertices=[]
    for v in items(hep.vertices_begin(),hep.vertices_end()):
        vertices.append(dict(barcode=int(v.barcode()),id=int(v.id()),position=xyz(v.position()),
            incoming=sorted(int(p.barcode()) for p in items(v.particles_in_const_begin(),v.particles_in_const_end())),
            outgoing=sorted(int(p.barcode()) for p in items(v.particles_out_const_begin(),v.particles_out_const_end()))))
    particles=[]
    for p in items(hep.particles_begin(),hep.particles_end()):
        particles.append(dict(barcode=int(p.barcode()),pdg=int(p.pdg_id()),status=int(p.status()),
            momentum=xyz(p.momentum()),generated_mass=float(p.generated_mass()),
            production_vertex=int(p.production_vertex().barcode()) if p.production_vertex() else None,
            end_vertex=int(p.end_vertex().barcode()) if p.end_vertex() else None))
    return dict(event_number=int(hep.event_number()),signal_process_id=int(hep.signal_process_id()),
        signal_vertex=int(hep.signal_process_vertex().barcode()) if hep.signal_process_vertex() else None,
        anchor_barcode=int(anchor(hep).barcode()),anchor=xyz(anchor(hep).position()),
        momentum_unit=int(hep.momentum_unit()),length_unit=int(hep.length_unit()),
        weights=list(map(float,hep.weights())),random_states=list(map(int,hep.random_states())),
        particles=sorted(particles,key=lambda x:x['barcode']),vertices=sorted(vertices,key=lambda x:x['barcode']))

def graph_hash(g):return hashlib.sha256(json.dumps(g,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def compare_graphs(before,after,target_mm,shift_ct):
    a,b=copy.deepcopy(before),copy.deepcopy(after)
    av,bv=a.pop('vertices'),b.pop('vertices');aa,ba=a.pop('anchor'),b.pop('anchor')
    require(a==b,'Graph identities, p4, masses, weights, units, RNG state or edges changed')
    require(len(av)==len(bv),'Vertex counts changed')
    delta=[target_mm[i]-aa[i] for i in range(3)]
    maximum=0.
    for old,new in zip(av,bv):
        x,y=old.pop('position'),new.pop('position');require(old==new,'Vertex identity or edges changed')
        expected=[x[i]+delta[i] for i in range(3)]+[x[3]+shift_ct]
        differences=[abs(one-two) for one,two in zip(expected,y)];maximum=max(maximum,*differences)
        require(max(differences)<=1.e-8,'Vertex translation or nominal source time changed')
    require(max(abs(ba[i]-target_mm[i]) for i in range(3))<=1.e-8,'Placed source missed its absolute target')
    return dict(exact_p4_graph_identity_weights_units_RNG=True,maximum_vertex_residual_mm=maximum,
        before_graph_sha256=graph_hash(before),after_graph_sha256=graph_hash(after),vertices=len(av),particles=len(a['particles']))


PROCESS='SHIFTFULLGEN'
COMPLETE='shiftFullMuonDecays'

def audit_completion(event,before,after):
    def val(instance,kind):
        h,v=product(event,(COMPLETE,instance,PROCESS),kind)
        return list(v) if 'vector' in kind else v[0]
    enabled=list(map(int,val('enabledPdgIds','std::vector<int>')))
    parent_ids=list(map(int,val('parentBarcodes','std::vector<int>')))
    parent_pdgs=list(map(int,val('parentPdgIds','std::vector<int>')))
    taus=list(map(float,val('sampledProperLifetimeMm','std::vector<double>')))
    means=list(map(float,val('nominalProperLifetimeMm','std::vector<double>')))
    require(len(parent_ids)==len(set(parent_ids))==len(parent_pdgs)==len(taus)==len(means),'Lifetime ledger dimensions differ')
    ordinary=float(val('proposalOrdinaryFraction','double'))
    ratio=float(val('proposalDensityRatio','double'));correction=float(val('proposalCorrection','double'))
    require(ordinary==ratio==correction==1. and bool(val('proposalAuditPassed','bool')),'Ordinary completion introduced a proposal weight')
    require(int(val('proposalComponent','unsigned int'))==0 and int(val('eligiblePairCount','unsigned int'))==0,'Unexpected conditioned decay component')
    for name,kind in [('eligibleParentBarcodes','std::vector<int>'),('eligibleMaximumProperLifetimeMm','std::vector<double>'),('eligibleDecayProbabilities','std::vector<double>'),('selectedPairBarcodes','std::vector<int>')]:
        require(not val(name,kind),'Ordinary completion unexpectedly contains finite or selected lifetimes')
    a,b=copy.deepcopy(before),copy.deepcopy(after)
    old_particles={x['barcode']:x for x in a.pop('particles')};new_particles={x['barcode']:x for x in b.pop('particles')}
    old_vertices={x['barcode']:x for x in a.pop('vertices')};new_vertices={x['barcode']:x for x in b.pop('vertices')}
    require(a==b,'Natural completion changed source, units, event identity, weights or RNG record')
    require(all(new_vertices.get(k)==v for k,v in old_vertices.items()),'Natural completion changed an original vertex or ancestry')
    changed=[]
    for barcode,p in old_particles.items():
        require(barcode in new_particles,'Original particle lost during completion')
        q=copy.deepcopy(new_particles[barcode]);old=copy.deepcopy(p)
        if old!=q:
            require(old['status']==1 and q['status']==2 and old['end_vertex'] is None and q['end_vertex'] in new_vertices and abs(old['pdg']) in enabled,'Unexpected original particle change')
            q['status']=old['status'];q['end_vertex']=old['end_vertex'];require(q==old,'Original p4/mass/production vertex changed')
            changed.append(barcode)
    require(set(changed)==set(parent_ids),'Actual decays differ from lifetime ledger')
    maximum=0.
    for v in new_vertices.values():
        if v['barcode'] in old_vertices:continue
        require(v['incoming'] and v['outgoing'],'Appended decay vertex has missing edges')
        for i in range(4):
            incoming=math.fsum(new_particles[k]['momentum'][i] for k in v['incoming'])
            outgoing=math.fsum(new_particles[k]['momentum'][i] for k in v['outgoing'])
            residual=abs(incoming-outgoing);maximum=max(maximum,residual)
            require(residual<=1.e-6*max(1.,abs(incoming)),'Appended decay violates four-momentum conservation')
    maximum_clock=0.
    for barcode,pdg,tau,mean in zip(parent_ids,parent_pdgs,taus,means):
        p=new_particles[barcode];require(p['pdg']==pdg and math.isfinite(tau) and tau>=0 and math.isfinite(mean) and mean>0,'Invalid saved natural lifetime')
        start=new_vertices[p['production_vertex']]['position'];end=new_vertices[p['end_vertex']]['position'];mass=p['generated_mass']
        require(math.isfinite(mass) and mass>0,'Natural parent has no positive generated mass')
        for i in range(4):
            expected=start[i]+p['momentum'][i]*tau/mass
            residual=abs(end[i]-expected);maximum_clock=max(maximum_clock,residual)
            require(residual<=1.e-6+1.e-9*max(abs(end[i]),abs(expected)),'Natural decay vertex does not match sampled proper lifetime and parent p4')
    added=[p for k,p in new_particles.items() if k not in old_particles and abs(p['pdg'])==13 and p['status']==1]
    require(int(val('decayedParents','unsigned int'))==len(changed) and int(val('addedMuons','unsigned int'))==len(added),'Native decay counts differ from full graph')
    return dict(enabled_PDG_ids=enabled,parent_barcodes=parent_ids,parent_PDG_ids=parent_pdgs,sampled_proper_lifetime_mm=taus,nominal_proper_lifetime_mm=means,decayed_parents=len(changed),added_stable_muons=len(added),added_particles=len(new_particles)-len(old_particles),added_vertices=len(new_vertices)-len(old_vertices),all_original_p4_and_ancestry_preserved=True,ordinary_fraction=ordinary,proposal_density_ratio=ratio,proposal_correction=correction,maximum_p4_residual_GeV=maximum,maximum_proper_clock_residual_mm=maximum_clock,completed_unshifted_graph_sha256=graph_hash(after))

def encoded_expected_particles(timed):
    """Build only from HepMC; let the native dictionary define its persistence."""
    import ROOT
    ROOT.gInterpreter.Declare('#include "DataFormats/HepMCCandidate/interface/GenParticle.h"')
    schema=P('/cvmfs/cms.cern.ch/el9_amd64_gcc13/cms/cmssw/CMSSW_17_0_0_pre4/src/DataFormats/Candidate/src/classes_def.xml')
    schema_sha=sha(schema)
    require(schema_sha=='66c6124fc1d1580acefe469c404ac143c95ba833aed9fcd8117698c2dcec62fa','Stock truth persistence dictionary changed')
    vertices={v['barcode']:v for v in timed['vertices']}
    expected=ROOT.std.vector('reco::GenParticle')()
    for p in timed['particles']:
        cand=ROOT.reco.GenParticle();cand.setPdgId(p['pdg']);cand.setStatus(p['status'])
        cand.setP4(ROOT.math.XYZTLorentzVector(*p['momentum']))
        point=[x*.1 for x in vertices[p['production_vertex']]['position'][:3]] if p['production_vertex'] is not None else [0.,0.,0.]
        cand.setVertex(ROOT.math.XYZPoint(*point));expected.push_back(cand)
    encoded={};maximum_vertex=0.;maximum_p4=0.
    with tempfile.TemporaryDirectory(prefix='expected_genparticles_',dir=str(P.cwd())) as directory:
        path=str(P(directory)/'expected.root');file=ROOT.TFile(path,'RECREATE');file.WriteObject(expected,'truth');file.Close()
        file=ROOT.TFile.Open(path);saved=file.Get('truth');require(len(saved)==len(timed['particles']),'Native expected truth encoding lost particles')
        for p,cand in zip(timed['particles'],saved):
            point=[x*.1 for x in vertices[p['production_vertex']]['position'][:3]] if p['production_vertex'] is not None else [0.,0.,0.]
            coords=[float(cand.vx()),float(cand.vy()),float(cand.vz())]
            momentum=[float(cand.px()),float(cand.py()),float(cand.pz()),float(cand.energy())]
            require(all(math.isfinite(x) for x in coords+momentum),'Native expected truth encoding is nonfinite')
            maximum_vertex=max(maximum_vertex,*[abs(a-b) for a,b in zip(coords,point)])
            maximum_p4=max(maximum_p4,*[abs(a-b) for a,b in zip(momentum,p['momentum'])])
            encoded[p['barcode']]=dict(momentum=momentum,position=coords)
        file.Close()
    return encoded,dict(stock_persistence_schema_sha256=schema_sha,
        encoding='native reco::GenParticle constructed from newly timed HepMC, then stock ROOT dictionary roundtrip',
        maximum_vertex_residual_cm=maximum_vertex,maximum_p4_raw_to_encoded_residual_GeV=maximum_p4)

def audit_genparticles(event,timed):
    h,candidates=product(event,('genParticles','',PROCESS),'std::vector<reco::GenParticle>')
    hb,barcodes=product(event,('genParticles','',PROCESS),'std::vector<int>')
    codes=list(map(int,barcodes));require(len(codes)==len(set(codes))==len(candidates)==len(timed['particles']),'Rebuilt truth barcode coverage differs')
    particles={p['barcode']:p for p in timed['particles']};vertices={v['barcode']:v for v in timed['vertices']}
    require(set(codes)==set(particles),'Truth barcodes differ from latest timed HepMC')
    expected,encoding=encoded_expected_particles(timed)
    for index,cand in enumerate(candidates):
        barcode=codes[index];p=particles[barcode];encoded=expected[barcode]
        require(int(cand.pdgId())==p['pdg'] and int(cand.status())==p['status'],'Rebuilt truth PDG/status differs for barcode '+str(barcode))
        actual=[float(cand.px()),float(cand.py()),float(cand.pz()),float(cand.energy())]
        if actual!=encoded['momentum']:
            raise ValueError('Persisted truth p4 mismatch '+json.dumps(dict(event_id=identity(event),barcode=barcode,raw_HepMC=p['momentum'],native_encoded_expected=encoded['momentum'],actual=actual)))
        coords=[float(cand.vx()),float(cand.vy()),float(cand.vz())]
        if coords!=encoded['position']:
            raise ValueError('Persisted truth vertex mismatch '+json.dumps(dict(event_id=identity(event),barcode=barcode,native_encoded_expected=encoded['position'],actual=coords)))
        mothers=sorted(codes[int(cand.motherRef(i).key())] for i in range(cand.numberOfMothers()))
        daughters=sorted(codes[int(cand.daughterRef(i).key())] for i in range(cand.numberOfDaughters()))
        expected_mothers=vertices[p['production_vertex']]['incoming'] if p['production_vertex'] is not None else []
        expected_daughters=vertices[p['end_vertex']]['outgoing'] if p['end_vertex'] is not None else []
        require(mothers==expected_mothers and daughters==expected_daughters,'Rebuilt truth mother/daughter barcode links differ for barcode '+str(barcode))
    _,latest=product(event,'genParticles','std::vector<reco::GenParticle>')
    require(len(latest)==len(candidates),'Unqualified truth lookup found stale products')
    return dict(particles=len(candidates),stock_persistence_encoding_exact=True,
        expected_encoded_p4_residual_GeV=0.,expected_encoded_vertex_residual_cm=0.,
        exact_PDG_status_and_barcode_ancestry=True,rebuilt_process=PROCESS,**encoding)


def source_configuration(unit,plan):
    """Use the qualified common source chain with the frozen full census."""
    input_label=unit['input_label']
    return f'''# Code style: AI-code. AI-generated frozen source-placement test configuration.
import json
import FWCore.ParameterSet.Config as cms
from shift_source_placement import configure_complete_source
p=cms.Process('SHIFTFULLGEN')
p.load('IOMC.ShiftEventTiming.shiftEventTime_cfi')
p.source=cms.Source('PoolSource',fileNames=cms.untracked.vstring('file:input.root'),eventsToProcess=cms.untracked.VEventRange({[':'.join(map(str,x))+'-'+':'.join(map(str,x)) for x in unit['event_ids']]!r}))
p.maxEvents=cms.untracked.PSet(input=cms.untracked.int32({len(unit['event_ids'])}))
p.options=cms.untracked.PSet(numberOfThreads=cms.untracked.uint32(1),numberOfStreams=cms.untracked.uint32(1))
chain=configure_complete_source(p,{input_label!r},seed={unit['seed']!r},test_scope={plan['test_scope']!r},event_positions={unit['positions']!r})
p.sourceNominalBeforePlacement=p.shiftEventTime.clone(src=cms.InputTag('shiftSourcePlacement','original'))
p.path=cms.Path(chain+p.sourceNominalBeforePlacement)
p.out=cms.OutputModule('PoolOutputModule',fileName=cms.untracked.string('file:placed.root'),outputCommands=cms.untracked.vstring('keep *','drop *_genParticles_*_*','drop *_generatorSmeared_*_*','drop *_shiftEventTime_*_*','keep *_genParticles_*_SHIFTFULLGEN','keep *_generatorSmeared_*_SHIFTFULLGEN','keep *_shiftEventTime_*_SHIFTFULLGEN'))
p.end=cms.EndPath(p.out)
process=p
open('resolved_cfg.py','w').write(p.dumpPython())
'''

def validate_plan(plan):
    """Reject censored sources, partial censuses and ambiguous exposure early."""
    bound=plan.get('maximum_events_per_unit')
    require(type(bound) is int and 1<=bound<=2000,'Declare a bounded 1--2000 original source census')
    units=plan.get('units');require(isinstance(units,list) and units,'Missing source units')
    families=set();sources=set();identities=set();total=0
    for unit in units:
        family=unit['family'];source=unit['source']
        require(family in ('qcd','dy','jpsi') and family not in families,'Require one bounded unit per supported family')
        families.add(family)
        require(source['source_family']==('qcd_ordinary' if family=='qcd' else family)
                and source.get('generator_profile')=='ordinary'
                and source.get('decay_dependent_filter') is False
                and not source.get('source_preparation_receipt_json')
                and source.get('muon_enhancement_complete') is not True,
                'Input must be the uncensored ordinary source before completion')
        require(source['gen_sha256'] not in sources,'Repeated original source exposure')
        sources.add(source['gen_sha256']);source_normalization(source)
        events=unit['event_ids'];weights=unit['native_weights'];positions=unit['positions']
        require(isinstance(events,list) and 1<=len(events)<=bound
                and len(events)==source['saved_events']==len(weights)==len(positions),
                'Prepare every saved original exactly once; prefixes/subsamples are not supported')
        require(type(unit.get('source_inclusion_probability')) in (int,float)
                and unit['source_inclusion_probability']==1. and unit.get('full_saved_source_census') is True,
                'The complete source census must declare unchanged unit inclusion probability')
        require([row['event_id'] for row in positions]==events,'Position map differs from frozen original order')
        placement_contract(None,unit['input_label'],test_scope=plan['test_scope'],event_positions=positions)
        require(unit['input_label']==source_timing_input(source),'Wrong native unshifted graph label')
        require(type(unit['seed']) is int and 1<=unit['seed']<=899999922,'Invalid natural-completion seed')
        for identity,weight in zip(events,weights):
            require(tuple(identity) not in identities,'Repeated original identity')
            identities.add(tuple(identity))
            require(type(weight) in (int,float) and math.isfinite(weight) and weight>0,'Invalid native original weight')
        total+=len(events)
    require(plan['event_positions']==[row for unit in units for row in unit['positions']],
            'Global native source-position census differs from unit positions')
    containment=plan['containment_rows']
    require(len(containment)==total and {tuple(row['event_id']) for row in containment}==identities,
            'Native containment expectations do not cover the complete source census')
    require(type(plan.get('material_feature_census',False)) is bool,'Feature census switch must be Boolean')
    return total


def feature_census(directory,unit,plan,output):
    """Reuse the existing scanner on every prepared event, retaining zero-muon controls."""
    dump(directory/'frame.json',plan['frame'])
    source_map={str(identity[0]):dict(source_id=unit['source']['source_id'],
                original_source_sha256=unit['source']['gen_sha256']) for identity in unit['event_ids']}
    dump(directory/'original_sources.json',source_map)
    features=directory/'features.json.gz'
    command=['python3',str(P(__file__).with_name('shift_gen_material_features.py')),
             '--gen',str(output),'--hepmc-label','shiftEventTime',
             '--geometry',plan['geometry_path'],'--frame',str(directory/'frame.json'),
             '--original-source-map',str(directory/'original_sources.json'),'--output',str(features)]
    started=time.monotonic()
    with (directory/'features.log').open('w') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=plan.get('feature_timeout_seconds',3600))
    import gzip
    with gzip.open(features,'rt') as stream:data=json.load(stream)
    expected={tuple(identity):weight for identity,weight in zip(unit['event_ids'],unit['native_weights'])}
    rows=data['events'];seen={tuple(row['event_id']):row['native_weight'] for row in rows}
    require(len(rows)==len(expected) and seen==expected,'Material-feature census changed original identities or weights')
    require(data['maximum_events_per_source'] is None and data['prefix_requested'] is False
            and data['summary']['source_read_counts'][0]['entire_input_file_read'] is True,
            'Material-feature scanner did not read the complete prepared source')
    require(data['sources'][0]['sha256']==sha(output)
            and all(row['original_source_sha256']==unit['source']['gen_sha256'] for row in rows),
            'Material-feature raw/prepared source SHA binding differs')
    return features,dict(events=len(rows),all_saved_originals_read=True,zero_muon_controls_retained=True,
        original_source_sha256=unit['source']['gen_sha256'],prepared_GEN_sha256=sha(output),
        feature_sha256=sha(features),feature_bytes=features.stat().st_size,summary=data['summary'],
        argv=command,seconds=time.monotonic()-started,usable_as_physics_weight=False)


def run(plan):
    expected_events=validate_plan(plan)
    from DataFormats.FWLite import Events
    import ROOT
    ROOT.gInterpreter.Declare('#include <HepMC/Units.h>')
    mm=int(ROOT.gInterpreter.ProcessLine('static_cast<int>(HepMC::Units::MM)'))
    for name,expected in plan['private_input_hashes'].items():require(sha(name)==expected,'Private geometry hash changed')
    from shift_material_path_score import artifact_point
    ROOT.TGeoManager.LockDefaultUnits(False)
    ROOT.TGeoManager.SetDefaultUnits(ROOT.TGeoManager.kRootUnits)
    ROOT.TGeoManager.LockDefaultUnits(True)
    geometry=ROOT.TGeoManager.Import(plan['geometry_path']);require(geometry,'Native geometry import failed')
    containment=[]
    for row in plan['event_positions']:
        point=artifact_point(row['target_position_cm'],plan['frame']);node=geometry.FindNode(*point)
        require(node and not geometry.GetCurrentNavigator().IsOutside(),'Drawn source point is outside native geometry')
        volume=node.GetVolume();material=volume.GetMaterial()
        expected=next(x for x in plan['containment_rows'] if x['event_id']==row['event_id'])
        require(str(volume.GetName())==expected['actual_volume'] and str(material.GetName())==expected['actual_material'],'Drawn source point differs from native containment receipt')
        containment.append(dict(event_id=row['event_id'],target_position_cm=row['target_position_cm'],
            volume=str(volume.GetName()),material=str(material.GetName()),density_g_cm3=float(material.GetDensity())))
    scratch=P(os.environ.get('_CONDOR_SCRATCH_DIR','')).resolve()
    require(os.environ.get('_CONDOR_SCRATCH_DIR'),'Private plugins require a real worker scratch directory')
    release=(P(os.environ['CMSSW_BASE'])/'lib'/os.environ['SCRAM_ARCH']).resolve()
    require(os.path.commonpath([str(release),str(scratch)])==str(scratch),
            'Refuse plugin installation outside the worker-local runtime')
    for filename,key in [('pluginShiftSourcePlacementProducer.so','plugin_sha256'),
                         ('pluginCompleteShiftMuonDecayProducer.so','complete_plugin_sha256'),
                         ('pluginShiftHepMC2CopyProducer.so','hepmc2_copy_plugin_sha256')]:
        plugin=TOOLS/'plugin'/filename
        require(sha(plugin)==plan[key],'Qualified private plugin changed: '+filename)
        target=release/filename
        require(not target.is_symlink(),'Refuse a symlink private-plugin target')
        shutil.copyfile(plugin,target)
    subprocess.run(['edmPluginRefresh',str(release)],check=True,timeout=120)
    report=dict(schema='shift-assumed-jaw-full-GEN-canary-v1',complete=False,started_epoch=time.time(),
        units=[],new_GEN_exposure=0,generator_run=False,SIM_or_detector_run=False,physics_normalization_ready=False,
        full_production_ready=False,source_distribution_qualified=False,source_assumption=plan['source_assumption'],
        target_map_sha256=plan['target_map_sha256'],source_profile_sha256=plan['source_profile_sha256'],containment_receipt_sha256=plan['containment_receipt_sha256'],native_timing_requires_MM=True,native_source_containment=containment,published=[])
    dump('summary.json',report)
    try:
        outputs=[]
        for unit in plan['units']:
            report['current_family']=unit['family'];dump('summary.json',report)
            directory=P(unit['family']);directory.mkdir();input_file=directory/'input.root'
            subprocess.run(['xrdcp','--silent','--cksum','adler32',unit['source']['url'],str(input_file)],check=True,timeout=600)
            require(sha(input_file)==unit['source']['gen_sha256'],'Native source GEN hash changed')
            require(int(Events(str(input_file)).size())==len(unit['event_ids']),'Raw input is not the frozen complete saved source census')
            source_metadata=metadata(input_file)
            input_label=source_timing_input(unit['source']);require(input_label==unit['input_label'],'Actual family timing adapter differs')
            selected={tuple(x):i for i,x in enumerate(unit['event_ids'])};before={}
            for event in Events(str(input_file)):
                eid=identity(event)
                if tuple(eid) not in selected:continue
                g=graph(event,input_label);require(g['length_unit']==mm,'Existing nominal timing requires MM input')
                _,info=product(event,'generator','GenEventInfoProduct');weight=float(info.weight())
                require(weight==unit['native_weights'][selected[tuple(eid)]],'Native event weight differs')
                old_timed=graph(event,'shiftEventTime') if unit['family']=='dy' else None
                before[tuple(eid)]=dict(graph=g,weight=weight,old_timed=old_timed)
            require(set(before)==set(selected),'Missing selected original identity')
            placement_contract(None,input_label,test_scope=plan['test_scope'],event_positions=unit['positions'])
            config=source_configuration(unit,plan)
            (directory/'placement_cfg.py').write_text(config)
            with (directory/'cmsRun.log').open('w') as log:
                subprocess.run(['cmsRun','placement_cfg.py'],cwd=directory,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=plan.get('completion_timeout_seconds',3600))
            output=directory/'placed.root';require(metadata(output)==source_metadata,'Full original Run/Lumi accounting changed')
            file_check=ROOT.TFile.Open(str(output));require(file_check and not file_check.IsZombie(),'Cannot open prepared GEN')
            derived_branches=[]
            for branch in file_check.Get('Events').GetListOfBranches():
                match=re.search(r'_(genParticles|generatorSmeared|shiftEventTime)_([^_]*)_([^.]*)\.',str(branch.GetName()))
                if match:
                    require(match.group(3)==PROCESS,'Stale input-derived truth or timing remains persisted')
                    derived_branches.append(str(branch.GetName()))
            require(derived_branches,'Missing newly prepared derived branches');file_check.Close()
            positions={tuple(x['event_id']):x['target_position_cm'] for x in unit['positions']};checks=[]
            for event in Events(str(output)):
                eid=identity(event);report['current_event_id']=list(eid)
                key=tuple(eid);require(key in before,'Unexpected output original')
                old=before[key];placed=graph(event,('shiftSourcePlacement','','SHIFTFULLGEN'))
                timed=graph(event,('shiftEventTime','','SHIFTFULLGEN'))
                unplaced_nominal=graph(event,('sourceNominalBeforePlacement','','SHIFTFULLGEN'))
                target_mm=[x*10. for x in positions[key]]
                first=compare_graphs(old['graph'],placed,target_mm,0.)
                raw_saved=graph(event,('shiftSourcePlacement','original','SHIFTFULLGEN'));require(raw_saved==old['graph'],'Retained explicit raw source differs')
                completed=graph(event,('shiftFullMuonDecays','','SHIFTFULLGEN'))
                decay_audit=audit_completion(event,placed,completed)
                second=compare_graphs(completed,timed,target_mm,-completed['anchor'][2])
                require(graph(event,('generatorSmeared','','SHIFTFULLGEN'))==timed,'New generatorSmeared proxy differs from timed completed graph')
                truth_audit=audit_genparticles(event,timed)
                if unit['family']=='dy':require(unplaced_nominal==old['old_timed'],'DY original unshifted-label nominal timing did not close')
                _,info=product(event,'generator','GenEventInfoProduct');require(float(info.weight())==old['weight'],'Native weight changed after placement')
                for i,name in enumerate(['originalXmm','originalYmm','originalZmm','originalCtMm']):
                    _,value=product(event,('shiftSourcePlacement',name,'SHIFTFULLGEN'),'double')
                    require(float(value[0])==old['graph']['anchor'][i],'Original anchor metadata differs')
                timing_meta={}
                for label in ['sourceXmm','sourceYmm','sourceZmm','sourceCtBeforeMm','appliedShiftCtMm']:
                    _,value=product(event,('shiftEventTime',label,'SHIFTFULLGEN'),'double');timing_meta[label]=float(value[0])
                require(timing_meta['appliedShiftCtMm']==-placed['anchor'][2] and abs(timing_meta['sourceZmm']-target_mm[2])<=1.e-8,'Nominal source time applied incorrectly')
                for i,name in enumerate(['targetXmm','targetYmm','targetZmm']):
                    _,value=product(event,('shiftSourcePlacement',name,'SHIFTFULLGEN'),'double');require(float(value[0])==target_mm[i],'Source target metadata differs')
                checks.append(dict(event_id=eid,native_weight=old['weight'],target_position_cm=positions[key],placement=first,completion=decay_audit,genParticles=truth_audit,nominal_timing=second,DY_existing_nominal_graph_exact=unit['family']=='dy',timing_metadata=timing_meta))
            require(len(checks)==len(selected) and {tuple(x['event_id']) for x in checks}==set(selected),'Output original event coverage differs')
            kind,n,sigma=source_normalization(unit['source'])
            report['units'].append(dict(family=unit['family'],source=unit['source'],input_label=input_label,source_GEN_sha256=sha(input_file),source_full_accounting=source_metadata,native_denominator_kind=kind,native_denominator=n,native_source_cross_section_pb=sigma,full_Run_Lumi_preserved=True,complete_natural_decays=True,GENParticles_rebuilt=True,derived_branches=derived_branches,prepared_process=PROCESS,completed_unshifted_label=COMPLETE,raw_unshifted_label='shiftSourcePlacement',raw_unshifted_instance='original',events=checks,output_sha256=sha(output),output_bytes=output.stat().st_size))
            outputs.append((output,unit['family']+'_full_GEN.root'))
            if plan.get('material_feature_census'):
                features,feature_audit=feature_census(directory,unit,plan,output)
                report['units'][-1]['material_feature_census']=feature_audit
                outputs.append((features,unit['family']+'_features.json.gz'))
            input_file.unlink();dump('summary.json',report)
        require(sum(len(x['events']) for x in report['units'])==expected_events,'Prepared source census event count changed')
        report.update(complete=True,events=expected_events,full_saved_source_census=True,source_inclusion_probability=1.,finished_epoch=time.time());dump('summary.json',report)
        return_bytes=sum((P(unit['family'])/name).stat().st_size for unit in plan['units']
                         for name in ('cmsRun.log','placement_cfg.py','features.log')
                         if (P(unit['family'])/name).is_file())
        expected=sum(path.stat().st_size for path,name in outputs)+3*P('summary.json').stat().st_size+return_bytes+65536
        report['retained_byte_guard']=dict(payload_bytes=sum(path.stat().st_size for path,name in outputs),return_log_and_config_bytes=return_bytes,projected_with_three_summary_copies_and_receipt_margin=expected,bound_bytes=plan['publication_bound_bytes'])
        dump('summary.json',report)
        require(expected<=plan['publication_bound_bytes'],'Source preparation publication exceeds registered bound')
        publications=publish(outputs+[(P('summary.json'),'audit.json')],plan['publication_directory'])
        report['published']=publications;report['publication_sha256_readback_verified']=True;dump('summary.json',report)
        require(sum(x['bytes'] for x in publications)+2*P('summary.json').stat().st_size+return_bytes+65536<=plan['publication_bound_bytes'],'Completion receipt and returned evidence exceed publication bound')
        publish([(P('summary.json'),'complete.json')],plan['publication_directory'])
        for path,name in outputs:path.unlink()
    except BaseException as error:
        report.update(complete=False,error=repr(error),finished_epoch=time.time());dump('summary.json',report);raise

if __name__=='__main__':run(json.loads(P(sys.argv[1]).read_text()))
