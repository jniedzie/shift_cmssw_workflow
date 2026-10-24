// Code style: AI-code. AI-generated exact-particle numerical reconciliation authority.
#ifndef SimG4Core_Generators_RecordedPrimaryParticleAuthority_h
#define SimG4Core_Generators_RecordedPrimaryParticleAuthority_h
#include "nlohmann/json.hpp"
#include <openssl/sha.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace sim {
namespace recordedPrimaryAuthority {
  inline bool digest(const std::string& value) {
    return value.size()==64 && value.find_first_not_of("0123456789abcdef")==std::string::npos;
  }
  inline std::uint64_t bits(double value) {
    std::uint64_t result; std::memcpy(&result,&value,sizeof(result)); return result;
  }
  inline double exactDouble(const std::string& text) {
    if (text.find("0x")==std::string::npos || text.find('p')==std::string::npos)
      throw std::runtime_error("Recorded-primary authority requires binary64 hexadecimal inputs");
    char* end=nullptr; const double value=std::strtod(text.c_str(),&end);
    if (end!=text.c_str()+text.size() || !std::isfinite(value))
      throw std::runtime_error("Invalid recorded-primary authority hexadecimal input");
    return value;
  }
  struct Entry {
    std::string gen; long long event; int barcode,pdg;
    std::array<double,4> p4; double mass,bound;
  };
  inline const std::vector<Entry>& entries() {
    static const std::vector<Entry> result=[] {
      const char* path=std::getenv("SHIFT_RECORDED_PRIMARY_AUTHORITY");
      const char* expected=std::getenv("SHIFT_RECORDED_PRIMARY_AUTHORITY_SHA256");
      if (!path && !expected) return std::vector<Entry>{};
      if (!path || !expected || path[0]!='/' || !digest(expected))
        throw std::runtime_error("Recorded-primary authority requires an absolute path and SHA256");
      std::ifstream stream(path,std::ios::binary);
      if (!stream) throw std::runtime_error("Cannot read recorded-primary authority");
      const std::string text((std::istreambuf_iterator<char>(stream)),std::istreambuf_iterator<char>());
      unsigned char hash[SHA256_DIGEST_LENGTH];
      SHA256(reinterpret_cast<const unsigned char*>(text.data()),text.size(),hash);
      std::ostringstream encoded; encoded<<std::hex<<std::setfill('0');
      for (unsigned char value:hash) encoded<<std::setw(2)<<static_cast<unsigned>(value);
      if (encoded.str()!=expected) throw std::runtime_error("Recorded-primary authority SHA256 changed");
      const auto data=nlohmann::json::parse(text);
      if (data.at("schema")!="shift-recorded-primary-particle-authority-v1"
          || data.at("complete")!=true || data.at("source_GEN_unchanged")!=true)
        throw std::runtime_error("Unqualified recorded-primary particle authority");
      std::vector<Entry> rows; std::set<std::string> identities;
      for (const auto& row:data.at("entries")) {
        Entry entry{row.at("source_gen_sha256").get<std::string>(),row.at("hepmc_event_number").get<long long>(),
          row.at("barcode").get<int>(),row.at("pdg").get<int>(),{},
          exactDouble(row.at("generated_mass_hex").get<std::string>()),row.at("maximum_energy_residual_GeV").get<double>()};
        if (!digest(entry.gen) || entry.event<0 || entry.barcode<=0 || entry.pdg==0 || entry.mass<0.
            || !std::isfinite(entry.bound) || entry.bound<=0. || row.at("p4_hex").size()!=4)
          throw std::runtime_error("Malformed recorded-primary particle authority");
        for (unsigned i=0;i<4;++i) entry.p4[i]=exactDouble(row.at("p4_hex")[i].get<std::string>());
        const std::string identity=entry.gen+":"+std::to_string(entry.event)+":"+std::to_string(entry.barcode);
        if (!identities.insert(identity).second) throw std::runtime_error("Duplicate recorded-primary particle authority");
        rows.push_back(entry);
      }
      if (rows.empty()) throw std::runtime_error("Empty recorded-primary particle authority");
      return rows;
    }();
    return result;
  }
}
  inline double recordedPrimaryParticleEnergyBound(long long event,int barcode,int pdg,
      double px,double py,double pz,double energy,double mass,double physicalEnergy) {
    const auto& entries=recordedPrimaryAuthority::entries();
    if (entries.empty()) return 0.;
    const char* gen=std::getenv("SHIFT_RECORDED_PRIMARY_GEN_SHA256");
    if (!gen || !recordedPrimaryAuthority::digest(gen))
      throw std::runtime_error("Recorded-primary authority needs the exact original GEN SHA256");
    const std::array<double,4> actual{{px,py,pz,energy}};
    for (const auto& row:entries) {
      if (row.gen!=gen || row.event!=event || row.barcode!=barcode || row.pdg!=pdg) continue;
      if (recordedPrimaryAuthority::bits(row.mass)!=recordedPrimaryAuthority::bits(mass)) return 0.;
      for (unsigned i=0;i<4;++i)
        if (recordedPrimaryAuthority::bits(row.p4[i])!=recordedPrimaryAuthority::bits(actual[i])) return 0.;
      const double scale=std::max({1.,energy,physicalEnergy});
      // The proved envelope is outward-rounded. Account separately for the
      // final binary64 hypot/setter evaluation; never enlarge conservation.
      return std::min(1.e-7*scale,row.bound+4.*std::numeric_limits<double>::epsilon()*scale);
    }
    return 0.;
  }
}
#endif
