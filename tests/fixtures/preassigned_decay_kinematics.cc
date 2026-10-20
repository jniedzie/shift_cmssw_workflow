// Code style: AI-code. AI-generated synthetic test of the installed Geant4 decay kernel.
// The changed parent states are explicit test inputs, not simulated material losses.
#include "G4Decay.hh"
#include "G4DynamicParticle.hh"
#include "G4Event.hh"
#include "G4LorentzVector.hh"
#include "G4MuonPlus.hh"
#include "G4NeutrinoMu.hh"
#include "G4PionPlus.hh"
#include "G4PrimaryParticle.hh"
#include "G4PrimaryTransformer.hh"
#include "G4PrimaryVertex.hh"
#include "G4Step.hh"
#include "G4SystemOfUnits.hh"
#include "G4Track.hh"
#include "G4TouchableHistory.hh"
#include "G4VParticleChange.hh"
#include <cmath>
#include <iomanip>
#include <iostream>
#include <stdexcept>

int main() {
  auto pion = G4PionPlus::Definition();
  auto muon = G4MuonPlus::Definition();
  auto neutrino = G4NeutrinoMu::Definition();
  const double mass = pion->GetPDGMass();
  const double mmu = muon->GetPDGMass();
  const double q = (mass*mass-mmu*mmu)/(2.*mass);
  const double birthEnergy = 100.*GeV;
  const G4ThreeVector birthMomentum(0., 0., -std::sqrt(birthEnergy*birthEnergy-mass*mass));
  const G4ThreeVector restMomentum(.6*q, 0., .8*q);
  const double finalEnergies[] = {100.*GeV, 10.*GeV, 1.*GeV, mass};
  std::cout << std::setprecision(17) << "{\"cases\":[";
  for (unsigned i=0; i<4; ++i) {
    G4LorentzVector mu(restMomentum, std::sqrt(q*q+mmu*mmu));
    G4LorentzVector nu(-restMomentum, q);
    mu.boost(birthMomentum/birthEnergy);
    nu.boost(birthMomentum/birthEnergy);
    G4Event event(i);
    auto vertex = new G4PrimaryVertex(G4ThreeVector(), 0.);
    auto mother = new G4PrimaryParticle(pion, birthMomentum.x(), birthMomentum.y(), birthMomentum.z());
    mother->SetMass(mass);
    mother->SetProperTime(1.*ns);
    auto first = new G4PrimaryParticle(muon, mu.px(), mu.py(), mu.pz());
    first->SetMass(mmu);
    auto second = new G4PrimaryParticle(neutrino, nu.px(), nu.py(), nu.pz());
    second->SetMass(0.);
    mother->SetDaughter(first);
    mother->SetDaughter(second);
    vertex->SetPrimary(mother);
    event.AddPrimaryVertex(vertex);
    G4PrimaryTransformer transformer;
    auto tracks = transformer.GimmePrimaries(&event);
    if (tracks->size()!=1) throw std::runtime_error("Mother must be the only primary");
    auto track = tracks->front();
    if (!track->GetDynamicParticle()->GetPreAssignedDecayProducts())
      throw std::runtime_error("Missing generator-assigned decay products");
    const double energy = finalEnergies[i];
    const G4ThreeVector direction = i==0 ? G4ThreeVector(0.,0.,-1.) : G4ThreeVector(.3,.4,-std::sqrt(.75));
    track->SetKineticEnergy(energy-mass);
    track->SetMomentumDirection(direction);
    track->SetPosition(G4ThreeVector(2.*cm,3.*cm,4.*cm));
    track->SetTouchableHandle(G4TouchableHandle(new G4TouchableHistory()));
    track->SetTrackStatus(i==3 ? fStopButAlive : fAlive);
    G4Decay decay;
    decay.StartTracking(track);
    G4Step step;
    step.SetTrack(track);
    track->SetStep(&step);
    G4VParticleChange* change;
    if (i==3) {
      G4ForceCondition condition;
      decay.AtRestGetPhysicalInteractionLength(*track,&condition);
      change = decay.AtRestDoIt(*track,step);
    } else {
      change = decay.PostStepDoIt(*track,step);
    }
    if (change->GetNumberOfSecondaries()!=2) throw std::runtime_error("Wrong daughter count");
    G4LorentzVector total;
    double actualMuonEnergy = 0.;
    for (unsigned j=0; j<2; ++j) {
      auto daughter = change->GetSecondary(j);
      total += daughter->GetDynamicParticle()->Get4Momentum();
      if (daughter->GetPosition()!=track->GetPosition()) throw std::runtime_error("Wrong birth position");
      if (daughter->GetDefinition()==muon) actualMuonEnergy = daughter->GetTotalEnergy();
    }
    const double momentum = std::sqrt(std::max(0.,energy*energy-mass*mass));
    const double energyError = std::abs(total.e()-energy);
    const double momentumError = (total.vect()-momentum*direction).mag();
    if (energyError>1.e-7*energy || momentumError>1.e-7*energy)
      throw std::runtime_error("Daughters do not conserve the transported parent's four-momentum");
    if (actualMuonEnergy>energy+1.e-7*energy) throw std::runtime_error("Muon kept the obsolete generator energy");
    if (i) std::cout << ',';
    std::cout << "{\"initial_parent_GeV\":" << birthEnergy/GeV
              << ",\"decay_parent_GeV\":" << energy/GeV
              << ",\"muon_birth_GeV\":" << actualMuonEnergy/GeV
              << ",\"energy_residual_GeV\":" << energyError/GeV
              << ",\"momentum_residual_GeV\":" << momentumError/GeV << '}';
    for (unsigned j=0; j<2; ++j) delete change->GetSecondary(j);
    change->Clear();
  }
  std::cout << "],\"passed\":true,\"actual_material_transport_test\":false}\n";
}
