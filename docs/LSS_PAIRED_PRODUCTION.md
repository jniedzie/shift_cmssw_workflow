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
removals. At most 100 jobs per campaign are materialized at once. Step 1
archives compact per-chunk upstream transport JSON alongside raw logs.

- Corrected five-event smoke: `lssTargetMaterial148m_smoke_2023`, cluster 17330023.
- 10k control: `lssPaired_control_10k_2023_v1`, cluster 17330025.
- The 10k combined sample is pending the corrected smoke result.

## Completion and analysis gate

Run `scripts/compare_lss_reconstruction.py CONTROL COMPARISON OUTPUT
--expected-events 10000 --transport-directory COMPARISON/logs` when all
Step-4 outputs are healthy. It rejects missing/unequal chunk sets, duplicate
events, mismatching generator content, invalid ROOT files, invalid generator
associations, and ambiguous transport joins. A SHA-256 fingerprint of the
stored truth content is saved for each event.

The report produces efficiency versus generated pt/pz/eta with Wilson 68%
intervals, signed q/pt response distributions, and pt scale and central-68%
resolution versus generated |pz|. The response is also shown for the common
set of muons reconstructed in both samples, separating selection changes
from response changes. Raw counts, unique matched counts, duplicates,
out-of-plot-range response counts, and rock-group denominators remain explicit.
