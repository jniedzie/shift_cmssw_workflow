// Code style: AI-code. AI-generated deterministic whole-HepMC source placement.
#ifndef Configuration_GenProduction_FixedSourcePlacement_h
#define Configuration_GenProduction_FixedSourcePlacement_h

#include "HepMC/GenEvent.h"
#include "HepMC/GenVertex.h"
#include "HepMC/Units.h"
#include <array>
#include <cmath>
#include <memory>
#include <stdexcept>

namespace shift {
  struct FixedSourcePlacement {
    std::unique_ptr<HepMC::GenEvent> graph;
    std::array<double, 4> originalMm;
    std::array<double, 3> targetMm;
  };

  inline FixedSourcePlacement placeSource(HepMC::GenEvent const* original,
                                          std::array<double, 3> const& targetCm) {
    if (!original || original->vertices_empty())
      throw std::invalid_argument("Source placement requires a nonempty HepMC graph");
    double mmPerUnit;
    if (original->length_unit() == HepMC::Units::MM)
      mmPerUnit = 1.;
    else if (original->length_unit() == HepMC::Units::CM)
      mmPerUnit = 10.;
    else
      throw std::invalid_argument("Unsupported HepMC length unit");
    auto const* source = original->signal_process_vertex();
    if (!source)
      source = *original->vertices_begin();
    auto const& anchor = source->position();
    std::array<double, 4> before{anchor.x(), anchor.y(), anchor.z(), anchor.t()};
    for (double value : before)
      if (!std::isfinite(value))
        throw std::invalid_argument("Nonfinite HepMC source coordinate");
    std::array<double, 3> delta, targetMm;
    for (unsigned int i = 0; i < 3; ++i) {
      if (!std::isfinite(targetCm[i]) || !std::isfinite(targetCm[i] * 10.))
        throw std::invalid_argument("Source point coordinates must be finite in mm");
      targetMm[i] = targetCm[i] * 10.;
      delta[i] = targetMm[i] / mmPerUnit - before[i];
      if (!std::isfinite(delta[i]))
        throw std::invalid_argument("Source translation overflows HepMC coordinates");
    }
    auto placed = std::make_unique<HepMC::GenEvent>(*original);
    for (auto vertex = placed->vertices_begin(); vertex != placed->vertices_end(); ++vertex) {
      auto const& position = (*vertex)->position();
      if (!(std::isfinite(position.x()) && std::isfinite(position.y()) &&
            std::isfinite(position.z()) && std::isfinite(position.t())))
        throw std::invalid_argument("Nonfinite HepMC vertex coordinate");
      HepMC::FourVector translated(position.x() + delta[0], position.y() + delta[1],
                                   position.z() + delta[2], position.t());
      if (!(std::isfinite(translated.x()) && std::isfinite(translated.y()) && std::isfinite(translated.z())))
        throw std::invalid_argument("Translated HepMC vertex is nonfinite");
      (*vertex)->set_position(translated);
    }
    for (double& value : before) {
      value *= mmPerUnit;
      if (!std::isfinite(value))
        throw std::invalid_argument("Source coordinate overflows mm metadata");
    }
    return {std::move(placed), before, targetMm};
  }
}  // namespace shift
#endif
