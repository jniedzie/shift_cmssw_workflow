// Code style: AI-code. AI-generated tracking-free validation using the production converter.
// This analyzer never changes the source event or performs detector transport.
#include "FWCore/Framework/interface/one/EDAnalyzer.h"
#include "FWCore/Framework/interface/Event.h"
#include "FWCore/Framework/interface/MakerMacros.h"
#include "FWCore/ParameterSet/interface/ParameterSet.h"
#include "FWCore/Utilities/interface/Exception.h"
#include "SimDataFormats/GeneratorProducts/interface/HepMCProduct.h"
#include "SimG4Core/Generators/interface/Generator.h"
#include "SimG4Core/Generators/interface/RecordedPrimaryMomentum.h"
#include "SimG4Core/ShiftDarkPhotonPhysics/interface/ShiftDarkPhotonPhysics.h"
#include "G4BosonConstructor.hh"
#include "G4LeptonConstructor.hh"
#include "G4MesonConstructor.hh"
#include "G4BaryonConstructor.hh"
#include "G4IonConstructor.hh"
#include "G4ShortLivedConstructor.hh"
#include "G4ParticleTable.hh"
#include "G4PrimaryVertex.hh"
#include "G4Event.hh"
#include "nlohmann/json.hpp"
#include <fstream>
#include <set>

class ShiftPrimaryKinematicsPreflight : public edm::one::EDAnalyzer<> {
public:
  explicit ShiftPrimaryKinematicsPreflight(const edm::ParameterSet& p)
      : input_(consumes<edm::HepMCProduct>(p.getParameter<edm::InputTag>("src"))),
        generator_(p.getParameter<edm::ParameterSet>("generator")),
        signal_(p.getParameter<double>("massGeV"), p.getParameter<double>("properCTauMm")),
        output_(p.getParameter<std::string>("output")) {
    report_ = {{"schema", "shift-primary-kinematics-preflight-v1"}, {"complete", false},
               {"events", 0}, {"converted_particles", 0}, {"old_guard_violations", 0},
               {"maximum_energy_residual_GeV", 0.}, {"maximum_new_tolerance_fraction", 0.},
               {"source_GEN_unchanged", true}, {"detector_tracking_performed", false},
               {"old_violations", nlohmann::json::array()}};
  }
private:
  void save() const { std::ofstream file(output_); file << report_.dump(2) << '\n'; }
  void beginJob() override {
    G4BosonConstructor().ConstructParticle(); G4LeptonConstructor().ConstructParticle();
    G4MesonConstructor().ConstructParticle(); G4BaryonConstructor().ConstructParticle();
    G4IonConstructor().ConstructParticle(); G4ShortLivedConstructor().ConstructParticle();
    G4ParticleTable::GetParticleTable()->SetReadiness(); signal_.ConstructParticle();
    save();
  }
  void analyze(const edm::Event& event, const edm::EventSetup&) override {
    const auto& product = event.get(input_);
    const auto* source = product.GetEvent();
    const auto identity = nlohmann::json::array({event.id().run(), event.luminosityBlock(), event.id().event()});
    try {
      G4Event converted;
      generator_.HepMC2G4(source, &converted);
      double scale = 1.;
      for (auto i = source->particles_begin(); i != source->particles_end(); ++i)
        scale = std::max(scale, std::abs((*i)->momentum().e()));
      std::set<int> seen;
      const auto inspect = [&](const auto& self, const G4PrimaryParticle* particle) -> void {
        for (; particle; particle = particle->GetNext()) {
          const auto* info = dynamic_cast<const GenParticleInfo*>(particle->GetUserInformation());
          if (!info || !seen.insert(info->id()).second)
            throw cms::Exception("KinematicsPreflight") << "Missing or duplicate converter source identity";
          const auto* original = source->barcode_to_particle(info->id());
          if (!original) throw cms::Exception("KinematicsPreflight") << "Converter identity absent from GEN";
          const auto& q = original->momentum();
          const double mass = original->generated_mass();
          const double physical = std::hypot(std::hypot(q.px(), q.py(), q.pz()), mass);
          const double residual = std::abs(physical-q.e());
          const double old = 32.*std::numeric_limits<double>::epsilon()*std::max({1.,scale,q.e(),physical});
          const double allowed = sim::recordedPrimaryEnergyTolerance(q.e(),physical,scale);
          report_["maximum_energy_residual_GeV"] = std::max(report_["maximum_energy_residual_GeV"].get<double>(), residual);
          report_["maximum_new_tolerance_fraction"] = std::max(report_["maximum_new_tolerance_fraction"].get<double>(), residual/allowed);
          report_["converted_particles"] = report_["converted_particles"].get<unsigned long long>()+1;
          if (residual > old) {
            report_["old_guard_violations"] = report_["old_guard_violations"].get<unsigned long long>()+1;
            report_["old_violations"].push_back({{"event_id",identity},{"barcode",info->id()},
              {"pdg",original->pdg_id()},{"p4_GeV",{q.px(),q.py(),q.pz(),q.e()}},
              {"generated_mass_GeV",mass},{"energy_residual_GeV",residual},
              {"old_tolerance_GeV",old},{"new_tolerance_GeV",allowed},{"source_scale_GeV",scale}});
          }
          self(self,particle->GetDaughter());
        }
      };
      for (int i=0;i<converted.GetNumberOfPrimaryVertex();++i)
        inspect(inspect,converted.GetPrimaryVertex(i)->GetPrimary());
      report_["events"] = report_["events"].get<unsigned long long>()+1;
      report_["last_event_id"] = identity;
    } catch (const std::exception& error) {
      report_["failed_event_id"] = identity; report_["error"] = error.what(); save(); throw;
    }
  }
  void endJob() override { report_["complete"] = !report_.contains("error"); save(); }
  edm::EDGetTokenT<edm::HepMCProduct> input_;
  Generator generator_;
  ShiftDarkPhotonPhysics signal_;
  std::string output_;
  nlohmann::json report_;
};
DEFINE_FWK_MODULE(ShiftPrimaryKinematicsPreflight);
