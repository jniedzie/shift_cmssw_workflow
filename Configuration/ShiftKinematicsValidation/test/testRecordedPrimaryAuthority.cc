// Code style: AI-code. AI-generated exact observed-vector authority regression.
#include "SimG4Core/Generators/interface/RecordedPrimaryMomentum.h"
#include "G4ParticleTable.hh"
#include "G4MesonConstructor.hh"
#include "G4BaryonConstructor.hh"
#include <iostream>
#include <vector>
struct P4 {double x,y,z,t;double px()const{return x;}double py()const{return y;}double pz()const{return z;}double e()const{return t;}};
struct Case {long long event;int barcode,pdg;P4 p;double mass,bound;std::string gen;};
const std::vector<Case> cases={
 {19,26,211,{-0x1.5a5fdde0e2e8bp-2,-0x1.32ae3d40743dcp-1,-0x1.f07ebe075fee7p+9,0x1.f07ec628682b3p+9},0x1.1dd714a451d96p-3,1.0855612681574012e-9,"eee69c35f5080bac204235173dbc12f1dd8d7baf0a1566f05d768bc9a32ac4b5"},
 {19,27,-211,{-0x1.a1648d1378030p-7,0x1.2314a1691d6a8p-2,-0x1.79f664f83525fp+5,0x1.79f89246542f6p+5},0x1.1dd714a451d96p-3,2.8124462662181462e-8,"eee69c35f5080bac204235173dbc12f1dd8d7baf0a1566f05d768bc9a32ac4b5"},
 {11,67,2212,{-0x1.1b29ec4e5891ep-4,0x1.91f81ae1f3480p-4,-0x1.98270a8ad6fa2p+7,0x1.982829e227734p+7},0x1.e064ece9a2c67p-1,3.884344680596187e-08,"a0de7ab0e0e355d3fb1e0939e55ce61145c719b46cd06ffcc79c977b48ac2637"},
 {11,68,-2112,{-0x1.88fc758726818p-5,-0x1.489ff8d95cad8p+0,-0x1.eff6b7772102bp+9,0x1.eff6e14dbe838p+9},0x1.e10f51ac9afe2p-1,2.1012296830115616e-08,"a0de7ab0e0e355d3fb1e0939e55ce61145c719b46cd06ffcc79c977b48ac2637"}
};
void require(bool yes,const char* why){if(!yes)throw std::runtime_error(why);}
std::string hex(double value){std::ostringstream out;out<<std::hexfloat<<value;return out.str();}
int main(int argc,char** argv){
 if(argc==2 && std::string(argv[1])=="cases"){
  nlohmann::json rows=nlohmann::json::array();
  for(const auto& c:cases)rows.push_back({{"source_gen_sha256",c.gen},{"hepmc_event_number",c.event},{"barcode",c.barcode},{"pdg",c.pdg},
   {"p4_hex",{hex(c.p.x),hex(c.p.y),hex(c.p.z),hex(c.p.t)}},{"generated_mass_hex",hex(c.mass)},{"maximum_energy_residual_GeV",c.bound}});
  std::cout<<rows.dump()<<'\n';return 0;
 }
 if(argc!=4)return 2;
 auto c=cases.at(std::stoul(argv[1]));const std::string mode=argv[2];const bool shouldAccept=std::string(argv[3])=="accept";
 G4MesonConstructor().ConstructParticle();G4BaryonConstructor().ConstructParticle();G4ParticleTable::GetParticleTable()->SetReadiness();
 require(sim::recordedPrimaryAuthority::bits(0.)!=sim::recordedPrimaryAuthority::bits(-0.),"Signed zero identity lost");
 if(mode=="wrong-event")++c.event;
 if(mode=="wrong-barcode")++c.barcode;
 if(mode=="wrong-pdg")c.pdg=-c.pdg;
 if(mode=="wrong-px")c.p.x=std::nextafter(c.p.x,1.);
 if(mode=="wrong-mass")c.mass=std::nextafter(c.mass,1.);
 if(mode=="over-cap")c.p.t+=.01*c.p.t;
 if(mode=="legacy"){c.p={.3,.4,1.,std::hypot(std::hypot(.3,.4,1.),c.mass)};}
 G4PrimaryParticle target(c.pdg);bool accepted=false;
 try{sim::setRecordedPrimaryMomentum(target,c.p,c.mass,6800.,c.event,c.barcode);accepted=true;}
 catch(const std::exception& error){if(shouldAccept){std::cerr<<error.what()<<'\n';return 1;}}
 require(accepted==shouldAccept,"Exact-particle acceptance classification changed");
 if(accepted){
  const double physical=std::hypot(std::hypot(c.p.x,c.p.y,c.p.z),c.mass);
  const double expected=mode=="legacy"?c.p.t:physical;
  require(std::abs(target.GetTotalEnergy()/GeV-expected)<=4.e-16*std::max(1.,expected),"Transported energy differs from deterministic on-shell value");
  require(std::abs(target.GetMass()/GeV-c.mass)<=4.e-16*std::max(1.,c.mass),"Generated mass changed");
  const auto momentum=target.GetMomentum()/GeV;
  require(std::abs(momentum.x()-c.p.x)<=8.e-15*std::max(1.,std::abs(c.p.x)),"Input px changed");
  require(std::abs(momentum.y()-c.p.y)<=8.e-15*std::max(1.,std::abs(c.p.y)),"Input py changed");
  require(std::abs(momentum.z()-c.p.z)<=8.e-15*std::max(1.,std::abs(c.p.z)),"Input pz changed");
 }
 std::cout<<"passed=true\n";
}
