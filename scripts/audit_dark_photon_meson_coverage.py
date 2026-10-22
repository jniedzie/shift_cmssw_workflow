# Code style: AI-code. AI-generated meson-channel coverage and source inventory audit.
"""Inventory candidate meson mechanisms without inventing missing decay rates.

The default Pythia table is an inventory, never a resolved source configuration
or a bound on omitted physics. This audit deliberately cannot declare the
inclusive meson model complete. Quantitative closure needs separately reviewed
timelike amplitudes, source spectra, overlapping-channel ownership and detector
acceptance. Its machine-readable gaps let detached qualification jobs retain
that distinction after ordinary production and publication have succeeded.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET


SCHEMA = 'shift-dark-photon-meson-coverage-v1'
MUON_THRESHOLD_GEV = .211316751
REFERENCES = {
    'pdg': 'https://pdg.lbl.gov/2025/listings/contents_listings.html',
    'omega': 'https://arxiv.org/abs/1608.07898',
    'phi_eta': 'https://doi.org/10.1016/j.physletb.2015.01.011',
    'phi_pi0': 'https://arxiv.org/abs/1601.06565',
    'dipion': 'https://arxiv.org/abs/2012.04257',
    'charged_rho': 'https://arxiv.org/abs/hep-ph/9707202',
    'mixing': 'https://arxiv.org/abs/2504.06828',
    'proton': 'https://arxiv.org/abs/2409.11096',
}
UPSTREAM_GENERATOR = {
    'repository': 'https://gitlab.com/YehorKyselyov/pythia-mixing',
    'revision': '805f5bd0223dfe8d218481c82ad6bc2f03868bb8',
    'reference': REFERENCES['mixing'],
    'reviewed_files_sha256': {
        'examples/MixingPattern.h': '0019c01473905eb89d92b2cda565952863d2c6cc380af4dbb9a3055292d20678',
        'examples/MixingPattern.cc': '73d929217e96d4a93b1ea9abdddc596593ba073ee28a90f25856e6233d18a1a8',
        'examples/FacilitySettings.h': '0e70cdd06231f604fa4ad3ee1345b25add7e840e6dfd3310c0476f4291834a9a',
        'examples/Makefile': 'c02c3254a79defb99f0353dbb84f5b1fe2765d59e7dda489cf849479d257bfd5',
    },
    'review_scope': 'Read-only source inspection, not build or physics qualification.',
    'decays_explicitly_added_by_DP_class': ['pi0->gamma_Aprime', 'eta->gamma_Aprime', 'eta_prime->gamma_Aprime'],
    'fragmentation_hooks': ['rho0', 'omega', 'phi'],
    'qualification_gaps': [
        'Isolated full fork build; no rebuild of the shared live CMSSW release.',
        'Replace example facilities with reviewed SHIFT 6800-GeV fixed-target settings and source normalization.',
        'Preserve full generated events, spectators, vertex ancestry and a fixed-source ledger; mixing01 only exports weighted LLP kinematics.',
        'Assign disjoint mechanism ownership, compare rates and spectra and qualify the full GEN-to-publication handoff.',
        'The public DP example does not implement all vector, dipion or heavy-meson decay channels.',
    ],
}


def _entry(channel, parent, recoil, endpoint, reason, reference='pdg'):
    return dict(channel=channel, parent_pdg_id=parent,
                recoil_pdg_id=recoil if not isinstance(recoil, list) else None,
                **(dict(recoil_pdg_ids=recoil) if isinstance(recoil, list) else {}),
                pole_endpoint_gev=endpoint, reason=reason,
                reference=REFERENCES[reference])


# Pole endpoints are explanatory numbers, not cutoffs on broad native parents.
CATALOGUE = [
    _entry('eta -> gamma Aprime', 221, 22, .547862,
           'Measured transition and source accounting must be qualified.'),
    _entry('etaprime -> gamma Aprime', 331, 22, .95778,
           'Finite-width timelike transition and source accounting must be qualified.'),
    _entry('omega -> pi0 Aprime', 223, 111, .6476832,
           'Measured NA60 form factor covers only mA <= .64 GeV; native tails and spin alignment need separate validation.', 'omega'),
    _entry('phi -> eta Aprime', 333, 221, .471598,
           'Final KLOE slope is 1.28 GeV^-2, unlike the older preprint; source width and polarization need qualification.', 'phi_eta'),
    _entry('phi -> pi0 Aprime', 333, 111, .8844832,
           'The fitted single-pole slope cannot be extrapolated through its artificial pole; require finite-width timelike amplitude.', 'phi_pi0'),
    _entry('rho0 -> pi0 Aprime', 113, 111, .6402832,
           'Broad parent spectral function, timelike form factor and polarization are not qualified.'),
    _entry('rho+- -> pi+- Aprime', 213, 211, .63553961,
           'Include both charges, broad parent mass support and timelike transition.'),
    _entry('rho0 -> eta Aprime', 113, 221, .227398,
           'Pole threshold does not close broad native rho tails; no timelike model is qualified.'),
    _entry('omega -> eta Aprime', 223, 221, .234798,
           'Threshold channel has no qualified transition and off-shell prescription.'),
    _entry('Kstar0 -> K0 Aprime', 313, 311, .397939,
           'Include charge conjugates, natural recoil decays and broad parent transition.'),
    _entry('Kstar+- -> K+- Aprime', 323, 321, .397993,
           'Include both charges, natural recoil decays and broad parent transition.'),
    _entry('eta -> pi+ pi- Aprime', 221, None, .26872122,
           'Require a differential three-body electromagnetic amplitude and pion correlations.', 'dipion'),
    _entry('etaprime -> pi+ pi- Aprime', 331, None, .67863922,
           'Treat resonant and nonresonant dipion coherently; do not add rho-gamma and inclusive dipion twice.', 'dipion'),
    _entry('rho0 -> pi+ pi- Aprime', 113, None, .49611922,
           'Conserved charged-pion radiation current with its contact term; validate the measured photon cut rate and label structure-dependent terms unresolved.'),
    _entry('rho+- -> pi+- pi0 Aprime', 213, [211, 111], .50056281,
           'Both charges require pion radiation, charged-vector radiation and the contact current with unequal pion masses. The g_EM=2 local pole benchmark does not fix higher electromagnetic moments, timelike form factors or broad-parent interference; the full-model photon rate differs by about 12 percent.', 'charged_rho'),
    _entry('other exclusive meson electromagnetic transitions', None, None, None,
           'Inventory multiparticle, scalar, tensor, axial-vector and excited-meson transitions; real-photon rates are not fixed-mass bounds.'),
    _entry('heavy-flavour and quarkonium transitions', None, None, None,
           'Audit production spectra and allowed eta_c, charmonium and heavier transitions; absence in a small source probe is not zero rate.'),
    _entry('weak kaon and heavy-flavour decays', None, None, None,
           'Require a minimal-dark-photon weak-current calculation and source/decay-position normalization.'),
    _entry('direct vector mixing in fragmentation', None, None, None,
           'Use mass-dependent fragmentation with event-level kinematics, not a one-particle on-shell vector-to-Aprime decay.', 'mixing'),
    _entry('diffractive parent-meson source', None, None, None,
           'Non-diffractive production does not include diffractive parents; calibrate separate source rates and spectra.'),
    _entry('secondary-target meson production', None, None, None,
           'Primary pp generation alone does not establish secondary-target yields.'),
]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_meson(pdg):
    value = abs(pdg)
    return 100 <= value < 10000000 and (value // 1000) % 10 == 0


def particle_data_inventory(path):
    """Read self-contained particle blocks from Pythia's documentation XML.

    The surrounding file contains documentation markup and HTML entities; it
    is not an ordinary XML document. Individual particle blocks are XML.
    """
    text = Path(path).read_text()
    particles = []
    for block in re.findall(r'<particle\s[^>]*>.*?</particle>', text, re.S):
        node = ET.fromstring(block)
        pdg = int(node.attrib['id'])
        if not is_meson(pdg):
            continue
        pole = float(node.get('m0', '0'))
        width = float(node.get('mWidth', '0'))
        low = float(node.get('mMin', str(pole)))
        high = float(node.get('mMax', str(pole)))
        particles.append(dict(pdg_id=pdg, name=node.get('name'), mass_gev=pole,
            mass_min_gev=low if width > 0 and low > 0 else pole,
            mass_max_gev=high if width > 0 and high >= pole else pole,
            mass_width_gev=width, channels=[dict(br=float(ch.get('bRatio')),
                products=[int(x) for x in ch.get('products', '').split()],
                on_mode=int(ch.get('onMode', '1')))
                for ch in node.findall('channel')]))
    if not particles or len({p['pdg_id'] for p in particles}) != len(particles):
        raise ValueError('Missing or duplicated mesons in particle table')
    return dict(schema='shift-default-meson-particle-data-v1', particle_data=particles,
                source_sampling_verified=False, resolved_source_configuration=False)


def model_owned_channels(model):
    """Index exclusive final states without counting a shared parent twice.

    The production card stores one row per exclusive channel. Resolved source
    models can instead group several channels under one physical parent. Both
    representations have the same ownership: parent and the complete recoil
    final state. In particular, a dipion recoil is not a missing photon field.
    """
    result = {}
    names = set()
    for parent in model.get('parents', []):
        pdg = parent.get('pdg_id')
        if type(pdg) is not int or not is_meson(pdg):
            raise ValueError('Exclusive model ownership requires a meson PDG ID')
        channels = parent.get('exclusive_channels', [parent])
        if not isinstance(channels, list) or not channels:
            raise ValueError('Exclusive model ownership requires nonempty channels')
        for channel in channels:
            if not isinstance(channel, dict) or channel.get('pdg_id', pdg) != pdg:
                raise ValueError('Grouped channel ownership must keep its physical parent')
            owner = dict(parent, **channel)
            owner.pop('exclusive_channels', None)
            key = (pdg, model_recoil(owner))
            name = owner.get('channel')
            if name is not None and (not isinstance(name, str) or not name):
                raise ValueError('Explicit model channel names must be nonempty strings')
            if key in result or (name is not None and name in names):
                raise ValueError('Duplicate exclusive final-state model ownership')
            if name is not None:
                names.add(name)
            result[key] = owner
    return result


def model_recoil(owner):
    if owner.get('decay_type') == 'charged-vector-dipion':
        recoil = owner.get('recoil_pdg_ids')
    elif owner.get('decay_type') in ('pseudoscalar-dipion', 'vector-dipion'):
        recoil = owner.get('recoil_pdg_ids', [211, -211])
    elif 'recoil_pdg_ids' in owner:
        recoil = owner['recoil_pdg_ids']
    else:
        recoil = [owner.get('recoil_pdg_id', 22)]
    if (not isinstance(recoil, list) or not recoil
            or any(type(pdg) is not int or pdg == 0 for pdg in recoil)):
        raise ValueError('Exclusive recoil ownership requires daughter PDG IDs')
    if owner.get('decay_type') in ('pseudoscalar-dipion', 'vector-dipion') and sorted(recoil) != [-211, 211]:
        raise ValueError('A charged dipion channel must own both pion daughters')
    if owner.get('decay_type') == 'charged-vector-dipion':
        parent = owner.get('pdg_id')
        if parent not in (-213, 213) or sorted(recoil) != sorted([211 if parent > 0 else -211, 111]):
            raise ValueError('Charged-rho dipion ownership requires the charge-matched pion and pi0')
    return tuple(sorted(recoil))


def catalogue_ownership(item):
    parent, recoil = item['parent_pdg_id'], item['recoil_pdg_id']
    if parent is None:
        return []
    if 'recoil_pdg_ids' in item:
        daughters = tuple(item['recoil_pdg_ids'])
    elif recoil is None:
        daughters = (-211, 211)
    else:
        daughters = (recoil,)
    required = [(parent, tuple(sorted(daughters)))]
    # Conjugate charged daughters and retain self-conjugate neutral mesons.
    # In particular rho- owns pi- pi0, never pi- anti-pi0 or pi+ pi-.
    if parent in (213, 313, 323):
        self_conjugate = {22, 111, 113, 221, 223, 331, 333}
        conjugate = tuple(sorted(pdg if pdg in self_conjugate else -pdg for pdg in daughters))
        required.append((-parent, conjugate))
    return required


def transition_candidates(inventory):
    """Enumerate photon replacement and neutral-vector replacement candidates.

    A candidate is not an independent contribution: resonance/direct paths can
    describe the same amplitude, and every such overlap remains to be reviewed.
    Broad-parent support uses the supplied table bounds, never pole masses alone.
    """
    particles = {p['pdg_id']: p for p in inventory.get('particle_data', [])}
    if not particles:
        raise ValueError('Source inventory needs particle_data')
    result = []
    for pdg, parent in sorted(particles.items()):
        if not is_meson(pdg):
            continue
        for index, decay in enumerate(parent.get('channels', [])):
            if float(decay['br']) <= 0 or decay.get('on_mode', 1) == 0:
                continue
            products = decay['products']
            for replacement in (22, 113, 223, 333):
                if replacement not in products:
                    continue
                rest = list(products)
                rest.remove(replacement)
                known = all(abs(x) in particles or x in (22, 11, -11, 13, -13)
                            for x in rest)
                recoil_min = 0.
                for daughter in rest:
                    item = particles.get(abs(daughter))
                    if item:
                        recoil_min += item.get('mass_min_gev', item['mass_gev'])
                    elif abs(daughter) == 11:
                        recoil_min += .00051099895
                    elif abs(daughter) == 13:
                        recoil_min += MUON_THRESHOLD_GEV / 2
                endpoint = parent.get('mass_max_gev', parent['mass_gev']) - recoil_min if known else None
                result.append(dict(parent_pdg_id=pdg, decay_index=index,
                    products=products, replace_pdg_id=replacement,
                    replacement_multiplicity=products.count(replacement),
                    source_branching_fraction=decay['br'],
                    native_support_endpoint_gev=endpoint,
                    dimuon_open_in_table=endpoint > MUON_THRESHOLD_GEV if endpoint is not None else None,
                    independent_rate_qualified=False,
                    reason='Timelike amplitude, mass-dependent rate and overlapping-channel ownership remain to be qualified.'))
    return result


def audit_coverage(model, masses, inventory):
    if not masses or any(not math.isfinite(m) or m <= MUON_THRESHOLD_GEV for m in masses):
        raise ValueError('Require finite masses above the dimuon threshold')
    included = model.get('channels', [])
    if (not isinstance(included, list) or any(not isinstance(x, str) or not x for x in included)
            or len(set(included)) != len(included)):
        raise ValueError('Model channels must be unique nonempty strings')
    owners = model_owned_channels(model)
    rows = []
    gaps, implemented, unvalidated = [], [], []
    classified = set()
    source_gaps = {'diffractive parent-meson source', 'secondary-target meson production'}
    for item in CATALOGUE:
        row = dict(item)
        required = catalogue_ownership(item)
        present = [key for key in required if key in owners]
        missing = [key for key in required if key not in owners]
        owned = [owners[key] for key in present]
        classified.update(present)
        row['implemented_in_model'] = bool(required) and not missing
        row['partially_implemented_in_model'] = bool(present) and bool(missing)
        row['owned_model_channels'] = [owner.get('channel') for owner in owned]
        row['owned_model_channel'] = owned[0].get('channel') if len(owned) == 1 else None
        row['required_parent_pdg_ids'] = [key[0] for key in required]
        row['missing_parent_pdg_ids'] = [key[0] for key in missing]
        row['pole_mass_open_points_gev'] = [m for m in masses if item['pole_endpoint_gev'] is None or m < item['pole_endpoint_gev']]
        row['pole_endpoint_is_native_cutoff'] = False
        if owned:
            domains = []
            for owner in owned:
                tff = owner.get('transition_form_factor', owner.get('dipion_model', owner.get('rho_radiative_model', owner.get('charged_rho_radiative_model', {}))))
                domain = tff.get('valid_mass_range_gev', tff.get('physical_dimuon_mass_range_gev'))
                domains.append(domain)
            row['declared_form_factor_domains_gev'] = domains
            row['declared_form_factor_domain_gev'] = domains[0] if len(domains) == 1 else None
            row['unsupported_form_factor_mass_points_gev'] = [m for m in masses
                if m in row['pole_mass_open_points_gev'] and
                any(not valid_domain(domain, m) for domain in domains)]
            unvalidated.append(dict(channel=item['channel'], status='needs_validation',
                reason=item['reason'], owned_model_channels=row['owned_model_channels']))
        if row['implemented_in_model']:
            implemented.append(item['channel'])
            row['status'] = 'needs_validation'
        else:
            row['status'] = 'missing_source' if item['channel'] in source_gaps else 'missing_model'
            reason = item['reason']
            if row['partially_implemented_in_model']:
                reason += ' Missing charge-conjugate parent PDG IDs: ' + ', '.join(str(key[0]) for key in missing) + '.'
            gaps.append(dict(channel=item['channel'], status=row['status'], reason=reason))
        rows.append(row)
    unclassified = [dict(parent_pdg_id=key[0], recoil_pdg_ids=list(key[1]),
                         model_channel=owner.get('channel'), status='needs_validation',
                         reason='Declared model channel has no reviewed catalogue classification or omission bound.')
                    for key, owner in owners.items() if key not in classified]
    return dict(schema=SCHEMA, included_channels=included, implemented_channels=implemented,
        unvalidated_channels=unvalidated, remaining_channels=gaps,
        unclassified_model_channels=unclassified,
        coverage_counts=dict(implemented=len(implemented), unvalidated=len(unvalidated),
            missing_models=sum(row['status'] == 'missing_model' for row in gaps),
            missing_sources=sum(row['status'] == 'missing_source' for row in gaps),
            unclassified_model_channels=len(unclassified)),
        physics_complete=False, mass_points_gev=masses, catalogue=rows,
        candidate_transitions=transition_candidates(inventory), references=REFERENCES,
        upstream_generator_reference=UPSTREAM_GENERATOR,
        source_sampling_verified=inventory.get('source_sampling_verified', False),
        resolved_source_configuration=inventory.get('resolved_source_configuration', False),
        implementation_evidence_scope='Declared exclusive model ownership only. A card does not prove runtime, amplitude, source-spectrum or absolute-rate validation.',
        omission_bound='None. Real-photon branching fractions and source multiplicities do not bound a fixed-mass timelike transition.',
        completion_requirement='Each relevant channel needs reviewed rates, spectra, common physical epsilon/lifetime, overlap ownership and validation, or a quantitative omitted-yield bound. No bound is inferred from this inventory.',
        nonmeson_production_scope='Proton radiation, DY matching and baryon transitions require separate owners; inclusive meson closure would not establish inclusive dark-photon closure.')


def valid_domain(domain, mass):
    return (isinstance(domain, list) and len(domain) == 2
            and all(type(value) in (int, float) and math.isfinite(value) for value in domain)
            and domain[0] <= mass <= domain[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--particle-data', required=True)
    parser.add_argument('--source-inventory')
    parser.add_argument('--model', required=True)
    parser.add_argument('--masses', nargs='+', type=float, required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    defaults = particle_data_inventory(args.particle_data)
    source = json.loads(Path(args.source_inventory).read_text()) if args.source_inventory else defaults
    model = json.loads(Path(args.model).read_text())
    report = audit_coverage(model, args.masses, source)
    report.update(model_sha256=sha256(args.model), particle_data_sha256=sha256(args.particle_data),
                  source_inventory_sha256=sha256(args.source_inventory) if args.source_inventory else None,
                  code_sha256=sha256(__file__))
    output = Path(args.output)
    if output.exists():
        raise FileExistsError('Coverage evidence is immutable; choose a fresh output path')
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps(dict(output=str(output), physics_complete=False,
                          candidate_transitions=len(report['candidate_transitions']),
                          **report['coverage_counts'])))


if __name__ == '__main__':
    main()
