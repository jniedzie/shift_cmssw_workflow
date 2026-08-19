# CMS+SHIFT event timing and collision-overlay plan

Last updated: 2026-08-18

## Fixed Run 3 constraints

The purpose of this study is to determine SHIFT-muon acceptance and
reconstruction performance under the detector and trigger system that CMS
actually operated in Run 3. Electronics integration windows, BX assignment,
buffers/readout, trigger rules, prescales, deadtime, and rate limits are fixed
constraints. They must be taken from authoritative configurations,
documentation, or data and reproduced unchanged. They must never be retimed,
loosened, or tuned to improve SHIFT performance. The variables in this workflow
control the physical SHIFT timing, simulation transport completeness, pileup
occupancy, and empirical sampling—not the CMS hardware or trigger rules.

## Scope and verified starting point

This plan treats detector acceptance as a complete timing funnel, not only as
an offline-reconstruction problem:

```text
physical crossing
  -> SimHit and time
  -> digi and BX/TDC
  -> RecHit
  -> segment
  -> trigger primitive and BX
  -> regional muon candidate
  -> uGMT/L1 decision
  -> RAW content
  -> HLT acceptance
  -> offline SHIFT track
```

The local workflow checkout was on `main` at `6e0caad` and had a pre-existing,
unrelated edit in `config/workflow.env`.  The CMSSW checkout was on
`shift-muon-segments-pre4` at `5aca14f3f61` and was clean before this work.
The analysis checkout is context only and is not changed in Milestone 1.

Inspection of the generated Step-1 configuration establishes the actual chain
as Pythia `generator:unsmeared` -> standard `VtxSmeared` ->
`generatorSmeared` -> `g4SimHits`.  Step 1 is `GEN,SIM`.  Step 2 is currently
`DIGI:pdigi_valid,L1,DIGI2RAW,HLT:@relval2024,ENDJOB` with `mixNoPU`; it has no
central-collision pileup overlay.  Both fixed-target fragments previously set
`Beams:offsetVertexZ = 148000.` mm and `Beams:offsetTime = -148000.` mm/c.

HepMC vertex positions use mm and the fourth coordinate uses mm/c.  The local
Geant4 conversion in `SimG4Core/Generators/src/Generator.cc` maps a HepMC
vertex time `t` to `t * mm / c_light`, so a common shift of 299.792458 mm/c is
one ns at the Geant4 primary vertex.

The standard `g4SimHits` configuration also sets `MaxTrackTime = 500 ns` in
`Physics`, `StackingAction` and `SteppingAction` (and 2000 ns in the forward
region).  `SteppingAction` compares a track's absolute Geant4 global time with
that limit.  A muon generated at +148 m with time zero reaches CMS after about
494 ns and can therefore be killed almost immediately in the central detector.
Milestone 1 explicitly raises this CPU-protection transport guard to a
configurable 5000 ns by default.  This is not an electronics/readout window;
later milestones must retain the real subsystem timing selections.

## Central-collision and trigger-timeline architecture

The ordinary CMS environment is not represented by one minimum-bias event per
25 ns.  The implementation is split into three explicit layers:

1. **Interaction library:** use the centrally produced CMS inclusive-inelastic
   13.6 TeV pp GEN-SIM dataset rather than generating it locally.  Its events
   are reusable interaction primitives.
2. **Detector-time window:** for every filled, colliding BX in the selected
   window, draw a luminosity-dependent Poisson number of library interactions
   and mix their SimHits with the SHIFT signal before digitization.  Empty and
   non-colliding slots contribute no pp interactions.  The filling-scheme
   mask, orbit/BX anchor and per-bunch means are versioned campaign inputs.
3. **Trigger timeline:** construct a sequence of candidate CMS readout BXs,
   run the L1/HLT emulation on the combined detector content for each candidate
   reference, and then reproduce the authoritative Run 3 trigger deadtime/rate
   rules unchanged on the ordered decisions. This layer answers whether an ordinary CMS trigger reads a
   fraction of a delayed SHIFT signal and whether accepted CMS and SHIFT
   triggers block one another.

CMSSW `MixingModule` supplies the second layer's SimHit/digitizer integration
and robust `EncodedEventId` separation, but its stock Poisson OOT mode uses the
same distribution at every BX in `minBunch..maxBunch`.  It neither represents
an arbitrary filling mask nor makes an independent L1/HLT decision for every
overlaid collision.  Therefore a uniform OOT pileup configuration is only a
controlled occupancy test, not the final physical or trigger-rate model.

The reference convention is that the nominal SHIFT collision occurs about
490 ns before the same bunch reaches CMS.  Thus a central pp collision at
mixing BX 0 is near the nominal SHIFT-muon arrival at CMS, while central BXs
near -20 occur around the target collision.  The exact phase and integer BX
anchor must be derived from the source location, beam direction and selected
filling scheme rather than hardcoded as 20.

## Timing definitions

- `nominal` is the deterministic fixed-target baseline.  For source coordinate
  `z_source`, CMS reference `z_CMS`, and beam direction `d_z = +/-1`, the
  producer adds `Delta(ct) = d_z * (z_source - z_CMS)`.  For the present +148 m
  target and inward beam (`d_z=-1`), this is approximately -148000 mm/c.  It is
  derived event by event from the generated, spatially smeared source.
- `legacy` adds exactly -148000 mm/c by default.  This is the regression point
  for the removed Pythia setting and intentionally does not compensate the
  event-by-event source-z spread.
- `fixed` adds a configured offset in ns and is intended for controlled tests.
- In every mode, `BX offset * 25 ns + fractional phase` is additive.  A timing
  acceptance scan is a grid of explicit BX offsets and phases; it is not a
  physical arrival-time distribution.
- The eventual physical distribution must condition on a bunch passage,
  filling scheme, source/collimator position, beam direction, generated
  production and decay vertices, path length and beta, and the CMS trigger BX.
  Fixed-target and collimator/beam-halo sources will have separate models.
- CMSSW simulation can test timing propagation and configured electronics
  models.  Readout-gate fidelity, firmware BX behavior, real noise/occupancy,
  event-building constraints and data-taking trigger behavior require
  comparison with CMS data and review by subsystem/firmware experts.

## Milestone 1 - explicit event time and reproducible baseline

**Status:** complete

**Objective:** Move timing policy out of the Pythia fragments and establish a
single configurable, provenance-bearing common event-time shift after
generation and standard vertex smearing but before Geant4.

**Repositories and likely files:**

- `cmssw`: new `IOMC/ShiftEventTiming` producer, Python configuration and
  customization.
- `shift_cmssw_workflow`: both fixed-target fragments,
  `config/workflow.env`, `run_step1_generation.sh`, and this plan.
- `tea_shift_cmssw`: no change.

**Implementation outline:** Clone the smeared HepMC event; derive the nominal
shift from the actual source z and configured beam direction; add optional BX,
phase or fixed shifts; alter only every vertex's fourth coordinate by the same
amount; route `generatorSmeared.currentTag` to the result; retain per-event
source coordinates, mode, beam direction, BX, phase, applied shift and model
version as EDM products.  Keep a `legacy` mode for the old -148000 mm/c
convention.  Configure the Geant4 physics/stacking/stepping maximum track times
well outside the intended timing scan, and use a separately configurable
Geant4 seed so paired timing samples share the same transport history.

**Required inputs:** source/target geometry, beam direction, 25 ns bunch
spacing, CMS z reference, timing mode and scan offsets, maximum scan extent and
fixed generator/Geant4 seeds for paired validation.

**Outputs and diagnostics:** shifted HepMC product; timing metadata products;
one concise `ShiftEventTiming` log record per event; generated cmsDriver
configuration snapshot; Geant4 primary debug time and muon-system PSimHit
times for focused tests.

**Validation criteria:** affected packages build; nominal, fractional-phase
and integer-BX samples run; all HepMC spatial coordinates, momenta and relative
vertex times are invariant; every absolute vertex time changes by the declared
common shift; Geant4 primary and CSC/RPC/GEM/DT PSimHit times move by the
expected amount where the event crosses those systems; legacy mode agrees with
the former Pythia offset within floating-point precision.  This milestone does
not validate digitization, readout or trigger acceptance.

**Dependencies on later work:** Defines the stable time/provenance interface
used by every following milestone.  Event-by-event physical sampling is
deliberately deferred.

### Milestone 1 validation record

Release: `CMSSW_17_0_0_pre4`; CMSSW branch:
`shift-muon-segments-pre4`; workflow branch: `main`; geometry: `DB:Extended`;
era: `Run3_2024`; conditions: `auto:phase1_2024_realistic`; beam spot:
`Realistic25ns13p6TeVEarly2023Collision`.

Exact build and sample commands, seeds, output paths and quantitative results
used for this focused validation were:

```bash
cd /afs/cern.ch/work/j/jniedzie/private/shift_cmssw/CMSSW_17_0_0_pre4
source /cvmfs/cms.cern.ch/cmsset_default.sh
eval "$(scram runtime -sh)"
scram b -j 8 IOMC/ShiftEventTiming
edmPluginRefresh lib/el9_amd64_gcc13

cd ../shift_cmssw_workflow
SAMPLE_DIR=/tmp/shift_timing_validation_v2/nominal \
  GENERATOR_SEED=24681357 SIMULATION_SEED=97531 CMSSW_PREPARED=1 \
  KEEP_STEP1_TMP=1 ./run_step1_generation.sh --force 0 1
SAMPLE_DIR=/tmp/shift_timing_validation_v2/phase7p5 \
  GENERATOR_SEED=24681357 SIMULATION_SEED=97531 CMSSW_PREPARED=1 \
  SHIFT_TIMING_PHASE_NS=7.5 KEEP_STEP1_TMP=1 \
  ./run_step1_generation.sh --force 0 1
SAMPLE_DIR=/tmp/shift_timing_validation_v2/bx1 \
  GENERATOR_SEED=24681357 SIMULATION_SEED=97531 CMSSW_PREPARED=1 \
  SHIFT_TIMING_BX_OFFSET=1 KEEP_STEP1_TMP=1 \
  ./run_step1_generation.sh --force 0 1
```

Inside the restricted development sandbox, ordinary `cmsRun` exited 139 before
its normal logging.  The same generated configurations ran to normal exit
under `gdb -batch -ex run`; the three exact temporary configurations were:

```bash
gdb -batch -ex run --args cmsRun \
  /tmp/shift_cmssw_step1_DbOkT4/events_step1_part0000_cfg.py
gdb -batch -ex run --args cmsRun \
  /tmp/shift_cmssw_step1_Vt3yY3/events_step1_part0000_cfg.py
gdb -batch -ex run --args cmsRun \
  /tmp/shift_cmssw_step1_XsRRgu/events_step1_part0000_cfg.py
```

All three exited normally under the debugger and produced one-event ROOT
files.  The resolved configuration has `shiftEventTime+generatorSmeared` at the
head of `pgen`, routes `generatorSmeared.currentTag` to `shiftEventTime`, fixes
the `g4SimHits` seed to 97531, and sets Physics/Stacking/Stepping central and
forward maximum track times to 5000 ns.

An ordinary unsandboxed run then demonstrated that exit 139 was an execution-
sandbox artifact rather than a CMSSW defect.  The complete wrapper ran without
the debugger, staged a readable 187065-byte one-event ROOT file and archived
its configuration and log:

```bash
SAMPLE_DIR=/tmp/shift_timing_validation_v2/wrapper_nominal \
  GENERATOR_SEED=24681357 SIMULATION_SEED=97531 CMSSW_PREPARED=1 \
  ./run_step1_generation.sh --force 0 1
edmFileUtil \
  /tmp/shift_timing_validation_v2/wrapper_nominal/samples/step1/events_step1_part0000.root
```

The quantitative comparison command was:

```bash
python3 scripts/validate_shift_timing.py \
  /tmp/shift_cmssw_step1_DbOkT4/events_step1_part0000.root \
  /tmp/shift_cmssw_step1_Vt3yY3/events_step1_part0000.root \
  /tmp/shift_cmssw_step1_XsRRgu/events_step1_part0000.root \
  --phase-ns 7.5 --bx-ns 25.0
```

For the nominal event, the post-smearing source was z=146935.839973925 mm and
ct=36.234721184 mm before timing; the producer applied -146935.839973925 mm/c
(-490.125205131 ns).  A debug run showed both muons accepted as Geant4
primaries at -490.004 ns.  Relative to nominal, the phase sample shifted every
HepMC vertex by exactly 2248.443435 mm/c and the BX sample by exactly
7494.811450 mm/c.  Spatial vertices and all particle momenta were bitwise
unchanged.

A deliberately nonphysical `timingMode=fixed`, zero-offset control tested the
transport guard independently of nominal timing.  Both muons entered Geant4 at
0.121 ns from z=146935.840 mm and traversed CMS to the -450 m world boundary,
ending normally at 1995.873 ns and 1991.925 ns.  They were therefore not killed
at the former 500 ns central limit.

With both random seeds fixed, the same muon PSimHits and positions were present
at all three timing points: 90 CSC, 16 RPC and 28 DT hits.  Their times shifted
by 7.5 ns and 25 ns with maximum float residuals of 1.91e-6 ns.  This event did
not geometrically cross an active GEM volume, so GEM timing is not yet directly
validated.

For the legacy regression, a separate one-event GEN control reinserted
`Beams:offsetTime = -148000.` only in the generated test configuration.  Its
`generatorSmeared` HepMC event and the new `timingMode=legacy` event each had 35
vertices and 73 particles; every vertex x/y/z/t and particle four-momentum was
identical (maximum difference 0).

Milestone 1 is **complete**.  The chosen event did not cross GEM, so a later
geometry-crossing sample must provide GEM-specific timing evidence before a GEM
acceptance claim.  No digitization, trigger, RAW, HLT or reconstruction claim
is made from these GEN-SIM tests.

## Milestone 2 - physical bunch/source timing model

**Status:** not started

**Objective:** Sample physically allowed SHIFT production times relative to a
selected CMS trigger/readout BX, separately for fixed-target and
collimator/beam-halo sources.

**Repositories and likely files:** `IOMC/ShiftEventTiming` model classes and
tests; workflow timing-model data/configuration under `config/` and `data/`;
campaign documentation.

**Implementation outline:** Define the bunch passage at each source from LHC
beam direction and orbit/filling data; combine it with the generated source and
decay positions, particle beta and path length; choose and record the reference
CMS BX.  Materialize the corresponding central-collision window as orbit BX,
beam-1/beam-2 occupancy, collision mask and per-BX mean interactions.  Use
separate versioned parameterizations for fixed target and collimator/halo.

**Required inputs:** surveyed source coordinates, beam/orbit convention,
filling schemes, trigger-reference definition and generator truth.

**Outputs and diagnostics:** sampled time and all terms entering it, model
version, source class, reference BX, versioned BX-window manifest and
distribution-comparison plots.

**Validation criteria:** analytic limiting cases and deterministic seeds agree;
generated distributions reproduce independently computed bunch-arrival times;
no unrecorded constants remain.

**Dependencies on later work:** Supplies realistic weights/distributions to
Milestones 4-12; it does not replace the controlled scan in Milestone 3.

## Milestone 3 - controlled timing/BX electronics scan

**Status:** not started

**Objective:** Map acceptance as a function of integer BX and intra-BX phase,
including station-to-station timing differences for traversing muons.

**Repositories and likely files:** workflow scan matrix/submission scripts;
CMSSW timing-funnel analyzers; later analysis plotting configuration.

**Implementation outline:** Produce a bounded grid of BX offsets and fractional
phases from identical generator seeds, then run the same event through each
electronics configuration.  Keep the uniform scan explicitly distinct from
the physical timing model and reweight only after response maps exist.

**Required inputs:** Milestone 1 timing controls, detector-specific readout
ranges and a representative geometry-crossing sample.

**Outputs and diagnostics:** per-stage efficiency/response maps versus BX and
phase, split by subsystem/station and source topology.

**Validation criteria:** paired samples have identical truth/geometry;
configured shifts are recovered at SimHit level; scan coverage includes at
least one full relevant readout window on either side.

**Dependencies on later work:** Provides the response grid for Milestones 5-8
and convolution with Milestone 2.

## Milestone 4 - central pp and out-of-time pileup before digitization

**Status:** in progress

**Objective:** Overlay central pp collisions, including out-of-time pileup,
before detector digitization without losing SHIFT truth identity.

**Repositories and likely files:** Step-2 cmsDriver/mixing configuration,
pileup dataset/configuration and campaign metadata in the workflow; CMSSW
mixing/truth customizations only if standard facilities are insufficient.

**Implementation outline:** Begin with the conditions-compatible central CMS
interaction library and standard RunIII2024Summer24 `MixingModule` profile
before DIGI.  Keep the no-pileup control and paired seeds, and record pileup
provenance and `EncodedEventId` assignments.  After the standard path is
validated, use Milestone 2's BX-window manifest to determine whether any
fill-aware extension is actually required; do not replace standard mixing
facilities speculatively.  Generate one combined detector event for each
candidate readout BX needed by the later trigger timeline, with the SHIFT
signal shifted consistently relative to that reference.

**Required inputs:** compatible minimum-bias GEN-SIM, pileup profile, in/out-of-
time BX range, GlobalTag/geometry and storage/CPU estimates.

**Outputs and diagnostics:** mixed GEN-SIM-DIGI-RAW, pileup summaries,
occupancy by BX/subdetector and truth-event identifiers.

**Validation criteria:** known pileup multiplicity/profile is reproduced;
signal and pileup SimHits are separable by encoded event identity; zero-pileup
output is a stable control.

**Dependencies on later work:** Required for occupancy conclusions in
Milestones 5-10, but not for Milestone 1 timing validation.

**Current implementation:** DAS identifies the production dataset
`/MinBias_TuneCP5_13p6TeV-pythia8/RunIII2024Summer24GS-140X_mcRun3_2024_realistic_v20-v1/GEN-SIM`
(2,499,014,000 events in 43,087 files).  Step 2 now has an opt-in
`run3_2024` mode using CMSSW scenario
`2024_25ns_RunIII2024Summer24_PoissonOOTPU`, an explicit mixing seed and a
submit-host-generated file manifest.  The no-pileup mode remains the default.
The production dataset is currently catalogued but tape-only: DAS/Rucio found
no active file replicas, and a representative file failed through the CERN,
Italian and global XRootD fallbacks.  Rucio rule
`bcd7943660744e5abec93117af3c920e` now requests one 11.374 GB file at
`T2_CH_CERN` with a 14-day lifetime (expiry 2026-09-01).  The rule is
`WAITING_APPROVAL`, with zero locks transferring, so a physics-valid pilot
must wait for approval and successful staging.

A mechanism-only one-event test therefore used one disk-resident official
13.6 TeV Run-3 minimum-bias file from
`Run3Winter25GS-Winter25PU_correctBS_142X_mcRun3_2025_realistic_v4-v1` while
retaining the 2024 pileup profile.  This is not a physics-production input.
With mixing seed 86420, Step 2 ran DIGI, L1, DIGI2RAW and
`HLT:@relval2024`, producing a readable 25,549,795-byte one-event file.  The
realized OOT window was BX -12 through +3 with 40-69 interactions per BX and
61 interactions at BX 0 (`trueNumInteractions=54.8763`).  The output contains
`PileupSummaryInfo`, `CrossingFramePlaybackInfoNew`, L1 products and HLT
`TriggerResults`; six of eight scheduled paths accepted, including the
digitization/L1/RAW paths and Physics, Random and ZeroBias HLT paths.

The original SHIFT `SimTrack`s remain identifiable as encoded event `(0,0)`;
pileup truth uses nonzero event numbers across all mixed BXs.  Standard
`mix:MergedTrackTruth` contained 172,388 pileup `TrackingParticle`s but no
SHIFT `(0,0)` particle, so dedicated SHIFT truth association is still needed
before efficiency claims.  Step 3 consumed the mixed output successfully and
published a readable 22,127,081-byte one-event AODSIM file.  These results
validate the workflow mechanism and downstream compatibility only, not the
2024 physics sample, trigger rates, electronics acceptance or reconstruction
efficiency.

### ZeroBias trigger-proxy seed

The first trigger-proxy component is implemented in
`scripts/zero_bias_unpack_cfg.py`,
`scripts/extract_zero_bias_trigger_bits.py` and
`scripts/run_zero_bias_trigger_extract.sh`.  The first script runs only the
standard Stage-2 uGT RAW unpacker; it does not re-emulate L1.  The extractor
writes streaming JSON Lines with complete correlated uGT algorithm decision
sets before prescales, after prescales and after masks for every stored uGT BX,
plus external bits, final-OR flags, prescale column, menu/firmware UUIDs,
run/lumi/event/orbit/BX, the HLT menu and all accepted HLT paths.  It stores one
HLT menu record per distinct ordered path list instead of repeating hundreds
of path names in every event.

An end-to-end 100-event test used the disk-resident Run2024G file
`/store/data/Run2024G/ZeroBias/RAW/v1/000/383/812/00000/a6d74641-5c7f-46f6-ae57-e8e7e4416ee1.root`
from run 383812, lumi 161.  Its collider menu has 837 HLT paths.  The sample
contained 48 distinct BX-0 initial/post-prescale L1 vectors, six distinct final
vectors and 91 events with final OR; the JSONL output was 412930 bytes.  This
validates extraction and preservation of correlations, not trigger
probabilities: 100 events from one lumi are far too few for rate estimates.

A previous technical test on run 386472 was rejected as a probability input
because it exposed only a small cosmics/random menu.  Campaign preparation
must select certified collision runs/lumis, group samples by compatible menu
and prescale column, and retain luminosity/pileup and bunch-slot metadata.
Events in an EDM file are not assumed to be time ordered.

This component deliberately stops before stochastic timeline generation.  The
next layer will draw one whole correlated decision record for each simulated
colliding BX, conditioned on the relevant run/lumi state, then apply an
explicit prescale/deadtime model in increasing orbit/BX order.  It must not
sample individual trigger paths independently, and it must keep a distinction
between a raw algorithm condition, an L1A/readout, an HLT acceptance and final
dataset storage.

On 2026-08-19 the next trigger-proxy layer was implemented.  The shared
`zero_bias_trigger_library.py` module and
`validate_zero_bias_trigger_library.py` now reject duplicate events,
inconsistent HLT menu references, malformed bit sets, and violations of the
expected initial -> post-prescale -> final subset relation.  Events are grouped
by the complete currently available conditions key: HLT menu hash, L1 menu
UUID, L1 firmware UUID and prescale column.  Summaries include marginal L1/HLT
counts and correlated HLT accept pairs.  They warn that fill,
luminosity/pileup and colliding-bunch metadata are not yet present.

`sample_zero_bias_trigger_timeline.py` samples complete event records with a
fixed seed onto an inclusive relative-BX range.  An optional colliding-BX file
leaves empty/noncolliding slots unsampled.  Every sampled record retains its
source file/line and run/lumi/event/orbit/BX.  The output explicitly sets
`deadtime_applied=false` and `readout_after_trigger_rules=null`; this is a
candidate-decision timeline, not yet a trigger-rule result.

The standard conditions-resolved `L1uGTTreeProducer` is used by
`zero_bias_l1_menu_cfg.py`; `extract_zero_bias_l1_menu.py` converts its ROOT
aliases into a bit-to-name JSON mapping.  For run 383812 with
`auto:run3_data_prompt`, 408 algorithm names were resolved.  The data payload
UUIDs (`d10cf9fc`, firmware `e4cb66da`) exactly match the only group in the
100-event library.  Named final counts were 53 `L1_ZeroBias`, 53
`L1_ZeroBias_copy`, 11 `L1_FirstBunchAfterTrain`, eight
`L1_LastCollisionInTrain`, eight `L1_FirstCollisionInTrain`, and 11
`L1_FirstCollisionInOrbit`.  Supplying a mapping with nonmatching UUIDs is a
validation error.

The real-data validator reported 100 events, one HLT menu, one trigger group,
zero errors and one expected conditioning-metadata warning.  A fixed-seed
test sampled 30 complete records onto BX -24 through +5.  Four unit/integration
tests cover duplicates, invalid L1 stage nesting, deterministic whole-record
sampling, menu-name propagation and colliding/empty BX handling.  These tests
do not establish trigger probabilities or implement deadtime.

## Milestone 5 - SimHit-to-digi signal survival

**Status:** not started

**Objective:** Measure whether each truth-associated SHIFT SimHit survives
digitization, thresholds and zero suppression in DT/CSC/RPC/GEM.

**Repositories and likely files:** CMSSW timing-funnel analyzer and subsystem
association helpers; workflow diagnostic output commands; downstream plotting
after schema stabilization.

**Implementation outline:** Match SHIFT SimHits using encoded event/track
identity, then associate them to channel digis while retaining time, BX/TDC,
station and channel.  Measure loss causes separately from geometrical misses.

**Required inputs:** Milestones 3-4 samples, subsystem digitizer configuration
and channel mapping.

**Outputs and diagnostics:** survival efficiencies, lost-signal reason bins,
occupancy and timing residuals per subsystem/station/BX.

**Validation criteria:** closure on no-noise single-muon tests; no pileup digi
is counted as signal solely through PDG or generator index; denominators are
truth crossings in active sensitive regions.

**Dependencies on later work:** Establishes the maximum possible primitive and
reconstruction efficiency.

## Milestone 6 - local muon trigger primitives and BX assignment

**Status:** not started

**Objective:** Quantify DT, CSC, RPC and GEM primitive formation, quality and BX
assignment for SHIFT signals distributed across neighbouring BXs.

**Repositories and likely files:** subsystem primitive analyzers and emulator
configuration; workflow output/scan settings.

**Implementation outline:** Trace matched signal digis into DT/CSC/RPC/GEM
trigger primitives; retain constituent links where available; compare assigned
BX with physical station crossing time and inspect cross-BX station patterns.

**Required inputs:** Milestone 5 associations, emulator menus/configuration and
firmware-expert definitions of expected timing windows.

**Outputs and diagnostics:** primitive efficiency/quality/BX matrices by
station, phase and occupancy.

**Validation criteria:** standard collision-muon controls reproduce reference
behavior; SHIFT primitive losses are attributable to input digi loss or
primitive logic; simulation limitations are documented with expert review.

**Dependencies on later work:** Feeds regional/global trigger evaluation.

## Milestone 7 - BMTF/OMTF/EMTF, uGMT and L1 decision efficiency

**Status:** not started

**Objective:** Follow subsystem primitives through regional track finders,
uGMT and the final L1 decision across BXs.

**Repositories and likely files:** L1 emulator diagnostic analyzers, menu
configuration and workflow output content.

**Implementation outline:** Match primitive groups to regional candidates and
uGMT muons; inspect BX, quality, pT and charge; evaluate existing algorithms
and a small justified menu study without claiming hardware behavior beyond the
validated emulator.

**Required inputs:** Milestone 6 results, exact L1 menu/emulator versions and
expert guidance.

**Outputs and diagnostics:** stage-by-stage efficiency/purity/fake rate and L1
algorithm acceptance versus timing/source/occupancy.

**Validation criteria:** event-level object/decision bookkeeping closes;
collision controls agree with references; discrepancies between emulator and
firmware expectations are explicitly flagged.

**Dependencies on later work:** Defines which events can enter RAW/HLT studies.

## Milestone 8 - RAW readout windows and HLT retention

**Status:** not started

**Objective:** Determine whether in-time and neighbouring-BX detector content
is retained in RAW/event building and whether HLT paths keep the event.

**Repositories and likely files:** DIGI2RAW/RAW2DIGI and HLT configurations,
RAW content analyzers and workflow stage controls.

**Implementation outline:** Compare pre-packing digis with unpacked RAW digis;
inventory FED/subsystem time windows; run the exact HLT menu with per-path and
per-module tracing; distinguish L1 seeding loss, RAW loss and HLT reconstruction
loss.

**Required inputs:** Milestone 7 accepted events, conditions-compatible HLT
menu, readout-window definitions and data/firmware expertise.

**Outputs and diagnostics:** packing/unpacking closure, FED presence, HLT path
acceptance and first-failing module versus timing.

**Validation criteria:** byte/content closure where expected; every rejected
event has an identified stage; simulation-only assumptions are validated
against data or explicitly left unverified.

**Dependencies on later work:** Determines the physically recordable sample
available to offline reconstruction.

## Milestone 9 - offline SHIFT reconstruction under occupancy

**Status:** not started

**Objective:** Measure SHIFT track and dimuon performance after the full
electronics/readout chain in central-collision and out-of-time occupancy.

**Repositories and likely files:** existing `PhysicsTools/ShiftMuonSegments`
configuration/diagnostics, Step 3/4 workflow settings, then analysis plotting.

**Implementation outline:** Hold reconstruction fixed initially; compare zero-
pileup and paired pileup samples; measure efficiency, purity, fake rate,
momentum/direction/vertex resolution and dimuon observables by timing/topology.

**Required inputs:** retained events from Milestone 8 and robust truth matching
from Milestone 10.

**Outputs and diagnostics:** reconstruction response and failure-stage plots,
split by occupancy, BX, subsystem coverage and source class.

**Validation criteria:** no empirical calibration or generic event skipping
masks a detector/electronics loss; paired-sample changes are statistically
quantified; ROOT outputs pass semantic checks.

**Dependencies on later work:** Uses all preceding detector-chain results;
reconstruction redesign, if justified, is a separate reviewable follow-up.

## Milestone 10 - SHIFT-versus-pileup truth association

**Status:** not started

**Objective:** Provide a common truth identity from mixed SimHits through
offline objects, robust against pileup muons and repeated generator indices.

**Repositories and likely files:** CMSSW association data products/analyzers,
output commands and downstream event/object readers.

**Implementation outline:** Use `EncodedEventId` (or a demonstrated equivalent)
plus SimTrack/TrackingParticle identity; propagate association quality and
ambiguity at every funnel stage.  Never identify signal by generator index or
PDG ID alone.

**Required inputs:** mixing provenance, signal event-id convention and object-
to-hit/primitive constituent access.

**Outputs and diagnostics:** stable signal/pileup labels, association scores,
ambiguity flags and closure tables.

**Validation criteria:** injected pileup muons cannot be mislabeled as SHIFT in
unit/integration tests; efficiencies and fake rates use disjoint, auditable
truth categories.

**Dependencies on later work:** Design begins with Milestone 4 and is required
for quantitative Milestones 5-9.

## Milestone 11 - beam-halo and unpaired-bunch data validation

**Status:** not started

**Objective:** Test simulation and firmware assumptions against real CMS
beam-halo/unpaired-bunch data and expert knowledge.

**Repositories and likely files:** workflow data skims/configuration and
analysis selections; CMSSW changes only for narrowly justified diagnostics.

**Implementation outline:** Identify certified runs, fills and unpaired bunches;
build data control samples; compare subsystem timing, primitive BX, L1/HLT and
RAW content with matched simulation categories; obtain subsystem/firmware
review of readout assumptions.

**Required inputs:** data-access approvals, filling schemes, luminosity/run
metadata, menus/conditions and expert contacts.

**Outputs and diagnostics:** data/simulation timing and efficiency comparisons,
run/fill provenance and a list of validated versus unresolved assumptions.

**Validation criteria:** selections and live-time denominators are auditable;
data conditions match emulation; disagreements are not tuned away without a
mechanism-level explanation.

**Dependencies on later work:** Can start once the diagnostic schema is stable;
required before physics claims about real electronics/firmware acceptance.

## Milestone 12 - production, CPU and sample-size strategy

**Status:** not started

**Objective:** Scale only the validated configurations to samples large enough
for subsystem-, BX-, topology- and occupancy-resolved conclusions.

**Repositories and likely files:** workflow campaign manifests, Condor submit
configuration, validation/merge tooling and production documentation.

**Implementation outline:** Benchmark GEN-SIM, mixing/DIGI/HLT and RECO
separately; estimate effective denominators for rare crossings; use paired
seeds and staged scan/physical samples; begin with a small meaningful batch,
then scale after log, scheduler, EOS and ROOT-content validation.

**Required inputs:** measured efficiencies/rates and CPU/memory/output size from
Milestones 1-11, target statistical uncertainties and storage quota.

**Outputs and diagnostics:** versioned campaign matrix, resource requests,
sample-size calculation, completion/integrity dashboard and merge manifest.

**Validation criteria:** pilot jobs terminate cleanly, publish atomically and
produce readable ROOT trees/products with expected event counts; requested
sample sizes meet stated precision; no shared CMSSW rebuild occurs while jobs
use the release.

**Dependencies on later work:** Final scaling milestone; conclusions remain
versioned to the exact timing, mixing, electronics and reconstruction setup.

## Immediate next work after Milestone 1

Milestone 4 begins with the centrally produced CMS pp interaction library and
standard CMSSW RunIII2024Summer24 pileup mixing.  The mechanism-only smoke
test has passed through reconstruction.  Next, obtain a disk replica of the
exact 2024 input and run paired no-pileup/pileup pilots; resolve the missing
SHIFT entries in standard merged tracking truth before making efficiency
claims.  Milestone 2's filling-scheme/BX-window manifest is still needed for
the later physical timeline.

The controlled BX/phase response grid remains distinct.  Trigger probability
and deadtime will not be inferred from the pileup mixture alone: the same
physical window must be evaluated as an ordered set of candidate reference
BXs with per-BX L1/HLT decisions and an explicit trigger-rule model.  No broad
minimum-bias or mixed production should begin before these focused tests pass.
