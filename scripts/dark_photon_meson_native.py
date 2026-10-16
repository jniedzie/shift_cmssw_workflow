# Code style: AI-code. AI-generated native Pythia/HepMC meson-source bridge.
"""Use the installed Pythia8 and HepMC libraries without rebuilding CMSSW.

Generation and graph replacement run in a private interpreter. No shared
release, detector configuration, electronics or trigger settings are changed.
"""
import json
import os
from pathlib import Path
import subprocess


CPP = r'''
#include "Pythia8/Pythia.h"
#include "Pythia8Plugins/HepMC2.h"
#include "HepMC/IO_GenEvent.h"
#include <algorithm>
#include <iomanip>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>
namespace ShiftMesonNative {
class Output {
  std::unique_ptr<HepMC::IO_GenEvent> stream;
public:
  explicit Output(std::string const& path):stream(std::make_unique<HepMC::IO_GenEvent>(path,std::ios::out)){stream->precision(17);}
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
  Source(std::vector<std::string> const& commands,int seed)
      :pythia(std::string(std::getenv("PYTHIA8DATA")),false) {
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
  std::string snapshot() const {
    std::ostringstream o;o<<std::setprecision(17)<<"{\"native_weight\":"<<pythia.info.weight()
       <<",\"ecm_gev\":"<<pythia.info.eCM()<<",\"parents\":[";
    bool first=true;
    for(int i=1;i<pythia.event.size();++i) {
      auto const& p=pythia.event[i];
      if(p.id()!=221&&p.id()!=331)continue;
      auto daughters=p.daughterList();
      if(daughters.empty())throw std::runtime_error("An eligible meson was not naturally decayed");
      bool copy=false;
      for(auto j:daughters)copy=copy||pythia.event[j].id()==p.id();
      if(copy)continue;
      if(!first)o<<',';first=false;
      o<<"{\"index\":"<<i<<",\"pdg_id\":"<<p.id()<<",\"native_mass_gev\":"<<p.m()
       <<",\"p4\":["<<p.e()<<','<<p.px()<<','<<p.py()<<','<<p.pz()<<"]}";
    }
    o<<"]}";return o.str();
  }
  std::string configuration() {
    std::ostringstream o;o<<std::setprecision(17)<<"{\"ecm_gev\":"<<pythia.info.eCM()
      <<",\"check_event\":"<<(pythia.settings.flag("Check:event")?"true":"false")
      <<",\"mass_tolerance\":"<<pythia.settings.parm("Check:mTolErr")
      <<",\"energy_tolerance\":"<<pythia.settings.parm("Check:epTolErr")<<",\"parents\":[";
    bool first=true;
    for(auto pdg:{221,331}) {
      auto entry=pythia.particleData.particleDataEntryPtr(pdg);
      if(!first)o<<',';first=false;
      o<<"{\"pdg_id\":"<<pdg<<",\"mass_min_gev\":"<<entry->m0Min()
       <<",\"mass_max_gev\":"<<entry->m0Max()<<'}';
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
    if(!parent||(parent->pdg_id()!=221&&parent->pdg_id()!=331)||
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
  void inject(int index,std::vector<double> const& values) {
    if(!current||values.size()!=22)throw std::runtime_error("Invalid decay injection input");
    auto* parent=current->barcode_to_particle(index);
    if(!parent||(parent->pdg_id()!=221&&parent->pdg_id()!=331)||!parent->end_vertex())
      throw std::runtime_error("Selected meson is absent from the source graph");
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
    dark->set_generated_mass(values[20]);
    auto* photon=new HepMC::GenParticle(p4(4),22,1);
    photon->set_generated_mass(0.);
    decay->add_particle_in(parent);decay->add_particle_out(dark);decay->add_particle_out(photon);
    current->add_vertex(decay);
    auto* endpoint=new HepMC::GenVertex(HepMC::FourVector(position.x()+values[16],
      position.y()+values[17],position.z()+values[18],position.t()+values[19]));
    auto* muon=new HepMC::GenParticle(p4(8),13,1);
    auto* antimuon=new HepMC::GenParticle(p4(12),-13,1);
    muon->set_generated_mass(values[21]);antimuon->set_generated_mass(values[21]);
    endpoint->add_particle_in(dark);endpoint->add_particle_out(muon);endpoint->add_particle_out(antimuon);
    current->add_vertex(endpoint);
    for(auto const& expected:spectators) {
      auto barcode=std::stoi(expected.substr(0,expected.find(' ')));
      auto const* unchanged=current->barcode_to_particle(barcode);
      if(!unchanged||particle_signature(unchanged)!=expected)
        throw std::runtime_error("Meson injection changed a spectator particle or its vertices");
    }
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


def native_source(settings, seed):
    if not os.environ.get('CMSSW_BASE') or not os.environ.get('PYTHIA8DATA'):
        raise RuntimeError('Enter the prepared CMSSW runtime; no shared build is performed')
    if type(seed) is not int or not 1 <= seed <= 899999900:
        raise ValueError('Require an explicit valid unused Pythia seed')
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
    return ROOT.ShiftMesonNative.Source(commands, seed)


def inject_decay(source, parent_index, sampled_decay):
    import ROOT
    values = ROOT.std.vector('double')()
    for key in ('dark_photon_p4', 'photon_p4', 'muon_p4', 'antimuon_p4'):
        for value in sampled_decay[key]:
            values.push_back(value)
    for value in sampled_decay['decay_displacement_mm']:
        values.push_back(value)
    values.push_back(sampled_decay['decay_ct_mm'])
    values.push_back(sampled_decay['dark_photon_mass_gev'])
    values.push_back(sampled_decay['muon_mass_gev'])
    source.inject(parent_index, values)
