<!-- Code style: AI-code. -->
# Pinned proton form factors

These unchanged data tables and their BSD-3-Clause license come from
[SensCalc commit ca588864](https://github.com/maksymovchynnikov/SensCalc/tree/ca588864742ee83ae8fd3a987b9698ea75c19251/phenomenology/DP/Production%20probabilities/Form%20factor).
They are also the ancillary files of
[Foroughi-Abari, Reimitz and Ritz, arXiv:2409.09123](https://arxiv.org/abs/2409.09123).
The module `scripts/dark_photon_bremsstrahlung.py` pins their SHA-256 values.
Vendor tables retain their original headers and have no code-style tags.

The central, lower and upper complex form factors use SensCalc's interpolation
of log(F) against log(m), with first-order interpolation. They must not be
replaced by interpolation of absolute values or by a probability without the
Dirac-Pauli interference term. The source module implements the FR prescription
and keeps the older Baseline prescription separate. Both follow
`codes/LLP distribution/prod-pheno-bremsstrahlung-proton.nb` at the pinned commit.

Its output is a cross-section density in pb per epsilon squared, before the
dimuon branching fraction or detector response. Collision energy, the source
kinematic range and the off-shell scale must be recorded by the caller.
This kernel alone is not a qualified GEN source or an inclusive DP production.
