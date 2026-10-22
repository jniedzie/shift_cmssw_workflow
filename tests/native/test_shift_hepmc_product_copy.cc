// Code style: AI-code. AI-generated native deep-copy contract regression.
#include "SimDataFormats/GeneratorProducts/interface/HepMCProduct.h"
#include "HepMC/GenEvent.h"
#include "HepMC/GenParticle.h"
#include "HepMC/GenVertex.h"
#include "HepMC/Units.h"
#include <cassert>

int main() {
  for (auto unit : {HepMC::Units::MM, HepMC::Units::CM}) {
    auto* graph = new HepMC::GenEvent(HepMC::Units::GEV, unit);
    auto* vertex = new HepMC::GenVertex(HepMC::FourVector(-100., 2., 148000., -148000.));
    auto* parent = new HepMC::GenParticle(HepMC::FourVector(0., 0., -10., 11.), 211, 2);
    auto* daughter = new HepMC::GenParticle(HepMC::FourVector(1., 2., -3., 4.), 13, 1);
    vertex->add_particle_in(parent); vertex->add_particle_out(daughter);
    graph->add_vertex(vertex); graph->set_signal_process_vertex(vertex);
    graph->set_event_number(17); graph->weights().push_back(-1.e-15);
    graph->set_random_states({123, 456});
    edm::HepMCProduct original(graph);
    edm::HepMCProduct copy(original);
    auto* result = copy.GetEvent();
    assert(result && result != original.GetEvent());
    assert(result->event_number() == 17 && result->length_unit() == unit);
    assert(result->weights() == graph->weights() && result->random_states() == graph->random_states());
    assert(result->particles_size() == 2 && result->vertices_size() == 1);
    auto* copiedVertex = result->signal_process_vertex();
    assert(copiedVertex != vertex && copiedVertex->position() == vertex->position());
    assert(copiedVertex->particles_in_size() == 1 && copiedVertex->particles_out_size() == 1);
    auto* copiedDaughter = result->barcode_to_particle(daughter->barcode());
    assert(copiedDaughter != daughter && copiedDaughter->momentum() == daughter->momentum());
    assert(copiedDaughter->pdg_id() == 13 && copiedDaughter->status() == 1);
    copiedDaughter->set_status(2);
    assert(daughter->status() == 1); // Output owns its graph; mutation never aliases input.
  }
}
