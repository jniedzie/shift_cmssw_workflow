# IR1/ATLAS LSS proxy model

This directory contains a frozen engineering fixture copied on 2026-08-28
from:

```text
/eos/project/l/lhc-mib/press148m_4/secTest
```

The master input identifies LineBuilder revision 432, FEDB revision 1566,
`LHC/IR1`, Beam 2 at 6.5 TeV, the ALFA study, and the ATLAS UX15 cavern. It is
not an IR5/LSS5 model, not a CMS cavern model, and not a Run-3 model. It may be
used only as a provisional software and model-sensitivity proxy while the
authoritative Run-3 IR5 bundle is unavailable.

`source/` contains the master FLUKA input, the field callback and its common
block, and every field-map file referenced by active `USRGCALL` cards. Compiled
objects, executables, run products, beam-gas sources, and obsolete duplicate
files were intentionally excluded. `SOURCE_SHA256SUMS` records the copied
bytes exactly.

The source deck uses FLUKA region numbers to bind geometry to constant,
analytic, and interpolated magnetic fields. A GDML conversion contains only
geometry and materials; it does not reproduce those field assignments,
FLUKA transport physics, biasing, or source/scoring behavior. Generated GDML
must never be enabled in production without the separately validated field
implementation and an explicit coordinate/placement transform.

Extract the field assignments independently of geometry conversion with:

```bash
python3 scripts/extract_ir1_fluka_fields.py --output /tmp/ir1_proxy_fields.json
```

The master deck parses after two recorded syntax normalizations, but
`pyg4ometry` 1.4.6 currently fails while meshing the `XRPHA6Rc` FLUKA lattice
cell for its Geant4 bounding box. The converter refuses to drop lattice cards:
doing so would produce a plausible-looking but physically incomplete model.
The explicit `--use-lattice-aabb-workaround` mode instead computes this
overlap-preselection box from the original FLUKA cell mesh and records that
choice in `conversion_report.json`. It does not alter the converted solids or
remove lattice placements, but its output still requires geometry validation.
