// Code style: AI-code. AI-generated native regression for reviewed Dstar flights.
#include "SimG4Core/Generators/interface/PromptDecaySupport.h"
#include <fstream>
#include <memory>

int main(int argc,char** argv) {
  if (argc!=5) return 2;
  const bool expected=std::string(argv[4])=="accept";
  try {
    std::ifstream input(argv[1]);const auto rows=nlohmann::json::parse(input);
    auto row=rows.at(std::stoi(argv[2]));const std::string mode=argv[3];
    auto value=[&](const char* key,unsigned i) {
      return sim::recordedPrimaryAuthority::exactDouble(row.at(key)[i]);
    };
    HepMC::GenEvent event;event.set_event_number(row.at("hepmc_event_number"));
    if (mode=="event") event.set_event_number(event.event_number()+1);
    auto* birth=new HepMC::GenVertex(HepMC::FourVector(value("birth_hex",0),value("birth_hex",1),value("birth_hex",2),value("birth_hex",3)));
    auto* end=new HepMC::GenVertex(HepMC::FourVector(value("end_hex",0),value("end_hex",1),value("end_hex",2),value("end_hex",3)));
    auto* p=new HepMC::GenParticle(HepMC::FourVector(value("p4_hex",0),value("p4_hex",1),value("p4_hex",2),value("p4_hex",3)),row.at("pdg"),2);
    p->set_generated_mass(sim::recordedPrimaryAuthority::exactDouble(row.at("generated_mass_hex")));
    birth->add_particle_out(p);end->add_particle_in(p);event.add_vertex(birth);event.add_vertex(end);
    p->suggest_barcode(row.at("barcode"));
    for (int barcode:row.at("daughter_barcodes").get<std::vector<int>>()) {
      auto* child=new HepMC::GenParticle(HepMC::FourVector(0.,0.,1.,2.),211,1);
      end->add_particle_out(child);child->suggest_barcode(barcode);
    }
    if (mode=="barcode") p->suggest_barcode(p->barcode()+1000);
    if (mode=="pdg") p->set_pdg_id(423);
    if (mode=="status") p->set_status(1);
    if (mode=="mass") p->set_generated_mass(std::nextafter(p->generated_mass(),3.));
    if (mode=="momentum") {auto q=p->momentum();q.setPx(std::nextafter(q.px(),1.));p->set_momentum(q);}
    if (mode=="birth") {auto q=birth->position();q.setX(std::nextafter(q.x(),1.));birth->set_position(q);}
    if (mode=="end") {auto q=end->position();q.setT(std::nextafter(q.t(),1.));end->set_position(q);}
    if (mode=="daughter") (*end->particles_out_const_begin())->suggest_barcode(10000);
    const double budget=mode=="zero-budget" ? 0. : mode=="other-budget" ? 9.e-6 : 1.e-5;
    const bool actual=sim::promptDecay::withinPromptBudget(*p,budget);
    if (actual!=expected) {std::cerr<<"unexpected acceptance "<<actual<<" in "<<mode<<std::endl;return 1;}
    return 0;
  } catch (const std::exception& e) {
    std::cerr<<e.what()<<std::endl;return expected ? 1 : 0;
  }
}
