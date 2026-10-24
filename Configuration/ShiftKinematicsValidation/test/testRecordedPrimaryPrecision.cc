// Code style: AI-code. AI-generated exact saved-vector and malformed-input regression.
#include "SimG4Core/Generators/interface/RecordedPrimaryMomentum.h"
#include "G4ParticleTable.hh"
#include "G4BosonConstructor.hh"
#include <iostream>
#include <vector>
struct P4 { double x,y,z,t;double px()const{return x;}double py()const{return y;}double pz()const{return z;}double e()const{return t;} };
struct Case {double x,y,z,e,m,scale;bool oldFails;};
void require(bool value,const char* why){if(!value)throw std::runtime_error(why);}
bool accept(P4 p,double m,double scale){G4PrimaryParticle g(22);try{sim::setRecordedPrimaryMomentum(g,p,m,scale);return true;}catch(const std::runtime_error&){return false;}}
int main(){ G4BosonConstructor().ConstructParticle(); G4ParticleTable::GetParticleTable()->SetReadiness();
 const std::vector<Case> cases={
  {0x1.3497fd491ce5bp-2,-0x1.dba7506071cacp-3,-0x1.517c98d600fd0p+5,0x1.51809566dfbecp+5,0x1.1dd6e04c05921p-3,0x1.a8ffffffffffep+12,true},
  {-0x1.13e8baea18de0p-1,0x1.bf5682d644572p-2,-0x1.6302aa8a1a5a7p+11,0x1.6302ad07585a5p+11,0x1.1d9d3458cd20bp+0,0x1.a8ffffffffffep+12,false},
  {0x1.7956a09c60ff1p-1,0x1.52adf8a9d998ap-3,-0x1.9c5be06218e5ep+8,0x1.9c5c2164fdb37p+8,0x1.fd8d79d0a6762p-2,0x1.a8ffffffffffep+12,true},
  {0x1.7956a09c60ff1p-1,0x1.52adf8a9d998ap-3,-0x1.9c5be06218e5ep+8,0x1.9c5c2164fdb37p+8,0x1.fd8d79d0a6762p-2,0x1.a8ffffffffffep+12,true},
  {0x1.ca5b860ffea9cp-4,0x1.f911116ed9ae8p-2,-0x1.55bd6e9172b14p+11,0x1.55bd704535b05p+11,0x1.e064ece9a2c67p-1,0x1.a8ffffffffffep+12,false},
  {-0x1.48370ecbe421cp-1,0x1.86c5aaf9eac02p-3,-0x1.176d1f66ce83ap+6,0x1.17708b496e287p+6,0x1.1dd714a451d96p-3,0x1.a8ffffffffffep+12,true},
  {-0x1.8d743d619e3c2p-1,0x1.2668666323bdcp-1,-0x1.2688074c9e556p+10,0x1.26880dea4d87dp+10,0x1.146eb76dbff70p-3,0x1.a8ffffffffffep+12,false},
  {-0x1.09b4e14150ec4p-5,0x1.cc658eb7f61aap-3,-0x1.a32b7f0fe8d9bp+1,0x1.a4882ec2473e3p+1,0x1.146eb76dbff70p-3,0x1.a8ffffffffffep+12,true},
  {-0x1.eb4294d306646p-5,0x1.c1c123b758594p-3,-0x1.481317b121b47p+1,0x1.495db63ede350p+1,0x0.0p+0,0x1.a8ffffffffffep+12,true},
  {0x1.5a68812b840fcp-2,-0x1.494d31f97c848p-4,-0x1.a247201f315f4p+10,0x1.a24720cf27b9ap+10,0x1.1dd714a451d96p-3,0x1.a8ffffffffffep+12,false},
  {-0x1.b8c535e03a2fcp-3,0x1.373785ecdbeedp-3,-0x1.795c6f071cbc8p+3,0x1.797b4de3b27a1p+3,0x1.1dd6e04c05921p-3,0x1.a8ffffffffffep+12,true},
  {-0x1.4e83e068e990fp-3,0x1.701ada2759a85p-4,-0x1.f12e99863ee82p+2,0x1.f15f037fc70eap+2,0x1.b0c88a47ecfeap-4,0x1.a8ffffffffffep+12,true},
  {0x1.b01dc166a417ep-2,0x1.64563852e8f26p-2,-0x1.839236831bf81p+11,0x1.83923812022f8p+11,0x1.e064ece9a2c67p-1,0x1.a8ffffffffffep+12,false},
  {-0x1.89f69eeab14bap-2,-0x1.0fcecbe027cc3p-3,-0x1.4ccd25c37199ap+2,0x1.4dee4aa30b979p+2,0x1.147064ece9a2cp-3,0x1.a8ffffffffffep+12,true},
  {-0x1.1e75b45fd7e46p-2,-0x1.3da1a00bcb8e6p-3,-0x1.d8504984d126ep+1,0x1.da15b7e1e0239p+1,0x0.0p+0,0x1.a8ffffffffffep+12,true},
  {0x1.3780c7da8a01ap-1,-0x1.1a8ada316ec8cp-1,-0x1.5668f32cf3065p+5,0x1.56798e4e3de5ap+5,0x1.1dd714a451d96p-3,0x1.a8ffffffffffep+12,true},
  {0x1.874a9c82b5bd8p-1,-0x1.d44959ba032fcp-1,-0x1.23a1bbadcf527p+6,0x1.23b90164d3c98p+6,0x1.604d0579ab7d0p+0,0x1.a8ffffffffffep+12,true},
  {0x1.cc6720822034cp-1,-0x1.308b887d4b511p-1,-0x1.eb052978a3786p+5,0x1.eb27475b8d1a3p+5,0x1.e10f51ac9afe2p-1,0x1.a8ffffffffffep+12,true},
  {0x1.cc23fdaa2aadbp-5,-0x1.0503bbc2e00d4p-1,-0x1.69dc6e8901b46p+8,0x1.69dcbdd309d80p+8,0x1.90b8cfbfc6541p-1,0x1.a8ffffffffffep+12,false},
  {0x1.2c544f34786a0p-1,-0x1.b57a5eafe2f75p-2,-0x1.2e78a216db077p+5,0x1.2e9eba9e0311dp+5,0x1.e064ece9a2c67p-1,0x1.a8ffffffffffep+12,true},
  {0x1.a214578336cafp+4,0x1.c85a2151be028p+2,-0x1.f339d7156bd3ap+11,0x1.f33cc77a31254p+11,0x1.b0c6d63522c3fp-4,0x1.a8ffffffffffep+12,false},
  {0x1.48771ce2975fap-3,-0x1.cef8046fabaf7p-3,-0x1.666e55270eef2p+3,0x1.6784d3ff29adap+3,0x1.a809307299df7p-1,0x1.a8ffffffffffep+12,true},
  {-0x1.1dc9084297aacp-5,-0x1.314c730be3b93p-4,-0x1.40211c9fd4c14p+3,0x1.402bdc1875bedp+3,0x1.1dd6e04c05921p-3,0x1.a8ffffffffffep+12,true},
  {0x1.8fe95ef33d4a6p-3,-0x1.3651cae9b9d2fp-3,-0x1.3269c439d16fap+0,0x1.3ac7bf359f76bp+0,0x1.147064ece9a2cp-3,0x1.a8ffffffffffep+12,true},
  {-0x1.89a873a838599p+1,0x1.e904aae06b29ap-4,-0x1.2e62554d5c3a4p+10,0x1.2e629588d9c22p+10,0x1.b0c6d63522c3fp-4,0x1.a8ffffffffffep+12,false},
  {0x1.42a7952637404p-3,-0x1.4266e58446545p-4,-0x1.a5a220bd87780p+1,0x1.a69cff85d5b60p+1,0x1.1dd714a451d96p-3,0x1.a8ffffffffffep+12,true},
  {0x1.d836aa1181d28p-4,-0x1.6d16ce983182bp-5,-0x1.652b8eb98c4d4p+1,0x1.65c6ad6b5697ap+1,0x1.b0c88a47ecfeap-4,0x1.a8ffffffffffep+12,true},
  {-0x1.eb501b34ff01dp-4,-0x1.c5e1fef21e8a7p-1,-0x1.6863a16d35eccp+11,0x1.6863a3d070486p+11,0x1.e064ece9a2c67p-1,0x1.a8ffffffffffep+12,false},
  {-0x1.8c761fa449745p-3,-0x1.a6949877fc9ccp-3,-0x1.0064e92ae484ap+2,0x1.012d8ccc9b47bp+2,0x1.146eb76dbff70p-3,0x1.a8ffffffffffep+12,true},
  {-0x1.28de5b1fe8ee2p-5,0x1.9ec37ed3289eep-7,-0x1.32a73d418a17ep-2,0x1.35298706e77cfp-2,0x0.0p+0,0x1.a8ffffffffffep+12,true},
  {-0x1.423e88dc4f38dp-3,-0x1.c080d0652f26bp-3,-0x1.da74eaad97c63p+1,0x1.dbb5e8b8599fcp+1,0x0.0p+0,0x1.a8ffffffffffep+12,true},
  {0x1.703d30b829e60p-1,-0x1.4066437b7d7c0p-2,-0x1.fc6cf31415884p+10,0x1.fc6cf5a13cb65p+10,0x1.146eb76dbff70p-3,0x1.a8ffffffffffep+12,false},
  {0x1.83c24ffcc1111p-3,0x1.470b1c4aa0ab0p-9,-0x1.aa58e730020dap+2,0x1.aa9b60e118974p+2,0x1.147064ece9a2cp-3,0x1.a8ffffffffffep+12,true},
  {0x1.91d7175797343p-3,-0x1.12c37988a6926p-7,-0x1.a5b28b829d6a6p+2,0x1.a5e27c8ab43c9p+2,0x0.0p+0,0x1.a8ffffffffffep+12,true},
  {0x1.78bd0838d7ea0p-6,-0x1.c052c60c799ccp-2,-0x1.7541d4728181fp+11,0x1.7541d588cc826p+11,0x1.8cb0890dd7514p-1,0x1.a8ffffffffffep+12,false},
 };
 unsigned saved=0,synthetic=0;
 for(const auto& c:cases){
  const double ep=std::hypot(std::hypot(c.x,c.y,c.z),c.m);
  const double old=32.*std::numeric_limits<double>::epsilon()*std::max({1.,c.scale,c.e,ep});
  require((std::abs(ep-c.e)>old)==c.oldFails,"Old guard classification changed");
  const auto tolerance=sim::recordedPrimaryEnergyTolerance(c.e,ep,c.scale);
  require(tolerance<=1.e-7*std::max({1.,c.e,ep}),"Conservation cap enlarged");
  P4 p{c.x,c.y,c.z,c.e};G4PrimaryParticle target(22);
  sim::setRecordedPrimaryMomentum(target,p,c.m,c.scale);
  require(std::abs(target.GetMass()/GeV-c.m)<=4.e-16*std::max(1.,c.m),"Generated mass changed");
  require(std::abs(target.GetTotalEnergy()/GeV-c.e)<=4.e-16*std::max(1.,c.e),"Saved energy changed");
  ++saved;
 }
 for(double mass:{0.,0.21131675078,0.211316751,0.22,2.,91.1876})
  for(double momentum:{0.,1.e-20,1.e-8,1.,1.e5,1.e12}){
   const double e=std::hypot(momentum,mass),scale=std::max(6800.,e);
   require(accept({0.,0.,momentum,e},mass,scale),"On-shell threshold/soft/boost control rejected");
   require(!accept({0.,0.,momentum,e+.01*std::max(1.,e)},mass,scale),"Genuine off-shell vector accepted");
   synthetic+=2;
  }
 const double nan=std::numeric_limits<double>::quiet_NaN(),inf=std::numeric_limits<double>::infinity();
 for(const auto& c:std::vector<Case>{{0,0,0,1,-1,6800,false},{0,0,0,1,2,6800,false},{nan,0,0,1,0,6800,false},
  {0,0,0,inf,0,6800,false},{0,0,0,1,0,nan,false},{0,0,1.e308,1.e308,0,1.e308,false}}){
  require(!accept({c.x,c.y,c.z,c.e},c.m,c.scale),"Malformed/overflow input accepted");++synthetic;
 }
 std::cout<<"saved_vectors="<<saved<<" synthetic_cases="<<synthetic<<" all_passed=true\n";
}
