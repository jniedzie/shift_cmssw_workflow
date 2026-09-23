#include <TError.h>
#include <TGeoManager.h>
#include <TGeoOverlap.h>
#include <TPolyMarker3D.h>
#include <TSystem.h>

#include <algorithm>
#include <iostream>

void audit_gdml_root(const char* gdmlPath, double toleranceMm = 1.0e-3,
                     bool runNavigation = true) {
  TGeoManager::SetDefaultUnits(TGeoManager::kG4Units);
  const auto oldErrorLevel = gErrorIgnoreLevel;
  gErrorIgnoreLevel = kFatal;
  auto* geometry = TGeoManager::Import(gdmlPath);
  gErrorIgnoreLevel = oldErrorLevel;
  if (!geometry) {
    std::cout << "SHIFT_ROOT_IMPORT_FAILED" << std::endl;
    gSystem->Exit(2);
    return;
  }

  std::cout << "SHIFT_ROOT_IMPORT_OK volumes="
            << geometry->GetListOfVolumes()->GetEntries()
            << " nodes=" << geometry->GetListOfNodes()->GetEntries() << std::endl;
  geometry->CheckOverlaps(toleranceMm);
  const auto overlapCount = geometry->GetListOfOverlaps()->GetEntries();
  std::cout << "SHIFT_ROOT_OVERLAPS count=" << overlapCount
            << " tolerance_mm=" << toleranceMm << std::endl;
  for (int index = 0; index < overlapCount; ++index) {
    auto* overlap = static_cast<TGeoOverlap*>(geometry->GetListOfOverlaps()->At(index));
    auto* points = overlap->GetPolyMarker();
    std::cout << "SHIFT_ROOT_OVERLAP_DETAIL index=" << index
              << " first=" << overlap->GetFirstVolume()->GetName()
              << " second=" << overlap->GetSecondVolume()->GetName()
              << " depth_mm=" << overlap->GetOverlap()
              << " sampled_points=" << (points ? points->GetN() : 0) << std::endl;
    if (!points) continue;
    const auto* xyz = points->GetP();
    const int reported = std::min(points->GetN(), 5);
    for (int point = 0; point < reported; ++point) {
      std::cout << "SHIFT_ROOT_OVERLAP_POINT index=" << index
                << " point=" << point << " x_mm=" << xyz[3 * point]
                << " y_mm=" << xyz[3 * point + 1]
                << " z_mm=" << xyz[3 * point + 2] << std::endl;
    }
  }
  if (runNavigation) {
    geometry->Test();
    std::cout << "SHIFT_ROOT_TEST_DONE" << std::endl;
  }
  gSystem->Exit(overlapCount == 0 ? 0 : 3);
}
