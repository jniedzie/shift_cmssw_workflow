# Dimuon vertex BDT scores in v10 Nano files

## October 12 recovery and all-sample first look

The October 12 metadata audit found 494 of the 503 grouped publications
complete. The old cluster `12879497` had returned 97 successful batches and
four failures, then ended; its event log records the four failed jobs and
factory being removed on October 10. The old continuation never submitted
v3. Its expired state is retained unchanged.

After verifying the queue, factories, immutable deployment and repaired-writer
canary, recovery cluster `12901333` submitted all 101 sealed v3 batches once.
The nine missing groups were 17--19, 128--129 and 196--199. Existing outputs
are checksum-verified and reused. All 101 recovery jobs exited zero; the
complete 503-file set contains 187,254 events and 20,184 retained pairs.
New submission intent, receipt, scheduler
snapshots and finish logs are under
`validation/dimuon_merged_score_test_20261009/resume_20261012/`.

All seven formerly unavailable signal points have now been scored separately
in `validation/bdt_signal_extension_20261012/`: 140 events and 28 retained
pairs, with 18 genuine and 10 unknown classifier labels. Every original branch,
schema and recomputed score passed verification. Their native `genWeight` is
used once. This extension preserves the original 503-file deployment.

The completion runner validates all 101 successful statuses before preparing
the exact 503-input performance manifest and 21 histogram merges. The combined
first look adds the seven signal inputs: 510 files, 187,394 events, all 19 SM
bins and all nine signal grid points. Its output directory is
`validation/bdt_first_look_20261012/`. The SM population remains the frozen
9,911-job subset, excluding 149 jobs from the original detector-production
target. Signal statistics remain too small to establish mass/lifetime closure.

The plot preparer accepts the merger's `rows` inventory as well as the original
`samples` format, preserves logical EOS paths in provenance, and resolves the
equivalent mounted path for reading. SM legends identify the process rather
than the first contributing bin. Dedicated plot scopes are `sm` and `all`.
Separate genuine/accidental/unknown overlays show all retained pairs; numerical
ROC and fixed-cut efficiencies exclude the model's training/validation events.
No score selection is applied to any ntuple or histogram.

The completed `performance/performance.json` excludes 2,790 original
training/validation pairs and evaluates 11,832 genuine and 2,383 accidental
held-out SM pairs. Empirical AUC is 0.803 (95% event-bootstrap interval
0.792--0.812) against all accidental pairs, or 0.876 (0.843--0.903) against
the 107 accidental QCD pairs. At the unchanged threshold
0.4403188643890805, genuine retention is 10,510/11,832 (88.8%); accidental
QCD rejection is 70/107 (65.4%), and all-accidental rejection is 49.9%.

The weighted QCD comparison has only 12.8 effective independent negative
events and fails the required minimum of 20. Its AUC is a diagnostic, not
validated physics performance. The all-retained genuine-SM weighted shape
receives 96.5% of its weight from one genuine QCD pair; that pair is excluded
from held-out evaluation, which has no genuine QCD support. Signal points
have only 1--5 genuine direct-parent pairs each (25/26 survive in total).
Neither mass nor lifetime independence is established.

Within the first-look directory, start with
`plots_all/dimuon_bdt/ShiftDimuonVertex_bdtScore_shape.png`,
`label_separation_annotated/`, and the held-out ROC in `performance_annotated/`.
The latter rerenders the frozen completed report with explicit statistical
support annotations; the original evaluation and artifacts in `performance/`
remain unchanged.
The plot-validation receipts distinguish all-retained weighted shapes from
held-out performance and preserve the original rendering evidence.

The subsequent fixed-threshold study is under
`validation/bdt_acceptance_study_20261012/`. It reproduces all 510 first-look
inputs and compares the quality-only baseline with the frozen uniformity BDT
on identical held-out events. Quality-only does not cure DY mass dependence
and has stronger radius dependence. Keep the uniformity score diagnostic;
neither physics cut is validated. The independent QCD/label audit confirms
the dominant pair's common omega origin and sampling arithmetic while keeping
provisional source normalization, unknown matches and fit precision separate.

Signal stage DAG `12902110` adds 200 fresh GEN events at each of the nine
physical points. Ninety detector chunks feed nine exact complete-point unions,
then unchanged scoring and standard plus BDT histogramming. All events and
native weights remain; source exposure is recorded once. The full GEN audits,
resolved-config/two-event detector canary, empty/nonempty preservation probes,
publication tests, storage forecast and live submission receipt are in
`production/`. Its `HANDOFF.md` owns monitoring and reviewed recovery. Preserve
all sealed files, running jobs and output evidence. The original QCD v11 pilot
and bulk hold are unchanged. Additional signals remain validation-only; reserve
fresh independent SM parents before any classifier optimization.

## Historical merged-group first test and continuation (2026-10-09)

The following records the October 9 state and planned commands. The completed
October 12 recovery and evaluation above supersede its pending steps.

The first test used 501 existing SM merged group files (up to 20 Nano
files per group), plus two completed prompt signal files. Every group keeps
its own scored ntuple and histogram output. No whole-bin event tree is made.
Only histogram files are merged by process-bin at the final step.

The user clarified that N_eff is the effective MC normalization fitted to
data. AUC and fixed-cut BDT efficiency are the classifier metrics; neither is
called N_eff. The new performance tool reports both empirical and correctly
weighted MC-mixture ROC, proper-c-tau/mass maps, uncertainties and support.
Seven signal points had no completed detector ntuple at that checkpoint and
were unmeasured. Uniformity remains a validation target.

The older individual-file campaign `12879319` was reversibly held at the
user's request to use small merged groups; its 20 materialized jobs were held.
Outputs, logs and frozen deployments are retained. Do not release it as part
of this first test.

Grouped campaign `12879497` used `deployment_v2`. Its local four-group canary
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
was prepared to wait for the original grouped jobs to drain, hold only the
obsolete grouped cluster, then submit `deployment_v3/production.sub` once.
It expired without submitting v3. Query failures are
unknown state; ambiguous submissions stop without a duplicate. Its state and
submission receipts are under `continuation/`. Reconcile those receipts and
the queue before any manual recovery submission.

The planned histogram-only merge, gated on all 101 complete recovery batch
statuses and the sealed plan SHA, was:

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
The plan called for the CMSSW classifier's `bdt_performance.py` to consume
`performance_inputs.json`, the frozen portable model and
`artifacts/phase2_seed71_72`, writing fixed-cut efficiencies and AUC into a new
output directory after complete validation. At that checkpoint, the two
available prompt points had too few genuine vertices to establish closure.
Pending lifetimes could not support a uniformity claim. The completed
October 12 evaluation above still records the limited signal support.

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
