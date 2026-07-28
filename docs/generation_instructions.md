# Generation instructions

## Configure the workflow

Edit only `config/workflow.env` before a run:

| Variable | Set to |
| --- | --- |
| `CMSSW_SRC` | Absolute path to the CMSSW `src` directory containing the installed release. |
| `SAMPLE_BASE` | Base directory where workflow data may be written. |
| `SAMPLE_NAME` | Sample identifier used in the output directory. |
| `CAMPAIGN_NAME` | Campaign identifier used in the output directory. |
| `N_EVENTS` | Default event count; keep the `${N_EVENTS:-10}` form to allow command-line overrides. |
| `N_JOBS` | Number of Condor jobs to submit; keep the `${N_JOBS:-100}` form to allow command-line overrides. |
| `PYTHIA_CONFIG` | CMSSW generator configuration fragment used for event generation. |
| `AOD_TO_EXONANO_CUSTOMISE` | Optional `cmsDriver --customise` hook for the EXONanoAOD content and branches. |

`SAMPLE_DIR` is derived automatically from the path/name variables. Do not hardcode `SAMPLE_DIR` elsewhere.

## Check the configuration

From the workflow repository, inspect the resolved values with:

```bash
source config/workflow.env
printf 'CMSSW_SRC=%s\nSAMPLE_BASE=%s\nSAMPLE_DIR=%s\n' "$CMSSW_SRC" "$SAMPLE_BASE" "$SAMPLE_DIR"
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

For Run 3, the production chain is AODSIM → EXONanoAOD, skipping MiniAOD. The final stage runs `PAT,NANO:@EXO` with `auto:phase1_2025_realistic`, `Run3,Run3_2025`, and four threads, matching the EXONanoAOD recipe. Set `AOD_TO_EXONANO_CUSTOMISE` only for an additional `cmsDriver --customise package/path.module.function` hook when needed.

To override the part (first argument) or event count (second argument) without editing configuration:

```bash
./run_step1_generation.sh 1 10
```

Check `$SAMPLE_DIR/samples/stepN/` for outputs, `$SAMPLE_DIR/configs/stepN/` for generated configs, and `$SAMPLE_DIR/logs/` for all logs.

## Enable DT and CSC segment tables

The checked-in `config/workflow.env` enables the reusable customization:

```bash
AOD_TO_EXONANO_CUSTOMISE="PhysicsTools/ShiftMuonSegments/shiftMuonSegments_customise.customise"
```

With that setting, Step 4 adds the `ShiftMuonSegmentsCounter` analyzer and
the `ShiftMuonSegmentsTableProducer` to the EXONanoAOD path. The producer
writes one row per reconstructed segment in the `ShiftDT` and `ShiftCSC`
FlatTables. Run the usual Step 4 command after Step 3:

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

Look for the `ShiftDT` and `ShiftCSC` tables and compare their row counts with
the per-event counter messages. If either collection is absent from the AOD,
stop and add the required segment products to the upstream AOD event content;
do not substitute another collection or infer a label.

## Condor execution

Configure the workflow as described above, including `N_JOBS`. Then run:

```bash
./run_condor.sh
```

To inspect jobs and logs:

```bash
condor_q
ls "$SAMPLE_DIR/logs/"
source config/workflow.env
find "$SAMPLE_DIR" -mindepth 2 -maxdepth 2 -type f -name '*part*.root'
```
