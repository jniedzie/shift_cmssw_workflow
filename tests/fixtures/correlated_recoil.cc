// Code style: AI-code. AI-generated synthetic correlated-recoil regression.
void shiftTestRecoilParent(ShiftMesonNative::Source& source,int pdg,double mass,
                          std::vector<double> const& p) {
  if(p.size()!=4)throw std::runtime_error("Synthetic parent needs one four-vector");
  source.current=std::make_unique<HepMC::GenEvent>(HepMC::Units::GEV,HepMC::Units::MM);
  auto* birth=new HepMC::GenVertex(HepMC::FourVector(0.,0.,0.,0.));
  auto* parent=new HepMC::GenParticle(HepMC::FourVector(p[1],p[2],p[3],p[0]),pdg,2);
  parent->set_generated_mass(mass);birth->add_particle_out(parent);
  auto* spectator=new HepMC::GenParticle(HepMC::FourVector(0.,0.,0.,.13957039),211,1);
  spectator->set_generated_mass(.13957039);birth->add_particle_out(spectator);
  source.current->add_vertex(birth);parent->suggest_barcode(7);spectator->suggest_barcode(8);
  auto* end=new HepMC::GenVertex(HepMC::FourVector(1.,2.,3.,4.));end->add_particle_in(parent);
  Pythia8::Vec4 first(0.,0.,mass/2,mass/2),second(0.,0.,-mass/2,mass/2);
  Pythia8::Vec4 momentum(p[1],p[2],p[3],p[0]);first.bst(momentum);second.bst(momentum);
  for(auto const& value:{first,second})
    end->add_particle_out(new HepMC::GenParticle(HepMC::FourVector(
        value.px(),value.py(),value.pz(),value.e()),22,1));
  source.current->add_vertex(end);
}

std::vector<double> shiftTestRecoilMuon(ShiftMesonNative::Source& source) {
  auto* parent=source.current->barcode_to_particle(7);
  for(auto it=parent->end_vertex()->particles_out_const_begin();
      it!=parent->end_vertex()->particles_out_const_end();++it) {
    auto* recoil=*it;
    if(recoil->pdg_id()!=443)continue;
    if(!recoil->end_vertex())throw std::runtime_error("Jpsi recoil did not decay");
    for(auto child=recoil->end_vertex()->particles_out_const_begin();
        child!=recoil->end_vertex()->particles_out_const_end();++child) {
      if((*child)->pdg_id()!=13)continue;
      auto const& p=(*child)->momentum();return {p.e(),p.px(),p.py(),p.pz()};
    }
    throw std::runtime_error("The validation-only VLL recoil lacks a muon");
  }
  throw std::runtime_error("Missing validation Jpsi recoil");
}

std::string shiftTestRecoilSpectator(ShiftMesonNative::Source& source) {
  return ShiftMesonNative::particle_signature(source.current->barcode_to_particle(8));
}
