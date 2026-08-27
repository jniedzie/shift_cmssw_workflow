# Run-3 LSS5 FLUKA integration into CMS-SHIFT

## Scope and non-negotiable provenance

The production model must describe the **Run-3 LSS5 around IP5, on the side
from which the SHIFT particles travel toward CMS**. It must cover the target
location used by the generator (currently `z = +148 m`) through the CMS cavern
interface and include the as-installed Run-3 configuration of:

- the LHC tunnel, CMS cavern, rock, shielding, and experiment-machine
  interface;
- beam pipes, vacuum equipment, collimators, absorbers, connection cryostats,
  interconnects, supports, and other material that can affect the cascade;
- all relevant LSS5 magnetic elements, their apertures, yokes/material, field
  maps or routines, fringe/scattered fields, polarities, and strengths;
- the correct Run-3 beam, optics, crossing configuration, and coordinate/time
  conventions.

The FASER/IP1 model is on the wrong side of the ring and is excluded. Phase-2,
HL-LHC, FACET/PREFACE, schematic, or newly reconstructed CAD models are not
acceptable substitutes for the Run-3 LSS5 production model. They may be used
only as software examples or comparison geometries and must be labelled as
such.

## Authoritative model trail

The public CMS beam-induced-background description from the BRIL Radiation
Simulation (RadSim) group identifies a detailed CMS/LSS5 FLUKA model. It states
that this model contains CMS subdetectors, LHC vacuum chambers and equipment,
the experiment-machine interface, LSS5 magnetic elements, and cavern
infrastructure. It also states that the field description includes aperture
fields, fields inside magnet material, and scattered fields. This is the model
family we need, but the paper's concrete example is a Phase-2 model (`v6.0`),
not evidence of a valid Run-3 deck.

The CMS Run-3 detector paper identifies `v5.0.0.0` as the baseline Run-3
detector/cavern geometry. It also identifies `v5.1.0.2` as including the new
forward shield expected in 2024. Run 3 is therefore not a single timeless
geometry: the requested validity interval must say whether the 2024 shield is
present. These tags do not by themselves establish the matching long LSS5
model, machine state, or correct side/direction. The exact paired revisions
must be confirmed by the BRIL RadSim model owners before any model is accepted.

The public BRIL RadSim page points CMS members to the internal BRIL SharePoint.
The initial contacts identified from the public documentation are Sophie
Mallows (BRIL RadSim) and Anastasiia Riabchikova (LSS5/BIB simulation; public
paper contact `Anastasiia.Riabchikova@cern.ch`).

Related FACET work demonstrates a FLUKA scoring-plane to HepMC2 to Geant4
workflow on the IP5 side. Its public converter and detector code are useful as
a precedent only. The corresponding FLUKA source files require CERN login,
refer to a plane around 100.9--103.66 m, and are not established as the
as-installed Run-3 LSS5 model required here.

## Exact asset request

Request a frozen, redistributable snapshot (or an access-controlled immutable
location) containing all of the following:

1. The Run-3 LSS5 master FLUKA input and every included input file for the
   physical IP5 side containing our target, spanning the target at about
   148 m to the CMS interface. Its mapping to the current CMSSW convention
   (`z = +148 m`, beam-B campaign) must be supplied or independently verified;
   the sign alone is not accepted as physical-side provenance.
2. The Flair project, geometry plots, and the commands/environment needed to
   preprocess, compile, link, and run the deck.
3. If generated with LineBuilder: its input card, prototype database, beam-pipe
   and tunnel profiles, additions/placement files, and the exact FLUKA Element
   Database revision.
4. The exact Run-3 TWISS/optics input, beam energy, crossing scheme, magnet
   strengths and polarities, and the validity interval (year/fill or other
   machine-state definition).
5. Every magnetic-field map and user routine, including fields in apertures,
   magnet material, and fringe/scattered-field regions, plus their coordinate
   transforms and units.
6. Material definitions, densities, production/transport thresholds, physics
   cards, biasing settings, source routines, and scoring routines.
7. The matching Run-3 CMS cavern/detector FLUKA model revision and the exact
   interface between the LSS5 and CMS models.
8. Reference results: geometry/material scans, field scans, scoring-plane
   particle spectra, and enough seeds/source events to reproduce them.
9. Provenance: model tag or commit, file checksums, authors, creation date,
   known limitations, and redistribution/licensing conditions.

### Draft request to the model owners

> We are integrating fixed-target particles produced around z=+148 m on the
> relevant LSS5/IP5 side into the CMS Run-3 CMSSW simulation. Could you provide
> or grant access to the frozen as-installed Run-3 LSS5 FLUKA model, including
> all LineBuilder/FEDB inputs, Run-3 optics, field maps/routines (including yoke
> and fringe fields), material/physics/source cards, the matching CMS cavern
> model and interface definition, and reference validation outputs? We need the
> model revision and validity interval explicitly recorded. Phase-2/HL-LHC or
> the IP1/FASER geometry cannot be used as substitutes.

## Integration architecture

### Baseline: native FLUKA LSS5, CMSSW from a boundary surface

Run the authoritative LSS5 model in native FLUKA and record every particle
crossing a closed or well-defined interface surface just before the existing
CMS Geant4 world. Convert those crossings into an event-preserving HepMC3 (or
an EDM source product) and begin the standard CMSSW simulation at that
surface.

This is the preferred first production baseline because it preserves the
validated LSS5 geometry, magnetic fields, materials, FLUKA physics, and biasing
without forcing them into CMSSW's DD4hep/Geant4 world. The interface record
must preserve, per primary event or bunch crossing:

- PDG identity, status, charge/mass convention, position, four-momentum, and
  absolute/relative time;
- event and primary identifiers, FLUKA run/history identifiers, generation,
  statistical weight, and last-interaction information when available;
- the coordinate transform, length/momentum/time units, surface normal and
  crossing direction;
- source normalization and enough metadata to reproduce the FLUKA job.

A plane can double-count recrossing particles or miss particles entering
through its sides. The surface/scoring implementation therefore needs an
explicit crossing rule and a deduplication identifier. Backscatter from the
CMSSW world into LSS5 is not represented by a one-way handoff and must be
measured as a validation systematic.

The public FACET converter must not be copied unchanged: the inspected version
sets all HepMC vertex times to zero, separates output by particle species, and
does not carry the complete FLUKA history/weight metadata required here.

### Secondary study: translate the LSS5 geometry into Geant4

A FLUKA-to-Geant4/GDML conversion (for example with `pyg4ometry`, or a future
FLUKA geometry interface) can be evaluated after the frozen source model is in
hand. Geometry conversion alone is insufficient: field routines/maps,
materials, regions/cuts, biasing, source logic, physics settings, and scoring
semantics all need an explicit implementation and validation. The converted
world also must be joined to the CMSSW DD4hep geometry without overlaps or
gaps.

This route is accepted for production only if it agrees with native FLUKA at
the interface and downstream reference surfaces. Until then it is a prototype,
not the authoritative model.

## Validation gates

No large sample production starts until all gates below pass for a small,
fixed-seed sample.

1. **Provenance gate:** every input is checksummed and tied to an approved
   Run-3 model revision and machine-state validity interval.
2. **Geometry gate:** longitudinal and transverse material scans, volume
   inventory, and overlap/gap checks reproduce the reference model around all
   high-impact components.
3. **Field gate:** signed `Bx`, `By`, and `Bz` scans along the reference orbit
   and off-axis points reproduce aperture, yoke, and fringe fields and confirm
   the correct side/polarities.
4. **Transport gate:** mono-particle tests for both charges and several
   momenta reproduce positions, directions, survival, energy loss, and timing
   at agreed scoring surfaces.
5. **Shower gate:** FLUKA-reference spectra and two-dimensional correlations
   (`x-y`, position-angle, momentum-angle, energy-time, species, multiplicity)
   agree at the CMSSW handoff surface, including statistical weights.
6. **Interface gate:** event multiplicity/correlation, total weight, units,
   timestamps, coordinate transform, and crossing direction survive the
   FLUKA-to-CMSSW conversion exactly.
7. **CMS gate:** the small sample completes the existing GEN-SIM through
   NanoAOD chain, and its detector-level distributions are compared with a
   native-FLUKA reference wherever one exists.

## Current status and next unblocker

- The authoritative organization and model family have been identified:
  CMS BRIL RadSim's CMS/LSS5 FLUKA model.
- Read access to the CERNBox/EOS `lhc-mib` project was obtained and its
  contents were inspected on 2026-08-27. The public record and this project
  establish the required model family and interface convention, but the frozen
  Run-3 LSS5 source deck and its exact version were not found.
- Public FACET and legacy FLUKA LineBuilder resources provide implementation
  clues, not an acceptable Run-3 source model.
- Implementation is intentionally blocked on obtaining the exact asset bundle
  above. Writing a converter against an unverified format now would bake in
  assumptions that can change event grouping, time, weights, geometry, and
  fields.

Once the bundle is available, the first executable deliverable will be a
native-FLUKA fixed-seed transport to a declared interface surface, plus a
lossless converter and CMSSW source test for one event before any geometry
translation is attempted.

## CERNBox/EOS inventory (2026-08-27)

The reader group `cernbox-project-lhc-mib-readers` exposes the project at
`/eos/project/l/lhc-mib`. The accessible material is useful, but it does not
contain the required Run-3 CMS/IP5 source model.

### Assets found

1. `/eos/project/l/lhc-mib/public/HL-LHC/CMS/local-beam-gas/FLUKA`
   contains compressed FLUKA particle-crossing records from CMS local
   beam-gas simulations. They are scored at the CMS cavern interface, 21.5 m
   from IP5. Their text schema includes FLUKA run and primary-event IDs,
   particle ID, kinetic energy, statistical weight, transverse position,
   transverse direction cosines, particle age, and the original collision
   position. The directory contains output records and normalization notes,
   not the geometry/source deck that produced them.
2. `/eos/project/l/lhc-mib/public/HL-LHC/CMS/beam-halo` contains analogous
   CMS interface records for horizontal and vertical TCT losses with two jaw
   material choices. These are also HL-LHC output products, not a source
   model for arbitrary particles produced at the SHIFT target.
3. `/eos/project/l/lhc-mib/public/HL-LHC/CMS/local-beam-gas/pressure_profile`
   contains an HL-LHC LSS5 ramp-up gas-pressure profile spanning approximately
   -256 m to +256 m. It covers the 148 m longitudinal region but supplies no
   tunnel geometry or magnetic field.
4. `/eos/project/l/lhc-mib/press148m_4/secTest` is a complete-looking legacy
   FLUKA working area: LineBuilder-generated input, magnet data, `bmagfld.f`,
   beam-gas source and scoring routines, Makefiles, executable, and example
   outputs. The deck explicitly identifies `LHC/IR1`, its production names are
   `lhc_ir1_exp_b2`, and its header identifies LineBuilder revision 432 from
   `trunk`. It is the old ATLAS/IP1 148 m pressure-bump setup, not CMS/IP5.
5. The remaining `press148m*`, `press58m*`, `bbgen`, and `offmom` areas are
   predominantly IR1 or older Run-1/Run-2 production results. The accessible
   `public/LHC-Run3` directory contains ALICE and LHCb material only; there is
   no CMS subdirectory.

The project's historical pressure-bump log and stored job scripts refer to
`/afs/cern.ch/project/lhc_mib` for source inputs. That legacy AFS project exists
but was not readable with the EOS reader entitlement. It may contain additional
source material or may only be the old IR1 production area; this must be
confirmed by its owners.

### What the accessible assets cannot establish

None of the inspected assets establishes a Run-3 SHIFT transport efficiency.
In particular:

- the HL-LHC CMS files contain already-transported MIB particles, so they
  cannot transport arbitrary SHIFT particles starting near 148 m;
- the HL-LHC pressure profile is not a material or field description;
- IR1 and IR5 have different machine layouts, fields, experiment interfaces,
  and coordinate conventions, so the IR1 deck is not a physical proxy or a
  conservative uncertainty bound for CMS;
- joining the IR1 deck to the CMS HL-LHC records would not represent any real
  LHC configuration.

## Best bounded work with the available assets

Until an IR5 source deck is obtained, use the CMS output and the IR1 source
deck as two independent software fixtures:

1. **Downstream interface prototype from CMS records.** Implement and test a
   strict parser and lossless intermediate representation for the CMS
   particle-crossing schema. Preserve event grouping, FLUKA identity,
   statistical weight, position, direction, time, and provenance through a
   HepMC3 or EDM conversion. Coordinate transforms, the fixed scoring-plane
   position, longitudinal direction, and particle-ID mapping must remain
   explicit and unconfirmed until documented by the model owner. Synthetic
   records can test the CMSSW source without presenting an HL-LHC sample as a
   Run-3 SHIFT sample.
2. **Upstream FLUKA harness from the IR1 deck.** Use the legacy complete deck
   only to learn/reproduce the build, mono-particle source, magnetic-field
   callback, boundary scoring, fixed-seed execution, and output bookkeeping.
   A useful test injects both muon charges at several momenta and verifies that
   event IDs, times, weights, and crossings survive the scoring pipeline. Its
   trajectories and survival fractions have no CMS/SHIFT physics meaning.
3. **Keep the current CMSSW-only transport as a declared no-machine-material
   reference.** It is not a realistic upper bound once fields, apertures, and
   secondary production matter, and it must not be combined with the IR1
   result as an uncertainty envelope.

If an exact Run-3 deck remains unavailable, fallback source models should be
requested in this order: a dated pre-HL Run-3 IR5 deck; the closest documented
Run-2 IR5 deck; then an HL-LHC IR5 deck. An HL-LHC IR5 source model has the
correct interaction region and is therefore a better provisional engineering
fixture than IR1, but any transport result from it must be labelled as a model
sensitivity study, not Run-3 detector acceptance. Before using any fallback,
inventory the Run-3 differences in magnets, apertures, absorbers/shielding,
optics, crossing setup, cavern interface, and coordinate conventions.

## Short request to colleagues

**Subject: Missing Run-3 CMS/LSS5 FLUKA source model for SHIFT simulation**

> Hi,
>
> We now have access to `/eos/project/l/lhc-mib` and have inventoried its
> contents. We found HL-LHC CMS/IP5 FLUKA particle-crossing outputs for local
> beam-gas and beam-halo simulations at the CMS cavern interface (21.5 m from
> IP5), plus an HL-LHC LSS5 pressure profile. These are output products; the
> underlying geometry/source deck is not included. We also found a complete
> legacy 148 m FLUKA bundle with LineBuilder geometry, magnetic-field and
> source/scoring routines, but it is explicitly the IR1/ATLAS Beam-2 model.
> The public Run-3 area contains only ALICE and LHCb material.
>
> For SHIFT we need the frozen as-installed Run-3 IR5/LSS5 FLUKA model for the
> physical side connecting our target around 148 m to CMS: master deck and
> includes, LineBuilder/FEDB inputs, field maps/routines, Run-3 optics and
> magnet settings, coordinate conventions, matching CMS cavern revision and
> interface definition, and reference validation scans.
>
> Does this source bundle exist under the legacy
> `/afs/cern.ch/project/lhc_mib` area or elsewhere? If the exact Run-3 model is
> unavailable, a dated Run-2 IR5 or HL-LHC IR5 source deck would also help as
> an explicitly provisional engineering model.
>
> Thanks!

## Public references

- [CMS beam-induced background simulations and LSS5 model description](https://cds.cern.ch/record/2816679/files/document.pdf)
- [Development of the CMS detector for LHC Run 3, including Run-3 FLUKA tags](https://arxiv.org/abs/2309.05466)
- [CMS BRIL Radiation Simulation public page and internal access route](https://twiki.cern.ch/twiki/bin/view/CMSPublic/BRILRadiationSimulation)
- [FLUKA LineBuilder](https://twiki.cern.ch/twiki/bin/view/FlukaTeam/FlukaLineBuilder)
- [FLUKA Element Database](https://twiki.cern.ch/twiki/bin/view/FlukaTeam/FlukaElementDataBase)
- [Public FACET Geant4 repository](https://gitlab.cern.ch/ssaariok/facet)
- [FACET Geant4 presentation describing its FLUKA input](https://indico.cern.ch/event/1279897/contributions/5377837/attachments/2640448/4569325/Geant4_FACET_3_5_2023.pdf)
- [`pyg4ometry` FLUKA-to-Geant4 conversion documentation](https://pyg4ometry.readthedocs.io/en/stable/manual/converting.html)
