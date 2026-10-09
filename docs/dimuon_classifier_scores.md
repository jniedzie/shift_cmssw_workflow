# Dimuon vertex BDT scores in v10 Nano files

The frozen `uniform_bdt_3p0_s71` benchmark is ready to produce a diagnostic
score column. Its mass/vertex/lifetime acceptance has not passed the physics
gates, so this production applies no BDT selection. The development record is
[DIMUON_VERTEX_CLASSIFIER.md](../../CMSSW_17_0_0_pre4/src/PhysicsTools/ShiftDimuonClassifier/DIMUON_VERTEX_CLASSIFIER.md).

All classifier inputs already exist in the v10 Nano files. The v10 replay
worker publishes Nano, completion receipts and evidence, without retaining the
SM AODs. Adding the score directly preserves the exact reconstructed sample,
sampling probabilities and weights. An AOD-to-Nano rerun is unnecessary for
this addition and would require restoring the original sampling metadata.

## Score and preservation contract

- `Events.ShiftDimuonVertex_bdtScore` is a variable-length Double branch, with
  exactly `nShiftDimuonVertex` entries in existing pair order. Zero-pair events
  have empty arrays and remain in the file. No event or pair is selected.
- Prediction reads reconstructed fields only. It never reads generator
  matching, truth, weights or the explicit mass nuisance target. Nonfinite
  features use frozen training medians; malformed pair references fail the
  file rather than silently discarding candidates.
- The explicit JSON forest reproduces all 3,494 comparison scores bit-for-bit.
  Production inference does not unpickle models or import sklearn/SciPy.
  Float32 score storage is avoided because it changes one tied benchmark cut.
- The original file is copied before appending. Every original event branch
  value/type, non-Events key payload and ROOT streamer schema is verified.
  ROOT TObject bookkeeping bits may change; type/checksum/schema fields must
  match. Original v10 files are never modified.
- ROOT metadata and a separate receipt identify the model and source digests,
  the historical threshold, Float64 storage and explicit provisional/no-cut
  status. The score is not a calibrated physical probability.

## Preparing and testing production

**Current storage gate:** the bulk attempt `12879017` is held because v10
payloads were relocated during the independently authorized EOS migration.
The classifier did not fail. Do not release/resubmit the old frozen deployment
or publish to its former output tree. Preserve its successful outputs and
failure evidence. A new deployment must freeze a completed migration map and
the canonical storage resolver, retain the logical receipt identities, and
use the dated process-bin layout. An incomplete migration map is a hard gate.

Enter the read-only LCG environment for export/scoring. No CMSSW build or
reconstruction configuration changes are needed. The reusable scorer is
`PhysicsTools/ShiftDimuonClassifier/add_bdt_score.py`, beside the feature and
portable-inference modules. Model files and all runtime artifacts remain
outside Git.

From the workflow repository, prepare a **new** frozen deployment:

```bash
python3 scripts/prepare_dimuon_score_production.py \
  --inventory ../validation/histogram_rerun_20261009/histogram_inputs.txt \
  --model ../CMSSW_17_0_0_pre4/src/PhysicsTools/ShiftDimuonClassifier/artifacts/portable_bdt_v1/uniform_bdt_3p0_s71.json \
  --migration-manifest ../validation/storage_reorganization_20261009/path_map.json \
  --campaign shift_detector_20261009_v10_bdt_v2 \
  --output ../validation/dimuon_score_production_20261009_v2 \
  --expected-files 10060 --prepare-condor
```

This command refuses preparation while the migration guard is incomplete.
After validation, it snapshots the exact map and canonical storage helper.
Original receipt paths remain logical identities; the frozen map supplies the
transport paths. Outputs use
`PROCESS_BIN/shift_detector_20261009_v10_bdt_v2/nanoAOD/nano_jobXXXXXXX.root`,
with score receipts, logs and completion markers under the campaign's
`metadata/bdt_scores/jobXXXXXXX/`. Startup verifies the map digest, and each
file checks that the migration state has not changed.

The 2026-10-09 frozen deployment is
`validation/dimuon_score_production_20261009/`. Its inventory contains 9,911
completed v10 files in all 19 SM bins. The 149 missing files are explicitly
recorded; this inventory does not establish completion of the whole campaign.
Defaults are 50 files per batch (199 batches), one CPU, 2 GB memory/scratch and
the LCG_108 EL9/GCC13 view. All sources, the model/parity receipt, inventory and
runtime setup are pinned by SHA-256. `pilot.sub` selects one existing file per
SM process without conditioning on observables or candidate presence.

The generated worker stages source Nano and its original completion receipt
through XRootD, verifies the published checksum, writes a distinct scored
copy, then publishes Nano, its score receipt and its scorer log. It reads back
all checksums before publishing the final completion marker. Exact completed
publications can be resumed; unknown EOS state, orphan outputs or changed
provenance stop without overwriting anything. Failure archives and pending
file identities are retained for recovery.

Submit only the pilot first:

```bash
condor_submit ../validation/dimuon_score_production_20261009_v2/pilot.sub
```

The historical pilot `12878715.0` passed with exit code zero, 60 events, one pair
and verified source/output/receipt/log checksums. The full 199-batch campaign
was submitted as `12879017` and subsequently held during source relocation;
do not submit a duplicate. Submission receipts are
`pilot_submission.json` and `production_submission.json` beside the frozen
plan. Check returned batch statuses and EOS completion markers for progress.
At the interruption, 15 materialized jobs were held and 13 distinct scored
files had been published. Its original intended EOS tree was
`shift_detector_representative_20261007_v10_bdt_v1`, alongside the original
v10 tree. The source files remain the normalization authority; their logical
old paths must now be resolved using the audited relocation map.

The migration-aware preparer passed 19 tests. The actual incomplete workspace
map was checked: preparation exits nonzero before creating a deployment or
submitting anything. The check is preserved as `migration_gate_check.json`
beside the historical plan. The revised preparation command above has not
been launched against a completed map yet.

The local five-file pilot preserved 82 events and ten dimuon rows across
J/psi, DY, QCD, a zero-pair QCD file and a 50 GeV signal file. All original
content and all 32 original ROOT streamer schemas per file passed validation.
The receipt is
`PhysicsTools/ShiftDimuonClassifier/artifacts/scored_nano_pilot_v2/pilot.json`.
These are software checks; a physics cut and common N_eff require the separate
mass/lifetime and per-process acceptance validation.
