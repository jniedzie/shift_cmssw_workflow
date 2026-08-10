# Generation instructions

## Configure the workflow

Edit only `config/workflow.env` before a run:

| Variable | Set to |
| --- | --- |
| `WORKFLOW_HOST` | Optional hostname override, primarily for testing site detection. |
| `CMSSW_SRC` | Optional override of the site-detected CMSSW `src` directory. |
| `SAMPLE_BASE` | Optional override of the site-detected production base directory. |
| `SAMPLE_NAME` | Sample identifier used in the output directory. |
| `CAMPAIGN_NAME` | Campaign identifier used in the output directory. |
| `SAMPLE_DIR` | Campaign root; defaults to `$SAMPLE_BASE/$SAMPLE_NAME/$CAMPAIGN_NAME`. |
| `STEP1_DIR` ... `STEP4_DIR` | Per-stage ROOT output directories. |
| `STEP1_CONFIG_DIR` ... `STEP4_CONFIG_DIR` | Per-stage generated configuration directories. |
| `LOG_DIR` | CMSSW and Condor log directory. |
| `CROSS_SECTION_FILE` | Shared generated cross-section summary file. |
| `N_EVENTS` | Default event count; keep the `${N_EVENTS:-10}` form to allow command-line overrides. |
| `N_JOBS` | Number of Condor jobs to submit; keep the `${N_JOBS:-100}` form to allow command-line overrides. |
| `GENERATOR_SEED` | `random` for a fresh Step 1 seed on each invocation, or an integer from 1 through 900000000 for reproducible generation. |
| `PYTHIA_CONFIG` | CMSSW generator configuration fragment used for event generation. |
| `ENABLE_EXONANOAOD` | `0` (default) for standard NanoAOD plus SHIFT content or `1` for an EXONanoAOD comparison in Step 4. |
| `AOD_TO_EXONANO_CUSTOMISE` | Optional `cmsDriver --customise` hook applied to Step 4 in either output mode. |

All production paths are derived in `workflow.env` and may be overridden there
for PNFS/dCache or local execution. The scripts contain no site-specific
storage path.

Hosts containing `lxplus` select the CERN AFS/EOS roots; hosts containing
`iihe` select the T2B `/user` and PNFS roots. An unrecognized hostname requires
explicit `CMSSW_SRC` and `SAMPLE_BASE` values and otherwise stops with an error.

## Check the configuration

From the workflow repository, inspect the resolved values with:

```bash
source config/workflow.env
printf 'WORKFLOW_SITE=%s\nCMSSW_SRC=%s\nSAMPLE_BASE=%s\nSAMPLE_DIR=%s\nLOG_DIR=%s\n' \
  "$WORKFLOW_SITE" "$CMSSW_SRC" "$SAMPLE_BASE" "$SAMPLE_DIR" "$LOG_DIR"
```

`CMSSW_SRC` must exist and be a CMSSW `src` directory. `SAMPLE_BASE` must be writable on the execution site.

## Local execution

Once the configuration above is done, run from the workflow repository:

```bash
./run_step1_generation.sh
./run_step2_digi_raw.sh
./run_step3_aod.sh
./run_step4_exonanoAOD.sh
```

For Run 3, the production chain is AODSIM → NanoAOD, skipping MiniAOD. Step 4 runs standard `PAT,NANO` by default, with `auto:phase1_2025_realistic`, `Run3,Run3_2025`, and one thread. The SHIFT customization adds the custom reconstruction tables and the four generator columns used by TEA but absent from standard NanoAOD: `GenPart_pz`, `GenPart_vx`, `GenPart_vy`, and `GenPart_vz`. Set `ENABLE_EXONANOAOD=1` only for an explicit `PAT,NANO:@EXO` comparison job.

Step-4 standard NanoAOD files are named `events_NanoAOD_part_*.root`. The TEA NanoAOD merge configuration selects that pattern explicitly, so legacy `events_EXONanoAOD_part_*.root` files left in the same directory are not mixed into a new merge.

To override the part (first argument) or event count (second argument) without editing configuration:

```bash
./run_step1_generation.sh 1 10
```

Each stage normally exits without running when its output is already valid.
Pass `--force` (or `-f`) to remove that stage's existing output and recreate it:

```bash
./run_step1_generation.sh --force 1 10
./run_step4_exonanoAOD.sh --force 1 10
```

The option can appear before or after the positional arguments. It removes
only the selected stage and chunk output; input files from earlier stages are
left untouched.

Check `$STEP1_DIR` ... `$STEP4_DIR` for outputs, the corresponding
`$STEP*_CONFIG_DIR` directories for generated configs, and `$LOG_DIR` for logs.

## Muon reconstruction diagnostics and segment tables

The checked-in `config/workflow.env` enables the reusable customization:

```bash
AOD_TO_EXONANO_CUSTOMISE="PhysicsTools/ShiftMuonSegments/shiftMuonSegments_customise.customise"
```

With that setting, Step 4 adds the `ShiftMuonSegmentsCounter` analyzer and
the `ShiftMuonSegmentsTableProducer` to the final NanoAOD path. The producer
writes one row per reconstructed segment in `ShiftDT`, `ShiftCSC`, and
`ShiftGEM`. Since RPC reconstruction produces hits rather than segments,
`ShiftRPC` contains one row per `RPCRecHit` (including BX and time).
These are the NanoAOD output tree names. Their EDM products use the
`nanoaodFlatTable_shiftMuonSegmentsTable_*` module label. Run the usual Step 4 command
after Step 3:

The same customization also writes the cleaned `ShiftMuon` NanoAOD
collection.  It combines `displacedStandAloneMuons`, `shiftTraversingMuons`,
and `shiftCosmicMuons`, removes overlaps using shared reconstructed detector
hits (with a conservative track-line fallback), and never consumes generator
or simulation truth.  The `source` column records `0` for DSA, `1` for the
traversing fit, and `2` for the cosmic fit.  DSA has precedence because the MC
comparison gives it the best momentum estimate; the traversing fit precedes
the ordinary cosmic fit because it gives better direction and production-line
precision when those two overlap.  Other columns retain the source index,
track fit quality, hit counts, inner/outer states, and the straight-line PCA.
Rows also carry `genPartIdx`, a one-to-one directional match to the NanoAOD
`GenPart` collection made only after reconstruction and cleaning.  It is `-1`
for unmatched tracks and whenever `finalGenParticles` is absent, including
collision data.  Momentum components and their track uncertainties, impact
parameters, raw chi2/ndof, valid muon-hit count, and station count are stored
directly in `ShiftMuon`; offline analysis therefore does not depend on the
cosmic or traversing AOD track collections being retained.

The MC-only study which established this ordering can be repeated with:

```bash
python3 scripts/analyze_shift_muon_quality.py "$LOG_DIR"/step3_events_AOD_part\*.log
```

Truth is used only by this diagnostic script, not by the `ShiftMuon` producer.

```bash
./run_step4_exonanoAOD.sh 0 10
```

The customization consumes the DT and CSC InputTags configured in
`PhysicsTools/ShiftMuonSegments/shiftMuonSegments_cfi.py`. Verify those
labels with `edmDumpEventContent` on the actual Step 3 AOD before production;
the test must use an AOD that contains both reconstructed segment collections.
The counter log distinguishes a missing/invalid collection from a valid
collection containing zero segments.

## Run the standalone segment producer test

From the CMSSW `src` directory, run the test against a representative Step 3
AOD. The input file is supplied on the command line:

```bash
cd "$CMSSW_SRC"
cmsenv
scram b -j 4
cmsRun PhysicsTools/ShiftMuonSegments/python/test_shiftMuonSegments_cfg.py \
  inputFile=file:/path/to/events_AOD.root maxEvents=10 \
  outputFile=shiftMuonSegments_test.root
```

The test processes at most 10 events, runs both the counter and table
producer, and writes `shiftMuonSegments_test.root`. Inspect the output with:

```bash
edmDumpEventContent shiftMuonSegments_test_numEvent10.root
python "$CMSSW_SRC/bin/$SCRAM_ARCH/inspectNanoFile.py" shiftMuonSegments_test_numEvent10.root
```

Look for the four `ShiftDT`, `ShiftCSC`, `ShiftRPC`, and `ShiftGEM` tables.

Step 3 also appends `ShiftMuonSegmentsCounter` after reconstruction. Search its
log for `[ShiftMuonRecoDebug]`. Each event has a `summary` line containing the
DT/CSC/RPC/GEM reconstructed-hit counts, DT/CSC/GEM segment counts, displaced
standalone seed count, and final DSA track count. Detail lines show segment
direction, hit multiplicity and chi2, followed by DSA track endpoints, valid
and lost hit counts, and fit quality. A zero first appears at the stage where
reconstruction loses the muon. Compare table row counts with these messages.
If a collection is absent from the AOD,
stop and add the required segment products to the upstream AOD event content;
do not substitute another collection or infer a label.

The first reconstruction trial changed only the forward and backward DSA
measurement compatibility windows from 3 to 5 sigma and did not change the
four-track yield. The current second trial retains 5 sigma and changes only
the forward/backward trajectory-updator hit `MaxChi2` from 25 to 100. Fit
directions, GEM use, and refitting remain unchanged. `NMinRecHits` has no
effect while `DoSeedRefit=False`; likewise, `MaxFractionOfLostHits` has no
effect while `DoRefit=False`.

The second trial did not change the four-track yield, although the looser hit
chi2 admitted six additional hits to the event-1 track. The current third
trial retains those two tested relaxations and changes only `SeedPosition`
from `in` to `out`. This makes the first filter propagate event 9's ME2 seed
toward its aligned ME3/ME4 segments; the backward filter still builds the
opposite leg.

That direction trial regressed the DSA yield from four events to event 1 only
and has been reverted. The current fourth trial restores `SeedPosition="in"`
and changes only `DoBackwardFilter` from true to false relative to the
four-track configuration. This tests whether the return leg invalidates an
otherwise usable forward trajectory.

Disabling the backward filter regressed the yield from four tracks to three
and has been reverted. The current fifth trial keeps the established fitter
settings and disables only the cosmic seed generator's global-y correlation
pruning. Every DT/CSC segment passing the existing validity and chi2 checks is
allowed to seed. This targets event 9, where the standard cosmic pruning made
seeds only from ME2 despite clean aligned ME1--ME4 segments.

## Condor execution

Configure the workflow as described above, including `N_JOBS`. Then run:

```bash
./run_condor.sh
```

By default every step is considered and an already-valid output is reused.
To submit only selected stages, pass a comma-separated list.  Add `--force`
to recreate the selected outputs, or use the `--force-steps` shorthand:

```bash
./run_condor.sh --steps 3,4
./run_condor.sh --force-steps 4
```

The selector accepts `1,2,3,4` or `step1,step2,step3,step4`.  Steps always
execute in workflow order, irrespective of the order in the list.  Inputs of
an isolated downstream step must already exist; for example, a Step-4-only
submission requires the corresponding Step-3 AOD file.

To inspect jobs and logs:

```bash
condor_q
ls "$LOG_DIR/"
source config/workflow.env
find "$SAMPLES_DIR" -mindepth 2 -maxdepth 2 -type f -name '*part*.root'
```
