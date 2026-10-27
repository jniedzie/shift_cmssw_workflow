#!/usr/bin/env python3
# Code style: AI-code. SensCalc source-to-GEN adapter with explicit exposure.
"""Import a fixed, audited LLP source bank through the existing SHIFT GEN chain.

The reference bank is unweighted without replacement before detector admission.
All source collisions and weights remain the rate authority. Each saved GEN
event has unit weight, an isotropic dimuon decay, and its unconditional physical
lifetime. These are isolated signal graphs, as in a flux-based sensitivity
calculation; accompanying collision activity is not simulated by this adapter.
"""
import argparse
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys

from dark_photon_senscalc import CHANNELS,digest,ledger_digest,source_ledger,source_summary,write_json
from dark_photon_meson import MUON_MASS_GEV,transition_form_factor_abs2
from run_dark_photon_spectral_gen import freeze_spectral_inputs,physical_width_table

MECHANISM='senscalc-meson-fragmentation'
SOURCES=('run_dark_photon_senscalc_gen.py','dark_photon_senscalc.py','dark_photon_meson.py',
         'run_dark_photon_spectral_gen.py','dark_photon_model.py','fixed_target_generation.py',
         'dark_photon_meson_gen_cfg.py','audit_dark_photon_gen.py','export_dark_photon_cross_sections.py',
         'pythia_generation_ledger.py','dark_photon_source_placement.py','shift_source_placement.py',
         'shift_jaw_source.py','shift_material_preselection.py','dark_photon_detector_runtime.py')


def source_bank(directory,profile,model):
    directory=Path(directory);manifest=json.loads((directory/'manifest.json').read_text())
    ledger,opener=source_ledger(directory)
    if (manifest.get('schema')!='shift-senscalc-source-sample-v1' or manifest.get('complete') is not True or
            ledger_digest(ledger)!=manifest['ledger_sha256'] or
            digest(directory/'source_settings.cmnd')!=manifest['settings_sha256']):
        raise ValueError('Require the complete unchanged reference source bank')
    mass=manifest['mass_gev'];native=manifest['native'];rows=[]
    settings=(directory/'source_settings.cmnd').read_text()
    parameters={}
    for line in settings.splitlines():
        if '=' in line:
            key,value=line.split('=',1);parameters[key.strip()]=value.strip()
    expected={'Beams:frameType':'2','Beams:idA':'2212','Beams:idB':'2212','Beams:eA':'0','Beams:eB':'6800'}
    if any(key not in parameters or float(parameters[key])!=float(value) for key,value in expected.items()):
        raise ValueError('Reference source differs from the stationary-target Beam-B convention')
    with opener(ledger,'rt') as stream:
        original=[json.loads(line) for line in stream]
    nominal=source_summary(original,native['sigma_source_mb'],mass=mass)
    if (not nominal['rate_qualified'] or nominal['source_calls']!=manifest['source_trials'] or
            nominal['candidates']!=native['candidates'] or
            not math.isclose(nominal['sum_weights_over_epsilon_squared'],native['sum_weights_over_epsilon_squared'],rel_tol=1.e-12)):
        raise ValueError('Source bank counters or complete weighted exposure differ')
    parents={row['pdg_id']:row for row in model['parents']}
    coefficients=manifest.get('coefficient_weights_over_epsilon_squared')
    if coefficients is not None and (set(coefficients)!=set(map(str,CHANNELS)) or
            any(not math.isfinite(value) or value<0 for value in coefficients.values())):
        raise ValueError('Incomplete reference coefficient envelope')
    for row in original:
        candidates=[]
        for candidate in row['candidates']:
            item=dict(candidate);factor=1.
            if coefficients is not None and not math.isclose(candidate['weight_over_epsilon_squared'],
                    coefficients[str(candidate['id'])],rel_tol=1.e-12,abs_tol=1.e-15):
                raise ValueError('Source candidate differs from the complete physical coefficient envelope')
            if profile=='measured-form-factor' and candidate['id'] in (4900221,4900331):
                parent=parents[{4900221:221,4900331:331}[candidate['id']]]
                desired=2*parent['br_gamma_gamma']*max(0.,1-(mass/parent['mass_gev'])**2)**3
                desired*=transition_form_factor_abs2(mass,parent['transition_form_factor'])
                reference=candidate['weight_over_epsilon_squared']*native['seed_probability']
                if reference<=0:raise ValueError('Cannot reweight an absent reference channel')
                factor=desired/reference
            elif profile!='pointlike' and profile!='measured-form-factor':raise ValueError('Unknown radiative rate profile')
            item['reference_weight_over_epsilon_squared']=candidate['weight_over_epsilon_squared']
            item['profile_factor']=factor;item['weight_over_epsilon_squared']*=factor
            candidates.append(item)
        rows.append(dict(row,candidates=candidates))
    return manifest,rows,source_summary(rows,native['sigma_source_mb'],mass=mass)


def unweighting_envelope(manifest,profile,model):
    """Use all physical channels, including those absent from a small bank.

    A common envelope lets independent banks share one conditional GEN
    estimator. Historical controls without this field keep their original draw.
    """
    coefficients=manifest.get('coefficient_weights_over_epsilon_squared')
    if coefficients is None:return None
    weights=dict(coefficients)
    if profile=='measured-form-factor':
        parents={row['pdg_id']:row for row in model['parents']};mass=manifest['mass_gev']
        for signal,parent_id in ((4900221,221),(4900331,331)):
            parent=parents[parent_id]
            phase=max(0.,1-(mass/parent['mass_gev'])**2)**3
            weights[str(signal)]=(2*parent['br_gamma_gamma']*phase*
                transition_form_factor_abs2(mass,parent['transition_form_factor'])/manifest['proposal_probability']
                if phase>0 else 0.)
    return max(weights.values())


def boost_reference(rows,mass):
    """Weighted mean boost and collision-level ratio-estimator standard error."""
    collision_weights=[];collision_boosts=[]
    for row in rows:
        collision_weights.append(math.fsum(c['weight_over_epsilon_squared'] for c in row['candidates']))
        collision_boosts.append(math.fsum(c['weight_over_epsilon_squared']*math.hypot(*c['p4'][1:])/mass
                                         for c in row['candidates']))
    total=math.fsum(collision_weights);n=len(rows)
    if total<=0 or n<2:raise ValueError('A LAB reference needs a nonempty independent collision bank')
    mean=math.fsum(collision_boosts)/total
    error=math.sqrt(n/(n-1)*math.fsum((b-mean*w)**2 for b,w in zip(collision_boosts,collision_weights)))/total
    return mean,error


def unweight(rows,seed,envelope=None):
    """Bernoulli sample a known finite weighted bank, without duplicating it."""
    observed=max((c['weight_over_epsilon_squared'] for row in rows for c in row['candidates']),default=0.)
    if envelope is None:envelope=observed
    if not math.isfinite(envelope) or envelope<=0:raise ValueError('No positive-rate source candidates')
    if observed>envelope*(1+1.e-12):raise ValueError('A source weight exceeds the frozen physical envelope')
    rng=random.Random(seed);selected=[];trials=0
    for row in rows:
        for candidate in row['candidates']:
            trials+=1;probability=candidate['weight_over_epsilon_squared']/envelope
            if rng.random()<probability:
                selected.append(dict(candidate,source_trial=row['trial'],sampling_probability=probability,
                    event_slot=len(selected)+1))
    return selected,dict(schema='shift-senscalc-bank-unweighting-v1',seed=seed,envelope=envelope,
        n_tried=trials,n_selected=len(selected),n_accepted=len(selected),
        interpretation='Finite source-bank Bernoulli draws, not Pythia internal trials or new collision exposure')


def decay_momenta(parent,mass,ctau,rng):
    """Isotropic reference decay, using a boost stable at large gamma."""
    energy,*momentum=parent
    cosine=2*rng.random()-1;phi=2*math.pi*rng.random()
    radius=math.sqrt(mass**2/4-MUON_MASS_GEV**2);sine=math.sqrt(1-cosine*cosine)
    rest=[radius*sine*math.cos(phi),radius*sine*math.sin(phi),radius*cosine]
    def boost(vector):
        product=math.fsum(a*b for a,b in zip(momentum,vector))
        lab_energy=energy/2+product/mass
        scale=.5+product/(mass*(energy+mass))
        return [lab_energy]+[a+b*scale for a,b in zip(vector,momentum)]
    length=rng.expovariate(1/ctau)
    return boost(rest),boost([-x for x in rest]),[length*x/mass for x in momentum]+[length*energy/mass]


def write_hepmc(path,selected,mass,ctau,seed):
    import ROOT
    info=subprocess.check_output(['scram','tool','info','hepmc'],text=True,cwd=Path(os.environ['CMSSW_BASE'])/'src')
    base=Path(re.search(r'^HEPMC_BASE=(.*)$',info,re.M).group(1))
    ROOT.gInterpreter.AddIncludePath(str(base/'include'))
    for library in ('libHepMC.so.4','libHepMCfio.so.4'):
        if ROOT.gSystem.Load(str(base/'lib'/library))<0:raise RuntimeError('Cannot load release '+library)
    if not hasattr(ROOT,'ShiftSensCalcWriter'):
        ROOT.gInterpreter.Declare(r'''
#include "HepMC/IO_GenEvent.h"
#include "HepMC/GenEvent.h"
#include "HepMC/GenVertex.h"
#include "HepMC/GenParticle.h"
#include <vector>
class ShiftSensCalcWriter {
  HepMC::IO_GenEvent output;
public:
  ShiftSensCalcWriter(std::string path):output(path,std::ios::out){output.precision(16);}
  void write(int number,double mass,double muonMass,std::vector<double> const& v){
    HepMC::GenEvent event(HepMC::Units::GEV,HepMC::Units::MM);
    event.set_event_number(number);event.weights().push_back(1.);
    auto p4=[&](int i){return HepMC::FourVector(v[i+1],v[i+2],v[i+3],v[i]);};
    auto* birth=new HepMC::GenVertex(HepMC::FourVector(0,0,0,0));
    auto* decay=new HepMC::GenVertex(HepMC::FourVector(v[12],v[13],v[14],v[15]));
    auto* dark=new HepMC::GenParticle(p4(0),32,3);dark->set_generated_mass(mass);
    auto* muon=new HepMC::GenParticle(p4(4),13,1);muon->set_generated_mass(muonMass);
    auto* anti=new HepMC::GenParticle(p4(8),-13,1);anti->set_generated_mass(muonMass);
    birth->add_particle_out(dark);decay->add_particle_in(dark);
    decay->add_particle_out(muon);decay->add_particle_out(anti);
    event.add_vertex(birth);event.add_vertex(decay);event.set_signal_process_vertex(birth);
    output.write_event(&event);
  }
};''')
    writer=ROOT.ShiftSensCalcWriter(str(path))
    for row in selected:
        key=json.dumps([seed,row['source_trial'],row['index']],separators=(',',':')).encode()
        import hashlib
        rng=random.Random(int(hashlib.sha256(key).hexdigest(),16))
        muon,anti,flight=decay_momenta(row['p4'],mass,ctau,rng)
        writer.write(row['event_slot'],mass,MUON_MASS_GEV,row['p4']+muon+anti+flight)
    del writer


def accounting(directory,contract):
    """Recompute the full source rate and exact finite-bank selection."""
    directory=Path(directory)
    for name,key in [('source_manifest.json','source_manifest_sha256'),('source_ledger.jsonl','source_ledger_sha256'),
                     ('source_exposure.json','source_exposure_sha256'),('radiative_model.json','radiative_model_sha256')]:
        path=directory/name
        if name=='source_ledger.jsonl':
            path=path if path.is_file() else source_ledger(directory/'source_bank')[0]
            observed=ledger_digest(path)
        else:observed=digest(path)
        if observed!=contract[key]:raise ValueError('Changed SensCalc source authority: '+name)
    # source_bank uses the source sampler's ordinary manifest filename.
    model=json.loads((directory/'radiative_model.json').read_text())
    source=directory/'source_bank'
    manifest,rows,summary=source_bank(source,contract['rate_profile'],model)
    if contract['mass_gev']!=manifest['mass_gev']:
        raise ValueError('Declared signal mass differs from its reference source bank')
    selected,ledger=unweight(rows,contract['seed'],unweighting_envelope(manifest,contract['rate_profile'],model))
    saved=json.loads((directory/'selected_signals.json').read_text())
    if (saved!=selected or len(selected)!=contract['requested_events'] or ledger!=contract['proposal_ledger'] or
            digest(source/'manifest.json')!=contract['source_manifest_sha256'] or
            ledger_digest(source_ledger(source)[0])!=contract['source_ledger_sha256']):
        raise ValueError('Changed finite-bank unweighting, source identities or exposure')
    exposure=json.loads((directory/'source_exposure.json').read_text())
    expected_exposure=dict(schema='shift-senscalc-source-exposure-v1',source_collisions=summary['source_collisions'],
        fixed_source_collisions=summary['source_collisions'],accepted_signal_events=len(selected),
        unit_output_gen_weights=True,raw_source_summary=summary)
    if exposure!=contract['source_exposure'] or exposure!=expected_exposure:
        raise ValueError('Changed original source exposure')
    authority=contract['physical_width_table']['spectral_source']
    inputs=directory/'width_inputs'
    if digest(inputs/'source_receipt.json')!=authority['source_receipt_sha256']:
        raise ValueError('Changed physical width source receipt')
    for name,sha in authority['files_sha256'].items():
        if digest(inputs/name)!=sha:raise ValueError('Changed physical width source: '+name)
    sys.path.insert(0,str(inputs));import darkcast
    width=physical_width_table(darkcast.Model('dark_photon'),manifest['mass_gev'],contract['epsilon'],authority)
    if width!=contract['physical_width_table'] or width!=contract['width_authority']:
        raise ValueError('Physical lifetime or branching fraction differs from the pinned model')
    factor=contract['epsilon']**2*contract['physical_width_table']['br_mumu']
    rate=dict(value_pb=summary['sigma_over_epsilon_squared_pb']*factor,
              error_pb=summary['sigma_statistical_error_over_epsilon_squared_pb']*factor)
    if rate!=contract['target_cross_section']:raise ValueError('SensCalc rate correction must be applied once')
    return dict(proposal_ledger=ledger,source_exposure=exposure,target_cross_sections=[rate],
        source_rate_uncertainty=summary,physical_width=contract['physical_width_table'])


def rate_record(contract,audit,*,point,campaign,source_hashes,gen_path,gen_sha256,source_directory):
    """Export the full source estimate, never interpreting GEN counts as collisions."""
    from export_dark_photon_cross_sections import SCHEMA
    import hashlib
    fresh=accounting(source_directory,contract);events=contract['requested_events']
    expected_ids=[[contract['run_number'],1,i] for i in range(1,events+1)]
    if (contract['filtering']!='none' or audit.get('runtime_validated') is not True or
            audit['events']!=events or audit['event_ids']!=expected_ids or
            audit['sum_weights']!=events or audit['sum_weights_squared']!=events or
            audit['proposal_ledger']!=fresh['proposal_ledger'] or
            audit['target_cross_sections']!=fresh['target_cross_sections'] or
            audit['source_exposure']!=fresh['source_exposure']):
        raise ValueError('Require the exact complete reference-flux GEN audit')
    rate=fresh['target_cross_sections'][0];branching=contract['physical_width_table']['br_mumu']
    source_manifest=json.loads((Path(source_directory)/'source_manifest.json').read_text())
    return dict(schema=SCHEMA,point=point,campaign=campaign,mass_gev=contract['mass_gev'],
        epsilon=contract['epsilon'],sampling_epsilon=contract['epsilon'],
        proper_ctau_mm=contract['physical_width_table']['ctau_mm'],physical_br_mumu=branching,
        corrections=dict(common_amplitude=contract['epsilon']**2,branching_ratio=branching,
            combined=contract['epsilon']**2*branching,applications=1,
            extra_detector_or_generator_filter_efficiency_applied=False),
        physical_selected_cross_sections_pb=[rate],
        physical_inclusive_cross_sections_pb=[dict(value_pb=rate['value_pb']/branching,error_pb=rate['error_pb']/branching)],
        cross_section_authority='Pinned reference ND source sigma times the complete collision mean of weighted signal coefficients',
        cross_section_convention=contract['cross_section_convention'],
        combination_rule='Only disjoint source banks with identical prescriptions may combine by original collision exposure; never add overlapping rate profiles as independent exposure',
        source_exposure=fresh['source_exposure'],source_exposure_sha256=contract['source_exposure_sha256'],
        source_ledger_sha256=contract['source_ledger_sha256'],rate_uncertainty=fresh['source_rate_uncertainty'],
        full_gen=dict(accepted_events=events,sum_weights=float(events),sum_weights_squared=float(events),
            event_ids_sha256=hashlib.sha256(json.dumps(expected_ids,separators=(',',':')).encode()).hexdigest(),
            event_ids_file='metadata/GEN/validation.json',source_collisions=fresh['source_exposure']['source_collisions'],
            full_gen_denominator=audit['full_gen_denominator'],
            interpretation='Unit-weight conditional signal events; source-bank sampling draws are not Pythia internal counters or additional collisions'),
        proposal_ledger=fresh['proposal_ledger'],physical_width_authority=contract['physical_width_table'],
        mechanism=MECHANISM,model=contract['model'],rate_profile=contract['rate_profile'],
        radiative_model_sha256=contract['radiative_model_sha256'],
        source_seed=source_manifest['seed'],
        source_sampling=dict(proposal_probability=source_manifest['proposal_probability'],
            settings_sha256=source_manifest['settings_sha256'],runtime_receipt_sha256=source_manifest['runtime_receipt_sha256'],
            unweighting_envelope=fresh['proposal_ledger']['envelope']),
        generated_mass_support_gev=contract['generated_mass_support_gev'],beam_energy_GeV=contract['beam_energy_GeV'],
        source_model=contract['source_model'],source_graph_scope=contract['source_graph_scope'],
        source_vertex_approximation=contract.get('source_vertex_approximation',
            'Reference LLP flux placed at the collision; source-parent flight and material transport omitted'),
        decay_radiation_model=contract.get('decay_radiation_model',
            'Born dimuon decay; no generator-level final-state photon radiation'),
        polarization_model=contract['polarization_model'],seed=contract['seed'],run_number=contract['run_number'],
        rate_scope=audit['rate_scope'],runtime_validated=True,physics_valid=False,normalization_ready=False,
        production_rate_validated=False,low_mass_mechanisms_included=True,prompt_interference_included=False,
        gen_path=gen_path,gen_sha256=gen_sha256,source_sha256=source_hashes,exporter_sha256=digest(__file__))


def run(args):
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    result=dict(status='preflight',backend=MECHANISM,physics_valid=False,normalization_ready=False)
    try:
        if not 1<=args.seed<=899999800 or not 1<=args.run_number<=4294967295:
            raise ValueError('Valid unused GEN seed and run required')
        if not math.isfinite(args.epsilon) or not 0<args.epsilon<=.01:raise ValueError('Small positive physical epsilon required')
        for name in SOURCES:shutil.copy2(Path(__file__).with_name(name),output/name)
        result['source_sha256']={name:digest(output/name) for name in SOURCES}
        shutil.copy2(args.radiative_model,output/'radiative_model.json')
        source=output/'source_bank'
        if args.sample is not None:
            source.mkdir()
            for name in ('manifest.json','source_settings.cmnd','source.log'):
                shutil.copy2(args.sample/name,source/name)
            ledger,_=source_ledger(args.sample)
            shutil.copy2(ledger,source/ledger.name)
        else:
            from dark_photon_senscalc import sample_source,unpack_runtime
            import tempfile
            with tempfile.TemporaryDirectory(prefix='senscalc-source-',dir=output.parent) as scratch:
                runtime=unpack_runtime(json.loads(args.source_runtime_profile.read_text()),Path(scratch)/'capsule')
                sample_source(runtime,args.source_settings,args.mass,args.source_collisions,
                              args.source_seed,source,probability=args.proposal_probability)
        shutil.copy2(source/'manifest.json',output/'source_manifest.json')
        ledger,opener=source_ledger(source)
        with opener(ledger,'rb') as incoming,(output/'source_ledger.jsonl').open('xb') as outgoing:
            shutil.copyfileobj(incoming,outgoing)
        model=json.loads((output/'radiative_model.json').read_text())
        sample,rows,summary=source_bank(source,args.rate_profile,model)
        selected,ledger=unweight(rows,args.seed,unweighting_envelope(sample,args.rate_profile,model))
        if not selected:raise ValueError('No unweighted signals; preserve the source rate and increase source statistics')
        mass=sample['mass_gev'];authority=freeze_spectral_inputs(args.darkcast_path,args.darkcast_source_receipt,output)
        sys.path.insert(0,str(output/'width_inputs'));import darkcast
        width=physical_width_table(darkcast.Model('dark_photon'),mass,args.epsilon,authority)
        write_json(output/'selected_signals.json',selected)
        exposure=dict(schema='shift-senscalc-source-exposure-v1',source_collisions=summary['source_collisions'],
            fixed_source_collisions=summary['source_collisions'],accepted_signal_events=len(selected),
            unit_output_gen_weights=True,raw_source_summary=summary)
        write_json(output/'source_exposure.json',exposure)
        factor=args.epsilon**2*width['br_mumu']
        mean_boost,boost_error=boost_reference(rows,mass)
        contract=dict(schema='shift-dark-photon-gen-v1',model='BC1-SensCalc-source-DarkCast-visible-widths',
            production_mechanism=MECHANISM,mechanism=MECHANISM,mass_gev=mass,epsilon=args.epsilon,
            seed=args.seed,run_number=args.run_number,requested_events=len(selected),maximum_framework_events=len(selected),
            generated_mass_support_gev=[mass*(1-1.e-6),mass*(1+1.e-6)],decay_mode='mumu',filtering='none',selection_applied=False,
            source_z_mm=148000.,source_sigma_z_mm=500.,source_model='Pinned reference ND pp meson decays and fragmentation',
            source_graph_scope='Isolated signal graphs; accompanying collision activity omitted',
            source_vertex_approximation='Reference LLP flux placed at the collision; source-parent flight and material transport omitted',
            polarization_model='Isotropic two-body reference decay; no source polarization density supplied',
            decay_radiation_model='Born dimuon decay; no generator-level final-state photon radiation',
            beam_energy_GeV=6800.,rate_profile=args.rate_profile,
            mean_beta_gamma_reference=mean_boost,mean_beta_gamma_reference_standard_error=boost_error,
            mean_lab_flight_reference_estimate_m=mean_boost*width['ctau_mm']/1000.,
            physical_width_table=width,width_authority=width,proposal_ledger=ledger,source_exposure=exposure,
            source_manifest_sha256=digest(output/'source_manifest.json'),source_ledger_sha256=digest(output/'source_ledger.jsonl'),
            source_exposure_sha256=digest(output/'source_exposure.json'),radiative_model_sha256=digest(output/'radiative_model.json'),
            target_cross_section=dict(value_pb=summary['sigma_over_epsilon_squared_pb']*factor,
                error_pb=summary['sigma_statistical_error_over_epsilon_squared_pb']*factor),
            cross_section_convention='Reference ND sigma times full-source weighted mean, epsilon squared and physical dimuon BR once; saved conditional GEN has unit weights',
            physics_valid=False,normalization_ready=False)
        write_hepmc(output/'signal.hepmc',selected,mass,width['ctau_mm'],args.seed)
        contract['signal_ascii_sha256']=digest(output/'signal.hepmc')
        if args.jaw_profile is not None:
            from shift_jaw_source import source_position
            profile=json.loads(args.jaw_profile.read_text());source_seed='dp-jaw-source-'+str(args.seed)
            positions=[source_position(profile,contract['signal_ascii_sha256'],[args.run_number,1,row['event_slot']],source_seed) for row in selected]
            contract['source_placement']=dict(schema='shift-dp-assumed-jaw-source-v1',profile=profile,
                profile_sha256=digest(args.jaw_profile),source_seed=source_seed,
                original_source_sha256=contract['signal_ascii_sha256'],original_source_kind='Reference LLP flux',event_positions=positions,material_or_depth_weight_applied=False)
            shutil.copy2(args.jaw_profile,output/'jaw_source_profile.json')
        write_json(output/'contract.json',contract);accounting(output,contract)
        if args.detector_runtime_profile is None:
            from dark_photon_source_placement import record_runtime
            record_runtime(result,Path(os.environ['CMSSW_BASE']),output)
        else:
            from dark_photon_detector_runtime import validate_profile,validate_cached_files
            profile=json.loads(args.detector_runtime_profile.read_text());manifest=validate_profile(profile)
            validate_cached_files(Path(os.environ['CMSSW_BASE']).parent,profile,manifest)
            shutil.copy2(args.detector_runtime_profile,output/'detector_runtime_profile.json')
            result['detector_runtime_profile']=profile
            result['detector_runtime_profile_sha256']=digest(output/'detector_runtime_profile.json')
        with (output/'cmsRun.log').open('w') as log:
            subprocess.run(['cmsRun',str(output/'dark_photon_meson_gen_cfg.py'),'pilotDirectory='+str(output)],
                stdout=log,stderr=subprocess.STDOUT,cwd=output,timeout=args.timeout,check=True)
        with (output/'audit.log').open('w') as log:
            subprocess.run([sys.executable,str(output/'audit_dark_photon_gen.py'),str(output)],stdout=log,stderr=subprocess.STDOUT,cwd=output,timeout=args.timeout,check=True)
        result.update(status='GEN runtime audit passed; reference production benchmark',output_sha256=digest(output/'gen.root'),
            validation=json.loads((output/'validation.json').read_text()),source_collisions=summary['source_collisions'],signal_events=len(selected),
            source_bank_reused=args.sample is not None,new_source_collisions=0 if args.sample is not None else summary['source_collisions'])
    except Exception as error:
        result.update(status='failed',error=repr(error));raise
    finally:write_json(output/'manifest.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('output','darkcast-path','darkcast-source-receipt','radiative-model'):
        parser.add_argument('--'+name,type=Path,required=True)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--sample',type=Path)
    source.add_argument('--source-runtime-profile',type=Path)
    parser.add_argument('--source-settings',type=Path)
    parser.add_argument('--mass',type=float);parser.add_argument('--source-collisions',type=int)
    parser.add_argument('--source-seed',type=int);parser.add_argument('--proposal-probability',type=float,default=.01)
    parser.add_argument('--epsilon',type=float,required=True)
    parser.add_argument('--seed',type=int,required=True);parser.add_argument('--run-number',type=int,required=True)
    parser.add_argument('--rate-profile',choices=('pointlike','measured-form-factor'),default='pointlike')
    parser.add_argument('--jaw-profile',type=Path);parser.add_argument('--timeout',type=int,default=600)
    parser.add_argument('--detector-runtime-profile',type=Path)
    args=parser.parse_args()
    fresh=(args.source_settings,args.mass,args.source_collisions,args.source_seed)
    if args.source_runtime_profile is not None and any(value is None for value in fresh):
        parser.error('Fresh sources need --source-settings, --mass, --source-collisions and --source-seed')
    if args.sample is not None and any(value is not None for value in fresh):
        parser.error('A saved bank cannot also request fresh source exposure')
    print(json.dumps(run(args),indent=2))


if __name__=='__main__':main()
