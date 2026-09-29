"""Bounded CMSSW check of native CONSTANT conversion, staging and field lookup.

Run with cmsRun in the existing SHIFT CMSSW environment. All input geometry and
fields here are synthetic; no experiment payload or release rebuild is needed.
"""

import json
from pathlib import Path
import sys
import tempfile
import zipfile

import FWCore.ParameterSet.Config as cms

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from convert_cms_lss_native_fields import convert_native_fields
from convert_cms_lss_field_domains import convert_domains
from stage_cms_lss_cmssw_payload import _normalise_element, render_field_cff
from PhysicsTools.ShiftMuonSegments.shiftMuonSegments_customise import customiseShiftLssMagneticField

with tempfile.TemporaryDirectory(prefix="shift_constant_field_") as temporary:
    root = Path(temporary)
    deck = root / "synthetic.inp"
    deck.write_text(
        "RCC OUTER 0 0 -2 0 0 4 5\n"
        "ZCC HOLE 0 0 1\n"
        "CELL_A 5 +OUTER -HOLE\n"
        "CELL_B 5 +OUTER -HOLE\n"
        "ROT-DEFI 1000 0 0 0 0 -10 ROT_A\n"
        "ROT-DEFI 1000 0 0 0 0 -30 ROT_B\n"
        "MGNCREAT 1 0.5 0 0 0 0 SKEW\n"
        "MGNCREAT 2 -3 4 0 0 0 &\n"
        "MGNCREAT 1 0.25 0 0 0 0 XFIELD\n"
        "MGNCREAT 1 0 0 0 0 0 &\n"
        "MGNFIELD -0.5 ROT_A 0 CELL_A 0 0 SKEW\n"
        "MGNFIELD -3 ROT_B 0 CELL_B 0 0 XFIELD\n",
        encoding="ascii",
    )
    with zipfile.ZipFile(root / "fields.zip", "w"):
        pass
    convert_native_fields(root, deck.name, root / "fields")
    geometry = root / "synthetic_bounds.json"
    geometry.write_text(json.dumps({"lattice_conversion": {"lattices": [
        {"cell": "CELL_A", "physical_cell_bounds_mm": [[-50, -50, 80], [50, 50, 120]]},
        {"cell": "CELL_B", "physical_cell_bounds_mm": [[-50, -50, 280], [50, 50, 320]]},
    ]}}))
    domains = convert_domains(deck, root / "fields/field_manifest.json", geometry)
    normalised = [_normalise_element(element, set()) for element in domains["elements"]]
    namespace = {}
    exec(compile(render_field_cff(normalised, "", "constantElements", "synthetic"),
                 "<synthetic constant field CFF>", "exec"), namespace)

factory = namespace["constantElements"]
elements = factory(modelOriginCm=(0, 0, 0), modelToCms=(1, 0, 0, 0, 1, 0, 0, 0, 1))
rotated = factory(modelOriginCm=(100, 0, 0), modelToCms=(0, -1, 0, 1, 0, 0, 0, 0, 1),
                  fieldScale=-2)
for element in rotated:
    element.name = cms.string(element.name.value() + ".rotated")
elements.extend(rotated)

process = cms.Process("CONSTANTFIELDTEST")
process.source = cms.Source("EmptySource")
process.maxEvents = cms.untracked.PSet(input=cms.untracked.int32(1))
process.load("MagneticField.Engine.uniformMagneticField_cfi")
process.UniformMagneticFieldESProducer.ZFieldInTesla = cms.double(3.8)
process = customiseShiftLssMagneticField(
    process, baseMagneticFieldProducer="UniformMagneticFieldESProducer", fieldElements=elements,
)

# Expected vectors are computed directly from the manual's B=K*(u,v,w),
# independently of the converter output. CONSTANT must survive beyond the
# irrelevant MGNCREAT radius while respecting its assigned annulus and ends.
samples = [
    ("non-unit vector and negative strength", (4, 0, 10), (-1, 1.5, -2)),
    ("negative X field", (4, 0, 30), (-3, 0, 0)),
    ("rotated vector and global scale", (100, 4, 10), (3, 2, 4)),
    ("rotated negative X field", (100, 4, 30), (0, 6, 0)),
    ("near outer boundary", (4.999, 0, 10), (-1, 1.5, -2)),
    ("near inner boundary", (1.001, 0, 10), (-1, 1.5, -2)),
    ("near longitudinal boundary", (4, 0, 11.999), (-1, 1.5, -2)),
    ("inside beam hole", (0, 0, 10), (0, 0, 3.8)),
    ("inside hole near boundary", (0.999, 0, 10), (0, 0, 3.8)),
    ("outside radial boundary", (5.001, 0, 10), (0, 0, 3.8)),
    ("outside longitudinal boundary", (4, 0, 12.001), (0, 0, 3.8)),
    ("rotated beam hole", (100, 0, 10), (0, 0, 3.8)),
]
process.validateConstantField = cms.EDAnalyzer(
    "ShiftLssMagneticFieldValidator",
    samples=cms.VPSet(*(cms.PSet(
        name=cms.string(name), pointCm=cms.vdouble(*point),
        expectedTesla=cms.vdouble(*expected), toleranceTesla=cms.double(1e-6),
    ) for name, point, expected in samples)),
)
process.validation = cms.Path(process.validateConstantField)
print("Validating", len(samples), "synthetic native-CONSTANT field probes")
