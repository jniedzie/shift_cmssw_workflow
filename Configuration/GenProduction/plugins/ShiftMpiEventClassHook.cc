#include "FWCore/ParameterSet/interface/ParameterSet.h"
#include "FWCore/Utilities/interface/Exception.h"
#include "GeneratorInterface/Pythia8Interface/interface/CustomHook.h"
#include "Pythia8/Pythia.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <string>

namespace {
bool isDirectJpsiState(int id) {
  id = std::abs(id);
  // Physical colour-singlet J/psi and Pythia's three J/psi colour-octet
  // states.  Feed-down states and hadronization/nonprompt J/psi are not in
  // this list and therefore remain in the complementary QCD event class.
  return id == 443 || id == 9940003 || id == 9941003 || id == 9942003;
}
}  // namespace

class ShiftMpiEventClassHook final : public Pythia8::UserHooks {
public:
  explicit ShiftMpiEventClassHook(const edm::ParameterSet& parameters)
      : eventClass_(parameters.getParameter<std::string>("eventClass")),
        pTHatMin_(parameters.getParameter<double>("pTHatMin")),
        pTHatMax_(parameters.getParameter<double>("pTHatMax")) {
    if (eventClass_ != "qcd" && eventClass_ != "direct_jpsi") {
      throw cms::Exception("Configuration")
          << "ShiftMpiEventClassHook eventClass must be qcd or direct_jpsi";
    }
    if (!std::isfinite(pTHatMin_) || pTHatMin_ < 0. ||
        !std::isfinite(pTHatMax_) || (pTHatMax_ != -1. && pTHatMax_ <= pTHatMin_)) {
      throw cms::Exception("Configuration")
          << "ShiftMpiEventClassHook requires finite 0 <= pTHatMin < pTHatMax, "
             "or pTHatMax=-1";
    }
  }

  bool canVetoPartonLevel() override { return true; }

  // SoftQCD has one generic process-level class.  Retrying parton level gives
  // a new impact parameter and MPI history without changing the physical
  // non-diffractive process definition.  Pythia accounts vetoed trials in the
  // selected cross section while CMSSW receives one accepted event per slot.
  bool retryPartonLevel() override { return true; }

  bool doVetoPartonLevel(const Pythia8::Event& event) override {
    double directJpsiScale = -1.;
    for (int index = 0; index < event.size(); ++index) {
      const auto& particle = event[index];
      const int status = std::abs(particle.status());
      if ((status == 23 || status == 33) && isDirectJpsiState(particle.id())) {
        directJpsiScale = std::max(directJpsiScale, particle.pT());
      }
    }

    const bool hasDirectJpsi = directJpsiScale >= 0.;
    const bool ownsEvent = eventClass_ == "direct_jpsi" ? hasDirectJpsi : !hasDirectJpsi;
    const double scale = eventClass_ == "direct_jpsi" ? directJpsiScale : infoPtr->pTHat();
    const bool inBin = scale >= pTHatMin_ && (pTHatMax_ < 0. || scale < pTHatMax_);
    return !(ownsEvent && inBin);
  }

private:
  std::string eventClass_;
  double pTHatMin_;
  double pTHatMax_;
};

REGISTER_USERHOOK(ShiftMpiEventClassHook);
