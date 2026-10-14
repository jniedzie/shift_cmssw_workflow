<!-- Code style: human-code. -->
# Persistent dark-photon production controller

`scripts/watch_dark_photon_production.py` keeps a heartbeat after the login
session closes. The submitted DAGs own GEN, detector processing, ntuple
grouping, histogramming and histogram merging. The controller watches their
exact manager IDs, reports held jobs and failed receipts, records fresh storage
headroom, and publishes the common `latest` campaign alias.

The alias is separate for each physical point. Yesterday's ready prompt output
can remain selected while today's displaced output is still running. The
controller advances a point only after its finalizer receipt matches its merged
histogram file, size and checksum. It preserves all dated source directories.
The ordinary plotter reads available histogram files through these links; it
does not load the controller's production checks or completion receipts.
Both lifetime-labelled points and explicit `m15_epsilon_1e-06` points use the
same alias. In `shift_paths.py`, the mass selector applies to both families.
The lab-flight and epsilon selectors apply independently; an empty lab-flight
list selects epsilon samples only, while an empty epsilon list keeps the
existing prompt and lab-flight samples. `None` selects all points in a family.

Before deploying, prepare a `shift-bsm-campaign-alias-candidates-v1` JSON file.
Its `source_records` list gives each campaign's absolute `plan_path`,
`plan_sha256` and `seal_sha256`. Its `settings` dictionary gives compatible
campaigns the same named settings, each with an absolute artifact `path` and
`sha256`. Include the generator model, detector templates, runtime, classifier
and histogram configuration. The source order sets replacement priority.
Overlapping points must have identical mass, epsilon, proper lifetime,
sampling epsilon and requested mean lab flight. New points are discovered from
the declared plans. Optional `afs_quota_path` enables the AFS headroom report.
Optional `owned_points` lists the exact physical point names whose aliases this
controller may update. All declared compatible source campaigns remain allowed
targets for those points. Use this when a recovery or an additional grid needs
its own supervisor, so it does not take ownership of unrelated point directories.

An audited partial preview may be kept in a separate dated campaign, rather
than occupying an unfinished production's final paths. Add it to
`external_ready_inputs` with `point`, `campaign`, `condition_campaign`,
`source_plan_sha256`, `events`, `histogram_path`, `histogram_bytes`,
`histogram_sha256`, `receipt_path` and `receipt_sha256`. Its condition campaign
must be a declared compatible source; its event count cannot exceed that
source's GEN count. Keep the receipt under the preview campaign's `metadata/`.
The controller verifies both payload and receipt and keeps the preview selected
until a canonical compatible output is ready.

Set `condor_pool` and `condor_schedd` explicitly when an execute node uses a
different collector. CERN's verified worker setup uses `tweetybird04.cern.ch`
and `bigbird26.cern.ch`. Freeze the JSON, controller and
`shift_condor_native.py` in a new controller directory. Keep its logs and state
there; do not change a live campaign.
First perform a read-only pass:

```bash
python3 /absolute/controller/watch_dark_photon_production.py \
  --config /absolute/controller/candidate_map.json \
  --state-directory /absolute/controller/state --once
```

Submit the frozen controller with Condor's `vanilla` universe and delegated
user credentials. The scheduler hosts do not provide an EOS mount/client or
Kerberos tickets; a vanilla worker was verified to provide all three. Use
`/usr/bin/python3` as the executable and the absolute script/config/state paths
as arguments. Add `--publish-aliases` to enable publication and
`--config-sha256 SHA256` to pin the reviewed configuration. Set
`on_exit_remove = (ExitBySignal == False && ExitCode == 0)` so an abnormal exit
is requeued. Add `--exit-on-complete` to finish successfully when all newest
outputs are ready and their aliases are published. Request a job flavour long
enough for the campaign. Set separate output, error and event logs under the
controller directory. Verify its first heartbeat and named scheduler query on
an actual worker before relying on it after disconnecting.

The default mode requires an accessible EOS data mount and native EOS client
at startup. It exits before publication if either is missing. Optional
`--eos-rpc` uses native EOS/XRootD commands for files and links instead of a
mounted data directory; preserve its downloaded cache in worker scratch.

`state/live_status.json` is replaced atomically after each pass. It records
available points, completion of the newest requested runs, queue status,
failure records, rescue files, quota and actions needing attention. A quota or
scheduler read error is reported as unknown. Unknown quota holds alias changes;
it does not remove existing links or outputs. Worker publication still uses
the worker's independent storage-reservation checks.

The controller does not resubmit workers or DAGs, release held jobs, cancel jobs
or delete data. DAGMan survives disconnects and handles its configured node
retries and manager restart. A terminal failed DAG or held job is reported for
review. Repair must respect the original workers' no-overwrite publication
contract; an automatic rescue without that check could duplicate processing or
collide with a partial publication.
