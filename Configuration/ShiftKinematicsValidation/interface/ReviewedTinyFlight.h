// Code style: AI-code. AI-generated opt-in, exact-source tiny-flight review.
#ifndef SimG4Core_Generators_ReviewedTinyFlight_h
#define SimG4Core_Generators_ReviewedTinyFlight_h
#include "SimG4Core/Generators/interface/PromptDecaySupport.h"
#include "SimG4Core/Generators/interface/RecordedPrimaryParticleAuthority.h"
#include "HepMC/GenEvent.h"
#include "HepMC3/GenEvent.h"
#include <iostream>

namespace sim::reviewedTinyFlight {
  inline long long eventNumber(const HepMC::GenParticle& p) {
    return p.parent_event() ? p.parent_event()->event_number() : -1;
  }
  inline long long eventNumber(const HepMC3::GenParticle& p) {
    return p.parent_event() ? p.parent_event()->event_number() : -1;
  }
  inline const nlohmann::json& entries() {
    static const nlohmann::json result=[] {
      using namespace recordedPrimaryAuthority;
      // Reuse the existing capsule's SHA-bound authority and original GEN hash.
      // This never activates without that explicitly pinned environment.
      if (recordedPrimaryAuthority::entries().empty()) return nlohmann::json::array();
      std::ifstream stream(std::getenv("SHIFT_RECORDED_PRIMARY_AUTHORITY"),std::ios::binary);
      const std::string text((std::istreambuf_iterator<char>(stream)),std::istreambuf_iterator<char>());
      unsigned char hash[SHA256_DIGEST_LENGTH];
      SHA256(reinterpret_cast<const unsigned char*>(text.data()),text.size(),hash);
      std::ostringstream encoded; encoded<<std::hex<<std::setfill('0');
      for (unsigned char value:hash) encoded<<std::setw(2)<<static_cast<unsigned>(value);
      if (encoded.str()!=std::getenv("SHIFT_RECORDED_PRIMARY_AUTHORITY_SHA256"))
        throw std::runtime_error("Tiny-flight authority changed while reading");
      const auto data=nlohmann::json::parse(text);
      const auto rows=data.value("reviewed_tiny_flights",nlohmann::json::array());
      if (!rows.is_array()) throw std::runtime_error("Malformed tiny-flight review");
      std::set<std::string> identities;
      for (const auto& row:rows) {
        const auto gen=row.at("source_gen_sha256").get<std::string>();
        const auto number=row.at("hepmc_event_number").get<long long>();
        const auto barcode=row.at("barcode").get<int>();
        const double limit=row.at("maximum_flight_mm").get<double>();
        if (!digest(gen) || number<0 || barcode<=0 || row.at("status")!=2
            || std::abs(row.at("pdg").get<int>())!=413 || !std::isfinite(limit)
            || limit<=1.e-5 || limit>1.5e-5 || row.at("daughter_barcodes").empty())
          throw std::runtime_error("Tiny-flight review exceeds its explicit Dstar scope");
        for (const char* key:{"p4_hex","birth_hex","end_hex"}) {
          if (row.at(key).size()!=4) throw std::runtime_error("Malformed tiny-flight vector");
          for (const auto& value:row.at(key)) exactDouble(value.get<std::string>());
        }
        if (exactDouble(row.at("generated_mass_hex").get<std::string>())<0.)
          throw std::runtime_error("Invalid tiny-flight mass");
        const auto identity=gen+":"+std::to_string(number)+":"+std::to_string(barcode);
        if (!identities.insert(identity).second) throw std::runtime_error("Duplicate tiny-flight review");
      }
      return rows;
    }();
    return result;
  }

  template <typename Particle>
  bool accepts(const Particle& p,double originalBudgetMm) {
    // The ordinary 10 nm policy stays unchanged. A zero/default budget never
    // enables this exception, and other future policies need their own review.
    if (originalBudgetMm!=1.e-5 || !p.production_vertex() || !p.end_vertex()
        || p.status()!=2 || std::abs(promptDecay::pdg(p))!=413) return false;
    const auto& rows=entries();
    if (rows.empty()) return false;
    const char* gen=std::getenv("SHIFT_RECORDED_PRIMARY_GEN_SHA256");
    if (!gen || !recordedPrimaryAuthority::digest(gen))
      throw std::runtime_error("Tiny-flight review requires the original GEN SHA256");
    const auto& momentum=p.momentum();
    const auto& a=p.production_vertex()->position();
    const auto& z=p.end_vertex()->position();
    const std::array<double,4> p4{{momentum.px(),momentum.py(),momentum.pz(),momentum.e()}};
    const std::array<double,4> birth{{a.x(),a.y(),a.z(),a.t()}},end{{z.x(),z.y(),z.z(),z.t()}};
    for (const auto& row:rows) {
      if (row.at("source_gen_sha256")!=gen || row.at("hepmc_event_number")!=eventNumber(p)
          || row.at("barcode")!=promptDecay::barcode(p) || row.at("pdg")!=promptDecay::pdg(p)) continue;
      using namespace recordedPrimaryAuthority;
      if (bits(exactDouble(row.at("generated_mass_hex")))!=bits(p.generated_mass())) return false;
      for (unsigned i=0;i<4;++i)
        if (bits(exactDouble(row.at("p4_hex")[i]))!=bits(p4[i])
            || bits(exactDouble(row.at("birth_hex")[i]))!=bits(birth[i])
            || bits(exactDouble(row.at("end_hex")[i]))!=bits(end[i])) return false;
      std::vector<int> daughters;
      for (const auto* child:promptDecay::children(p)) daughters.push_back(promptDecay::barcode(*child));
      if (promptDecay::incoming(p)!=1 || daughters!=row.at("daughter_barcodes").get<std::vector<int>>()) return false;
      const double flight=std::hypot(z.x()-a.x(),z.y()-a.y(),z.z()-a.z()),ct=z.t()-a.t();
      const double limit=row.at("maximum_flight_mm");
      if (!std::isfinite(flight) || !std::isfinite(ct) || flight<0. || ct<0.
          || flight>limit || ct>limit) return false;
      std::clog << "[SHIFT reviewed tiny-flight] GEN=" << gen << " event=" << eventNumber(p)
                << " barcode=" << promptDecay::barcode(p) << " pdg=" << promptDecay::pdg(p)
                << " flight_mm=" << flight << " original_budget_mm=" << originalBudgetMm
                << " uncertainty=entire_affected_event_response" << std::endl;
      return true;
    }
    return false;
  }
}
#endif
