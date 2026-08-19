# CMS+SHIFT timing work: handoff for 2026-08-19

This note records the workspace state at the end of 2026-08-18.  It is meant
to be sufficient to resume the pileup and trigger-timeline work without
reconstructing decisions from shell history.  No commit was made; preserve the
current dirty working tree.

## Objective and agreed architecture

The SHIFT collision remains the signal event.  Ordinary CMS pp collisions are
handled through two separate mechanisms:

1. Central CMS minimum-bias GEN-SIM is mixed before digitization with the
   standard CMSSW `MixingModule`.  This represents detector occupancy,
   out-of-time pileup and its effect on digitization/reconstruction.
2. Real ZeroBias data supplies empirical, correlated L1 and HLT decision
   records.  Whole decision records will be sampled onto an ordered sequence
   of simulated bunch crossings.  Prescales, deadtime/trigger rules, L1A,
   readout, HLT acceptance and final storage remain distinct states.

Do not generate an ad hoc mixture of SoftQCD, Drell-Yan, ttbar and Higgs
events.  Centrally produced inclusive minimum-bias interactions already give
the detector input, while ZeroBias data measures the inclusive probability and
correlation of real triggers.  Also do not sample trigger paths as independent
Bernoulli variables: the correlations within each recorded event are the
point of the ZeroBias proxy.

The phrase "force a pileup event to trigger" should mean assigning a sampled
CMS reference-BX decision/readout state to the simulated timeline.  It should
not modify the physics objects of an overlaid pileup interaction.

## Work completed

### Standard pileup mixing

Step 2 has an opt-in `PILEUP_MODE=run3_2024`.  It passes the standard CMSSW
pileup scenario
`2024_25ns_RunIII2024Summer24_PoissonOOTPU` to `cmsDriver.py`, accepts DAS,
file-list or explicit PFN input, and sets a reproducible mixing seed.  The
default remains `PILEUP_MODE=none`, so existing no-pileup production does not
change implicitly.

The intended central dataset is:

```text
/MinBias_TuneCP5_13p6TeV-pythia8/RunIII2024Summer24GS-140X_mcRun3_2024_realistic_v20-v1/GEN-SIM
```

DAS reported 2,499,014,000 events in 43,087 files.  A helper now prepares an
atomic, validated input manifest on the submit host:

```bash
./scripts/prepare_pileup_file_list.sh \
  "$PWD/config/pileup_files_run3_2024.txt" 0
```

The Condor submit path propagates `PILEUP_MODE`, `PILEUP_SCENARIO`,
`PILEUP_DATASET`, `PILEUP_INPUT` and `PILEUP_SEED` explicitly.  Workers should
consume a versioned file list instead of querying DAS with a user proxy.

### Pileup mechanism validation

The correct 2024 dataset had only tape replicas, so the mechanism test used
one disk-resident official 2025 Run-3 minimum-bias file while retaining the
2024 mixing profile.  This was only a software-path test and is not a
physics-valid input.

For one SHIFT event with `PILEUP_SEED=86420`:

- Step 2 completed `DIGI,L1,DIGI2RAW,HLT:@relval2024`.
- The readable Step-2 output was 25,549,795 bytes.
- The realized pileup window was BX -12 through +3.
- There were 40-69 interactions per BX, 61 at BX 0, and
  `trueNumInteractions=54.8763`.
- Six of eight scheduled paths accepted, including Physics, Random and
  ZeroBias HLT paths.
- The original SHIFT `SimTrack`s remained encoded event `(0,0)` and pileup
  truth had nonzero event identifiers across BXs.
- `mix:MergedTrackTruth` contained 172,388 pileup `TrackingParticle`s but no
  SHIFT `(0,0)` particle.  Dedicated SHIFT truth association is therefore
  still required before efficiency measurements.
- Step 3 consumed the mixed output and produced a readable 22,127,081-byte
  AODSIM file.

### Temporary Rucio pileup replica request

One exact Summer24 pileup file was requested at `T2_CH_CERN`:

```text
Rule ID: bcd7943660744e5abec93117af3c920e
State at handoff: WAITING_APPROVAL
Destination: T2_CH_CERN
Lifetime: 1209600 seconds (14 days)
Expiry: 2026-09-01 13:51:31
Size: 11,373,506,164 bytes
Adler-32: 8883ba65
Source currently available at: T1_UK_RAL_Tape
```

The requested file DID is:

```text
cms:/store/mc/RunIII2024Summer24GS/MinBias_TuneCP5_13p6TeV-pythia8/GEN-SIM/140X_mcRun3_2024_realistic_v20-v1/2810004/c4064bce-66c9-4f24-b0cf-e02360eca003.root
```

Because the account has no disk quota, the rule was submitted with
`--ask-approval`.  `WAITING_APPROVAL` means that no copy is transferring yet;
do not use it as though it were staged.

Check it with:

```bash
source /cvmfs/cms.cern.ch/rucio/setup-py3.sh
rucio rule show bcd7943660744e5abec93117af3c920e
```

### ZeroBias trigger-decision extraction

Three new scripts implement the first trigger-proxy component:

- `scripts/zero_bias_unpack_cfg.py` runs only the standard Stage-2 uGT RAW
  unpacker on FED 1404.  It does not re-emulate L1.  Its small EDM output keeps
  `GlobalAlgBlk`, `GlobalExtBlk` and the original HLT `TriggerResults`.
- `scripts/extract_zero_bias_trigger_bits.py` writes streaming JSON Lines.
- `scripts/run_zero_bias_trigger_extract.sh` runs both stages with a temporary
  EDM file and cleans that temporary file afterward.

Each JSONL event retains:

- source event index, run, lumi, event, orbit and absolute BX;
- every stored uGT relative BX slice;
- complete fired-bit sets for initial, post-prescale and final algorithm
  decisions;
- final-OR/pre-veto/veto flags and prescale column;
- L1 menu and firmware UUIDs;
- external trigger bits;
- an HLT menu identifier and the complete accepted/error path sets.

HLT path lists are emitted once per distinct menu.  The event records refer to
that menu ID, avoiding repetition of hundreds of names.  The algorithm stages
follow the standard hardware payload semantics: initial is before prescales
and masks, intermediate is after prescales, and final is after masks.

### ZeroBias validation and rejected input

The first technical file, from Run2024I run 386472, exposed only a small
cosmics/random menu.  It was useful for debugging but was explicitly rejected
as a trigger-probability input.  This is a selection pitfall: membership in a
`/ZeroBias/Run2024*-v1/RAW` dataset alone does not establish representative pp
collision conditions.

The valid end-to-end software test used this disk-resident collider-era file:

```text
Dataset: /ZeroBias/Run2024G-v1/RAW
Run/lumi: 383812 / 161
File: /store/data/Run2024G/ZeroBias/RAW/v1/000/383/812/00000/a6d74641-5c7f-46f6-ae57-e8e7e4416ee1.root
Replica used: T1_US_FNAL_Disk via cmsxrootd.fnal.gov
```

The exact test was:

```bash
cd /afs/cern.ch/work/j/jniedzie/private/shift_cmssw/CMSSW_17_0_0_pre4/src
source /cvmfs/cms.cern.ch/cmsset_default.sh
eval "$(scram runtime -sh)"

../../shift_cmssw_workflow/scripts/run_zero_bias_trigger_extract.sh \
  root://cmsxrootd.fnal.gov//store/data/Run2024G/ZeroBias/RAW/v1/000/383/812/00000/a6d74641-5c7f-46f6-ae57-e8e7e4416ee1.root \
  /tmp/shift_zero_bias_collision_100.jsonl 100 \
  /ZeroBias/Run2024G-v1/RAW
```

Results:

- 100 events and one HLT menu were written.
- The HLT menu contained 837 paths.
- BX 0 had 48 distinct initial decision vectors, 48 distinct intermediate
  vectors and six distinct final vectors.
- All 100 events had nonempty initial/intermediate vectors.
- 91 events had a nonempty final vector and asserted final OR.
- The JSONL file was 412,930 bytes.

This validates product access, schema and preservation of correlations.  It
does not validate probabilities or rates: 100 events from one lumi are far
too few, and no luminosity/fill weighting has been implemented.  EDM file
order must not be assumed to be chronological.

Python compilation, shell syntax and `git diff --check` passed.  No CMSSW C++
package rebuild was needed for this component.

## Current working-tree state

Modified files:

```text
condor/shift_cmssw.sub
config/workflow.env
docs/cms_shift_timing_overlay_plan.md
docs/generation_instructions.md
run_condor.sh
run_step2_digi_raw.sh
```

New, untracked files:

```text
scripts/extract_zero_bias_trigger_bits.py
scripts/prepare_pileup_file_list.sh
scripts/run_zero_bias_trigger_extract.sh
scripts/zero_bias_unpack_cfg.py
docs/cms_shift_timing_handoff_2026-08-18.md
```

Do not reset or discard this tree.  Review all changes together before making
an eventual commit.  Temporary validation outputs under `/tmp` are useful
evidence but are not durable campaign inputs.

## Work to do next

Proceed in this order.

### 1. Resolve and validate the exact Summer24 pileup input

1. Recheck Rucio rule `bcd7943660744e5abec93117af3c920e`.
2. If it is still `WAITING_APPROVAL`, no physics-valid pileup pilot is possible
   yet; follow up with the site/data-management approver rather than silently
   falling back to the 2025 file.
3. Once the rule reaches `OK`, verify the active `T2_CH_CERN` replica and PFN.
4. Build a one-file manifest containing exactly the staged Summer24 LFN.
5. Run paired one-event `PILEUP_MODE=none` and `PILEUP_MODE=run3_2024` jobs with
   fixed signal, simulation and pileup seeds.
6. Validate logs, readable Step-2 and Step-3 ROOT content, pileup BX/multiplicity
   and SHIFT/pileup identity before increasing the sample.

### 2. Build a representative ZeroBias reference library

1. Select certified pp collision runs/lumis across the intended 2024 eras;
   exclude cosmics, commissioning, special and noncolliding configurations.
2. Record run, fill, filling scheme, instantaneous pileup/luminosity, active
   bunch slot, HLT menu and L1 prescale column for every input range.
3. Extract enough events from several files/lumis to measure rare trigger
   combinations.  Determine the required statistics from target probability
   precision instead of choosing an arbitrary event count.
4. Map L1 bit numbers to algorithm names using the exact menu identified by
   the stored menu UUID/conditions.  The current JSONL intentionally contains
   bit numbers but not names.
5. Add validation summaries: per-bit/path rates, joint/coincidence matrices,
   menu/prescale consistency, duplicate-event checks and coverage by
   luminosity/pileup bin.

### 3. Implement the empirical correlated sampler

1. Define explicit conditioning bins, initially at least menu, prescale column,
   colliding-bunch status and pileup/luminosity range.
2. Sample one complete ZeroBias event record per simulated CMS BX, preserving
   all L1 and HLT correlations.  Never sample individual bits or paths
   independently.
3. Give the sampler a fixed/random seed interface and record the seed plus the
   source event identity selected for every BX.
4. Separate raw initial algorithms, post-prescale algorithms, final hardware
   algorithms/final OR, L1A/readout, HLT paths and stored datasets in the
   output schema.
5. Validate closure by resampling held-out ZeroBias data and comparing both
   marginal rates and joint trigger correlations.

### 4. Reproduce the ordered bunch-crossing and fixed Run 3 deadtime timeline

1. Introduce a versioned filling-scheme mask and an explicit SHIFT collision
   anchor.  Derive the approximately 20-BX SHIFT-to-CMS separation from source
   position, beam direction and bunch spacing rather than hardcoding 20.
2. Generate the required BX window before and after CMS, including relevant
   post-CMS bunches and empty/noncolliding slots.
3. Apply the sampled candidate decisions in increasing orbit/BX order.
4. Encode the authoritative Run 3 trigger-rule/deadtime behavior unchanged and
   apply it separately from the empirical collision decision. Rule parameters
   require authoritative CMS configuration or trigger-expert input; do not
   infer them solely from sparse recorded events and never tune them for SHIFT.
5. Produce an auditable per-BX state table explaining every accepted or
   blocked CMS/SHIFT readout and which earlier accept caused a block.

### 5. Couple the timeline to detector integration/readout studies

The empirical trigger timeline answers which BX becomes a reference readout;
it does not itself answer which delayed SHIFT hits are present in that event.
For each accepted reference BX, run or select the corresponding controlled
timing/BX detector response and measure retained digis, trigger primitives,
RAW content and reconstruction.  This is where questions such as "a CMS
collision triggers tens of ns after SHIFT; what fraction of SHIFT hits lies in
its electronics window?" are answered.

Keep these two axes separate:

- standard pileup mixing changes occupancy and reconstruction;
- the sampled trigger/deadtime timeline chooses candidate readout BXs;
- detector electronics simulations determine which SHIFT hits survive in each
  chosen BX window.

## Minimal restart checklist

```bash
cd /afs/cern.ch/work/j/jniedzie/private/shift_cmssw
git -C shift_cmssw_workflow status --short
git -C shift_cmssw_workflow diff --check

source /cvmfs/cms.cern.ch/rucio/setup-py3.sh
rucio rule show bcd7943660744e5abec93117af3c920e

cd CMSSW_17_0_0_pre4/src
source /cvmfs/cms.cern.ch/cmsset_default.sh
eval "$(scram runtime -sh)"
python3 -m py_compile \
  ../../shift_cmssw_workflow/scripts/zero_bias_unpack_cfg.py \
  ../../shift_cmssw_workflow/scripts/extract_zero_bias_trigger_bits.py
bash -n ../../shift_cmssw_workflow/scripts/run_zero_bias_trigger_extract.sh
```

The immediate decision point tomorrow is the Rucio rule state.  In parallel
with any transfer approval delay, the safe next coding task is the
menu-aware ZeroBias library validator and correlated-record sampler.  Do not
start broad pileup or ZeroBias production until the exact Summer24 input and
representative certified run/lumi selection are both validated.

## Continuation on 2026-08-19

The Rucio rule was rechecked and remains `WAITING_APPROVAL`; there is still no
`T2_CH_CERN` replica and the only copy is at `T1_UK_RAL_Tape`.  No Summer24
physics pilot was attempted.

The menu-aware validator and whole-record sampler described above are now
implemented:

```text
scripts/zero_bias_trigger_library.py
scripts/validate_zero_bias_trigger_library.py
scripts/sample_zero_bias_trigger_timeline.py
tests/test_zero_bias_trigger_library.py
```

Run-dependent L1 bit-name extraction was also implemented without a custom
CMSSW build:

```text
scripts/zero_bias_l1_menu_cfg.py
scripts/extract_zero_bias_l1_menu.py
```

For run 383812, `auto:run3_data_prompt` resolved 408 algorithms and matched
menu UUID `d10cf9fc` and firmware UUID `e4cb66da` from the raw payload.  The
100-event real-data library passed with zero errors as one trigger group.  A
fixed-seed BX -24 through +5 candidate timeline was produced successfully;
deadtime remains explicitly unapplied.  Four automated tests pass.

The next work is no longer the sampler skeleton.  It is to add certified
run/fill/filling-scheme and luminosity/pileup conditioning, extract a
statistically meaningful multi-lumi library, validate resampling closure, and
only then implement an authoritative trigger-rule/deadtime layer.  The Rucio
approval remains the independent blocker for the exact Summer24 occupancy
pilot.
