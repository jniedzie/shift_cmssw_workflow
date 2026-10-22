// Code style: AI-code. AI-generated fixed native HepMC2 product copy.
#include "FWCore/Framework/interface/Event.h"
#include "FWCore/Framework/interface/MakerMacros.h"
#include "FWCore/Framework/interface/stream/EDProducer.h"
#include "FWCore/ParameterSet/interface/ParameterSet.h"
#include "FWCore/Utilities/interface/Exception.h"
#include "FWCore/Utilities/interface/InputTag.h"
#include "SimDataFormats/GeneratorProducts/interface/HepMCProduct.h"

#include <memory>

// Fixed product registration avoids GeneratorSmearedProducer's dynamic
// HepMC2/HepMC3 callbacks when multiple old and new timing products coexist.
class ShiftHepMC2CopyProducer : public edm::stream::EDProducer<> {
public:
  explicit ShiftHepMC2CopyProducer(edm::ParameterSet const& config)
      : token_(consumes<edm::HepMCProduct>(config.getParameter<edm::InputTag>("src"))) {
    produces<edm::HepMCProduct>();
  }

  void produce(edm::Event& event, edm::EventSetup const&) override {
    auto const& input = event.get(token_);
    if (!input.GetEvent())
      throw cms::Exception("EventCorruption") << "Cannot copy an empty native HepMC2 graph";
    event.put(std::make_unique<edm::HepMCProduct>(input));
  }

private:
  edm::EDGetTokenT<edm::HepMCProduct> token_;
};

DEFINE_FWK_MODULE(ShiftHepMC2CopyProducer);
