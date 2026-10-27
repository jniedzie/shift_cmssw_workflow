// Code style: AI-code. Bounded bridge to the unchanged upstream DPMixing class.
#include "Pythia8/PythiaParallel.h"
#include "MixingPattern.h"
#include <fstream>
#include <iomanip>
#include <stdexcept>
#include <set>
using namespace Pythia8;

int main(int argc,char** argv) {
  try {
    if(argc==3 && std::string(argv[1])=="--rates") {
      double mass=std::stod(argv[2]);DPMixing model(0.01);
      std::cout<<std::setprecision(17);
      for(int id:{4900111,4900221,4900331,4900113,4900223,4900333})
        std::cout<<id<<" "<<model.CalculateWeight(mass,id)<<"\n";
      return 0;
    }
    if(argc!=8)throw std::runtime_error("xml settings mass trials seed output proposalProbability");
    double mass=std::stod(argv[3]),probability=std::stod(argv[7]);
    int trials=std::stoi(argv[4]),seed=std::stoi(argv[5]);
    if(!(mass>0.211316751 && mass<1.) || trials<1 || trials>100000 ||
       seed<1 || seed>899999999 || !(probability>0. && probability<=0.01))
      throw std::runtime_error("Invalid bounded reference point");
    PythiaParallel parallel(argv[1],false);
    std::ifstream input(argv[2]);
    if(!input)throw std::runtime_error("Missing explicit collision settings");
    std::string line;
    while(std::getline(input,line))if(!line.empty()&&!parallel.readString(line))
      throw std::runtime_error("Rejected collision setting: "+line);
    parallel.readString("Print:quiet=on");
    parallel.readString("Parallelism:numThreads=1");
    parallel.readString("Random:setSeed=on");
    parallel.readString("Random:seed="+std::to_string(seed));
    DPMixing model(probability);
    model.ApplyMixing(parallel,mass);
    if(!parallel.init())throw std::runtime_error("Reference source initialization failed");
    Pythia* generator=nullptr;
    parallel.foreach([&](Pythia* p){generator=p;});
    if(!generator || !generator->settings.flag("Check:event"))
      throw std::runtime_error("Native event checks must remain enabled");
    std::ofstream output(argv[6]);
    if(!output)throw std::runtime_error("Cannot open reference ledger");
    output<<std::setprecision(17);
    std::set<int> ids{4900111,4900221,4900331,4900113,4900223,4900333};
    long success=0,failures=0,candidates=0;
    double weightSum=0.;
    for(int trial=1;trial<=trials;++trial) {
      bool ok=generator->next();
      output<<"{\"trial\":"<<trial<<",\"generator_success\":"<<(ok?"true":"false");
      if(!ok){++failures;output<<",\"candidates\":[]}\n";continue;}
      ++success;
      if(generator->info.weight()!=1.)throw std::runtime_error("Unit source weights required");
      output<<",\"native_weight\":1,\"candidates\":[";
      bool first=true;
      for(int i=0;i<generator->event.size();++i) {
        const auto& p=generator->event[i];
        if(!p.isFinal() || !ids.count(p.id()))continue;
        double weight=model.CalculateWeight(mass,p.id())/probability;
        if(!(weight>=0.) || !std::isfinite(weight))throw std::runtime_error("Nonfinite reference weight");
        int mother=p.mother1();
        if(!first)output<<",";first=false;++candidates;weightSum+=weight;
        output<<"{\"index\":"<<i<<",\"id\":"<<p.id()
          <<",\"mother_id\":"<<generator->event[mother].id()
          <<",\"mechanism\":\""<<model.GetProductionType(generator->event[mother].id())
          <<"\",\"weight_over_epsilon_squared\":"<<weight
          <<",\"p4\":["<<p.e()<<","<<p.px()<<","<<p.py()<<","<<p.pz()<<"]}";
      }
      output<<"]}\n";
    }
    output.close();
    std::cout<<std::setprecision(17)<<"RESULT {\"trials\":"<<trials
      <<",\"source_collisions\":"<<success<<",\"generator_failures\":"<<failures
      <<",\"candidates\":"<<candidates<<",\"sum_weights_over_epsilon_squared\":"<<weightSum
      <<",\"sigma_source_mb\":"<<generator->info.sigmaGen()
      <<",\"sigma_source_error_mb\":"<<generator->info.sigmaErr()
      <<",\"ecm_gev\":"<<generator->info.eCM()
      <<",\"pythia_version\":"<<PYTHIA_VERSION
      <<",\"seed_probability\":"<<probability<<"}\n";
  }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}
}
