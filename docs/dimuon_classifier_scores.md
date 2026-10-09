# Dimuon vertex BDT scores in v10 Nano files

## Current merged-group first test and continuation (2026-10-09)

The active first test uses 501 existing SM merged group files (up to 20 Nano
files per group), plus two completed prompt signal files. Every group keeps
its own scored ntuple and histogram output. No whole-bin event tree is made.
Only histogram files are merged by process-bin at the final step.

The user clarified that N_eff is the effective MC normalization fitted to
data. AUC and fixed-cut BDT efficiency are the classifier metrics; neither is
called N_eff. The new performance tool reports both empirical and correctly
weighted MC-mixture ROC, proper-c-tau/mass maps, uncertainties and support.
Seven signal points currently have no completed detector ntuple and remain
unmeasured. Uniformity is a validation target, not an established result.

The older individual-file campaign `12879319` was reversibly held at the
user's request to use small merged groups; its 20 materialized jobs are held.
Outputs, logs and frozen deployments are retained. Do not release it as part
of this first test.

Grouped campaign `12879497` uses `deployment_v2`. Its local four-group canary
passed 1,209 events and 58 pairs, including standard and BDT histogramming.
Two original merged-file layouts exposed a ROOT writer limitation before
publication. The repaired metadata-only writer passed both actual failed-file
probes and six streamer/four original scoring regression tests.

`deployment_v3` freezes that repair and proves the same 503 source/destination
rows, model, predictors, labels, weights and histogram definitions before
reusing verified v2 publications. Existing published files are never rewritten.
Its five-group recovery canary passed all groups 10--14 with no pending input;
the proof is `recovery_canary_batch_status.json`.

Control/state and source inventory are under workspace
`validation/dimuon_merged_score_test_20261009/`. A finite continuation watcher
waits for the original grouped jobs to drain, holds only the obsolete grouped
cluster, then submits `deployment_v3/production.sub` once. Query failures are
unknown state; ambiguous submissions stop without a duplicate. Its state and
submission receipts are under `continuation/`. Reconcile those receipts and
the queue before any manual recovery submission.

When all 101 recovery batch statuses are complete, run the histogram-only
merger with the sealed plan SHA printed in `deployment_v3/plan.json` receipts:

```bash
source /cvmfs/sft.cern.ch/lcg/views/LCG_108/x86_64-el9-gcc13-opt/setup.sh
python3 scripts/merge_merged_dimuon_bdt_histograms.py \
  --plan ../validation/dimuon_merged_score_test_20261009/deployment_v3/plan.json \
  --plan-sha256 9fb387d477c10f5c3ba99205a866cd4fcb475b65deca0d741f33c86e22fad6b5 \
  --results-directory ../validation/dimuon_merged_score_test_20261009/deployment_v3/results \
  --work-directory ../validation/dimuon_merged_score_test_20261009/histogram_merges
```

The merger requires all 503 exact-plan publications, verifies inputs and
creates 19 SM bin plus two signal-point histogram outputs. It never consumes
event trees as histogram inputs. `bin_histogram_inputs.json` then feeds
`tea_shift_cmssw/utils/prepare_shift_bdt_plots.py`; per-bin sigma/full GEN sumw
is applied before process shapes are combined. The dedicated plot config is
`shift_bdt_plotter_config.py`, selected through `SHIFT_BDT_PLOT_INPUTS`.

Performance manifest preparation is preserved beside the source inventory.
Once all outputs are validated, run the CMSSW classifier's
`bdt_performance.py` with `performance_inputs.json`, the frozen portable
model, `artifacts/phase2_seed71_72`, and a new output directory. Inspect both
fixed-cut efficiencies and AUC. The two available prompt points have too few
genuine vertices to establish closure; pending lifetimes must not be
interpolated into a uniformity claim. See the task log for exact commands and
the continuation checklist.

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

**Historical individual-file launch:** the migration map is complete and its lossless audit
passed. A fresh deployment was prepared as
`validation/dimuon_score_production_20261009_v2/`; its three-file pilot is
Condor `12879278.0` and passed independent readback. Full production is
submitted as `12879319`: 199 batches covering 9,911 input files. At
12:47:36 UTC on 2026-10-09, 12 jobs were running and five were idle, with no
holds. This records a launched campaign, not 9,911 completed outputs.

The historical bulk attempt `12879017` remains held after source relocation.
Do not release/resubmit its frozen deployment or publish to its former output
tree. Its 13 scored publications survived migration with exact hashes; see
the historical deployment's `concurrent_additions.json`. New deployments
freeze the completed map and canonical storage resolver, retain the logical
receipt identities, and use the dated process-bin layout. An incomplete or
changed migration map is a hard gate.

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
beside the historical plan. After migration completed, the revised command
above sealed all 9,911 inputs and 199 batches using the audited map. The fresh
pilot completed three publications with 60 events and one pair. Independent
native XRootD readback verified all 12 Nano/receipt/log/marker objects and the
actual Double score arrays. It terminated normally with exit code zero in
473 seconds. Reported peak RSS was approximately 1,465 MB against the site's
3,000 MB allocation; the submit file requested 2,000 MB. PSS was unavailable.
The proof is `pilot_readback.json` in the v2 deployment. Full submission is
recorded in its `production_submission.json`; do not submit a duplicate.

The local five-file pilot preserved 82 events and ten dimuon rows across
J/psi, DY, QCD, a zero-pair QCD file and a 50 GeV signal file. All original
content and all 32 original ROOT streamer schemas per file passed validation.
The receipt is
`PhysicsTools/ShiftDimuonClassifier/artifacts/scored_nano_pilot_v2/pilot.json`.
These are software checks; a physics cut and common N_eff require the separate
mass/lifetime and per-process acceptance validation.
