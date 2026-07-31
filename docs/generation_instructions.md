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
| `PYTHIA_CONFIG` | CMSSW generator configuration fragment used for event generation. |
| `ENABLE_EXONANOAOD` | `1` for EXONanoAOD content or `0` for standard NanoAOD content in Step 4. |
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

For Run 3, the production chain is AODSIM → NanoAOD, skipping MiniAOD. Step 4 runs `PAT,NANO:@EXO` by default, with `auto:phase1_2025_realistic`, `Run3,Run3_2025`, and four threads, matching the EXONanoAOD recipe. Set `ENABLE_EXONANOAOD=0` in `config/workflow.env` to run standard `PAT,NANO` instead. `AOD_TO_EXONANO_CUSTOMISE` is applied in either mode, so the muon-segment tables can be retained without enabling the full EXONanoAOD content.

To override the part (first argument) or event count (second argument) without editing configuration:

```bash
./run_step1_generation.sh 1 10
```

Check `$STEP1_DIR` ... `$STEP4_DIR` for outputs, the corresponding
`$STEP*_CONFIG_DIR` directories for generated configs, and `$LOG_DIR` for logs.

## Enable DT and CSC segment tables

The checked-in `config/workflow.env` enables the reusable customization:

```bash
AOD_TO_EXONANO_CUSTOMISE="PhysicsTools/ShiftMuonSegments/shiftMuonSegments_customise.customise"
```

With that setting, Step 4 adds the `ShiftMuonSegmentsCounter` analyzer and
the `ShiftMuonSegmentsTableProducer` to the final NanoAOD path. The producer
writes one row per reconstructed segment in the `ShiftDT` and `ShiftCSC`
FlatTables; these are the NanoAOD output tree names. Their EDM products are
`nanoaodFlatTable_shiftMuonSegments_ShiftDT` and
`nanoaodFlatTable_shiftMuonSegments_ShiftCSC`. Run the usual Step 4 command
after Step 3:

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
ls "$LOG_DIR/"
source config/workflow.env
find "$SAMPLES_DIR" -mindepth 2 -maxdepth 2 -type f -name '*part*.root'
```
