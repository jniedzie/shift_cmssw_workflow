<!-- Code style: human-code. -->
# Dark-photon GEN with spectral decay widths

For masses between 2 and 12 GeV, use
`scripts/run_dark_photon_spectral_gen.py`. This keeps the native Pythia
Drell–Yan process, but takes the physical total decay width and dimuon
branching fraction from the pinned DarkCast 2.1 spectral model.

The native Pythia process samples a larger common coupling so its resonance
width can be resolved. Its natural width is retained. The physical decay
length is set separately from the complete spectral width:

`cτ = ħc / Γ_total`, and `mean lab flight = mean βγ × cτ`.

The native selected cross section already includes its own dimuon branching
fraction. Apply this correction once:

`(epsilon / sampling_epsilon)^2 × BR_physical(μμ) / BR_native(μμ)`.

The contract records both factors, the two width authorities and their source
hashes. GEN weights remain native unit weights. Keep every accepted GEN event,
including parents that decay outside the detector region. The output counts
accepted events, retains their original event numbers and allows at most ten
framework input attempts per requested output event. Pythia tried, selected
and accepted counters remain a separate ledger.

Example, from the workflow directory inside the existing built CMSSW runtime:

```bash
python3 scripts/run_dark_photon_spectral_gen.py \
  --mass 2 --epsilon 1e-7 --sampling-epsilon 0.01 \
  --darkcast-path ../validation/dark_photon_lifetimes_20261013/external \
  --darkcast-source-receipt ../validation/dark_photon_lifetimes_20261013/darkcast_source_receipt.json \
  --events 100 --seed 325102001 --run-number 425102001 \
  --output /tmp/new-spectral-gen-reference
```

Use a new, reserved seed/run namespace and output directory for each run.
The numbers above belong to the completed 2026-10-13 reference and must not
be reused for a new population. Retain the external provider's original
license and authorship files; do not commit third-party payloads.

Use `scripts/prepare_dark_photon_spectral_grid.py` to freeze a mass and lab
flight grid, or pass `--epsilons 1e-6 1e-4 1e-2` for a Cartesian mass/epsilon
grid. The two column options are mutually exclusive. The default sampling
epsilon is 0.01; set `--sampling-epsilon` explicitly when a new validated
proposal needs another value. New point names use mass and reference mean lab
flight, for example `m2_lab_100m`; the physical epsilon and original selector
remain in metadata. It uses the standard detector partitioning and plan writer,
copies the external width provider into the preparation, and records its
hashes. The standard production preparation and controller then handle
detector jobs, same-point ntuple grouping, histogramming and histogram merging.

This backend supplies only Drell–Yan production. Meson decays and proton
bremsstrahlung are absent. Below roughly 1.4 GeV, partonic Drell–Yan is not
a complete physical production model. The native resonance engine also
closes the dimuon channel below `2 m_mu + 0.1 GeV`, and collapses resonance
mass support for proposal widths below `1e-6 GeV`. Do not lower the shared
native mass guard to bypass these limitations. Threshold-to-1-GeV samples
need a separate meson/bremsstrahlung or validated narrow-width production
backend.

The explicit `--experimental-low-mass-dy` option admits 1.4--2 GeV GEN-only
diagnostics while retaining those native support checks. The default remains
2--12 GeV, and the resulting contract records the experimental scope. The
reserved 1.8/1.5 GeV pilot configuration is in
`config/campaigns/dark_photon_low_mass_20261016.json`. This option does not
establish a complete production model or authorize extrapolating reconstructed
efficiencies below the simulated mass range. The separate meson decay kernel
and its tests cover kinematics and branching weights; parent-source rates,
form factors and detector integration remain required.

Completed low-mass GEN diagnostics can use the standard detector, ntuple
grouping and histogram pipeline. Preserve their DY-only scope and full source
denominators when inspecting that detector response.

DarkCast uses photon-like spectral widths in this mass range; the native
proposal uses the full hypercharge-mixing couplings. The small neutral-current
width correction, production theory uncertainties and signal-rate closure
still need evaluation before using these samples for epsilon limits. A passed
runtime audit establishes the implemented event, lifetime and weight contract.

The explicit `--experimental-large-epsilon` option permits epsilon up to 0.1.
For those points only, the spectral runner constructs a frozen DarkCast model
with the full hypercharge vector and axial fermion couplings, including
neutrinos. It records the exact neutral mass matrix, coupling card, complete
partial widths and comparison with the photon-like width. The native runner
supports the same option above 12 GeV and checks its natural widths separately.
The default epsilon bound remains 0.01. These remain pure on-shell DY samples
in the recorded mass window; larger mixing, finite-width effects and gamma/Z
interference need separate qualification before a sensitivity interpretation.

References: [DarkCast release](https://gitlab.com/darkcast/releases),
[spectral-width method](https://arxiv.org/abs/1801.04847),
[hypercharge mixing and widths](https://arxiv.org/abs/1412.0018),
[low-mass production mechanisms](https://arxiv.org/abs/2011.05115),
[Pythia resonance mass guard](https://pythia.org/latest-doxygen/classPythia8_1_1ResonanceWidths.html).
