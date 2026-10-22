# Code style: AI-code. AI-generated zero-collision native kaon graph controls.
"""Synthetic graph tests in the installed native runtime; not a GEN canary.

No Pythia next() call, CMSSW build, event file or source exposure is produced.
The fixture tests attachment/charge/recoil/ancestry behavior separately from
the later real-source/full-GEN acceptance and transport qualification.
"""
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dark_photon_kaon import sample_radiative_decay,sample_weak_decay
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
}
'''


@unittest.skipUnless(os.environ.get('CMSSW_BASE') and importlib.util.find_spec('ROOT'),
                     'Requires the installed private CMSSW native runtime')
class NativeKaonGraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=native_source(['Beams:eCM=10.','SoftQCD:nonDiffractive=on',
            'ParticleDecays:limitTau0=on','ParticleDecays:tau0Max=10.'],861022296,
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

    def test_old_snapshot_and_injection_require_existing_end_vertex(self):
        index,_=self.new_fixture(130)
        with self.assertRaises(Exception):self.source.snapshot()
        with self.assertRaises(Exception):self.source.selected_parent_record(index)
        record=json.loads(str(self.source.undecayed_kaon_record(index)))
        decay=sample_radiative_decay(130,record['p4'],.25,12.,random.Random(1))
        with self.assertRaises(Exception):inject_decay(self.source,index,decay)
        self.assertEqual(self.state(index)['parent_status'],1)

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


if __name__=='__main__':unittest.main()
