<!-- Code style: human-code. -->
# Generation instructions

## Assumed jaw interaction depths (2026-10-22)

The user selected inside-jaw collisions and authorized a simple front-heavy
interaction-depth distribution. `shift_jaw_source.py` samples an exponential
in integrated nuclear interaction lengths, conditional on a collision within
the selected assembly. A supplied profile declares the jaw mixture, native
material segments and their interaction-length scale. It is an assumed source
model; never call it measured halo data or a material-survival correction.
The current diagnostic profile uses both horizontal jaws equally and fixed
transverse center lines, with the actual copper/core/copper paths. Keep the
profile, source-only seed, exact original identity and drawn position together.
The draw changes source coordinates; no inverse depth-density or additional
material factor enters W or reconstruction W/p.

`ShiftSourcePlacementProducer` and `shift_source_placement.py` support a fixed
point or an explicit per-original point map for controlled canaries. They copy
the whole HepMC graph, preserve momenta/edges/weights/relative vertex separations,
and precede the nominal physical source-time producer. Its existing timing
implementation expects MM inputs; the actual canary rejects other length units.
Read the native unshifted input (`VtxSmeared` for ordinary DY), not a graph whose
nominal time shift has already been applied. Compile canary plugins privately;
do not rebuild a shared release used by live jobs.

`configure_complete_source` prepares uncensored ordinary GEN in this order:
read the original input with `@skipCurrentProcess`, retain its exact graph as
`shiftSourcePlacement:original`, place it, complete natural muon-producing
hadron decays, apply nominal source time, rebuild the single `generatorSmeared`
proxy, then rebuild `genParticles`. A second smeared-producer proxy causes a
framework registration cycle and must not be added. Keep the
unshifted completed graph as `shiftFullMuonDecays`. Drop old persisted timing
and derived truth collections when saving the prepared input. The first
placement-only canary deliberately does not satisfy this full-input contract.
Ordinary completion uses natural branching fractions and unrestricted
lifetimes; it adds no proposal correction, survival factor or GEN exposure.
A prepared-input file hash differs from the original source hash. Retain both,
with source normalization bound to the original complete source, once.

The stock GenParticle dictionary stores polar coordinates as Double32. For
nearly massless particles, compiled CMSSW and interactive ROOT can differ in
the cancelling mass-squared calculation despite identical source p4. The V2
audit (workflow5193088/80284c9) keeps graph, W, identities, links, stored angles,
Cartesian xyz and vertices exact. Only signed mass-squared and derived energy
use an operation-count bound with explicit Float32 mass storage. Keep truthful
nonzero residuals and freeze the worker and consumer together; the older exact
receipt mode remains strict. This changes validation, not generated physics.

The independent 295-original QCD jaw census now passes this preparation audit,
with all 500 source trials retained. Its broad chamber-envelope check finds
one GEN mass-above-20-GeV candidate; this does not establish detector survival
or useful weighted tail statistics. The next all-original p=1 detector benchmark
keeps the ranking fixed and includes low-score/unknown controls. Do not promote
the GEN score or raw pair count to a measured reconstruction efficiency.

The native 5,000+5,000 DY20+ forced/natural control agrees on the primary muonic
rate within about 1.76 approximate standard errors. This supports the forced
cross section already including the muonic channel factor; do not multiply by
another guessed branching fraction. This result does not close low-mass DY, direct Jpsi or
jaw/material acceptance. Exact counts, per-mass limits and independent final
native readback are in `../../SHIFT_ANALYSIS.md` and the named DY control study.

The separate direct-Jpsi5--10 native forced/natural control is now complete.
The forced source reproduces the canonical20,000-slot graphs, weights and
Run/Lumi ledger exactly. The natural muonic yield106+/-26pb agrees with
the forced source times native443 muonic probability0.0593:114+/-7pb.
Unlike hard DY, the ordinary-hadron source sigma does not include that decay
restriction. Every event in this control has one distinct direct443 parent.
Do not apply a blanket event factor to multiple-parent, secondary or cross-parent
pairs, or claim other bins qualified. No plot weights changed. Exact controls,
Monte Carlo errors and remaining scope are recorded in `../../SHIFT_ANALYSIS.md`.

For a bounded heavy-flavour source check, the existing first-MPI proposal now
also accepts the unfiltered `ordinary` QCD profile (workflow0b7faaf and2dd556f).
Its audit requires every native accepted original and its exact W, an unchanged
full-trial ledger, the actual history/prefilter/source proof and a resolved GEN
path with no muon/decay filter. Cylinder mode remains a separate conditional
population. Each pilot chunk stays within2--2000 fixed trials; this extension
is not a production gate. The raw `qcd_ordinary` adapter still refuses detector
processing until audited jaw placement and full natural muon-parent completion.
The native source-proposal correction is already in W: reconstruction uses W/p,
with no second HF, filter or material factor. Freeze the auditor/imported helpers
together; the new adapter imports `validate_uncensored_config`. Native ordinary
versus disabled-proposal graph/W/exposure identity and a new mixture control
remain required before scaling this unfiltered path.

This does not qualify translating old decay-filtered GEN as a new production.
Fresh source placement must occur before decay-dependent filtering, with full
source exposure, lifetime support, positive reconstruction controls and exact
cumulative probabilities retained. Qualify bounded material transport and
Both-Both reconstruction for QCD, DY and J/psi separately before scaling.
The active six-original GEN/timing canary and exact assumed profile are under
`../../validation/sm_material_selection_20261022_v1/`; its native result and EOS
readback, not a submission receipt or algorithm test, establish runtime closure.

The common diagnostic policy is maintained in
`config/campaigns/sm_material_admission_pilot_v1.json`. Its opt-in pointing V2
ranks proximity to a CSC station on each side, including the beam hole. The
rounded public-XML gas envelopes and single station planes are approximate;
the50cm edge scale is a sampling choice, not detector acceptance or a geometry
uncertainty. V1 policies retain their original result. No muon-pT cut or hard
station veto is added. Keep positive random controls and exact cumulative W/p;
validate actual Both-Both tails on independent originals before production.

Pointing V3 (`shift-two-endcap-pointing-v3`) is a separate opt-in schema for
muons born inside an endcap. It scores forward station projections on both
sides from the actual birth position. A reference plane behind the birth no
longer forces a zero score; only forward reference planes enter the broad
radial score. V1/V2 and the common V2 campaign policy are unchanged. This is a
ranking fix, with positive controls and exact W/p retained, not evidence of
better detector efficiency or populated tails.

## Opt-in complete hadron transport canary (2026-10-22)

CMSSW `Generator.TrackAllHadronDecays` defaults to false. Its private canary
mode transports finite hadron decay trees without a birth-position or direction
restriction, including positive proper time at rest. It registers the original
source lineage before stacking. Only the configured CPU dead-region guards
may be bypassed for external lineage under the existing transport-corridor
flag; time, energy, neutrino and detector/electronics settings stay unchanged.
Unsupported finite decay subtrees fail explicitly instead of promoting daughters.

The response worker accepts an explicit `hadron_transport` request binding
`track_all_hadron_decays: true` and a SHA-bound `build_receipt`. That receipt
must contain the complete matching Generators, Application and application-plugin
library inventory. Install all three only in Condor worker-local runtime,
refresh its plugin cache and omit the old incoming-overlay preload. Existing
requests retain their previous behavior. Native graph/stacking/BSM controls
and actual frozen-runtime config/library-loading checks pass; the common
eight-original real-event test has not been submitted. This mode does not
establish inclusive material response, normalization or better tail precision.
Read `../../SHIFT_RECONSTRUCTION.md` and the current material-study handoff
before preparing that bounded test; do not rebuild a live shared release.

## GEN-tail diagnosis and finer high bins (2026-10-21)

The independent 7,935-original material response test completed with only one
OS Both-Both pair and no retained tails. `trace_shift_material_response.py`
joins SHA-verified GEN features, saved SIM muons/chamber hits and reconstructed
pairs by original identity. Its existing-sample census finds one GEN mass >20
event (not incoming) and nine GEN pair-pT >10 events. Only one pT-tail original
has two matched muons with CSC hits, both confined to the near endcap. Missing
saved tracks do not prove absorption. The detailed detector findings remain in
`../../SHIFT_RECONSTRUCTION.md`.

The new diagnostic in `validation/qcd_gen_tail_trace_20261021_v1/` uses the
unchanged weighted SoftQCD/MPI source and mu-enriched cylinder profile. Its
finer half-open bins are 20--25, 25--30, 30--40 and 40+, with 20,000 fixed
trials each, plus a separate successful 2,000-trial 40+ canary. Native GEN
audits, resolved configurations and EOS SHA readback pass. The 20,680 saved
events contain 66 OS GEN pair-pT >10 originals, including 32 with an incoming
tail pair; none has OS GEN mass >20. These are raw diagnostic counts, not a
weighted rate gain. Keep each fine bin's full trial denominator; the 40+
denominator includes both its 2,000- and 20,000-trial sources, once each. Do
not add the overlapping old 20+ campaign as independent bin exposure.

The scanner freezes every GEN-tail original with probability one and a hashed
1% sample of the remainder, including same-sign/no-tail controls. This yields
281 detector-test originals (66 tails, 215 controls), grouped into 31 workers.
`run_shift_material_response.py` now preserves an explicit per-original
probability vector in both Nano Double W/p and response evidence. The original
uniform-within-source interface remains supported. No old events or weights
are rewritten. This deliberate tail diagnosis is not a production GEN-mass
cut: reconstruction and charge errors can move other GEN events into the tails.

Before reusing a frozen detector archive, exercise its actual weight-annotation
code with the new request. The October 21 HF canary completed all detector
stages but failed because an older worker required scalar `sample.probability`
and the request correctly used per-original `sample.probabilities`. A successful
uniform-sampling benchmark does not qualify that mixed-probability interface.
Freeze the worker and its imported helpers together; keep the original failure.

For a bounded reconstruction diagnosis, a new worker request may declare
`diagnostic_edm_tiers: ["SIM", "DIGIHLT", "RECO"]` (or a subset). The worker
retains the unchanged `step1.root`, `step2.root` and `step3.root` as `sim.root`,
`digi.root` and `reco.root`. It validates tier names before downloading GEN,
includes all retained files in the existing total publication bound, records
their sizes and hashes in the evidence, and verifies remote SHA readback
before deleting scratch inputs. Omitting the option keeps ordinary Nano and
evidence publication. Replay each complete original batch with its original
seed; changing the batch changes the detector random-number sequence.

The detector test uses the same frozen V10 reconstruction and incoming-parent
overlay as the reference. It leaves the shared release, electronics and trigger
settings untouched. A mixed tail/control canary and worker-runtime downstream
verification gate the remaining workers. Final verification writes the ordinary
comparison PDF and a fresh GEN-to-detector loss trace. Read the current native
queue and `response/heartbeat.json` for progress; submission alone is not
completion. The new 3.5 GB stage reservation is inside the already protected
6.144 GB QCD allowance, alongside the unchanged older reservations. No live
peer policy was changed. No ordinary campaign alias is switched by this test.

The larger existing-source census scans forty pinned completed high-bin GEN
files (sources 0004--0043), without regenerating or changing them. All forty
worker audits/readbacks pass: 412,907 saved originals, 800,000 full source
trials, thirteen OS GEN mass >20 originals, four with an incoming tail pair,
none above mass 50. GEN pT >10 gives 1,052 originals (613 incoming); pT >20
gives 121 (102 incoming). Keep raw counts separate from weighted precision:
the thirteen mass originals have effective count only 1.22; the four incoming
ones give 1.75. These are GEN-only effective counts, not reconstructed yields.
`trace_shift_material_response.py --collect-scans` validates returned source
SHA/identity/exposure bindings and pools full trials once before computing them.

All four nominally incoming mass cases contain one muon below 0.7 GeV total
momentum. Its straight projection to z=1100 cm is 98--146 m off-axis. An
incoming pz sign alone is not a detector-pointing criterion. Those projections
ignore fields/scattering and do not establish a hard veto. `mass_response/`
replays all thirteen cases with the unchanged worker archive already qualified
on the original 7,935-event benchmark. It is deliberately GEN-mass-selected,
not an inclusive reconstructed-tail estimator. The separate finer-bin cohort
retains positive no-tail/same-sign controls, and old/source-overlapping outputs
are not merged as independent exposure. Both case cohorts preserve native
weights, complete original decay graphs and full source metadata. Case workers,
downstream traces and read-only watchdogs use persistent working directories.

## Independent material-response benchmark (2026-10-20)

`validation/qcd_material_response_20261020_v2/` freezes 7,935 unique originals
from nine published mu-enriched sources: 971 in pThat 5--10, 964 in 10--20,
and 6,000 in 20+. Uniform sampling within each saved source is independent of
the candidate score. Every benchmark original is reconstructed; the candidate
gate is evaluated afterwards. This is an independent detector-response test,
not another GEN-only efficiency forecast. The current frozen V10 reconstruction
and incoming-parent SIM overlay are separate from the other agent's reco work.

There is no muon-pT threshold in this GEN filter or material preselection.
The filter's `MinPt` is zero; the material rank uses total momentum, with a
smooth 20 GeV scale rather than a hard cut. The legacy streamer's pT-based
priority labels are not used by this test and must not be carried into the
material-guided selection. Pair-pT thresholds below define diagnostic tails,
not event admission. Keep the complementary generator pThat bins separately.

Raw production GEN stores `generatorSmeared`. A timing-only CMSSW step persists
the established nominal `shiftEventTime` translation before material features
or SIM. It runs no generator or decay module. Native graph signatures, weights
and full source Run/Lumi products must agree after the same time translation.
The complete saved decay trees and natural branching fractions are unchanged.
Two real events pass this check. SIM still decides parent interactions, energy
loss, decay and daughter kinematics; the material score never multiplies a rate.

Nanos store exact Double native W/r, where r is the benchmark's fixed-size
source inclusion probability. Pool full source trials once per pThat bin;
never sum copied Nano Run/Lumi exposure or apply the muon-filter efficiency a
second time. A hypothetical candidate retains W/(r p), with separate positive
conditional probability p. The benchmark's published Nanos retain W/r.

Before any detector response returned, the study declared control floors
10%, 1% and 0.1%, with the same OS/parent-material/total-momentum score and
native-weight protection. There is no muon-pT cut. Qualification compares
event-grouped second moments times measured detector CPU, charging the full
feature scan to the candidate. The two primary tails are retained OS Both-Both
mass >20 GeV and pair pT >10 GeV; mass >50 and pT >20 are additional checks.
Require at least 30 positive originals and observed benchmark effective count
25, and intervals supporting a gain. Resample whole originals within pThat
bins. The three-floor/two-primary-tail comparison uses Bonferroni-adjusted
99.1667% percentile intervals. A few added pairs or a raw CPU forecast cannot
pass this test. Detailed weighted shapes use the shared-reference covariance.

`qualification.json` and the project PDFs `control_*_comparison.pdf` are the
reviewed comparison outputs. The first automatic `summary.json` is preliminary;
the final qualification uses the observed W/r effective count, rather than an
extrapolated full-pool count. All results remain conditional on saved finite-
cylinder mu-enriched GEN. They do not establish inclusive material/shower
normalization, missing lifetime support, or complete outward/forward transport.
Do not replace ordinary analysis campaigns with this validation sample.

The actual scheduler lacks XRootD and matplotlib. Detector workers are kept
unchanged, while the replacement `verifier_v3` DAG adopts the three original
canaries and runs its completion/plot jobs in the qualified worker runtime.
The old manager must not be released. The standalone final-analysis worker
explicitly requests credentials and has passed real EOS readback and project
PDF creation/removal. Its read-only watchdog covers the preserved canary,
replacement DAG and final analysis. See `SHIFT_ANALYSIS.md` for current job IDs.
The 1.5 GiB bound reuses part of the existing protected 6.144 GB QCD reservation;
it does not change live peer policies or release the older pilot allocation.

## Material-consistent QCD selection (2026-10-20)

The weekend QCD stream and the 86-original material pilot were retired at the
user's request. Preserve their outputs, full-source accounting and failures;
do not restart their controllers or call their incomplete cohorts complete.
Their shared-storage reservations remain protected until separately reconciled.
See the October 20 consolidation in `../../SHIFT_ANALYSIS.md` and the transport
checks in `../../SHIFT_RECONSTRUCTION.md`.

The weekend V10 runtime does not contain the pilot's private incoming-hadron
transport overlay. Its ready plots are statistical references, not a validated
material-aware QCD prediction. The incoming-hadron kernel must transport the
parents, allow physical interactions to interrupt their planned decays, and
boost the daughters using the parent's actual decay state. A standalone
Geant4 11.4.1 test in `tests/fixtures/preassigned_decay_kinematics.cc` checks the
last step. This does not replace a full material/shower validation.

Use the same muon-enhancement physics, source model and pThat bins when
qualifying a replacement, with full root lifetimes/daughter trees and unfiltered
source exposure established before any decay-dependent filter. The older
finite-cylinder filtered inputs are useful for conditional selection studies;
they do not establish complete inclusive parent/shower transport. Never redraw
their selected decays with the unfiltered importance correction. Keep a positive
control sample for events without the desired GEN pair, including possible
material-created muons and charge migrations.

For a new reconstruction selection, `shift_material_preselection.py` supports
`require_opposite_sign: true`. It prioritizes two incoming opposite-sign GEN
muons with total momentum and the union of their parent material flights.
There is no muon-pT cut. The new `rank_partial_parent_paths: true` option can
use the known external prefix when a parent later reaches unscored central
CMS. Incomplete paths stay flagged; missing material is never a measured
survival probability. The separately chosen positive `unknown_probability`
and `control_probability` remain in the exact event weight. An offline test
used 0.1 for both, with a fresh selection domain. These settings are a candidate
for an independent detector test, not qualified production efficiencies.

An optional `two_endcap_pointing` policy ranks the straight paths on **both**
endcaps, rather than testing only their z direction. Its schema is
`shift-two-endcap-pointing-v1`; freeze `z_planes_cm` (one positive, one negative),
`radial_scale_cm`, and a `geometry_reference` with its `geometry_sha256`.
For each muon the factor is `1 / (1 + (max_projected_radius / radial_scale)^4)`;
multiply the two muon factors into the existing parent-material/total-momentum
priority. This is a broad outer-envelope ranking, not acceptance: it omits the
beam hole, gaps, fields and scattering. Missing vectors remain unknown and keep
their protected probability. A backwards ray has zero pointing priority but
retains the positive reconstruction control. Old policies without this option
are unchanged. It adds no pT cut, material correction or event-weight factor.
Only the recorded admission probability enters W/p. The initial offline check
preserves the known Both-Both control; it does not establish a tail gain and is
not installed in the running frozen detector tests.

The optional `muon_column_priority` policy compares total muon momentum with
an ionization scale times the actual straight-path mass column, measured from
that muon's birth to the external/CMS boundary. Its schema is
`shift-muon-column-priority-v1`; declare `mass_stopping_power_GeV_cm2_g` and its
`reference`. The smooth pair factor is the product of
`1 / (1 + (scale * column / total_momentum)^4)` for the two muons. No pT enters.
This distinguishes a soft muon born beside CMS from one crossing much rock.
It is a ranking proxy, not a calibrated range or survival calculation; it
omits composition changes, radiative losses, straggling and central CMS.
Incomplete columns remain unknown. Both this and the pointing factor change
only admission probability, whose exact inverse remains in W/p. Geant4 still
decides transport. For reference, the PDG standard-rock minimum-ionization
scale is 0.001688 GeV cm2/g:
https://pdg.lbl.gov/2024/AtomicNuclearProperties/HTML/standard_rock.html.
That reference scale is not a universal material model or an efficiency.

`shift_material_path_score.py` also accepts angular-map inputs and reports
nuclear interaction lengths, radiation lengths and mass column separately.
Straight-ray maps and the material score only guide sampling. They do not
replace Geant4 energy loss, interactions, scattering, fields or detector
acceptance, and they never multiply a physics weight.

Freeze GEN/sampling, SIM transport and reconstruction revisions separately for
each test. Score material survival first with parent/daughter histories and
chamber hits, then measure Both-Both performance with the recorded reconstruction
version. Reconstruction is being improved independently; do not rebuild or
change the shared release for these selection studies.

The response-source adapter also accepts the native weighted direct-J/psi
receipt (`family='jpsi'`) and ordinary unit-weight DY receipt (`family='dy'`).
The latter needs the original receipt-bound contract, semantic audit and GEN
bytes, with an explicit relocated GEN path when needed. J/psi keeps its full
fixed-trial denominator and native W; DY keeps its accepted-GEN denominator and
native run cross section. They are distinct conventions: never label DY's
accepted count as Pythia trials, or apply another muon-filter/branching factor.
Both adapters remain diagnostic-only with the forced-decay convention open.
The shared response worker, summary and proposal-weight annotation preserve
these denominator kinds. The older QCD loss-trace reader remains QCD-specific.
Native DY has already timed `generatorSmeared`; its explicit timing input is
the preserved unshifted `VtxSmeared`, so the nominal source-time shift is
reproduced once. Weighted J/psi and existing QCD keep their original untimed
`generatorSmeared` input. Compare full nominal graph signatures before SIM.
These historical-source checks do not qualify a collision inside a collimator
jaw: bind the new source position/material and generator-model audit separately.
The additional explicit `family='qcd_ordinary'` imports an unfiltered ordinary
weighted QCD source with its complete fixed-trial proof. It declares
`requires_full_muon_completion: true` and `muon_enhancement_complete: false`;
the detector response worker rejects that raw input before any download. This
is the input to jaw placement followed by full natural weak-decay completion,
nominal timing and regenerated genParticles, not a renamed mu-enhanced sample.
Keep its original W and trial exposure through that separately audited GEN
preparation. The default mu-enriched QCD adapter is unchanged. Do not substitute
older unit-weight accepted-event records for the required fixed-trial ledger.

For the controlled jaw/full-decay preparation, `prepared_source_record` reads
the existing native full-GEN `complete.json`, pinned by its exact SHA. It checks
placement, full natural decays, nominal timing, rebuilt genParticles, original
event weights and full Run/Lumi accounting, together with the published input
checksum. The prepared descriptor retains the raw source and its denominator;
its `gen_sha256` identifies the prepared input, while `original_source_sha256`
still identifies the original hard source. The response's `source_GEN_sha256`
keeps that original meaning; `prepared_input_GEN_sha256` records the new input.
Workers reject an unbound or changed preparation before I/O. They reproduce
nominal physical source time only from `shiftFullMuonDecays:SHIFTFULLGEN` and
require exact graph equality with the prepared timed graph before SIM. Recorded
reconstruction probabilities and W/p stay unchanged. Repeated prepared versions
do not add original GEN exposure. This binding is canary support, not approval
of the assumed jaw distribution or full production.
The native raw-copy audit uses `shiftSourcePlacement:original:SHIFTFULLGEN`.
Placement reads the prior process directly; it does not schedule a second
GeneratorSmearedProducer, which would create a product-registration cycle.
The stock GenParticle dictionary persists polar momentum and vertices as
`Double32_t`, so those stored values need not equal the original HepMC doubles.
The prepared-source audit constructs expected GenParticles directly from the
newly timed HepMC and passes them through the stock ROOT dictionary. It requires
exact equality to those independently encoded values, exact PDG/status/barcode
ancestry, the pinned dictionary SHA, and zero encoded-reference residuals. It
also records the raw-to-stored deviations; it does not accept a loose momentum
or vertex tolerance. The HepMC placement, decay and timing checks stay strict.
Validated prepared sources also require the frozen
`plugin/pluginShiftHepMC2CopyProducer.so` and request field
`hepmc2_copy_plugin_sha256`. The response worker verifies this checksum and
installs the plugin only into a runtime beneath the real Condor scratch path;
it refuses shared releases and escaping symlinks. After `edmPluginRefresh`, the
prepared SIM config replaces `generatorSmeared` with `ShiftHepMC2CopyProducer`
reading `shiftEventTime::SHIFTTIMING`. This fixed HepMC2 deep copy avoids the
stock producer's duplicate HepMC3 product registration when both old and new
timing products are present. Prepared inputs must already have passed the
explicit native HepMC2 graph audit; there is no HepMC3 conversion or fallback.
The physical timing step, detector settings, particles and W/p do not change.
Legacy inputs keep their previous SIM copy and require no new plugin.

## Streaming mu-enhanced QCD (2026-10-16)

The new stream consumes only completed, published true-production GEN receipts.
Pilots, tests, unfinished files and unused high-bin DAG nodes are excluded.
`shift_streaming_qcd_inputs.py` freezes the complete source plan and validates
the original identities, native weights and full source-trial denominators.
GEN remains on EOS throughout this first reconstruction pass.

`prepare_shift_stream_controller.py` freezes a separate scheduler deployment.
It does not submit. The controller and its read-only watchdog use persistent
absolute working directories, the verified local schedd and seven-day limits.
`prepare_shift_stream_tasks.py` then prepares immutable per-source tasks as
new GEN publications arrive. It never submits them itself.

Each source first receives a GEN feature scan in a worker. The initial preview
drew up to six tail events, three forward opposite-sign events and one event
from the remainder. Scarce groups contributed fewer events; every nonempty
group kept positive inclusion probability. Those small preview selections ran
one original per detector job, retaining its native weight and exact inclusion
probability. Saved muon decays were not drawn again.

For material-priority studies on these mu-enriched sources, keep the saved
decays unchanged. Declare the source `generator_profile` and each event's
`saved_decays_unchanged: true` in the material sampling authority. The material
gate keeps native weights divided by its recorded reconstruction probability;
it does not apply the GEN filter efficiency again. The material gate rejects
lifetime redraws from this decay-filtered subset: a new lifetime proposal must
start before the muon filter, with its own full-source accounting. An unrelated
unfiltered, full-lifetime diagnostic is not the weekend mu-enriched production.

The separately frozen weekend pass instead selects every GEN-tail event,
every forward opposite-sign event and a rounded-up 1% sample of the remainder
in all six pThat bins. It uses 20 selected originals per detector job, a fresh
selection seed, disjoint reconstruction IDs/seeds and a separate output
campaign. Its existing GEN inputs keep the same muon enhancement, SoftMPI
low-pT treatment and weighted high-bin profile. About 324,000 selected originals
is a planning forecast; actual usable statistics require closed source cohorts
and the full normalization checks. Neither storage nor completion of that whole
pool is guaranteed by preparing a worklist.

The controller submits each task at most once. A durable submission intent and
unique nonce survive disconnects. An unknown scheduler response, missing job or
ambiguous submit does not permit another science attempt. Task completion needs
the native exit event and the required identity, count and publication checks.
Histogramming waits for every selected original in its source cohort. A failed
original is not silently omitted from its cohort.

Each finished source publishes histograms immediately to its normal campaign
histograms directory. Rate merges include all complete source cohorts,
independently in each pThat bin; an unfinished or failed source and its entire
exposure remain pending. Exact membership is frozen for each merge. Merges
retain native W/p and apply sigmaND divided by the sum of distinct included
full-source trials once. They publish into ordinary histograms_merged campaign
directories before optional PDF rendering. The plotter selects the newest
successfully closed version per bin. The controller normally re-merges after
at least 100 new originals and 20% growth in any bin, no more often than every
30 minutes, or after six hours with any new complete source.

The October 19 selection revision applies only where selected.json does not
exist. It keeps GEN-tail and forward-OS events with probability one and chooses
control probabilities proportional to the native absolute weight, with a 1%
floor and a bound of two times the native inclusive expected weight-squared
sum. One control event has known certainty inclusion. Event selection uses an
independent deterministic hash draw; every retained event stores its own exact
probability and W/p. Existing selections, detector jobs, weights, training
folds, full GEN denominators and muon-enhanced generator settings are unchanged.
This controls inclusive sampling variance; it does not establish sufficient
reconstructed tail statistics or physics closure.

Designated sources are operational canaries. Bulk reconstruction waits until
their scans, every selected detector job, histogram publication and project PDF
readbacks have passed. The weekend pass additionally requires both real
20-event low/high-bin canaries to fit its 6 MiB publication bound, counting
Nano, evidence, result/receipt bytes and a further 65,536 bytes. Native queue
proof must also show all old preview/canary payloads and their retired managers
have drained. A successful pipeline gate does not establish physical detector
response or the statistical reach of the tails.

Quota admission protects the other declared campaigns and uses only remaining
headroom. Earlier previews also reserved every whole storage slot their parallel
GEN guard could still admit. The new weekend pass consumes the closed GEN pool
and uses the shared peer authority described below. Publication reservations
cover EOS outputs, not local worker scratch. Workers enforce their size bound
before writing outputs.

A new supervisor can set `parallel_gen_completion` to release that reserve after
GEN finishes. This authority has schema
`shift-stream-gen-completion-authority-v1`, the absolute `manager_directory`,
the native `manager_cluster`, and a `files` mapping with SHA-256 hashes for
`ordinary/manifest.json`, `weighted/manifest.json`,
`weighted/scientific_gate.json` and `production.dag`. Both final accounting
records must close their complete fixed production budgets. Native DAG counts
must show every node finished, and a fresh scheduler query must contain no GEN
manager or child records. A new quota observation must start after those final
files close. Missing or conflicting evidence keeps the full reserve. This
storage check does not change the physics or normalization validation flags.
Adopt supervisor changes through a reviewed cold rollover; leave active worker
descriptors, selections and weights intact.

The optional `bound_gen_reserve_by_unfinished_sources: true` also caps the GEN
wave by its remaining canonical output budget. It derives all 1,055 IDs from
the pinned manifests and fixed high-bin gate: 1,045 production sources and ten
validation sources. A complete, audited, readback-verified publication stops
claiming space only when its local receipt closed before the quota audit began
and no corresponding GEN job remains queued or held. Missing, invalid, live and
later publications keep their full worker reservation. Unexpected writers,
result IDs, changed worker/publisher code or an incomplete budget retain the
original whole-wave reserve. Unchanged receipts reuse compact inode/size/time
proofs; a changed receipt is checked again. No source weights or trial counts
change when a storage reservation is released.

The stream also releases a completed task's old admission claim once a newer
quota observation includes its outputs. This covers short workers that start
and finish between scheduler polls. Release requires the exact task manifest,
unchanged successful status, complete result, native execution and exit-zero
events, and no matching queue record, including held jobs or earlier attempts.
The quota observation must start after completion and every status/result/event
log modification time. Unknown evidence keeps the claim. Release receipts bind
the task, status, result and event-log hashes. The shared GEN admission helper
is unchanged.

For that rollover, `prepare_shift_stream_controller.py NEW --adopt-campaign OLD`
freezes a new supervisor without copying or resetting the campaign ledger.
Its scheduler working directory is NEW; task state, quota state, heartbeats and
controller locks stay in OLD. Both original supervisors must be stopped before
submitting the replacements. A frozen replacement factory command may use
`prepare_shift_stream_tasks.py OLD --policy NEW/factory_policy.json`; that policy
may change the reviewed histogram worker, source admission and publication
settings. A sampling revision must separately pin the old factory, source plan
and replacement policy; it applies only to sources without an existing
selection. Existing scans, reconstruction workers, sampling and descriptors stay
pinned.
Quota observations allow 600 seconds for sealed external accounting, and the
audit job requests 1200 seconds before matching.

The historical October 16 V7 capacity rollover also permits
`maximum_open_sources` in that replacement factory policy. This changes how
many predefined source cohorts can advance together; source order, membership,
selections and weights stay
fixed. Supervisor-only overrides `admission_worker_ceiling` and
`maximum_submissions_per_poll` raise scheduling capacity without editing the
original campaign or its sealed admission/storage authorities. That historical
deployment used an account ceiling of 1,000 and reserved its entire possible
worker wave, so more than 100 external workers blocked its 900-worker stream.
The October 16 draft's account ceiling of 10,000 is also historical; neither
value establishes the site's current ceiling.

New streaming controllers default to `reserve_full_worker_ceiling: false`.
They reserve one proposed task plus their queued, held and pending claims,
against other running, transferring or suspended workers. Other productions'
idle jobs do not consume the site's running ceiling. Unknown remote execution
state still stops admission. Standalone DAG callers retain full-wave
reservation by default; an explicit `true` also retains it for a stream.
The October 17 evening replacement uses the verified site ceiling of 1,607,
with at most 900 own workers, twenty waiting jobs and twenty submissions per
poll. Recheck the current site ceiling before any future cold replacement.
The initial sixteen-worker and six-source limits were preview settings.

A separately frozen bulk pass may use `reco_events_per_job` (1--50),
`reco_job_stride` and `reco_job_offset`. Batches cover each selected original
exactly once and reserve disjoint source job/seed ranges. Changing batching,
selection, seeds or output campaign is not a supervisor rollover: prepare a new
pass. Fixed GEN quotas or fractions can be declared separately by pThat bin;
fractions round up to an integer sample and record that exact inclusion
probability. Every nonempty group retains positive sampling support. A group
sampled in full has probability one. These changes do not alter native GEN
weights or full source-trial denominators.

Task preparation hashes each shared frozen worker/archive once per invocation,
checking inode, size and timestamps again before reuse. A change during that
invocation fails preparation. Scientific input hashes and task pins are still
checked separately for every task; the controller also validates its frozen
inputs before scheduling them.

After preparation and quota auditing, the controller refreshes both its real
clock and native local queue before admission. Each PRE claim uses the actual
admission time, so a long preparation cannot make a newer audit look as though
it came from the future.

`worker_publication_bytes` bounds reconstruction and provides the default for
other stages. Explicit `stage_publication_bounds` may raise `histogram` and
`merge` to at most 512 MiB. The admission guard reserves its explicit baseline
per worker plus the difference for each actual large-stage worker, persistent
PRE claim and new candidate. Live/held workers, pending PREs and completed tasks
not yet covered by a newer verified quota keep that extra reservation. Unknown
task identities or stage bounds stop admission. The shared GEN guard is
unchanged. V7 retains its frozen 32 MiB baseline; the unsubmitted first weekend
draft used 128 MiB. The final weekend policy instead requires the measured
6 MiB total-publication canary gate, uses that 6 MiB baseline and reserves the
additional 506 MiB for each actual 512 MiB histogram/merge stage.

An explicit `maximum_outstanding_publication_bytes` also caps the sum of all
baseline and large-stage claims, including held workers and completed workers
not covered by a fresh quota. Set the admission account ceiling no higher than
the verified current site limit from `condor_userprio`; never raise the site's
ceiling. Replace an obsolete frozen policy through a reviewed cold deployment,
preserving live workers and all storage checks.
The weekend policy caps all its outstanding publication claims at exactly
6,144,000,000 bytes, including baseline and stage differences. Thus 900 is a
scheduler limit, not a promise that storage will admit 900 workers at once.

The optional `shift-statistics-peer-storage-authority-v1` authority pins the
existing main, meson and supplement worklists, guards, controller configurations
and native launch identities. It reports every future canonical claim and counts the shared
transient floor once. When full-future headroom is insufficient, its reviewed
constrained mode protects active main/meson allocations and every possible
transient writer, while bounding this stream by the QCD allowance already
protected by each peer guard. Deferred supplements retain their whole-future
admission gates. Unknown peer controllers, unknown writers and undrained old
SM campaigns block admission in both modes. The native quota audit must test
this authority before a new deployment is armed. Native controller Iwd, command,
arguments and marker must match their pinned guard deployment; a replacement
inside the same broad source directory does not inherit its predecessor's
approval. If a deferred supplement starts a science writer, new stream admission
requires the full-future mode. The constrained mode never reports that deferred
future as completed or releases its scientific exposure.

Optional `stage_worker_priorities` for `histogram` and `merge` keep publication
and closed-source merging ahead of the large reconstruction queue. Quota audits
must retain still higher priority so fresh storage observations cannot starve.
Reconstruction batches retain source order; any batch containing a GEN-tail
event gets the tail priority. This boosts such batches but does not put every
tail original ahead of all other originals.

A two-source operational canary may set
`produce_snapshots: false`; this avoids presenting two bins as a complete
six-bin rate snapshot. Normal production keeps snapshots enabled. Optional
frozen `reference_histogram_inputs` and its SHA-256 are transferred to snapshot
workers with `--reference-inputs` for the existing DY/Jpsi components.

New controllers may opt into `isolate_failed_cohorts: true`. A failed detector
execution or timeout is isolated only after the native execute/nonzero exit and
the failed report prove the exact selected GEN identities, count, descriptor
and completed GEN input audit. The report must identify an active detector
processing tier. The whole source and every dependent snapshot are quarantined;
other sources may continue. Failed jobs are never released or retried, and a
failed source is never omitted from a rate denominator. Configuration, identity,
weight and output audits, EOS publication, credentials, unknown exits and queue
faults still stop new admissions globally. Failed evidence remains pinned by
SHA-256 and cannot change silently.

`prepare_shift_stream_retry.py` is an explicit, cold repair for failed histogram
tasks. It requires a fresh, successful native full-owner queue receipt with the
expected schedd, exact query constraint and requested controller attributes.
Old supervisors and the failed job must be absent. The tool preserves the exact
old task and failed ledger record, hashes its unchanged working files, then
prepares the same logical histogram in a separate attempt directory with a
reviewed tools archive. Only that ledger entry returns to waiting; its failed
history remains in `attempts`. It does not submit jobs. A durable intent resumes
an interruption between task replacement and ledger replacement. An earlier
interruption before the intent exists fails closed and needs manual review of
the incomplete preparation directory.

Scratch detector tiers are removed only after their successor passes its audit.
Single-event EOS Nano files are retired only after the closed source's merged
Nano and histograms pass publication readback. Exact deletion receipts remain
beside the cohort outputs; a cleanup problem does not repeat completed science.
Final PDF files return through Condor, pass archive and path checks, and are
published under `tea_shift_cmssw/plots/`. Duplicate returned archives are removed
after the project copies and their checksum receipts are verified.

Campaign findings and current job status belong in `SHIFT_ANALYSIS.md`; detector
findings belong in `SHIFT_RECONSTRUCTION.md`.

## Material-aware QCD study tools (2026-10-19)

`scripts/audit_shift_material_scout.py` compares complete GEN graphs and native
weights with actual transport, saved ancestry and CSC SimHits. It checks original
event identities and preserves the full run/luminosity accounting. CSC support
does not establish digitization, trigger or reconstruction acceptance.

`scripts/shift_material_path_score.py` reads an explicitly supplied private
geometry and measures material along parent chords. Its score is a ranking
feature; it is never a survival correction or an event weight. Unknown or
unmodelled path sections remain explicit. Neither tool changes production.

`scripts/shift_gen_material_features.py` reads the complete HepMC ancestry,
incoming momentum, unique ancestor material flights and daughter material
column to the external/CMS boundary. Bind each row to its original source
ledger, including when several original sources share one merged GEN file.
Unknown paths and zero-GEN-muon records remain explicit. Prefix scans and
intentionally selected controls do not establish representative exposure.

`scripts/shift_material_preselection.py` creates a new diagnostic admission
ledger from those features, a source/decay authority and a frozen policy.
It preserves full original source exposure even for zero-admitted sources.
Each event has positive inclusion probability. New random domains must be
independent of earlier gates; record the cumulative probability and apply
`native_weight / probability * full_event_decay_f_over_q` exactly once.
The material score sets priority only. V1 permits one decay draw per original;
it cannot enlarge a previous selection. The separate TEA weight consumer pins
the exact upstream Nano SHA before appending Double likelihood weights.

The qualified private `CompleteShiftMuonDecayProducer` diagnostic is maintained
under `Configuration/GenProduction/plugins/`; its initial import in `cf4ce80`
was byte-identical to the earlier tested implementation. Ordinary mode completes natural weak decays without
a cylinder cutoff. Its existing importance mode requires exact run/lumi/event,
frozen OS-pion barcodes and proper-lifetime caps. It sums every compatible pair
in the full mixture density and leaves native weights unchanged. Descendant
cascade kernels stay natural. This default remains the bounded truncated-pair
diagnostic. An optional `smoothLifetimeComponents` VPSet adds full-support
exponential proposals for frozen one-parent or two-parent subsets. Each PSet
declares `probability` (absolute whole-event probability), `parentBarcodes`
and aligned `proposalMeanProperLifetimeMm`. These probabilities plus
`ordinaryFraction` must sum to one. Smooth mode requires empty legacy pion
bounds and exact original event identity. Every component contributes to q/f,
including components not selected on that draw. No geometric survival factor
enters the physics weight. The old/new ordinary and legacy-truncated graphs,
lifetimes and metadata agree exactly in native compatibility tests; smooth
means are still diagnostic, without a measured tail-precision gain. Bind the input
file/graph with the external frozen receipt. The legacy output name
`selectedPairBarcodes` holds the chosen subset and can have one entry in smooth
mode. Never load its private plugin
together with a release that already registers the same module. The source
and direct BuildFile dependencies are preserved without rebuilding the shared
release or changing a live campaign; a future standard SCRAM build still
needs its own bounded qualification.

Keep scientific conclusions and the remaining qualification steps in
[SHIFT_ANALYSIS.md](../../SHIFT_ANALYSIS.md) and
[SHIFT_RECONSTRUCTION.md](../../SHIFT_RECONSTRUCTION.md). Study receipts belong
in the compact validation evidence, not in production inputs or this guide.

## Night reconstruction of saved QCD GEN (2026-10-14)

`prepare_shift_night_draws.py` freezes independent draws over all saved QCD
originals. `prepare_shift_night_reconstruction.py` then prepares a separate
immutable worker and scheduler supervisor. It does not submit jobs. The
October 14 worklist is in `../validation/qcd_night_reco_20261014_v1/draws/`:
fourteen fixed 1% draws over 2,866,485 originals in 709 sources require 23,836
new jobs for 368,014 previously unprocessed originals. Reused originals stay
in each draw's membership ledger and are omitted only from reconstruction work.

The controller and watchdog run in the scheduler universe. The default local
night is 22:00--06:00, with new starts stopped at 04:00. Running workers drain;
slow jobs can finish later than 06:00. An expiring three-minute start permission
stops new reconstruction if the controller disappears. A positively verified
worker still staging inputs can receive renewed permission; it checks the
current scheduler permission again before reconstruction. Workers already
reconstructing keep their fixed settings and drain. The campaign starts at
50 workers and can grow to 1000, subtracting other account workers and checking
fresh account and quota observations. Later frozen draws can run while a slow
earlier job finishes. Isolated computation failures remain recorded for explicit
recovery; common input, identity, weight, configuration or publication failures
stop admissions. Reconstruction that already started is never retried automatically.

A supervisor may be submitted before its operational tests finish. Its frozen
`required_operational_gates.json` then keeps it in
`waiting_for_operational_validation`, with no worker submissions or renewed start
permissions. The two named receipts must explicitly pass the native admission
guard and the detector/publication canary. They pin the expected science runtime,
response, base and draw authorities; the guard also pins the exact worker helper,
launcher and manifest. Each required native check must be true, and the recorded
evidence files must still match their SHA-256 values. Merely creating a receipt,
setting a campaign flag, or completing a test with another worker does not arm it.
Only the separate gate receipts may be added after submission; do not edit the
frozen policy, worker files or deployment manifest used by a live supervisor.

Normal Nano, evidence and completion receipts go to versioned support EOS, with
whole-file SHA-256 readback. The initial reservation is 32 MiB and eight EOS
files per admitted worker, above reserves of 50 GiB and 2000 files. Capacity
uses current storage headroom rather than reserving all future workers at once.
An output larger than its reservation stops new admissions for a sizing review.
The full worklist needs more files than the present free file quota allows;
preparing fourteen draws does not promise that all fourteen can finish before
storage is expanded or a separately reviewed complete merge is retained.

Workers are submitted with `condor_submit -spool`. Completed sandboxes remain
on the scheduler for up to thirty days. Rescue archives preserve unique detector
tiers when EOS publication fails. The controller retrieves exact completed
jobs individually only after native output-size and AFS-room checks. Unknown
sizes or insufficient room leave outputs in the scheduler spool and stop new
admissions. Only a successful completed queue record with verified EOS outputs,
retained native exit evidence and hashed retrieved status may be removed
automatically. Failed, held and running jobs are preserved.

The October 15 scheduler repair freezes a separate worker and supervisor; it
does not edit the October 14 deployments. Collector/account discovery runs on
the authenticated audit worker. That worker queries other account schedds but
excludes the local schedd; the scheduler manager combines that evidence with a
fresh query through its verified local address. Named collector queries on the
scheduler host failed in the real detached probe and must not be reintroduced.
Every manager and audit submission uses a persistent absolute `initialdir` and
the same `cwd`. Supervisor submit files explicitly request thirty days, and
the queued `Iwd` and `MaxRuntime` must be checked after submission.

Only idempotent Chirp reads/writes receive bounded transport retries. A failure
to report final progress does not change successful, fully published science
into a failed detector job. The controller may import a frozen
`retained_completions.json` containing exact old status/retrieval hashes. It
accepts old native exit 73 only when the receipt proves an otherwise successful
publication, a final Chirp reporting error and an empty rescue archive. No
science is repeated. Explicitly reviewed zero-science failures and deferrals
may be retired and recovered with their unchanged selected originals, seeds,
probabilities and response. A transient native `Spooling input data files` hold
with zero starts remains reserved rather than becoming a science failure.

Execution order may use `job_order=source_round_robin_descending`. This visits
the harder end of the frozen source catalogue first and spreads work among
GEN files. It changes neither membership nor sampling probabilities; a partial
epoch still cannot be used as an unbiased analysis sample. The October 15
replacement starts at 200 workers after the previous 291 successful jobs,
allows growth to 1000, and uses a five-minute lease. It keeps the 22:00--06:00
night and 04:00 stop for new starts. The watchdog records stale controllers,
observer faults and night-running managers that fail to produce child workers.

Before arming the replacement, exercise the actual detached manager, account
audit, downstream detector worker, native expired-admission guard, all detector
tiers and whole-file EOS publication/readback. Unit tests and submission
receipts alone do not pass those gates. Repair evidence and exact retirement
manifests are in `../validation/qcd_night_reco_20261014_v1/scheduler_repair_20261015/`.

Every analysis release stays closed. Native weights must be divided once by
the union probability for the fixed, fully included draw prefix; original GEN
exposure is counted once. Partial draws and omitted failures cannot become a
normalized physics sample. The unchanged V10 parent-hadron transport still
needs physical validation. Campaign state and numerical results belong in
`../SHIFT_ANALYSIS.md` and `../SHIFT_RECONSTRUCTION.md`.

## Process-bin storage layout (2026-10-09)

The EOS data base now contains process-bin directories only. New standard
workflow runs use `process_bin/YYYYMMDD-campaign/tier/file.root`, with the date
allowed anywhere in the campaign name. Bin boundaries retain their exact value
and encode decimal points as `p`; open upper limits are labelled `inf`.
`workflow.env` derives the process-bin from the configured generator bounds or
fragment name, requires a valid campaign date, and writes direct `GEN_SIM`,
`DIGI_RAW_HLT`, `AODSIM` and `nanoAOD` tiers. Configs, logs and cross sections
are under `metadata/`. Explicit site path overrides remain supported.

Existing data were relocated with metadata-only EOS renames. Their original
immutable manifests, completion receipts, normalization records and hashes
are preserved. The complete migration inventory and path map are in workspace
`validation/storage_reorganization_20261009/`; the storage resolver applies
that map when reading a historical receipt, rather than rewriting its paths.
Shared trigger/pileup inputs and runtime/control payloads are stored under the
sibling `shift_cmssw_support` EOS directory.

Do not resubmit an old frozen deployment to its former output namespace.
New frozen detector-DAG/scoring deployments must version their storage contract,
package the resolver dependencies and publish to the new dated process-bin
tiers. The retained legacy manifest/publication contracts remain unchanged for
auditing; relocating their payloads does not authorize regeneration or cleanup.

## Standing production-retention policy

After the actual production is finalized, remove disposable `nanoAOD_canaries`
and `GEN_pilot` payloads once canonical outputs pass their intended coverage,
identity, weight and ROOT-content checks and no live job, controller, recovery
or downstream consumer needs them. A terminal failed or partial campaign is
not finalized. Keep pilot ROOTs that are the sole scientific evidence or a
required reproducible reference.

The 2026-10-09 request authorizes the reviewed obsolete campaigns/bins across
all processes. It does not authorize blanket deletion of future campaigns.
Freeze an exact inventory, check dependencies and current jobs, and preserve
canonical outputs and unique references before deleting listed payloads.
Keep references outside the analysis base under
`shift_cmssw_support/legacy_references/<process-bin>/<campaign>/`. Archive retired
campaign metadata alongside those references, retaining its directory structure
and verifying hashes. Preserve cross sections, weights, full attempted/accepted denominators,
provenance, configurations, seeds, receipts and validation gates; entire
`metadata/` directories are not disposable. Record per-file deletion receipts,
verify absence and retained-output health, and keep historical paths resolvable
through the audited storage map. Names, age or overlapping bins alone do not
establish obsolescence. Follow the workspace and repository AGENTS.md safeguards.

The 2026-10-09 retirement audit and exact receipts are in
`../validation/pilot_campaign_cleanup_20261009/`. Its independent
`retention_path_map.json` extends historical path resolution without rewriting
the original migration map, which active scoring jobs pin by hash. New frozen
deployments that consume archived references must resolve and freeze the
retention map too; a custom migration map deliberately opts out of the default
retention overlay unless it is supplied explicitly.

## Bounded dark-photon pilots (2026-10-09)

The signal model, limitations, history and active plan are owned by
[SHIFT_BSM.md](../../SHIFT_BSM.md). The first backend is pure on-shell DY
with the full hypercharge-mixing fermion couplings translated into the native
Pythia engine. It retains the CMS common/CP5 shower, stationary-proton target,
6800 GeV Beam B, 148 m source and natural residual muon-producing hadron decays.
It rejects masses below 12 GeV until the low-mass spectral and production
mechanisms are implemented. This is a pilot, not final normalization approval.

Enter the existing built `CMSSW_17_0_0_pre4` runtime without invoking the
workflow's registration/build path. `IOMC/ShiftDarkPhoton` must be built and
present in the plugin cache. Before any shared build, verify that current jobs
do not use the release; do not rebuild or relink a release used by live jobs.

Example bounded signal GEN preparation/run in a **new** output directory:

```bash
python3 scripts/run_dark_photon_gen.py \
  --mass 15 --epsilon 3e-8 --sampling-epsilon 0.005 \
  --events 100 --seed 24682357 \
  --output ../validation/bsm_darkphoton_20261009/m15_displaced_gen100
```

The physical model epsilon is `3e-8`. The sampling value `0.005` specifies
a common amplification of every physical-target fermion coupling; it is not
a replacement physical model point. Pythia 8.317 collapses support for widths
below 1e-6 GeV and can misnormalize very narrow resonances. The runner checks
actual initialized width and both sides of the mass pole. It freezes the
physical target lifetime with `tauCalc=off`, keeps the full natural total width
for the branching fractions, and records the inverse squared amplitude factor
as `production_rate_correction`. Native forced-dimuon cross sections already
include BR_mumu. Apply the amplitude correction once, with no second BR factor.
The independently checked native width is massive-fermion tree level plus
first-order QCD. Final precision/model and exposure gates remain separate.

The runner never builds, submits, overwrites or publishes. It preserves a
contract, copied source, resolved config, logs, ROOT output and full-graph GEN
audit. Its signal parent adapter preserves shower/history copies and physical
decay vertices, marks completed parents as history, and leaves daughter
transport to the unchanged simulation. Complete A' decay graphs outside the
reference hadron cylinder are counted and retained in the full GEN denominator.

For new grids, `--accepted-events` makes `--events N` count written accepted
GEN events. CMSSW stops at N outputs or at a bounded 10*N input slots. The
audit still requires exactly N accepted events, preserves gaps in original
event IDs from rejected generator slots, and retains all Pythia trial counters.
The default remains the original input-slot budget for reproducibility.

After a successful GEN audit, replay a small slice through the exact frozen
background templates:

```bash
python3 scripts/run_dark_photon_detector.py \
  ../validation/bsm_darkphoton_20261009/m15_displaced_gen100 \
  --count 2 --job 900002 \
  --templates ../validation/ntuple_production_20261007_v10/templates \
  --output ../validation/bsm_darkphoton_20261009/m15_displaced_detector2
```

This reuses `run_shift_gen_to_nano.py`, adding only local frozen-input support
and explicit missing-filter metadata. It checks exact identities/weights,
full timed HepMC graph/spacetime through the SIM/RAW tiers, exact retained
GenParticle graph in AOD, and signal ancestry in Nano. Standard regeneration
can permute the same multi-mother references; the audit compares adjacency
multisets and exact physical values, keeping duplicate ancestry edges.
The copied templates retain CMS IR5 material/fields, unchanged detector and
SHIFT reconstruction settings, no pileup and placeholder `HLT_Fake2`. Keep the
result labelled bounded background-chain replay; it is not final recorded-event
acceptance. Detector/reconstruction validation is recorded only in
[SHIFT_RECONSTRUCTION.md](../../SHIFT_RECONSTRUCTION.md).

The second bounded checkpoint also provides reusable signal diagnostics and
histogram replay. With ROOT available, audit one Nano into a **new** JSON file:

```bash
python3 scripts/audit_dark_photon_dimuons.py \
  --input ../validation/bsm_darkphoton_20261009_v2/m50_eps3e-8_detector2/nano.root \
  --output ../validation/bsm_darkphoton_20261009_v2/diagnostics/new_m50.json \
  --max-events 2
```

The diagnostic counts unique retained direct A' pairs and hit-matched signal
legs/vertices; accompanying hadron/cascade muons are distinct. It does not change
reconstruction, data selection or the existing J/psi truth histograms. Retained
Nano truth is a slice diagnostic, not the complete GEN exposure. Timed GEN/HepMC,
not Float Nano vertices, owns proper lifetimes.

For a newly completed detector pilot that has no histogram output yet:

```bash
python3 scripts/run_dark_photon_histograms.py /absolute/path/to/new_detector_pilot
```

This runner enters the existing frozen background TEA runtime itself, verifies
its binary/library/configuration digests and completed detector receipts, and
allows 1-20 events. It overrides only `nEvents` and `weightsBranchName='genWeight'`.
It refuses to overwrite output/receipts and never builds, submits or publishes.
The existing completed pilots must not be rerun into their original directories.

`scripts/dark_photon_scan.py` provides the `LifetimeProposal`, `LifetimeEvent`,
`estimate_lifetime_acceptance` and `recommend_scan_refinements` APIs. Freeze
proper-length anchors and counts before GEN, retain every event and use an
authoritative target total width. Production/BR corrections stay external.
Do not retrospectively add held-out points to the proposal or mix physics
contexts. The utilities recommend new evidence and numerical scan points;
they do not compute CLs, run jobs or establish sensitivity. See
[SHIFT_BSM.md](../../SHIFT_BSM.md) for the adaptive-grid and precision contract,
and [SHIFT_ANALYSIS.md](../../SHIFT_ANALYSIS.md) for the expected-only statistical
design, blinding and upstream normalization/recording gates.

### Preparing a physical 3 by 3 signal grid

`scripts/prepare_dark_photon_grid.py` freezes a reviewable pilot plan, without
running it. Its default is 15, 30 and 50 GeV, with prompt (`epsilon=1e-3`),
approximately 5 m and approximately 30 m **mean lab flight** at each mass.
The physical epsilon is derived from the natural width table and a measured
unfiltered mean boost. Every decay keeps the full exponential distribution.
Proper decay lengths for the displaced columns are about 62-78 mm and
373-469 mm. Lab estimates inherit the finite reference-sample uncertainty.
Use `--epsilons E1 E2 E3` for a strict Cartesian mass/epsilon grid instead.

```bash
python3 scripts/prepare_dark_photon_grid.py \
  --references ../validation/bsm_grid_20261009/reference_inputs.json \
  --templates ../validation/ntuple_production_20261007_v10/templates \
  --output ../validation/bsm_grid_20261009/new_grid_plan
```

The reference schema is `shift-dark-photon-grid-reference-v1`, with
`sample_kind='simulation_only'`, the common/CP5 lists, and per-mass physical
width tables, mean boosts and source provenance. Preparation requires the
existing built CMSSW runtime, independent native partial-width closure and
resolvable proposal support. It writes nine hashed contracts, copied scripts
and V10 templates, plus `plan.json` with separate `gen_argv` and `detector_argv`
for each point. Default budgets are 100 GEN events and a two-event detector
canary per point. Use new directories; existing evidence is protected.

Run and audit each GEN point before its detector command. The GEN receipt
stores technical Pythia tried/selected/accepted counters and the log digest.
Repeated identical statistics tables count once; missing or contradictory
counters fail closed. Accepted events must equal the complete unfiltered GEN
denominator, including out-of-envelope decays. Pythia trials are not proton
collisions or luminosity; authoritative cross sections come from GEN run data.

Check storage headroom before starting. ROOT outputs, cards, frozen scripts,
model files, plots and runtime receipts stay outside Git. Commit reusable
source, tests and instructions only when separately authorized. For the small
grid authorized on 2026-10-09, retire exact per-point GEN/SIM/DIGI/AOD ROOT
intermediates after validated full intended GEN coverage, healthy final ntuples,
histograms and diagnostics, and terminal worker/reference checks. Preserve
source inventories, full-GEN/trial ledgers, locks and final evidence. The grid remains a bounded
software pilot: real recording acceptance, physical rate closure and classifier
mass/lifetime transfer must still be established before using a common N_eff.

### Completed small kinematics grid

**Completion checked 2026-10-12:** all nine points completed with 180 intended
GEN/Nano events and validated per-point histograms. DAG 12878246 succeeded;
the fresh account-wide queue has no live grid jobs. Completion evidence is in
`validation/bsm_status_20261012/`, and all-nine plots are in
`validation/bsm_grid_production_20261009/kinematics/full_grid_20261012/`.
The old controller state records only four plotted points and pending
retirement bookkeeping; retain it as historical evidence. No existing worker
needs histogram resubmission. One final Nano/histogram file exists per distinct
physical point, so no same-point merge is needed for this pilot.

For a new statistics campaign, the user's October 12 processing order is
GEN -> disjoint detector chunks -> same-point Nano grouping to a few thousand
events/file -> histogramming -> same-point histogram merging. The old worker
histograms each twenty-event slice immediately and must not be reused for
that order. Freeze a new BSM inventory/orchestration, validate exact complete
event unions and native weights, count shared GEN source ledgers once, and
keep distinct mass/epsilon/contracts separate. Existing helpers' twenty-event
limits remain intact. See the current checkpoint and plan in
[SHIFT_BSM.md](../../SHIFT_BSM.md).

`prepare_dark_photon_grid.repartition_detector_chunks(...)` prepares smaller
detector slices while preserving every selected original descriptor. Supply
the full ordered GEN identity inventory, the IDs of all materialized or
validated chunks, a new EOS namespace and a disjoint detector job/seed base.
It returns a complete ordered partition; it never submits or changes jobs.
Before choosing the retained set, stop further submissions from the original
manager and reconcile its exact queue, event log and publication receipts.
Freeze replacement workers that look up stable chunk IDs rather than list
offsets, accept the smaller counts, and validate original/new receipt
authorities. Use external completion barriers for retained live workers.
Merge the entire same-point GEN union before scoring/histogramming. A pilot
with only 200 events per point uses one complete file; larger stages should
group a few thousand events per file. The October 12 five-event handover and
its exact scheduling/validation receipts are in
`validation/bsm_repartition_20261012/HANDOFF.md` in the workspace.

`scripts/prepare_dark_photon_production.py` prepares a new immutable full-chain
DAG from a qualified deployment and a new native-preflight grid. Supply the
completed GEN identity inventory, checksum/readback publication receipt,
reviewed storage budget and prior GEN inventories. It rejects reused event,
run, generator-seed and detector-job namespaces. The detector gates run first;
each point then groups its complete Nano union before histogramming, and the
collector waits for every point's merged histogram. Preparation never submits.
This qualified finalizer supports one complete group per point; choose a GEN
budget at or below the grouping target. For simultaneous deployments, list
their pinned authorities in the budget's `external_reservations`. The new
workers reserve every unfinished external detector chunk and count shared
safety floors once. Inspect fresh total quota and concurrency before submitting
the prepared DAG with the native Condor tools.

Preserve every completed signal GEN run's rates before retiring its source
directory. `scripts/export_dark_photon_cross_sections.py --plan PLAN --output
NEW_RETENTION_DIRECTORY --publish` exports `cross_sections.json` and
`cross_sections.txt` to each canonical sample/campaign directory. It also copies
the exact GEN contract, run audit, manifest, log, resolved configuration and
frozen generator/width sources into `metadata/GEN/`, with SHA-256 readback
receipts. The exporter leaves sealed deployments and existing different files
unchanged. It can also run without `--publish` to prepare the records first.

For a statistics extension, pass `--reuse-physical-plan OLD_PLAN` to the native
or spectral grid preparer. This keeps the exact old physical epsilon, proper
lifetime and reference boost, then checks the lifetime again during fresh width
preflight. New accepted-event counts, run numbers and random seeds are separate.
The planners support up to 10,000 accepted events per point; use bounded detector
chunks and groups of a few thousand events before histogramming. `--gen-timeout`
sets a CPU deadline without changing any physical or detector setting.

`scripts/prepare_dark_photon_gen_stage.py` prepares a sealed Condor GEN DAG for
work that must continue after logout. Its `--combine-only --plans ...` mode
combines compatible native/spectral preparations while preserving each point's
event budget and all source authorities. The GEN worker checks complete accepted
identities and weights, publishes with independent SHA-256 readback, and collects
one full-GEN inventory. Only then does the DAG execute the explicit frozen
downstream commands from `--handoff-config`. These commands must prepare and
submit the qualified detector-to-merged-histogram stage and retain cross sections.
Preparation itself never submits. Failed or interrupted commands retain their
evidence and require review; the DAG does not blindly retry or overwrite them.

Use `physical_selected_cross_sections_pb` for dimuon yield estimates: the
native selected rate already contains its dimuon branching fraction, and the
recorded amplitude and physical/native branching corrections were applied once.
Keep the inferred inclusive rate separate. Normalize an accepted signal count
with the full GEN sum of weights, including decays outside the detector and
events without reconstructed candidates. Pythia trial counters are retained
for generator accounting; they are not luminosity. These rates remain an
explicitly provisional pure-DY prediction until physical rate closure passes.
Ordinary partial plotting still requires only available histogram files.

The frozen preparation is
`validation/bsm_grid_production_20261009/preparation_v2/plan.json`: nine points,
20 GEN and 20 Nano events each, including events without reconstructed muons.
Condor DAG 12878246 ran one batch canary followed by seven workers, capped at
two. The local m15_prompt canary was completed before submission. Frozen worker
code is in `analysis_tools/`; runtime and code hashes must remain unchanged while jobs run.
Never rebuild this shared CMSSW release during those workers.

`scripts/run_dark_photon_grid.py` binds every point to its exact physics/source
contract and complete event identities before common histogramming and signal
diagnostics. It does no submission or deletion. The renderer
`scripts/plot_dark_photon_kinematics.py --plan PLAN --output-dir NEW_DIRECTORY`
creates PNG/PDF figures, a multipage overview and ROOT histograms; use
`--allow-incomplete` for a recorded completed-point checkpoint. Curves are
counts per full generated event, not exposure-weighted predictions.

`scripts/retire_dark_photon_intermediates.py` first freezes a dry-run plan and
then accepts `--execute` with a fresh global queue proof and exact validated
point. Retain its append-only journal and every protected record. The native
controller additionally requires exact terminal Condor ownership/exit zero;
workers never remove their own intermediates while running. Native host process
coverage is required. Explicit pinned login-process exceptions remain recorded
as visibility limitations; deletion must not run in the tool PID sandbox.

Controller v3 is frozen under `controller_tools_v3/`; its resumable state is
`monitor_state.json`, log `controller.log`, and plot coverage `latest_kinematics`.
It never submits/retries/cancels jobs. It validates closed points, retires only
their named intermediates and refreshes plots. Condor history is bounded with
`-since 'ClusterId < 12878246'` to avoid scanning older unrelated campaigns.
Check this state and the current process before resuming; never launch a second
controller for the same campaign. Production, constraints and retirement
history remain in [SHIFT_BSM.md](../../SHIFT_BSM.md).

> **Fixed-constraint principle:** this project evaluates SHIFT reconstruction
> under the real Run 3 CMS detector and trigger system. Never change electronics
> integration windows, BX assignment, buffering/readout behavior, trigger
> rules, prescales, or deadtime to improve SHIFT acceptance. Select and model
> the authoritative settings for the chosen year unchanged. See `AGENTS.md`.

## Configure the workflow

Edit `config/workflow.env` before a run. The main controls are:

| Variable | Purpose |
| --- | --- |
| `CMSSW_SRC` | Site-detected CMSSW `src` directory, or an explicit override. |
| `SAMPLE_BASE` | Site-detected production base directory, or an explicit override. |
| `SAMPLE_NAME`, `CAMPAIGN_NAME` | Components of the campaign output path. |
| `N_EVENTS`, `N_JOBS` | Events per chunk and number of Condor jobs. |
| `COLLISION_YEAR` | Coherent `2022`, `2023`, or `2024` era/GlobalTag/pileup preset; currently defaults to `2023`. |
| `GENERATOR_SEED` | `random` or a fixed campaign seed base. A fixed base is advanced by chunk, avoiding duplicate jobs while pairing matching chunks across scans. |
| `SIMULATION_SEED` | Geant4 campaign seed base; fix it with `GENERATOR_SEED` for paired timing scans. |
| `PILEUP_MODE` | `none` or opt-in `standard` central pileup in Step 2. |
| `PILEUP_SCENARIO` | CMSSW pileup profile selected by `COLLISION_YEAR`. |
| `PILEUP_DATASET` | Central CMS minimum-bias GEN-SIM dataset queried through DAS. |
| `PILEUP_INPUT` | `filelist:/absolute/path`, `das:...`, or explicit pileup ROOT PFNs. |
| `PILEUP_SEED` | Mixing campaign seed base, advanced by chunk; fix it for comparisons. |
| `PILEUP_SEQUENTIAL` | Set to `1` only for paired timing scans. Matching chunks use the same pileup sequence, while different chunks rotate across the manifest. |
| `PILEUP_RSE` | Disk RSE used to prepare production manifests; defaults to `T2_CH_CERN`. |
| `SHIFT_TIMING_MODE` | `nominal`, exact `legacy` regression, or a `fixed` test shift. |
| `SHIFT_TIMING_BEAM_DIRECTION_Z` | Longitudinal beam direction, `-1` or `1`. |
| `SHIFT_TIMING_BX_OFFSET` | Additive integer 25 ns shift of the physical SHIFT event. In piggyback mode, positive means SHIFT arrives later than the central BX-0 L1A. |
| `SHIFT_TIMING_PHASE_NS` | Additive fractional timing phase in ns; use `0 <= phase < 25` in piggyback mode. |
| `SHIFT_TIMING_FIXED_OFFSET_NS` | Common ns shift used in `fixed` mode. |
| `SHIFT_READOUT_DIAGNOSTICS` | Default-off persistence of pre-pack muon digis and trigger products for a bounded audit. |
| `SHIFT_SIMHIT_REFERENCE_BX_OFFSET`, `SHIFT_SIMHIT_REFERENCE_PHASE_NS` | Default-disabled time shift applied to one fixed muon PSimHit realization immediately before no-pileup digitization. |
| `SHIFT_SIMHIT_REFERENCE_INPUT` | Absolute shared Step-1 file required by a same-SimHit reference scan. |
| `SHIFT_G4_MAX_TRACK_TIME_NS` | Central Geant4 transport guard, 5000 ns by default. |
| `SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS` | Forward Geant4 transport guard, 5000 ns by default. |
| `SHIFT_LSS_MATERIAL_MODE` | `none` or `external`; attaches the same explicitly transformed external material in Step 1 and Step 4. |
| `SHIFT_LSS_FIELD_MODE` | `none`, provisional `ir1_atlas_proxy`, or validated-payload candidate `cms_ir5_2023_z1100`; selects the same composite field for simulation and SHIFT reconstruction. |
| `SHIFT_LSS_GDML_FILE`, `SHIFT_LSS_GDML_SHA256` | Installed CMSSW `FileInPath` and required frozen-artifact checksum for external material. |
| `SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM` | Converter-recorded model coordinate of the recentered GDML origin. This keeps material aligned with fields. |
| `SHIFT_LSS_MODEL_ORIGIN_CM`, `SHIFT_LSS_MODEL_TO_CMS` | CMS position of FLUKA `(0,0,0)` and the common proper rotation. There is deliberately no default transform. |
| `SHIFT_LSS_FIELD_SCALE` | Required signed scale for either external field payload. Its sign records the reviewed polarity. |
| `TRIGGER_SCENARIO` | `piggyback_central` conditions production on an ordinary recorded central collision; `none` disables that contract. |
| `TRIGGER_TIMELINE_MODE` | `none` or `zero_bias_proxy` for a correlated candidate-trigger sidecar. |
| `TRIGGER_LIBRARY_JSONL`, `TRIGGER_L1_MENU_JSON` | Validated ZeroBias inputs used by the proxy. |
| `TRIGGER_TIMELINE_START_BX`, `TRIGGER_TIMELINE_END_BX` | Relative BX interval sampled around every SHIFT event. |
| `TRIGGER_TIMELINE_SEED` | Trigger sampler seed; fixed seeds are offset by Condor chunk. |
| `TRIGGER_COLLIDING_BX_FILE` | Legacy relative-BX software fixture; mutually exclusive with the physical mask. |
| `TRIGGER_COLLIDING_BX_MASK` | Absolute normalized LPC IP5 mask JSON; required for fill-aware timelines. |
| `TRIGGER_REFERENCE_SLOT_MODE` | `uniform-colliding` for the conditional central-collision sample, `uniform-filled` for structural studies, or `fixed` for a control. |
| `TRIGGER_REFERENCE_BX_SLOT`, `TRIGGER_SHIFT_BEAM` | Fixed physical slot, when requested, and the SHIFT beam. |
| `TRIGGER_RUN_FILL_MAP` | Versioned authoritative trigger-run to fill mapping; required with the physical mask. |
| `TRIGGER_RULE_MODE` | `recorded` for the conditional piggyback sample; `none` or synthetic `run3` only for separate controls/rate studies. |
| `TRIGGER_RULE_HISTORY_START_BX` | First warm-up BX; `run3` requires at least 240 BX before the analysis start. |
| `PIGGYBACK_FILTER_RECONSTRUCTION`, `PIGGYBACK_FILTER_LEVEL` | Filter Step 3 to the decision report at `raw` or `persisted` level. Production defaults to persisted. |
| `ENABLE_EXONANOAOD` | `0` for production NanoAOD; `1` only for an explicit EXO comparison. |

The private CMS IR5 working decks no longer include the unused `MB.inp`, as
confirmed by the provider on 2026-09-29. The comparison launcher audits the
includes and named fields actually present in the selected deck. Missing
required includes and incomplete staged payloads still stop submission.
Original decks, revised-source checksums and validation evidence remain in the
private CMS LSS source directory; see `../SHIFT_LHC_GEOMETRY.md` for the record.

The native field converter supports inline FLUKA `CONSTANT` fields, retaining
their continuation-card components and applying the signed `MGNFIELD` scale.
These fields fill their assigned region; the dipole core-radius cutoff does
not apply. Missing vectors, unsupported symmetry/interpolation and ambiguous
continuations are rejected. The bounded synthetic check
`cmsRun tests/run_cms_lss_constant_field_validation_cfg.py`, run in the SHIFT
CMSSW environment, exercises conversion through runtime field lookup. This
support does not certify the complete 2025 geometry or its physical alignment.

The supported production layout is deliberately canonical:

```text
samples/step1
samples/step2
samples/step3
samples/step4
configs/step1
configs/step2
configs/step3
configs/step4
logs
```

The production reconstruction uses Standard DT navigation with compatible DT
segments added to the precision refit. Tracker seeding/attachment, GEM
measurements, HCAL/ZDC association studies, extended timing, detailed-material
experiments, and the momentum-continuity guard are disabled. These values are
kept together in `workflow.env`; there are no reconstruction-variant presets.

Step 1 applies SHIFT timing after standard vertex smearing and before Geant4.
The 5000 ns transport guards deliberately replace CMSSW's stock 500 ns central
track cutoff for this workflow: a time-zero particle from 148 m reaches CMS at
about 494 ns and would otherwise be killed before detector response can decide
whether it is accepted.  These guards are not electronics readout windows.

### Three campaign configurations

Keep a distinct `CAMPAIGN_NAME` for every row. The recommended sequence is
cumulative, so the difference between adjacent campaigns isolates one new
mechanism:

| Campaign | `SHIFT_TIMING_MODE` | `PILEUP_MODE` | `TRIGGER_TIMELINE_MODE` | Interpretation |
| --- | --- | --- | --- | --- |
| timing | `nominal` | `none` | `none` | Clean source-derived timing and configurable Geant4 transport guard. |
| occupancy | `nominal` | `standard` | `none` | Timing plus central CMS pileup mixed through standard CMSSW mixing. |
| central piggyback | `nominal` | `standard` | `zero_bias_proxy` | Conditions on an already-recorded central collision and reconstructs the standard BX-0 readout. |

For example, the first campaign needs only:

```bash
COLLISION_YEAR=2023
CAMPAIGN_NAME="${PROCESS}_timing_2023"
SHIFT_TIMING_MODE=nominal
SHIFT_G4_MAX_TRACK_TIME_NS=5000.0
SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS=5000.0
PILEUP_MODE=none
TRIGGER_TIMELINE_MODE=none
```

For the occupancy campaign, give it a new name and change:

```bash
CAMPAIGN_NAME="${PROCESS}_occupancy_2023"
PILEUP_MODE=standard
PILEUP_SEED=86420
TRIGGER_TIMELINE_MODE=none
```

On lxplus, the 2023 preset already resolves `PILEUP_INPUT` to the complete
27,774-file manifest under `$SAMPLE_BASE/pileup_inputs`. Override it only when
using another site or a deliberately bounded pilot manifest.

For the conditional central-piggyback campaign, retain the occupancy settings,
use another name, and add durable files visible on every worker:

```bash
CAMPAIGN_NAME="${PROCESS}_piggyback_central_2023"
TRIGGER_SCENARIO=piggyback_central
TRIGGER_TIMELINE_MODE=zero_bias_proxy
TRIGGER_TIMELINE_START_BX=0
TRIGGER_TIMELINE_END_BX=0
TRIGGER_TIMELINE_SEED=24680
TRIGGER_RULE_MODE=recorded
TRIGGER_REFERENCE_SLOT_MODE=uniform-colliding
TRIGGER_SHIFT_BEAM=2
PIGGYBACK_FILTER_RECONSTRUCTION=1
PIGGYBACK_FILTER_LEVEL=persisted
```

The 2023 preset already resolves the validated Run-369943 library and L1 menu
under `$SAMPLE_BASE/trigger_inputs/2023/run369943`. Explicit
`TRIGGER_LIBRARY_JSONL` and `TRIGGER_L1_MENU_JSON` overrides remain available
for another site, run, or year; non-2023 presets intentionally leave them
unset until a year-matched library is prepared.

This campaign is conditional on a central event already accepted in data. It
does not reapply synthetic rules and it never lets SHIFT activity affect the
trigger decision. Step 2 retains every counterfactual event for the denominator
and writes a provenance-rich decision report under
`$SAMPLE_DIR/piggyback_decisions`. Step 3 filters to the selected recorded
readouts; unchanged standard digitization, RAW packing/unpacking, and
reconstruction determine which delayed SHIFT hits survive relative to the
central BX-0 L1A. `SHIFT_TIMING_BX_OFFSET=0` and
`SHIFT_TIMING_PHASE_NS=0.0` are the nominal coincident control. A positive
offset moves the complete physical SHIFT event later while leaving the central
trigger and pileup at BX 0; a negative offset moves SHIFT earlier. Hits on
either side of the L1A may survive when they fall in the real subsystem sample
buffers. They are not removed merely because their time is before the L1A.
This measures
conditional reconstruction performance, not the absolute probability for a
SHIFT collision to coincide with a recorded central event. The ordinary
recorded trigger source and simulated pileup occupancy are currently sampled
independently. Therefore this production does not reproduce event-by-event
correlations between the central event's trigger class and detector occupancy;
treat that as an occupancy systematic, or replace the independent mixing with
a validated data-overlay design before making a data-level absolute claim.

## Absolute collection convolution

Do not multiply average conditional efficiencies into a collection rate. Use
`scripts/convolve_shift_collection.py` after preparing one compatible Run-3
run/fill with a versioned parent-slot-weight JSON, an ordered measured-L1A/HLT
timeline JSONL, and an event-level delay-response JSON. The input schemas are
`shift-parent-bunch-distribution`, `shift-l1a-opportunity-timeline`, and
`shift-event-delay-response` (all version 1).

For each recorded L1A at relative BX `k`, the script queries the matching
event's response at `physical_delay_ns - 25*k` and unions every readout in the
window. It rejects provisional inputs unless `--allow-provisional` is given;
such an output remains an explicitly invalid structural control.

Use `scripts/build_shift_parent_bunch_distribution.py` to normalize the first
input from a complete CSV of `slot,bunch_population,source_weight`, checked
against the official LPC mask. It records both input sources and rejects a
missing, duplicated, or unfilled slot. The `--physics-valid` flag is an
explicit assertion that the supplied bunch populations and source weights are
authoritative; it does not derive either quantity from the simulation.

Use `scripts/build_shift_l1a_opportunity_timeline.py` to turn an ordered,
measured scaler/TCDS/OMS CSV into the second input. It requires the complete
state for each tested BX: recorded-L1A, HLT persistence, run, and lumisection;
it rejects a noncausal HLT-persisted entry. `audit_shift_muon_truth.py` now
also writes unchanged `shiftEventTime` source coordinates, source time, applied
shift, phase, and BX provenance beside each event's existing per-muon SimHit
time range. This supplies the physical-time record without changing generation
or detector simulation.

Use `scripts/build_shift_event_delay_response.py` to validate the third input
from paired full-chain reconstruction results. Its CSV preserves each signal
event's physical delay and outcome at every scanned additional delay; it
rejects reconstruction without a readout and duplicate timing points.

```bash
python3 scripts/convolve_shift_collection.py \
  --parent-weights parent_bunch_distribution.json \
  --response event_delay_response.json \
  --timeline measured_l1a_opportunities.jsonl \
  --output collection_probability.json
```

To measure the complete physical timing response, make paired campaigns with
fixed generator, Geant4, pileup, and trigger seeds, changing only the physical
SHIFT arrival offset. Also set `PILEUP_SEQUENTIAL=1`: the mixing seed fixes the
random draws, while CMSSW's sequential secondary source makes matching jobs
read the same ordered pileup events from the same manifest. Every point must
rerun Steps 1 through 4 because the shift is applied before Geant4; this keeps
standard pileup at BX 0 and avoids modifying CMSSW mixing or electronics code.
For example:

```bash
GENERATOR_SEED=13579
SIMULATION_SEED=24680
PILEUP_SEED=86420
PILEUP_SEQUENTIAL=1
TRIGGER_TIMELINE_SEED=24680

# Coincident reference
CAMPAIGN_NAME="${PROCESS}_piggybackCentral_bx0_2023" \
SHIFT_TIMING_BX_OFFSET=0 SHIFT_TIMING_PHASE_NS=0.0 \
  ./run_condor.sh

# Central L1A occurs 25 ns before the nominal SHIFT arrival
CAMPAIGN_NAME="${PROCESS}_piggybackCentral_bxPlus1_2023" \
SHIFT_TIMING_BX_OFFSET=1 SHIFT_TIMING_PHASE_NS=0.0 \
  ./run_condor.sh --prebuilt
```

The decision JSON records the signed SHIFT-arrival difference from the BX-0
L1A and explicitly records that the electronics configuration was not changed.
This full physical scan includes all standard timing-dependent behavior from
Geant4 onward. Use the existing no-pileup same-SimHit scan when the question is
strictly which losses were introduced by digitization, BX assignment, and RAW
readout, with the simulated detector crossings held exactly fixed.

### LSS material and field comparison

LSS transport is off unless at least one of `SHIFT_LSS_MATERIAL_MODE` and
`SHIFT_LSS_FIELD_MODE` is explicitly enabled. The workflow validates one
common origin and rotation and passes it to Step-1 Geant4 simulation and the
Step-4 SHIFT propagators. External material also enables detailed Geant4e
navigation on the target leg. It is mutually exclusive with the older
`SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL=1` ablation.

The provisional IR1/ATLAS fixture additionally requires:

```bash
SHIFT_LSS_GDML_FILE=PhysicsTools/ShiftLssGeometry/data/validated_lss.gdml
SHIFT_LSS_GDML_SHA256=recorded_64_character_digest
SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM=converter_x,converter_y,converter_z
SHIFT_LSS_MODEL_ORIGIN_CM=x,y,z
SHIFT_LSS_MODEL_TO_CMS=r00,r01,r02,r10,r11,r12,r20,r21,r22
SHIFT_LSS_MINIMUM_ABS_Z_CM=2750
SHIFT_LSS_MATERIAL_BOUNDARY_ABS_Z_CM=14800
SHIFT_LSS_GEANT4E_MAXIMUM_PATH_LENGTH_CM=20000
SHIFT_LSS_FIELD_SCALE=reviewed_signed_scale
```

Do not copy the numerical placeholders into a campaign. The artifact origin
must come from the matching conversion report. The model transform, source
side, boundary, and polarity must be documented and reviewed first. The GDML
must be installed below `CMSSW_SRC` and always runs with overlap checks.
For every enabled LSS Step-4 job, the workflow loads the archived Step-1
configuration and the newly resolved Step-4 configuration with
`scripts/audit_lss_resolved_configs.py`. It fails before reconstruction if
their contract hashes or recorded settings differ, or if a selected geometry,
field, or reconstruction-transport object is absent.

Use four paired campaigns with identical generator and simulation seeds:

| Control | `SHIFT_LSS_MATERIAL_MODE` | `SHIFT_LSS_FIELD_MODE` |
| --- | --- | --- |
| CMS only | `none` | `none` |
| Material only | `external` | `none` |
| Field only | `none` | `ir1_atlas_proxy` |
| Material and field | `external` | `ir1_atlas_proxy` |

The middle two are diagnostic ablations; only the combined row represents the
complete proxy. Keep every result labelled IR1/ATLAS until an authoritative
Run-3 IR5 model passes the same gates.
Do not combine offsets with an assumed probability yet: the current recorded
ZeroBias source establishes a real accepted central readout, but it does not
provide an unbiased distribution of central-trigger times relative to an
independent SHIFT collision.

`run_condor.sh` pins the process, campaign/sample paths, event count, collision
year, timing controls, pileup controls, and trigger controls into the submitted
jobs. It is therefore safe to edit `CAMPAIGN_NAME` and submit the next campaign
without queued jobs silently switching to the new output directory. Do not edit
or rebuild the shared CMSSW release itself while jobs are running.

Check the resolved configuration with:

```bash
source config/workflow.env
printf 'WORKFLOW_SITE=%s\nCMSSW_SRC=%s\nSAMPLE_DIR=%s\n' \
  "$WORKFLOW_SITE" "$CMSSW_SRC" "$SAMPLE_DIR"
printf 'STEP1=%s\nSTEP2=%s\nSTEP3=%s\nSTEP4=%s\n' \
  "$STEP1_DIR" "$STEP2_DIR" "$STEP3_DIR" "$STEP4_DIR"
```

## Run locally

Prepare a stable pileup input manifest on a submit host with a valid CMS
proxy. By default this keeps all currently available replicas at
`PILEUP_RSE=T2_CH_CERN`; a small positive max-files value is useful only for
focused tests:

```bash
source config/workflow.env
./scripts/prepare_pileup_file_list.sh "${PILEUP_INPUT#filelist:}" 0
```

Enable standard CMSSW pileup mixing explicitly:

```bash
PILEUP_MODE=standard \
PILEUP_SEED=86420 ./run_step2_digi_raw.sh 0 1
```

`PILEUP_MODE=none` remains the default no-pileup control.  The manifest avoids
requiring a DAS query and user proxy on every Condor worker.  Do not use a
small smoke-test manifest for production because excessive event reuse would
distort occupancy correlations.

Step 2 uses the slim transient `GENRAW` event content. It retains packed RAW,
HLT `TriggerResults`, pileup summaries and exact pileup-playback provenance,
plus the signal HepMC, SimTracks, SimVertices and PSimHits needed by the SHIFT
analysis. The Run-3 simulated RPC digis are retained explicitly because the
Step-3 `muonRPCDigis` merger consumes them outside `rawDataCollector`.
`mix:MergedTrackTruth` is deliberately dropped: canonical Step 3 does not run
`RECOSIM`, and SHIFT truth association uses the signal `g4SimHits` products
directly. This changes persisted intermediate content only; DIGI, L1,
DIGI2RAW, HLT and detector/electronics configuration remain unchanged.

For a bounded SimHit-to-RAW audit, set `SHIFT_READOUT_DIAGNOSTICS=1` on Step 2.
This default-off mode persists the standard pre-pack muon digis, local
primitives, regional candidates, and uGMT candidates; it does not reconfigure
any digitizer, emulator, packer, BX range, or trigger rule. Unpack the resulting
RAW with `scripts/shift_readout_unpack_cfg.py`, then run
`scripts/analyze_shift_readout_capture.py`. Compare timing points with
`scripts/classify_shift_multi_readout.py`; it fails closed if the paired files
do not contain identical per-muon non-timing SimHit fingerprints.

For the next fixed-trigger boundary, run
`scripts/analyze_shift_trigger_funnel.py` on each unpacked diagnostic file and
combine reports with `scripts/classify_shift_trigger_readouts.py`. The analyzer
tests chamber-compatible CSC correlated LCTs across the real pack/unpack
boundary. It uses the standard CMSSW comparison convention of emulator BX
minus 6 versus the readout-relative RAW BX; this is a representation conversion,
not retiming. DT primitives are simulated-side diagnostics because standard
`RawToDigi` does not expose a post-RAW DT primitive collection here. Regional
and uGMT counts are event-global and must not be called signal-truth matches or
proof of HLT/DAQ acceptance.

For the conditional electronics-response control, reuse one exact nominal
Step-1 file in distinct Step-2 campaigns and vary only the reference offset:

```bash
baseline_step1=/absolute/path/events_step1_part0000.root

for reference_bx in 0 1; do
  CAMPAIGN_NAME="same_simhit_bx${reference_bx}_2023" \
  PILEUP_MODE=none \
  SHIFT_READOUT_DIAGNOSTICS=1 \
  SHIFT_SIMHIT_REFERENCE_INPUT="$baseline_step1" \
  SHIFT_SIMHIT_REFERENCE_BX_OFFSET="$reference_bx" \
  SHIFT_SIMHIT_REFERENCE_PHASE_NS=0.0 \
    ./run_step2_digi_raw.sh 0 10
done
```

This producer copies DT, CSC, RPC, and GEM PSimHits and changes only their
`timeOfFlight` before the unchanged standard mixing/digitization chain. It is
restricted to no-pileup controls and is not a replacement for Step-1 physical
timing or detector simulation.

For a complete BX/phase control, the resumable local runner performs Step 2,
standard RAW unpacking, truth-linked digi capture, and CSC trigger-funnel
analysis in isolated point directories. It requires an already prepared CMSSW
runtime and never rebuilds it:

```bash
CMSSW_PREPARED=1 ./scripts/run_shift_readout_response_grid.py \
  "$baseline_step1" /tmp/shift_readout_integer_grid \
  --offsets=-5:24 --phases=0,6.25,12.5,18.75 \
  --events 10 --workers 2
```

For a high-statistics reconstruction-efficiency scan, use the separate
test-only runner. It copies each Step-1 file to local scratch once, reuses the
same SimHits at every requested delay, and runs the unchanged 2023
digitization, RAW packing/unpacking, and SHIFT reconstruction for each point.
It writes compact NanoAOD files directly, without persistent Step-2 or Step-3
files and without the unrelated PAT/EXONanoAOD work. Each ROOT file is created
in local scratch and moved into the requested output directory only after the
CMSSW job succeeds:

```bash
./scripts/run_shift_reco_delay_scan.py \
  /absolute/path/to/the/baseline/sample \
  /absolute/path/to/shift_delay_scan \
  --delays=-100:100:10 --files 1 --files-per-job 1 --workers 2

../tea_shift_cmssw/utils/shift_delay_efficiency_plotter.py \
  /absolute/path/to/shift_delay_scan --workers 4
```

For the complete sample, submit the grouped scan to HTCondor instead of
running it on a login node:

```bash
./scripts/submit_shift_reco_delay_scan.py \
  /absolute/path/to/the/baseline/sample \
  /absolute/path/to/shift_delay_scan \
  --delays=-100:100:10 --files-per-job 1 --workers 2
```

The submitter uses all available Step-1 files unless `--files` limits them.
Each Condor job owns one input group and all delays for that group, preserving
paired denominators and allowing failed groups to be resubmitted safely.
Keep `--files-per-job 1` for the current samples: event numbers restart in
each Step-1 file, so CMSSW rejects later files in a multi-file group as
duplicate events. Grouping ten such files therefore processes only the first
file and silently loses 90% of the intended statistics.

Wait for the scan cluster to leave the queue before repairing incomplete
points. The repair submitter checks the full JSON/ROOT size and provenance
contract, then creates one small forced job for each invalid file/delay pair.
It does not overwrite healthy pairs:

```bash
./scripts/submit_shift_reco_delay_repair.py \
  /absolute/path/to/the/baseline/sample \
  /absolute/path/to/shift_delay_scan \
  --delays=-100:100:10 --workers 8
```

Run the same command with `--dry-run` after the repair cluster finishes. An
`invalid points: 0` result is the metadata and file-size gate before merging.
The merge then opens every input ROOT file and checks that their total event
count is preserved.

After every delay job is complete and its ROOT/JSON pair is healthy, merge one
file per delay on Condor. This stages the small inputs locally, runs `hadd`,
and replaces the single-file tag with the union of all Step-1 provenance, so
the plotter can still enforce identical denominators:

```bash
./scripts/submit_shift_reco_delay_merge.py /absolute/path/to/shift_delay_scan

../tea_shift_cmssw/utils/shift_delay_efficiency_plotter.py \
  /absolute/path/to/shift_delay_scan/merged --workers 4
```

The delay is in ns and may be positive or negative. The runner converts it to
the exact BX plus phase representation; for example, `-6.25 ns` becomes BX
`-1` plus phase `18.75 ns`. Each output embeds that conversion and the original
Step-1 path in NanoAOD run metadata and in a JSON sidecar. The plotter applies
the same J/psi truth matching and topology definitions as
`ShiftHistogramsFiller::FillEfficiencies` and writes the binomial counts as
JSON beside the muon and dimuon PDFs. Before counting, it keeps only file-group
names present at every delay and reports any incomplete groups it excludes.
It then refuses to compare points whose embedded delays disagree with their
directories or whose resulting Step-1 input sets are not identical. The first
counting pass uses four processes by default and loads only the twelve required
ROOT branches. Its JSON output is also the cache: repeating the same command
redraws immediately from those counts. Use `--workers N` to change the
first-pass parallelism, `--recount` after changing the inputs or counting
definitions, or `--counts-json FILE` to redraw directly from a saved cache.

This scan is the no-pileup, same-SimHit control. It isolates the response of
the fixed electronics/readout and reconstruction to delay. It is not the final
piggyback result with central-collision occupancy. Validate selected points
against the full four-step physical-timing workflow before interpreting the
curve as the production result.

After producing a rule-enabled timeline whose analysis BX range maps to the
response offsets, convolve the independent inputs with:

```bash
./scripts/classify_shift_event_capture.py \
  /tmp/rule_timeline.jsonl \
  --response-dir /tmp/shift_readout_integer_grid \
  --phase-ns 6.25 \
  --output /tmp/shift_event_capture_phase_6p25.json
```

The classifier requires embedded same-SimHit provenance, exact signal
identities and non-timing SimHit fingerprints, complete candidate-L1A grid
coverage, a rule-enabled timeline, and a structured fill mask. It reports
candidate L1A, rule-accepted RAW, and HLT-persistence-proxy layers separately,
including DT/CSC/RPC loss counts. One explicit phase is selected per output;
run the classifier once per phase. RPC/GEM digi BX values can establish
`split_within_readout`; DT TDC and CSC time-bin closure remain separate.

As checked on 2026-08-19, the default 2023 dataset contains 999,856,000 events
in 27,774 DBS files, but some blocks have no current file replicas. Do not use
that unfiltered inventory for production. The current `T2_CH_CERN` manifest
contains 3,713 available disk PFNs. The 2022 preset also has CERN disk replicas.
The 2024 dataset remains tape-only: temporary one-file rule
`bcd7943660744e5abec93117af3c920e` was still `WAITING_APPROVAL` when last
checked. `COLLISION_YEAR=2023` therefore changes the CMSSW era, GlobalTag,
pileup profile, and dataset together rather than mixing a 2023 library into a
nominal 2024 campaign.

Extract a correlated trigger-decision seed from certified collider ZeroBias
RAW data separately from pileup mixing.  Enter the CMSSW runtime, then run:

```bash
./scripts/run_zero_bias_trigger_extract.sh \
  root://eoscms.cern.ch//store/data/Run2023D/ZeroBias/RAW/v1/000/369/943/00000/37bc5780-a374-4104-87a4-3169e9efe16b.root \
  /tmp/zero_bias_run369943.jsonl 100 \
  /ZeroBias/Run2023D-v1/RAW 2023
```

The JSONL keeps complete L1 bit vectors and the accepted HLT-path set per
event, so correlations are preserved.  It is not yet a trigger timeline or a
rate model.  Do not sample paths independently, and do not treat an L1
algorithm bit, final L1A, HLT acceptance and storage as interchangeable.

Resolve the exact L1 bit names through the run-dependent conditions and
validate the empirical library:

```bash
cmsRun ./scripts/zero_bias_l1_menu_cfg.py \
  inputFiles=root://eoscms.cern.ch//store/data/Run2023D/ZeroBias/RAW/v1/000/369/943/00000/37bc5780-a374-4104-87a4-3169e9efe16b.root \
  outputFile=/tmp/zero_bias_l1_menu_run369943.root \
  globalTag=auto:run3_data_prompt collisionYear=2023

python3 ./scripts/extract_zero_bias_l1_menu.py \
  /tmp/zero_bias_l1_menu_run369943.root \
  --output /tmp/zero_bias_l1_menu_run369943.json \
  --global-tag auto:run3_data_prompt

./scripts/validate_zero_bias_trigger_library.py \
  /tmp/zero_bias_run369943.jsonl \
  --l1-menu /tmp/zero_bias_l1_menu_run369943.json \
  --min-events-per-group 100 \
  --output /tmp/zero_bias_run369943_summary.json
```

The validator prints the exact trigger-group ID.  If an input contains more
than one group, pass that ID explicitly to the sampler.  A focused candidate
BX timeline can then be produced with:

```bash
./scripts/sample_zero_bias_trigger_timeline.py \
  /tmp/zero_bias_run369943.jsonl \
  --l1-menu /tmp/zero_bias_l1_menu_run369943.json \
  --output /tmp/zero_bias_timeline_seed24680.jsonl \
  --start-bx -24 --end-bx 5 --signal-events 10 --seed 24680
```

This output is deliberately pre-deadtime.  `readout_after_trigger_rules` is
null until a separately validated trigger-rule engine is applied. A final
timeline must also use a physical 3564-slot fill mask. Run 369943 maps to fill
9017 in `config/run3_trigger_run_fill_map.json`, using CMS BRIL data tag
`24v2`. Normalize the matching official LPC response:

```bash
./scripts/fetch_lpc_bunch_mask.py 9017 \
  --output /tmp/fill_9017_ip5_bunch_mask.json
```

Before selecting a reference slot, scan every filled slot and group identical
nearby-collision patterns:

```bash
./scripts/scan_shift_reference_slots.py \
  /tmp/fill_9017_ip5_bunch_mask.json \
  --beam 2 --start-bx -24 --end-bx 5 \
  --output /tmp/fill_9017_beam2_reference_slots.json
```

The reported `uniform_filled_slot_fraction` is only a structural diagnostic.
It is explicitly not physics-valid weighting because the normalized LPC mask
does not contain authoritative per-bunch intensities.

For conditional central-piggyback production, sample only IP5-colliding slots.
The ordinary event is already recorded, so its trigger-rule decision must not
be synthesized a second time:

```bash
./scripts/sample_zero_bias_trigger_timeline.py \
  /tmp/zero_bias_run369943.jsonl \
  --l1-menu /tmp/zero_bias_l1_menu_run369943.json \
  --output /tmp/piggyback_central_seed24680.jsonl \
  --start-bx 0 --end-bx 0 --signal-events 10 --seed 24680 \
  --colliding-bx-mask /tmp/fill_9017_ip5_bunch_mask.json \
  --run-fill-map config/run3_trigger_run_fill_map.json \
  --reference-slot-mode uniform-colliding --shift-beam 2 \
  --trigger-rule-mode recorded
```

The sampler verifies the normalized LPC provenance, beam occupancy, IP5
collision subset, file digest, orbit wrapping, and trigger-library run-to-fill
match, and embeds them in timeline metadata. `recorded` means that the source
event's real L1A and TCDS history are provenance for an already-made decision;
the four-rule proxy is not reapplied. Uniform colliding-slot weights remain
provisional because the LPC mask contains no per-bunch luminosities. A fixed
slot remains available only as a mechanism control by using
`--reference-slot-mode fixed --reference-bx-slot SLOT`.

Do not pair a convenient fill with an unrelated trigger run. Omitting the mask
treats every BX as colliding, while legacy `--colliding-bx-file` accepts only
relative BX values. Both modes are software fixtures and are rejected by the
final classifier unless `--allow-all-colliding-fixture` is explicit.

For a separate absolute-opportunity/rate study using the synthetic rule engine,
retain the relevant analysis range and add a complete causal warm-up:

```bash
TRIGGER_RULE_MODE=run3
TRIGGER_RULE_HISTORY_START_BX=-264  # 240 BX before analysis start -24
```

The `run3` preset reproduces the four spacing constraints encoded by CMSSW's
`TriggerRulePrefireVetoFilter`, but remains marked as requiring run-period TCDS
validation. It must not be used for a final Run-3 result until that validation
is complete.

The corrected physical timing, Run-3 trigger-rule warm-up, TCDS validation and
detector-response implementation sequence is specified in the
[top-level trigger document](../../SHIFT_TRIGGER.md).

Run the stages in order from the workflow repository:

```bash
./run_step1_generation.sh 0 10
./run_step2_digi_raw.sh 0 10
./run_step3_aod.sh 0 10
./run_step4_exonanoAOD.sh 0 10
```

Before any large Condor submission, run the non-mutating preflight:

```bash
./run_condor.sh --check
```

It validates the conditional piggyback contract, trigger library/menu,
run-to-fill association, physical mask, reference-slot mode, deterministic
seed, and reconstruction filter without building, cleaning logs, or contacting
Condor. A production submission must use a new `CAMPAIGN_NAME`; the default is
`${PROCESS}_piggybackCentral_bx0_phase0_2023_v1` so it cannot silently reuse the older
trigger-proxy outputs.

The first positional argument is the chunk and the second is the number of
events. A valid existing output is reused. Pass `--force` to recreate only the
selected stage and chunk.

Step 3 writes AODSIM and persists a compact `ShiftRecoDiag` singleton table
with reconstruction provenance and detector counts. The verbose per-event
segment/track counter is not part of production; it remains available in the
standalone package tests when detailed debugging is needed.

Step 4 runs `PAT,NANO` with one thread and adds the `ShiftMuon`,
`ShiftDimuonVertex`, `ShiftDT`, `ShiftCSC`, `ShiftRPC`, and `ShiftGEM` tables.
It also writes the generator momentum and vertex columns required by TEA.
Production files are named `events_NanoAOD_part_*.root`.

## Validate before production

For a representative local chunk, require all of the following:

1. `cmsRun` exits successfully.
2. The staged Step-3 and Step-4 ROOT files are non-empty and readable.
3. The Step-4 `Events` tree has the expected entry count.
4. `nShiftMuon`, `ShiftMuon_pt`, and the topology/provenance branches exist.
5. The generated Step-4 configuration has
   `directionalRefitUseMomentumContinuityGuard = False`, DT augmentation
   enabled, and tracker/HCAL/ZDC experiments disabled.

The standalone segment-table test remains available for a deeper detector
content check:

```bash
cd "$CMSSW_SRC"
cmsenv
cmsRun PhysicsTools/ShiftMuonSegments/python/test_shiftMuonSegments_cfg.py \
  inputFile=file:/path/to/events_AOD.root maxEvents=10 \
  outputFile=shiftMuonSegments_test.root
```

## Isolated fixed-target GEN pilots (2023 preparation)

The physics contract and readiness gates are in the workspace
`SHIFT_ANALYSIS.md`. These pilots do not use or modify the live campaign,
build CMSSW, simulate the detector, run reconstruction, or submit Condor jobs.
Enter the existing CMSSW runtime and run, from this workflow directory:

```bash
python3 scripts/run_fixed_target_gen.py --sample qcd --lower 1 --upper 5 \
  --events 20 --seed 13579 --output /absolute/new/output/directory
```

Use `--sample dy --lower 2 --upper 5` for an mHat-binned dimuon pilot;
`--sample jpsi --lower 1 --upper 5` selects direct-J/psi hard-process pThat.
Bounds are in GeV. Use distinct seeds for independent samples/chunks.
Existing output directories are refused; event count is capped at 10000.
The output includes `manifest.json`, `contract.json`, `resolved_cfg.py`,
`cmsRun.log`, `audit.log`, `validation.json` and `gen.root`.
A passing audit establishes GEN integrity only, not a production-ready
2023 model, sample exclusivity, normalization, or detector acceptance.
Read failed logs before retrying; retain failed directories as evidence.

## Run with Condor

### Unfiltered QCD and J/psi production (2026-09-21)

#### September 22 incident: mandatory gates before the next large submission

**Recovery implementation (September 22):** exact delimited provenance matching
replaces the seed-matching wildcard. On TERM/INT the chain stops and waits for
its child process group before releasing its lock. Hard-kill locks still fail
closed; verified abandoned locks are archived by the explicit recovery tool,
never automatically stolen. The affected chunks' existing configs, logs and
checkpoints are copied to `chain_metadata/partNNNN/recovery_before_20260922_v1/`.

The recovery bootstrap is transferred by Condor. It downloads a SHA256-pinned
runtime/workflow bundle once from EOS, relocates CMSSW in worker scratch, and
rejects AFS entries in runtime import/library/search paths. Its local compiled
library fingerprint matches the original production. A ten-worker/two-event
canary precedes the 326-row missing-chunk recovery manifest. The recovery uses
one cluster for all six bins/processes, with `max_materialize=50`, `max_idle=50`
and failed jobs held rather than blindly retried. This bounds the total number
of materialized (therefore running) jobs across this recovery, not 50 per bin.
See the [HTCondor submission reference](https://htcondor.readthedocs.io/en/lts/man-pages/condor_submit.html#max_materialize).
After 35 successful recovery jobs were independently reopened on September 22,
the user requested more parallelism. The live recovery factory 1960281 was
raised to `JobMaterializeLimit=100` at 11:17 CERN using `condor_qedit`, while
`JobMaterializeMaxIdle=50` and all runtime/physics inputs were kept unchanged.
At 11:25 CERN the user explicitly approved releasing all remaining recovery
jobs: the live cap was raised again to **326**, with the idle throttle still
50. Readback showed 100 running, 50 idle and zero held immediately after the
edit. This overrides the earlier 100 cap for this factory only; actual running
concurrency remains subject to CERN allocation and account limits.
The original 50-cap submit file remains an immutable historical record.
This is a monitored-by-snapshot, provisional increment for the scratch-staged
recovery, not a new general default or a verified CERN-safe AFS load threshold.
The current numeric account ceiling remains unverified; central enforcement
must not be bypassed. See `../validation/qcd_unfiltered_20260921/RECOVERY_20260922.md`
for the evidence and exact current-vs-original scheduling distinction.
`bigbird21.cern.ch` is the chosen alternate scheduler; account identity and
CERN's central limits are unchanged. Query/submit that scheduler explicitly.
Do not submit the old six full campaign scripts again.

The three Step-1 errors were 249 valid generated events from 250 requested
framework slots. Exact-seed GEN-only replay reproduced all three cases, including
the missing source identities and matching 249/249 external-filter and GEN-lumi
counters. The audit now records `framework_requested_events` and
`generator_failed_framework_slots` separately; it still requires unit weights,
unfiltered valid generated events, exact IDs, process ownership and bin bounds.
No seeds or physics settings are changed to obtain a favorable event count.
Downstream stages must preserve the actual generated count/identities. Combine
cross sections using the actual generated-event denominator, not the requested
250 times job count. A completed campaign can therefore contain slightly fewer
events than its name suggests; final merge checks must use the metadata total.

The September 21 launch submitted 1320 jobs at once against a shared AFS
workflow/CMSSW installation. CERN IT reported about 1608 concurrent jobs for
the account, excessive AFS load, a temporary account ceiling of 1206, and
automatic eviction/release of some jobs. The ceiling is an account-wide
maximum, **not** a safe target for this workflow. The supplied IT message says
it clears within a day after the load returns to normal; its current value
must be rechecked rather than assumed. On September 22 `condor_q` could not
find the bigbird14 schedd, and `condor_userprio` returned no matching user row.

Do not repeat this launch pattern or automatically retry failed campaigns.
Before recovery or another large production:

- Restore a reliable scheduler view and check the account ceiling and all
  other campaigns. Query failures mean unknown state, not an empty queue.
- Stage a frozen, relocatable runtime and workflow into worker-local scratch
  using a supported transfer/archive mechanism. Keep the standard release on
  CVMFS where appropriate; validate the local custom CMSSW overlay, plugin
  lookup, geometry data and Python imports. Merely copying scripts, leaving
  symlinks or library paths pointing to AFS, is not sufficient.
- Avoid repeated shared-tree scans and runtime setup per stage. The current
  setup sources the shared CMSSW area and scans its library metadata for a
  fingerprint. Keep integrity checks, but perform them on the staged runtime.
  Log redirection to EOS protects AFS space; it does not eliminate AFS reads.
- Use a **global** concurrency limit across QCD, J/psi and other SHIFT bins,
  not one independent limit per submitted cluster. First validate 10 canary
  workers end to end, then use a provisional ceiling of 50 concurrent SHIFT
  workers, or less if the available account headroom is smaller. These are
  conservative operational starting points, not CERN-certified safe rates.
  Increase only after checking measured load and, if needed, consulting IT.
  Do not evade the account ceiling by changing schedds/accounts.
- Explicitly test eviction/restart and multiple simultaneous chunks before
  scaling. Graceful termination should clean only an owned lock. Hard-kill
  recovery must prove the old owner is gone before clearing its exact lock;
  preserve the fail-closed behavior when ownership or scheduler state is unknown.
- Fix provenance selection to match the delimited chunk ID, not arbitrary
  digits later in the filename. The current `*part*NNNN*` pattern also matches
  seed digits; single-chunk smoke tests missed this. Add multi-chunk tests with
  realistic seeded filenames, including 0000/0001/0039/0390.
- Keep lightweight Condor scheduler logs on supported storage. Stage payload
  data/logs locally and publish to EOS with bounded transfers. Never put bulk
  event output on AFS. Validate full-job disk needs before staging a runtime.

The supplied IT email refers to KB0003076 for storage best practices. Its
linked CERN service-portal article and the public batch file-transfer page
were not accessible in this session; do not claim their contents were verified.
The actions above are based on the incident logs and this workflow's code.

Current evidence: all six clusters have terminal records, with 994 successful
and 326 failed jobs. Of the failures, 201 have explicit "Vacated by StackStorm
due to high AFS usage" records followed by a stale-lock failure, 122 have
ambiguous provenance matches, and three have Step-1 subprocess failures.
No bin is complete. Do not publish a survivors-only merge as a full sample:
the missing jobs are not established to be a physics-independent subsample.
Fix/validate recovery, resume only missing chunks, then re-audit all outputs
and combine complete cross sections before merging. Detailed chunk sets and
event-log hashes are in
`../validation/qcd_unfiltered_20260921/terminal_audit_20260922.json`.

The new `config/campaigns/qcd_unfiltered_2023.env` replaces selective replay for
future QCD campaigns. It runs every generated event through the ordinary four
stages: no `mugenfilter`, two-muon momentum preference, or random 10% selection.
The new `QCD_UnfilteredDecays` fragment preserves the previous pi/K/KL decay
corridor, CMS common/CP5 settings, beam/source configuration, nominal physical
timing, and HardQCD-versus-direct-charmonium ownership. Removing acceptance
filters does **not** remove phase-space bin boundaries, decay physics, or
process separation. The historical fragments and replay outputs are unchanged
for reproducibility. They do not become unfiltered samples retroactively.

Use a fresh shell for each bin. `QCD_BIN` accepts `1to2`, `2to5`, `5to10`,
`10to20`, and `20to-1`; the last two need their own runtime sizing before large
production. `0to1` is deliberately rejected pending a validated low-pT model.
The same default fragment is configured with explicit `GEN_PTHAT_MIN/MAX`;
the resolved config and generator audit record and check the actual Born bin.
Bin-specific seed bases are independent of the previous sampling campaigns.

```bash
set -a
QCD_BIN=1to2
N_JOBS=1                  # Set the agreed campaign size only before submission.
source config/campaigns/qcd_unfiltered_2023.env
set +a
./run_condor.sh --check
# Parses a real submit description, but does NOT submit or rebuild:
./run_condor.sh --dry-run /absolute/new/path/condor_1to2.ad
# Only after explicit production approval:
# ./run_condor.sh --prebuilt --keep-logs
```

Defaults are 250 events/job, one CPU, 4000 MB memory, 10000 MB scratch disk and
`+MaxRuntime=50400` (14 hours). Specify either `CONDOR_MAX_RUNTIME_SECONDS` or
`CONDOR_JOB_FLAVOUR`, not both. The 250-event choice is an estimate from the
128 completed 50-event QCD replay jobs: stage wall time per event averaged
77, 81 and 107 seconds in 1--2, 2--5 and 5--10 respectively; the maximum was
148 seconds/event. Linear extrapolation gives about 5.4, 5.6 and 7.4 hours/job,
with 10.3 hours at the observed slowest rate, before additional audit/I/O costs.
Condor recorded a maximum 2686 MB resident-memory estimate. Events are streamed,
so increasing events/job chiefly affects time and disk, not simultaneous event
memory. These are sizing estimates, not a completed 250-event benchmark or a
promise that the whole campaign finishes overnight. Available slots and long
event tails still matter. Stage resource reports record actual peak RSS/time.

`WORKFLOW_LOCAL_GENERATOR=1` loads the frozen fragment in a temporary Python
package and lets cmsDriver inline it into the archived config. It requires a
prebuilt CMSSW runtime, never installs symlinks or rebuilds shared libraries,
and works while unrelated frozen campaigns run. All production settings are
captured in the usual immutable workflow snapshot and Condor environment.

#### Optional rolling intermediate retention

`CLEANUP_PREVIOUS_STEP=1` is enabled in this new profile only; the global default
is zero. It is implemented by `scripts/run_condor_job.sh` and
`scripts/run_retiring_chain.py`, not by standalone `run_stepN` invocations.
Currently it requires the new unfiltered QCD or J/psi process, the complete non-forced
1,2,3,4 chain, one input per Step-4 job, ordinary NanoAOD, and no pileup/trigger
or shared same-SimHit input. Unsupported combinations fail before submission.

After Step N succeeds, its **published** ROOT file is copied back, checked for
zombie/recovered flags, all event identities, full expected count, unit weights,
and essential stage products. Nano validation reads only IDs, weights and
muon/vertex counts, never mass. The exact identity set must match the original
generator metadata and predecessor. Config/log snapshots and a durable
checkpoint are required before deleting that chunk's Step N-1 ROOT file.
No broad deletion or recursive campaign cleanup is used; all configs, logs,
seeds, generation cross sections/counters, per-stage hashes, resource reports,
and deletion receipts remain. At completion only the Step-4 event file remains.

The full-chain worker resumes from its highest validated checkpoint. It does
not regenerate earlier outputs deliberately retired by this option. A corrupt
or missing latest checkpoint, changed physics settings, or a concurrent chunk
lock causes a fail-closed stop. A hard-killed worker can leave
`chain_metadata/partNNNN/active.lock`; verify the old worker is truly gone before
explicitly removing that exact stale lock. Never clear another running job's
lock. `--force` is disallowed: recovery after loss of the last retained output
requires an explicit new campaign/controlled regeneration from saved seeds.

Unfiltered production audits require valid generated = saved = processed events and
unit `genWeight`. There is no sampling sidecar correction. Keep the usual
histogram `genWeight` fill and normalize a complete bin by its cross section
divided by its complete generated-event count. Preserve per-chunk generator
metadata and combine cross-section estimates (do not sum them or rely on the
first-worker text estimate). The existing `collect_generation_metadata.py`
supports the new process and rejects mixed pThat bins. Before interpreting
physics yields, the ATLAS-proxy, low-pT coverage, trigger and decay-model
validation gates still apply.

After the full campaign is complete, export its combined estimate without
overwriting the earlier first-worker file:

```bash
python3 scripts/collect_generation_metadata.py "$SAMPLE_DIR/generation_metadata" \
  --expected-chunks "$N_JOBS" --output "$SAMPLE_DIR/normalization_complete.json" \
  --cross-section-output "$SAMPLE_DIR/cross_sections_complete.txt"
```

Use the combined cross section with the ordinary complete-sample denominator;
no per-chunk or sampling weight is necessary for the new unfiltered sample.
This metadata collector does not replace the final Step-4 completeness and
ROOT integrity audit before merging/plotting.

The analogous `config/campaigns/jpsi_unfiltered_2023.env` accepts `JPSI_BIN`
in `1to2`, `2to5`, `5to10`, with independent seeds and the same resources and
retention checks. Its `Charmonium_Unfiltered` fragment reproduces the direct
charmonium channels, forced 443 dimuon decay, CMS common/CP5 lifetime policy,
and beam/source settings used by the existing binned J/psi GEN pilots. It
does not inherit the old inclusive fragment's `limitTau0=off`, tiny resonance
width cutoff, or stable-muon override. The forced decay is the signal model,
not a muon-acceptance filter; its absolute branching-fraction convention stays
explicitly provisional in metadata. Do not apply an extra branching fraction
without auditing that convention. The collector supports this process too.

For the authorized September 21 overnight run, the selected sizes are
100,000 QCD and 10,000 J/psi events **per bin** in these first three bins,
respectively 400 and 40 jobs of 250 events. Previous J/psi jobs averaged about
82, 100 and 96 seconds/event; the largest observed rate was 146 seconds/event.
High-pThat bins have longer unfinished tails and are not included in this
scale-up. The 0--1 GeV model remains unresolved and is not submitted.

Full worker transcripts are written under EOS `chain_metadata/partNNNN/`,
in addition to retained stage logs/configs. AFS Condor output contains only
short progress/checkpoint messages to avoid exhausting the tight AFS quota.
Historical productions/logs are not deleted by this setup.

### QCD machinery test

Use the isolated preset; it does not change the default J/psi campaign:

```bash
set -a
source config/campaigns/qcd_machinery_2023.env
set +a
./run_condor.sh --check
# Only after a successful bounded step 1--4 test and runtime preparation:
./run_condor.sh --prebuilt --keep-logs
```

This is 1000 x 10 unfiltered pThat 1--5 GeV QCD events under `qcd/`, with
provisional ATLAS proxy material/field and no pileup/trigger conditioning.
The canonical physics scope and ownership rules are in `SHIFT_ANALYSIS.md`.
The prepared-release check rejects a missing or changed generator fragment.
Step 1 audits QCD ownership and saves per-chunk normalization metadata before
publishing the ROOT file. After completion, in the campaign directory:

```bash
python3 /absolute/workflow/scripts/collect_generation_metadata.py \
  generation_metadata --expected-chunks 1000 --output qcd_normalization.json
```

This requires complete generator metadata, but is not a downstream ROOT/job
completeness audit. Validate all four stages before plotting. Do not normalize
from the legacy first-worker `cross_sections.txt` file.

### Mu-enriched QCD pilot

The isolated mu-enriched preset applies the CMS HardQCD plus long-lived-hadron
decay plus generator-muon-filter mechanism to the fixed-target source. It is a
bounded machinery pilot, not an approved production campaign:

```bash
set -a
source config/campaigns/qcd_mu_enriched_pilot_2023.env
set +a
CMSSW_PREPARED=1 N_EVENTS=10 ./run_step1_generation.sh 0 10
```

The fragment selects at least one status-1 generator muon moving from the
source toward CMS, with no generator momentum threshold. Step 1
records both attempted and accepted event counts through `GenFilterInfo`; the
accepted-event weight is based on the attempted denominator. Do not add this
sample to inclusive QCD: it is a filtered subset. Do not scale or submit the
preset until the decay corridor, timing, material-interaction limitation and
normalization audit have been reviewed with the CMS LSS geometry.

The currently authorized 10k machinery campaign runs **Step 1 only**; it does
not test reconstruction:

```bash
set -a
source config/campaigns/qcd_mu_enriched_10k_2023.env
set +a
./run_condor.sh --steps 1 --check
./run_condor.sh --steps 1 --prebuilt --keep-logs
```

This is 1000 chunks of 10 attempts; the filter discards any event without a
qualifying muon before simulation/output. The Step-1 audit rejects any saved
event without a filter-eligible muon and records the attempted/pass denominator
per chunk. Do not proceed to Steps 2--4 until generator production itself has
been completed and reviewed.

### Bounded pThat and early-exit pilots (2026-09-21)

The later user authorization includes full-chain mu-enriched QCD and J/psi
sampling tests, plus recovery of the original QCD campaign. It supersedes the
Step-1-only scope above for these explicitly named machinery tests, not for
final CMS-LSS physics production. The canonical plan and results are in
`../SHIFT_ANALYSIS.md`.

`scripts/prepare_sampling_scan.py OUTPUT --template RESOLVED_STEP1_CFG` freezes
a workflow snapshot and prepares separate `smoke.sub`, `gen.sub` and `full.sub`
Condor manifests. It is a dated, CERN-specific pilot launcher, not a general
central-production interface. Use a fresh output and fresh campaign tags for
a new study; the runner refuses an existing per-chunk report directory.
Inspect the generated manifest before submission. No shared release build is
performed. Verify the frozen Step-1 geometry, conditions and source settings
and run the bounded four-stage smoke before submitting the full manifest.

The present manifest requests five Born pThat bins (1--2, 2--5, 5--10,
10--20, 20--infinity GeV), separately for direct J/psi and mu-enriched QCD:
5000 GEN attempts plus ten full-chain chunks of 20 attempts in each bin.
Preserve original seeds for failed-job recovery, retain failed evidence, and
explicitly select any replacement report so it is not counted twice.

`summarize_sampling_scan.py MANIFEST --output SUMMARY` aggregates only
validated reports and records missing/failed chunks. Its hypothetical
generator-muon, detector-hit and reconstructed-muon gates do **not** filter
events. `audit_sampling_gen_bins.py SUMMARY --output AUDIT`, run in the CMSSW
environment, independently checks saved GEN ROOT files and Born bin ownership.
`audit_campaign_event_counts.py CAMPAIGN --chunks N --all-stages --output AUDIT`
checks the original campaign's four-stage ROOT identities against each chunk's
generation metadata. All reconstructed reads have explicit mass-free branch
allowlists.

The stored Pythia pThat can move below a configured bin edge after constituent
masses are assigned. The production audit reconstructs the Born sampling pThat
from the hard-process record; do not loosen bin bounds or throw away these
events based only on the stored value. `pythia_pthat.py` supports only the
validated built-in HardQCD and direct-charmonium process codes. The nominal
J/psi 0--1 fragment is not valid low-pT coverage with the default divergence
cutoff. Neither setting that cutoff to zero nor mixing arbitrary SoftQCD and
HardQCD samples is an approved solution.

### SoftQCD/MPI low-pT partition (2026-09-25)

The production implementation for the 0--1 GeV bin uses Pythia 8's regulated,
eikonalized `SoftQCD:nonDiffractive` model with the CP5 tune and
`MultipartonInteractions:processLevel = 3`. Both QCD and direct-J/psi samples
start from this identical model. They are complementary event classes:

- `direct_jpsi` contains an MPI hard-process J/psi singlet or octet state
  (PDG 443, 9940003, 9941003 or 9942003 with absolute Pythia status 23 or 33);
- `qcd` contains every other non-diffractive event. Feed-down, nonprompt and
  hadronization J/psi therefore stay in this class.

Within each class, bins are half-open `[lower, upper)`. QCD uses Pythia's
hardest-MPI pThat. Direct J/psi uses the maximum pT of its direct hard state.
The final 20--infinity bin has no upper edge. This makes the two classes
exhaustive and disjoint and gives every event exactly one bin. It also avoids a
model switch at 1 GeV. Do not merge these samples with the older HardQCD or
standalone Charmonium samples; those are separate diagnostics and overlap the
same physical phase space.

For generator-only event production, source one class/bin in a fresh shell.
This campaign runs only GEN and audits the output before publishing it. The
separate `soft_mpi_partition_2023.env` profile still requests GEN,SIM with the
provisional LSS payload; it is for detector software checks, not validated
CMS-detector physics production.

```bash
cd /afs/cern.ch/work/j/jniedzie/private/shift_cmssw/shift_cmssw_workflow
export MPI_PARTITION_SAMPLE=qcd       # qcd or jpsi
export MPI_PARTITION_BIN=0to1         # 0to1, 1to2, 2to5, 5to10, 10to20, 20to-1
set -a
source config/campaigns/soft_mpi_gen_only_2023.env
set +a
source /cvmfs/cms.cern.ch/cmsset_default.sh
./run_condor.sh --steps 1 --check
./run_condor.sh --steps 1 --prebuilt --keep-logs
```

The GEN-only profile rejects Steps 2--4. Each class/bin has a separate
campaign, seed range and 100000-run EDM identity range, so outputs from
separate strata can be inspected together without duplicate run/event IDs.
Chunk IDs must remain below 100000. Step 1 publishes audited metadata only
after the ROOT file matches its recorded SHA-256; reruns verify that pair.
Run all 12 strata for a complete non-diffractive partition. Use a bounded
J/psi pilot to measure wall time before choosing `N_EVENTS` and `N_JOBS`.
Do not use an unclosed per-bin pilot as a normalized sample.

The September 25 bounded GEN validation is recorded in
`validation/soft_mpi_genonly_20260925/readiness.json`. QCD pilots with 1000
accepted events per bin give 63.074 mb in `[0,2)`; an independent 1000-event
inclusive no-hook sample gives 62.752 mb from its QCD low-bin occupancy
(0.34 standard deviations apart). Thirty accepted direct-J/psi events in each
of `[0,1)` and `[1,2)` give about 18% generator uncertainty per bin and
measure roughly 34,000 and 188,000 Pythia trials per accepted event,
respectively. Those direct-J/psi pilots predate the disjoint run-number
namespace and are validation samples only. The inclusive sample has too few
trials to validate rare direct J/psi independently. Full 12-stratum closure
and detector-physics validation remain separate gates.

`ShiftMpiEventClassHook` retries Pythia parton level until the requested class
and bin is found. Pythia's internal cross section already includes these vetoed
trials; the external CMSSW filter efficiency remains one and must not be
applied again. Step 1 records the Pythia tried/accepted counts from its log.
When combining chunks that stop after a fixed number of accepted events,
weight their cross-section estimates by *trials*, not accepted events.
Direct J/psi is rare, so small validation jobs are required before choosing
production chunk sizes. The J/psi fragment forces
`443 -> mu+ mu-`; record that decay convention explicitly and do not multiply
another generator-filter efficiency into its production cross section.

Before normalization, collect every bin's per-chunk metadata and run
`scripts/audit_soft_mpi_partition.py` over all 12 strata with an independent
inclusive non-diffractive cross-section estimate. The audit requires both
classes, all contiguous bins, one model/partition contract, no external filter
loss, agreement of the source-model digest, and statistical closure to the
inclusive cross section. The default gate also requires each bin's generator
statistical uncertainty to be at most 20%. Until that full-suite closure
passes, per-bin pilots remain `normalization_ready=false`. High-pT direct-J/psi
rejection may be too slow for production; measure it before scaling rather
than substituting the older hard samples.

These pilots keep `physics_valid=false` and `normalization_ready=false`.
They use the ATLAS proxy with no pileup/trigger. In particular, a zero-SimHit
gate saves no transport CPU and must not be assumed safe for noise, pileup or
fake vertices. Rejected events need identity, weight, denominator and reason
bookkeeping even when their analysis branches are empty.

The sampling runner also exports the standard campaign `cross_sections.txt`
after the Step-1 audit, using the existing GenXsecAnalyzer log parser. Its
complete per-run estimates remain in `sampling_pilot/*/part*/report.json`.
The text file is first-successful-job bookkeeping, not an average of chunks.
For the September 21 J/psi bins, the missing text files in both `scan` and
`analysis1k_chunk50` campaigns were recovered from the existing 5000-attempt
GEN-only estimates after matching every target generator configuration.
`cross_sections.provenance.json` records the source, full-precision estimate,
uncertainty and checksums. No rerun or event-file modification was needed.
The GEN estimate event count is **not** the reconstructed-sample normalization
denominator. No extra branching fraction or detector efficiency was applied;
the forced-decay normalization convention remains provisional.

### Weighted GEN replay pilots

The bounded September 21 follow-up reuses saved GEN instead of regenerating
collisions. `prepare_weighted_sampling.py REPORT --output LEDGER` selects a
fixed prefix of framework attempts, keeping all events with two forward
stable GEN muons above 10 GeV and a deterministic random 10% of the rest.
Both values are explicit options. The nonzero floor is mandatory. An
independent check in the old QCD sample showed that a hard 20 GeV cut loses
two of its three both-both events; neither 10 nor 20 GeV is approved as a
lossless veto. These variables are MC sampling inputs, never data cuts.

The ledger retains every upstream saved identity, every upstream-absent
identity, sampling probability, selected flag, inverse probability and sums
of weights and squared weights. `validate_sampling_ledger.py` recomputes all
choices and requires an exact partition of the parent attempts. The selected
events are replayed by `run_sampling_replay.py` through unchanged simulation
and Steps 2--4, with an exact shifted-HepMC signature check. The split SIM
path must retain CMS's `PPSTransportTask`; it cannot be dropped with generation.

`prepare_replay_jobs.py` prepares the explicitly bounded CERN pilot manifest;
submit only after an independent four-stage smoke audit. Keep source GEN and
ledger digests fixed. The pilot's default 500-attempt prefixes and five-event
chunks are test sizes, not a production-efficiency recommendation.
`summarize_sampling_replay.py` checks completeness, probabilities and weights
before reporting usable totals. `audit_sampling_stage_identities.py` then
opens every ROOT stage and checks exact identities. `write_sampling_bookkeeping.py`
writes one compact JSONL record per parent attempt: unprocessed events have
null reconstruction, not a fabricated physical zero.
`audit_sampling_replay_counts.py MANIFEST BOOKKEEPING --output AUDIT`, run
inside the CMSSW environment, independently checks Nano muon/topology counts
and every bookkeeping state without enabling any mass branch. Require all
selected chunks to have completed before the final audit. If an exact retry
uses a separate campaign, record it in the manifest's `chunk_campaigns`
mapping; never count both the original and replacement.

`compare_sampling_cost.py BASELINE_SUMMARY REPLAY_SUMMARY --output COST`
compares only complete matching bins and scales the baseline to the same
number of parent attempts. Its output is a recorded successful-stage wall-time
comparison, not a matched-event CPU benchmark or a precision improvement.
The first complete 611-event pilot gave roughly 1.4 times lower recorded
stage cost but no both-both vertices. Do not scale production on timing alone.

**Do not normalize replay output using ordinary Nano `genWeight` or by
summing its inherited parent GEN run/lumi counters.** The per-event sampling
factor is 1/probability for selected events and zero for unselected entries;
the denominator is the unique parent ledger, counted once. Cross-section
weights and luminosity are separate. Sidecar weighting must be integrated
and independently validated before production analysis. Never add a replay
subset to its parent as a separate background. All current replay outputs
remain `physics_valid=false` and `normalization_ready=false`.

### Larger-chunk September 21 follow-up

The explicitly authorized expansion is frozen under
`validation/sampling_scan_20260921_v10` in the workspace. It uses 50 selected
QCD events per chunk (6336 events from three 5000-attempt GEN parents), and
50 J/psi attempts per chunk (1000 attempts per Born pThat bin in 1--2, 2--5,
5--10, 10--20 and 20--infinity). Clusters are 17387638 and 17387641.
The external stage allowance is six hours; detector transport guards are
unchanged. Do not edit the frozen configuration or rebuild the shared release.

`prepare_expanded_sampling.py OUTPUT --template CFG` prepares this bounded
study, not a generic production campaign. Only `replay.sub` and `jpsi.sub`
are its submission manifests; do not submit the generic freezer's other
default manifests. The expanded QCD population contains the previous
500-attempt prefixes and is not additive independent statistics.

After completion, summarize and audit the exact manifests. For J/psi,
`merge_sampling_nano.py SUMMARY --sample jpsi --campaign-tag analysis1k_chunk50
--output MERGE_REPORT` publishes only complete bins, verifying schema,
identities and mass-free topology counts before and after merging. Existing
aggregates are never overwritten. It preserves compressed ROOT baskets;
a bounded parallel-merge test checked 20 exact event identities and topology
counts. Use explicit file lists, not a glob over old and new merges.

For the original recovered QCD campaign, `merge_complete_qcd.py` verifies
every source chunk against its generation metadata and writes a separate
complete aggregate. An older merged file exists but omits recovered events.
Do not delete inputs just because a merge exists. Preserve normalization,
sampling ledgers, seeds, configs, input manifests and validation reports.
The detailed read-only retention proposal is `v10/RETENTION.md`; no cleanup
is part of the generation submission.

Implementation checkpoint (2026-09-21): the sampling/replay launchers, Born
pThat audit, exact-identity and merge checks, cross-section export, and
non-deleting storage inventory/retirement helpers are versioned together.
`audit_lss_resolved_configs.py` loads Step 1 and Step 4 in separate Python
processes to avoid CMSSW's global-era collision; all existing material/field
contract checks remain mandatory. The retention helpers only inventory,
archive metadata or propose explicit file lists; they do not remove data.
Before this checkpoint, 43 focused tests passed across sampling, weighted
sampling, Born pThat, cross-section export, QCD generation, fixed-target
settings, Step-4 chunk contracts and the paired LSS launcher. This test result
does not promote the provisional campaigns to physics-ready production.

### Weighted QCD replay merges and cross sections

`merge_sampling_replay.py MANIFEST --audit-directory AUDIT_DIRECTORY` publishes
one complete NanoAOD per replay bin. The audit directory must contain successful
`summary.json`, `four_stage_audit.json`, `bookkeeping_audit.json` and the full
`bookkeeping.jsonl`, produced by the replay summarizer and the independent
identity/bookkeeping audits above. It recomputes ledger decisions, checks every
source Nano against its report and sampling chunk, checks schemas and duplicate
identities, and verifies the complete merged union. Published ROOT files are
reopened; copied artifacts are checksum-verified. Existing outputs are refused.
No reconstructed mass values are enabled or inspected.

The campaign root receives `cross_sections.txt` from the original parent GEN
log, full-precision `cross_sections.json`, `sampling_ledger.json`, keyed
`sampling_weights.jsonl`, all-parent `sampling_bookkeeping.jsonl`, and
`NORMALIZATION_README.txt`. The merge's adjacent JSON records their paths and
digests. This helper currently accepts only complete single-run QCD parents,
not parent prefixes, and verifies weighted and unweighted parent filter counts
against the generator log. No generator rerun is needed.

For the September 21 expanded samples, each bin has 5000 unique parent attempts.
Use `event_weight_pb = sigma_before_filter_pb / 5000 / sampling_probability`.
The equivalent filtered-sample expression uses `sigma_after_filter_pb` divided
by the upstream saved-event count, **not** the number selected for simulation.
Never apply the filter efficiency a second time or divide by the realized sum
of inverse sampling probabilities. Join the weight sidecar using all three
event identifiers `(run, luminosityBlock, event)`; multiply by luminosity in
inverse pb only when computing event yields. For uncertainties, accumulate
squared event weights as well as their sum.

The current histogrammer does not yet consume this sidecar. A merged ROOT file
and ordinary `cross_sections.txt` are therefore not sufficient for correctly
weighted plots with that application. Native `genWeight` and inherited Runs
counters are unchanged and must not be used as sampling-aware weights or
denominators. Keep `physics_valid=false` and `normalization_ready=false` until
analysis integration and the existing geometry/physics gates are validated.
Do not combine these outputs with the overlapping older 500-attempt replays.

### Complete unfiltered production merges

After the scheduler and factory drain, reconcile original terminal records,
recovery exits and expected chunk IDs. `scripts/merge_unfiltered_production.py`
merges a complete QCD or J/psi bin without modifying its source chunks:

```bash
python3 scripts/merge_unfiltered_production.py /absolute/campaign/path \
  --chunks 400 --date 20260923 --report-directory /absolute/audit/directory
```

Use 40 chunks for the September 21 10k J/psi bins. The helper requires every
four-stage checkpoint, unchanged config/log and generator metadata hashes,
no active lock, exact disjoint generated IDs, unit weights and a common schema.
It stages inputs/merges in local temporary storage, checks the merged union,
checksum-verifies publication and independently reopens the EOS result.
Only IDs, weights, counts and topology labels are read, never mass values.
Existing outputs/normalization files are refused rather than overwritten.

The single `samples/step4_merged/ntuple_complete_<events>events_<date>.root`
has an adjacent audit JSON. `normalization_complete.json` and
`cross_sections_complete.txt` are published at campaign level. Use those
combined estimates and actual event counts; the original `cross_sections.txt`
may contain only the first worker's estimate and is preserved as provenance.
Ordinary `genWeight` filling plus the combined cross section divided by the
actual full event count is the unfiltered-sample convention. Do not reuse
old weighted-replay cross sections or sampling corrections. ATLAS-proxy,
no-pileup/no-trigger and forced-decay normalization limitations remain.
This helper does not silently omit failed jobs; an explicitly approved partial
merge needs a separate recorded chunk list and missing-sample qualification.

### General submission

**September 22 log retention correction:** automatic pre-submission cleanup
now scans all campaign directories under this checkout's `condor/logs`, not
only the newly selected campaign. It queries all schedulers for the account;
active cluster IDs and explicit output/error/event-log paths are protected,
including jobs using frozen wrapper paths. Failed, malformed, or warning-bearing
queue responses skip cleanup. Symlinks, unrecognized filenames and files
created/changed during the check are not removed. `--keep-logs` still opts out;
check/dry-run modes do not clean. Do not routinely pass `--keep-logs` for new
production once incident evidence has been recorded.

EOS payload logs are retained even after jobs finish: the retiring chain hashes
them as checkpoint provenance. Deleting them would break validated resumption.
The September 22 manual AFS cleanup removes only inactive scheduler logs, not
those EOS records or the live recovery logs. Future recovery submissions that
bypass `run_condor.sh` must explicitly invoke the same pre-submission cleanup
or perform an equivalent checked inventory. Do not edit a running recovery
bootstrap or its frozen bundle to change log retention.

Submit the full chain or selected stages with:

```bash
./run_condor.sh
./run_condor.sh --steps 3,4
./run_condor.sh --force-steps 4
```

Before submitting, the workflow builds the shared CMSSW release and records a
runtime fingerprint. It also makes a read-only workflow snapshot for the
workers, so later edits to the main checkout cannot alter running jobs. Never
rebuild or relink the shared CMSSW release while jobs are running. The cleanup
helper also preserves logs whenever it cannot reliably query the scheduler.

For each production, validate the Condor event log and payload publication
messages in addition to counting EOS files. Do not merge until every expected
job has terminated normally and representative destination ROOT files have
the expected trees, branches, and entries.

#### Persisted GEN to detector tiers and NanoAOD

`scripts/prepare_shift_gen_to_nano.py` inventories exact ordinary and weighted
GEN receipts and partitions their event identities into disjoint slices.
`scripts/prepare_shift_ntuple_restart.py` freezes the bootstrap, fair bin order,
storage policy and supervisor deployment. `scripts/freeze_shift_gen_runtime.py`
packages the built runtime without rebuilding it or embedding private geometry
payloads. `scripts/run_shift_gen_to_nano.py` preserves persisted physical time,
GEN kinematics, event identities and weights while validating every downstream
tier and the published Nano readback. Parent normalization ledgers remain
authoritative. Current campaign evidence and readiness are recorded only in
`../SHIFT_RECONSTRUCTION.md`.

The inventory preparer accepts `--template-directory` to choose the archived
Step 1-4 configuration directory; its default is the previously validated CMS
IR5 recipe. Preparers record recipe and parent provenance. They do not grant
permission to launch jobs or manufacture a user-approval record.

`scripts/prepare_shift_detector_sample.py` prepares a representative detector
sample from that complete GEN inventory. For each bin it fixes probabilities
`p = min(1, c * abs(original_GEN_weight))` before one reproducible Bernoulli
draw, using an explicit seed and all 256 SHA-256 bits. The expected count is
set by `--target-events`; the realized count can differ. Do not redraw,
top up or select events using reconstruction outcomes. The frozen plan and
`sampling_events.jsonl.gz` retain exact selected identities, probabilities
and parent normalization authorities. Job offsets address `selected_indices`
rather than consecutive raw GEN entries.

Use `--include-strata qcd_0to1,qcd_1to2` to retain exact named bins, and
`--target-events-json TARGETS.json` for a JSON object of per-bin expected draw
sizes. Unspecified included bins use `--target-events`. These options preserve
the same single draw and full parent normalization; they do not authorize
top-ups or selection using detector outcomes.

For the current storage layout, set `--process-bin-campaign YYYYMMDD-NAME`
and a canonical `--eos-data-root /eos/user/USER_PATH`. Final Nano files use
`PROCESS_BIN/CAMPAIGN/nanoAOD/nano_jobXXXXXXX.root`; completion receipts and
evidence use `metadata/detector_jobs/jobXXXXXXX/`. Canary outputs have a
separate `canaries/` subdirectory. Invalid roots and unknown publication
contracts are rejected. Omit the storage option only when preparing the
historical publication contract.

`--resolve-input-paths` freezes current GEN/receipt locations and retains
`logical_gen`/`logical_receipt` for provenance. Runtime workers can then read
the frozen GEN path without a second migration lookup. `--complete-muon-decays`
enables the existing natural residual muon-producing hadron-decay completion
in the common replay; it preserves branching fractions and the fixed detector
recipe. These settings are recorded in the sampled manifest and descriptors.
The canonical helper is supplied by `tea_shift_cmssw/configs/shift_storage_paths.py`
through the workflow facade; missing helper support fails closed.

The launch preparer honors an explicit canonical XRootD `url` in
`runtime_freeze.json`, allowing the runtime bundle to live outside the data
campaign. An invalid explicit URL is rejected before launch files are written.
Without that field, the historical campaign-relative bundle path is retained.

Sampled NanoAOD retains native `genWeight` and adds double-precision
`shiftSamplingProbability`, `shiftSamplingWeight` (`1/p`),
`shiftOriginalGenWeight` and `shiftSamplingGenWeight` (`original weight / p`).
Use `shiftSamplingGenWeight` once with the full original GEN exposure/trial
denominator. Native Nano `Runs` metadata is not that denominator, and neither
sampling completion nor successful production establishes physics readiness.

The launch preparer freezes the effective `--stage-timeouts` into the hashed
bootstrap and passes them to the worker. Sample defaults scale with event
count: ten-event jobs receive SIM/DIGIHLT/RECO/NANO budgets of
7,200/1,800/5,400/5,400 seconds, a six-hour total limit and an independent
1,800-second event-stall guard. Full-inventory defaults are retained; explicit
stage and total-time overrides are recorded in the frozen launch policy.

`scripts/run_shift_ntuple_controller.py` restores its submitted phase on
restart and reconstructs exact successful counts. Individual failed/stalled
workers are isolated in `worker_incidents.json`; healthy bins continue and
missing outputs never count as complete. Confirmed global quota failures hold
the campaign while both supervisors remain alive and publish separate
heartbeats. Read-only scheduler queries receive batches of three bounded
attempts and continue retrying with live unknown-state heartbeats. Read outages
do not hold healthy workers or authorize capacity growth or other mutations;
ambiguous submit/hold/edit commands are never blindly retried. One durable
email marker prevents repeated alerts across repairs of the same campaign.

After a scheduler outage, the watchdog reads the latest controller heartbeat
before judging it stale. Quota results that arrived during the outage are
inspected before the original polling deadline. A completed, expired audit
can be preserved and refreshed after its terminal state is confirmed;
pending or ambiguous audits must never be duplicated. Global stops include
the durable IDs of unmaterialized recovery factories as well as visible jobs.

Bulk campaigns use `asynchronous_audits=True`. A single durable
`pending_quota.json` records the audit nonce and submission identity before
submission; every controller pass polls it without blocking worker checks.
Quota workers have priority 100. An audit waiting for a slot has no queue
deadline that can stop production. Failed or unavailable observations freeze
capacity growth, preserve the last confirmed allocation and retry only after
the audit is confirmed terminal. Ambiguous submissions retain their identity
and are reconciled rather than repeated. Verified fresh quota exhaustion still
stops the campaign. Validation/release gates continue to require verified quota.

Observer exceptions are recorded in `controller_fault.json` or
`watchdog_fault.json` and restart the observer after 60 seconds while keeping
worker-side time limits active. Only an explicit campaign stop, confirmed
storage exhaustion or a failed exact-inventory completion gate raises
`GlobalProductionError` and holds the whole campaign. Null and previous-attempt
telemetry use the current attempt's setup guard. Every controlled worker retry
must clear previous stage timestamps before release, including Condor's own
periodic-hold timestamps.

When the last requested framework record starts, its processing and output
closure use the existing absolute stage deadline. The worker publishes a
matching scheduler timeout so the controller does not apply an obsolete
per-record timeout while the final record or file close is still running.
The worker chirps the progress epoch before shortening a timeout at a stage
transition. Since scheduler attributes arrive separately, both the controller
and the submitted periodic guard use a positive current-attempt stage deadline
when one is available. The worker enforces its own per-event stall guard.
Missing or previous-attempt telemetry uses the current attempt's setup guard;
an expired absolute deadline or total worker limit still isolates the job.

Worker progress follows actual framework event records. The event-stall guard
is separate from the measured absolute stage budget, and configuration
resolution has its own deadline and log. Native XRootD clients run without
CMSSW loader paths. Uploaded evidence and receipts must keep the same hashes
through worker exit. Do not infer supervisor state from `condor_q` batch
columns: inspect numeric `JobStatus` with `-nobatch`/JSON, plus the timestamp,
phase and health in `controller_state.json` and `watchdog_state.json`.

For a new frozen launch, `prepare_shift_ntuple_restart.py` accepts
`--config-timeout-seconds` (default 600) and
`--event-stall-timeout-seconds` (default 1800). Both must be positive. They are
stored in `policy.json` and the hashed `bootstrap.json`, then passed explicitly
to the worker. Configuration and event-stall guards are not scaled with the
selected event count. `--stage-timeouts` and the total worker limit remain
independent; their defaults are unchanged. For selected jobs, the four stage
budgets scale per 100 events, subject to the worker's existing stage floors.

The October 7 tail recovery uses this explicit operational example for a new
deployment, preserving the exact selected events and all detector settings:

```bash
python3 scripts/prepare_shift_ntuple_restart.py /path/to/new_frozen_campaign \
  --email jeremi.niedziela@cern.ch --pilot-size 0 \
  --config-timeout-seconds 1800 --event-stall-timeout-seconds 7200 \
  --stage-timeouts 72000,18000,54000,144000 --worker-timeout-seconds 21600
```

For a ten-event selected slice this supplies a four-hour NANO stage budget,
a two-hour event-stall guard, and a six-hour total worker bound. A stage budget
does not extend the total bound. These measured tail limits are an explicit
recovery choice, not new universal defaults. Never regenerate launch files
inside an existing live campaign. See the dated findings and actual production
state in [SHIFT_RECONSTRUCTION.md](../../SHIFT_RECONSTRUCTION.md); this example
is not a current completion claim.

For decay-replay DAGs, `watch_shift_decay_dag.py` publishes
`production_complete.json` once every planned Nano receipt passes exact
identity, event-count, tier, path, and provenance validation. This does not
wait for histogram merging. `final_complete.json` records the later merged
histogram audit. Once DAGMan has ended and its child queue has drained, the
watchdog exits on either success or failure. Matching DAG metrics confirm
termination; an empty queue alone is not completion. A failed DAG writes
`production_failed.json` and a terminal failure in `live_status.json`. Missing
terminal evidence and scheduler errors stop the monitor after five checks,
recording `monitor_error` rather than falsely marking production complete.

The bootstrap writes `status.json` in Condor scratch before attempting failure
uploads, using the host Python without CMSSW library or Python paths. Missing,
unreadable or malformed reports still produce a failure status. Failure
evidence uses `failures/job<id>/attempt_<cluster>.<proc>.<nanoseconds>/` below
the frozen source output base, preserving earlier attempts. Uploads use
explicit argument lists and bounded native XRootD commands. Upload errors are
recorded in the local status; they cannot suppress status transfer or change
the worker exit result.

At supervisor startup, successful receipts resolve incidents together with
one ledger read and at most one write per pass. This preserves all incident
identities, records and earlier resolution timestamps instead of rereading
the complete ledger for every finished job. Both receipt scanning and
validation refresh the controller heartbeat every 30 seconds.

Condor can reap the main factory after its last original worker exits while
registered recovery workers are still running. The supervisor queries the
factory itself with native `condor_q -factory`; a factory with no materialized
workers is distinct from a verified retired factory. It skips allocation
edits for a retired main factory while continuing recovery observation and
capacity admission. A failed query remains unknown and cannot authorize
allocation changes or campaign completion. Exact validated job receipts and
per-bin tier event totals remain the completion gate.

Production stdout/stderr use `logs/g<job-id // 500>/`, with the Condor cluster
and process IDs included in filenames. Preparers create both ordinary and
canary shards and use the native `$INT(log_group)` submit macro. This bounds
each directory's entries instead of placing two files for every job into one
AFS directory. An AFS `errno 27` output-transfer hold can affect a worker that
already published a valid Nano: reconcile its canonical EOS `complete.json`,
source descriptor and exact selected IDs, then verify the Nano and evidence
hashes before restoring the status receipt and retiring the redundant held
entry. Releasing it blindly may repeat work or collide with its completed
canonical output. Preserve the full directory by renaming it, then create a
writable replacement; do not delete campaign logs or payloads as a shortcut.

For deferred factories, queue age starts at `JobMaterializeDate` or a more
recent idle transition, rather than the original factory submission date.
CERN may remove resumed attempts whose old runtime accounting exceeded its
wall-time policy: reconcile successful receipts, current exact job IDs and
unmaterialized factory rows before preparing disjoint fresh attempts. Record
their clusters, worker budgets and exact job IDs in `submission.json`.
The supervisor reloads recovery registrations every pass and reserves at most
the number of unfinished recovery identities, including unmaterialized jobs.
It returns that reservation as their receipts pass. A newly registered factory
can use zero idle allowance and `deferred_recovery_clusters` to wait until the
union of the live local queue and verified account snapshot has space for its allocation.
Worker identities include the schedd so equal numeric IDs on different queues
remain distinct; overlapping local records are counted once. The controller
then activates the recovery factory without exceeding the combined ceiling.
Preserve interrupted publication under a distinct EOS path with
matching checksums before retrying a canonical destination.

Capacity-only supervisor updates can use a separate frozen deployment and
`--capacity-policy /absolute/deployment/capacity_policy.json`. The override
accepts scheduling keys only; worker files, source selection and timeouts
remain frozen. The supported concurrency ceiling is 1,000. An explicit
`initial_workers` equal to `worker_ceiling` authorizes that allocation directly;
Condor availability determines actual running slots. Keep the watchdog-first
graceful handoff and reuse the recorded bulk cluster rather than resubmitting
the queue. Preserve the new deployment path in campaign `submission.json`.

#### Persistent GEN production tools

These tools are retained for production, audit, and recovery. Campaign state
and frozen deployments are separate from the source checkout.

| Purpose | Entry points and supporting files in `scripts/` |
| --- | --- |
| Frozen ordinary GEN production | `freeze_shift_gen_runtime.py`, `prepare_shift_production_dag.py`, `submit_shift_production_dag.py`; `shift_production_plan.py`, `run_shift_production_bootstrap.sh`, `run_shift_production_node.py`. |
| Rolling GEN recovery | `prepare_shift_rolling_resumption.py`, `run_shift_production_rolling_node.py`. Completed receipts and the original scientific manifest remain authoritative. |
| Capacity monitoring and readback | `run_shift_production_monitor.py`, `run_shift_production_health_audit.py`, `run_shift_health_bootstrap.sh`; `shift_adaptive_capacity.py`, `shift_condor_native.py`. |
| Fixed-trial weighted GEN | `prepare_shift_weighted_gen_manager.py`, `run_shift_weighted_gen.py`, `audit_shift_weighted_gen.py`, `gate_shift_weighted_gen.py`, `finalize_shift_weighted_gen.py`; `shift_weighted_gen_pre.py`, `shift_weighted_initial_admission.py`. |
| GEN to validated detector tiers and NanoAOD | `prepare_shift_gen_to_nano.py`, `prepare_shift_ntuple_restart.py`, `run_shift_gen_to_nano.py`. The launch preparer owns the frozen bootstrap template. |
| Weighted representative detector selection | `prepare_shift_detector_sample.py`; preserve its sampling plan, event ledger and full parent denominators. |
| GEN-to-Nano supervision and quota | `run_shift_ntuple_controller.py`, `run_shift_quota_audit.py`; `shift_condor_native.py`. |

The fixed-trial weighted workflow replaces the standalone high-bin prototype.
The obsolete `prepare_shift_high_bin_manager.py`, `run_shift_high_bin_probe.py`,
and `audit_shift_high_bin_probes.py` source copies were removed during the
2026-10-05 cleanup. Its frozen worker/auditor copies, receipts, logs, and
recovery state remain under `../validation/high_bins_20261001/manager_v1/`.

Deploy from reviewed source into a new frozen directory before launch. The
monitor deployment uses the filenames `monitor.py`, `health_audit.py`, and
`health_bootstrap.sh` for their corresponding `run_shift_*` source files;
helper imports and deployment hashes must match. Committing source changes
does not update an existing deployment. Never edit or relocate files used by
live jobs. The weighted manager preparer retains the October 1 campaign
recipe and external asset requirements; it is not a general campaign preset.

Use `tmp/<YYYYMMDD-task>/` for one-off experiments, following `AGENTS.md` and
`tmp/README.md`. Keep reusable tools and their regression tests in the paths
listed above and in `tests/`.

#### October 1 GEN scheduling and monitoring record

The following is a dated deployment record, not current queue status. Query
the queue and read the campaign state before acting on these job IDs.

The GEN-only replacement removes the 50-job batch barrier and reuses exact
completed receipts. The scientific manifest and runtime remain frozen. The
user authorized increasing scheduling capacity gradually above 50; the earlier
50-worker starting point is not a permanent limit for this campaign.

`run_shift_production_monitor.py` runs as Condor scheduler job 12794348 using
an absolute frozen deployment, Python `-I -S`, a singleton lock and native
local Condor commands. It coordinates rolling controller 12793294 and the
weighted high-bin GEN controller 12795644; prototype controller 12794350
remains held with its evidence preserved. One shared budget grows
through 50/75/100/150/200/300, with at least 20 minutes and ten new completed
chunks between increases. Idle pressure, stale audits or uncertain queue
coverage freeze growth. Verified failures or exhausted quota hold managers
without cancelling workers. High-bin experimental failures affect only that
manager. No schedd/account changes are used to evade capacity constraints.

Read-only vanilla workers run `run_shift_production_health_audit.py`: quota,
exact immutable receipts, unique event identities and representative ROOT
readback, plus account-wide scheduler discovery through
`tweetybird04.cern.ch` on the submission collector port. Execute hosts' default
collector port 10040 has no submission ads. Scheduler jobs have refreshed AFS
tokens but no Kerberos credentials, so they delegate remote coverage and EOS
checks to these workers. `shift_condor_native.py` enforces clean command
results, finite process-group timeouts and exactly one job per edit.

The active worker category is prepared at 300; live DAGMan global caps are
changed via `DAGMan_MaxJobs`/`DAGMan_MaxIdle`. HTCondor 24.12 does not update
category `MAXJOBS` through those attributes. The category was raised by holding
only DAGMan, validating the sole numeric DAG replacement, then recovering it;
all 38 running workers retained their original start/run counters.
`DAGMAN_REMOVE_JOBS_AFTER_LIMIT_CHANGE=false` protects existing workers when
future admission caps are reduced. A controller recovery can reset its global
caps from Arguments; the common monitor persists the same numeric limits in
Arguments for recovery. Shared capacity reserves already submitted workers
before granting another manager jobs. Zero would mean unlimited to DAGMan,
so a manager with no available slot receives an owned capacity hold instead.
Only that exact owned hold is automatically released; scientific and manual
holds require review. Physics manifests, worker scripts and runtime hashes are unchanged.

Live status, registry, frozen deployments, audits and the append-only changes
journal are under `../validation/production_plan_20261001/manager_monitor/`.
The high-bin standalone validation receipts are separate under
`../validation/high_bins_20261001/manager_v1/`; they must not be counted as
weighted GEN production. The separate `manager_v2/` graph runs fourteen
closure/pilot nodes, an automatic scientific gate, fixed-trial high-bin
production, and final effective-statistics accounting. The initial PRE barrier
prevents any worker submission until the held controller is registered.
Its publication contract is `shift-weighted-gen-receipt-v3`: positive event
weights and zero accepted chunks are valid, fixed trial counts are authoritative,
pilots are excluded from production normalization, and remote artifacts receive
independent SHA-256 readback. Common audits reopen ROOT, ledger and resolved
configuration for each active high bin using the pinned semantic validator.
Storage reservation uses 1 GB per high worker, its ceiling of 80, ordinary
workers' 250 MB bound, and the 50 GB free-space reserve. Consult
`../SHIFT_ANALYSIS.md` for the remaining physics gates. These managers and
monitoring continue after lxplus logout.
