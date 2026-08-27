# Run-3 SHIFT trigger/readout implementation handoff (2026-08-27)

## Scope and fixed constraints

The immediate objective is to measure whether an ordinary Run-3 CMS L1 accept
can record a delayed SHIFT muon completely.  Detector integration windows, BX
assignment, RAW readout windows, trigger rules, prescales, deadtime and HLT
behavior are fixed Run-3 constraints.  They must be reproduced, never widened
or disabled to improve SHIFT acceptance.

The Run-4/dedicated-SHIFT-trigger scenario is a later, separate comparison.  It
must not enter the Run-3 baseline configuration or results.

## Correct physical timing model

Use the central collision of the same bunch as time zero at the CMS interaction
point.  The bunch passage at the fixed target is not random.  For beam direction
`s_z = +/-1`, generated source position `z_src` and CMS reference `z_CMS`, the
production-time anchor is

```text
t_production = s_z * (z_src - z_CMS) / c.
```

For the nominal 148 m target this is approximately -493.7 ns for the beam that
travels from the target toward CMS, or -19.75 nominal 25 ns BX.  The exact value
must be derived from the generated source vertex, as the existing
`ShiftEventTimeProducer` does, rather than rounded to -20 BX.

The arrival time of detector signal `h` is

```text
t_h = t_production + integral_path(ds / (beta(s) c)) + t_detector_response.
```

The stochastic component is supplied by generated muon momentum/direction and
Geant4 transport (including path length and energy loss).  Do not draw an
independent random event-time or beta after generation.  Each muon in a dimuon
event can have a different arrival time and station-to-station span.

Central collisions occupy the real filling-scheme BX slots.  An ordinary
central trigger can therefore occur only at one of those BXs.  The physics
question is whether an *accepted* central L1A lies at a time whose unchanged
subsystem readout windows contain the required SHIFT signal.

## Run-3 trigger-rule model

`EventFilter/L1TRawToDigi/plugins/TriggerRulePrefireVetoFilter.cc` in the CMSSW
release checks recorded TCDS L1A histories using these rules:

| Rule | Constraint |
| --- | --- |
| 1 | no more than 1 accepted L1A in any 3 BX |
| 2 | no more than 2 accepted L1As in any 25 BX |
| 3 | no more than 3 accepted L1As in any 100 BX |
| 4 | no more than 4 accepted L1As in any 240 BX |

The CMSSW module diagnoses already-recorded data; it is not an MC timeline
simulator.  Before calling this the final 2023 rule set, validate it against
TCDS L1A histories from the selected certified 2023 run/period and record the
source/configuration provenance.  Do not infer or tune rule parameters from
the SHIFT acceptance result.

Apply candidate decisions in increasing orbit/BX order.  A candidate L1A at
BX `b` is rejected when accepting it would exceed any rule.  Store every
violated rule, the preceding accepted BXs counted by it and the causal prior
accept.  Prescale/mask decisions, trigger-rule acceptance, detector readout,
HLT acceptance and final storage remain distinct fields.

### Required warm-up

The analysis window is currently relative BX -24 through +5.  Starting with an
empty accepted-L1A history at -24 biases every rule, especially the 240-BX
rule.  A rule-enabled timeline must begin at least 240 BX before the analysis
window (for example, history start -264 for analysis start -24).  Warm-up BXs
must be sampled and processed but marked outside the analysis window.  Only BXs
whose complete preceding 240-BX history is present may enter denominators.

## Ordered implementation plan

### A. Trigger-rule foundation

1. Implement a small deterministic rule engine with the four versioned rules.
2. Add unit tests for boundary cases at exactly 3, 25, 100 and 240 BX, multiple
   simultaneous violations, non-candidate BXs and independent event timelines.
3. Extend the timeline sampler with an explicit rule mode and history-start BX.
4. Write per-BX candidate, accepted/blocked, violated-rule and accepted-history
   provenance.  Preserve pre-rule output as an explicit control mode.
5. Refuse rule-enabled generation when the warm-up interval is insufficient.

### B. TCDS data validation

1. Add the standard `TcdsRawToDigi` unpacker to the ZeroBias extraction job.
2. Persist `TCDSRecord`, including current orbit/BX and the recent L1A history.
3. Add the history to trigger-library JSONL and validate ordering/delta-BX
   consistency.
4. Compare observed histories with the four-rule engine for Run 369943 and at
   least one additional certified 2023 run/conditions group.
5. Record whether the rule set is common across 2022/2023/2024 or split the
   preset by run period.  Unknown applicability remains provisional.

### C. Filling scheme and trigger activity

1. Produce a versioned colliding-BX mask for the actual 2023 fill/run used by
   the trigger library.
2. Add fill, luminosity/pileup and bunch-slot metadata to the library grouping.
3. Replace the 100-event one-lumi seed with a statistically useful certified
   sample, grouped by menu UUID, firmware UUID, prescale column and run/lumi
   state.
4. Validate resampled marginal and joint trigger activity on held-out data.

This empirical layer chooses candidate central trigger BXs.  It does not
simulate electronics or claim that the SHIFT muon itself fired L1.

### D. Controlled detector-response scan

1. Use identical generator, simulation and pileup seeds across a bounded grid
   of integer BX offsets and intra-BX phases.
2. Keep the physical target anchor fixed; vary only the candidate reference
   L1A alignment used to map the detector response.
3. Trace SHIFT identity with `(EncodedEventId, SimTrackId)` through:

```text
SimHit -> digi/time/BX -> local trigger primitive/BX
       -> regional/uGMT candidate/BX -> packed RAW -> unpacked RAW
       -> HLT/offline object
```

4. Preserve the exact Run-3 digitizer, emulator, packing and HLT configuration.
5. Start with no-pileup single-muon closure, then paired no-pileup/pileup events.

### E. Event-capture classification

For every accepted ordinary L1A and each truth SHIFT muon, classify:

- `complete_one_readout`: all required station content is in one readout;
- `split_within_readout`: content spans BXs but one real readout retains it;
- `split_across_readouts`: different accepted L1As retain different parts;
- `partial_trigger_rule_loss`: a needed adjacent readout was rule-blocked;
- `partial_electronics_loss`: signal was outside a fixed integration/readout
  window or failed digitization/primitive formation;
- `no_readout`: no accepted ordinary L1A retained usable signal.

Report the first failing stage and keep trigger-rule loss separate from
electronics, primitive, HLT and offline-reconstruction loss.

### F. Physical result

Convolve the detector response map with the generated muon kinematics and the
fill-aware, rule-filtered ordinary-trigger timeline.  Report efficiencies by
muon topology, beta/momentum, station pattern, arrival BX/phase and pileup.
Only after this closure should production be scaled.

## First-session completion criteria

The first implementation slice is complete when:

- the rule engine and sampler integration have passing unit tests;
- rule-enabled timelines enforce a complete 240-BX warm-up;
- every candidate BX has an auditable accept/block explanation;
- the ZeroBias unpacking configuration persists `TCDSRecord` and the extractor
  supports its history;
- a bounded real-data extraction validates the TCDS product and rule history,
  or the exact external-data blocker is documented;
- no electronics/readout acceptance claim is made from the timeline alone.

## Implementation status at handoff

Completed on 2026-08-27:

- `scripts/run3_trigger_rules.py` implements the four ordered spacing rules and
  reports every violated rule with the preceding accepted BXs it counted.
- `sample_zero_bias_trigger_timeline.py` supports `--trigger-rule-mode run3`,
  requires a 240-BX warm-up, marks warm-up versus analysis records, and fills
  `readout_after_trigger_rules` plus structured decision provenance.
- `TRIGGER_RULE_MODE` and `TRIGGER_RULE_HISTORY_START_BX` are serialized into
  Condor jobs.  The default remains the explicit pre-rule control because the
  run-period validation is not yet complete.
- ZeroBias extraction now runs `TcdsRawToDigi`, persists `TCDSRecord`, writes
  its 16-entry preceding-L1A history to JSONL and validates history ordering
  against the provisional rules.
- Ten unit/integration tests pass.  A real 100-event Run-369943/lumi-356 probe
  produced one trigger group with zero validation errors.  All histories were
  ordered and rule-consistent; the closest preceding L1A in this small sample
  was 7 BX away.  This validates extraction and consistency, but does not
  exercise the 3-BX boundary or establish the rule set for every 2023 period.
- A one-event rule-enabled software timeline covering history BX -264 through
  analysis BX +5 was generated successfully.  With every BX deliberately
  treated as colliding for that software test, 7 of 270 candidate records were
  accepted overall and 3 of 30 in the analysis window.  These counts are not a
  physics rate or acceptance result.

Still required before physics use:

- validate the rule preset on another certified 2023 run/conditions group and
  against authoritative run-period configuration or trigger-expert input;
- add the real filling-scheme mask and larger luminosity-conditioned trigger
  library;
- prevent reuse of an existing pre-rule timeline when rule configuration or
  provenance changes;
- implement the SimHit-to-digi/primitive/RAW timing funnel and event-capture
  classification described above.

## Restart commands

```bash
cd /afs/cern.ch/work/j/jniedzie/private/shift_cmssw/shift_cmssw_workflow
git status --short
git diff --check
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests/test_zero_bias_trigger_library.py
```

Before any CMSSW build or production pilot, confirm that no jobs are using the
shared release and inspect the resolved campaign paths and serialized Condor
environment.
