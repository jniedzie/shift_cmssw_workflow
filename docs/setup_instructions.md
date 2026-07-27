# Setup instructions

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

## Bootstrap behavior

Every step sources `scripts/setup_cmssw.sh`. It loads the configuration, enters `CMSSW_SRC`, runs `cmsenv`, returns to the workflow repository, and creates the derived campaign directories:

```text
SAMPLE_DIR/
  step1/events_step1_partNNNN.root
  step2/events_step2_partNNNN.root
  step3/events_step3_partNNNN.root
  step4/events_NanoAOD_part_NNNN.root
  condor/logs/
```

No manual `mkdir` command is required for these directories.
