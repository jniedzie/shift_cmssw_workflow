# SHIFT project instructions

These instructions apply to every task in this repository and must be read
before changing the workflow, CMSSW configuration, simulation, trigger model,
or reconstruction.

## Required branch

Always work directly on `main` in this repository. Do not create task branches
or use Git worktrees unless the user explicitly overrides this policy.

## Non-negotiable physics objective

The goal is to measure how well SHIFT muons can be triggered, recorded, and
reconstructed **under the actual Run 3 CMS detector constraints**.

- Never change, loosen, retime, optimize, or invent detector-electronics
  integration windows, BX assignment behavior, buffering, readout behavior,
  trigger rules, prescales, deadtime, or rate limits to improve SHIFT
  acceptance.
- Treat those quantities as fixed external constraints. Use authoritative Run
  3 configurations, conditions, firmware documentation, or measured data to
  model them faithfully. If an authoritative value is unavailable, keep the
  result explicitly unknown or provisional; do not choose a favorable value.
- Configuration may select the appropriate real Run 3 year/era or reproduce a
  documented detector setting. It may also vary the physical SHIFT collision
  time or the reference BX to measure acceptance. Neither operation authorizes
  changing the CMS electronics or trigger system itself.
- `SHIFT_G4_MAX_TRACK_TIME_NS` and
  `SHIFT_G4_MAX_TRACK_TIME_FORWARD_NS` are Geant4 CPU-protection transport
  guards, not electronics timing windows. They may be raised only to prevent
  premature simulation loss; detector/readout acceptance must remain governed
  by the real Run 3 subsystem behavior.
- Pileup mixing is an occupancy/reconstruction study. The ZeroBias timeline is
  an empirical model of ordinary trigger activity. Electronics response and
  trigger-rule application must remain separate, auditable layers using the
  fixed real CMS behavior.
- Never describe a trigger-candidate sidecar as an electronics-timing result.
  A final claim requires the delayed SHIFT hits to pass through the unchanged
  detector integration, BX assignment, readout, and trigger constraints.

If a proposed implementation would alter any electronics or trigger rule,
stop and flag that it conflicts with the project objective instead of making
the change.

## Workflow safety

- Read `docs/generation_instructions.md` and the relevant timing/overlay note
  before launching or modifying a campaign.
- Do not edit live campaign configuration or rebuild the shared CMSSW release
  while jobs using it are running. Documentation-only edits are safe.
- Keep timing, occupancy, trigger-candidate, electronics-response, and offline
  reconstruction conclusions clearly separated in code, metadata, and reports.
