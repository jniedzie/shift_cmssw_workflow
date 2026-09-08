# Paired LSS production, 7 September 2026

The requested measurement is the change in generated-muon reconstruction
efficiency and momentum response with the provisional ATLAS LSS input. This
is an IR1/ATLAS proxy sensitivity study, not an approved CMS IR5 model result.

## Corrections to the preliminary interpretation

An eventual stopping position, possibly more than a kilometre downstream,
does not imply a loss before CMS. The transport report now stops accumulating
upstream material at the first inward crossing of the plane at |z|=11 m.
This plane is a transport milestone only: it has no radial acceptance cut and
is not evidence of a muon detector hit, readout or reconstruction.

In the 100-event material/field trace, 166 of 201 primary muons cross that
plane. The remaining 35 terminate at low energy upstream. The last recorded
material is EARTHBOH for 29 and CONCRETE for six. The step logger omits the
process-killing step, so the last recorded material is reported as evidence,
not an exact terminal-material measurement. Fifty low-energy terminations
over the complete trajectories must not be presented as fifty upstream losses.

Rock entry anywhere in a trajectory is also not proof of stopping in rock.
The previous classifier incorrectly made that inference; it has been removed.

The earlier pilot reconstructed the target momentum using detailed material
only to |z|=11 m. The remaining route to the 148 m target used vacuum
propagation. The new setup tests the documented 148 m material propagation
boundary and a 200 m maximum path, without changing detector electronics.
The corrected five-event smoke campaign must pass before scaling the
material sample.

## Existing 100-event comparison

The complete stored GenPart arrays agree for every paired event. Event keys
include chunk, run, luminosity block and event; event numbers repeat between
chunks. Generated muons are status-one particles with |PDG ID|=13.

| Population | Generated muons | Control matched | LSS matched |
| --- | ---: | ---: | ---: |
| All | 201 | 67 | 28 |
| No earth before entrance plane | 120 | 23 | 11 |
| Earth before entrance plane | 81 | 44 | 17 |

The earth grouping is held fixed from the LSS trajectory in both samples.
Matches use the existing directional GenPart association, not a SimHit truth
association. Multiple reconstructed rows matching one generated muon count
once; the minimum deltaR representative supplies its momentum response.
Twenty-four muons are matched in both samples, 43 only in control and four
only in LSS. The old raw reconstructed-row counts are not efficiencies.

These are pilot results with the old 11 m reconstruction propagation boundary.
They are useful for validating pairing and analysis, not the final 10k
momentum scale/resolution result.

## Reproducible production

`scripts/run_lss_paired_production.sh MODE CAMPAIGN` invokes the frozen
workflow with explicit fixed seeds 13579/24680, nominal 2023 timing, no pileup
or trigger timeline, 1000 chunks of ten events and all four stages. MODE is
`control`, `material`, or `combined`. The default comparison repeats the
previous control versus combined material/field setup; that difference must
not be called a material-only effect.

The rock-continuation GDML digest is
`cce155b2e5bb2cc81a0a4f113fa0be839fd0dbd2de96aeb583561e8b612bdc1a`.
The batch request uses the workday flavour to avoid the previous twenty-minute
removals. Submit all jobs without a materialization/concurrency throttle. Step 1
archives compact per-chunk upstream transport JSON alongside raw logs.

- Corrected five-event smoke: `lssTargetMaterial148m_smoke_2023`, cluster 17330023.
  Passed with exit 0, five readable NanoAOD events, identical GenPart content
  to the paired control, four reconstructed rows (one matched primary), and
  a valid 148 m material boundary on all four reconstructed rows. The resolved
  Step-1/Step-4 geometry/field contract audit passed.
- 10k control: `lssPaired_control_10k_2023_v1`, cluster 17330025.
- 10k combined: `lssPaired_materialField_10k_2023_v1`, cluster 17330027.
- The automatic paired-validation monitor was cancelled when production was
  intentionally stopped short of 1000 valid chunks. No final 10k plots have
  been produced yet.

Both productions contain 10,000 events, not 10,000 reconstructed muons. The
initial 100-job materialization cap was lifted on both active factories to
their full 1000-job size at the user's request. Future submissions omit
`max_materialize` entirely.

## Current 997-chunk handoff

Production was deliberately stopped after three control jobs remained in the
scheduler (17330025.506, .513 and .686); the separate plot-monitor job was
also cancelled. At handoff, .513 and .686 were in Condor's removal state;
they are no longer running. The three jobs had already written their paired
NanoAOD files, so cancelling them does not alter the retained event set.

Terminal failures left control chunk 0810 and material-plus-field chunks 0463
and 0469 without final NanoAOD. To make the retained samples strictly paired,
the existing counterparts were removed: material-plus-field 0810 and control
0463/0469. Both campaigns now contain the same 997 readable Step-4 chunk
indices. The corresponding NanoAOD files are under:

- `/eos/home-j/jniedzie/shift_cmssw/jpsi/lssPaired_control_10k_2023_v1/samples/step4`
- `/eos/home-j/jniedzie/shift_cmssw/jpsi/lssPaired_materialField_10k_2023_v1/samples/step4`

Before analysis, re-check that the two filename-index sets are identical and
that each ROOT file is readable. The retained events still use the fixed,
per-chunk generator and simulation seeds, so the 997 chunks are suitable for
one-to-one control versus combined material/field comparison.

## Pending analysis gate

Run `scripts/compare_lss_reconstruction.py CONTROL COMPARISON OUTPUT
--expected-events 9970` after confirming the 997-chunk set above. Do not pass
`--transport-directory`: the full per-step transport trace was disabled for
the later production jobs to avoid AFS scheduler-log quota exhaustion. The
script rejects missing/unequal chunk sets, duplicate events, mismatching
generator content, invalid ROOT files, and invalid generator associations. A
SHA-256 fingerprint of the stored truth content is saved for each event.

The report produces efficiency versus generated pt/pz/eta with Wilson 68%
intervals, signed q/pt response distributions, and pt scale and central-68%
resolution versus generated |pz|. The response is also shown for the common
set of muons reconstructed in both samples, separating selection changes
from response changes. Raw counts, unique matched counts, duplicates, and
out-of-plot-range response counts remain explicit. The detailed causal
rock/tunnel transport classification is available only from the earlier
100-event trace sample.
