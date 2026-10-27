// Code style: AI-code. AI-generated SM-only preflight of the frozen primary converter.
// No source mutation, particle transport, detector geometry or BSM definitions.
#include "FWCore/Framework/interface/one/EDAnalyzer.h"
#include "FWCore/Framework/interface/Event.h"
#include "FWCore/Framework/interface/MakerMacros.h"
#include "FWCore/ParameterSet/interface/ParameterSet.h"
#include "FWCore/Utilities/interface/Exception.h"
#include "SimDataFormats/GeneratorProducts/interface/HepMCProduct.h"
#include "SimG4Core/Generators/interface/Generator.h"
#include "G4BosonConstructor.hh"
#include "G4LeptonConstructor.hh"
#include "G4MesonConstructor.hh"
#include "G4BaryonConstructor.hh"
#include "G4IonConstructor.hh"
#include "G4ShortLivedConstructor.hh"
#include "G4ParticleTable.hh"
#include "G4PrimaryParticle.hh"
#include "G4PrimaryVertex.hh"
#include "G4Event.hh"
#include "nlohmann/json.hpp"
#include <fstream>
#include <set>
#include <tuple>

class ShiftSMPrimaryKinematicsPreflight : public edm::one::EDAnalyzer<> {
public:
  explicit ShiftSMPrimaryKinematicsPreflight(const edm::ParameterSet& parameters)
      : input_(consumes<edm::HepMCProduct>(parameters.getParameter<edm::InputTag>("src"))),
        generator_(parameters.getParameter<edm::ParameterSet>("generator")),
        output_(parameters.getParameter<std::string>("output")) {
    report_ = {{"schema", "shift-SM-primary-kinematics-preflight-v1"},
               {"complete", false}, {"events", 0}, {"converted_particles", 0},
               {"detector_tracking_performed", false}, {"source_requested_read_only", true},
               {"event_ids", nlohmann::json::array()}, {"event_counts", nlohmann::json::array()}};
  }

private:
  void save() const {
    std::ofstream stream(output_);
    if (!stream) throw cms::Exception("KinematicsPreflight") << "Cannot write preflight receipt";
    stream << report_.dump(2) << '\n';
  }

  void beginJob() override {
    G4BosonConstructor().ConstructParticle();
    G4LeptonConstructor().ConstructParticle();
    G4MesonConstructor().ConstructParticle();
    G4BaryonConstructor().ConstructParticle();
    G4IonConstructor().ConstructParticle();
    G4ShortLivedConstructor().ConstructParticle();
    G4ParticleTable::GetParticleTable()->SetReadiness();
    save();
  }

  void analyze(const edm::Event& event, const edm::EventSetup&) override {
    const auto identity = nlohmann::json::array({event.id().run(), event.luminosityBlock(), event.id().event()});
    try {
      if (!seen_.emplace(event.id().run(), event.luminosityBlock(), event.id().event()).second)
        throw cms::Exception("KinematicsPreflight") << "Repeated original GEN identity";
      const auto* source = event.get(input_).GetEvent();
      if (source == nullptr)
        throw cms::Exception("KinematicsPreflight") << "Missing native HepMC source";
      G4Event converted;
      generator_.HepMC2G4(source, &converted);
      unsigned long long particles = 0;
      std::set<int> barcodes;
      const auto inspect = [&](const auto& self, const G4PrimaryParticle* particle) -> void {
        for (; particle; particle = particle->GetNext()) {
          const auto* info = dynamic_cast<const GenParticleInfo*>(particle->GetUserInformation());
          if (!info || !barcodes.insert(info->id()).second)
            throw cms::Exception("KinematicsPreflight") << "Missing or repeated converted GEN barcode";
          if (source->barcode_to_particle(info->id()) == nullptr)
            throw cms::Exception("KinematicsPreflight") << "Converted barcode absent from original GEN";
          ++particles;
          self(self, particle->GetDaughter());
        }
      };
      for (int vertex = 0; vertex < converted.GetNumberOfPrimaryVertex(); ++vertex)
        inspect(inspect, converted.GetPrimaryVertex(vertex)->GetPrimary());
      report_["event_ids"].push_back(identity);
      report_["event_counts"].push_back({{"event_id", identity}, {"converted_particles", particles},
          {"converted_vertices", converted.GetNumberOfPrimaryVertex()}});
      report_["events"] = report_["events"].get<unsigned long long>()+1;
      report_["converted_particles"] = report_["converted_particles"].get<unsigned long long>()+particles;
      save();
    } catch (const std::exception& error) {
      report_["failed_event_id"] = identity;
      report_["error"] = error.what();
      save();
      throw;
    }
  }

  void endJob() override {
    report_["complete"] = !report_.contains("error");
    save();
  }

  edm::EDGetTokenT<edm::HepMCProduct> input_;
  Generator generator_;
  std::string output_;
  std::set<std::tuple<unsigned, unsigned, unsigned long long>> seen_;
  nlohmann::json report_;
};

DEFINE_FWK_MODULE(ShiftSMPrimaryKinematicsPreflight);
