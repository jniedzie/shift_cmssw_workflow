#!/usr/bin/env python3

from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ir1_fluka_geometry import (  # noqa: E402
    audit_gdml_material_references,
    expand_predefined_materials,
)


class Ir1FlukaMaterialsTest(unittest.TestCase):
    def test_material_reference_audit_finds_undefined_reference(self):
        gdml = """<?xml version="1.0"?>
<gdml>
  <materials><material name="Defined"><D value="1"/></material></materials>
  <solids><box name="Box" x="1" y="1" z="1"/></solids>
  <structure>
    <volume name="DefinedVolume"><materialref ref="Defined"/><solidref ref="Box"/></volume>
    <volume name="MissingVolume"><materialref ref="Missing"/><solidref ref="Box"/></volume>
  </structure>
</gdml>
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.gdml"
            path.write_text(gdml, encoding="utf-8")
            audit = audit_gdml_material_references(path)
        self.assertEqual(audit["defined_material_count"], 1)
        self.assertEqual(audit["referenced_material_count"], 2)
        self.assertEqual(audit["undefined_materials"], ["Missing"])

    def test_predefined_materials_are_written_as_explicit_compositions(self):
        import pyg4ometry.geant4 as geant4
        from pyg4ometry.gdml import Writer

        registry = geant4.Registry()
        air = geant4.MaterialPredefined("G4_AIR", registry)
        aluminium = geant4.MaterialPredefined("G4_Al", registry)
        air_mixture = geant4.MaterialCompound("AirMixture", 0.0012, 1, registry)
        air_mixture.add_material(air, 1.0)
        world_solid = geant4.solid.Box("WorldSolid", 100, 100, 100, registry)
        child_solid = geant4.solid.Box("ChildSolid", 10, 10, 10, registry)
        world = geant4.LogicalVolume(world_solid, air, "World", registry)
        child = geant4.LogicalVolume(child_solid, aluminium, "Child", registry)
        geant4.PhysicalVolume([0, 0, 0], [0, 0, 0], child, "ChildPlacement", world, registry)
        registry.setWorld(world)

        expanded = expand_predefined_materials(registry)
        self.assertEqual(expanded["G4_AIR"], "G4_AIR")
        self.assertEqual(expanded["G4_Al"], "Material_G4_Al")
        self.assertNotEqual(world.material.type, "nist")
        self.assertNotEqual(child.material.type, "nist")
        self.assertNotEqual(air_mixture.components[0][0].type, "nist")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.gdml"
            writer = Writer()
            writer.addDetector(registry)
            writer.write(str(path))
            audit = audit_gdml_material_references(path)
        self.assertEqual(audit["undefined_materials"], [])
        self.assertGreaterEqual(audit["defined_material_count"], 2)


if __name__ == "__main__":
    unittest.main()
