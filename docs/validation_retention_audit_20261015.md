<!-- Code style: human-code. -->
# Validation storage and cleanup

## Current checkpoint: 27 October 2026

Removed **327 disposable files: 564,593,363 payload bytes (538.4 MiB)**,
occupying 564,769,792 allocated bytes. The exact scope was:

- Six downloaded GEN inputs in
  `tmp/20261012-qcd-selection-study/source_hash_checks/`.
- Two duplicate `artifacts.zip` files in
  `tmp/20261013-qcd-tail-followup/extra_gen_{10to20,40plus}/`.
- Regenerable CPython caches in the workspace's
  `tmp/first_limits_20261014/`; its PDF libraries and scientific records remain.

The ZIPs' 22 extracted files, including both GEN ROOTs, configurations, native
logs, proposal ledgers and audits, match their archive members by size and
SHA-256 before and after removal. Five downloaded inputs match fresh readbacks
of their original EOS files. The sixth original EOS path is absent: its
187,672,964-byte cached GEN input was preserved first at:

```text
root://eosuser.cern.ch//eos/user/j/jniedzie/shift_cmssw_support/legacy_references/qcd_20toinf/shift_weighted_high_bins_20261001_v2/source02700_gen_90b7bfcbe3c1.root
```

Its full readback matches SHA-256
`90b7bfcbe3c1acf961d5ec1ab91423e692af735d7f509761e8991e8336b5c698`.
[The relocation record](validation_retention_audit_20261015/cleanup_20261027/retained_source.json)
keeps its old EOS/cache paths, descriptor checksum and exact retained location.
Use this mapping when restoring the retired hash-check probe; its old source
inventory was already retired in the October 15 cleanup. The other five
original locations are recorded in the readback receipts.

The final global queue query succeeded with no jobs. Its blank native response
was accepted only after terminal history confirmed that the three last-seen SM
response jobs completed with exit zero. No jobs were cancelled or resubmitted.
Maintained entry points and the actual frozen worker/watchdog inputs have no
references to the exact removed paths. All 24 checked frozen files are unchanged.
The broader historical dependency scan was stopped before completion; it is
not evidence for retiring any additional study or deployment.

[Result](validation_retention_audit_20261015/cleanup_20261027/result.json),
[exact manifest](validation_retention_audit_20261015/cleanup_20261027/manifest.json)
and [per-file deletions](validation_retention_audit_20261015/cleanup_20261027/deleted.jsonl)
retain the accounting. The same directory contains archive-member comparisons,
EOS readback evidence, queue/history records and protected-runtime checksums.
Only compact records are kept on AFS; no new runtime or cleanup framework was added.

AFS quota usage fell from 89% to 88%. The workflow's `tmp/` now occupies about
498 MiB and the workspace's `tmp/` about 57 MiB. This pass retains about 190 KiB
of AFS evidence and the 179 MiB input on EOS. The existing PDF library still
imports and reads the published limits reference (one page, 217 vector paths)
after cache removal; no replacement render was generated.

## Material retained and its cleanup condition

The current SM response and BSM meson-model studies, shared CMSSW release,
ordinary plot deliveries, human-code and unrelated edits remain untouched.
No scientific selections, exposure, weights or reconstruction behavior changed.
Source/test consolidation was deferred because those areas contain ongoing
model and reconstruction work and frozen consumers.

The two native GEN references in `extra_gen_{10to20,40plus}/` total
287,804,713 bytes. Later material-study requests still name these inputs.
The 40-plus runtime is also named by the mu-enrichment contracts, and the
acceptance study's `cut_funnel_runtime` is named by an early-lifetime control.
Keep these references until their consumers are retired or migrated with
verified readback. Historical PDF-check tools still import the limits task's
PDF library installation; remove that installation only after replacing those
imports and checking their actual document-read commands.

The largest older validation trees measured in this pass were
`bsm_adaptive_20261016` (about 4.6 GiB), `bsm_meson_20261016` (1.2 GiB), and
`qcd_gen_recovery_20261014` (903 MiB). They contain frozen production state,
accounting and scientific references. No completed dependency audit established
whole-tree disposal; they were retained. A later retirement must identify actual
current consumers and the minimal native accounting/reference set first.
Directory age and a historical link alone are not retention decisions.

Private geometry in `cms_lss_20260922`, `lss_reco_20260909` and
`lss_reco_20260910` remains protected until byte-identical copies are verified in
the mandatory private geometry store. `cleanup_20261009` retains its unique
67.9 MB worker-accounting archive until a verified durable copy replaces it.
These were explicit outstanding protections in the earlier audit; this pass
made no geometry or campaign-data retirement.

## Completed October 15 cleanup

At 13:25–13:26 CEST on October 15, the reviewed 44 whole directories and
21 subdirectories were removed: 32,526 files, eight symlinks and 827 directories,
reclaiming **1,459,014,656 allocated bytes (1.36 GiB)**. The then-current
`validation/` size was 2.90 GiB and quota usage fell from 69% to 67%.
These are historical measurements, not the current workspace size or new savings.

The retained 4.44 MiB archive contains all 1,079 required records and was checked
member by member. Post-deletion validation checked 1,395 required paths,
985 retained fallback/private files, 28 model/source hashes and four helpers.
Current-at-the-time BSM/QCD/limits dependencies and selected plot/model inputs
were preserved. Ordinary histogram plotting did not require the retired legacy
audit defaults; some historical replay commands already needed input restoration.

[Execution result](validation_retention_audit_20261015/execution_20261015/deletion_result.json),
[frozen inventory](validation_retention_audit_20261015/execution_20261015/manifest.jsonl.gz),
[deletion receipt](validation_retention_audit_20261015/execution_20261015/deleted.jsonl.gz),
[retained records](validation_retention_audit_20261015/remaining_retained_records_20261015.tar.gz)
and [member manifest](validation_retention_audit_20261015/remaining_retained_records_manifest.json.gz)
are the durable evidence. Earlier proposals, duplicate candidates and partial
inventories remain in the same ignored evidence directory; their proposed sizes
must not be counted as completed or currently available savings.

The repeated proposal/status narrative was consolidated here on October 27.
Its committed earlier wording is available in Git history, including `c5b6cc9`.
Ignored evidence is retained separately and cannot be recovered from Git.
Apply the current workspace [storage instructions](../../AGENTS.md) to any
further cleanup; old reports do not authorize campaign retirement.
