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
from collections import Counter, defaultdict
import os
from pathlib import Path
import re
import random
import copy
import time
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
    kind = owner.get('decay_type')
    if kind in ('pseudoscalar-double-radiative', 'weak-kaon-pion-photon',
                'neutral-kaon-dipion', 'quarkonium-radiative'):
        parent = owner.get('pdg_id')
        daughter = owner.get('recoil_pdg_id')
        if kind == 'pseudoscalar-double-radiative':
            if (parent, daughter) not in ((221, 111), (331, 111), (331, 221)):
                raise ValueError('Double-radiative ownership requires its physical pseudoscalar recoil')
            expected = [daughter, 22]
        elif kind == 'weak-kaon-pion-photon':
            if parent != 130 or daughter != 111:
                raise ValueError('Weak radiative-kaon ownership requires KL and pi0 plus photon')
            expected = [111, 22]
        elif kind == 'neutral-kaon-dipion':
            if parent not in (130, 310):
                raise ValueError('Neutral-kaon dipion ownership requires KL or KS')
            expected = [111, 111]
        else:
            expected_daughter = {441:22, 10441:443, 20443:443, 445:443,
                                 10443:441, 100443:441}.get(parent)
            if expected_daughter is None or daughter != expected_daughter:
                raise ValueError('Quarkonium ownership requires its physical radiative recoil')
            expected = [daughter]
        recoil = owner.get('recoil_pdg_ids', expected)
        if not isinstance(recoil, list) or sorted(recoil) != sorted(expected):
            raise ValueError('Declared recoil disagrees with the exclusive current ownership')
    elif kind == 'weak-kaon-leptonic':
        parent = owner.get('pdg_id')
        lepton, neutrino = owner.get('charged_lepton_pdg_id'), owner.get('neutrino_pdg_id')
        sign = 1 if parent == 321 else -1
        if (parent not in (-321, 321) or type(lepton) is not int or abs(lepton) not in (11, 13)
                or lepton != -sign*abs(lepton) or neutrino != sign*(abs(lepton)+1)):
            raise ValueError('Leptonic-kaon ownership requires charge-matched lepton and neutrino')
        recoil = [lepton, neutrino]
        if 'recoil_pdg_ids' in owner and sorted(owner['recoil_pdg_ids']) != sorted(recoil):
            raise ValueError('Leptonic-kaon recoil disagrees with its physical daughters')
    elif owner.get('decay_type') == 'charged-vector-dipion':
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


def bind_completion_scope(report, scope, digest):
    """Retain both the generic inventory gaps and a reviewed detailed scope."""
    gaps = scope.get('remaining_channels')
    if (scope.get('physics_complete') is not False
            or scope.get('included_channels') != report['included_channels']
            or not isinstance(gaps, list) or not gaps
            or any(not row.get('channel') or not row.get('reason') or
                   row.get('status') not in ('missing_model', 'needs_validation', 'missing_source')
                   for row in gaps)):
        raise ValueError('Detailed completion scope differs from the audited model or omits its gaps')
    result = dict(report)
    result.update(inventory_remaining_channels=report['remaining_channels'],
        remaining_channels=gaps, completion_scope_sha256=digest,
        completion_scope_note='Detailed conditional-current gaps and generic inventory gaps are both retained; neither establishes complete physics.')
    return result


def valid_domain(domain, mass):
    return (isinstance(domain, list) and len(domain) == 2
            and all(type(value) in (int, float) and math.isfinite(value) for value in domain)
            and domain[0] <= mass <= domain[1])


def physical_mesons(snapshot):
    """Own one meson after generator copies and neutral-kaon bookkeeping.

    Different species in a natural decay chain remain separate physical parents;
    their production and possible signal amplitudes must not be summed blindly.
    A flavor K0 converted into KS/KL is bookkeeping for the same physical kaon.
    """
    records = {row['index']: row for row in snapshot['mesons']}
    if len(records) != len(snapshot['mesons']):
        raise ValueError('The native meson inventory repeats an event index')
    result, excluded = [], Counter()
    for row in records.values():
        daughters = [records[index] for index in row['daughter_indices'] if index in records]
        if any(child['pdg_id'] == row['pdg_id'] for child in daughters):
            excluded['same_species_generator_copy'] += 1
        elif abs(row['pdg_id']) == 311 and any(abs(child['pdg_id']) in (130, 310) for child in daughters):
            excluded['neutral_kaon_flavor_to_mass_eigenstate'] += 1
        else:
            result.append(row)
    return result, dict(excluded)


def feature_summary(values):
    if not values:
        return None
    ordered = sorted(values)
    return dict(count=len(values), minimum=ordered[0], maximum=ordered[-1],
                mean=math.fsum(values)/len(values),
                quantiles={str(q): ordered[round(q*(len(values)-1))] for q in (0., .1, .5, .9, 1.)})


class FeatureMoments:
    """Exact streaming extrema/mean and a bounded diagnostic quantile sample."""
    def __init__(self, maximum_sample=512):
        self.count, self.mean, self.m2 = 0, 0., 0.
        self.minimum, self.maximum = math.inf, -math.inf
        self.sample, self.maximum_sample = [], maximum_sample

    def add(self, value, rng):
        if not math.isfinite(value):
            raise ValueError('Nonfinite native source feature')
        self.count += 1
        difference = value-self.mean
        self.mean += difference/self.count
        self.m2 += difference*(value-self.mean)
        self.minimum, self.maximum = min(self.minimum,value), max(self.maximum,value)
        if len(self.sample) < self.maximum_sample:
            self.sample.append(value)
        else:
            index = rng.randrange(self.count)
            if index < self.maximum_sample:
                self.sample[index] = value

    def summary(self):
        if not self.count:
            return None
        quantiles = feature_summary(self.sample)['quantiles']
        return dict(count=self.count,minimum=self.minimum,maximum=self.maximum,mean=self.mean,
            sample_variance=self.m2/(self.count-1) if self.count>1 else 0.,
            quantiles=quantiles,quantile_method='deterministic independent-RNG reservoir',
            quantile_sample_size=len(self.sample),maximum_quantile_sample_size=self.maximum_sample)


def collision_mean_upper_bound(mean, sum_count_squared, collisions, maximum_count,
                               confidence=.95, method='hoeffding'):
    """One preselected bound for independent, bounded whole-collision counts.

    Empirical Bernstein is Maurer and Pontil (2009), Theorem 4, rescaled from
    [0,1] to [0,maximum_count]: https://arxiv.org/pdf/0907.3740 . Its variance
    is the unbiased sample variance. Keep its finite-range remainder even for
    zero observations. Choosing the smaller of two bounds needs union accounting.
    """
    if (type(collisions) is not int or collisions<2 or not 0<confidence<1
            or not math.isfinite(mean) or not math.isfinite(maximum_count)
            or not 0<=mean<=maximum_count):
        raise ValueError('Invalid bounded whole-collision mean inputs')
    delta = 1.-confidence
    variance = None
    if method == 'hoeffding':
        margin = maximum_count*math.sqrt(math.log(1./delta)/(2.*collisions))
        reference = 'https://arxiv.org/pdf/0907.3740, Theorem 1'
    elif method == 'empirical_bernstein':
        mean_square_sum = collisions*mean*mean
        if (sum_count_squared is None or not math.isfinite(sum_count_squared)
                or sum_count_squared < mean_square_sum-8.*math.ulp(mean_square_sum)
                or sum_count_squared > collisions*maximum_count*maximum_count):
            raise ValueError('Whole-collision second moment violates its finite range')
        variance = max(0.,(sum_count_squared-mean_square_sum)/(collisions-1))
        logarithm = math.log(2./delta)
        margin = (math.sqrt(2.*variance*logarithm/collisions)
                  +7.*maximum_count*logarithm/(3.*(collisions-1)))
        reference = 'https://arxiv.org/pdf/0907.3740, Theorem 4'
    else:
        raise ValueError('Unknown preselected collision mean bound')
    return dict(method=method,confidence=confidence,delta=delta,collisions=collisions,
        mean=mean,sum_collision_count_squared=sum_count_squared,sample_variance=variance,
        maximum_count=maximum_count,mean_upper=min(maximum_count,mean+margin),
        reference=reference,preselected_method=True,
        scope='Independent complete collisions; correlations among parents within each collision allowed. This is not a simultaneous bound across species or adaptively selected methods.')


def final_parent_yield_bound(parent, lifetime_settings, source_configuration, beams, collisions,
                             observed_mean, sigma_source_pb, confidence=.95, *, method='hoeffding',
                             sum_count_squared=None):
    """A deliberately loose bound for disjoint final-state parents only.

    Energy conservation bounds each whole collision count. Hoeffding's bound
    then needs independent collisions, never independent parents within one.
    The source event check permits a known relative final-energy deviation.
    This is conditional on this generator model, not an uncertainty on nature.
    """
    stable = (not parent['may_decay'] or (lifetime_settings['limitTau0']
              and parent['mean_proper_lifetime_mm'] >= lifetime_settings['tau0Max']))
    minimum = parent['mass_min_gev']
    if not stable or minimum <= 0 or not source_configuration['check_event']:
        return None
    # Pythia checks |m(p4)-m_native| <= mTolErr * max(E,1 GeV).
    # Solve E + mTolErr*max(E,1) >= m_min rather than assuming exact mass shell.
    mass_tolerance = source_configuration['mass_tolerance']
    minimum_energy = (minimum-mass_tolerance if minimum <= 1.+mass_tolerance
                      else minimum/(1.+mass_tolerance))
    if minimum_energy <= 0:
        return None
    energy = beams['eA_gev']+beams['eB_gev']
    ceiling = math.floor((1.+source_configuration['energy_tolerance'])*energy/minimum_energy)
    if not 0 <= observed_mean <= ceiling or not 0 < confidence < 1:
        raise ValueError('Invalid bounded final-parent source estimate')
    statistical = collision_mean_upper_bound(observed_mean,sum_count_squared,collisions,ceiling,confidence,method)
    upper = statistical['mean_upper']
    return dict(confidence=confidence, bound='one-sided '+method+' over complete independent collisions',
        statistical_bound=statistical,
        maximum_parents_per_collision=ceiling, minimum_native_mass_gev=minimum,
        minimum_final_particle_energy_gev=minimum_energy,
        allowed_native_mass_deviation_fraction=mass_tolerance,
        incoming_lab_energy_gev=energy, allowed_fractional_final_energy_deviation=source_configuration['energy_tolerance'],
        mean_parents_per_collision_upper=upper,
        assumption='Only final native parents with positive declared minimum mass; not a sum over different ancestor species.',
        maximum_accepted_events_if_branching_and_efficiency_are_one={str(lumi):upper*sigma_source_pb*lumi*1000.
            for lumi in (.03,.3,3.)},
        interpretation='Loose BR<=1 and efficiency<=1 ceiling conditional on the ND source model. No epsilon scaling, measured-source uncertainty, material correction or timelike-rate bound is implied.')


def resolved_source_inventory(model, particle_data, collisions, seed, timeout, *, examples_per_species=4,
                              heartbeat_path=None):
    """Fixed-exposure, unfiltered source census; no signal-decay selection."""
    if (type(collisions) is not int or not 2 <= collisions <= 100000
            or type(seed) is not int or not 1 <= seed <= 899999800 or timeout <= 0):
        raise ValueError('Require a bounded collision exposure, explicit seed and positive timeout')
    if model['source']['source_class'] != 'SoftQCD:nonDiffractive':
        raise ValueError('The census requires the declared primary non-diffractive pp source')
    from dark_photon_meson_native import native_source, cmssw_native_libraries
    from run_dark_photon_meson_gen import source_settings
    settings = source_settings(model)
    source = native_source(settings['source_settings'], seed,
                           list(dict.fromkeys(row['pdg_id'] for row in model['parents'])))
    table = particle_data_inventory(particle_data)
    # Read back the initialized table, including every mass/lifetime override.
    conjugates = []
    for row in table['particle_data']:
        pdg = row['pdg_id']
        data = source.pythia.particleData.particleDataEntryPtr(pdg)
        row.update(mass_gev=float(data.m0()), mass_width_gev=float(data.mWidth()),
                   mass_min_gev=float(data.m0Min()), mass_max_gev=float(data.m0Max()),
                   mean_proper_lifetime_mm=float(data.tau0()), may_decay=bool(data.mayDecay()),
                   calculate_lifetime=bool(data.tauCalc()))
        row['channels'] = [dict(br=float(data.channel(index).bRatio()),
            on_mode=int(data.channel(index).onMode()), products=[int(data.channel(index).product(j))
            for j in range(data.channel(index).multiplicity())]) for index in range(data.sizeChannels())]
        if data.hasAnti():
            conjugates.append(dict(row, pdg_id=-pdg,
                name=str(source.pythia.particleData.name(-pdg)),
                channels=[dict(channel, products=[-child if source.pythia.particleData.hasAnti(child)
                    else child for child in channel['products']]) for channel in row['channels']]))
    table['particle_data'].extend(conjugates)
    lifetime_settings = {key:bool(source.pythia.settings.flag('ParticleDecays:'+key))
                         for key in ('limitTau0','limitTau','limitRadius','limitCylinder')}
    lifetime_settings.update({key:float(source.pythia.settings.parm('ParticleDecays:'+key))
                              for key in ('tau0Max','tauMax','rMax','xyMax','zMax')})
    process_flags = {key:bool(source.pythia.settings.flag(key)) for key in
        ('SoftQCD:nonDiffractive','SoftQCD:elastic','SoftQCD:singleDiffractive',
         'SoftQCD:doubleDiffractive','SoftQCD:centralDiffractive','Charmonium:all',
         'Bottomonium:all','HardQCD:hardccbar','HardQCD:hardbbbar')}
    com_beams = dict(eA_gev=float(source.pythia.info.eA()),eB_gev=float(source.pythia.info.eB()),
                    pzA_gev=float(source.pythia.info.pzA()),pzB_gev=float(source.pythia.info.pzB()),
                    massA_gev=float(source.pythia.info.mA()),massB_gev=float(source.pythia.info.mB()))
    beam = None
    counts, second, occupied, cross, exclusions = Counter(), Counter(), Counter(), Counter(), Counter()
    features = defaultdict(lambda: defaultdict(FeatureMoments))
    mothers, examples, final_counts = defaultdict(Counter), defaultdict(list), Counter()
    quantile_rng = random.Random(seed ^ 0x5a17c3d9)
    stream_hash, started, accepted, calls = hashlib.sha256(), time.monotonic(), 0, 0
    last_heartbeat = started
    while accepted < collisions:
        if time.monotonic()-started > timeout or calls >= 10*collisions:
            raise TimeoutError('The fixed source census did not finish its declared exposure')
        calls += 1
        if not source.next():
            stream_hash.update(json.dumps(dict(trial=calls, generator_success=False), sort_keys=True).encode())
            continue
        accepted += 1
        if heartbeat_path is not None and time.monotonic()-last_heartbeat >= 30.:
            heartbeat = Path(heartbeat_path)
            temporary = heartbeat.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(source_collisions=accepted,source_calls=calls,
                requested_collisions=collisions,seed=seed,checked_epoch=time.time()))+'\n')
            temporary.replace(heartbeat)
            last_heartbeat = time.monotonic()
        snapshot = json.loads(str(source.inventory_snapshot()))
        if snapshot['native_weight'] != 1.:
            raise ValueError('The source census requires unchanged unit native weights')
        incoming = snapshot['incoming_beams']
        event_beam = dict(eA_gev=incoming[0]['p4'][0],eB_gev=incoming[1]['p4'][0],
            pzA_gev=incoming[0]['p4'][3],pzB_gev=incoming[1]['p4'][3],
            frame='actual native event record, after the fixed-target laboratory boost')
        if beam is None:
            beam = event_beam
        elif beam != event_beam:
            raise ValueError('The fixed source beam record changed across collisions')
        if snapshot['final_event_energy_gev'] > (beam['eA_gev']+beam['eB_gev'])*(1.+float(source.pythia.settings.parm('Check:epTolErr'))):
            raise ValueError('The final-event energy exceeds its declared native bound')
        raw = dict(trial=calls, collision=accepted, **snapshot)
        stream_hash.update(json.dumps(raw, sort_keys=True, separators=(',', ':')).encode())
        physical, excluded = physical_mesons(snapshot)
        exclusions.update(excluded)
        event_counts = Counter(row['pdg_id'] for row in physical)
        by_index = {row['index']: row for row in snapshot['mesons']}
        counts.update(event_counts)
        for pdg, count in event_counts.items():
            occupied[pdg] += 1
            second[pdg] += count*count
        for pdg, count in event_counts.items():
            for other, other_count in event_counts.items():
                if pdg <= other:
                    cross[pdg, other] += count*other_count
        for row in physical:
            pdg = row['pdg_id']
            momentum = math.sqrt(math.fsum(value*value for value in row['p4'][1:]))
            values = dict(momentum_gev=momentum, pt_gev=math.hypot(*row['p4'][1:3]),
                pz_gev=row['p4'][3], beta_gamma=momentum/row['native_mass_gev'],
                source_com_energy_gev=((beam['eA_gev']+beam['eB_gev'])*row['p4'][0]
                    -(beam['pzA_gev']+beam['pzB_gev'])*row['p4'][3])/snapshot['ecm_gev'],
                production_z_mm=row['production_position_mm'][2],
                stored_decay_z_mm=row['stored_decay_position_mm'][2],
                selected_proper_lifetime_mm=row['selected_proper_lifetime_mm'])
            if row['mean_proper_lifetime_mm'] > 0:
                values['proper_lifetime_over_mean'] = row['selected_proper_lifetime_mm']/row['mean_proper_lifetime_mm']
            for name, value in values.items():
                features[pdg][name].add(value,quantile_rng)
            final_counts[pdg] += int(row['final'])
            mother_pdgs = [by_index[index]['pdg_id'] for index in row['mother_indices'] if index in by_index]
            mothers[pdg].update(mother_pdgs)
            if len(examples[pdg]) < examples_per_species:
                examples[pdg].append(dict(trial=calls, collision=accepted, **row,
                    meson_mothers=[by_index[index] for index in row['mother_indices'] if index in by_index]))
    native = json.loads(str(source.summary()))
    if native['source_collisions'] != collisions or native['source_calls'] != calls:
        raise ValueError('Native and census source exposures disagree')
    table_by_id = {row['pdg_id']:row for row in table['particle_data']}
    table_ids = set(table_by_id)
    modeled = {row['pdg_id'] for row in model['parents']}
    species = []
    for pdg in sorted(table_ids | set(counts)):
        count, mean = counts[pdg], counts[pdg]/collisions
        variance = max(0., (second[pdg]-collisions*mean*mean)/(collisions-1))
        bound = (final_parent_yield_bound(table_by_id[pdg], lifetime_settings,
            json.loads(str(source.configuration())), beam, collisions, mean, native['sigma_source_mb']*1.e9)
            if pdg in table_by_id else None)
        if bound and final_counts[pdg] != count:
            raise ValueError('A source-table final-parent bound conflicts with native particle status')
        species.append(dict(pdg_id=pdg, physical_parents=count,
            collisions_with_parent=occupied[pdg], mean_parents_per_collision=mean,
            sum_collision_count_squared=second[pdg],
            standard_error_from_independent_collisions=math.sqrt(variance/collisions),
            inclusive_parent_multiplicity_cross_section_pb=mean*native['sigma_source_mb']*1.e9,
            final_parent_source_yield_upper=bound,
            zero_observed_any_parent_probability_upper_95=(1.-.05**(1./collisions) if not count else None),
            zero_observed_is_zero_physical_rate=False, modeled_parent_species=pdg in modeled,
            final_native_records=final_counts[pdg],
            meson_mother_counts={str(k):v for k,v in sorted(mothers[pdg].items())},
            features={key:value.summary() for key,value in features[pdg].items()},
            exact_native_examples=examples[pdg]))
    return dict(table, schema='shift-resolved-unfiltered-meson-source-inventory-v1',
        source_sampling_verified=True, resolved_source_configuration=True,
        source_class='SoftQCD:nonDiffractive', seed=seed,
        collision_exposure=collisions, generator_calls=calls, native=native,
        resolved_settings=settings, source_configuration=json.loads(str(source.configuration())),
        resolved_beams=beam, native_info_beams_com_frame=com_beams,
        native_event_check_reference='https://gitlab.com/Pythia8/releases/-/raw/pythia8317/src/Pythia.cc',
        resolved_lifetime_settings=lifetime_settings, resolved_process_flags=process_flags,
        lifetime_documentation=dict(path=str(Path(particle_data).with_name('ParticleDecays.xml').resolve()),
            sha256=sha256(Path(particle_data).with_name('ParticleDecays.xml')),
            native_rule='A tau is selected and stored for each particle whether it decays or not; a mean-tau0 cutoff is distinct from an actual-tau cutoff.'),
        runtime=dict(cmssw_base=os.environ.get('CMSSW_BASE'),cmssw_version=os.environ.get('CMSSW_VERSION'),
                     scram_arch=os.environ.get('SCRAM_ARCH')),
        native_libraries=[dict(path=path,sha256=sha256(path)) for path in cmssw_native_libraries()],
        source_tool_sha256={str(Path(path).resolve()):sha256(path) for path in
            (__file__, native_source.__code__.co_filename, source_settings.__code__.co_filename,
             source_settings.__globals__['beam_settings'].__code__.co_filename,
             source_settings.__globals__['dipion_channel'].__code__.co_filename)},
        particle_data_path=str(Path(particle_data).resolve()), particle_data_sha256=sha256(particle_data),
        source_record_stream_sha256=stream_hash.hexdigest(),
        source_record_stream_definition='Canonical JSON per attempted trial; failed calls retained; successful snapshots include every meson before signal selection.',
        quantile_sampling=dict(seed=seed ^ 0x5a17c3d9,maximum_values_per_species_feature=512,
            independent_of_native_generator=True,changes_source_sampling=False),
        excluded_bookkeeping=dict(exclusions), species=species,
        collision_count_cross_moments=[dict(pdg_ids=[a,b],sum_count_product=value) for (a,b),value in sorted(cross.items())],
        interpretation='A primary ND Pythia source census, not measured inclusive hadron production or a meson-amplitude completeness proof. Different species in feed-down chains are correlated; all moments use whole collisions.',
        lifetime_interpretation='Stored native vDec/tau do not prove an actual decay or HepMC endvertex. Final long-lived particles need a separately reviewed flight/decay treatment.',
        omitted_yield_bounds='No epsilon-scaled bound without a reviewed timelike branching/form-factor ceiling and a finite parent-multiplicity confidence bound. Zero counts bound only the probability of at least one parent in this exact source model.',
        quarkonium_source_caveat='Counts characterize the configured SoftQCD ND, MPI and fragmentation model only. Primary hard-process Charmonium/Bottomonium flags do not disable onia included separately by MPI processLevel=3. Zero counts do not establish zero physical quarkonium production or qualify missing direct eta_c mechanisms.',
        com_energy_bound_caveat='Parent COM energies use Lorentz dot(P_beams,p)/sqrt(s). A tighter sqrt(s)/m multiplicity ceiling is not asserted: the native check allows laboratory-frame energy and energy-dependent mass-shell deviations. Final-state per-species parents are an antichain; different ancestor species are never summed for a ceiling.',
        elapsed_seconds=time.monotonic()-started)


def combine_source_inventories(inventories):
    """Pool compatible independent sources once, using raw collision moments.

    The caller should pin each input file and require completed worker receipts.
    This function performs no scheduler queries and cannot clear failed workers.
    """
    if not inventories:
        raise ValueError('No completed source inventories to combine')
    identity_keys = ('schema','source_class','model_sha256','resolved_settings','source_configuration',
        'resolved_beams','native_info_beams_com_frame','resolved_lifetime_settings','resolved_process_flags',
        'particle_data','native_libraries')
    reference, accepted, by_seed, excluded = None, [], {}, []
    for inventory in inventories:
        if (inventory.get('schema') != 'shift-resolved-unfiltered-meson-source-inventory-v1'
                or inventory.get('source_sampling_verified') is not True
                or inventory.get('resolved_source_configuration') is not True):
            raise ValueError('Only verified, unpooled fixed-source inventories can be combined')
        count, seed = inventory['collision_exposure'], inventory['seed']
        native = inventory['native']
        if (type(count) is not int or count<2 or type(seed) is not int
                or native['source_collisions'] != count or native['source_calls'] != inventory['generator_calls']
                or native['generator_failures']+count != native['source_calls']
                or not math.isfinite(native['sigma_source_mb']) or native['sigma_source_mb']<=0):
            raise ValueError('Incomplete or inconsistent source exposure')
        identity = {key:inventory[key] for key in identity_keys}
        identity['source_tools_by_name'] = {Path(path).name:digest
            for path,digest in inventory['source_tool_sha256'].items()}
        identity['runtime'] = {key:inventory['runtime'][key] for key in ('cmssw_version','scram_arch')}
        if reference is not None:
            differences = [key for key in reference if reference[key] != identity[key]]
            if differences:
                raise ValueError('Source physics or producer authority differs: '+', '.join(differences))
        if seed in by_seed:
            previous = by_seed[seed]
            if (count != previous['collision_exposure']
                    or inventory['source_record_stream_sha256'] != previous['source_record_stream_sha256']
                    or inventory['species'] != previous['species']
                    or inventory['collision_count_cross_moments'] != previous['collision_count_cross_moments']):
                raise ValueError('Conflicting reuse of a source seed needs explicit review: '+str(seed))
            excluded.append(dict(seed=seed,source_collisions=count,
                source_record_stream_sha256=inventory['source_record_stream_sha256'],
                reason='Repeated source revision; exposure counted once'))
            continue
        if reference is None:
            reference = identity
        by_seed[seed] = inventory
        accepted.append(inventory)
    seeds = {item['seed'] for item in accepted}
    if len(seeds) != len(accepted):
        raise ValueError('Independent source seeds were not unique')
    total = sum(item['collision_exposure'] for item in accepted)
    species_ids = sorted({row['pdg_id'] for item in accepted for row in item['species']})
    counts, squares, occupied, final_counts, cross = Counter(), Counter(), Counter(), Counter(), Counter()
    rows_by_species, mothers = defaultdict(list), defaultdict(Counter)
    for inventory in accepted:
        n = inventory['collision_exposure']
        rows = {row['pdg_id']:row for row in inventory['species']}
        if len(rows)!=len(inventory['species']):
            raise ValueError('A source repeats its parent species')
        if not {row['pdg_id'] for row in inventory['particle_data']} <= set(rows):
            raise ValueError('A source omits explicit particle-table zero-count species')
        for pdg,row in rows.items():
            count, second, nonzero = (row[key] for key in
                ('physical_parents','sum_collision_count_squared','collisions_with_parent'))
            if (any(type(value) is not int or value<0 for value in (count,second,nonzero))
                    or not 0<=nonzero<=min(n,count) or (count>0 and nonzero==0)
                    or second<count or second*n<count*count
                    or not 0<=row['final_native_records']<=count):
                raise ValueError('Invalid raw whole-collision parent moments')
            counts[pdg] += count
            squares[pdg] += second
            occupied[pdg] += nonzero
            final_counts[pdg] += row['final_native_records']
            mothers[pdg].update(row['meson_mother_counts'])
            rows_by_species[pdg].append((inventory,row))
        seen = set()
        for moment in inventory['collision_count_cross_moments']:
            a,b = moment['pdg_ids']
            value = moment['sum_count_product']
            if (a>b or (a,b) in seen or a not in rows or b not in rows
                    or type(value) is not int or value<0
                    or value*value>rows[a]['sum_collision_count_squared']*rows[b]['sum_collision_count_squared']):
                raise ValueError('Invalid raw collision-count cross product')
            seen.add((a,b))
            cross[a,b] += value
            if a==b and value != rows[a]['sum_collision_count_squared']:
                raise ValueError('A diagonal cross moment differs from its second moment')
        if any(row['sum_collision_count_squared'] and (pdg,pdg) not in seen for pdg,row in rows.items()):
            raise ValueError('A nonzero parent lacks its raw diagonal cross moment')
    result = copy.deepcopy(accepted[0])
    sigma_values = [item['native']['sigma_source_mb'] for item in accepted]
    # Repeated sources share one production model. Keep every returned sigma;
    # the largest numerical reference is conservative, not a physical sigma CI.
    sigma = max(sigma_values)
    result['species'] = []
    table = {row['pdg_id']:row for row in result['particle_data']}
    for pdg in species_ids:
        mean = counts[pdg]/total
        variance = max(0.,(squares[pdg]-total*mean*mean)/(total-1))
        features, examples = {}, []
        feature_names = sorted({key for _,row in rows_by_species[pdg] for key in row['features']})
        for name in feature_names:
            pieces = [row['features'][name] for _,row in rows_by_species[pdg] if name in row['features']]
            size = sum(piece['count'] for piece in pieces)
            center = math.fsum(piece['count']*piece['mean'] for piece in pieces)/size
            complete_variances = all(piece.get('sample_variance') is not None for piece in pieces)
            m2 = math.fsum((piece['count']-1)*piece['sample_variance']+piece['count']*(piece['mean']-center)**2
                          for piece in pieces) if complete_variances else None
            features[name] = dict(count=size,minimum=min(piece['minimum'] for piece in pieces),
                maximum=max(piece['maximum'] for piece in pieces),mean=center,
                sample_variance=(m2/(size-1) if m2 is not None and size>1 else None),
                quantiles=None,quantile_method='Not pooled: per-worker quantile summaries do not define a pooled quantile.')
        for inventory,row in rows_by_species[pdg]:
            for example in row['exact_native_examples']:
                if len(examples)<4:
                    examples.append(dict(example,source_seed=inventory['seed']))
        bound = (final_parent_yield_bound(table[pdg],result['resolved_lifetime_settings'],
            result['source_configuration'],result['resolved_beams'],total,mean,sigma*1.e9,
            sum_count_squared=squares[pdg]) if pdg in table else None)
        if bound and final_counts[pdg] != counts[pdg]:
            raise ValueError('A final-parent ceiling conflicts with pooled native statuses')
        result['species'].append(dict(pdg_id=pdg,physical_parents=counts[pdg],
            collisions_with_parent=occupied[pdg],mean_parents_per_collision=mean,
            sum_collision_count_squared=squares[pdg],standard_error_from_independent_collisions=math.sqrt(variance/total),
            inclusive_parent_multiplicity_cross_section_pb=mean*sigma*1.e9,
            zero_observed_any_parent_probability_upper_95=(1.-.05**(1./total) if not counts[pdg] else None),
            zero_observed_is_zero_physical_rate=False,modeled_parent_species=rows_by_species[pdg][0][1]['modeled_parent_species'],
            final_native_records=final_counts[pdg],final_parent_source_yield_upper=bound,
            meson_mother_counts=dict(mothers[pdg]),features=features,exact_native_examples=examples))
    result.update(schema='shift-pooled-unfiltered-meson-source-inventory-v1',collision_exposure=total,
        generator_calls=sum(item['generator_calls'] for item in accepted),
        seeds=sorted(seeds),seed=None,excluded_duplicate_revisions=excluded,
        collision_count_cross_moments=[dict(pdg_ids=[a,b],sum_count_product=value) for (a,b),value in sorted(cross.items())],
        input_sources=[dict(seed=item['seed'],source_collisions=item['collision_exposure'],
            source_record_stream_sha256=item['source_record_stream_sha256'],native=item['native']) for item in accepted],
        sigma_source_pooling='Conservative maximum of observed repeated-generator references; not a confidence bound on physical production cross section. Every source sigma is retained.',
        pooled_quantiles_supported=False,
        aggregation_code_sha256=sha256(__file__),
        elapsed_seconds=sum(item.get('elapsed_seconds',0.) for item in accepted))
    result['native'] = {key:sum(item['native'].get(key,0) for item in accepted) for key in
        ('source_calls','generator_failures','source_collisions','native_n_tried','native_n_accepted')}
    result['native'].update(sigma_source_mb=sigma,
        sigma_source_error_mb=max(item['native']['sigma_source_error_mb'] for item in accepted))
    result['excluded_bookkeeping'] = dict(sum((Counter(item['excluded_bookkeeping']) for item in accepted),Counter()))
    result.pop('source_record_stream_sha256',None)
    result.pop('source_record_stream_definition',None)
    result.pop('quantile_sampling',None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--particle-data', required=True)
    parser.add_argument('--source-inventory')
    parser.add_argument('--model', required=True)
    parser.add_argument('--completion-scope', help='Retain the pinned detailed scope alongside generic inventory gaps')
    parser.add_argument('--masses', nargs='+', type=float, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--source-collisions', type=int,
                        help='Also sample this fixed unfiltered source exposure in the current CMSSW runtime.')
    parser.add_argument('--seed', type=int)
    parser.add_argument('--timeout', type=float, default=600.)
    parser.add_argument('--sampled-inventory-output')
    parser.add_argument('--examples-per-species', type=int, default=4)
    parser.add_argument('--heartbeat')
    args = parser.parse_args()
    defaults = particle_data_inventory(args.particle_data)
    model = json.loads(Path(args.model).read_text())
    if args.source_collisions is not None:
        if args.source_inventory or not args.sampled_inventory_output or args.seed is None:
            parser.error('Source sampling needs --seed and --sampled-inventory-output, without --source-inventory')
        if not 0 <= args.examples_per_species <= 4:
            parser.error('--examples-per-species must be between zero and four')
        source = resolved_source_inventory(model, args.particle_data, args.source_collisions, args.seed, args.timeout,
            examples_per_species=args.examples_per_species,heartbeat_path=args.heartbeat)
        source.update(model_path=str(Path(args.model).resolve()),model_sha256=sha256(args.model),
                      code_sha256=sha256(__file__))
        inventory_path = Path(args.sampled_inventory_output)
        if inventory_path.exists():
            raise FileExistsError('Sampled source evidence is immutable; choose a fresh output path')
        inventory_path.write_text(json.dumps(source, indent=2, sort_keys=True)+'\n')
        args.source_inventory = str(inventory_path)
    else:
        source = json.loads(Path(args.source_inventory).read_text()) if args.source_inventory else defaults
    report = audit_coverage(model, args.masses, source)
    if args.completion_scope:
        report = bind_completion_scope(report, json.loads(Path(args.completion_scope).read_text()),
                                       sha256(args.completion_scope))
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
