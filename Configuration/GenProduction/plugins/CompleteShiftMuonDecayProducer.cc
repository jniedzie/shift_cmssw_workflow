// Code style: AI-code. AI-generated complete-lifetime private producer.
#include "FWCore/Framework/interface/Event.h"
#include "FWCore/Framework/interface/MakerMacros.h"
#include "FWCore/Framework/interface/stream/EDProducer.h"
#include "FWCore/MessageLogger/interface/MessageLogger.h"
#include "FWCore/ParameterSet/interface/ParameterSet.h"
#include "FWCore/ServiceRegistry/interface/Service.h"
#include "FWCore/Utilities/interface/Exception.h"
#include "FWCore/AbstractServices/interface/RandomNumberGenerator.h"
#include "GeneratorInterface/Pythia8Interface/interface/P8RndmEngine.h"
#include "SimDataFormats/GeneratorProducts/interface/HepMCProduct.h"
#include "HepMC/GenEvent.h"
#include "HepMC/GenParticle.h"
#include "HepMC/GenVertex.h"
#include "Pythia8/Pythia.h"
#include "Pythia8Plugins/HepMC2.h"

#include <cmath>
#include <cstdlib>
#include <memory>
#include <set>
#include <sstream>
#include <vector>
#include <algorithm>
#include <map>
#include <utility>
#include <limits>

// Complete undecayed muon-producing hadron chains in a persisted GEN event.
// The hard event, existing decays, event identity and native weight stay intact.
// Channels retain their physical branching ratios: no muon-enrichment filter.
class CompleteShiftMuonDecayProducer : public edm::stream::EDProducer<> {
public:
  explicit CompleteShiftMuonDecayProducer(edm::ParameterSet const& config)
      : token_(consumes<edm::HepMCProduct>(config.getParameter<edm::InputTag>("src"))),
        pythia_(std::getenv("PYTHIA8DATA") ? std::getenv("PYTHIA8DATA") : "", false),
        random_(std::make_shared<gen::P8RndmEngine>()) {
    ordinaryFraction_ = config.getParameter<double>("ordinaryFraction");
    expectedRun_ = config.existsAs<unsigned int>("expectedRun") ? config.getParameter<unsigned int>("expectedRun") : 0;
    expectedLumi_ = config.existsAs<unsigned int>("expectedLuminosityBlock") ? config.getParameter<unsigned int>("expectedLuminosityBlock") : 0;
    expectedEvent_ = config.existsAs<unsigned long long>("expectedEvent") ? config.getParameter<unsigned long long>("expectedEvent") : 0;
    if (ordinaryFraction_ < 1. && !(expectedRun_ && expectedLumi_ && expectedEvent_))
      throw cms::Exception("Configuration") << "Conditioned fixed-barcode test requires exact original event identity";
    eligibleBarcodes_ = config.getParameter<std::vector<int>>("eligiblePionBarcodes");
    maximumProperLifetimeMm_ = config.getParameter<std::vector<double>>("maximumProperLifetimeMm");
    if (!(std::isfinite(ordinaryFraction_) && ordinaryFraction_ > 0. && ordinaryFraction_ <= 1.) ||
        eligibleBarcodes_.size() != maximumProperLifetimeMm_.size())
      throw cms::Exception("Configuration") << "Positive ordinary component and aligned eligible pion bounds required";
    for (unsigned int i = 0; i < eligibleBarcodes_.size(); ++i) {
      if (eligibleBarcodes_[i] <= 0 || !(std::isfinite(maximumProperLifetimeMm_[i]) && maximumProperLifetimeMm_[i] > 0.) ||
          !eligiblePosition_.emplace(eligibleBarcodes_[i], i).second)
        throw cms::Exception("Configuration") << "Unique positive eligible barcodes and finite positive proper lifetimes required";
    }
    auto xy = config.getParameter<double>("decayCylinderRadiusMm");
    auto z = config.getParameter<double>("decayCylinderHalfLengthMm");
    if (!(std::isfinite(xy) && xy > 0 && std::isfinite(z) && z > 0))
      throw cms::Exception("Configuration") << "Finite positive decay-cylinder dimensions required";
    pythia_.readString("ProcessLevel:all = off");
    pythia_.readString("PartonLevel:all = off");
    pythia_.readString("HadronLevel:Hadronize = off");
    pythia_.readString("ParticleDecays:limitTau0 = off");
    pythia_.readString("ParticleDecays:limitCylinder = off");
    pythia_.settings.parm("ParticleDecays:xyMax", xy);
    pythia_.settings.parm("ParticleDecays:zMax", z);
    pythia_.readString("Init:showProcesses = off");
    pythia_.readString("Init:showChangedSettings = off");
    pythia_.readString("Init:showChangedParticleData = off");
    pythia_.setRndmEnginePtr(random_);

    // Transitive closure over all enabled, positive-BR Pythia decay channels.
    // This includes indirect chains (strange baryons -> pions -> muons),
    // rather than assuming that three named species exhaust the parents.
    std::set<int> reachesMuon{13};
    bool changed = true;
    while (changed) {
      changed = false;
      for (auto const& item : pythia_.particleData) {
        int id = item.first;
        auto const& entry = item.second;
        // canDecay is initialized by Pythia::init(), after configuration.
        // The XML channel table is already available at this point.
        if (reachesMuon.count(id) || entry->sizeChannels() == 0)
          continue;
        for (int c = 0; c < entry->sizeChannels(); ++c) {
          auto const& channel = entry->channel(c);
          if (!channel.onMode() || !(channel.bRatio() > 0))
            continue;
          for (int d = 0; d < channel.multiplicity(); ++d) {
            if (reachesMuon.count(std::abs(channel.product(d)))) {
              reachesMuon.insert(id);
              changed = true;
              break;
            }
          }
          if (reachesMuon.count(id))
            break;
        }
      }
    }
    for (int id : reachesMuon) {
      if (id != 13) {
        enabled_.insert(id);
        pythia_.particleData.mayDecay(id, true);
      }
    }
    // Muons remain available to detector transport; do not decay the observable.
    pythia_.particleData.mayDecay(13, false);
    converter_.set_store_pdf(false);
    converter_.set_store_proc(false);
    converter_.set_store_xsec(false);
    converter_.set_store_weights(false);
    produces<edm::HepMCProduct>();
    produces<unsigned int>("decayedParents");
    produces<unsigned int>("addedMuons");
    produces<std::vector<int>>("enabledPdgIds");
    produces<std::vector<int>>("parentBarcodes");
    produces<std::vector<int>>("parentPdgIds");
    produces<std::vector<double>>("sampledProperLifetimeMm");
    produces<std::vector<double>>("nominalProperLifetimeMm");
    produces<std::vector<int>>("eligibleParentBarcodes");
    produces<std::vector<double>>("eligibleMaximumProperLifetimeMm");
    produces<std::vector<double>>("eligibleDecayProbabilities");
    produces<std::vector<int>>("selectedPairBarcodes");
    produces<unsigned int>("proposalComponent");
    produces<unsigned int>("eligiblePairCount");
    produces<double>("proposalOrdinaryFraction");
    produces<double>("proposalDensityRatio");
    produces<double>("proposalCorrection");
    produces<bool>("proposalAuditPassed");
    std::ostringstream ids;
    for (int id : enabled_)
      if (pythia_.particleData.tau0(id) >= 10.)
        ids << ' ' << id;
    edm::LogVerbatim("ShiftMuonDecays") << "Muon-producing parents with ctau >= 10 mm:" << ids.str();
  }

  void produce(edm::Event& event, edm::EventSetup const&) override {
    if (expectedEvent_ && (event.id().run() != expectedRun_ || event.luminosityBlock() != expectedLumi_ || event.id().event() != expectedEvent_))
      throw cms::Exception("EventCorruption") << "Frozen eligible barcode proposal belongs to a different original event";
    edm::Service<edm::RandomNumberGenerator> rng;
    if (!rng.isAvailable())
      throw cms::Exception("Configuration") << "CompleteShiftMuonDecayProducer needs RandomNumberGeneratorService";
    random_->setRandomEngine(&rng->getEngine(event.streamID()));
    if (!initialized_) {
      if (!pythia_.init())
        throw cms::Exception("PythiaError") << "Could not initialize residual decays";
      initialized_ = true;
    }
    auto const& input = event.get(token_);
    auto output = std::make_unique<HepMC::GenEvent>(*input.GetEvent());
    if (output->momentum_unit() != HepMC::Units::GEV || output->length_unit() != HepMC::Units::MM)
      throw cms::Exception("EventCorruption") << "Residual decays require HepMC GeV/mm units";
    std::vector<HepMC::GenParticle*> parents;
    int maximumBarcode = 0;
    for (auto it = output->particles_begin(); it != output->particles_end(); ++it) {
      auto* particle = *it;
      maximumBarcode = std::max(maximumBarcode, particle->barcode());
      if (particle->status() == 1 && !particle->end_vertex() && enabled_.count(std::abs(particle->pdg_id())))
        parents.push_back(particle);
    }
    // The complete eligible set is a frozen property of the original event.
    // It must not depend on the sampled daughters or detector response.
    std::vector<HepMC::GenParticle*> eligible(eligibleBarcodes_.size(), nullptr);
    for (auto* parent : parents) {
      auto found = eligiblePosition_.find(parent->barcode());
      if (found == eligiblePosition_.end())
        continue;
      if (std::abs(parent->pdg_id()) != 211 || !parent->production_vertex())
        throw cms::Exception("EventCorruption") << "Eligible barcode is not an undecayed original charged pion";
      auto const& mom = parent->momentum();
      double mass = parent->generated_mass();
      if (!(std::isfinite(mass) && mass > 0. && std::isfinite(mom.e()) && mom.e() > 0.) ||
          std::abs(mass - pythia_.particleData.m0(parent->pdg_id())) > 1.e-6 ||
          std::abs(mom.m2() - mass * mass) > std::max(1.e-7, 64. * std::numeric_limits<double>::epsilon() * mom.e() * mom.e()))
        throw cms::Exception("EventCorruption") << "Invalid eligible pion mass/GeV momentum";
      eligible[found->second] = parent;
    }
    if (std::any_of(eligible.begin(), eligible.end(), [](auto* p) { return p == nullptr; }))
      throw cms::Exception("EventCorruption") << "Frozen eligible pion barcode missing from original stable parent set";
    std::vector<std::pair<unsigned int, unsigned int>> pairs;
    for (unsigned int i = 0; i < eligible.size(); ++i)
      for (unsigned int j = i + 1; j < eligible.size(); ++j)
        if (eligible[i]->pdg_id() * eligible[j]->pdg_id() < 0)
          pairs.emplace_back(i, j);
    if (pairs.empty() && ordinaryFraction_ < 1.)
      throw cms::Exception("Configuration") << "Conditioned component requires an eligible opposite-charge pion pair";
    std::vector<double> probabilities(eligible.size());
    for (unsigned int i = 0; i < eligible.size(); ++i) {
      double tau0 = pythia_.particleData.tau0(eligible[i]->pdg_id());
      probabilities[i] = -std::expm1(-maximumProperLifetimeMm_[i] / tau0);
      if (!(std::isfinite(tau0) && tau0 > 0. && std::isfinite(probabilities[i]) && probabilities[i] > 0. && probabilities[i] <= 1.))
        throw cms::Exception("EventCorruption") << "Invalid natural lifetime or conditional decay probability";
    }
    unsigned int component = 0;
    std::set<int> conditionedBarcodes;
    std::vector<int> chosen;
    // Alpha=1 consumes exactly the original RNG sequence, including init.
    if (ordinaryFraction_ < 1. && pythia_.rndm.flat() >= ordinaryFraction_) {
      component = 1;
      auto selected = std::min(pairs.size() - 1, static_cast<size_t>(pythia_.rndm.flat() * pairs.size()));
      auto const& pair = pairs[selected];
      chosen = {eligibleBarcodes_[pair.first], eligibleBarcodes_[pair.second]};
      conditionedBarcodes.insert(chosen.begin(), chosen.end());
    }
    std::vector<int> recordedBarcodes, recordedPdgIds;
    std::vector<double> recordedLifetimes, recordedNominalLifetimes;
    std::vector<double> eligibleLifetimes(eligible.size(), std::numeric_limits<double>::quiet_NaN());
    unsigned int decayed = 0, muons = 0;
    for (auto* parent : parents) {
      if (!parent->production_vertex())
        throw cms::Exception("EventCorruption") << "Missing parent production vertex";
      auto const& p = parent->momentum();
      auto const& v = parent->production_vertex()->position();
      pythia_.event.reset();
      int index = pythia_.event.append(parent->pdg_id(), 93, 0, 0, p.px(), p.py(), p.pz(), p.e(),
                                     parent->generated_mass());
      pythia_.event[index].vProd(v.x(), v.y(), v.z(), v.t());
      double tau0 = pythia_.particleData.tau0(parent->pdg_id());
      double tau;
      auto position = eligiblePosition_.find(parent->barcode());
      if (conditionedBarcodes.count(parent->barcode()))
        tau = -tau0 * std::log1p(-pythia_.rndm.flat() * probabilities[position->second]);
      else
        tau = pythia_.rndm.exp() * tau0;
      if (!(std::isfinite(tau) && tau >= 0.))
        throw cms::Exception("EventCorruption") << "Invalid sampled proper lifetime";
      if (position != eligiblePosition_.end())
        eligibleLifetimes[position->second] = tau;
      recordedBarcodes.push_back(parent->barcode());
      recordedPdgIds.push_back(parent->pdg_id());
      recordedLifetimes.push_back(tau);
      recordedNominalLifetimes.push_back(tau0);
      pythia_.event[index].tau(tau);
      if (!pythia_.moreDecays())
        throw cms::Exception("PythiaError") << "Residual decay failed for PDG " << parent->pdg_id();
      if (pythia_.event[index].isFinal())
        throw cms::Exception("EventCorruption") << "Saved lifetime did not produce a full preassigned decay";
      parent->set_status(2);
      for (int i = 2; i < pythia_.event.size(); ++i)
        if (pythia_.event[i].isFinal() && std::abs(pythia_.event[i].id()) == 13)
          ++muons;
      if (!converter_.fill_next_event(pythia_.event, output.get(), -1, nullptr, nullptr, true, parent,
                                      maximumBarcode))
        throw cms::Exception("EventCorruption") << "Could not append residual decay to HepMC";
      maximumBarcode += pythia_.event.size() - 2;
      ++decayed;
    }
    double pairDensity = 0.;
    for (auto const& pair : pairs) {
      auto i = pair.first, j = pair.second;
      if (!(std::isfinite(eligibleLifetimes[i]) && std::isfinite(eligibleLifetimes[j])))
        throw cms::Exception("EventCorruption") << "Missing recorded original pion lifetime";
      if (eligibleLifetimes[i] < maximumProperLifetimeMm_[i] && eligibleLifetimes[j] < maximumProperLifetimeMm_[j])
        pairDensity += 1. / (probabilities[i] * probabilities[j]);
    }
    // Marginalize over ALL eligible pairs, not only the selected component.
    double ratio = ordinaryFraction_ + (pairs.empty() ? 0. : (1. - ordinaryFraction_) * pairDensity / pairs.size());
    double correction = 1. / ratio;
    if (!(std::isfinite(ratio) && ratio > 0. && std::isfinite(correction) && correction > 0.))
      throw cms::Exception("EventCorruption") << "Invalid full-mixture proposal weight";
    event.put(std::make_unique<std::vector<int>>(recordedBarcodes), "parentBarcodes");
    event.put(std::make_unique<std::vector<int>>(recordedPdgIds), "parentPdgIds");
    event.put(std::make_unique<std::vector<double>>(recordedLifetimes), "sampledProperLifetimeMm");
    event.put(std::make_unique<std::vector<double>>(recordedNominalLifetimes), "nominalProperLifetimeMm");
    event.put(std::make_unique<std::vector<int>>(eligibleBarcodes_), "eligibleParentBarcodes");
    event.put(std::make_unique<std::vector<double>>(maximumProperLifetimeMm_), "eligibleMaximumProperLifetimeMm");
    event.put(std::make_unique<std::vector<double>>(probabilities), "eligibleDecayProbabilities");
    event.put(std::make_unique<std::vector<int>>(chosen), "selectedPairBarcodes");
    event.put(std::make_unique<unsigned int>(component), "proposalComponent");
    event.put(std::make_unique<unsigned int>(pairs.size()), "eligiblePairCount");
    event.put(std::make_unique<double>(ordinaryFraction_), "proposalOrdinaryFraction");
    event.put(std::make_unique<double>(ratio), "proposalDensityRatio");
    event.put(std::make_unique<double>(correction), "proposalCorrection");
    event.put(std::make_unique<bool>(true), "proposalAuditPassed");
    event.put(std::make_unique<edm::HepMCProduct>(output.release()));
    event.put(std::make_unique<unsigned int>(decayed), "decayedParents");
    event.put(std::make_unique<unsigned int>(muons), "addedMuons");
    event.put(std::make_unique<std::vector<int>>(enabled_.begin(), enabled_.end()), "enabledPdgIds");
  }

private:
  edm::EDGetTokenT<edm::HepMCProduct> token_;
  Pythia8::Pythia pythia_;
  gen::P8RndmEnginePtr random_;
  HepMC::Pythia8ToHepMC converter_;
  std::set<int> enabled_;
  bool initialized_ = false;
  double ordinaryFraction_;
  unsigned int expectedRun_ = 0, expectedLumi_ = 0;
  unsigned long long expectedEvent_ = 0;
  std::vector<int> eligibleBarcodes_;
  std::vector<double> maximumProperLifetimeMm_;
  std::map<int, unsigned int> eligiblePosition_;
};

DEFINE_FWK_MODULE(CompleteShiftMuonDecayProducer);
