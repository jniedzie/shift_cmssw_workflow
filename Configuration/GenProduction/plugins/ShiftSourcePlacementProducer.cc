// Code style: AI-code. AI-generated controlled fixed-point HepMC placement.
#include "FWCore/Framework/interface/Event.h"
#include "FWCore/Framework/interface/MakerMacros.h"
#include "FWCore/Framework/interface/stream/EDProducer.h"
#include "FWCore/ParameterSet/interface/ParameterSet.h"
#include "FWCore/Utilities/interface/Exception.h"
#include "FWCore/Utilities/interface/InputTag.h"
#include "SimDataFormats/GeneratorProducts/interface/HepMCProduct.h"
#include "HepMC/GenEvent.h"
#include "HepMC/GenVertex.h"
#include "HepMC/Units.h"
#include "Configuration/GenProduction/interface/FixedSourcePlacement.h"

#include <cmath>
#include <memory>
#include <map>
#include <string>
#include <tuple>
#include <vector>

// A controlled material test, not a model of beam-halo interaction positions.
// Copy the whole graph; do not regenerate decays or alter momenta or weights.
class ShiftSourcePlacementProducer : public edm::stream::EDProducer<> {
public:
  explicit ShiftSourcePlacementProducer(edm::ParameterSet const& config)
      : token_(consumes<edm::HepMCProduct>(config.getParameter<edm::InputTag>("src"))),
        targetCm_(config.getParameter<std::vector<double>>("targetPositionCm")),
        scope_(config.getParameter<std::string>("testScope")) {
    if (scope_ != "fixed-point-material-canary" && scope_ != "assumed-jaw-depth-canary")
      throw cms::Exception("Configuration") << "Require a declared diagnostic source-placement scope";
    auto positions = config.existsAs<std::vector<edm::ParameterSet>>("eventPositions")
                         ? config.getParameter<std::vector<edm::ParameterSet>>("eventPositions")
                         : std::vector<edm::ParameterSet>{};
    if ((scope_ == "fixed-point-material-canary" && (targetCm_.size() != 3 || !positions.empty())) ||
        (scope_ == "assumed-jaw-depth-canary" && (!targetCm_.empty() || positions.empty())))
      throw cms::Exception("Configuration") << "Use one fixed point or an explicit nonempty per-event map";
    for (double value : targetCm_)
      if (!std::isfinite(value) || !std::isfinite(value * 10.))
        throw cms::Exception("Configuration") << "Source point coordinates must be finite";
    for (auto const& row : positions) {
      Key key{row.getParameter<unsigned int>("run"), row.getParameter<unsigned int>("luminosityBlock"),
              row.getParameter<unsigned long long>("event")};
      auto point = row.getParameter<std::vector<double>>("targetPositionCm");
      if (std::get<0>(key) == 0 || std::get<1>(key) == 0 || std::get<2>(key) == 0 || point.size() != 3)
        throw cms::Exception("Configuration") << "Invalid source-placement identity or point";
      for (double value : point)
        if (!std::isfinite(value) || !std::isfinite(value * 10.))
          throw cms::Exception("Configuration") << "Invalid per-event source point";
      if (!eventPositions_.emplace(key, std::array<double, 3>{point[0], point[1], point[2]}).second)
        throw cms::Exception("Configuration") << "Repeated source-placement event identity";
    }
    produces<edm::HepMCProduct>();
    produces<edm::HepMCProduct>("original");
    for (auto const* name : {"originalXmm", "originalYmm", "originalZmm", "originalCtMm",
                             "targetXmm", "targetYmm", "targetZmm"})
      produces<double>(name);
    produces<std::string>("testScope");
  }

  void produce(edm::Event& event, edm::EventSetup const&) override {
    auto const& input = event.get(token_);
    std::array<double, 3> target;
    if (eventPositions_.empty()) {
      target = {targetCm_[0], targetCm_[1], targetCm_[2]};
    } else {
      auto found = eventPositions_.find(Key{event.id().run(), event.luminosityBlock(), event.id().event()});
      if (found == eventPositions_.end())
        throw cms::Exception("EventCorruption") << "Original event is absent from the frozen source-point map";
      target = found->second;
    }
    auto result = shift::placeSource(input.GetEvent(), target);
    // Keep the exact input graph without another GeneratorSmearedProducer:
    // two such proxies listen to registrations and form a framework cycle.
    event.put(std::make_unique<edm::HepMCProduct>(new HepMC::GenEvent(*input.GetEvent())), "original");
    event.put(std::make_unique<edm::HepMCProduct>(result.graph.release()));
    char const* oldNames[] = {"originalXmm", "originalYmm", "originalZmm", "originalCtMm"};
    char const* targetNames[] = {"targetXmm", "targetYmm", "targetZmm"};
    for (unsigned int i = 0; i < 4; ++i)
      event.put(std::make_unique<double>(result.originalMm[i]), oldNames[i]);
    for (unsigned int i = 0; i < 3; ++i)
      event.put(std::make_unique<double>(result.targetMm[i]), targetNames[i]);
    event.put(std::make_unique<std::string>(scope_), "testScope");
  }

private:
  using Key = std::tuple<unsigned int, unsigned int, unsigned long long>;
  edm::EDGetTokenT<edm::HepMCProduct> token_;
  std::vector<double> targetCm_;
  std::string scope_;
  std::map<Key, std::array<double, 3>> eventPositions_;
};

DEFINE_FWK_MODULE(ShiftSourcePlacementProducer);
