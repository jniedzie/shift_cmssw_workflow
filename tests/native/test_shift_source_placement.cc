// Code style: AI-code. AI-generated native synthetic graph regression test.
#include "Configuration/GenProduction/interface/FixedSourcePlacement.h"
#include "HepMC/GenParticle.h"
#include <cassert>
#include <iostream>
#include <limits>

std::unique_ptr<HepMC::GenEvent> graph(HepMC::Units::LengthUnit unit) {
  auto value = std::make_unique<HepMC::GenEvent>(HepMC::Units::GEV, unit);
  value->set_event_number(731);
  value->weights().push_back(1.234567890123e-14);
  value->weights().push_back(-2.5);
  auto* source = new HepMC::GenVertex(HepMC::FourVector(1., -2., 148123., 7.));
  auto* decay = new HepMC::GenVertex(HepMC::FourVector(3., 4., 148023., 109.));
  auto* incoming = new HepMC::GenParticle(HepMC::FourVector(0., 0., -6800., 6800.1), 2212, 4);
  auto* parent = new HepMC::GenParticle(HepMC::FourVector(2., 3., -100., 100.2), 443, 2);
  source->add_particle_in(incoming);
  source->add_particle_out(parent);
  decay->add_particle_in(parent);
  decay->add_particle_out(new HepMC::GenParticle(HepMC::FourVector(1., 2., -60., 60.2), 13, 1));
  decay->add_particle_out(new HepMC::GenParticle(HepMC::FourVector(1., 1., -40., 40.2), -13, 1));
  value->add_vertex(source);
  value->add_vertex(decay);
  value->set_signal_process_vertex(source);
  return value;
}

int main() {
  std::array<double, 3> target{-6.800226226267938, 0., 14800.};
  for (auto unit : {HepMC::Units::MM, HepMC::Units::CM}) {
    auto before = graph(unit);
    auto result = shift::placeSource(before.get(), target);
    auto const& after = *result.graph;
    assert(after.event_number() == before->event_number());
    assert(after.weights() == before->weights());
    assert(after.length_unit() == before->length_unit());
    assert(after.momentum_unit() == before->momentum_unit());
    assert(after.particles_size() == before->particles_size());
    assert(after.vertices_size() == before->vertices_size());
    double scale = unit == HepMC::Units::MM ? 1. : 10.;
    auto const& point = after.signal_process_vertex()->position();
    assert(std::abs(point.x() * scale - target[0] * 10.) < 1.e-9);
    assert(std::abs(point.y() * scale - target[1] * 10.) < 1.e-9);
    assert(std::abs(point.z() * scale - target[2] * 10.) < 1.e-9);
    for (auto p = before->particles_begin(); p != before->particles_end(); ++p) {
      auto const* copy = after.barcode_to_particle((*p)->barcode());
      assert(copy && copy->pdg_id() == (*p)->pdg_id() && copy->status() == (*p)->status());
      assert(copy->momentum() == (*p)->momentum());
      assert((copy->production_vertex() ? copy->production_vertex()->barcode() : 0) ==
             ((*p)->production_vertex() ? (*p)->production_vertex()->barcode() : 0));
      assert((copy->end_vertex() ? copy->end_vertex()->barcode() : 0) ==
             ((*p)->end_vertex() ? (*p)->end_vertex()->barcode() : 0));
    }
    for (auto v = before->vertices_begin(); v != before->vertices_end(); ++v) {
      auto const* copy = after.barcode_to_vertex((*v)->barcode());
      assert(copy && copy->position().t() == (*v)->position().t());
      assert(std::abs((copy->position().z() - point.z()) -
                       ((*v)->position().z() - before->signal_process_vertex()->position().z())) < 1.e-9);
    }
    before->set_signal_process_vertex(nullptr);
    assert(shift::placeSource(before.get(), target).graph);
  }
  unsigned int rejected = 0;
  try { shift::placeSource(nullptr, target); } catch (std::invalid_argument const&) { ++rejected; }
  HepMC::GenEvent empty;
  try { shift::placeSource(&empty, target); } catch (std::invalid_argument const&) { ++rejected; }
  auto bad = graph(HepMC::Units::MM);
  target[0] = std::numeric_limits<double>::quiet_NaN();
  try { shift::placeSource(bad.get(), target); } catch (std::invalid_argument const&) { ++rejected; }
  target[0] = 0.;
  bad->signal_process_vertex()->set_position(HepMC::FourVector(0., 0., 0., std::numeric_limits<double>::infinity()));
  try { shift::placeSource(bad.get(), target); } catch (std::invalid_argument const&) { ++rejected; }
  assert(rejected == 4);
  std::cout << "Passed native MM/CM graph, tiny weights, p4, edges, vertices, fallback and four failure checks\n";
}
