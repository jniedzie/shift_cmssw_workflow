# Generation instructions

The four steps run in order. All chunks share the campaign directory, and their files are distinguished by a zero-padded part number:

```text
$SAMPLE_DIR/samples/step1/events_step1_partNNNN.root
$SAMPLE_DIR/samples/step2/events_step2_partNNNN.root
$SAMPLE_DIR/samples/step3/events_step3_partNNNN.root
$SAMPLE_DIR/samples/step4/events_NanoAOD_part_NNNN.root
```

`SAMPLE_DIR` comes from `config/workflow.env`. Each local step takes `PART_NUMBER [EVENT_COUNT]`; the part defaults to `0` and the event count defaults to `N_EVENTS` from the config. Part `1` becomes `PART=0001`.

## Local execution

First complete the configuration checks in [setup instructions](setup_instructions.md), then run from the workflow repository:

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

Check `$SAMPLE_DIR/samples/stepN/` for outputs, `$SAMPLE_DIR/configs/stepN/` for generated configs, and `$SAMPLE_DIR/logs/stepN/` for logs. For part `1`, the expected inputs are `samples/step1/events_step1_part0001.root`, `samples/step2/events_step2_part0001.root`, and `samples/step3/events_step3_part0001.root`; Step 4 produces `samples/step4/events_NanoAOD_part_0001.root`.

## Condor execution

The Condor submit file passes `$(Process)` as the wrapper's first argument. The wrapper reads `N_EVENTS` from `config/workflow.env` and passes both values explicitly to all four steps. No per-job `CHUNK` or `N_EVENTS` environment variable is used. All jobs write into the same `$SAMPLE_DIR`, using distinct filenames. Condor's submit logs are kept in the repository's `condor/logs/`; the workflow's step logs use the same part-qualified filenames under `$SAMPLE_DIR/logs/stepN/`.

Before submitting, verify `config/workflow.env` and run:

```bash
./run_condor.sh
```

The submit file controls the job count through its `queue` line. Change that line when changing the number of chunks. Do not put sample, campaign, PNFS, or CMSSW paths in the submit file; those belong in `config/workflow.env`.

Inspect jobs and logs with:

```bash
condor_q
ls condor/logs/
source config/workflow.env
find "$SAMPLE_DIR" -mindepth 2 -maxdepth 2 -type f -name '*part*.root'
```
