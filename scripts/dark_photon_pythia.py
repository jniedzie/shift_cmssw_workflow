"""Extract native Pythia resonance widths without rebuilding CMSSW.

This is a perturbative backend diagnostic, not a validated low-mass hadronic
model. Run inside the existing CMSSW runtime. The caller supplies natural
decay settings and must freeze the returned EW inputs and widths before
forcing a selected final state. No events or batch jobs are generated here.
"""

import json
import math
import os


_CPP = r'''
#include "Pythia8/Pythia.h"
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
namespace ShiftDarkPhotonProbe {
std::string inspect(const std::vector<std::string>& settings, int events=0) {
  Pythia8::Pythia pythia(std::string(std::getenv("PYTHIA8DATA")), false);
  pythia.readString("Print:quiet = on");
  pythia.readString("Init:showChangedSettings = off");
  pythia.readString("Init:showChangedParticleData = off");
  for (const auto& command : settings)
    if (!pythia.readString(command))
      throw std::runtime_error("Pythia rejected setting: " + command);
  if (events == 0) pythia.readString("ProcessLevel:all = off");
  if (!pythia.init()) {
    std::ostringstream errors;
    pythia.logger.errorStatistics(errors);
    throw std::runtime_error("Pythia initialization failed: " + errors.str());
  }
  auto particle = pythia.particleData.particleDataEntryPtr(32);
  if (!particle) throw std::runtime_error("No PDG 32 particle data");
  const double mass = particle->m0();
  std::ostringstream out;
  out << std::setprecision(17);
  out << "{\"pythia_version\":" << PYTHIA_VERSION
      << ",\"mass_gev\":" << mass
      << ",\"m_min_gev\":" << particle->mMin()
      << ",\"m_max_gev\":" << particle->mMax()
      << ",\"m0_min_gev\":" << particle->m0Min()
      << ",\"m0_max_gev\":" << particle->m0Max()
      << ",\"m_width_gev\":" << particle->mWidth()
      << ",\"effective_resonance_function\":" << pythia.particleData.resWidth(32, mass)
      << ",\"effective_open_resonance_function\":" << pythia.particleData.resWidthOpen(32, mass)
      << ",\"tau0_mm\":" << particle->tau0()
      << ",\"may_decay\":" << (particle->mayDecay() ? "true" : "false")
      << ",\"is_resonance\":" << (particle->isResonance() ? "true" : "false")
      << ",\"resonance_min_width_gev\":" << pythia.settings.parm("ResonanceWidths:minWidth")
      << ",\"resonance_min_threshold\":" << pythia.settings.parm("ResonanceWidths:minThreshold")
      << ",\"sin2_theta_w\":" << pythia.coupSM.sin2thetaW()
      << ",\"sin2_theta_w_bar\":" << pythia.coupSM.sin2thetaWbar()
      << ",\"alpha_em_at_mass\":" << pythia.coupSM.alphaEM(mass*mass)
      << ",\"alpha_s_at_mass\":" << pythia.coupSM.alphaS(mass*mass)
      << ",\"m_z_gev\":" << pythia.particleData.m0(23)
      << ",\"fermion_masses_gev\":{";
  const std::vector<int> ids = {1,2,3,4,5,6,11,12,13,14,15,16};
  for (size_t i=0; i<ids.size(); ++i) {
    if (i) out << ',';
    out << '\"' << ids[i] << "\":" << pythia.particleData.m0(ids[i]);
  }
  out << "},\"channels\":[";
  for (int i=0; i<particle->sizeChannels(); ++i) {
    const auto& channel = particle->channel(i);
    if (i) out << ',';
    out << "{\"on_mode\":" << channel.onMode()
        << ",\"me_mode\":" << channel.meMode()
        << ",\"branching_fraction\":" << channel.bRatio()
        << ",\"on_shell_width_gev\":" << channel.onShellWidth()
        << ",\"products\":[";
    for (int j=0; j<channel.multiplicity(); ++j) {
      if (j) out << ',';
      out << channel.product(j);
    }
    out << "]}";
  }
  out << "],\"events\":[";
  int accepted=0;
  for (int attempted=0; attempted<events*10 && accepted<events; ++attempted) {
    if (!pythia.next()) continue;
    if (accepted++) out << ',';
    out << "{\"weight\":" << pythia.info.weight() << ",\"dark_photons\":[";
    bool first=true;
    for (int i=0; i<pythia.event.size(); ++i) {
      const auto& part=pythia.event[i];
      if (part.id()!=32) continue;
      if (!first) out << ',';
      first=false;
      out << "{\"index\":" << i << ",\"status\":" << part.status()
          << ",\"mass_gev\":" << part.m()
          << ",\"tau_mm\":" << part.tau()
          << ",\"p4\":[" << part.px() << ',' << part.py() << ',' << part.pz() << ',' << part.e()
          << "],\"v_prod_mm\":[" << part.xProd() << ',' << part.yProd() << ',' << part.zProd() << ',' << part.tProd()
          << "],\"v_dec_mm\":[" << part.xDec() << ',' << part.yDec() << ',' << part.zDec() << ',' << part.tDec()
          << "],\"daughters\":[";
      const auto daughters=part.daughterList();
      for (size_t j=0; j<daughters.size(); ++j) {
        if (j) out << ',';
        const auto& child=pythia.event[daughters[j]];
        out << "{\"id\":" << child.id() << ",\"v_prod_mm\":[" << child.xProd() << ',' << child.yProd() << ',' << child.zProd() << ',' << child.tProd() << "]}";
      }
      out << "]}";
    }
    out << "]}";
  }
  out << "],\"accepted\":" << accepted
      << ",\"sigma_gen_mb\":" << pythia.info.sigmaGen()
      << ",\"sigma_err_mb\":" << pythia.info.sigmaErr()
      << ",\"n_tried\":" << pythia.info.nTried()
      << ",\"n_accepted\":" << pythia.info.nAccepted() << '}';
  return out.str();
}
}
'''


def _native_inspect(settings, events=0):
    if not os.environ.get("CMSSW_BASE") or not os.environ.get("PYTHIA8DATA"):
        raise RuntimeError("Enter the existing CMSSW runtime; no build is performed")
    import ROOT
    if ROOT.gSystem.Load("libpythia8") < 0:
        raise RuntimeError("Cannot load the current CMSSW Pythia8 library")
    if not hasattr(ROOT, "ShiftDarkPhotonProbe"):
        if not ROOT.gInterpreter.Declare(_CPP):
            raise RuntimeError("Cannot declare the native Pythia inspection helper")
    commands = ROOT.std.vector("string")()
    for command in settings:
        commands.push_back(command)
    result = json.loads(str(ROOT.ShiftDarkPhotonProbe.inspect(commands, events)))
    result["input_settings"] = list(settings)
    result["pythia_data"] = os.environ["PYTHIA8DATA"]
    result["cmssw_version"] = os.environ.get("CMSSW_VERSION")
    result["diagnostic_overrides"] = ["ProcessLevel:all = off"] if events == 0 else []
    return result


def measure_widths(settings):
    """Return natural native widths/BRs and EW inputs before decay forcing.

    Settings must specify the pure dark-photon process and all couplings.
    Channel forcing and manual width changes are rejected because their
    effect on cross sections cannot be confused with the natural decay table.
    The result remains explicitly provisional for perturbative QCD.
    """
    if not isinstance(settings, (list, tuple)) or not all(isinstance(s, str) for s in settings):
        raise TypeError("settings must be a sequence of Pythia command strings")
    harmless = {"32:onmode=on", "32:onmode=true", "32:doforcewidth=off", "32:doforcewidth=false"}
    forbidden = ("32:on", "32:addchannel", "32:onechannel", "32:mwidth", "32:doforcewidth")
    for setting in settings:
        compact = setting.lower().replace(" ", "")
        if compact.startswith(forbidden) and compact not in harmless:
            raise ValueError("Measure natural widths before channel or width forcing")
    result = _native_inspect(settings)
    width = result["m_width_gev"]
    if result["mass_gev"] < 12:
        raise ValueError("Native quark widths are not the required low-mass spectral model")
    if not math.isfinite(width) or width <= result["resonance_min_width_gev"]:
        raise ValueError("Natural resonance width is below the Pythia decay threshold")
    if not result["may_decay"] or not result["is_resonance"]:
        raise ValueError("Dark photon is not an unstable Pythia resonance")
    result["schema"] = "shift-dark-photon-native-width-diagnostic-v1"
    result["width_scope"] = "native-Pythia-perturbative-QCD-pilot-provisional"
    result["physical_width_validated"] = False
    result["proper_length_mm"] = 1.973269804e-13 / width
    result["sum_partial_widths_gev"] = math.fsum(c["on_shell_width_gev"] for c in result["channels"])
    if not math.isclose(result["sum_partial_widths_gev"], width, rel_tol=1e-10):
        raise ValueError("Native nominal total and channel widths do not close")
    return result
