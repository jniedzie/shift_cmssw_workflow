# Generation instructions

## Configure the workflow

Edit `config/workflow.env` before a run. The main controls are:

| Variable | Purpose |
| --- | --- |
| `CMSSW_SRC` | Site-detected CMSSW `src` directory, or an explicit override. |
| `SAMPLE_BASE` | Site-detected production base directory, or an explicit override. |
| `SAMPLE_NAME`, `CAMPAIGN_NAME` | Components of the campaign output path. |
| `N_EVENTS`, `N_JOBS` | Events per chunk and number of Condor jobs. |
| `COLLISION_YEAR` | Coherent `2022`, `2023`, or `2024` era/GlobalTag/pileup preset; currently defaults to `2023`. |
| `GENERATOR_SEED` | `random` or a fixed integer from 1 through 900000000. |
| `SIMULATION_SEED` | Geant4 seed; fix it with `GENERATOR_SEED` for paired timing scans. |
| `PILEUP_MODE` | `none` or opt-in `standard` central pileup in Step 2. |
| `PILEUP_SCENARIO` | CMSSW pileup profile selected by `COLLISION_YEAR`. |
| `PILEUP_DATASET` | Central CMS minimum-bias GEN-SIM dataset queried through DAS. |
| `PILEUP_INPUT` | `filelist:/absolute/path`, `das:...`, or explicit pileup ROOT PFNs. |
| `PILEUP_SEED` | Mixing seed; fix it for reproducible occupancy comparisons. |
| `SHIFT_TIMING_MODE` | `nominal`, exact `legacy` regression, or a `fixed` test shift. |
| `SHIFT_TIMING_BEAM_DIRECTION_Z` | Longitudinal beam direction, `-1` or `1`. |
| `SHIFT_TIMING_BX_OFFSET` | Additive integer 25 ns BX offset. |
| `SHIFT_TIMING_PHASE_NS` | Additive fractional timing phase in ns. |
| `SHIFT_TIMING_FIXED_OFFSET_NS` | Common ns shift used in `fixed` mode. |
| `SHIFT_G4_MAX_TRACK_TIME_NS` | Central Geant4 transport guard, 5000 ns by default. |
| `SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS` | Forward Geant4 transport guard, 5000 ns by default. |
| `TRIGGER_TIMELINE_MODE` | `none` or `zero_bias_proxy` for a correlated candidate-trigger sidecar. |
| `TRIGGER_LIBRARY_JSONL`, `TRIGGER_L1_MENU_JSON` | Validated ZeroBias inputs used by the proxy. |
| `TRIGGER_TIMELINE_START_BX`, `TRIGGER_TIMELINE_END_BX` | Relative BX interval sampled around every SHIFT event. |
| `TRIGGER_TIMELINE_SEED` | Trigger sampler seed; fixed seeds are offset by Condor chunk. |
| `TRIGGER_COLLIDING_BX_FILE` | Optional filling-scheme-derived list of colliding relative BXs. |
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

Step 1 applies SHIFT timing after standard vertex smearing and before Geant4.
The 5000 ns transport guards deliberately replace CMSSW's stock 500 ns central
track cutoff for this workflow: a time-zero particle from 148 m reaches CMS at
about 494 ns and would otherwise be killed before detector response can decide
whether it is accepted.  These guards are not electronics readout windows.

### Three campaign configurations

Keep a distinct `CAMPAIGN_NAME` for every row. The recommended sequence is
cumulative, so the difference between adjacent campaigns isolates one new
mechanism:

| Campaign | `SHIFT_TIMING_MODE` | `PILEUP_MODE` | `TRIGGER_TIMELINE_MODE` | Interpretation |
| --- | --- | --- | --- | --- |
| timing | `nominal` | `none` | `none` | Clean source-derived timing and configurable Geant4 transport guard. |
| occupancy | `nominal` | `standard` | `none` | Timing plus central CMS pileup mixed through standard CMSSW mixing. |
| trigger proxy | `nominal` | `standard` | `zero_bias_proxy` | Adds a correlated candidate-trigger timeline sidecar for every SHIFT event. |

For example, the first campaign needs only:

```bash
COLLISION_YEAR=2023
CAMPAIGN_NAME="${PROCESS}_timing_2023"
SHIFT_TIMING_MODE=nominal
SHIFT_G4_MAX_TRACK_TIME_NS=5000.0
SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS=5000.0
PILEUP_MODE=none
TRIGGER_TIMELINE_MODE=none
```

For the occupancy campaign, give it a new name and change:

```bash
CAMPAIGN_NAME="${PROCESS}_occupancy_2023"
PILEUP_MODE=standard
PILEUP_SEED=86420
TRIGGER_TIMELINE_MODE=none
```

On lxplus, the 2023 preset already resolves `PILEUP_INPUT` to the complete
27,774-file manifest under `$SAMPLE_BASE/pileup_inputs`. Override it only when
using another site or a deliberately bounded pilot manifest.

For the trigger-proxy campaign, retain the occupancy settings, use another
name, and add durable files visible on every worker:

```bash
CAMPAIGN_NAME="${PROCESS}_trigger_proxy_2023"
TRIGGER_TIMELINE_MODE=zero_bias_proxy
TRIGGER_TIMELINE_START_BX=-24
TRIGGER_TIMELINE_END_BX=5
TRIGGER_TIMELINE_SEED=24680
# TRIGGER_COLLIDING_BX_FILE="/absolute/shared/path/colliding_relative_bx.txt"
```

The 2023 preset already resolves the validated Run-369943 library and L1 menu
under `$SAMPLE_BASE/trigger_inputs/2023/run369943`. Explicit
`TRIGGER_LIBRARY_JSONL` and `TRIGGER_L1_MENU_JSON` overrides remain available
for another site, run, or year; non-2023 presets intentionally leave them
unset until a year-matched library is prepared.

The third campaign currently tests trigger-proxy orchestration and preserves
real L1/HLT correlations. It does **not** yet change detector electronics
integration windows, decide final L1A after trigger rules, or alter which
CMSSW event is stored. Its per-chunk JSONL is written under
`$SAMPLE_DIR/trigger_timelines`; those missing links are the next electronics
timing implementation step, not something the sidecar silently approximates.

`run_condor.sh` pins the process, campaign/sample paths, event count, collision
year, timing controls, pileup controls, and trigger controls into the submitted
jobs. It is therefore safe to edit `CAMPAIGN_NAME` and submit the next campaign
without queued jobs silently switching to the new output directory. Do not edit
or rebuild the shared CMSSW release itself while jobs are running.

Check the resolved configuration with:

```bash
source config/workflow.env
printf 'WORKFLOW_SITE=%s\nCMSSW_SRC=%s\nSAMPLE_DIR=%s\n' \
  "$WORKFLOW_SITE" "$CMSSW_SRC" "$SAMPLE_DIR"
printf 'STEP1=%s\nSTEP2=%s\nSTEP3=%s\nSTEP4=%s\n' \
  "$STEP1_DIR" "$STEP2_DIR" "$STEP3_DIR" "$STEP4_DIR"
```

## Run locally

Prepare a stable pileup input manifest on a submit host with a valid CMS
proxy.  A zero max-files value keeps the complete central dataset; a small
positive value is useful only for focused tests:

```bash
source config/workflow.env
./scripts/prepare_pileup_file_list.sh "${PILEUP_INPUT#filelist:}" 0
```

Enable standard CMSSW pileup mixing explicitly:

```bash
PILEUP_MODE=standard \
PILEUP_SEED=86420 ./run_step2_digi_raw.sh 0 1
```

`PILEUP_MODE=none` remains the default no-pileup control.  The manifest avoids
requiring a DAS query and user proxy on every Condor worker.  Do not use a
small smoke-test manifest for production because excessive event reuse would
distort occupancy correlations.

As checked on 2026-08-19, the default 2023 dataset contains 999,856,000 events
in 27,774 files. Its blocks have active disk replicas at `T2_CH_CERN`,
`T1_US_FNAL_Disk`, and `T2_US_Nebraska`; a representative 7.04 GB file was
confirmed at CERN at file level. The 2022 preset also has CERN disk replicas.
The 2024 dataset remains tape-only: temporary one-file rule
`bcd7943660744e5abec93117af3c920e` was still `WAITING_APPROVAL` when last
checked. `COLLISION_YEAR=2023` therefore changes the CMSSW era, GlobalTag,
pileup profile, and dataset together rather than mixing a 2023 library into a
nominal 2024 campaign.

Extract a correlated trigger-decision seed from certified collider ZeroBias
RAW data separately from pileup mixing.  Enter the CMSSW runtime, then run:

```bash
./scripts/run_zero_bias_trigger_extract.sh \
  root://eoscms.cern.ch//store/data/Run2023D/ZeroBias/RAW/v1/000/369/943/00000/37bc5780-a374-4104-87a4-3169e9efe16b.root \
  /tmp/zero_bias_run369943.jsonl 100 \
  /ZeroBias/Run2023D-v1/RAW 2023
```

The JSONL keeps complete L1 bit vectors and the accepted HLT-path set per
event, so correlations are preserved.  It is not yet a trigger timeline or a
rate model.  Do not sample paths independently, and do not treat an L1
algorithm bit, final L1A, HLT acceptance and storage as interchangeable.

Resolve the exact L1 bit names through the run-dependent conditions and
validate the empirical library:

```bash
cmsRun ./scripts/zero_bias_l1_menu_cfg.py \
  inputFiles=root://eoscms.cern.ch//store/data/Run2023D/ZeroBias/RAW/v1/000/369/943/00000/37bc5780-a374-4104-87a4-3169e9efe16b.root \
  outputFile=/tmp/zero_bias_l1_menu_run369943.root \
  globalTag=auto:run3_data_prompt collisionYear=2023

python3 ./scripts/extract_zero_bias_l1_menu.py \
  /tmp/zero_bias_l1_menu_run369943.root \
  --output /tmp/zero_bias_l1_menu_run369943.json \
  --global-tag auto:run3_data_prompt

./scripts/validate_zero_bias_trigger_library.py \
  /tmp/zero_bias_run369943.jsonl \
  --l1-menu /tmp/zero_bias_l1_menu_run369943.json \
  --min-events-per-group 100 \
  --output /tmp/zero_bias_run369943_summary.json
```

The validator prints the exact trigger-group ID.  If an input contains more
than one group, pass that ID explicitly to the sampler.  A focused candidate
BX timeline can then be produced with:

```bash
./scripts/sample_zero_bias_trigger_timeline.py \
  /tmp/zero_bias_run369943.jsonl \
  --l1-menu /tmp/zero_bias_l1_menu_run369943.json \
  --output /tmp/zero_bias_timeline_seed24680.jsonl \
  --start-bx -24 --end-bx 5 --signal-events 10 --seed 24680
```

This output is deliberately pre-deadtime.  `readout_after_trigger_rules` is
null until a separately validated trigger-rule engine is applied.  Use
`--colliding-bx-file` with a versioned filling-scheme-derived list to leave
empty/noncolliding slots unsampled; without it, every requested BX is treated
as colliding for software tests only.

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
