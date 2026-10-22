// Code style: AI-code. AI-generated read-only parent decay/terminal diagnostic.
// This is a preload for bounded physics tests, not a production physics process.
#include "SimG4Core/Application/interface/SteppingAction.h"
#include "SimG4Core/Notification/interface/GenParticleInfo.h"
#include "G4Decay.hh"
#include "G4DynamicParticle.hh"
#include "G4Event.hh"
#include "G4EventManager.hh"
#include "G4Material.hh"
#include "G4PrimaryParticle.hh"
#include "G4Step.hh"
#include "G4VParticleChange.hh"
#include "G4VProcess.hh"
#include "CLHEP/Units/SystemOfUnits.h"
#include <cmath>
#include <cstdlib>
#include <dlfcn.h>
#include <iomanip>
#include <iostream>
#include <sstream>

namespace {
int barcode(G4Track const& track) {
  auto primary = track.GetDynamicParticle()->GetPrimaryParticle();
  auto info = primary ? dynamic_cast<GenParticleInfo*>(primary->GetUserInformation()) : nullptr;
  return info ? info->id() : -1;
}
int eventId() {
  auto manager = G4EventManager::GetEventManager();
  auto event = manager ? manager->GetConstCurrentEvent() : nullptr;
  return event ? event->GetEventID() : -1;
}
void vector(std::ostream& out, G4ThreeVector const& value, double unit) {
  out << '[' << value.x()/unit << ',' << value.y()/unit << ',' << value.z()/unit << ']';
}
void four(std::ostream& out, G4LorentzVector const& value) {
  out << '[' << value.px()/CLHEP::GeV << ',' << value.py()/CLHEP::GeV << ','
      << value.pz()/CLHEP::GeV << ',' << value.e()/CLHEP::GeV << ']';
}
template<class Function> Function delegate(char const* symbol) {
  auto pointer = reinterpret_cast<Function>(dlsym(RTLD_NEXT, symbol));
  if (!pointer) {
    std::cerr << "[SHIFTParentLoggerError] " << symbol << ':' << dlerror() << std::endl;
    std::abort();
  }
  Dl_info location{};
  dladdr(reinterpret_cast<void*>(pointer), &location);
  std::cerr << "[SHIFTParentLoggerDelegate] " << symbol << '='
            << (location.dli_fname ? location.dli_fname : "unknown") << std::endl;
  return pointer;
}
}

G4VParticleChange* G4Decay::DecayIt(G4Track const& track, G4Step const& step) {
  using Function = G4VParticleChange* (*)(G4Decay*, G4Track const&, G4Step const&);
  static Function original = delegate<Function>("_ZN7G4Decay7DecayItERK7G4TrackRK6G4Step");
  // Read the entry state before the native decay can change anything. The
  // returned daughters have not yet reached stacking, including neutrinos.
  const auto parent = track.GetDynamicParticle()->Get4Momentum();
  const auto entryKineticEnergy = track.GetKineticEnergy();
  const auto entryDynamicMass = track.GetDynamicParticle()->GetMass();
  const auto position = track.GetPosition();
  const auto proper = track.GetProperTime();
  const auto global = track.GetGlobalTime();
  const auto assigned = track.GetDynamicParticle()->GetPreAssignedDecayProperTime();
  const bool preassigned = track.GetDynamicParticle()->GetPreAssignedDecayProducts() != nullptr;
  const auto inputStatus = track.GetTrackStatus();
  auto change = original(this, track, step);
  if (!preassigned) return change;
  std::ostringstream out;
  out << std::setprecision(17) << "[SHIFTParentDecay] {\"g4_event\":" << eventId()
      << ",\"trackId\":" << track.GetTrackID() << ",\"parentId\":" << track.GetParentID()
      << ",\"barcode\":" << barcode(track) << ",\"pdg\":" << track.GetDefinition()->GetPDGEncoding()
      << ",\"entry_track_status\":" << inputStatus << ",\"parent_entry_p4_GeV\":";
  four(out, parent);
  out << ",\"position_mm\":"; vector(out, position, CLHEP::mm);
  out << ",\"proper_time_ns\":" << proper/CLHEP::ns
      << ",\"assigned_proper_time_ns\":" << assigned/CLHEP::ns
      << ",\"global_time_ns\":" << global/CLHEP::ns
      << ",\"birth_kinetic_energy_GeV\":" << track.GetVertexKineticEnergy()/CLHEP::GeV
      << ",\"entry_kinetic_energy_GeV\":" << entryKineticEnergy/CLHEP::GeV
      << ",\"entry_dynamic_mass_GeV\":" << entryDynamicMass/CLHEP::GeV
      << ",\"PDG_mass_GeV\":" << track.GetDefinition()->GetPDGMass()/CLHEP::GeV
      << ",\"preassigned\":true,\"children\":[";
  for (int i=0; i<change->GetNumberOfSecondaries(); ++i) {
    auto child = change->GetSecondary(i);
    if (i) out << ',';
    out << "{\"barcode\":" << barcode(*child) << ",\"pdg\":" << child->GetDefinition()->GetPDGEncoding()
        << ",\"p4_GeV\":"; four(out, child->GetDynamicParticle()->Get4Momentum());
    out << ",\"position_mm\":"; vector(out, child->GetPosition(), CLHEP::mm);
    out << ",\"global_time_ns\":" << child->GetGlobalTime()/CLHEP::ns << '}';
  }
  out << "]}";
  std::cerr << out.str() << std::endl;
  return change;
}

void SteppingAction::UserSteppingAction(G4Step const* step) {
  using Function = void (*)(SteppingAction*, G4Step const*);
  static Function original = delegate<Function>("_ZN14SteppingAction18UserSteppingActionEPK6G4Step");
  auto track = step->GetTrack();
  const int before = track->GetTrackStatus();
  original(this, step);
  const int pdg = std::abs(track->GetDefinition()->GetPDGEncoding());
  const int code = barcode(*track);
  if (!(pdg == 13 || (code > 0 && pdg > 100))) return;
  auto pre = step->GetPreStepPoint();
  auto post = step->GetPostStepPoint();
  auto process = post->GetProcessDefinedStep();
  const int subtype = process ? process->GetProcessSubType() : -1;
  if (!(track->GetCurrentStepNumber() == 1 || before != fAlive || track->GetTrackStatus() != fAlive ||
        subtype == 121 || subtype == 201)) return;
  auto material = pre->GetMaterial();
  std::ostringstream out;
  out << std::setprecision(17) << "[SHIFTParentTerminal] {\"g4_event\":" << eventId()
      << ",\"barcode\":" << code << ",\"trackId\":" << track->GetTrackID()
      << ",\"parentId\":" << track->GetParentID() << ",\"pdg\":" << track->GetDefinition()->GetPDGEncoding()
      << ",\"step\":" << track->GetCurrentStepNumber()
      << ",\"status_before_original_action\":" << before
      << ",\"status_after_original_action\":" << track->GetTrackStatus()
      << ",\"process\":\"" << (process ? process->GetProcessName() : "none")
      << "\",\"process_subtype\":" << subtype << ",\"material\":\""
      << (material ? material->GetName() : "unknown") << "\",\"density_g_cm3\":"
      << (material ? material->GetDensity()/(CLHEP::g/CLHEP::cm3) : -1)
      << ",\"position_mm\":"; vector(out, track->GetPosition(), CLHEP::mm);
  out << ",\"proper_time_ns\":" << track->GetProperTime()/CLHEP::ns
      << ",\"assigned_proper_time_ns\":" << track->GetDynamicParticle()->GetPreAssignedDecayProperTime()/CLHEP::ns
      << ",\"birth_kinetic_energy_GeV\":" << track->GetVertexKineticEnergy()/CLHEP::GeV
      << ",\"mass_GeV\":" << track->GetDefinition()->GetPDGMass()/CLHEP::GeV
      << ",\"birth_position_mm\":"; vector(out, track->GetVertexPosition(), CLHEP::mm);
  out << ",\"birth_direction\":"; vector(out, track->GetVertexMomentumDirection(), 1.);
  out << ",\"pre_step_kinetic_energy_GeV\":" << pre->GetKineticEnergy()/CLHEP::GeV
      << ",\"kinetic_energy_GeV\":" << track->GetKineticEnergy()/CLHEP::GeV
      << ",\"post_step_track_p4_GeV\":"; four(out, track->GetDynamicParticle()->Get4Momentum());
  out << '}';
  std::cerr << out.str() << std::endl;
}
