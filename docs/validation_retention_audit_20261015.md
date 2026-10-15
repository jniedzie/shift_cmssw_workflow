<!-- Code style: human-code. -->
# Validation directory storage audit

Audit date: 15 October 2026. **Do not remove the whole directory.** It contains
running production deployments, inputs used by current tools and published
results, and scientific data without verified replacement copies. Large
inactive payloads can leave AFS after verified archival and dependency updates.
The initial audit was read-only. The user later authorized the exact reviewed
removals recorded below; current production deployments were preserved.

## Completed cleanup

Executed at 13:25–13:26 CEST on 15 October after the user's explicit request.
All **44 whole directories and 21 subdirectories** in the reviewed combined
list are absent. The per-entry receipt records **32,526 files, eight symlinks
and 827 directories removed**, totalling **1,459,014,656 allocated bytes
(1.36 GiB)**. No additional removal scope was added.

`validation/` now occupies **2.90 GiB** (3,042,439 KiB), down from 4.26 GiB
immediately before execution. AFS quota usage fell from **69% to 67%**.
Concurrent production writes can affect the volume-wide change.

The retained 4.44 MiB archive contains all 1,079 required records. Its SHA-256
is unchanged after deletion. Fresh checks of both account schedulers passed
at 13:26 CEST: no job references a removed scope and all previously required
inputs remain. Current BSM/QCD/limits inputs, the ordinary-QCD fallback,
selected-model snapshots, project plots and the four protected old roots
listed below remain in place.

The [execution result](validation_retention_audit_20261015/execution_20261015/deletion_result.json),
[frozen inventory](validation_retention_audit_20261015/execution_20261015/manifest.jsonl.gz)
and [per-entry deletion receipt](validation_retention_audit_20261015/execution_20261015/deleted.jsonl.gz)
record the completed operation. The execution evidence directory also holds
the preflight and post-deletion checks. This completed status supersedes the
proposal and preparation status in the historical sections below.

## Further removals after the first cleanup

Checked at about 13:12 CEST on 15 October. The user removed the main large
directories from the preceding proposal. The remaining validation contents
occupy about **4.25 GiB**, compared with 11.50 GiB in the first inventory.
The workspace AFS volume reports **69% used**. There are still 103 top-level
entries, including ordinary files as well as directories.

**The reviewed second proposal covered 44 whole directories and 21
subdirectories, with an estimated net saving of 1.35 GiB. It has now been
executed as recorded above.**
The [whole-directory list](validation_retention_audit_20261015/remaining_whole_directory_removals.txt)
contains absolute paths to the 44 parents. The
[subdirectory list](validation_retention_audit_20261015/remaining_subdirectory_removals.txt)
contains only the 21 disposable descendants; their parents must remain. The
[combined list](validation_retention_audit_20261015/remaining_directory_removals.txt)
and [sizes and reasons](validation_retention_audit_20261015/remaining_directory_removals.csv)
cover all 65 nonoverlapping scopes. This section records the review before
execution.

Preparation is complete for this list: **1,079 required records have been
retained in a verified 4.44 MiB archive**, including old accounting and deletion
receipts, closed-study summaries, original high-bin scientific metadata and the
post-retirement night-controller pointer. Every retained archive member was
reopened and compared with its source SHA-256. The six old historical accounting
directories are preserved in full, including their original member paths.

The archive is
`validation_retention_audit_20261015/remaining_retained_records_20261015.tar.gz`.
Its compressed member/hash manifest records the original workspace-relative
paths. Extraction can restore those retained records; it cannot restore the
discarded experimental ROOT, training or build payloads.

The largest additional whole-directory removals are:

| Path below validation | Allocated MiB |
| --- | ---: |
| `qcd_selection_study_20261012` | 403.1 |
| `qcd_night_reco_20261014_v1` | 249.2 |
| `bsm_plot_inputs_20261013` | 134.8 |
| `bsm_sm_plots_20261012` | 69.2 |
| `bdt_discrimination_improvement_20261012` | 65.0 |
| `lss_reco_20260916` | 38.2 |
| `retention_20260922` | 25.4 |
| `high_bins_20261001` | 19.7 |

Two earlier exceptions are cleared by inspecting what current code actually
opens. The selected Vertex model's old BDT paths are `original_path`
attribution fields; its actual `path` fields use byte-identical snapshots
already retained under TEA plots. The old QCD geometry defaults are used only
by retired initial-review tools, not current production or the independent
reference. Thus those two parent directories can go after preserving their
compact summaries. No active helper or geometry-array migration is needed.

The night directory is also cleared: the campaign is retired, has no jobs and
has no current controller/observer/quota reader. Its final disabled-restart
pointer and closeout records are now retained outside it. The earlier copy in
the new QCD retirement directory was a pre-retirement snapshot, so this audit
preserved the later pointer explicitly.

Fresh checks established that the first cleanup removed **no required active
inputs**. Regenerated BSM dependencies and QCD manager frozen inputs exist;
30 absent BSM paths are expected future completions already absent before the
cleanup. Both account schedulers were queried successfully after a transient
security-query retry: at 13:03 CEST `bigbird26` had 316 running and four held
jobs; `bigbird14` was empty. No local scientific process held validation files.
The proposed removals have no intersection with the current required BSM
files or active production/limits deployment roots. Current Codex sessions
still own the SM/QCD and BSM productions and the unfinished limits; the
AI-code commit task has completed.

Keep the current BSM/QCD/limits inputs, the usable ordinary-QCD fallback and
their required metadata. Also keep these four old directories out of the
removal list:

- `cms_lss_20260922`, `lss_reco_20260909` and `lss_reco_20260910` contain private
  geometry payloads without verified copies in their mandatory private storage.
- `cleanup_20261009` holds the unique 67.9 MB archive of 669 detailed high-bin
  worker accounting receipts. That archive needs a verified retained copy
  before this directory can go.

The `remaining_` evidence files record this check and the preserved records.
Historical diagnostics retired by this cleanup lose their old default input
paths; current production, selected models and final project plots stay in
place. This section supersedes the earlier three-parent hold list and the
earlier pending-preservation status for scopes included in the new lists.

## Proposed cleanup using active tasks

Updated at 12:50 CEST following the user's request to retain active tasks and
retire everything else. **The concrete proposal covers 8.36 GiB gross across
56 paths, with a target of roughly 8 GiB removed after small required records
and exceptions are retained.** This is deletion of closed experiment payloads;
archiving all of them elsewhere is not a condition of the proposal. It
supersedes the broad historical-reference retention assessment further below.

The other Codex sessions and their latest task events identify ongoing SM/QCD
production, BSM production/publication, unfinished BSM limits and the current
AI-code review. Older vertex-training and plot-software tasks have completed.
Completion of a chat turn alone does not retire its detached production.
Both account-advertised schedulers were checked: at 12:43 CEST `bigbird26`
had 324 jobs, and at 12:44 `bigbird14` had zero. The old QCD night campaign is
explicitly retired, its restart pointer is disabled and no matching job remains.

| Proposed retirement below validation | Gross GiB | What goes |
| --- | ---: | --- |
| `qcd_tail_followup_20261013` | 4.14 | Closed proposal/decay/closure experiments, old native reference GEN, completed tracking/cleaner diagnostics, copies and runtimes. |
| Selected parts of `qcd_gen_recovery_20261014` | 0.88 | Source slices, build copies, old trace variants and completed cleaner replays. Keep returned fallback science and its accounting. |
| `qcd_selection_study_20261012`, `qcd_acceptance_study_20261012` | 0.74 | Superseded feature extraction and acceptance experiments; retain four small geometry/helper inputs still referenced by the enrichment audit. |
| `qcd_tail_followup_20261014`, `qcd_tail_continuation_20261014` | 0.50 | Closed replay/fit/duplicate-cleaner experiments, retired together with their old cross-links. |
| `qcd_night_reco_20261014_v1` | 0.24 | Retired draw worklists and old worker/supervisor deployments. Keep the disabled restart pointer and retirement records. |
| `qcd_gen_recovery_20261015`, old v11 `sources` | 0.23 | Closed recovery probes and cached input slices; preserve fallback production/accounting records. |
| Three old `dimuon_*score*` production/test roots | 1.15 | Local histogram merge scratch, obsolete deployments, path-map copies and resolved failure bundles. |
| Old BDT, mixing proof of concept and software-test targets | 0.48 | Superseded training/evaluation arrays, pair tables, profiles, duplicate renders and one-off probes. |
| **Total** | **8.36** | **About 8 GiB net is the proposed deletion target.** |

The [exact 56-path table](validation_retention_audit_20261015/active_task_cleanup_targets.csv)
and [proposal record](validation_retention_audit_20261015/active_task_cleanup_plan.json)
define the scope. Paths are retirement scopes, not instructions to remove an
entire parent directory without applying the protected-file exceptions.
The candidate scopes contain 47,542 files in the original complete inventory;
sizes must be refreshed when the deletion manifest is built.

Keep these current inputs and results:

- Current BSM production, aggregate, publisher and their exact sealed inputs,
  including older baseline metadata and required canary payloads. The exact
  live protected set has no intersections with the proposed retirement scopes.
- Current mu-enriched GEN manager/supervisor/reference, detector checks and
  their operational/accounting gates; held and unfinished limits deployments;
  the active code-review workspace.
- The usable ordinary-QCD fallback, full GEN normalization, original identities
  and sampling probabilities. In particular, keep the returned tail and repaired
  baseline result archives, about 0.83 GiB. They are excluded from the savings
  above; verified EOS migration could free that additional AFS space later.
- The selected `Vertex_best` model, final figures and current EOS ntuples and
  histograms. Retain four tiny old BDT helper source paths used by its metadata,
  or migrate those references with verified copies before removal. Keep the
  three old BDT production records still used by the live BSM seal.
- Only the compact provenance/accounting and deletion receipts needed by those
  retained results. Old experiments do not need complete runtime/sandbox copies.

The [keep-path record](validation_retention_audit_20261015/active_task_live_keep_paths.txt)
lists minimum exact protected files. Active deployment directories and the
remaining ordinary-QCD fallback are protected in the supporting live/QCD
records as well. The list is not a claim that every other file may be deleted.

Before execution, refresh the queue/session/consumer check, retain the stated
exceptions and compact records, and verify the canonical EOS publications
corresponding to the old local histogram merge scratch. Current BSM already
owns the byte-identical deployed BDT model. Historical diagnostic utilities
that default to retired validation paths must be marked retired or given
explicit maintained inputs; that coupling is not grounds to retain all bulk.

The old five-file native reference is now eligible for deletion: current
mu-enriched workers and their independent reference do not consume its ROOTs,
and the old preparer's needed config/library have already been copied into
the enrichment workspace. Its old detailed study, like the other closed
experiments in this proposal, would no longer be replayable from its original
payloads. Persistent conclusions and current required accounting survive.

At that proposal stage, no payload deletion or EOS migration had been performed. The new evidence
files use the `active_task_` prefix. Other remaining inactive directories are
not automatically approved: historical private geometry variants still follow
the mandatory private-copy rule, and old production metadata can remain an
input to the current BSM publication graph. This 8 GiB pass does not wait for
those smaller follow-up cases.

## Size and coverage

The complete file inventory at 11:43 CEST contains **82,648 regular files**,
6,730 directories and 28 symlinks under 113 top-level entries. It accounts for
**11.50 GiB allocated** and 11.44 GiB of file contents. Traversal reported no
errors and did not follow symlinks. The independent `du -sk validation` result
was 12,055,872 KiB; small differences reflect directory accounting and live
writes. The AFS volume reported 76% of its 100 GiB quota used.

Two new top-level directories appeared while the audit ran:
`qcd_mu_enriched_production_20261015_v1` and `ai_code_review_20261015`.
They are separately recorded and protected as concurrent work; a local
reference job was writing into the former. Other entries can also grow while
jobs run.

The audit combines a complete path/size inventory, canonical-document links,
maintained source and plot metadata references, targeted payload inspection,
selected duplicate hashes, and current scheduler/local-process observations.
It does not certify every ROOT file's physics content or prove the absence of
consumers on every scheduler and login node. The classifications below are
retention decisions and review priorities, not a deletion allowlist.

| Largest entry below validation | Allocated GiB | What matters for retention |
| --- | ---: | --- |
| `qcd_tail_followup_20261013` | 4.143 | Independent GEN references, proposal/decay accounting, transformed graphs and detector/closure evidence; also repeated configs and preserved copies. |
| `qcd_gen_recovery_20261014` | 1.749 | Returned RECO/Nano archives, exact draw/source inputs and full GEN normalization. |
| `dimuon_merged_score_test_20261009` | 0.809 | Scored-production plans/models, merge outputs, old deployments and failure archives. Review individual bundles. |
| `qcd_selection_study_20261012` | 0.394 | GEN feature extraction and comparison evidence. Preserve identities/selection provenance; archive large arrays. |
| `cms_lss_20260922` | 0.358 | Geometry variants and conversion/transport audits. Private payload storage rule applies. |
| `qcd_acceptance_study_20261012` | 0.342 | Independent sampling and conditioned-decay evidence. |
| `dimuon_score_production_20261009_v2` | 0.283 | Sealed scoring records; one frozen storage-path map alone is 221 MiB. |
| `qcd_tail_followup_20261014` | 0.281 | Detector/reconstruction evidence, with symlinks back to earlier QCD inputs. |
| `mixing_poc_20261009` | 0.251 | Mixing row augmentation and closure records; preserve unique identities and weights. |
| `bsm_scale10_20261014` | 0.245 | Live detector, aggregation and publication deployment. |
| `qcd_night_reco_20261014_v1` | 0.243 | Frozen draws, worker/supervisor deployments, recovery and operational gates. |

These eleven entries account for about 79% of allocated space. The
[complete directory table](validation_retention_audit_20261015/directory_inventory.csv)
lists every inventoried top-level entry, size, file count, disposition and
reference counts. Additional rows record directories created during the audit.
Size units in the CSV are bytes.

## Live files that must stay in place

At 11:43:34 CEST, the verified assigned scheduler `bigbird26.cern.ch` contained
103 jobs: 102 running and one held. The snapshot established these dependencies:

- `bsm_scale10_20261014/production`, `aggregate` and `controller`: 99 detector
  workers, two DAG managers and the publication controller.
- `bsm_limits_resource_diagnosis_20261015/canary_m15_combined90`: a held canary.
- A local `cmsRun` was still writing under
  `qcd_mu_enriched_production_20261015_v1/independent_reference/` at 11:46:45
  CEST. Its launcher reads `qcd_mu_enrichment_20261015_v1` too.

The live BSM aggregation/publication also reads older campaigns' plans, seals,
completion receipts and full generation metadata. Protected inputs occur in
`bdt_acceptance_study_20261012`, `bsm_repartition_20261012`,
`bsm_stage2_20261013`, `bsm_mass_extension_20261013`,
`bsm_spectral_extension_20261013`, `bsm_epsilon_native_20261013`,
`bsm_epsilon_spectral_20261013`, `bsm_stage2_recovery_20261013`,
`bsm_remaining_recovery_20261014`, `bsm_epsilon_recovery_20261014` and
`bsm_plot_refresh_20261014`.

Some GEN `cmsRun.log` files are part of full scientific normalization and
hashed provenance. Their `.log` extension does not make them disposable.
Likewise, an old campaign with no remaining jobs can still feed the live
aggregate. The compressed live-dependency manifest identifies a minimum
protected set; it does not enumerate every possible downstream dependency.

Queue coverage is the assigned `bigbird26` scheduler only. Process coverage is
`lxplus923` only. Other schedulers/login nodes were not established empty.

## Original broad retention assessment

The first audit used broad historical reproducibility as a retention default.
The active-task proposal above supersedes it for closed experimental payloads.
The following records explain what those areas contain; they do not require
keeping every old experiment in the new deletion proposal.

- **Unique GEN and detector evidence.** Five native reference `gen.root` files
  in `qcd_tail_followup_20261013/weighted_pilot/native20_conditional_closure_100k_v1`
  total 931,914,868 bytes. Preserve all five independent exposures and their
  full attempted/accepted ledgers. Later continuation receipts supersede an
  older two-block wrap-up. The `qcd_gen_recovery_20261014/tail_reco_launch_v1/results`
  subtree occupies about 809 MiB. A checked returned archive contains
  `step3.root`, `nano.root` and provenance. Existing records explicitly retain
  local returns without EOS publication; a replacement must be verified first.
- **Event selection and normalization.** Keep the QCD `full_gen_scan`,
  `independent_tail_draw_v1` and `sampling_weights/full_GEN_normalization_v1`
  authorities, event identities, seeds and probabilities. Night-draw preparation
  reads these paths and verifies hashes/counts. Published limits also reference
  the full GEN `authority.json`. Keep BSM exported cross sections, native
  contracts, weights and complete proposal/GEN denominators.
- **Models and their reproducible evaluations.** Keep trained classifier
  models, locked event splits, protocols, evaluation/score arrays and input
  inventories under the BDT directories. Maintained utilities use them.
  Published selected-model metadata also hashes original BSM template and GEN
  records. Two original BSM worker implementations are regression references
  loaded directly by maintained tests.
- **Cleanup and migration records.** Preserve `retention_20260922`,
  `cleanup_20261009` and other deletion/relocation manifests and receipts.
  Compact or compress inactive evidence rather than discarding the history
  of which scientific files were retained or deleted.
- **Geometry references.** There are 25 GDML files totalling about 292 MiB.
  Eighteen CMS variants were compared against the private payload tree and
  had no byte-identical private copies; one of those is a small synthetic
  fixture requiring separate source review. Historical production variants
  must be copied to the required private geometry directory and verified
  before local removal. Moving them within the same AFS volume frees no quota.

The source/plot metadata scan found 609 existing absolute validation targets
in 1,773 files; seven JSON files over 8 MiB were skipped. Some targets are
redundant rendered figures, but others are essential numerical/provenance
inputs. Historical document links alone do not establish a runtime dependency.

## What can be reduced

There is confirmed redundant content, plus larger archival opportunities:

1. **Specific redundant-copy candidates: 100.6 MiB.** Three large files under QCD
   `job_review/preserved` are SHA-256-identical to their retained original
   scoring plans/source inventory, totalling 79,118,673 bytes. Another 149 BDT
   PDFs, totalling 2,689,101 bytes, match files already published under
   `tea_shift_cmssw/plots`. The plot-performance baseline/final histogram dumps
   are also identical; retaining one instead of both saves another 22.6 MiB.
   The [153-file candidate manifest](validation_retention_audit_20261015/redundant_copy_candidates.json)
   preserves exact source/destination/hash pairs. Deletion still requires
   fresh live/reference checks. Across files at least 1 MiB, the hash
   audit found 91 duplicate groups with 505.4 MiB of repeated content. That
   larger figure overlaps the candidates above and includes sealed/live
   deployments, whose files must stay in place while their consumers run.
2. **Archive the large inactive scientific files.** The five native ROOT
   references and returned detector payloads alone offer about 1.66 GiB of
   potential AFS relief after verified EOS archival. Keep their metadata and
   working consumers intact. The broader two QCD areas occupy 5.89 GiB, but
   that whole amount is not a disposable-output estimate.
3. **Reduce copied text and runtimes.** `.py` files occupy about 1.98 GiB and
   `.json` about 2.38 GiB; many are copied resolved configs, inventories and
   ledgers. Preserve required content, deduplicate/archive inactive copies,
   and avoid another full expanded-config copy per probe. Live seals/checksums
   prevent rewriting a frozen deployment merely to save space.
4. **Retire completed software-test scratch.** Plot-performance/render tests
   can keep a compact timing/equivalence receipt instead of every profile,
   large histogram dump, draft render and one-off probe. The directory CSV
   marks these as `REVIEW SCRATCH`; that status still requires checking the
   recorded result and consumers before removal.
5. **Inspect failed-job bundles before discarding them.** The old dimuon
   score test includes roughly 181 MiB in deployment-v2 results, including
   large failure archives. Preserve any unique failure reproducer, ROOT
   evidence or accounting record; retain only the necessary diagnostic subset
   when the failure has been resolved. An archive is not assumed redundant.

The QCD full-scan migration already provides a useful local example:
`full_gen_scan/archive_storage_relocation.json` records verified EOS readback
and removal of 896,607,047 bytes of former AFS archives. That saving happened
before this audit and is not counted as new reclaimable space.

Several legacy paths are already absent, including the old storage/retention
maps, v10/v8 ntuple authorities and histogram hold/stall runtimes. Their
hard-coded references are recorded separately. Ordinary available-histogram
plotting does not require those authorities, but some legacy audit/merge
entrypoints would require them. This audit did not remove them or repair
those unrelated defaults.

## Cleanup instructions now in force

The workspace [AGENTS.md](../../AGENTS.md) now requires cleanup at the end of
each task and substantial work chunk, quota/size checks, a storage plan before
writing more than 100 MiB of scratch, local temporary storage for disposable
heavy tests, and versioned EOS support storage for retained bulk evidence.
It also requires compact receipts, explicit reasons for anything substantial
left behind, fresh dependency/live checks and verified archival before AFS
copy removal. New ordinary tools must not acquire dated validation inputs.
Existing live/frozen consumers remain protected.

## Audit evidence

The [evidence directory](validation_retention_audit_20261015) contains a complete
compressed file inventory, per-directory CSV, source/document reference maps,
content/duplicate findings, minimum live metadata dependencies and compact
queue observations. Raw payloads, private geometry contents and full job
environments are not copied into the repository. JSON and CSV are data records.

The report and policy edits are documentation changes. Verification checks
inventory totals, compressed-file readability, reference existence and Git
whitespace; no production test or shared-release rebuild is needed.
