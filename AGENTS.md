<!-- Code style: human-code. -->
# SHIFT project instructions

These instructions apply to every task in this repository and must be read
before changing the workflow, CMSSW configuration, simulation, trigger model,
or reconstruction.

## Required branch

Always work directly on `main` in this repository. Do not create task branches
or use Git worktrees unless the user explicitly overrides this policy.

## Temporary scripts and cleanup

- For the next new SM/BSM production, use `scripts/shift_production_selection.py`
  and the default material mode of `prepare_shift_detector_sample.py` on the
  full placed/completed GEN census. Freeze admission before SIM; do not couple
  it to the evolving reconstruction templates or apply W/p twice. Keep unknown
  material/model paths at p=1 and a positive random control for other events;
  use total momentum, not muon pT. The old pilot JSON is not the shared default.
- The BSM detector entry now expects a compact prepared selected descriptor,
  `material_selected_source.json` beside GEN (or `--material-source`). Produce
  that once from the shared sampler, not by rescanning full feature evidence
  in every worker. Plan chunks over selected offsets. Older BSM full-GEN/unit-
  weight grouping and publication assumptions must be requalified for W/p
  before using thinned inputs. New model runtimes must freeze both shared
  selection helpers and the coherent material-transport build; preserve their
  model-specific physics lists and the fixed Run-3 settings. Bounded legacy
  controls require an explicit override. The user authorized a fresh full DP
  weekend production on October23 with the released reconstruction; qualify
  the actual weighted full chain before bulk admission. Cancelled deployments
  remain cancelled. Use a dated DP campaign and publish `latest` when ready.

- Stay focused on the agreed milestone. Ask before a side investigation or
  new workstream would materially delay it or expand its scope; explain the
  evidence and expected time or compute cost. Necessary small checks within
  the agreed task do not need repeated permission. If progress stalls, report
  the result and seek the user's judgement before starting another detour.
- Test reconstruction fixes on several failure cases and fixed good muon and
  dimuon controls. Keep paired iterations small and fast, preserve all failures
  and original identities, and report control regressions as well as gains.

- Generated validation reports, inventories, scheduler snapshots, archives,
  figures and deletion receipts are local artifacts. Keep them in ignored
  `validation/`, `docs/results/` or `docs/validation_retention_audit_<date>/`
  directories. Keep concise Markdown conclusions in the versioned docs.
  Do not force-add ignored artifacts. JSON belongs in Git when it is maintained
  configuration, a schema or a small synthetic test fixture.
- Put one-off probes, debugging scripts, generated test configurations, and
  disposable outputs in `tmp/<YYYYMMDD-task>/` within this repository. Do not
  put temporary scripts in the repository root, `scripts/`, or `tests/`.
- Add a short note in each task directory stating its purpose, owner, and
  when it is safe to remove. Contents of `tmp/` are ignored except its shared
  `README.md`; do not force-add temporary files to Git.
- Keep reusable workflow tools in `scripts/` and regression tests in `tests/`.
  Promote a useful temporary script there only after review, documentation,
  and appropriate validation.
- Keep campaign state, frozen deployments, receipts, and persistent validation
  evidence outside `tmp/`. Store private FLUKA/geometry payloads only in the
  private directory required below, even during temporary tests.
- Before cleanup, check references and running jobs. Remove only files known
  to be disposable; never use a blanket cleanup on campaign state, data, logs,
  or the repository. A directory named `tmp` is not proof it is unused.

## Production retention and obsolete campaigns

- Once the actual production is finalized, remove disposable
  `nanoAOD_canaries` and `GEN_pilot` payloads after validating canonical outputs
  and confirming no live job, controller, recovery or downstream consumer
  needs them. Terminal failed or partial campaigns do not qualify.
- Keep pilot ROOT files that are the sole scientific evidence or a required
  reproducible reference. Retain unique references outside the analysis data
  base under `shift_cmssw_support/legacy_references/`.
- Preserve cross sections, weights, full attempted/accepted denominators,
  provenance, configurations, seeds, receipts and validation gates. Do not
  discard whole `metadata/` directories; archive retired campaign metadata
  alongside its references under
  `shift_cmssw_support/legacy_references/<process-bin>/<campaign>/` with
  verified hashes.
- The 2026-10-09 request authorizes the reviewed obsolete campaigns/bins across
  all processes, not blanket deletion of future campaigns.
  Require exact inventories, dependency and live-job checks, validated
  canonical outputs and preserved references/metadata before deletion. Record
  per-file receipts, verify deleted paths are absent and check retained outputs
  and storage mappings. Names, age or overlapping bins alone are insufficient.

## Non-negotiable physics objective

The goal is to measure how well SHIFT muons can be triggered, recorded, and
reconstructed **under the actual Run 3 CMS detector constraints**.

- Never change, loosen, retime, optimize, or invent detector-electronics
  integration windows, BX assignment behavior, buffering, readout behavior,
  trigger rules, prescales, deadtime, or rate limits to improve SHIFT
  acceptance.
- Treat those quantities as fixed external constraints. Use authoritative Run
  3 configurations, conditions, firmware documentation, or measured data to
  model them faithfully. If an authoritative value is unavailable, keep the
  result explicitly unknown or provisional; do not choose a favorable value.
- Configuration may select the appropriate real Run 3 year/era or reproduce a
  documented detector setting. It may also vary the physical SHIFT collision
  time or the reference BX to measure acceptance. Neither operation authorizes
  changing the CMS electronics or trigger system itself.
- `SHIFT_G4_MAX_TRACK_TIME_NS` and
  `SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS` are Geant4 CPU-protection transport
  guards, not electronics timing windows. They may be raised only to prevent
  premature simulation loss; detector/readout acceptance must remain governed
  by the real Run 3 subsystem behavior.
- Pileup mixing is an occupancy/reconstruction study. The ZeroBias timeline is
  an empirical model of ordinary trigger activity. Electronics response and
  trigger-rule application must remain separate, auditable layers using the
  fixed real CMS behavior.
- Never describe a trigger-candidate sidecar as an electronics-timing result.
  A final claim requires the delayed SHIFT hits to pass through the unchanged
  detector integration, BX assignment, readout, and trigger constraints.

If a proposed implementation would alter any electronics or trigger rule,
stop and flag that it conflicts with the project objective instead of making
the change.

## LSS geometry-converter priority

- The provisional IR1/ATLAS deck is a difficult regression fixture, not the
  geometry to optimize. The production input is expected to become a CMS
  LSS/IR5 FLUKA deck.
- Improve the FLUKA-to-CMSSW converter and its audits generically: preserve
  FLUKA Boolean semantics, lattices, transforms, materials, bounds, and
  completeness across input models.
- Do not hard-code ATLAS region/body names, coordinates, materials, hand-tuned
  AABBs, or gap fills to make this fixture pass. Use an ATLAS-specific probe
  only to isolate a reusable defect and add a construct-level regression test.
- If a change would not apply to an equivalent FLUKA construct in the future
  CMS deck, report it as model-specific and do not spend time fine-tuning it.
- The future CMS deck must pass the same fail-closed conversion, overlap, gap,
  material, transform, and transport gates without favorable special cases.

## Private FLUKA and geometry payloads

- Never add, stage, commit, or publish raw FLUKA decks, includes, field routines, field maps, source-checksum manifests, or production-generated GDML files in this repository.
- Store such payloads only in `/afs/cern.ch/work/j/jniedzie/private/cms_lss_fluka_description`; copy and verify SHA-256 before deleting a working copy.
- Check both `git ls-files` and `git status`: ignored or untracked payloads are not permission to publish them. Synthetic unit-test fixtures are allowed only when they contain no experiment geometry, map, or production payload.

## Workflow safety

- On October 23, 2026, the user authorized a fresh DP weekend production with
  the released reconstruction. A dated DP campaign is allowed, with `latest`
  publication when ready. Qualify its weighted full chain before bulk admission. Previously cancelled frozen
  deployments stay cancelled; do not resume them or credit their deleted data.
- For an authorized unattended production, freeze automatic progression from
  qualification through bulk processing, grouping, histogramming and final
  publication. Do not leave a successful test waiting for a manual gate file.
  Check the eligibility logic against actual report field types. Preserve
  failures and keep independent points progressing without silently dropping
  failed or zero-selected cohorts from their full GEN normalization.
  GEN-only qualification receipts are not published signal points. Missing
  signal forecasts must stay unknown; status reporting must not invent zero
  predictions or require fields absent from a valid frozen worklist.
- Exercise shared-storage admission with real peer state after source-only
  qualifications exist. A source qualification is not a published histogram
  cohort and must not earn completed-output storage credit.
- Check generated shell entries with `bash -n` before sealing or submitting.
  Qualify the exact production template, including its EXIT trap, from scratch
  under the deployed environment. Match a standalone entry line when inserting
  commands; the first matching substring may be inside a quoted trap.
- Qualify a replacement monitor with real replacement-manager and child records.
  A reviewed wrapper-only recovery can keep the original payload directory;
  distinguish its children by the exact new DAG manager and frozen proof.
  Keep old-manager children and unrelated jobs in that directory as failures.
- Check the effective stage wall-time limits for a one-event job, including
  event-count scaling. They must allow the advertised first-event/stall budget
  and setup overhead; a lower hidden stage limit can kill a valid slow event.
  These are CPU-protection limits, not detector or trigger timing settings.
- Test the frozen controller's real preparation and submission subprocesses
  under its deployed environment. LCG exports `PYTHONHOME` and library paths:
  invoking `/usr/bin/python3` with those settings can fail before the script
  starts. Clean the environment before launching the system interpreter, or
  use the matching LCG interpreter when its ROOT packages are needed. Exercise
  an interrupted preparation too, so an existing receipt does not trap every
  later poll in the same error.
- If a scratch-only GEN worker fails after producing its ROOT file, preserve
  that file and the minimal raw contract/audit evidence before its temporary
  directory is removed. Keep failed exposure separate from admitted exposure.
  A replay must use the declared frozen contract and seeds; metadata must not
  silently change a source-position draw keyed by a contract hash.
- Optimize validation for time to a useful result. Run independent events and
  variants in parallel, using one event per job when that shortens iteration.
  Thousands of jobs are acceptable within actual site and storage limits;
  do not add a small worker cap just to reduce job submissions. Publish each
  completed comparison without waiting for unrelated tests. Size short worker
  wall-time requests separately from longer controller lifetimes.
- Allocate signal production for comparable numbers of events passing the
  actual reconstruction and plot selections, not equal GEN counts. Measure
  efficiency and its uncertainty, credit ready and queued events, and give
  inefficient mass/lifetime points more exposure. Use a bounded follow-up
  sample when no events pass; do not assume zero acceptance.
- Read `docs/generation_instructions.md` and the relevant timing/overlay note
  before launching or modifying a campaign.
- Run the coverage audit on the complete new model card before registering its
  production. Synthetic ownership tests can miss new recoil types. Retain the
  generic inventory gaps as well as any more detailed, conditional model scope.
- Verify a new native queue filter against a known live manager and worker.
  An empty mocked queue missed a double-escaped path regex on October 23;
  use literal directory-prefix comparisons for ClassAd working directories.
  For large ID sets, use flat ClassAd membership lists rather than hundreds
  of chained OR terms; the latter returned an empty live queue on October 23.
- After changing a live DAG limit, verify that its manager consumed it. A
  ClassAd readback alone is insufficient. Newer DAGMan versions provide
  `htcondor dag throttle`; the frontend client may be an older version.
- Do not edit live campaign configuration or rebuild the shared CMSSW release
  while jobs using it are running. Documentation-only edits are safe.
- Keep timing, occupancy, trigger-candidate, electronics-response, and offline
  reconstruction conclusions clearly separated in code, metadata, and reports.
- Follow the workspace section "Detached production and handoff".
  In particular, set a persistent `initialdir`, submit with that
  directory as `cwd`, inspect the queued `Iwd`, and verify actual downstream
  execution. See the October 14–15 DP incident in `../SHIFT_BSM.md`.
- After stable controller and worker operation and the required downstream
  handoff are verified, stop manual monitoring. Leave detached supervision
  active; check again only at the user's request, on a reported failure, or
  to validate a specific intervention.
