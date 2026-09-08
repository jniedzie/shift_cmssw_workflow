# Collection sensitivity with the existing source

This is a calculated sensitivity result, not an absolute Run-3 prediction.
The source, detector simulation, electronics, trigger rules and reconstruction
are unchanged. It uses the completed 10k-event, 81-delay sample (11,202
generator muons and 5,601 generator dimuons at each delay) and fill 9017's
399 filled Beam-2 slots / 386 colliding IP5 slots. Input hashes are in
`sensitivity.json`; `summary.csv` and `parent_slots.csv` give the numbers.

## Assumptions and method

Production is uniform across filled Beam-2 slots. Signal kinematics are
independent of slot and ordinary trigger decisions. Let q be the marginal
probability that a colliding BX has a usable stored ordinary CMS readout,
already including L1, HLT, stream retention and live-state losses. Noncolliding
BXs have zero opportunity in this restricted ordinary-collision scenario.
The q values 0.1%, 1%, 5% are parameter examples, not measured rates.  A
simple rate-based provisional estimate is much smaller: using 100 kHz ordinary
L1A, 3 kHz usable HLT output, and 386 colliding slots gives
`q = 3,000 / (40 MHz * 386 / 3564) = 0.0695%` per colliding BX.  This treats
the usable HLT rate as already including stream retention and live-state
losses.  It is a planning estimate, not a Run-3 measurement.

For a deliberately broad sensitivity range, 2--5 kHz usable HLT, 300--500
colliding slots, and 90--110 kHz L1A correspond to approximately 0.0036--0.0149%
per colliding BX.  The L1A rate cancels from q when the usable HLT rate is
specified directly; it remains useful for checking the implied conditional HLT
retention (about 2--5% in this example).  The calculation is reproducible with
`scripts/estimate_piggyback_opportunity.py`.

For each parent slot s and relative readout BX k in [-7,+7], use the existing
efficiency at additional delay phase - 25*k ns. Flight time is already present
in the scan and MUST NOT be added again. The probabilities of individual
successful readouts are p_k = q * efficiency(phase - 25*k) at colliding slots.
Then max(p_k) <= P(at least one successful readout) <= min(1,sum(p_k)).
Average these bounds over the 399 parent slots. No independent Bernoulli BX
sampling is used. The bounds allow arbitrary inter-BX correlations; real
trigger constraints can tighten them. They need not be attainable endpoints.
The sum also gives expected reconstructed readout multiplicity, which can
double-count a generator object and is not the unique collection probability.

At nominal additional phase:

| Assumed q | Collected generator muons | Collected generator dimuons |
|---|---|---|
| 0.1% | 0.0177–0.0811% | 0.00364–0.0132% |
| 1% | 0.177–0.811% | 0.0364–0.132% |
| 5% | 0.885–4.05% | 0.182–0.660% |

At q=1%, this corresponds to 177–811 collected muons per 100,000 generator
muons, or 36–132 collected dimuons per 100,000 generator dimuons under the
scan's selections. These are separate denominators, not SHIFT interactions.

The +10 ns phase gives the largest upper bound and expected readout count on
the tested five-point phase grid: 0.844% for muons and 0.139% for dimuons at
q=1%. This is only about 4% and 5% above nominal, respectively. It does not
establish an optimal collection delay: the unique-event bounds overlap and
there is no statistical uncertainty on this phase comparison yet.

## Interpretation and next inputs

These are finite-window, empirical no-pileup bounds, not confidence intervals
or a bound on unmeasured timing tails. Cross-readout track assembly is not
assumed. Source/luminosity correlations and occupancy effects remain unknown.

Report yield per produced generator object initially. If production of those
objects is assumed to obey N_gen = Y * L_CMS, the collected yield is Y * L_CMS
times the reported fraction. This proportionality is an extra assumption for
a beam-loss source. An effective luminosity relative to a reference efficiency
epsilon_ref would be L_eff/L_CMS = fraction/epsilon_ref; it is not the actual
CMS recorded-luminosity fraction. Never multiply cross section, luminosity,
interaction rate and running time together as independent normalizations.

The most useful next improvements are extracting joint event/object responses
from the existing NanoAOD to tighten duplicate-readout bounds, and a few paired
pileup anchors. Measured stored-readout probabilities can replace q later;
relative bunch/source weights can replace uniform weighting. Absolute yield
still needs source normalization. The earlier CSV builders are adapters only:
their physics-valid flags do not establish these missing validations.

Reproduce from the workflow directory:

```bash
MPLCONFIGDIR=/tmp/jniedzie/mpl_shift python3 scripts/shift_collection_sensitivity.py \
  --counts /eos/home-j/jniedzie/shift_cmssw/jpsi/Charmonium_FixedTarget_pThat_1to5GeV_13p6TeV_sameSimHitDelayScan_m200to200_10k_2023_v2/plots_refined/shift_delay_efficiencies.json \
  --mask /eos/home-j/jniedzie/shift_cmssw/trigger_response_grids/run369943_fill9017_same_simhit_20260901/shift_fill_9017_mask.json \
  --output-dir docs/results/2026-09-07_collection_sensitivity
```

Validation: paired source lists and constant denominators checked at all 81
input points; exact delays required; tests cover bounds, orbit wrap, timing
sign and missing-point rejection. PDF and PNG were rendered and inspected.
