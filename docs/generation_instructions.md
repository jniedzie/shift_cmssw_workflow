# Generation instructions

## Configure the workflow

Edit `config/workflow.env` before a run. The main controls are:

| Variable | Purpose |
| --- | --- |
| `CMSSW_SRC` | Site-detected CMSSW `src` directory, or an explicit override. |
| `SAMPLE_BASE` | Site-detected production base directory, or an explicit override. |
| `SAMPLE_NAME`, `CAMPAIGN_NAME` | Components of the campaign output path. |
| `N_EVENTS`, `N_JOBS` | Events per chunk and number of Condor jobs. |
| `GENERATOR_SEED` | `random` or a fixed integer from 1 through 900000000. |
| `ENABLE_EXONANOAOD` | `0` for production NanoAOD; `1` only for an explicit EXO comparison. |

The supported production layout is deliberately canonical:

```text
samples/step1
samples/step2
samples/step3
samples/step4
configs/step1
configs/step2
configs/step3
configs/step4
logs
```

The production reconstruction uses Standard DT navigation with compatible DT
segments added to the precision refit. Tracker seeding/attachment, GEM
measurements, HCAL/ZDC association studies, extended timing, detailed-material
experiments, and the momentum-continuity guard are disabled. These values are
kept together in `workflow.env`; there are no reconstruction-variant presets.

Check the resolved configuration with:

```bash
source config/workflow.env
printf 'WORKFLOW_SITE=%s\nCMSSW_SRC=%s\nSAMPLE_DIR=%s\n' \
  "$WORKFLOW_SITE" "$CMSSW_SRC" "$SAMPLE_DIR"
printf 'STEP1=%s\nSTEP2=%s\nSTEP3=%s\nSTEP4=%s\n' \
  "$STEP1_DIR" "$STEP2_DIR" "$STEP3_DIR" "$STEP4_DIR"
```

## Run locally

Run the stages in order from the workflow repository:

```bash
./run_step1_generation.sh 0 10
./run_step2_digi_raw.sh 0 10
./run_step3_aod.sh 0 10
./run_step4_exonanoAOD.sh 0 10
```

The first positional argument is the chunk and the second is the number of
events. A valid existing output is reused. Pass `--force` to recreate only the
selected stage and chunk.

Step 3 writes AODSIM and persists a compact `ShiftRecoDiag` singleton table
with reconstruction provenance and detector counts. The verbose per-event
segment/track counter is not part of production; it remains available in the
standalone package tests when detailed debugging is needed.

Step 4 runs `PAT,NANO` with one thread and adds the `ShiftMuon`,
`ShiftDimuonVertex`, `ShiftDT`, `ShiftCSC`, `ShiftRPC`, and `ShiftGEM` tables.
It also writes the generator momentum and vertex columns required by TEA.
Production files are named `events_NanoAOD_part_*.root`.

## Validate before production

For a representative local chunk, require all of the following:

1. `cmsRun` exits successfully.
2. The staged Step-3 and Step-4 ROOT files are non-empty and readable.
3. The Step-4 `Events` tree has the expected entry count.
4. `nShiftMuon`, `ShiftMuon_pt`, and the topology/provenance branches exist.
5. The generated Step-4 configuration has
   `directionalRefitUseMomentumContinuityGuard = False`, DT augmentation
   enabled, and tracker/HCAL/ZDC experiments disabled.

The standalone segment-table test remains available for a deeper detector
content check:

```bash
cd "$CMSSW_SRC"
cmsenv
cmsRun PhysicsTools/ShiftMuonSegments/python/test_shiftMuonSegments_cfg.py \
  inputFile=file:/path/to/events_AOD.root maxEvents=10 \
  outputFile=shiftMuonSegments_test.root
```

## Run with Condor

Submit the full chain or selected stages with:

```bash
./run_condor.sh
./run_condor.sh --steps 3,4
./run_condor.sh --force-steps 4
```

Before submitting, the workflow builds the shared CMSSW release and records a
runtime fingerprint. Never rebuild or relink that release while jobs are
running. The cleanup helper also preserves logs whenever it cannot reliably
query the scheduler.

For each production, validate the Condor event log and payload publication
messages in addition to counting EOS files. Do not merge until every expected
job has terminated normally and representative destination ROOT files have
the expected trees, branches, and entries.
