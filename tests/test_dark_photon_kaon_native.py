# Code style: AI-code. AI-generated zero-collision native kaon graph controls.
"""Synthetic graph tests in the installed native runtime; not a GEN canary.

No Pythia next() call, CMSSW build, event file or source exposure is produced.
The fixture tests attachment/charge/recoil/ancestry behavior separately from
the later real-source/full-GEN acceptance and transport qualification.
"""
import importlib.util
from decimal import Decimal, localcontext
import json
import os
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_kaon import sample_radiative_decay,sample_weak_decay
from dark_photon_kaon_leptonic import sample_decay as sample_leptonic_decay
from dark_photon_meson_native import native_source,inject_decay,inject_undecayed_kaon_decay


FIXTURE = r'''
namespace ShiftKaonBridgeTest {
int make(ShiftMesonNative::Source& source,int pdg) {
  auto& event=source.pythia.event;event.reset();
  double mass=source.pythia.particleData.m0(pdg),pz=.3,energy=std::hypot(mass,pz);
  event.append(311,-91,0,0,0.,0.,pz+.1,energy+.1,std::sqrt(mass*mass+.2*(energy-pz)));
  int index=event.append(pdg,91,0,0,0.,0.,pz,energy,mass);
  event[index].mothers(1,1);event[1].daughters(index,index);
  event[index].vProd(1.,2.,3.,4.);event[index].tau(123.456);
  event.append(22,1,0,0,0.,0.,.1,.1,0.);
  source.current=std::make_unique<HepMC::GenEvent>(HepMC::Units::GEV,HepMC::Units::MM);
  source.current->weights().push_back(1.);
  auto* vertex=new HepMC::GenVertex(HepMC::FourVector(1.,2.,3.,4.));
  for(int i=1;i<=3;++i) {
    auto const& p=event[i];
    auto* particle=new HepMC::GenParticle(HepMC::FourVector(p.px(),p.py(),p.pz(),p.e()),p.id(),i==1?2:1);
    particle->set_generated_mass(p.m());particle->suggest_barcode(i);
    if(i==1)vertex->add_particle_in(particle);else vertex->add_particle_out(particle);
  }
  source.current->add_vertex(vertex);return index;
}
int append_natural_eta_with_copy(ShiftMesonNative::Source& source) {
  auto& event=source.pythia.event;
  double mass=source.pythia.particleData.m0(221);
  auto copy=event.append(221,-51,0,0,0.,0.,0.,mass,mass);
  auto physical=event.append(221,-91,0,0,0.,0.,0.,mass,mass);
  auto first=event.append(22,1,0,0,0.,0.,mass/2,mass/2,0.);
  auto second=event.append(22,1,0,0,0.,0.,-mass/2,mass/2,0.);
  event[copy].daughters(physical,physical);event[physical].mothers(copy,copy);
  event[physical].daughters(first,second);
  event[first].mothers(physical,physical);event[second].mothers(physical,physical);
  return physical;
}
int make_production_inventory(ShiftMesonNative::Source& source,int pdg) {
  auto& event=source.pythia.event;event.reset();
  double proton=source.pythia.particleData.m0(2212),energy=5.;
  double beam=std::sqrt((energy-proton)*(energy+proton));
  event.append(2212,-12,0,0,0.,0.,beam,energy,proton);
  event.append(2212,-12,0,0,0.,0.,-beam,energy,proton);
  double mass=source.pythia.particleData.m0(pdg),pz=.3;
  int index=event.append(pdg,91,0,0,0.,0.,pz,std::hypot(mass,pz),mass);
  event[index].mothers(1,2);event[index].vProd(1.,2.,3.,4.);event[index].tau(123.456);
  return index;
}
void boost_production_inventory(ShiftMesonNative::Source& source,double beta) {
  for(int i=1;i<source.pythia.event.size();++i)source.pythia.event[i].bst(0.,0.,beta);
}
std::string state(ShiftMesonNative::Source const& source,int index) {
  auto const* parent=source.current->barcode_to_particle(index);
  double residual=0.;
  for(auto v=source.current->vertices_begin();v!=source.current->vertices_end();++v) {
    double sum[4]={0.,0.,0.,0.};
    auto add=[&](HepMC::FourVector const& q,double sign) {
      sum[0]+=sign*q.e();sum[1]+=sign*q.px();sum[2]+=sign*q.py();sum[3]+=sign*q.pz();
    };
    for(auto p=(*v)->particles_in_const_begin();p!=(*v)->particles_in_const_end();++p)add((*p)->momentum(),1.);
    for(auto p=(*v)->particles_out_const_begin();p!=(*v)->particles_out_const_end();++p)add((*p)->momentum(),-1.);
    for(auto value:sum)residual=std::max(residual,std::abs(value));
  }
  std::ostringstream o;o<<std::setprecision(17)<<"{\"parent_status\":"<<parent->status()
    <<",\"native_tau\":"<<source.pythia.event[index].tau()
    <<",\"unit_weight\":"<<source.current->weights()[0]
    <<",\"maximum_vertex_residual_gev\":"<<residual
    <<",\"ancestor_signature\":\""<<ShiftMesonNative::particle_signature(source.current->barcode_to_particle(1))
    <<"\",\"spectator_signature\":\""<<ShiftMesonNative::particle_signature(source.current->barcode_to_particle(3))<<"\"";
  if(parent->end_vertex()) {
    auto const* vertex=parent->end_vertex();
    for(auto p=vertex->particles_out_const_begin();p!=vertex->particles_out_const_end();++p)
      if((*p)->pdg_id()==32&&(*p)->end_vertex()) {
        auto const& point=(*p)->end_vertex()->position();
        o<<",\"dark_decay_position_mm\":["<<point.x()<<','<<point.y()<<','<<point.z()<<','<<point.t()<<']';
      }
  }
  o<<'}';
  return o.str();
}
std::string daughter_records(ShiftMesonNative::Source const& source,int index) {
  auto const* parent=source.current->barcode_to_particle(index);
  if(!parent||!parent->end_vertex())throw std::runtime_error("Missing attached parent");
  std::ostringstream o;o<<std::setprecision(17)<<'[';bool first=true;
  auto row=[&](HepMC::GenParticle const* p) {
    auto const& q=p->momentum();
    o<<"{\"pdg_id\":"<<p->pdg_id()<<",\"status\":"<<p->status()
     <<",\"mass_gev\":"<<p->generated_mass()<<",\"p4\":["
     <<q.e()<<','<<q.px()<<','<<q.py()<<','<<q.pz()<<"]}";
  };
  auto const* vertex=parent->end_vertex();
  for(auto p=vertex->particles_out_const_begin();p!=vertex->particles_out_const_end();++p) {
    if(!first)o<<',';first=false;
    o<<"{\"particle\":";row(*p);o<<",\"daughters\":[";bool next=false;
    if((*p)->end_vertex()) {
      auto const* end=(*p)->end_vertex();
      for(auto child=end->particles_out_const_begin();child!=end->particles_out_const_end();++child) {
        if(next)o<<',';next=true;row(*child);
      }
    }
    o<<"]}";
  }
  o<<']';return o.str();
}
}
'''


@unittest.skipUnless(os.environ.get('CMSSW_BASE') and importlib.util.find_spec('ROOT'),
                     'Requires the installed private CMSSW native runtime')
class NativeKaonGraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=native_source(['Beams:eCM=10.','SoftQCD:nonDiffractive=on',
            'ParticleDecays:limitTau0=on','ParticleDecays:tau0Max=10.',
            # The real K* recoil source changes flavour311, not KL/KS masses.
            '311:m0=0.497611'],861022296,
            [130,310,321,-321])
        import ROOT
        cls.ROOT=ROOT
        if not ROOT.gInterpreter.Declare(FIXTURE):
            raise RuntimeError('Zero-collision native kaon fixture did not compile')

    def new_fixture(self,pdg):
        index=self.ROOT.ShiftKaonBridgeTest.make(self.source,pdg)
        return index,json.loads(str(self.ROOT.ShiftKaonBridgeTest.state(self.source,index)))

    def state(self,index):
        return json.loads(str(self.ROOT.ShiftKaonBridgeTest.state(self.source,index)))

    def lepton_tables(self):
        data=self.source.pythia.particleData
        return {pdg:dict(mass=data.m0(pdg),ctau=data.tau0(pdg),
            can_decay=bool(data.canDecay(pdg)),may_decay=bool(data.mayDecay(pdg)))
            for pdg in (11,12,13,14)}

    def leptonic_model(self,record,lepton):
        authority=json.loads((Path(__file__).resolve().parents[1]/
            'config/physics/dark_photon_kl2_chpt_1993.json').read_text())
        # This zero-collision fixture uses nominal initialized tables. Do not
        # retune them to the physical-rate card or change that card itself.
        return dict(authority['form_factor_model'],parent_mass_gev=record['native_mass_gev'],
            lepton_mass_gev=authority['explicit_conventions'][lepton+'_mass_gev'])

    def test_initialized_neutral_mass_provenance_is_opt_in_and_exact(self):
        configuration=json.loads(str(self.source.configuration()))
        rows=configuration['native_kaon_mass_authorities']
        self.assertEqual([row['pdg_id'] for row in rows],[130,310,311])
        for row in rows:
            entry=self.source.pythia.particleData.particleDataEntryPtr(row['pdg_id'])
            self.assertEqual(row,dict(pdg_id=row['pdg_id'],mass_gev=entry.m0(),
                mass_min_gev=entry.m0Min(),mass_max_gev=entry.m0Max(),mass_width_gev=entry.mWidth()))
        self.assertNotEqual(rows[0]['mass_gev'],rows[2]['mass_gev'])
        original=list(self.source.parent_ids)
        try:
            self.source.parent_ids.clear();self.source.parent_ids.push_back(221)
            ordinary=json.loads(str(self.source.configuration()))
            self.assertEqual(set(ordinary),{'ecm_gev','check_event','mass_tolerance','energy_tolerance','parents'})
            self.assertNotIn('native_kaon_mass_authorities',ordinary)
        finally:
            self.source.parent_ids.clear()
            for pdg in original:self.source.parent_ids.push_back(pdg)
        self.assertEqual(json.loads(str(self.source.summary()))['source_calls'],0)

    def test_old_snapshot_and_injection_require_existing_end_vertex(self):
        index,_=self.new_fixture(130)
        with self.assertRaises(Exception):self.source.snapshot()
        with self.assertRaises(Exception):self.source.selected_parent_record(index)
        record=json.loads(str(self.source.undecayed_kaon_record(index)))
        decay=sample_radiative_decay(130,record['p4'],.25,12.,random.Random(1))
        with self.assertRaises(Exception):inject_decay(self.source,index,decay)
        self.assertEqual(self.state(index)['parent_status'],1)

    def test_production_snapshot_mixes_natural_mesons_and_pinned_final_kaons(self):
        for pdg in (130,310,321,-321):
            for beta in (0.,.9,-.9999):
                index=int(self.ROOT.ShiftKaonBridgeTest.make_production_inventory(self.source,pdg))
                self.source.parent_ids.push_back(221)
                try:
                    eta_index=int(self.ROOT.ShiftKaonBridgeTest.append_natural_eta_with_copy(self.source))
                    self.ROOT.ShiftKaonBridgeTest.boost_production_inventory(self.source,beta)
                    record=json.loads(str(self.source.undecayed_kaon_record(index,False)))
                    summary_before=str(self.source.summary())
                    mixed=json.loads(str(self.source.production_snapshot()))
                    self.assertEqual(mixed['native_weight'],1.)
                    self.assertEqual(mixed['ecm_gev'],10.)
                    self.assertEqual(mixed['source_beam_pdg_ids'],[2212,2212])
                    self.assertEqual(len(mixed['parents']),2)
                    self.assertEqual(mixed['parents'][0],record)
                    self.assertTrue(record['native_final'])
                    self.assertEqual(record['native_status'],91)
                    self.assertEqual(record['mother_indices'],[1,2])
                    self.assertEqual(record['daughter_indices'],[])
                    self.assertEqual(record['source_total_p4'],mixed['source_total_p4'])
                    self.assertGreaterEqual(record['source_com_energy_mantissa_bits'],64)
                    # Independent high-precision audit of the stored p.Q/sqrt(Q²).
                    # A 32-operation double envelope includes JSON rounding of Q
                    # and the native extended-precision arithmetic. Cancellation
                    # enters through the actual |products|/Q², not a fixed epsilon.
                    with localcontext() as context:
                        context.prec=70
                        p=[Decimal.from_float(x) for x in record['p4']]
                        q=[Decimal.from_float(x) for x in record['source_total_p4']]
                        metric=[1,-1,-1,-1]
                        squared=sum(sign*x*x for sign,x in zip(metric,q))
                        product=sum(sign*x*y for sign,x,y in zip(metric,p,q))
                        expected=product/squared.sqrt()
                        gamma=Decimal(32)*Decimal(2)**-53/(1-Decimal(32)*Decimal(2)**-53)
                        squared_error=gamma*sum(x*x for x in q)
                        lower=(squared-squared_error).sqrt()
                        bound=(gamma*sum(abs(x*y) for x,y in zip(p,q))/lower+
                            abs(product)*squared_error/(lower*squared.sqrt()*(lower+squared.sqrt()))+
                            gamma*abs(expected))
                        self.assertLess(abs(Decimal.from_float(record['source_com_energy_gev'])-expected),bound)
                    self.assertGreaterEqual(record['source_com_energy_gev'],record['native_mass_gev'])
                    eta=mixed['parents'][1]
                    self.assertEqual(eta['index'],eta_index)
                    self.assertEqual(eta['pdg_id'],221)
                    native_eta=self.source.pythia.event[eta_index]
                    self.assertEqual(eta['native_mass_gev'],native_eta.m())
                    self.assertEqual(eta['p4'],[native_eta.e(),native_eta.px(),native_eta.py(),native_eta.pz()])
                    self.assertEqual(str(self.source.summary()),summary_before)
                    with self.assertRaises(Exception):self.source.snapshot()
                    self.source.pythia.event[index].status(-91)
                    self.source.pythia.event[index].daughters(eta_index,eta_index)
                    with self.assertRaises(Exception):self.source.production_snapshot()
                finally:
                    self.source.parent_ids.pop_back()

    def test_existing_native_lifetime_point_and_ancestors_survive_every_qualified_chain(self):
        for pdg,recoil_mass in ((130,0.),(310,0.),(321,.13957039),(-321,.13957039),(310,.1349768)):
            index,before=self.new_fixture(pdg)
            snapshot=json.loads(str(self.source.undecayed_kaon_snapshot()))
            self.assertEqual(len(snapshot['parents']),1)
            record=snapshot['parents'][0]
            self.assertEqual(record,json.loads(str(self.source.undecayed_kaon_record(index))))
            decay=(sample_radiative_decay(pdg,record['p4'],.25,12.,random.Random(1)) if not recoil_mass else
                   sample_weak_decay(pdg,record['p4'],recoil_mass,.25,12.,random.Random(1)))
            inject_undecayed_kaon_decay(self.source,index,decay,native_parent_record=record)
            after=self.state(index)
            self.assertEqual(after['parent_status'],2)
            self.assertEqual(before['native_tau'],after['native_tau'])
            for key in ('ancestor_signature','spectator_signature','unit_weight'):
                self.assertEqual(before[key],after[key])
            graph=json.loads(str(self.source.selected_parent_record(index)))
            self.assertEqual(graph['decay_position_mm'],record['stored_decay_position_mm'])
            expected=[a+b for a,b in zip(record['stored_decay_position_mm'],
                list(decay['decay_displacement_mm'])+[decay['decay_ct_mm']])]
            self.assertEqual(after['dark_decay_position_mm'],expected)
            self.assertEqual(decay['native_parent_flight'],record)
            self.assertLess(after['maximum_vertex_residual_gev'],1.e-12)
            recoil=decay['recoil_records'][0]
            self.assertEqual(recoil['pdg_id'],decay['recoil_pdg_id'])
            self.assertEqual(recoil['natural_decay'],recoil['pdg_id']==111)
            with self.assertRaises(Exception):self.source.attach_native_kaon_decay_vertex(index)
        summary=json.loads(str(self.source.summary()))
        self.assertEqual(summary['source_calls'],0)
        self.assertEqual(summary['source_collisions'],0)

    def test_flavor_parent_and_changed_native_graph_are_rejected(self):
        index,_=self.new_fixture(311)
        with self.assertRaises(Exception):self.source.attach_native_kaon_decay_vertex(index)
        index,_=self.new_fixture(130)
        self.source.pythia.event[index].tau(-1.)
        with self.assertRaises(Exception):self.source.attach_native_kaon_decay_vertex(index)
        index,_=self.new_fixture(310)
        self.source.pythia.event[index].vProd(2.,2.,3.,4.)
        with self.assertRaises(Exception):self.source.attach_native_kaon_decay_vertex(index)

    def test_resampled_positive_lifetime_and_parent_flight_payload_are_rejected(self):
        index,_=self.new_fixture(130)
        record=json.loads(str(self.source.undecayed_kaon_record(index)))
        decay=sample_radiative_decay(130,record['p4'],.25,12.,random.Random(1))
        self.source.pythia.event[index].tau(234.567)
        with self.assertRaisesRegex(ValueError,'snapshot/lifetime'):
            inject_undecayed_kaon_decay(self.source,index,decay,native_parent_record=record)
        self.assertEqual(self.state(index)['parent_status'],1)
        index,_=self.new_fixture(130)
        record=json.loads(str(self.source.undecayed_kaon_record(index)))
        decay=sample_radiative_decay(130,record['p4'],.25,12.,random.Random(1))
        decay['parent_flight_included']=True
        with self.assertRaisesRegex(ValueError,'resampling'):
            inject_undecayed_kaon_decay(self.source,index,decay,native_parent_record=record)
        self.assertEqual(self.state(index)['parent_status'],1)

    def test_leptonic_kaon_chains_preserve_native_flight_and_correlated_muons(self):
        tables=self.lepton_tables()
        for pdg in (321,-321):
            for lepton in ('electron','muon'):
                for seed in (12,106,871,4410):
                    index,before=self.new_fixture(pdg)
                    native_before=str(self.source.inventory_snapshot())
                    summary_before=str(self.source.summary())
                    record=json.loads(str(self.source.undecayed_kaon_record(index)))
                    decay=sample_leptonic_decay(record['p4'],.25,12.,random.Random(seed),
                        model=self.leptonic_model(record,lepton),parent_pdg_id=pdg)
                    inject_undecayed_kaon_decay(self.source,index,decay,native_parent_record=record)
                    self.assertTrue(decay['native_parent_flight_attached'])
                    after=self.state(index)
                    self.assertEqual(after['parent_status'],2)
                    self.assertEqual(after['native_tau'],before['native_tau'])
                    for key in ('ancestor_signature','spectator_signature','unit_weight'):
                        self.assertEqual(after[key],before[key])
                    self.assertEqual(str(self.source.inventory_snapshot()),native_before)
                    self.assertEqual(str(self.source.summary()),summary_before)
                    self.assertEqual(self.lepton_tables(),tables)
                    self.assertEqual(json.loads(str(self.source.undecayed_kaon_record(index,False))),record)
                    graph=json.loads(str(self.source.selected_parent_record(index)))
                    self.assertEqual(graph['decay_position_mm'],record['stored_decay_position_mm'])
                    expected=[a+b for a,b in zip(record['stored_decay_position_mm'],
                        list(decay['decay_displacement_mm'])+[decay['decay_ct_mm']])]
                    self.assertEqual(after['dark_decay_position_mm'],expected)
                    self.assertLess(after['maximum_vertex_residual_gev'],1.e-12)
                    rows=json.loads(str(self.ROOT.ShiftKaonBridgeTest.daughter_records(self.source,index)))
                    by_id={row['particle']['pdg_id']:row for row in rows}
                    dark=by_id[32]
                    self.assertEqual(dark['particle']['p4'],list(decay['dark_photon_p4']))
                    dimuons={row['pdg_id']:row['p4'] for row in dark['daughters']}
                    self.assertEqual(dimuons,{13:list(decay['muon_p4']),-13:list(decay['antimuon_p4'])})
                    for row,pdg_id,p4 in zip(decay['recoil_records'],decay['recoil_pdg_ids'],decay['recoil_p4s']):
                        self.assertEqual(row['pdg_id'],pdg_id)
                        self.assertEqual(row['p4'],list(p4))
                        self.assertEqual(row['status'],1)
                        self.assertFalse(row['natural_decay'])
                        self.assertEqual(by_id[pdg_id]['daughters'],[])
        self.assertEqual(json.loads(str(self.source.summary()))['source_calls'],0)

    def test_primary_muon_stays_final_under_unchanged_nominal_decay_switch(self):
        tables=self.lepton_tables()
        self.assertTrue(tables[13]['can_decay'])
        self.assertFalse(tables[13]['may_decay'])
        previous=bool(self.source.pythia.settings.flag('ParticleDecays:limitTau0'))
        try:
            self.assertTrue(self.source.pythia.readString('ParticleDecays:limitTau0=off'))
            for pdg in (321,-321):
                index,before=self.new_fixture(pdg)
                record=json.loads(str(self.source.undecayed_kaon_record(index)))
                decay=sample_leptonic_decay(record['p4'],.25,12.,random.Random(781),
                    model=self.leptonic_model(record,'muon'),parent_pdg_id=pdg)
                inject_undecayed_kaon_decay(self.source,index,decay,native_parent_record=record)
                rows=json.loads(str(self.ROOT.ShiftKaonBridgeTest.daughter_records(self.source,index)))
                primary=next(row for row in rows if row['particle']['pdg_id']==decay['charged_lepton_pdg_id'])
                # Nominal muon mayDecay=false remains authoritative even
                # without the separate mean-lifetime cutoff. Do not force it.
                self.assertEqual(primary['particle']['status'],1)
                self.assertEqual(primary['daughters'],[])
                self.assertEqual(primary['particle']['p4'],list(decay['charged_lepton_p4']))
                self.assertLess(self.state(index)['maximum_vertex_residual_gev'],1.e-12)
                self.assertEqual(self.lepton_tables(),tables)
                for key in ('ancestor_signature','spectator_signature','unit_weight','native_tau'):
                    self.assertEqual(self.state(index)[key],before[key])
        finally:
            self.source.pythia.readString('ParticleDecays:limitTau0='+('on' if previous else 'off'))

    def test_invalid_leptonic_recoils_are_rejected_before_parent_attachment(self):
        for pdgs,masses in (([13,14],[.1056583755,0.]),
                            ([-13,-14],[.1056583755,0.]),
                            ([-13,12],[.1056583755,0.]),
                            ([-13,14,22],[.1056583755,0.,0.]),
                            ([-13,14],[.1056583755,1.e-6]),
                            ([-13,14],[float('nan'),0.])):
            index,before=self.new_fixture(321)
            record=json.loads(str(self.source.undecayed_kaon_record(index)))
            decay=sample_leptonic_decay(record['p4'],.25,12.,random.Random(12),
                model=self.leptonic_model(record,'muon'))
            decay['recoil_pdg_ids']=pdgs
            decay['recoil_masses_gev']=masses
            if len(pdgs)==3:decay['recoil_p4s'].append((0.,0.,0.,0.))
            with self.assertRaises(Exception):
                inject_undecayed_kaon_decay(self.source,index,decay,native_parent_record=record)
            self.assertEqual(self.state(index),before)
            self.assertNotIn('native_parent_flight',decay)


if __name__=='__main__':unittest.main()
