<!-- Code style: AI-code. AI-generated usage for the qualified singleton worker. -->
# Frozen reconstruction probes

`scripts/run_reco_probe_worker.sh PERSISTENT_IWD` runs one already prepared
CMSSW reconstruction job. It reuses the worker-local CMSSW runtime and a small
SHA-pinned overlay. It does not submit jobs, choose events, change physics
options, retry failures or validate physics results.

Freeze the shell and `scripts/reco_probe_worker.py` together. Transfer the latter
as `reco_probe_worker.py`, plus `job.json`, one AOD, its original Step4 template,
the tiny overlay config and `native_overlay.tar.gz`. All transfer sources must
be in persistent, schedd-readable storage such as AFS; frontend `/tmp` paths
are not readable by the remote schedd. Preserve the exact frozen files while
queued jobs or readers need them.

The JSON contract uses `id`, `events`, `config_sha256`, `input`, `template`,
`output`, `input_sha256`, `template_sha256`, `transfer_input`,
`transfer_template`, `transfer_config`, `base_uri`, `base_sha256`,
`overlay_sha256` and `required_libraries` (library basename to SHA256).
The three `transfer_*` values are the transferred file basenames. The config
must contain the exact original template/input/output strings; only these
strings are relocated. Extra provenance fields remain allowed. Runtime archives
must be trusted and SHA-pinned, with the qualified `CMSSW_17_0_0_pre4/src` and
`overlay` layout. This helper does not make arbitrary archives safe to run.

Use an absolute persistent `initialdir` and pass the same path as the argument.
Set `+SendCredential = True`, transfer the files above, request measured worker
memory with margin and enough disk, set failure holds and disable automatic
release. The shell runs only private `scram b ProjectRename` relocation, never
a shared release build. Independent frozen singleton jobs can run in parallel.

`bootstrap.json`, `relocation.log`, `native.log`, `nano.root` and terminal
`run.json` live in the persistent directory. The receipt keeps the original
job/config identity, output digest, actual library mappings and observed peak
RSS. Download, hash, relocation or environment failures produce a failed
receipt with `native_started: false`; shell failure handling preserves an
existing native receipt. An unexpected run-finalization failure records
`native_started: null` (unknown), together with observed output/log presence;
it must not be treated as an unused event attempt. A missing receipt after a worker/AFS/host failure is
unknown state, so retain scheduler logs and review before retrying.

The native process retains the qualified 1200-second guard and 16 MiB log
budget. Submission alone is not execution: verify a real execute/bootstrap and
a closed readable ROOT/accounting gate before wider admission. Runtime success
and library hashes do not establish reconstruction quality.

The frozen predecessor completed a native singleton panel. This maintained
promotion has synthetic orchestration checks; its new failure paths have not
been qualified by additional native events. Active studies keep their frozen
worker until a separately reviewed deployment replaces it.

Run the event-free synthetic checks with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_reco_probe_worker.py
bash -n scripts/run_reco_probe_worker.sh
```
