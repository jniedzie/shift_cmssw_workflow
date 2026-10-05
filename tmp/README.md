# Temporary work

Create `tmp/<YYYYMMDD-task>/` for one-off scripts, probes, generated test
configurations, and disposable outputs. Everything here except this guide is
ignored by Git. Add a short note in each task directory with its purpose,
owner, and cleanup condition.

Reusable tools belong in `scripts/`; regression tests belong in `tests/`.
Review, document, and validate a script before promoting it. Keep campaign
state, frozen deployments, receipts, and persistent validation evidence
outside this directory. Private FLUKA/geometry payloads must stay in the
private source directory specified in `../AGENTS.md`.

Check references and running jobs before removing a task directory. Preserve
anything needed to resume a campaign or reproduce a persistent result.
