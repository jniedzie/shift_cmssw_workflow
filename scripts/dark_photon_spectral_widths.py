#!/usr/bin/env python3
# Code style: AI-code. AI-generated exact-coupling spectral-width adapter.
"""Create a frozen DarkCast vector/axial model at one physical A' point.

DarkCast supplies its published exclusive vector and axial hadronic model.
The neutral mass matrix and all fermion couplings come from our independent
hypercharge implementation. This is a model benchmark, not rate approval.
"""
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path

from dark_photon_model import ElectroweakInputs, FERMIONS, fermion_couplings, mixing_parameters


def full_hypercharge_model(darkcast, mass, epsilon, output):
    """Return a point-specific model and the exact card used to construct it.

    Divide by epsilon in the card because DarkCast applies its global coupling
    separately. Its electromagnetic input stays unchanged; do not silently
    replace the provider's alpha by a generator alpha at another scale.
    """
    photon = darkcast.Model('dark_photon')
    electric_charge = abs(photon.xfs['e'][1](mass))
    inputs = ElectroweakInputs(alpha_inverse=4 * math.pi / electric_charge**2,
                              sin2_theta_w=.2312, m_z_gev=91.1876)
    mixing = mixing_parameters(mass, epsilon, inputs)
    couplings = {}
    for fermion in FERMIONS:
        coupling = fermion_couplings(mass, epsilon, fermion.charge, fermion.t3_left, inputs)
        couplings[fermion.name] = (coupling.axial / epsilon, coupling.vector / epsilon)
    # DarkCast calls the electron "e", while Pythia uses the same species key.
    card = dict(schema='shift-exact-hypercharge-spectral-couplings-v1', mass_gev=mass,
                epsilon=epsilon, electroweak_inputs=asdict(inputs), mixing=asdict(mixing),
                xfs=couplings, global_coupling=epsilon,
                references=['https://arxiv.org/abs/1412.0018',
                            'https://arxiv.org/abs/1801.04847',
                            'https://arxiv.org/abs/2206.08563'])
    content = json.dumps(card, sort_keys=True, allow_nan=False)
    name = 'shift_hypercharge_' + hashlib.sha256(content.encode()).hexdigest()[:20]
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    module = directory / (name + '.py')
    text = ('# Code style: AI-code. AI-generated frozen DarkCast coupling card.\n'
            + 'xfs = ' + repr(couplings) + '\n')
    if module.exists():
        if module.read_text() != text:
            raise ValueError('Existing frozen coupling module differs from its physical point')
    else:
        module.write_text(text)
    card['module'] = module.name
    card['module_sha256'] = hashlib.sha256(module.read_bytes()).hexdigest()
    model = darkcast.Model(name, path=str(directory))
    return model, photon, card


def correction_summary(model, photon, mass, epsilon):
    states = ['e_e', 'mu_mu', 'tau_tau', 'hadrons', 'quarks', 'neutrinos', 'dark', 'total']
    full = {state: model.width(state, mass, epsilon) for state in states}
    reference = {state: photon.width(state, mass, epsilon) for state in states}
    return dict(full_partial_widths_gev=full, photon_partial_widths_gev=reference,
                total_ratio=full['total'] / reference['total'],
                dimuon_partial_ratio=full['mu_mu'] / reference['mu_mu'],
                br_mumu_ratio=(full['mu_mu'] / full['total']) /
                              (reference['mu_mu'] / reference['total']))
