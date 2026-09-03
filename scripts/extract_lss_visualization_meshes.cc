#include <algorithm>
#include <array>
#include <cctype>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "TBuffer3D.h"
#include "TGeoBBox.h"
#include "TGeoManager.h"
#include "TGeoMatrix.h"
#include "TGeoNode.h"
#include "TGeoVolume.h"

namespace {
  struct Totals {
    unsigned int meshes = 0;
    unsigned int rejectedMeshes = 0;
    unsigned long long vertices = 0;
    unsigned long long triangles = 0;
  };

  std::string upper(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char character) {
      return static_cast<char>(std::toupper(character));
    });
    return value;
  }

  bool containsAny(std::string const& value, std::vector<std::string> const& tokens) {
    return std::any_of(tokens.begin(), tokens.end(), [&](auto const& token) { return value.find(token) != std::string::npos; });
  }

  std::string sanitize(std::string value) {
    for (char& character : value) {
      if (!(std::isalnum(static_cast<unsigned char>(character)) || character == '_' || character == '-' ||
            character == '.')) {
        character = '_';
      }
    }
    return value;
  }

  std::vector<unsigned int> orderedPolygon(TBuffer3D const& buffer, unsigned int offset) {
    // Follow the TBuffer3D segment-chain approach used by AliRoot's
    // AliColladaBuffer, then triangulate each ordered polygon as a fan.
    unsigned int const edgeCount = buffer.fPols[offset];
    std::vector<std::pair<unsigned int, unsigned int>> edges;
    edges.reserve(edgeCount);
    for (unsigned int index = 0; index < edgeCount; ++index) {
      unsigned int const segment = buffer.fPols[offset + 1 + index];
      edges.emplace_back(buffer.fSegs[3 * segment + 1], buffer.fSegs[3 * segment + 2]);
    }
    if (edges.empty()) {
      return {};
    }
    std::vector<unsigned int> ordered = {edges.front().first, edges.front().second};
    edges.erase(edges.begin());
    while (!edges.empty()) {
      auto const endpoint = ordered.back();
      auto match = std::find_if(edges.begin(), edges.end(),
                                [&](auto const& edge) { return edge.first == endpoint || edge.second == endpoint; });
      if (match == edges.end()) {
        return {};
      }
      ordered.push_back(match->first == endpoint ? match->second : match->first);
      edges.erase(match);
    }
    if (ordered.size() > 2 && ordered.front() == ordered.back()) {
      ordered.pop_back();
    }
    return ordered;
  }

  void writeNode(std::ofstream& output,
                 TGeoNode const& node,
                 TGeoHMatrix const& parent,
                 std::string const& category,
                 Totals& totals,
                 std::array<std::pair<double, double>, 3> const* requiredBounds = nullptr) {
    TGeoHMatrix matrix(parent);
    matrix.Multiply(node.GetMatrix());
    std::unique_ptr<TBuffer3D> buffer(node.GetVolume()->GetShape()->MakeBuffer3D());
    if (!buffer || buffer->NbPnts() == 0 || buffer->NbPols() == 0) {
      return;
    }
    std::vector<std::array<double, 3>> vertices;
    vertices.reserve(buffer->NbPnts());
    for (unsigned int index = 0; index < buffer->NbPnts(); ++index) {
      double local[3] = {buffer->fPnts[3 * index], buffer->fPnts[3 * index + 1], buffer->fPnts[3 * index + 2]};
      double master[3];
      matrix.LocalToMaster(local, master);
      vertices.push_back({master[0], master[1], master[2]});
    }
    if (requiredBounds && std::any_of(vertices.begin(), vertices.end(), [&](auto const& vertex) {
          for (unsigned int axis = 0; axis < 3; ++axis) {
            if (vertex[axis] < (*requiredBounds)[axis].first - 0.1 ||
                vertex[axis] > (*requiredBounds)[axis].second + 0.1) {
              return true;
            }
          }
          return false;
        })) {
      ++totals.rejectedMeshes;
      std::cerr << "REJECT_OUTSIDE_ENVELOPE " << node.GetName() << '\n';
      return;
    }
    unsigned long long const firstVertex = totals.vertices + 1;
    output << "o " << sanitize(node.GetName()) << '\n';
    output << "g " << category << '\n';
    output << "usemtl " << category << '\n';
    for (auto const& vertex : vertices) {
      output << "v " << vertex[0] / 1000.0 << ' ' << vertex[1] / 1000.0 << ' ' << vertex[2] / 1000.0 << '\n';
    }
    unsigned int offset = 1;
    unsigned long long triangleCount = 0;
    for (unsigned int polygon = 0; polygon < buffer->NbPols(); ++polygon) {
      auto const ordered = orderedPolygon(*buffer, offset);
      for (unsigned int index = 1; index + 1 < ordered.size(); ++index) {
        output << "f " << firstVertex + ordered[0] << ' ' << firstVertex + ordered[index] << ' '
               << firstVertex + ordered[index + 1] << '\n';
        ++triangleCount;
      }
      offset += buffer->fPols[offset] + 2;
    }
    totals.vertices += buffer->NbPnts();
    if (triangleCount > 0) {
      ++totals.meshes;
      totals.triangles += triangleCount;
    }
  }

  void writeBoundingBox(std::ofstream& output,
                        TGeoNode const& node,
                        TGeoHMatrix const& parent,
                        std::string const& category,
                        Totals& totals,
                        std::array<std::pair<double, double>, 3> const* requiredBounds = nullptr) {
    TGeoHMatrix matrix(parent);
    matrix.Multiply(node.GetMatrix());
    auto const* bounds = dynamic_cast<TGeoBBox const*>(node.GetVolume()->GetShape());
    if (!bounds) {
      ++totals.rejectedMeshes;
      return;
    }
    double const* origin = bounds->GetOrigin();
    std::array<std::array<double, 3>, 8> vertices;
    for (unsigned int index = 0; index < vertices.size(); ++index) {
      double local[3] = {origin[0] + ((index & 1) ? bounds->GetDX() : -bounds->GetDX()),
                         origin[1] + ((index & 2) ? bounds->GetDY() : -bounds->GetDY()),
                         origin[2] + ((index & 4) ? bounds->GetDZ() : -bounds->GetDZ())};
      double master[3];
      matrix.LocalToMaster(local, master);
      vertices[index] = {master[0], master[1], master[2]};
    }
    if (requiredBounds && std::any_of(vertices.begin(), vertices.end(), [&](auto const& vertex) {
          for (unsigned int axis = 0; axis < 3; ++axis) {
            if (vertex[axis] < (*requiredBounds)[axis].first - 0.1 ||
                vertex[axis] > (*requiredBounds)[axis].second + 0.1) {
              return true;
            }
          }
          return false;
        })) {
      ++totals.rejectedMeshes;
      return;
    }
    static constexpr std::array<std::array<unsigned int, 3>, 12> triangles = {
        {{{0, 2, 3}}, {{0, 3, 1}}, {{4, 5, 7}}, {{4, 7, 6}}, {{0, 1, 5}}, {{0, 5, 4}},
         {{2, 6, 7}}, {{2, 7, 3}}, {{0, 4, 6}}, {{0, 6, 2}}, {{1, 3, 7}}, {{1, 7, 5}}}};
    unsigned long long const firstVertex = totals.vertices + 1;
    output << "o " << sanitize(node.GetName()) << '\n';
    output << "g " << category << '\n';
    output << "usemtl " << category << '\n';
    for (auto const& vertex : vertices) {
      output << "v " << vertex[0] / 1000.0 << ' ' << vertex[1] / 1000.0 << ' ' << vertex[2] / 1000.0 << '\n';
    }
    for (auto const& triangle : triangles) {
      output << "f " << firstVertex + triangle[0] << ' ' << firstVertex + triangle[1] << ' '
             << firstVertex + triangle[2] << '\n';
    }
    ++totals.meshes;
    totals.vertices += vertices.size();
    totals.triangles += triangles.size();
  }

  std::string lssCategory(TGeoNode const& node) {
    auto const* volume = node.GetVolume();
    std::string const name = upper(std::string(node.GetName()) + " " + volume->GetName());
    std::string const material = volume->GetMaterial() ? upper(volume->GetMaterial()->GetName()) : "";
    bool const lattice = name.find("_LATTICE_") != std::string::npos;
    if (!lattice && material == "CONCRETE") {
      return "lss_tunnel";
    }
    if (containsAny(material, {"AIR", "GALACTIC", "VAC", "LHE"})) {
      return "";
    }
    auto const* bounds = dynamic_cast<TGeoBBox const*>(volume->GetShape());
    if (name.find("BP") != std::string::npos && bounds && bounds->GetDX() <= 200.0 && bounds->GetDY() <= 200.0) {
      return "lss_beamline";
    }
    if (containsAny(name, {"YOKE", "VESS", "IRONO", "TSHL", "COLL", "TASB", "VV"})) {
      return "lss_magnet";
    }
    return "";
  }
}  // namespace

int main(int argc, char** argv) {
  if (argc != 9) {
    std::cerr << "Usage: " << argv[0]
              << " combined_geometry.root output.obj xmin ymin zmin xmax ymax zmax\n"
              << "Bounds are the external-model envelope in metres.\n";
    return 2;
  }
  try {
    // ROOT registers the imported manager globally and owns it through TROOT.
    // A second smart-pointer owner would double-delete it during shutdown.
    TGeoManager* manager = TGeoManager::Import(argv[1]);
    if (!manager) {
      throw std::runtime_error("failed to import combined TGeo file");
    }
    TGeoVolume* top = manager->GetTopVolume();
    if (!top || top->GetNdaughters() != 1) {
      throw std::runtime_error("expected the standard CMS world daughter to remain the sole world child");
    }
    TGeoNode* cmsNode = top->GetNode(0);
    if (!cmsNode || cmsNode->GetVolume()->GetNdaughters() != 1) {
      throw std::runtime_error("could not identify the CMS environment root");
    }
    TGeoNode* cmseNode = cmsNode->GetVolume()->GetNode(0);
    TGeoVolume* cms = cmseNode->GetVolume();
    TGeoNode* lssNode = nullptr;
    for (int index = 0; index < cms->GetNdaughters(); ++index) {
      TGeoNode* node = cms->GetNode(index);
      std::string const volumeName = node->GetVolume()->GetName();
      if (node->GetVolume()->IsAssembly() && volumeName.size() >= 9 &&
          volumeName.compare(volumeName.size() - 9, 9, "_assembly") == 0) {
        if (lssNode) {
          throw std::runtime_error("multiple external geometry assemblies found below the CMS environment");
        }
        lssNode = node;
      }
    }
    if (!lssNode) {
      throw std::runtime_error("could not identify the external geometry assembly below the CMS environment");
    }
    TGeoVolume* lss = lssNode->GetVolume();
    TGeoHMatrix cmsMatrix;
    cmsMatrix.Multiply(cmsNode->GetMatrix());
    cmsMatrix.Multiply(cmseNode->GetMatrix());
    TGeoHMatrix lssMatrix;
    lssMatrix.Multiply(cmsNode->GetMatrix());
    lssMatrix.Multiply(cmseNode->GetMatrix());
    lssMatrix.Multiply(lssNode->GetMatrix());
    std::array<std::pair<double, double>, 3> requiredLssBounds;
    for (unsigned int axis = 0; axis < 3; ++axis) {
      requiredLssBounds[axis] = {1000.0 * std::stod(argv[3 + axis]), 1000.0 * std::stod(argv[6 + axis])};
      if (requiredLssBounds[axis].first >= requiredLssBounds[axis].second) {
        throw std::runtime_error("invalid external-model envelope");
      }
    }

    std::ofstream output(argv[2]);
    if (!output) {
      throw std::runtime_error("could not create output OBJ");
    }
    output.precision(8);
    output << "# Simplified CMS plus IR1/ATLAS LSS proxy from combined CMSSW TGeo\n";
    output << "# Coordinates are metres; identity proxy model frame is not physical IR5 placement.\n";
    Totals totals;
    std::vector<std::pair<int, std::string>> const cmsComponents = {
        {6, "cms_tracker"}, {7, "cms_calo"},      {8, "cms_muon"},     {19, "cms_cavern"},
        {30, "cms_cavern"}, {31, "cms_tracker"}, {32, "cms_tracker"}, {33, "cms_calo"},
        {34, "cms_calo"},   {37, "cms_forward"}, {38, "cms_forward"}};
    for (auto const& [index, category] : cmsComponents) {
      writeNode(output, *cms->GetNode(index), cmsMatrix, category, totals);
    }
    for (int index = 0; index < lss->GetNdaughters(); ++index) {
      TGeoNode const& node = *lss->GetNode(index);
      std::string const category = lssCategory(node);
      if (!category.empty()) {
        // Boolean surfaces can contain thousands of low-level facets and ROOT's
        // raw buffer exposes untrimmed operands for some GDML composites.  The
        // TGeo-computed volume bounds retain the placement and useful scale,
        // giving a stable overview model without pretending to be CAD detail.
        writeBoundingBox(output, node, lssMatrix, category, totals, &requiredLssBounds);
      }
    }
    std::cout << "MESHES=" << totals.meshes << " REJECTED_MESHES=" << totals.rejectedMeshes
              << " VERTICES=" << totals.vertices << " TRIANGLES=" << totals.triangles << '\n';
    output.close();
    std::cout.flush();
    std::cerr.flush();
    // The imported 2.3-million-node manager is registered in ROOT's global
    // geometry list.  Some ROOT releases crash while tearing that list down;
    // all output is closed, so bypass process-global cleanup here.
    std::_Exit(0);
  } catch (std::exception const& error) {
    std::cerr << "ERROR: " << error.what() << '\n';
    return 1;
  }
  return 0;
}
