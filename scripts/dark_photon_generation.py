"""Pure configuration contract for bounded native-Pythia dark-photon pilots.

Only the pure on-shell DY contribution is implemented. Low-mass hadronic
phenomenology and prompt gamma/Z interference are deliberately outside scope.
The Zprime engine receives the derived dark-photon couplings, not a generic
Zprime model. Its native running-alpha / first-order-QCD width is the pilot
width authority; no artificial width or independent lifetime is introduced.
"""
import math

from dark_photon_model import ElectroweakInputs, pythia_couplings
from fixed_target_generation import beam_settings


NATIVE_EW = ElectroweakInputs(sin2_theta_w=0.2312, m_z_gev=91.1876)
SIGNAL_PDG_ID = 32


def production_settings(mass_gev, epsilon, coupling_scale=1.):
    if not math.isfinite(mass_gev) or not 12 <= mass_gev < NATIVE_EW.m_z_gev:
        raise ValueError('Native perturbative DY pilot requires 12 <= mass < mZ; low mass needs spectral inputs')
    if not math.isfinite(epsilon) or not 0 < abs(epsilon) <= 0.01:
        raise ValueError('Require finite nonzero abs(epsilon) <= 0.01')
    if not math.isfinite(coupling_scale) or coupling_scale < 1 or abs(epsilon)*coupling_scale > .01:
        raise ValueError('Sampling amplitude scale must be >=1 and within the perturbative pilot range')
    couplings = pythia_couplings(mass_gev, epsilon, NATIVE_EW)
    settings = [
        'NewGaugeBoson:ffbar2gmZZprime = on', 'Zprime:gmZmode = 3',
        'Zprime:universality = off', 'Zprime:coup2gen4 = off',
        'Zprime:coup2WW = 0',
        f'StandardModel:sin2thetaW = {NATIVE_EW.sin2_theta_w:.17g}',
        f'23:m0 = {NATIVE_EW.m_z_gev:.17g}',
        f'32:m0 = {mass_gev:.17g}',
        # Narrow user bounds can defeat Pythia's resonance phase-space search.
        # Broader support leaves the natural physical width unchanged.
        f'32:mMin = {0.99 * mass_gev:.17g}',
        f'32:mMax = {1.01 * mass_gev:.17g}',
        f'PhaseSpace:mHatMin = {0.99 * mass_gev:.17g}',
        f'PhaseSpace:mHatMax = {1.01 * mass_gev:.17g}',
        # Exact threshold mass used throughout the project.
        '13:m0 = 0.1056583755', '13:mayDecay = off',
    ]
    for species, values in couplings.items():
        settings.extend([f'Zprime:v{species} = {coupling_scale*values["vector"]:.17g}',
                         f'Zprime:a{species} = {coupling_scale*values["axial"]:.17g}'])
    return settings


def check_width_table(table, mass_gev, epsilon):
    for key, expected in (('mass_gev', mass_gev), ('epsilon', epsilon)):
        if not math.isclose(table[key], expected, rel_tol=1e-12, abs_tol=0):
            raise ValueError('Width authority does not match physical point: ' + key)
    width = table['total_width_gev']
    length = table['ctau_mm']
    branching = table['br_mumu']
    if not all(math.isfinite(x) for x in (width, length, branching)) or not (
            width > 0 and length > 0 and 0 < branching < 1):
        raise ValueError('Invalid native width/lifetime/branching table')
    if not math.isclose(width * length, 1.973269804e-13, rel_tol=1e-10):
        raise ValueError('Lifetime differs from the full physical width')


def signal_settings(mass_gev, epsilon, width_table, decay_mode='mumu', coupling_scale=1.):
    check_width_table(width_table, mass_gev, epsilon)
    if decay_mode not in ('mumu', 'inclusive'):
        raise ValueError('Unknown signal decay mode')
    result = production_settings(mass_gev, epsilon, coupling_scale)
    # Pythia's resonance tauCalc can replace an explicit tau0. The preflight
    # derives the lifetime from its own total width; freeze that value once.
    result.extend(['32:tauCalc = off',
                   f'32:tau0 = {width_table["ctau_mm"]:.17g}'])
    if decay_mode == 'mumu':
        result.extend(['32:onMode = off', '32:onIfMatch = 13 -13'])
    return result


def contract(mass_gev, epsilon, width_table, decay_mode, events, seed, run_number,
             common, cp5, sampling_width_table=None, coupling_scale=1.):
    if not 1 <= events <= 10000 or not 1 <= seed <= 899999900:
        raise ValueError('Require 1..10000 events and valid reserved RNG seed range')
    if not 1 <= run_number <= 4294967295:
        raise ValueError('Invalid run-number namespace')
    sampling_width_table = sampling_width_table or width_table
    if not math.isclose(sampling_width_table['total_width_gev'],
                        width_table['total_width_gev']*coupling_scale**2, rel_tol=1e-8):
        raise ValueError('Sampling and physical widths do not have common amplitude scaling')
    # Native Breit-Wigner sampling below machine resolution can bias rates.
    # Leave margin for independent convergence tests; never widen the width.
    # Pythia 8.317 initBWmass also collapses pole support below its compiled
    # NARROWMASS=1e-6 GeV; broad phase-space then covers only the upper half.
    # This generator adapter is pinned to 8.317 and rejects that path.
    if sampling_width_table['total_width_gev'] < max(1e-6,1e6*math.ulp(mass_gev)):
        raise ValueError('Native pole width is numerically unresolved; use explicit narrow-width sampling')
    commands = signal_settings(mass_gev, epsilon, width_table, decay_mode, coupling_scale)
    return dict(schema='shift-dark-photon-gen-v1', model='PBC-BC1-visible-hypercharge',
        mechanism='pure-on-shell-drell-yan', mass_gev=mass_gev, epsilon=epsilon,
        generated_mass_support_gev=[.99*mass_gev,1.01*mass_gev],
        pdg_id=SIGNAL_PDG_ID, decay_mode=decay_mode, requested_events=events,
        seed=seed, run_number=run_number, beam_energy_GeV=6800.,
        source_z_mm=148000., source_sigma_z_mm=500.,
        source_model='provisional on-axis stationary proton target',
        width_authority=width_table, process_settings=commands,
        sampling_width_authority=sampling_width_table,
        sampling_method=('direct' if coupling_scale == 1 else 'narrow-width-common-amplitude-sampling'),
        sampling_amplitude_scale=coupling_scale, production_rate_correction=1/coupling_scale**2,
        lifetime_owner='physical target width, independent of sampling amplitude',
        narrow_width_physics_validated=False,
        beam_settings=beam_settings(6800.), common=list(common), cp5=list(cp5),
        hadron_decay_policy='common residual muon-parent closure; natural BR; absolute 8000/151000 mm cylinder',
        signal_decay_policy='native resonance decay; full physical exponential; outside-envelope outcomes retained',
        signal_parent_transport='completed parent HepMC status 3; transport daughters from physical vertices',
        cross_section_convention=('native Pythia already includes BR_mumu; no second BR factor; apply production_rate_correction once'
                                  if decay_mode == 'mumu' else 'inclusive native Pythia signal decay channels'),
        timing='proper decays after source placement; nominal Beam-B time shift once on complete graph; canonical shiftEventTime metadata',
        filtering='none', physics_valid=False, normalization_ready=False,
        low_mass_mechanisms_included=False, prompt_interference_included=False,
        detector_simulated=False)
