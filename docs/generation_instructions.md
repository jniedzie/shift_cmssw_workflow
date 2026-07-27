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

`SAMPLE_DIR` is derived automatically from the other four path/name variables. Do not hardcode `SAMPLE_DIR` elsewhere.

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
./run_step3_reco_miniAOD.sh
./run_step4_nanoAOD.sh
```

To override the part (first argument) or event count (second argument) without editing configuration:

```bash
./run_step1_generation.sh 1 10
```

Check `$SAMPLE_DIR/samples/stepN/` for outputs, `$SAMPLE_DIR/configs/stepN/` for generated configs, and `$SAMPLE_DIR/logs/stepN/` for logs.

## Condor execution

Configure the workflow as described above. Then, set the number of jobs in the `queue` variable of `condor/submit_cmssw.sub` script. Then run:

```bash
./run_condor.sh
```

To inspect jobs and logs:

```bash
condor_q
ls condor/logs/
source config/workflow.env
find "$SAMPLE_DIR" -mindepth 2 -maxdepth 2 -type f -name '*part*.root'
```
