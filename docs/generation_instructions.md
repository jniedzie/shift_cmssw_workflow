# Generation instructions

> **Fixed-constraint principle:** this project evaluates SHIFT reconstruction
> under the real Run 3 CMS detector and trigger system. Never change electronics
> integration windows, BX assignment, buffering/readout behavior, trigger
> rules, prescales, or deadtime to improve SHIFT acceptance. Select and model
> the authoritative settings for the chosen year unchanged. See `AGENTS.md`.

## Configure the workflow

Edit `config/workflow.env` before a run. The main controls are:

| Variable | Purpose |
| --- | --- |
| `CMSSW_SRC` | Site-detected CMSSW `src` directory, or an explicit override. |
| `SAMPLE_BASE` | Site-detected production base directory, or an explicit override. |
| `SAMPLE_NAME`, `CAMPAIGN_NAME` | Components of the campaign output path. |
| `N_EVENTS`, `N_JOBS` | Events per chunk and number of Condor jobs. |
| `COLLISION_YEAR` | Coherent `2022`, `2023`, or `2024` era/GlobalTag/pileup preset; currently defaults to `2023`. |
| `GENERATOR_SEED` | `random` or a fixed campaign seed base. A fixed base is advanced by chunk, avoiding duplicate jobs while pairing matching chunks across scans. |
| `SIMULATION_SEED` | Geant4 campaign seed base; fix it with `GENERATOR_SEED` for paired timing scans. |
| `PILEUP_MODE` | `none` or opt-in `standard` central pileup in Step 2. |
| `PILEUP_SCENARIO` | CMSSW pileup profile selected by `COLLISION_YEAR`. |
| `PILEUP_DATASET` | Central CMS minimum-bias GEN-SIM dataset queried through DAS. |
| `PILEUP_INPUT` | `filelist:/absolute/path`, `das:...`, or explicit pileup ROOT PFNs. |
| `PILEUP_SEED` | Mixing campaign seed base, advanced by chunk; fix it for comparisons. |
| `PILEUP_SEQUENTIAL` | Set to `1` only for paired timing scans. Matching chunks use the same pileup sequence, while different chunks rotate across the manifest. |
| `PILEUP_RSE` | Disk RSE used to prepare production manifests; defaults to `T2_CH_CERN`. |
| `SHIFT_TIMING_MODE` | `nominal`, exact `legacy` regression, or a `fixed` test shift. |
| `SHIFT_TIMING_BEAM_DIRECTION_Z` | Longitudinal beam direction, `-1` or `1`. |
| `SHIFT_TIMING_BX_OFFSET` | Additive integer 25 ns shift of the physical SHIFT event. In piggyback mode, positive means SHIFT arrives later than the central BX-0 L1A. |
| `SHIFT_TIMING_PHASE_NS` | Additive fractional timing phase in ns; use `0 <= phase < 25` in piggyback mode. |
| `SHIFT_TIMING_FIXED_OFFSET_NS` | Common ns shift used in `fixed` mode. |
| `SHIFT_READOUT_DIAGNOSTICS` | Default-off persistence of pre-pack muon digis and trigger products for a bounded audit. |
| `SHIFT_SIMHIT_REFERENCE_BX_OFFSET`, `SHIFT_SIMHIT_REFERENCE_PHASE_NS` | Default-disabled time shift applied to one fixed muon PSimHit realization immediately before no-pileup digitization. |
| `SHIFT_SIMHIT_REFERENCE_INPUT` | Absolute shared Step-1 file required by a same-SimHit reference scan. |
| `SHIFT_G4_MAX_TRACK_TIME_NS` | Central Geant4 transport guard, 5000 ns by default. |
| `SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS` | Forward Geant4 transport guard, 5000 ns by default. |
| `SHIFT_LSS_MATERIAL_MODE` | `none` or `external`; attaches the same explicitly transformed external material in Step 1 and Step 4. |
| `SHIFT_LSS_FIELD_MODE` | `none` or provisional `ir1_atlas_proxy`; selects the same composite field for simulation and SHIFT reconstruction. |
| `SHIFT_LSS_GDML_FILE`, `SHIFT_LSS_GDML_SHA256` | Installed CMSSW `FileInPath` and required frozen-artifact checksum for external material. |
| `SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM` | Converter-recorded model coordinate of the recentered GDML origin. This keeps material aligned with fields. |
| `SHIFT_LSS_MODEL_ORIGIN_CM`, `SHIFT_LSS_MODEL_TO_CMS` | CMS position of FLUKA `(0,0,0)` and the common proper rotation. There is deliberately no default transform. |
| `SHIFT_LSS_FIELD_SCALE` | Required signed scale for the provisional field. Its sign records the reviewed polarity. |
| `TRIGGER_SCENARIO` | `piggyback_central` conditions production on an ordinary recorded central collision; `none` disables that contract. |
| `TRIGGER_TIMELINE_MODE` | `none` or `zero_bias_proxy` for a correlated candidate-trigger sidecar. |
| `TRIGGER_LIBRARY_JSONL`, `TRIGGER_L1_MENU_JSON` | Validated ZeroBias inputs used by the proxy. |
| `TRIGGER_TIMELINE_START_BX`, `TRIGGER_TIMELINE_END_BX` | Relative BX interval sampled around every SHIFT event. |
| `TRIGGER_TIMELINE_SEED` | Trigger sampler seed; fixed seeds are offset by Condor chunk. |
| `TRIGGER_COLLIDING_BX_FILE` | Legacy relative-BX software fixture; mutually exclusive with the physical mask. |
| `TRIGGER_COLLIDING_BX_MASK` | Absolute normalized LPC IP5 mask JSON; required for fill-aware timelines. |
| `TRIGGER_REFERENCE_SLOT_MODE` | `uniform-colliding` for the conditional central-collision sample, `uniform-filled` for structural studies, or `fixed` for a control. |
| `TRIGGER_REFERENCE_BX_SLOT`, `TRIGGER_SHIFT_BEAM` | Fixed physical slot, when requested, and the SHIFT beam. |
| `TRIGGER_RUN_FILL_MAP` | Versioned authoritative trigger-run to fill mapping; required with the physical mask. |
| `TRIGGER_RULE_MODE` | `recorded` for the conditional piggyback sample; `none` or synthetic `run3` only for separate controls/rate studies. |
| `TRIGGER_RULE_HISTORY_START_BX` | First warm-up BX; `run3` requires at least 240 BX before the analysis start. |
| `PIGGYBACK_FILTER_RECONSTRUCTION`, `PIGGYBACK_FILTER_LEVEL` | Filter Step 3 to the decision report at `raw` or `persisted` level. Production defaults to persisted. |
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
| central piggyback | `nominal` | `standard` | `zero_bias_proxy` | Conditions on an already-recorded central collision and reconstructs the standard BX-0 readout. |

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

For the conditional central-piggyback campaign, retain the occupancy settings,
use another name, and add durable files visible on every worker:

```bash
CAMPAIGN_NAME="${PROCESS}_piggyback_central_2023"
TRIGGER_SCENARIO=piggyback_central
TRIGGER_TIMELINE_MODE=zero_bias_proxy
TRIGGER_TIMELINE_START_BX=0
TRIGGER_TIMELINE_END_BX=0
TRIGGER_TIMELINE_SEED=24680
TRIGGER_RULE_MODE=recorded
TRIGGER_REFERENCE_SLOT_MODE=uniform-colliding
TRIGGER_SHIFT_BEAM=2
PIGGYBACK_FILTER_RECONSTRUCTION=1
PIGGYBACK_FILTER_LEVEL=persisted
```

The 2023 preset already resolves the validated Run-369943 library and L1 menu
under `$SAMPLE_BASE/trigger_inputs/2023/run369943`. Explicit
`TRIGGER_LIBRARY_JSONL` and `TRIGGER_L1_MENU_JSON` overrides remain available
for another site, run, or year; non-2023 presets intentionally leave them
unset until a year-matched library is prepared.

This campaign is conditional on a central event already accepted in data. It
does not reapply synthetic rules and it never lets SHIFT activity affect the
trigger decision. Step 2 retains every counterfactual event for the denominator
and writes a provenance-rich decision report under
`$SAMPLE_DIR/piggyback_decisions`. Step 3 filters to the selected recorded
readouts; unchanged standard digitization, RAW packing/unpacking, and
reconstruction determine which delayed SHIFT hits survive relative to the
central BX-0 L1A. `SHIFT_TIMING_BX_OFFSET=0` and
`SHIFT_TIMING_PHASE_NS=0.0` are the nominal coincident control. A positive
offset moves the complete physical SHIFT event later while leaving the central
trigger and pileup at BX 0; a negative offset moves SHIFT earlier. Hits on
either side of the L1A may survive when they fall in the real subsystem sample
buffers. They are not removed merely because their time is before the L1A.
This measures
conditional reconstruction performance, not the absolute probability for a
SHIFT collision to coincide with a recorded central event. The ordinary
recorded trigger source and simulated pileup occupancy are currently sampled
independently. Therefore this production does not reproduce event-by-event
correlations between the central event's trigger class and detector occupancy;
treat that as an occupancy systematic, or replace the independent mixing with
a validated data-overlay design before making a data-level absolute claim.

To measure the complete physical timing response, make paired campaigns with
fixed generator, Geant4, pileup, and trigger seeds, changing only the physical
SHIFT arrival offset. Also set `PILEUP_SEQUENTIAL=1`: the mixing seed fixes the
random draws, while CMSSW's sequential secondary source makes matching jobs
read the same ordered pileup events from the same manifest. Every point must
rerun Steps 1 through 4 because the shift is applied before Geant4; this keeps
standard pileup at BX 0 and avoids modifying CMSSW mixing or electronics code.
For example:

```bash
GENERATOR_SEED=13579
SIMULATION_SEED=24680
PILEUP_SEED=86420
PILEUP_SEQUENTIAL=1
TRIGGER_TIMELINE_SEED=24680

# Coincident reference
CAMPAIGN_NAME="${PROCESS}_piggybackCentral_bx0_2023" \
SHIFT_TIMING_BX_OFFSET=0 SHIFT_TIMING_PHASE_NS=0.0 \
  ./run_condor.sh

# Central L1A occurs 25 ns before the nominal SHIFT arrival
CAMPAIGN_NAME="${PROCESS}_piggybackCentral_bxPlus1_2023" \
SHIFT_TIMING_BX_OFFSET=1 SHIFT_TIMING_PHASE_NS=0.0 \
  ./run_condor.sh --prebuilt
```

The decision JSON records the signed SHIFT-arrival difference from the BX-0
L1A and explicitly records that the electronics configuration was not changed.
This full physical scan includes all standard timing-dependent behavior from
Geant4 onward. Use the existing no-pileup same-SimHit scan when the question is
strictly which losses were introduced by digitization, BX assignment, and RAW
readout, with the simulated detector crossings held exactly fixed.

### LSS material and field comparison

LSS transport is off unless at least one of `SHIFT_LSS_MATERIAL_MODE` and
`SHIFT_LSS_FIELD_MODE` is explicitly enabled. The workflow validates one
common origin and rotation and passes it to Step-1 Geant4 simulation and the
Step-4 SHIFT propagators. External material also enables detailed Geant4e
navigation on the target leg. It is mutually exclusive with the older
`SHIFT_REFIT_GEOMETRY_TARGET_MATERIAL=1` ablation.

The provisional IR1/ATLAS fixture additionally requires:

```bash
SHIFT_LSS_GDML_FILE=PhysicsTools/ShiftLssGeometry/data/validated_lss.gdml
SHIFT_LSS_GDML_SHA256=recorded_64_character_digest
SHIFT_LSS_ARTIFACT_ORIGIN_IN_MODEL_CM=converter_x,converter_y,converter_z
SHIFT_LSS_MODEL_ORIGIN_CM=x,y,z
SHIFT_LSS_MODEL_TO_CMS=r00,r01,r02,r10,r11,r12,r20,r21,r22
SHIFT_LSS_MINIMUM_ABS_Z_CM=2750
SHIFT_LSS_MATERIAL_BOUNDARY_ABS_Z_CM=14800
SHIFT_LSS_GEANT4E_MAXIMUM_PATH_LENGTH_CM=20000
SHIFT_LSS_FIELD_SCALE=reviewed_signed_scale
```

Do not copy the numerical placeholders into a campaign. The artifact origin
must come from the matching conversion report. The model transform, source
side, boundary, and polarity must be documented and reviewed first. The GDML
must be installed below `CMSSW_SRC` and always runs with overlap checks.
For every enabled LSS Step-4 job, the workflow loads the archived Step-1
configuration and the newly resolved Step-4 configuration with
`scripts/audit_lss_resolved_configs.py`. It fails before reconstruction if
their contract hashes or recorded settings differ, or if a selected geometry,
field, or reconstruction-transport object is absent.

Use four paired campaigns with identical generator and simulation seeds:

| Control | `SHIFT_LSS_MATERIAL_MODE` | `SHIFT_LSS_FIELD_MODE` |
| --- | --- | --- |
| CMS only | `none` | `none` |
| Material only | `external` | `none` |
| Field only | `none` | `ir1_atlas_proxy` |
| Material and field | `external` | `ir1_atlas_proxy` |

The middle two are diagnostic ablations; only the combined row represents the
complete proxy. Keep every result labelled IR1/ATLAS until an authoritative
Run-3 IR5 model passes the same gates.
Do not combine offsets with an assumed probability yet: the current recorded
ZeroBias source establishes a real accepted central readout, but it does not
provide an unbiased distribution of central-trigger times relative to an
independent SHIFT collision.

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
proxy. By default this keeps all currently available replicas at
`PILEUP_RSE=T2_CH_CERN`; a small positive max-files value is useful only for
focused tests:

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

Step 2 uses the slim transient `GENRAW` event content. It retains packed RAW,
HLT `TriggerResults`, pileup summaries and exact pileup-playback provenance,
plus the signal HepMC, SimTracks, SimVertices and PSimHits needed by the SHIFT
analysis. The Run-3 simulated RPC digis are retained explicitly because the
Step-3 `muonRPCDigis` merger consumes them outside `rawDataCollector`.
`mix:MergedTrackTruth` is deliberately dropped: canonical Step 3 does not run
`RECOSIM`, and SHIFT truth association uses the signal `g4SimHits` products
directly. This changes persisted intermediate content only; DIGI, L1,
DIGI2RAW, HLT and detector/electronics configuration remain unchanged.

For a bounded SimHit-to-RAW audit, set `SHIFT_READOUT_DIAGNOSTICS=1` on Step 2.
This default-off mode persists the standard pre-pack muon digis, local
primitives, regional candidates, and uGMT candidates; it does not reconfigure
any digitizer, emulator, packer, BX range, or trigger rule. Unpack the resulting
RAW with `scripts/shift_readout_unpack_cfg.py`, then run
`scripts/analyze_shift_readout_capture.py`. Compare timing points with
`scripts/classify_shift_multi_readout.py`; it fails closed if the paired files
do not contain identical per-muon non-timing SimHit fingerprints.

For the next fixed-trigger boundary, run
`scripts/analyze_shift_trigger_funnel.py` on each unpacked diagnostic file and
combine reports with `scripts/classify_shift_trigger_readouts.py`. The analyzer
tests chamber-compatible CSC correlated LCTs across the real pack/unpack
boundary. It uses the standard CMSSW comparison convention of emulator BX
minus 6 versus the readout-relative RAW BX; this is a representation conversion,
not retiming. DT primitives are simulated-side diagnostics because standard
`RawToDigi` does not expose a post-RAW DT primitive collection here. Regional
and uGMT counts are event-global and must not be called signal-truth matches or
proof of HLT/DAQ acceptance.

For the conditional electronics-response control, reuse one exact nominal
Step-1 file in distinct Step-2 campaigns and vary only the reference offset:

```bash
baseline_step1=/absolute/path/events_step1_part0000.root

for reference_bx in 0 1; do
  CAMPAIGN_NAME="same_simhit_bx${reference_bx}_2023" \
  PILEUP_MODE=none \
  SHIFT_READOUT_DIAGNOSTICS=1 \
  SHIFT_SIMHIT_REFERENCE_INPUT="$baseline_step1" \
  SHIFT_SIMHIT_REFERENCE_BX_OFFSET="$reference_bx" \
  SHIFT_SIMHIT_REFERENCE_PHASE_NS=0.0 \
    ./run_step2_digi_raw.sh 0 10
done
```

This producer copies DT, CSC, RPC, and GEM PSimHits and changes only their
`timeOfFlight` before the unchanged standard mixing/digitization chain. It is
restricted to no-pileup controls and is not a replacement for Step-1 physical
timing or detector simulation.

For a complete BX/phase control, the resumable local runner performs Step 2,
standard RAW unpacking, truth-linked digi capture, and CSC trigger-funnel
analysis in isolated point directories. It requires an already prepared CMSSW
runtime and never rebuilds it:

```bash
CMSSW_PREPARED=1 ./scripts/run_shift_readout_response_grid.py \
  "$baseline_step1" /tmp/shift_readout_integer_grid \
  --offsets=-5:24 --phases=0,6.25,12.5,18.75 \
  --events 10 --workers 2
```

For a high-statistics reconstruction-efficiency scan, use the separate
test-only runner. It copies each Step-1 file to local scratch once, reuses the
same SimHits at every requested delay, and runs the unchanged 2023
digitization, RAW packing/unpacking, and SHIFT reconstruction for each point.
It writes compact NanoAOD files directly, without persistent Step-2 or Step-3
files and without the unrelated PAT/EXONanoAOD work:

```bash
./scripts/run_shift_reco_delay_scan.py \
  /absolute/path/to/the/baseline/sample \
  /absolute/path/to/shift_delay_scan \
  --delays=-100:100:10 --files 100 --workers 2

../tea_shift_cmssw/utils/shift_delay_efficiency_plotter.py \
  /absolute/path/to/shift_delay_scan
```

The delay is in ns and may be positive or negative. The runner converts it to
the exact BX plus phase representation; for example, `-6.25 ns` becomes BX
`-1` plus phase `18.75 ns`. Each output embeds that conversion and the original
Step-1 path in NanoAOD run metadata and in a JSON sidecar. The plotter applies
the same J/psi truth matching and topology definitions as
`ShiftHistogramsFiller::FillEfficiencies` and writes the binomial counts as
JSON beside the muon and dimuon PDFs. It refuses to compare points whose
embedded delays disagree with their directories or whose Step-1 input sets are
not identical.

This scan is the no-pileup, same-SimHit control. It isolates the response of
the fixed electronics/readout and reconstruction to delay. It is not the final
piggyback result with central-collision occupancy. Validate selected points
against the full four-step physical-timing workflow before interpreting the
curve as the production result.

After producing a rule-enabled timeline whose analysis BX range maps to the
response offsets, convolve the independent inputs with:

```bash
./scripts/classify_shift_event_capture.py \
  /tmp/rule_timeline.jsonl \
  --response-dir /tmp/shift_readout_integer_grid \
  --phase-ns 6.25 \
  --output /tmp/shift_event_capture_phase_6p25.json
```

The classifier requires embedded same-SimHit provenance, exact signal
identities and non-timing SimHit fingerprints, complete candidate-L1A grid
coverage, a rule-enabled timeline, and a structured fill mask. It reports
candidate L1A, rule-accepted RAW, and HLT-persistence-proxy layers separately,
including DT/CSC/RPC loss counts. One explicit phase is selected per output;
run the classifier once per phase. RPC/GEM digi BX values can establish
`split_within_readout`; DT TDC and CSC time-bin closure remain separate.

As checked on 2026-08-19, the default 2023 dataset contains 999,856,000 events
in 27,774 DBS files, but some blocks have no current file replicas. Do not use
that unfiltered inventory for production. The current `T2_CH_CERN` manifest
contains 3,713 available disk PFNs. The 2022 preset also has CERN disk replicas.
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
null until a separately validated trigger-rule engine is applied. A final
timeline must also use a physical 3564-slot fill mask. Run 369943 maps to fill
9017 in `config/run3_trigger_run_fill_map.json`, using CMS BRIL data tag
`24v2`. Normalize the matching official LPC response:

```bash
./scripts/fetch_lpc_bunch_mask.py 9017 \
  --output /tmp/fill_9017_ip5_bunch_mask.json
```

Before selecting a reference slot, scan every filled slot and group identical
nearby-collision patterns:

```bash
./scripts/scan_shift_reference_slots.py \
  /tmp/fill_9017_ip5_bunch_mask.json \
  --beam 2 --start-bx -24 --end-bx 5 \
  --output /tmp/fill_9017_beam2_reference_slots.json
```

The reported `uniform_filled_slot_fraction` is only a structural diagnostic.
It is explicitly not physics-valid weighting because the normalized LPC mask
does not contain authoritative per-bunch intensities.

For conditional central-piggyback production, sample only IP5-colliding slots.
The ordinary event is already recorded, so its trigger-rule decision must not
be synthesized a second time:

```bash
./scripts/sample_zero_bias_trigger_timeline.py \
  /tmp/zero_bias_run369943.jsonl \
  --l1-menu /tmp/zero_bias_l1_menu_run369943.json \
  --output /tmp/piggyback_central_seed24680.jsonl \
  --start-bx 0 --end-bx 0 --signal-events 10 --seed 24680 \
  --colliding-bx-mask /tmp/fill_9017_ip5_bunch_mask.json \
  --run-fill-map config/run3_trigger_run_fill_map.json \
  --reference-slot-mode uniform-colliding --shift-beam 2 \
  --trigger-rule-mode recorded
```

The sampler verifies the normalized LPC provenance, beam occupancy, IP5
collision subset, file digest, orbit wrapping, and trigger-library run-to-fill
match, and embeds them in timeline metadata. `recorded` means that the source
event's real L1A and TCDS history are provenance for an already-made decision;
the four-rule proxy is not reapplied. Uniform colliding-slot weights remain
provisional because the LPC mask contains no per-bunch luminosities. A fixed
slot remains available only as a mechanism control by using
`--reference-slot-mode fixed --reference-bx-slot SLOT`.

Do not pair a convenient fill with an unrelated trigger run. Omitting the mask
treats every BX as colliding, while legacy `--colliding-bx-file` accepts only
relative BX values. Both modes are software fixtures and are rejected by the
final classifier unless `--allow-all-colliding-fixture` is explicit.

For a separate absolute-opportunity/rate study using the synthetic rule engine,
retain the relevant analysis range and add a complete causal warm-up:

```bash
TRIGGER_RULE_MODE=run3
TRIGGER_RULE_HISTORY_START_BX=-264  # 240 BX before analysis start -24
```

The `run3` preset reproduces the four spacing constraints encoded by CMSSW's
`TriggerRulePrefireVetoFilter`, but remains marked as requiring run-period TCDS
validation. It must not be used for a final Run-3 result until that validation
is complete.

The corrected physical timing, Run-3 trigger-rule warm-up, TCDS validation and
detector-response implementation sequence is specified in the
[top-level trigger document](../../SHIFT_TRIGGER.md).

Run the stages in order from the workflow repository:

```bash
./run_step1_generation.sh 0 10
./run_step2_digi_raw.sh 0 10
./run_step3_aod.sh 0 10
./run_step4_exonanoAOD.sh 0 10
```

Before any large Condor submission, run the non-mutating preflight:

```bash
./run_condor.sh --check
```

It validates the conditional piggyback contract, trigger library/menu,
run-to-fill association, physical mask, reference-slot mode, deterministic
seed, and reconstruction filter without building, cleaning logs, or contacting
Condor. A production submission must use a new `CAMPAIGN_NAME`; the default is
`${PROCESS}_piggybackCentral_bx0_phase0_2023_v1` so it cannot silently reuse the older
trigger-proxy outputs.

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
runtime fingerprint. It also makes a read-only workflow snapshot for the
workers, so later edits to the main checkout cannot alter running jobs. Never
rebuild or relink the shared CMSSW release while jobs are running. The cleanup
helper also preserves logs whenever it cannot reliably query the scheduler.

For each production, validate the Condor event log and payload publication
messages in addition to counting EOS files. Do not merge until every expected
job has terminated normally and representative destination ROOT files have
the expected trees, branches, and entries.
