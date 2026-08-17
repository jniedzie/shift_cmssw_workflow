# SHIFT detector-ablation presets

Source exactly one preset before running Step 3 and Step 4.  Each preset sets a
stable variant name/code and writes to `step3_<variant>` and `step4_<variant>`,
with matching configuration and log directories.

The controlled comparisons are:

- `reference` versus `dt_off` or `dt_direct` for DT;
- `reference` versus `tracker_general` or `tracker_p5` for tracker matching;
- `reference` versus `gem` for GEM measurements in the DSA builder;
- `reference` versus `hcal` or `zdc` for truth-attributed calorimeter
  diagnostics;
- `all` as an integration test, not as the automatic production choice.
- `detector_integration_v2` is the guarded second-generation integration test:
  extended timing, covariance-aware DT/tracker RecHit augmentation, full
  calorimeter/ZDC association diagnostics, and a dedicated early-ZDC Step-2
  digitization stream. It never changes the baseline precision refit unless a
  tracker measurement is actually accepted.
- `detector_integration_v3` is the validated successor: it explicitly enables
  the Phase-I ZDC digitizer, uses covariance-selected DT augmentation, carries
  per-muon tracker truth, and adds propagated calorimeter/ZDC diagnostics. It
  uses a new Step-2 identity so the older empty-ZDC digi files cannot be reused.

CSC and RPC remain enabled in every preset.  HCAL and ZDC are diagnostic
associations in this iteration and do not enter the precision Kalman fit.

Example:

```bash
source config/reco_variants/dt_direct.env
./run_step3_aod.sh 0 10
./run_step4_exonanoAOD.sh 0 10
```
