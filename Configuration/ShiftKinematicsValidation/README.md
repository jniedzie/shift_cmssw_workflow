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
