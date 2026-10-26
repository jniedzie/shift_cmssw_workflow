<!-- Code style: human-code. -->
# Saved GEN primary precision

This opt-in package validates saved GEN with the actual HepMC-to-Geant4
primary converter, without tracking particles or changing the event.
`prepare_dark_photon_kinematics_preflight.py` prepares its standalone worker.
It runs the original signal replay and original generator cuts on every
accepted GEN event, including events rejected by material sampling. The
receipt pins the original plan, seal, GEN hashes, ordered event identities,
resolved configurations and replacement runtime.

The accompanying patch belongs in a qualified **private** runtime. Do not
apply it to a shared live CMSSW release. It leaves the source four-vectors,
generated masses, converter class layouts and momentum setters unchanged.

The numerical guard uses `s = max(1 GeV, event energy scale, E, E_on)`,
`a = 32 epsilon_double s` and an invariant uncertainty `4 a s` in GeV², retaining all four signed
square terms of the invariant.
Since `E_on² - E² = (E_on - E)(E_on + E)`, the allowed energy residual is
`min(1e-7 max(1 GeV,E,E_on), max(a, 4 a s/(E+E_on)))`.
At zero denominator the propagated term is `a`. Nonfinite values, negative
mass, energy below mass and an overflowing numerical envelope are rejected.
This accounts for event-scale conditioning in native fragmentation and
boosts while retaining the existing conservation cap. It does not use the
much looser generator mass-tolerance settings.

On 24 October, all 13 failed DP events had an ordinary hadron or photon
whose residual exceeded the previous energy-only bound. The same vectors
already existed in native `generator:unsmeared`, before source placement.
All 11 preceding successfully simulated event controls passed the old bound.
The largest observed residual was 1.55e-9 GeV, compared with an old bound of
4.83e-11 GeV. Its native pion decay still conserved four-momentum to about
1e-15 GeV. This is a numerical representation defect, not evidence for a
change to detector conditions or signal physics.

The native regression contains 36 exact failed/control vectors and 114
threshold, soft, highly boosted and malformed cases. Passing these tests
does not qualify a runtime by itself. A full accepted-GEN scan and paired
weighted detector-chain tests must pass before production migration.
The canonical production status and qualification evidence are recorded in
`SHIFT_BSM.md` at the workspace root.

A later native Pythia 8.317 audit found four additional final-string-pair
hadrons whose stored vectors exceed the v3 envelope. An instrumented replay
reproduced them exactly. An 80-digit decomposition of the final-pair solve
separates the scalar, basis-metric and final-vector arithmetic contributions;
the sum of their absolute energy contributions supplies each particle's bound.
This is an evaluated bound for those exact particles, not a universal increase
in the generator tolerance. The source implementation is
[StringFragmentation.cc, Pythia 8.317](https://gitlab.com/Pythia8/releases/-/raw/pythia8317/src/StringFragmentation.cc).

The optional private `recorded_primary_exact_authority.patch` applies after the
v3 invariant patch. Copy `interface/RecordedPrimaryParticleAuthority.h` into
the private release's `SimG4Core/Generators/interface/` first. Rebuild the
Generators library, this validation plugin and both native tests in that
private release; the patch adds JSON/OpenSSL dependencies and no Generator
members or class-layout changes. Never apply it to the shared live release.
Run the existing `testRecordedPrimaryPrecision`, then
`python3 test/testExactParticleAuthority.py /absolute/path/to/testRecordedPrimaryAuthority`.

The runtime capsule pins the compact authority by SHA-256. Its activation uses
`SHIFT_RECORDED_PRIMARY_AUTHORITY`,
`SHIFT_RECORDED_PRIMARY_AUTHORITY_SHA256` and
`SHIFT_RECORDED_PRIMARY_GEN_SHA256`; the last comes from the unchanged full GEN
inventory for the point being processed. A row must match that GEN hash,
HepMC event number, barcode, PDG ID and the exact binary64 bits of all four
momentum components and the generated mass. Hexadecimal doubles retain signed
zero and avoid decimal-rounding ambiguity. Unknown particles or mismatched
inputs retain the v3 rejection.

Only a matched particle rejected by v3 can use its independently derived
bound, plus four double epsilons of the energy scale for the final `hypot`
evaluation. The existing `1e-7` conservation cap remains an upper bound. Its
transport energy becomes `hypot(px,py,pz,m)`, while the three-momentum,
generated mass, raw GEN, identities and weights stay unchanged. The original
v3 setter path remains unchanged for all other particles. The native converter
log and preflight receipt record every such reconciliation. The 10 nm
undefined-hadron flight guard is unchanged.

The separate `reviewed_tiny_flight.patch` is an optional, source-specific
transport approximation. Copy `interface/ReviewedTinyFlight.h` beside
`PromptDecaySupport.h` in a private v4 runtime before applying it. It retains
the ordinary 10 nm budget and only accepts explicitly reviewed charged-D*
records up to 15 nm. The pinned authority's `reviewed_tiny_flights` rows bind
the original GEN hash, event number, barcode, PDG, status, binary64 momentum,
mass, birth/end vertices and daughter barcodes. All other records retain the
existing guard. Daughter vertices and their subsequent transport remain intact.

This is not an exact D* material model or a numerical-precision repair.
Deployment requires an explicit physics approval, complete original-GEN
converter scans, failed-event full chains and paired successful controls.
Keep the affected event IDs and absolute W/p contributions as an event-response
uncertainty. The absolute bound applies to event indicators; it is not a small
relative uncertainty on rare bins or a bound on arbitrary pair multiplicities.
Do not omit the flagged events from exposure or quietly discard this uncertainty
when producing limits. The October 26 tests and decision are in `SHIFT_BSM.md`.

The native regression is `testReviewedTinyFlight`. Run its isolated-process
checks with `python3 test/testReviewedTinyFlight.py /absolute/path/to/testReviewedTinyFlight`.
Also retain the original prompt-tree, hadron-source and signal-decay controls.
