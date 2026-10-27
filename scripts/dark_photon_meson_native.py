# Code style: AI-code. AI-generated native Pythia/HepMC meson-source bridge.
"""Use the installed Pythia8 and HepMC libraries without rebuilding CMSSW.

Generation and graph replacement run in a private interpreter. No shared
release, detector configuration, electronics or trigger settings are changed.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import hashlib


CPP = r'''
#include "Pythia8/Pythia.h"
#include "Pythia8Plugins/HepMC2.h"
#include "HepMC/IO_GenEvent.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <functional>
#include <iomanip>
#include <memory>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>
namespace ShiftMesonNative {
class Output {
  std::unique_ptr<HepMC::IO_GenEvent> stream;
public:
  explicit Output(std::string const& path):stream(std::make_unique<HepMC::IO_GenEvent>(path,std::ios::out)){stream->precision(16);}
  void write(HepMC::GenEvent const* event){if(!stream)throw std::runtime_error("Closed HepMC stream");stream->write_event(event);}
  void close(){stream.reset();}
};
std::string particle_signature(HepMC::GenParticle const* p) {
  std::ostringstream o;
  o<<std::setprecision(17)<<p->barcode()<<' '<<p->pdg_id()<<' '<<p->status()<<' '
   <<p->momentum().px()<<' '<<p->momentum().py()<<' '<<p->momentum().pz()<<' '<<p->momentum().e()<<' '
   <<p->generated_mass();
  for(auto const* v:{p->production_vertex(),p->end_vertex()}) {
    if(!v){o<<" none";continue;}
    o<<' '<<v->barcode()<<' '<<v->position().x()<<' '<<v->position().y()<<' '
     <<v->position().z()<<' '<<v->position().t();
  }
  return o.str();
}
class Source {
public:
  Pythia8::Pythia pythia;
  HepMC::Pythia8ToHepMC converter;
  std::unique_ptr<HepMC::GenEvent> current;
  long calls=0, failures=0, collisions=0;
  std::vector<int> parent_ids;
  std::vector<double> recoil_spin_data;
  std::function<bool(HepMC::GenParticle*,HepMC::FourVector const&)> correlated_recoil_decay;
  Source(std::vector<std::string> const& commands,int seed,std::vector<int> const& parents)
      :pythia(std::string(std::getenv("PYTHIA8DATA")),false),parent_ids(parents) {
    if(parent_ids.empty())throw std::runtime_error("Explicit source parent inventory is empty");
    pythia.readString("Print:quiet=on");
    pythia.readString("Random:setSeed=on");
    pythia.readString("Random:seed="+std::to_string(seed));
    for(auto const& command:commands)
      if(!pythia.readString(command))throw std::runtime_error("Rejected Pythia setting: "+command);
    if(!pythia.init())throw std::runtime_error("Unfiltered parent-source initialization failed");
  }
  bool next() {
    ++calls;
    if(!pythia.next()){++failures;return false;}
    ++collisions;
    if(pythia.info.weight()!=1.)throw std::runtime_error("Parent source is not unit weighted");
    return true;
  }
  std::string radiative_ancestor(int index) const {
    // A coherent P -> P' gamma A' amplitude owns its P -> gamma V feed-down.
    // Follow only same-species generator copies, never arbitrary ancestors.
    std::set<int> seen;
    int child=index;
    while(seen.insert(child).second) {
      auto mothers=pythia.event[child].motherList();
      if(mothers.size()!=1)return "null";
      int mother=mothers.front();
      if(mother<=0||mother>=pythia.event.size())
        throw std::runtime_error("Invalid radiative-source ancestry");
      if(pythia.event[mother].id()==pythia.event[child].id()) {child=mother;continue;}
      if(pythia.event[mother].id()!=221&&pythia.event[mother].id()!=331)return "null";
      auto daughters=pythia.event[mother].daughterList();
      if(daughters.size()!=2)return "null";
      bool contains=false,photon=false;
      for(int daughter:daughters) {
        contains=contains||daughter==child;
        photon=photon||(daughter!=child&&pythia.event[daughter].id()==22);
      }
      if(!contains||!photon)return "null";
      std::ostringstream out;
      out<<"{\"index\":"<<mother<<",\"pdg_id\":"<<pythia.event[mother].id()
         <<",\"vector_pdg_id\":"<<pythia.event[child].id()<<",\"sibling_pdg_id\":22}";
      return out.str();
    }
    throw std::runtime_error("Cyclic radiative-source ancestry");
  }
  std::string snapshot() const {
    std::ostringstream o;o<<std::setprecision(17)<<"{\"native_weight\":"<<pythia.info.weight()
       <<",\"ecm_gev\":"<<pythia.info.eCM()<<",\"parents\":[";
    bool first=true;
    for(int i=1;i<pythia.event.size();++i) {
      auto const& p=pythia.event[i];
      if(std::find(parent_ids.begin(),parent_ids.end(),p.id())==parent_ids.end())continue;
      auto daughters=p.daughterList();
      if(daughters.empty())throw std::runtime_error("An eligible meson was not naturally decayed");
      bool copy=false;
      for(auto j:daughters)copy=copy||pythia.event[j].id()==p.id();
      if(copy)continue;
      if(!first)o<<',';first=false;
      o<<"{\"index\":"<<i<<",\"pdg_id\":"<<p.id()<<",\"native_mass_gev\":"<<p.m()
       <<",\"p4\":["<<p.e()<<','<<p.px()<<','<<p.py()<<','<<p.pz()<<"]"
       <<",\"radiative_ancestor\":"<<radiative_ancestor(i)<<'}';
    }
    o<<"]}";return o.str();
  }
  std::string configuration() {
    std::ostringstream o;o<<std::setprecision(17)<<"{\"ecm_gev\":"<<pythia.info.eCM()
      <<",\"check_event\":"<<(pythia.settings.flag("Check:event")?"true":"false")
      <<",\"mass_tolerance\":"<<pythia.settings.parm("Check:mTolErr")
      <<",\"energy_tolerance\":"<<pythia.settings.parm("Check:epTolErr")<<",\"parents\":[";
    bool first=true;
    for(auto pdg:parent_ids) {
      auto entry=pythia.particleData.particleDataEntryPtr(pdg);
      if(!first)o<<',';first=false;
      o<<"{\"pdg_id\":"<<pdg<<",\"mass_min_gev\":"<<entry->m0Min()
       <<",\"mass_max_gev\":"<<entry->m0Max()<<",\"mass_width_gev\":"<<entry->mWidth()<<'}';
    }
    o<<']';
    if(std::find(parent_ids.begin(),parent_ids.end(),130)!=parent_ids.end()||
       std::find(parent_ids.begin(),parent_ids.end(),310)!=parent_ids.end()) {
      o<<",\"native_kaon_mass_authorities\":[";first=true;
      for(auto pdg:{130,310,311}) {
        auto entry=pythia.particleData.particleDataEntryPtr(pdg);
        if(!first)o<<',';first=false;
        o<<"{\"pdg_id\":"<<pdg<<",\"mass_gev\":"<<entry->m0()
         <<",\"mass_min_gev\":"<<entry->m0Min()<<",\"mass_max_gev\":"<<entry->m0Max()
         <<",\"mass_width_gev\":"<<entry->mWidth()<<'}';
      }
      o<<']';
    }
    o<<'}';return o.str();
  }
  std::string inventory_snapshot() const {
    // This diagnostic sees every meson, including undecayed long-lived ones.
    // Copies and K0 -> KS/KL bookkeeping remain explicit for the audit to own.
    std::ostringstream o;o<<std::setprecision(17)<<"{\"native_weight\":"<<pythia.info.weight()
      <<",\"ecm_gev\":"<<pythia.info.eCM()<<",\"incoming_beams\":[";
    for(int i=1;i<=2;++i) {
      auto const& p=pythia.event[i];if(i==2)o<<',';
      o<<"{\"pdg_id\":"<<p.id()<<",\"p4\":["<<p.e()<<','<<p.px()<<','<<p.py()<<','<<p.pz()<<"]}";
    }
    double final_energy=0.;
    for(int i=1;i<pythia.event.size();++i)if(pythia.event[i].isFinal())final_energy+=pythia.event[i].e();
    o<<"],\"final_event_energy_gev\":"<<final_energy<<",\"mesons\":[";
    bool first=true;
    for(int i=1;i<pythia.event.size();++i) {
      auto const& p=pythia.event[i];
      if(!pythia.particleData.isMeson(p.id()))continue;
      if(!first)o<<',';first=false;
      o<<"{\"index\":"<<i<<",\"pdg_id\":"<<p.id()<<",\"status\":"<<p.status()
       <<",\"final\":"<<(p.isFinal()?"true":"false")<<",\"native_mass_gev\":"<<p.m()
       <<",\"p4\":["<<p.e()<<','<<p.px()<<','<<p.py()<<','<<p.pz()<<"]"
       <<",\"production_position_mm\":["<<p.xProd()<<','<<p.yProd()<<','<<p.zProd()<<','<<p.tProd()<<"]"
       <<",\"stored_decay_position_mm\":["<<p.xDec()<<','<<p.yDec()<<','<<p.zDec()<<','<<p.tDec()<<"]"
       <<",\"selected_proper_lifetime_mm\":"<<p.tau()<<",\"mean_proper_lifetime_mm\":"<<p.tau0()
       <<",\"mother_indices\":[";
      bool next=false;for(auto index:p.motherList()){if(next)o<<',';next=true;o<<index;}
      o<<"],\"daughter_indices\":[";next=false;
      for(auto index:p.daughterList()){if(next)o<<',';next=true;o<<index;}
      o<<"]}";
    }
    o<<"]}";return o.str();
  }
  HepMC::GenEvent* convert(int number) {
    current=std::make_unique<HepMC::GenEvent>(HepMC::Units::GEV,HepMC::Units::MM);
    if(!converter.fill_next_event(pythia,current.get(),number))
      throw std::runtime_error("Native source conversion to HepMC failed");
    if(current->weights().size()!=1||current->weights()[0]!=1.)
      throw std::runtime_error("Unexpected source HepMC weights");
    return current.get();
  }
  std::string selected_parent_record(int index) const {
    auto* parent=current?current->barcode_to_particle(index):nullptr;
    if(!parent||std::find(parent_ids.begin(),parent_ids.end(),parent->pdg_id())==parent_ids.end()||
       !parent->production_vertex()||!parent->end_vertex())
      throw std::runtime_error("Selected parent graph/vertices are missing");
    std::ostringstream o;o<<std::setprecision(17)<<"{\"barcode\":"<<parent->barcode()
      <<",\"pdg_id\":"<<parent->pdg_id()<<",\"native_mass_gev\":"<<parent->generated_mass();
    for(auto const& item:{std::make_pair("production_position_mm",parent->production_vertex()),
                          std::make_pair("decay_position_mm",parent->end_vertex())}) {
      auto const& p=item.second->position();
      o<<",\""<<item.first<<"\":["<<p.x()<<','<<p.y()<<','<<p.z()<<','<<p.t()<<']';
    }
    o<<'}';return o.str();
  }
  std::array<long double,4> source_total_p4() const {
    if(pythia.event.size()<3)throw std::runtime_error("Native incoming beam records are absent");
    auto const& a=pythia.event[1];auto const& b=pythia.event[2];
    std::array<long double,4> q{{static_cast<long double>(a.e())+b.e(),
      static_cast<long double>(a.px())+b.px(),static_cast<long double>(a.py())+b.py(),
      static_cast<long double>(a.pz())+b.pz()}};
    for(auto value:q)if(!std::isfinite(value))throw std::runtime_error("Nonfinite native beam sum");
    return q;
  }
  std::string undecayed_kaon_record(int index,bool converted=true) const {
    if(index<=0||index>=pythia.event.size())throw std::runtime_error("Missing native kaon index");
    auto const& p=pythia.event[index];
    if((p.id()!=130&&p.id()!=310&&p.id()!=321&&p.id()!=-321)||
       std::find(parent_ids.begin(),parent_ids.end(),p.id())==parent_ids.end()||
       !p.isFinal()||!p.daughterList().empty()||!std::isfinite(p.m())||p.m()<=0.||
       !std::isfinite(p.tau0())||p.tau0()<=0.||
       !std::isfinite(p.tau())||p.tau()<0.)
      throw std::runtime_error("Require an explicit physical undecayed kaon with its sampled native lifetime");
    for(auto value:{p.e(),p.px(),p.py(),p.pz(),p.xProd(),p.yProd(),p.zProd(),p.tProd(),
                    p.xDec(),p.yDec(),p.zDec(),p.tDec()})
      if(!std::isfinite(value))throw std::runtime_error("Nonfinite native kaon momentum/flight");
    if(converted) {
      auto const* parent=current?current->barcode_to_particle(index):nullptr;
      if(!parent||parent->pdg_id()!=p.id()||parent->status()!=1||
         !parent->production_vertex()||parent->end_vertex()||
         parent->momentum().e()!=p.e()||parent->momentum().px()!=p.px()||
         parent->momentum().py()!=p.py()||parent->momentum().pz()!=p.pz()||
         parent->generated_mass()!=p.m())
        throw std::runtime_error("Native kaon does not match an unchanged final HepMC parent");
      auto const& position=parent->production_vertex()->position();
      if(position.x()!=p.xProd()||position.y()!=p.yProd()||
         position.z()!=p.zProd()||position.t()!=p.tProd())
        throw std::runtime_error("Native kaon production vertex changed during conversion");
    }
    auto q=source_total_p4();
    long double q2=q[0]*q[0];
    for(int i=1;i<4;++i)q2=std::fma(-q[i],q[i],q2);
    if(q[0]<=0.||q2<=0.)throw std::runtime_error("Future timelike native beam sum required");
    long double product=static_cast<long double>(p.e())*q[0];
    product=std::fma(-static_cast<long double>(p.px()),q[1],product);
    product=std::fma(-static_cast<long double>(p.py()),q[2],product);
    product=std::fma(-static_cast<long double>(p.pz()),q[3],product);
    auto com_energy=product/std::sqrt(q2);
    if(!std::isfinite(com_energy))throw std::runtime_error("Nonfinite native COM kaon energy");
    std::ostringstream o;o<<std::setprecision(17)<<"{\"index\":"<<index
      <<",\"pdg_id\":"<<p.id()<<",\"native_mass_gev\":"<<p.m()
      <<",\"p4\":["<<p.e()<<','<<p.px()<<','<<p.py()<<','<<p.pz()<<"]"
      <<",\"production_position_mm\":["<<p.xProd()<<','<<p.yProd()<<','<<p.zProd()<<','<<p.tProd()<<"]"
      <<",\"stored_decay_position_mm\":["<<p.xDec()<<','<<p.yDec()<<','<<p.zDec()<<','<<p.tDec()<<"]"
      <<",\"selected_proper_lifetime_mm\":"<<p.tau()
      <<",\"mean_proper_lifetime_mm\":"<<p.tau0()
      <<",\"native_end_vertex_present\":false,\"native_final\":true,\"native_status\":"<<p.status()
      <<",\"mother_indices\":[";
    bool next=false;for(auto mother:p.motherList()){if(next)o<<',';next=true;o<<mother;}
    o<<"],\"daughter_indices\":[],\"source_total_p4\":["
      <<std::setprecision(std::numeric_limits<long double>::max_digits10)
      <<q[0]<<','<<q[1]<<','<<q[2]<<','<<q[3]<<"]"
      <<",\"source_com_energy_gev\":"<<com_energy
      <<",\"source_com_energy_mantissa_bits\":"<<std::numeric_limits<long double>::digits<<'}';
    return o.str();
  }
  std::string undecayed_kaon_snapshot() const {
    std::ostringstream o;o<<std::setprecision(17)<<"{\"native_weight\":"<<pythia.info.weight()
      <<",\"ecm_gev\":"<<pythia.info.eCM()<<",\"parents\":[";
    bool first=true;
    for(int i=1;i<pythia.event.size();++i) {
      auto const& p=pythia.event[i];
      if((p.id()!=130&&p.id()!=310&&p.id()!=321&&p.id()!=-321)||
         std::find(parent_ids.begin(),parent_ids.end(),p.id())==parent_ids.end())continue;
      auto daughters=p.daughterList();
      bool copy=false;for(auto j:daughters)copy=copy||pythia.event[j].id()==p.id();
      if(copy)continue;
      if(!first)o<<',';first=false;o<<undecayed_kaon_record(i,false);
    }
    o<<"]}";return o.str();
  }
  std::string production_snapshot() const {
    // Opt-in combined inventory. Keep snapshot()'s naturally decayed-meson
    // contract unchanged; physical kaons require their separate pinned final
    // record and are counted after removing same-species generator copies.
    auto q=source_total_p4();
    std::ostringstream o;o<<std::setprecision(17)<<"{\"native_weight\":"<<pythia.info.weight()
      <<",\"ecm_gev\":"<<pythia.info.eCM()<<",\"source_total_p4\":["
      <<std::setprecision(std::numeric_limits<long double>::max_digits10)
      <<q[0]<<','<<q[1]<<','<<q[2]<<','<<q[3]<<"]"
      <<",\"source_beam_pdg_ids\":["<<pythia.event[1].id()<<','<<pythia.event[2].id()<<"]"
      <<std::setprecision(17)<<",\"parents\":[";
    bool first=true;
    for(int i=1;i<pythia.event.size();++i) {
      auto const& p=pythia.event[i];
      if(std::find(parent_ids.begin(),parent_ids.end(),p.id())==parent_ids.end())continue;
      auto daughters=p.daughterList();bool copy=false;
      for(auto j:daughters)copy=copy||pythia.event[j].id()==p.id();
      if(copy)continue;
      if(!first)o<<',';first=false;
      if(p.id()==130||p.id()==310||p.id()==321||p.id()==-321) {
        o<<undecayed_kaon_record(i,false);
      } else {
        if(daughters.empty())throw std::runtime_error("An eligible non-kaon meson was not naturally decayed");
        o<<"{\"index\":"<<i<<",\"pdg_id\":"<<p.id()<<",\"native_mass_gev\":"<<p.m()
         <<",\"p4\":["<<p.e()<<','<<p.px()<<','<<p.py()<<','<<p.pz()<<"]"
         <<",\"radiative_ancestor\":"<<radiative_ancestor(i)<<'}';
      }
    }
    o<<"]}";return o.str();
  }
  std::string attach_native_kaon_decay_vertex(int index) {
    // The final native kaon already owns tau and vDec. Never draw again or
    // alter the Pythia record, momentum, production vertex or ancestors.
    auto record=undecayed_kaon_record(index);
    auto const& p=pythia.event[index];
    auto* parent=current->barcode_to_particle(index);
    auto* vertex=new HepMC::GenVertex(HepMC::FourVector(p.xDec(),p.yDec(),p.zDec(),p.tDec()));
    vertex->add_particle_in(parent);current->add_vertex(vertex);parent->set_status(2);
    return record;
  }
  void decay_recoil(HepMC::GenParticle* recoil,HepMC::FourVector const& position) {
    if(correlated_recoil_decay&&correlated_recoil_decay(recoil,position))return;
    // The new recoil uses the ordinary source particle tables and RNG. Only a
    // private temporary Event is decayed; the original source record/counters
    // are restored, including when Pythia rejects the decay.
    // Stable e/nu recoils have no natural decay or lifetime draw. Muons
    // retain the same ordinary mayDecay/tau0 limits as the source tables.
    if(!pythia.particleData.canDecay(recoil->pdg_id()))return;
    Pythia8::Event original=pythia.event;
    Pythia8::Event generated;
    try {
      pythia.event.reset();
      auto const& p=recoil->momentum();
      auto index=pythia.event.append(recoil->pdg_id(),1,0,0,p.px(),p.py(),p.pz(),p.e(),recoil->generated_mass());
      pythia.event[index].vProd(position.x(),position.y(),position.z(),position.t());
      pythia.event[index].tau(pythia.event[index].tau0()*pythia.rndm.exp());
      if(!pythia.moreDecays())
        throw std::runtime_error("The physical recoil decay failed");
      generated=pythia.event;
    } catch(...) {pythia.event=original;throw;}
    pythia.event=original;
    // Long-lived pions and kaons may remain final under the ordinary source
    // lifetime settings. Never force a decay that those settings suppress.
    if(generated[1].isFinal())return;
    std::map<int,HepMC::GenParticle*> particles{{1,recoil}};
    for(int index=2;index<generated.size();++index) {
      auto const& p=generated[index];
      if(p.col()!=0||p.acol()!=0)throw std::runtime_error("Recoil decay unexpectedly requires hadronization");
      auto* particle=new HepMC::GenParticle(HepMC::FourVector(p.px(),p.py(),p.pz(),p.e()),
                                           p.id(),p.isFinal()?1:2);
      particle->set_generated_mass(p.m());particles[index]=particle;
    }
    recoil->set_status(2);
    std::set<int> attached;
    for(int index=1;index<generated.size();++index) {
      auto daughters=generated[index].daughterList();
      if(daughters.empty())continue;
      auto const& position=generated[daughters.front()].vProd();
      auto* vertex=new HepMC::GenVertex(HepMC::FourVector(position.px(),position.py(),position.pz(),position.e()));
      vertex->add_particle_in(particles.at(index));
      Pythia8::Vec4 total;
      for(auto child:daughters) {
        auto mothers=generated[child].motherList();
        if(mothers.size()!=1||mothers.front()!=index||!attached.insert(child).second)
          throw std::runtime_error("Recoil natural-decay graph has ambiguous ancestry");
        auto const& child_position=generated[child].vProd();
        if(child_position.px()!=position.px()||child_position.py()!=position.py()||
             child_position.pz()!=position.pz()||child_position.e()!=position.e())
          throw std::runtime_error("Recoil daughters have different production vertices");
        total+=generated[child].p();vertex->add_particle_out(particles.at(child));
      }
      auto residual=total-generated[index].p();
      if(std::max({std::abs(residual.e()),std::abs(residual.px()),std::abs(residual.py()),std::abs(residual.pz())})>
            1.e-6*std::max(1.,generated[index].e()))
        throw std::runtime_error("Recoil natural decay violates momentum conservation");
      current->add_vertex(vertex);
    }
    if(attached.size()!=size_t(generated.size()-2))
      throw std::runtime_error("Recoil natural-decay graph lost a descendant");
  }
  void validate_recoils(int parent_pdg,std::vector<int> const& recoil_pdgs,
                       std::vector<double> const& recoil_masses) const {
    if(recoil_pdgs.empty()||recoil_pdgs.size()!=recoil_masses.size())
      throw std::runtime_error("Missing or inconsistent exclusive recoils");
    bool leptonic=false;
    for(size_t i=0;i<recoil_pdgs.size();++i) {
      auto id=std::abs(recoil_pdgs[i]);
      bool lepton=id==11||id==12||id==13||id==14;
      leptonic=leptonic||lepton;
      bool massless=id==22||id==12||id==14;
      if((!lepton&&id!=22&&id!=111&&id!=211&&id!=221&&id!=311&&id!=321&&id!=441&&id!=443)||
         !std::isfinite(recoil_masses[i])||
         (massless?recoil_masses[i]!=0.:recoil_masses[i]<=0.))
        throw std::runtime_error("Invalid exclusive recoil identity or mass");
    }
    if(leptonic) {
      int charged=0,neutrino=0;
      for(auto id:recoil_pdgs) {
        if(std::abs(id)==11||std::abs(id)==13)charged=id;
        if(std::abs(id)==12||std::abs(id)==14)neutrino=id;
      }
      int sign=parent_pdg>0?1:-1;
      if(std::abs(parent_pdg)!=321||recoil_pdgs.size()!=2||
         charged*sign>=0||neutrino*sign<=0||std::abs(neutrino)!=std::abs(charged)+1)
        throw std::runtime_error("Require a charged-kaon lepton and matching neutrino pair");
    }
    int recoil_charge=0;
    for(auto id:recoil_pdgs)recoil_charge+=pythia.particleData.chargeType(id);
    if(recoil_charge!=pythia.particleData.chargeType(parent_pdg))
      throw std::runtime_error("Exclusive decay violates charge conservation");
  }
  void inject(int index,std::vector<double> const& values,
              std::vector<int> const& recoil_pdgs,std::vector<double> const& recoil_masses) {
    if(!current||values.size()!=18+4*recoil_pdgs.size())
      throw std::runtime_error("Invalid decay injection input");
    auto* parent=current->barcode_to_particle(index);
    if(!parent||std::find(parent_ids.begin(),parent_ids.end(),parent->pdg_id())==parent_ids.end()||!parent->end_vertex())
      throw std::runtime_error("Selected meson is absent from the source graph");
    validate_recoils(parent->pdg_id(),recoil_pdgs,recoil_masses);
    auto position=parent->end_vertex()->position();
    std::set<HepMC::GenVertex*> vertices;
    std::set<HepMC::GenParticle*> descendants;
    std::vector<HepMC::GenVertex*> pending{parent->end_vertex()};
    while(!pending.empty()) {
      auto* vertex=pending.back();pending.pop_back();
      if(!vertices.insert(vertex).second)continue;
      for(auto p=vertex->particles_out_const_begin();p!=vertex->particles_out_const_end();++p) {
        descendants.insert(*p);
        if((*p)->end_vertex())pending.push_back((*p)->end_vertex());
      }
    }
    for(auto* vertex:vertices)
      for(auto p=vertex->particles_in_const_begin();p!=vertex->particles_in_const_end();++p)
        if(*p!=parent&&!descendants.count(*p))
          throw std::runtime_error("Meson decay subtree shares an external incoming particle");
    std::vector<std::string> spectators;
    for(auto p=current->particles_begin();p!=current->particles_end();++p)
      if(*p!=parent&&!descendants.count(*p))spectators.push_back(particle_signature(*p));
    for(auto* vertex:vertices) {
      std::vector<HepMC::GenParticle*> attached;
      for(auto p=vertex->particles_in_const_begin();p!=vertex->particles_in_const_end();++p)attached.push_back(*p);
      for(auto p=vertex->particles_out_const_begin();p!=vertex->particles_out_const_end();++p)attached.push_back(*p);
      for(auto* particle:attached)vertex->remove_particle(particle);
    }
    for(auto* vertex:vertices){current->remove_vertex(vertex);delete vertex;}
    for(auto* particle:descendants)delete particle;
    auto p4=[&](int offset){return HepMC::FourVector(values[offset+1],values[offset+2],values[offset+3],values[offset]);};
    auto* decay=new HepMC::GenVertex(position);
    auto* dark=new HepMC::GenParticle(p4(0),32,3);
    auto const offset=12+4*recoil_pdgs.size();
    dark->set_generated_mass(values[offset+4]);
    std::vector<HepMC::GenParticle*> recoils;
    decay->add_particle_in(parent);decay->add_particle_out(dark);
    for(size_t i=0;i<recoil_pdgs.size();++i) {
      auto* recoil=new HepMC::GenParticle(p4(4+4*i),recoil_pdgs[i],1);
      recoil->set_generated_mass(recoil_masses[i]);
      decay->add_particle_out(recoil);recoils.push_back(recoil);
    }
    current->add_vertex(decay);
    for(auto* recoil:recoils)if(recoil->pdg_id()!=22)decay_recoil(recoil,position);
    auto* endpoint=new HepMC::GenVertex(HepMC::FourVector(position.x()+values[offset],
      position.y()+values[offset+1],position.z()+values[offset+2],position.t()+values[offset+3]));
    auto* muon=new HepMC::GenParticle(p4(4+4*recoil_pdgs.size()),13,1);
    auto* antimuon=new HepMC::GenParticle(p4(8+4*recoil_pdgs.size()),-13,1);
    muon->set_generated_mass(values[offset+5]);antimuon->set_generated_mass(values[offset+5]);
    endpoint->add_particle_in(dark);endpoint->add_particle_out(muon);endpoint->add_particle_out(antimuon);
    current->add_vertex(endpoint);
    for(auto const& expected:spectators) {
      auto barcode=std::stoi(expected.substr(0,expected.find(' ')));
      auto const* unchanged=current->barcode_to_particle(barcode);
      if(!unchanged||particle_signature(unchanged)!=expected)
        throw std::runtime_error("Meson injection changed a spectator particle or its vertices");
    }
  }
  std::string recoil_record(int index) const {
    auto const* parent=current?current->barcode_to_particle(index):nullptr;
    if(!parent||!parent->end_vertex())throw std::runtime_error("Injected recoil vertex is absent");
    std::ostringstream o;o<<std::setprecision(17)<<'[';bool first=true;
    auto const* vertex=parent->end_vertex();
    for(auto p=vertex->particles_out_const_begin();p!=vertex->particles_out_const_end();++p) {
      auto const* recoil=*p;if(recoil->pdg_id()==32)continue;
      if(!first)o<<',';first=false;
      auto const& q=recoil->momentum();
      o<<"{\"pdg_id\":"<<recoil->pdg_id()<<",\"mass_gev\":"<<recoil->generated_mass()
       <<",\"status\":"<<recoil->status()<<",\"natural_decay\":"<<(recoil->end_vertex()?"true":"false")
       <<",\"p4\":["<<q.e()<<','<<q.px()<<','<<q.py()<<','<<q.pz()<<"]}";
    }
    o<<']';return o.str();
  }
  std::string summary() const {
    std::ostringstream o;o<<std::setprecision(17)<<"{\"source_calls\":"<<calls
     <<",\"generator_failures\":"<<failures<<",\"source_collisions\":"<<collisions
     <<",\"sigma_source_mb\":"<<pythia.info.sigmaGen()<<",\"sigma_source_error_mb\":"<<pythia.info.sigmaErr()
     <<",\"native_n_tried\":"<<pythia.info.nTried()<<",\"native_n_accepted\":"<<pythia.info.nAccepted()<<'}';return o.str();
  }
  std::string statistics() {
    pythia.readString("Stat:showProcessLevel=on");
    std::ostringstream text;
    auto* previous=std::cout.rdbuf(text.rdbuf());
    try {pythia.stat();}catch(...){std::cout.rdbuf(previous);throw;}
    std::cout.rdbuf(previous);
    return text.str();
  }
};
}
'''


EVTGEN_CPP = r'''
#include "EvtGen/EvtGen.hh"
#include "EvtGenBase/EvtParticleFactory.hh"
#include "EvtGenBase/EvtRandom.hh"
#include "EvtGenBase/EvtRandomEngine.hh"
#include "EvtGenBase/EvtSpinDensity.hh"
#include "EvtGenBase/EvtVectorParticle.hh"
#include "EvtGenExternal/EvtExternalGenList.hh"
#include <complex>
namespace ShiftMesonEvtGen {
using Complex=std::complex<double>;
class Random:public EvtRandomEngine {
  ShiftMesonNative::Source& source;
public:
  explicit Random(ShiftMesonNative::Source& value):source(value){}
  double random()override{return source.pythia.rndm.flat();}
};
class RecoilDecay {
  ShiftMesonNative::Source& source;
  Random random;
  EvtExternalGenList external;
  std::list<EvtDecayBase*> models;
  EvtGen generator;
  bool allowed(EvtParticle* particle)const {
    auto const& table=source.pythia.particleData;
    int id=particle->getPDGId();
    if(!table.isParticle(id)||!table.canDecay(id)||!table.mayDecay(id))return false;
    auto& settings=source.pythia.settings;
    if(settings.flag("ParticleDecays:limitTau0")&&
       table.tau0(id)>settings.parm("ParticleDecays:tau0Max"))return false;
    if(settings.flag("ParticleDecays:limitTau")&&
       particle->getLifetime()>settings.parm("ParticleDecays:tauMax"))return false;
    return true;
  }
  void attach(EvtParticle* generated,HepMC::GenParticle* particle,
              HepMC::FourVector const& birth) {
    if(generated->getNDaug()==0) {
      int id=std::abs(generated->getPDGId());
      if(id<=6||id==21)throw std::runtime_error("EvtGen left an unhadronized recoil parton");
      return;
    }
    // EvtGen may have generated weak descendants. Retain them as undecayed
    // particles whenever the unchanged native source would stop that decay;
    // material transport then determines their physical fate.
    if(!allowed(generated))return;
    auto const& q=particle->momentum();double mass=generated->mass();
    if(!(mass>0.)||!std::isfinite(generated->getLifetime())||generated->getLifetime()<0.)
      throw std::runtime_error("Invalid correlated recoil flight");
    double scale=generated->getLifetime()/mass;
    auto* vertex=new HepMC::GenVertex(HepMC::FourVector(birth.x()+q.px()*scale,
        birth.y()+q.py()*scale,birth.z()+q.pz()*scale,birth.t()+q.e()*scale));
    vertex->add_particle_in(particle);particle->set_status(2);
    std::vector<std::pair<EvtParticle*,HepMC::GenParticle*>> children;
    std::array<double,4> total{};
    for(size_t index=0;index<generated->getNDaug();++index) {
      auto* daughter=generated->getDaug(index);auto p=daughter->getP4Lab();
      auto* child=new HepMC::GenParticle(HepMC::FourVector(p.get(1),p.get(2),p.get(3),p.get(0)),
                                         daughter->getPDGId(),1);
      child->set_generated_mass(daughter->mass());vertex->add_particle_out(child);
      for(int i=0;i<4;++i)total[i]+=p.get(i);
      children.emplace_back(daughter,child);
    }
    // Match decay_recoil's existing external-generator graph contract.
    // EvtGen/PHOTOS descendants need not close at machine epsilon; do not
    // repair their momenta. Whole-event and mass-shell audits remain separate.
    double tolerance=1.e-6*std::max(1.,std::abs(q.e()));
    if(std::max({std::abs(total[0]-q.e()),std::abs(total[1]-q.px()),
        std::abs(total[2]-q.py()),std::abs(total[3]-q.pz())})>tolerance) {
      std::ostringstream message;message<<std::setprecision(17)
        <<"Correlated recoil decay violates momentum conservation: pdg="<<particle->pdg_id()
        <<" parent="<<q.e()<<','<<q.px()<<','<<q.py()<<','<<q.pz()
        <<" daughters="<<total[0]<<','<<total[1]<<','<<total[2]<<','<<total[3]
        <<" tolerance="<<tolerance;
      throw std::runtime_error(message.str());
    }
    source.current->add_vertex(vertex);
    for(auto const& child:children)attach(child.first,child.second,vertex->position());
  }
public:
  RecoilDecay(ShiftMesonNative::Source& value,std::string const& decay,
              std::string const& pdl,std::string const& xml)
      :source(value),random(value),external(false,xml,"gamma",true),
       models(external.getListOfModels()),
       generator(decay.c_str(),pdl.c_str(),&random,external.getPhotosModel(),&models) {
    if(source.pythia.settings.flag("ParticleDecays:limitRadius")||
       source.pythia.settings.flag("ParticleDecays:limitCylinder"))
      throw std::runtime_error("Correlated recoil adapter needs a qualified native spatial decay policy");
  }
  bool decay(HepMC::GenParticle* recoil,HepMC::FourVector const& position) {
    if(recoil->pdg_id()!=441&&recoil->pdg_id()!=443)return false;
    if(!source.pythia.particleData.mayDecay(recoil->pdg_id()))
      throw std::runtime_error("A spin-carrying recoil must decay in the qualified source adapter");
    EvtRandom::setRandomEngine(&random);
    auto const& p=recoil->momentum();
    auto* generated=EvtParticleFactory::particleFactory(EvtPDL::evtIdFromStdHep(recoil->pdg_id()),
        EvtVector4R(p.e(),p.px(),p.py(),p.pz()));
    if(!generated)throw std::runtime_error("EvtGen has no physical recoil definition");
    try {
      if(recoil->pdg_id()==443) {
        auto const& data=source.recoil_spin_data;
        if(data.size()!=30)throw std::runtime_error("Missing conditional Jpsi spin density and basis");
        Complex overlap[3][3],density[3][3];
        for(int i=0;i<3;++i)for(int j=0;j<3;++j) {
          density[i][j]=Complex(data[2*(3*i+j)],data[2*(3*i+j)+1]);
          auto basis=generated->epsParent(i);Complex product{};
          for(int k=0;k<4;++k)product+=(k==0?1.:-1.)*
              std::conj(Complex(real(basis.get(k)),imag(basis.get(k))))*data[18+4*j+k];
          overlap[i][j]=-product;
        }
        double precision=2048*std::numeric_limits<double>::epsilon()*
            std::max(1.,p.e()*p.e()/(recoil->generated_mass()*recoil->generated_mass()));
        for(int i=0;i<3;++i)for(int j=0;j<3;++j) {
          Complex product{};for(int k=0;k<3;++k)product+=overlap[i][k]*std::conj(overlap[j][k]);
          if(std::abs(product-Complex(i==j?1.:0.,0.))>precision)
            throw std::runtime_error("Correlated recoil polarization bases are not orthonormal");
        }
        EvtSpinDensity rho;rho.setDim(3);
        for(int i=0;i<3;++i)for(int j=0;j<3;++j) {
          Complex value{};
          for(int b=0;b<3;++b)for(int c=0;c<3;++c)
            value+=overlap[i][b]*density[b][c]*std::conj(overlap[j][c]);
          rho.set(i,j,EvtComplex(value.real(),value.imag()));
        }
        if(!rho.check())throw std::runtime_error("Invalid conditional EvtGen recoil density");
        generated->setSpinDensityForward(rho);
      } else generated->setDiagonalSpinDensity();
      generator.generateDecay(generated);
      if(generated->getNDaug()==0)throw std::runtime_error("Correlated recoil has no actual daughters");
      attach(generated,recoil,position);
      generated->deleteTree();source.recoil_spin_data.clear();
    } catch(...) {generated->deleteTree();throw;}
    return true;
  }
};
struct Callback {
  std::shared_ptr<RecoilDecay> engine;
  bool operator()(HepMC::GenParticle* particle,HepMC::FourVector const& position)const {
    return engine->decay(particle,position);
  }
};
void enable(ShiftMesonNative::Source& source,std::string const& decay,
             std::string const& pdl,std::string const& xml) {
  auto engine=std::make_shared<RecoilDecay>(source,decay,pdl,xml);
  source.correlated_recoil_decay=Callback{engine};
}
}
'''


def cmssw_native_libraries():
    """Resolve this release's libraries, including versioned HepMC sonames.

    Clean batch environments need not expose unversioned libHepMC.so links.
    SCRAM's tool configuration is the authority; never fall back to another
    HepMC installation inherited from an interactive environment.
    """
    base = Path(os.environ['CMSSW_BASE'])
    libraries = []
    for tool, names in [('hepmc', ('HepMC', 'HepMCfio')), ('pythia8', ('pythia8',))]:
        configuration = subprocess.check_output(['scram', 'tool', 'info', tool],
            cwd=base/'src', text=True)
        directories = [line.split('=',1)[1] for line in configuration.splitlines() if line.startswith('LIBDIR=')]
        if len(directories) != 1 or not Path(directories[0]).is_absolute():
            raise RuntimeError('Cannot resolve the current SCRAM library directory: ' + tool)
        directory = Path(directories[0])
        for name in names:
            candidates = {path.resolve() for path in directory.glob('lib'+name+'.so*') if path.is_file()}
            if len(candidates) != 1:
                raise RuntimeError('Missing or ambiguous current CMSSW library: ' + name + ' in ' + str(directory))
            libraries.append(str(candidates.pop()))
    return libraries


def native_source(settings, seed, parent_ids=(221, 331)):
    if not os.environ.get('CMSSW_BASE') or not os.environ.get('PYTHIA8DATA'):
        raise RuntimeError('Enter the prepared CMSSW runtime; no shared build is performed')
    if type(seed) is not int or not 1 <= seed <= 899999900:
        raise ValueError('Require an explicit valid unused Pythia seed')
    if (not parent_ids or len(set(parent_ids)) != len(parent_ids) or
            any(type(pdg) is not int or pdg not in
                (221, 331, 113, 213, -213, 223, 333, 313, -313, 323, -323,
                 130, 310, 321, -321, 441, 10441, 20443, 445, 10443, 100443) for pdg in parent_ids)):
        raise ValueError('Require distinct explicitly supported meson source parents')
    import ROOT
    libraries = cmssw_native_libraries()
    for library in libraries:
        if ROOT.gSystem.Load(library) < 0:
            raise RuntimeError('Cannot load the current CMSSW library: ' + library)
    print('Native meson libraries: ' + json.dumps(libraries), flush=True)
    if not hasattr(ROOT, 'ShiftMesonNative') and not ROOT.gInterpreter.Declare(CPP):
        raise RuntimeError('Cannot declare the private native meson source')
    commands = ROOT.std.vector('string')()
    for setting in settings:
        if setting.lower().replace(' ', '').startswith('random:'):
            raise ValueError('The explicit source seed must not be overridden by generator settings')
        commands.push_back(setting)
    parents = ROOT.std.vector('int')()
    for pdg in parent_ids:
        parents.push_back(pdg)
    source = ROOT.ShiftMesonNative.Source(commands, seed, parents)
    if any(abs(pdg) in (441,10441,20443,445,10443,100443) for pdg in parent_ids):
        source._shift_evtgen_provenance = enable_correlated_recoils(source)
    return source


def enable_correlated_recoils(source, *, decay_file=None):
    """Use this release's EvtGen and native RNG for spin-carrying recoils.

    The explicit decay-file override is for bounded native validation only.
    Actual source runs use and fingerprint the release's default tables.
    """
    import ROOT
    base=Path(os.environ['CMSSW_BASE'])
    configuration=subprocess.check_output(['scram','tool','info','evtgen'],cwd=base/'src',text=True)
    values=dict(line.split('=',1) for line in configuration.splitlines() if '=' in line)
    include=Path(values['INCLUDE']);directory=Path(values['LIBDIR'])
    ROOT.gInterpreter.AddIncludePath(str(include))
    libraries=[]
    for name in ('EvtGen','EvtGenExternal'):
        paths={path.resolve() for path in directory.glob('lib'+name+'.so*') if path.is_file()}
        if len(paths)!=1:
            raise RuntimeError('Missing or ambiguous release EvtGen library: '+name)
        library=paths.pop()
        if ROOT.gSystem.Load(str(library))<0:
            raise RuntimeError('Cannot load the release correlated-recoil library: '+str(library))
        libraries.append(library)
    if not hasattr(ROOT,'ShiftMesonEvtGen') and not ROOT.gInterpreter.Declare(EVTGEN_CPP):
        raise RuntimeError('Cannot declare the correlated heavy-recoil adapter')
    data=Path(os.environ['EVTGENDATA'])
    decay=Path(decay_file) if decay_file is not None else data/'DECAY.DEC'
    pdl=data/'evt.pdl'
    ROOT.ShiftMesonEvtGen.enable(source,str(decay),str(pdl),os.environ['PYTHIA8DATA'])
    return dict(adapter='EvtGen-forward-density-v1',native_source_rng_unchanged=True,
        native_decay_limits_unchanged=True,
        files=[dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
               for path in [*libraries,decay,pdl]])


def resolved_meson_production_parameters(source):
    """Read initialized fragmentation weights and dedicated quarkonium flags.

    These are generator parameters, not inclusive species probabilities or
    bounds on physical hadron production. Zero direct-fragmentation weights
    do not remove natural feed-down from heavier particles.
    """
    result = {}
    for prefix in ('StringFlav:','Onia:','Charmonium:','Bottomonium:',
                   'OniaShower:','CharmoniumShower:','BottomoniumShower:'):
        group = {}
        for kind,getter,convert in (('flags','getFlagMap',bool),('modes','getModeMap',int),
                                    ('parameters','getParmMap',float)):
            entries = getattr(source.pythia.settings,getter)(prefix)
            group[kind] = {str(entry.second.name):dict(value=convert(entry.second.valNow),
                default=convert(entry.second.valDefault)) for entry in entries}
        # Individual NRQCD subprocess switches and their state lists are vectors.
        for kind,getter,convert in (('flag_vectors','getFVecMap',bool),
                                    ('mode_vectors','getMVecMap',int),
                                    ('parameter_vectors','getPVecMap',float)):
            entries = getattr(source.pythia.settings,getter)(prefix)
            group[kind] = {str(entry.second.name):dict(
                value=[convert(value) for value in entry.second.valNow],
                default=[convert(value) for value in entry.second.valDefault]) for entry in entries}
        result[prefix] = group
    if not result['StringFlav:']['parameters']:
        raise RuntimeError('Initialized StringFlav parameters are missing')
    directory = Path(os.environ['PYTHIA8DATA'])
    references = [directory/name for name in ('FlavourSelection.xml','OniaProcesses.xml','OniaShowers.xml',
                                             'MultipartonInteractions.xml')
                  if (directory/name).is_file()]
    return dict(schema='shift-resolved-meson-production-parameters-v1',parameters=result,
        mpi_process_level=int(source.pythia.settings.mode('MultipartonInteractions:processLevel')),
        onium_shower_enabled=bool(source.pythia.info.getOniumShower()),
        documentation=[dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                       for path in references],
        interpretation='Relative generator weights and switches, not measured inclusive production probabilities. Primary hard-process flags do not disable the separate NRQCD processes included by MPI processLevel=3. Zero configured fragmentation weight or zero MC count does not establish a zero physical rate.')


def _sampled_recoils(sampled_decay):
    """Read the shared exclusive-recoil contract before graph mutation."""
    if 'recoil_p4s' in sampled_decay:
        return [dict(pdg_id=pdg, mass_gev=mass, p4=momentum) for pdg, mass, momentum in
            zip(sampled_decay['recoil_pdg_ids'], sampled_decay['recoil_masses_gev'],
                sampled_decay['recoil_p4s'], strict=True)]
    if 'recoils' in sampled_decay:
        return sampled_decay['recoils']
    key = 'recoil_p4' if 'recoil_p4' in sampled_decay else 'photon_p4'
    return [dict(pdg_id=sampled_decay.get('recoil_pdg_id', 22),
        mass_gev=sampled_decay.get('recoil_mass_gev', 0.), p4=sampled_decay[key])]


def _native_recoil_vectors(recoils):
    import ROOT
    identifiers, masses = ROOT.std.vector('int')(), ROOT.std.vector('double')()
    for recoil in recoils:
        identifiers.push_back(recoil['pdg_id'])
        masses.push_back(recoil['mass_gev'])
    return identifiers, masses


def inject_decay(source, parent_index, sampled_decay):
    import ROOT
    if sampled_decay.get('recoil_spin_correlations_required'):
        if not getattr(source,'_shift_evtgen_provenance',None):
            raise ValueError('A conditional recoil density needs the qualified native spin adapter')
        density=sampled_decay.get('recoil_spin_density_matrix')
        basis=sampled_decay.get('recoil_polarization_basis_p4')
        if (not isinstance(density,list) or len(density)!=3
                or any(not isinstance(row,list) or len(row)!=3 for row in density)
                or not isinstance(basis,list) or len(basis)!=3 or any(len(row)!=4 for row in basis)):
            raise ValueError('Missing conditional recoil spin matrix or transported polarization basis')
        packed=[value for row in density for entry in row for value in entry]
        packed.extend(value for row in basis for value in row)
        if len(packed)!=30 or not all(math.isfinite(value) for value in packed):
            raise ValueError('Nonfinite or malformed conditional recoil spin information')
        source.recoil_spin_data.clear()
        for value in packed:source.recoil_spin_data.push_back(value)
    values = ROOT.std.vector('double')()
    recoils = _sampled_recoils(sampled_decay)
    momenta = [sampled_decay['dark_photon_p4']] + [row['p4'] for row in recoils] + [
        sampled_decay['muon_p4'], sampled_decay['antimuon_p4']]
    for momentum in momenta:
        for value in momentum:
            values.push_back(value)
    for value in sampled_decay['decay_displacement_mm']:
        values.push_back(value)
    values.push_back(sampled_decay['decay_ct_mm'])
    values.push_back(sampled_decay['dark_photon_mass_gev'])
    values.push_back(sampled_decay['muon_mass_gev'])
    identifiers, masses = _native_recoil_vectors(recoils)
    source.inject(parent_index, values, identifiers, masses)
    if sampled_decay.get('recoil_spin_correlations_required'):
        if not source.recoil_spin_data.empty():
            raise RuntimeError('Native recoil adapter did not consume the conditional density')
        sampled_decay.update(recoil_spin_density_attached=True,
            recoil_spin_adapter='EvtGen-forward-density-v1',
            recoil_decay_provenance=source._shift_evtgen_provenance)
    # Pin the actual ordinary recoil outcome for the independent FWLite audit.
    sampled_decay['recoil_records'] = json.loads(str(source.recoil_record(parent_index)))
    if len(recoils) == 1:
        sampled_decay['recoil_natural_decay_required'] = sampled_decay['recoil_records'][0]['natural_decay']


def inject_undecayed_kaon_decay(source, parent_index, sampled_decay, *, native_parent_record):
    """Attach at native kaon vDec, then reuse ordinary recoil/graph injection.

    The C++ primitive rejects flavor311, an existing end vertex, second use,
    changed p4/production coordinates, and an unsampled/nonphysical parent.
    The pinned snapshot record must still match before attachment, including
    tau/vDec; a second draw or changed native record is rejected. Only HepMC
    status/endvertex are added. This is vacuum construction, not a material model.
    """
    if type(parent_index) is not int or parent_index <= 0:
        raise ValueError('Explicit positive native kaon index required')
    if (sampled_decay.get('parent_flight_included') is not False or
            'native_parent_flight' in sampled_decay or
            any(key in sampled_decay for key in ('parent_uniform','parent_proper_decay_length_mm',
                                                'parent_decay_position_mm'))):
        raise ValueError('Supply only a new relative A-prime flight, without resampling the parent')
    record=json.loads(str(source.undecayed_kaon_record(parent_index)))
    if (record != native_parent_record or
            sampled_decay.get('parent_pdg_id') != record['pdg_id']):
        raise ValueError('The selected native kaon snapshot/lifetime or sampler identity changed')
    # Reject an unsupported or wrong-flavor weak recoil before attaching an
    # endvertex, rather than leaving an empty half-injected parent on failure.
    identifiers,masses=_native_recoil_vectors(_sampled_recoils(sampled_decay))
    source.validate_recoils(record['pdg_id'],identifiers,masses)
    attached=json.loads(str(source.attach_native_kaon_decay_vertex(parent_index)))
    if attached != record:
        raise RuntimeError('The native kaon record changed during decay attachment')
    sampled_decay['native_parent_flight']=record
    inject_decay(source,parent_index,sampled_decay)
    sampled_decay['native_parent_flight_attached']=True
