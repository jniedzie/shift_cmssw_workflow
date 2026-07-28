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

For Run 3, the production chain is AODSIM → EXONanoAOD, skipping MiniAOD.
The final stage runs `PAT,NANO:@EXO` with `auto:phase1_2025_realistic`,
`Run3,Run3_2025`, and four threads, matching the EXONanoAOD recipe. Set
`AOD_TO_EXONANO_CUSTOMISE` only for an additional `cmsDriver --customise`
`module:function` hook when needed.

The final-stage thread count can be overridden with `N_THREADS`, for example:

```bash
N_THREADS=8 ./run_step4_exonanoAOD.sh 0 10
```

To override the part (first argument) or event count (second argument) without editing configuration:

```bash
./run_step1_generation.sh 1 10
```

Check `$SAMPLE_DIR/samples/stepN/` for outputs, `$SAMPLE_DIR/configs/stepN/` for generated configs, and `$SAMPLE_DIR/logs/` for all logs.

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
