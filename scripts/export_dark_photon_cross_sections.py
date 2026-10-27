#!/usr/bin/env python3
# Code style: AI-code. AI-generated durable dark-photon rate and GEN metadata exporter.
"""Retain audited GEN cross sections without changing a production deployment.

Ordinary plotting does not use this audit. This offline exporter preserves
full-precision per-run rates and denominators for later yield calculations.
It never submits, edits a sealed deployment, overwrites a different artifact,
or combines independent cross-section estimates without their trial weights.
"""
import argparse
import atexit
from bisect import bisect_right
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from pythia_generation_ledger import read_pythia_generation_ledger, same_ledger_content


SCHEMA = "shift-dark-photon-cross-sections-v1"
SAFE_NAME = re.compile(r"[A-Za-z0-9_-]+")
_MESON_LEDGER_READBACKS = {}


def cleanup_meson_ledger_readbacks():
    """Remove only temporary remote readbacks created by this process."""
    for temporary, unused_path in _MESON_LEDGER_READBACKS.values():
        temporary.cleanup()
    _MESON_LEDGER_READBACKS.clear()


atexit.register(cleanup_meson_ledger_readbacks)


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def canonical_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def retain_bytes(path, payload):
    """Create once, or verify a byte-identical previous export."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("Refuse to overwrite different retained metadata: " + str(path))
    else:
        with path.open("xb") as stream:
            stream.write(payload)


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Require positive finite " + name)
    return float(value)


def validate_rates(rows):
    if not rows:
        raise ValueError("Missing authoritative GEN run cross sections")
    result = []
    for row in rows:
        value = positive(row["value_pb"], "cross section in pb")
        error = row["error_pb"]
        if isinstance(error, bool) or not isinstance(error, (int, float)) or not math.isfinite(error) or error < 0:
            raise ValueError("Require finite nonnegative cross-section error")
        result.append(dict(value_pb=value, error_pb=float(error)))
    return result


def dimuon_branching(table):
    if "br_mumu" in table:
        value = table["br_mumu"]
    else:
        value = math.fsum(row["branching_fraction"] for row in table["channels"]
                          if sorted(row["products"]) == [-13, 13])
    value = positive(value, "dimuon branching fraction")
    if value > 1:
        raise ValueError("Branching fraction exceeds one")
    return value


def nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Require finite nonnegative " + name)
    return float(value)


def same_number(actual, expected, name):
    if not math.isclose(nonnegative(actual, name), expected, rel_tol=1e-10, abs_tol=1e-100):
        raise ValueError("Meson source accounting differs: " + name)


def same_signed_number(actual, expected, name, *, absolute_roundoff=0.):
    if (type(actual) not in (int, float) or not math.isfinite(actual) or
            not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=max(1e-100,absolute_roundoff))):
        raise ValueError("Meson native precision accounting differs: " + name)


def native_precision_roundoff(parent,checked,coefficient):
    """Arithmetic budget only for subtraction-derived native diagnostics.

    Positive physical coefficients still use the unchanged relative check.
    The diagnostics subtract nearby masses/rates or subtract one from a
    near-unit ratio. Reserve 32 ulps of those underlying operands for their
    scalar evaluation, subtraction and division across supported runtimes.
    This is a numerical acceptance bound, not a physical model uncertainty.
    Aggregate difference sums receive the sum of their per-parent budgets.
    """
    scale=max(abs(coefficient),abs(coefficient+parent['coefficient_difference_from_kinematic_mass']),
              abs(coefficient+checked['coefficient_difference_from_kinematic_mass']))
    return dict(coefficient_relative_precision_shift=32*math.ulp(1.),
        coefficient_difference_from_kinematic_mass=32*math.ulp(scale),
        native_mass_shell_deviation=32*math.ulp(max(parent['native_mass_gev'],checked['kinematic_mass_gev']))/
            max(parent['p4'][0],1.))


def cdf_payload_difference_bound(first, second):
    """Bound the L1 difference of two normalized positive bilinear tables.

    Use the union of both grids. On each resulting cell, the integral of the
    absolute difference is bounded by its four absolute corner differences.
    This remains meaningful at amplitude zeros, where relative node errors
    can be large despite negligible probability. No node is discarded.
    """
    def grid(payload):
        x,y,z=payload['axes'],payload['cosines'],payload['values']
        if (not 2<=len(x)<=1024 or not 2<=len(y)<=1024
                or x[0]!=0. or x[-1]!=1. or y[0]!=-1. or y[-1]!=1.
                or any(not math.isfinite(v) for v in x+y)
                or any(a>=b for a,b in zip(x,x[1:])) or any(a>=b for a,b in zip(y,y[1:]))
                or len(z)!=len(x) or any(len(row)!=len(y) for row in z)
                or any(not math.isfinite(v) or v<0 for row in z for v in row)):
            raise ValueError('Invalid positive CDF grid')
        integral=math.fsum((b-a)*(d-c)*math.fsum((z[i][j],z[i+1][j],z[i][j+1],z[i+1][j+1]))/4
            for i,(a,b) in enumerate(zip(x,x[1:])) for j,(c,d) in enumerate(zip(y,y[1:])))
        if integral<=0:raise ValueError('CDF grid has no positive probability')
        return x,y,z,integral
    a,b=grid(first),grid(second)
    def value(g,x,y):
        xs,ys,z,_=g;i=max(0,min(len(xs)-2,bisect_right(xs,x)-1));j=max(0,min(len(ys)-2,bisect_right(ys,y)-1))
        u=(x-xs[i])/(xs[i+1]-xs[i]);v=(y-ys[j])/(ys[j+1]-ys[j])
        return (1-u)*((1-v)*z[i][j]+v*z[i][j+1])+u*((1-v)*z[i+1][j]+v*z[i+1][j+1])
    xs=sorted(set(a[0]+b[0]));ys=sorted(set(a[1]+b[1]));terms=[]
    for x0,x1 in zip(xs,xs[1:]):
        for y0,y1 in zip(ys,ys[1:]):
            corners=[abs(value(a,x,y)-value(b,x,y)) for x,y in ((x0,y0),(x1,y0),(x0,y1),(x1,y1))]
            terms.append((x1-x0)*(y1-y0)*math.fsum(corners)/4)
    absolute=math.fsum(terms)+64*math.ulp(max(a[3],b[3]))
    relative=2*absolute/min(a[3],b[3])
    if relative>1.e-5:raise ValueError('Independent CDF probability crosscheck differs by more than 10 ppm')
    return dict(normalized_l1_bound=relative,native_integral=a[3],portable_integral=b[3],all_cells_checked=True)


def same_resolved_meson_model(stored, resolved, name='resolved exclusive meson model'):
    """Compare identities exactly and floating inputs at accounting precision.

    CMSSW and the standalone exporter use different Python/libm versions.
    Their hypot results can differ by one ulp for identical physical inputs.
    """
    if isinstance(resolved, dict):
        if resolved.get('schema') == 'shift-neutral-vector-dipion-positive-cdf-rate-v1':
            if not isinstance(stored, dict):
                raise ValueError(name + ' lacks the native CDF authority')
            # Retain the exact native SHA, and verify the payload it identifies.
            # Then compare every numerical node at the existing accounting
            # precision. Hash equality alone is not portable across libm.
            for authority in (stored, resolved):
                payload=authority.get('cdf_table_payload')
                if payload is not None:
                    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,
                        separators=(',',':'),allow_nan=False).encode()).hexdigest()
                    if digest != authority.get('cdf_table_sha256'):
                        raise ValueError(name + ' CDF payload differs from its recorded SHA')
                elif 'cdf_table_payload' in authority and authority.get('cdf_table_sha256') is not None:
                    raise ValueError(name + ' CDF SHA has no numerical payload')
                elif 'cdf_table_payload' in authority and authority.get('width_over_em_mixing_squared_gev') != 0.:
                    raise ValueError(name + ' closed CDF has nonzero rate')
            if 'cdf_table_payload' not in stored:
                if stored.get('cdf_table_sha256') != resolved.get('cdf_table_sha256'):
                    raise ValueError(name + ' legacy CDF requires its original native payload')
                resolved={key:value for key,value in resolved.items() if key!='cdf_table_payload'}
            else:
                first,second=stored.get('cdf_table_payload'),resolved.get('cdf_table_payload')
                if (first is None)!=(second is None):raise ValueError(name+' changed whether the CDF is open')
                if first is not None:
                    same_resolved_meson_model({k:v for k,v in first.items() if k not in ('axes','cosines','values')},
                        {k:v for k,v in second.items() if k not in ('axes','cosines','values')},name+'.CDF inputs')
                    comparison=cdf_payload_difference_bound(first,second)
                    same_signed_number(stored['width_over_em_mixing_squared_gev'],comparison['native_integral'],name+'.native CDF integral')
                    same_signed_number(resolved['width_over_em_mixing_squared_gev'],comparison['portable_integral'],name+'.portable CDF integral')
                omitted=('cdf_table_sha256','cdf_table_payload','width_over_em_mixing_squared_gev')
                stored={key:value for key,value in stored.items() if key not in omitted}
                resolved={key:value for key,value in resolved.items() if key not in omitted}
        if not isinstance(stored, dict) or stored.keys() != resolved.keys():
            raise ValueError(name + ' keys differ from the native authority')
        for key in resolved:
            same_resolved_meson_model(stored[key], resolved[key], name + '.' + str(key))
    elif isinstance(resolved, list):
        if not isinstance(stored, list) or len(stored) != len(resolved):
            raise ValueError(name + ' list differs from the native authority')
        for index, (actual, expected) in enumerate(zip(stored, resolved)):
            same_resolved_meson_model(actual, expected, name + '[' + str(index) + ']')
    elif type(resolved) is float:
        same_signed_number(stored, resolved, name)
    elif type(stored) is not type(resolved) or stored != resolved:
        raise ValueError(name + ' identity differs from the native authority')


def open_meson_ledger(path):
    """Read the exact JSONL text without expanding a compressed ledger on disk."""
    path = Path(path)
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")


def meson_ledger_readback(path, receipt):
    """Fetch a frozen ledger when this worker has no native EOS mount.

    One verified download per immutable receipt is reused within a process.
    The caller still checks the actual encoded and original hashes. Temporary
    files remain in worker scratch or /tmp and are removed at process exit.
    """
    key = (str(path), receipt['sha256'], receipt['bytes'],
           receipt['uncompressed_sha256'], receipt['uncompressed_bytes'])
    if key in _MESON_LEDGER_READBACKS:
        return _MESON_LEDGER_READBACKS[key][1]
    scratch = Path(os.environ.get('_CONDOR_SCRATCH_DIR', '/tmp'))
    if not scratch.is_absolute() or str(scratch).startswith(('/afs/', '/eos/')):
        raise ValueError('Meson ledger readback requires local worker scratch or /tmp')
    temporary = tempfile.TemporaryDirectory(prefix='dp-meson-ledger-', dir=scratch)
    target = Path(temporary.name) / path.name
    environment = {key: value for key, value in os.environ.items() if key not in
                   ('PYTHONPATH', 'PYTHONHOME', 'LD_LIBRARY_PATH', 'LD_PRELOAD', 'ROOTSYS')}
    environment['PATH'] = '/usr/bin:/bin'
    try:
        subprocess.run(['/usr/bin/xrdcp', '--silent', '--cksum', 'adler32',
            'root://eosuser.cern.ch/' + str(path), str(target)],
            env=environment, check=True, capture_output=True, text=True, timeout=1800)
        if target.stat().st_size != receipt['bytes'] or sha256(target) != receipt['sha256']:
            raise ValueError('Remote meson ledger readback failed SHA-256/size verification')
    except Exception:
        temporary.cleanup()
        raise
    _MESON_LEDGER_READBACKS[key] = (temporary, target)
    return target


def resolve_meson_ledger(directory, contract):
    """Resolve one local ledger or a pinned canonical EOS publication.

    The receipt preserves both stored and original byte identities. It never
    substitutes for reading the actual publication, including decompressed
    bytes for gzip. No inventory discovery or compatibility alias is used.
    """
    directory = Path(directory)
    expected = contract["source_ledger_sha256"]
    local = directory / "meson_source_ledger.jsonl"
    if local.is_file():
        if sha256(local) != expected:
            raise ValueError("Pinned meson source input changed: meson_source_ledger.jsonl")
        return local
    receipt = read(directory / "meson_ledger_publication.json")
    if receipt.get("schema") != "shift-meson-source-ledger-publication-v1":
        raise ValueError("Unknown meson source ledger publication receipt")
    name = receipt.get("path")
    if not isinstance(name, str):
        raise ValueError("Meson source ledger publication requires an explicit EOS path")
    path = Path(name)
    allowed = ("/eos/user/j/jniedzie/shift_cmssw/", "/eos/user/j/jniedzie/shift_cmssw_support/")
    if (not path.is_absolute() or ".." in path.parts or str(path) != name or
            not name.startswith(allowed)):
        raise ValueError("Meson source ledger publication is outside its canonical EOS namespace")
    encoding = receipt.get("encoding")
    filename = "meson_source_ledger.jsonl.gz" if encoding == "gzip" else "meson_source_ledger.jsonl"
    if encoding not in ("identity", "gzip") or path.name != filename:
        raise ValueError("Meson source ledger encoding or canonical filename changed")
    if (type(receipt.get("bytes")) is not int or receipt["bytes"] <= 0 or
            type(receipt.get("uncompressed_bytes")) is not int or receipt["uncompressed_bytes"] <= 0 or
            receipt.get("sha256") != receipt.get("readback_sha256") or
            receipt.get("uncompressed_sha256") != expected):
        raise ValueError("Meson source ledger publication proof differs from its frozen authority")
    try:
        stored_size = path.stat().st_size
    except FileNotFoundError:
        path = meson_ledger_readback(path, receipt)
        stored_size = path.stat().st_size
    if stored_size != receipt["bytes"] or sha256(path) != receipt["sha256"]:
        raise ValueError("Published meson source ledger failed SHA-256/size readback")
    if encoding == "identity":
        if receipt["sha256"] != expected or receipt["bytes"] != receipt["uncompressed_bytes"]:
            raise ValueError("Uncompressed meson source ledger identity changed")
    else:
        checksum, size = hashlib.sha256(), 0
        with gzip.open(path, "rb") as source:
            for block in iter(lambda: source.read(1048576), b""):
                size += len(block)
                checksum.update(block)
        if size != receipt["uncompressed_bytes"] or checksum.hexdigest() != expected:
            raise ValueError("Decompressed meson source ledger differs from its full original bytes")
    return path


def meson_physics_input_files(directory,contract):
    """Only explicitly declared frozen rate inputs belong to retained metadata."""
    inputs={parent['ks_dispersive_reference']['path']:parent['ks_dispersive_reference']['sha256']
            for parent in contract['meson_model_card']['parents'] if 'ks_dispersive_reference' in parent}
    if inputs!=contract.get('meson_physics_inputs_sha256',{}):
        raise ValueError('Declared meson rate inputs differ from their contract manifest')
    for relative,expected in inputs.items():
        path=Path(relative)
        if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='physics_inputs':
            raise ValueError('Meson rate inputs require explicit frozen relative paths')
        if sha256(Path(directory)/path)!=expected:
            raise ValueError('Pinned meson rate input changed: '+relative)
    return inputs


def same_native_parent_precision_report(stored,checked,*,deferred_policy=False):
    """Retain exact deferred proofs and the original strict report schema."""
    if stored==checked:return
    if (not deferred_policy and isinstance(stored,dict) and isinstance(checked,dict) and
            checked.get('deferred_full_source_check') is False):
        legacy=dict(checked)
        for key in ('deferred_full_source_check','scope','nominal_parent_coefficient',
                    'conservative_absolute_parent_measure_bound'):legacy.pop(key,None)
        if stored==legacy:return
    raise ValueError('Stored native parent precision report differs from its pinned model audit')


def meson_source_exposure(contract, directory):
    """Reconstruct the fixed collision exposure, including failures and zeroes.

    The collision probability already includes BR(A' -> mumu). Pythia's
    successful source count is independent of the number of emitted signal
    events. Never replace its terminal counters with the latter.
    """
    from dark_photon_meson import MUON_MASS_GEV, radiative_branching, transition_form_factor_abs2

    directory = Path(directory)
    exposure_path = directory / "source_exposure.json"
    ledger_path = resolve_meson_ledger(directory, contract)
    model_path = directory / "meson_model.json"
    for path, key in [(exposure_path, "source_exposure_sha256"),
                      (model_path, "meson_model_card_sha256")]:
        if not path.is_file() or sha256(path) != contract.get(key):
            raise ValueError("Pinned meson source input changed: " + path.name)
    exposure, card = read(exposure_path), read(model_path)
    if exposure != contract.get("source_exposure") or card != contract.get("meson_model_card"):
        raise ValueError("Frozen meson source/model differs from its contract")
    meson_physics_input_files(directory,contract)
    if exposure.get("schema") != "shift-meson-source-exposure-v1":
        raise ValueError("Unknown meson source exposure schema")
    if card.get("schema") != "shift-dark-photon-meson-model-v1":
        raise ValueError("Unknown meson physical model card")
    parents = {item["pdg_id"]: item for item in card["parents"]}
    unified = contract.get("production_mechanism") == "meson-radiative"
    from dark_photon_meson_source import (PARENTS,validate_native_precision_policy,
        deferred_native_precision_measure,full_source_native_precision_summary,
        exclusive_channels,radiative_cascade_owner)
    precision_policy=card.get('native_precision_policy')
    deferred_precision=validate_native_precision_policy(precision_policy)
    supported = set(PARENTS) if unified else {221, 331}
    if not parents or set(parents) - supported or (not unified and len(parents) != len(card["parents"])):
        raise ValueError("Require distinct supported exclusive meson parent models")
    if unified and (card.get("mechanism") != "meson-radiative" or
                    contract.get("exclusive_channels") != card.get("channels") or not card.get("channels")):
        raise ValueError("Unified meson channel ownership differs from its model card")
    for flag in ("coefficient_includes_br_mumu", "coefficient_excludes_epsilon_squared", "unit_output_gen_weights"):
        if exposure.get(flag) is not True:
            raise ValueError("Meson source convention is missing: " + flag)
    if exposure.get("source_ledger_sha256") != contract["source_ledger_sha256"]:
        raise ValueError("Meson source ledger ownership changed")
    count = exposure["fixed_source_collisions"]
    if type(count) is not int or count <= 1 or exposure["source_collisions"] != count:
        raise ValueError("Require a fixed source exposure of at least two collisions")
    bound = positive(exposure["analytic_coefficient_bound"], "analytic meson coefficient bound")
    epsilon = positive(contract["epsilon"], "physical meson epsilon")
    if epsilon > .1 or positive(contract["mass_gev"], "dimuon mass") <= 2 * MUON_MASS_GEV:
        raise ValueError("Meson signal requires an open dimuon channel and small physical mixing")
    width = contract["physical_width_table"]
    branching = dimuon_branching(width)
    same_number(width["mass_gev"], contract["mass_gev"], "physical width mass")
    same_number(width["epsilon"], epsilon, "physical width epsilon")
    positive(width["ctau_mm"], "physical proper lifetime")
    total_width = positive(width["total_width_gev"], "physical total width")
    same_number(math.fsum(nonnegative(value, "partial width")
                          for value in width["partial_widths_gev"].values()), total_width, "partial width sum")
    same_number(width["partial_widths_gev"]["mu_mu"] / total_width, branching, "physical dimuon branching")
    same_number(width["ctau_mm"] * total_width, 1.973269804e-13, "proper lifetime-width relation")
    if width.get("spectral_source") != contract.get("width_authority"):
        raise ValueError("Meson width table differs from its pinned spectral authority")
    precision = contract.get("source_precision")
    precision_models = {}
    if precision is not None:
        from dark_photon_meson_source import model_precision_budget, resolve_parent_kinematics
        if precision.get("check_event") is not True:
            raise ValueError("The native Pythia event check must remain enabled")
        positive(precision["mass_tolerance"], "native Pythia mass tolerance")
        positive(precision["energy_tolerance"], "native Pythia energy tolerance")
        reference = Path(precision["reference"])
        if reference.name != "ErrorChecks.xml" or sha256(reference) != precision["reference_sha256"]:
            raise ValueError("Pinned native Pythia precision reference changed")
        if precision.get("prescription") != "native generated mass for branching and bound; unchanged p4 invariant for decay":
            raise ValueError("Native parent precision prescription changed")
        if unified:
            from dark_photon_meson_source import resolved_parent_models
            precision_models = resolved_parent_models(card, precision, contract["mass_gev"],
                epsilon=epsilon,model_directory=directory)
            for pdg, model in precision_models.items():
                same_resolved_meson_model(contract["resolved_parent_models"].get(str(pdg)), model)
                # The generator's positive table defines its exact rate and
                # sampling distribution. After the independent current check,
                # audit the ledger with that native rate, not a libm-dependent
                # reevaluation of the current in this export runtime.
                from dark_photon_meson_source import exclusive_channels
                from dark_photon_neutral_vector_dipion import register_native_rate_authority
                stored_channels=exclusive_channels(contract['resolved_parent_models'][str(pdg)])
                for stored_channel,resolved_channel in zip(stored_channels,exclusive_channels(model)):
                    if resolved_channel.get('decay_type')=='neutral-vector-dipion':
                        authority=stored_channel['numerical_cdf_rate_authority']
                        if authority.get('cdf_table_payload') is not None:
                            register_native_rate_authority(resolved_channel,contract['mass_gev'],authority)
        for support in (() if unified else precision["parents"]):
            pdg = support["pdg_id"]
            if pdg not in parents or pdg in precision_models:
                raise ValueError("Native parent mass support has changed or repeated species")
            lower = positive(support["mass_min_gev"], "native minimum parent mass")
            upper = positive(support["mass_max_gev"], "native maximum parent mass")
            if upper < lower:
                raise ValueError("Native parent mass support is reversed")
            parent = parents[pdg]
            opened = upper > contract["mass_gev"]
            precision_models[pdg] = dict(mass_min_gev=lower, mass_max_gev=upper,
                br_gamma_gamma=parent["br_gamma_gamma"],
                form_factor_abs2=transition_form_factor_abs2(contract["mass_gev"], parent["transition_form_factor"]) if opened else 1.,
                relative_model_uncertainty=model_precision_budget(parent, contract["mass_gev"]) if opened else 0.)
            stored_model = contract["resolved_parent_models"][str(pdg)]
            for key, value in precision_models[pdg].items():
                same_number(stored_model[key], value, "resolved parent " + key)
        if set(precision_models) != set(parents):
            raise ValueError("Native precision support does not cover every parent species")
        if unified:
            from dark_photon_meson_source import coefficient_bound
            same_number(bound, coefficient_bound(precision['ecm_gev'], branching,
                        precision_models, contract['mass_gev']), 'analytic exclusive-channel bound')
    calls = successes = failures = zeroes = 0
    slots = []
    totals = {name: [0., 0.] for name in ("coefficient_sum", "coefficient_sum_squared",
              "physical_probability_sum", "physical_probability_sum_squared")}
    if precision is not None:
        totals["coefficient_precision_difference_sum"] = [0., 0.]
    if deferred_precision:
        if precision is None:raise ValueError('Deferred full-source precision requires pinned native checks')
        totals['conservative_native_precision_measure_sum']=[0.,0.]
    deferred_parent_count=0
    maximum_precision_shift = 0.
    precision_difference_roundoff=0.

    def accumulate(name, value):
        # Compensated sums keep full-source ledgers bounded in memory.
        total, lost = totals[name]
        adjusted = value - lost
        updated = total + adjusted
        totals[name] = [updated, (updated - total) - adjusted]

    # Stream the complete ledger without keeping all collisions in memory.
    with open_meson_ledger(ledger_path) as source:
        for line in source:
            row = json.loads(line)
            calls += 1
            if row.get("schema") != "shift-meson-source-trial-v1" or type(row.get("trial")) is not int or row["trial"] != calls:
                raise ValueError("Meson source trial identities are missing or duplicated")
            if type(row.get("generator_success")) is not bool or type(row.get("accepted")) is not bool:
                raise ValueError("Meson source trial flags must be explicit booleans")
            coefficient = nonnegative(row["coefficient_sum"], "collision coefficient")
            probability = nonnegative(row["physical_probability"], "collision physical probability")
            acceptance = nonnegative(row["acceptance_probability"], "conditional acceptance probability")
            if not row["generator_success"]:
                failures += 1
                if (row.get("native_weight") is not None or row["parents"] or coefficient or probability or acceptance
                        or row["accepted"] or row.get("parent_index") is not None or row.get("accepted_slot") is not None):
                    raise ValueError("Failed generator call was counted as a source collision")
                continue
            successes += 1
            if row["native_weight"] != 1. or type(row["native_weight"]) is bool:
                raise ValueError("Meson source requires unit native collision weights")
            identities, weights = set(), {}
            coherent_replacements=[]
            checked_parents = {}
            if precision is not None:
                checked_parents = {parent["index"]: parent for parent in resolve_parent_kinematics(
                    row["parents"], precision["mass_tolerance"], contract["mass_gev"], precision_models, branching,
                    native_precision_policy=precision_policy)}
            if deferred_precision:
                measure,exceptions=deferred_native_precision_measure(checked_parents.values(),branching,precision_policy)
                same_number(row['conservative_native_precision_measure_bound'],measure,'per-collision conservative native measure')
                if (type(row.get('exceptional_native_precision_parent_count')) is not int or
                        row['exceptional_native_precision_parent_count']!=exceptions):
                    raise ValueError('Per-collision exceptional native-parent count changed')
                accumulate('conservative_native_precision_measure_sum',measure)
                deferred_parent_count+=exceptions
            for parent in row["parents"]:
                identity, pdg = parent["index"], parent["pdg_id"]
                if type(identity) is not int or identity <= 0 or identity in identities or pdg not in parents:
                    raise ValueError("Meson parent identities are duplicated or unsupported")
                identities.add(identity)
                mass = positive(parent["mass_gev"], "generated parent mass")
                p4 = parent["p4"]
                if len(p4) != 4 or not all(type(value) in (int, float) and math.isfinite(value) for value in p4) or p4[0] <= 0:
                    raise ValueError("Invalid source parent four-vector")
                invariant = p4[0]**2 - math.fsum(value**2 for value in p4[1:])
                if precision is not None:
                    checked_parent = checked_parents[identity]
                    same_number(parent["native_mass_gev"], mass, "native generated mass")
                    model = precision_models[pdg]
                    if not model["mass_min_gev"] <= mass <= model["mass_max_gev"]:
                        raise ValueError("Native generated mass violates its frozen support")
                    from dark_photon_meson_source import parent_branching_coefficient
                    nominal_coefficient=parent_branching_coefficient(mass,contract['mass_gev'],model,branching)
                    roundoff=native_precision_roundoff(parent,checked_parent,nominal_coefficient)
                    for key in ("kinematic_mass_gev", "native_mass_shell_deviation",
                                "coefficient_relative_precision_shift", "coefficient_difference_from_kinematic_mass"):
                        same_signed_number(parent[key], checked_parent[key], key,
                            absolute_roundoff=roundoff.get(key,0.))
                    precision_difference_roundoff=math.fsum((precision_difference_roundoff,
                        roundoff['coefficient_difference_from_kinematic_mass']))
                    same_native_parent_precision_report(parent.get('native_parent_precision_report'),
                        checked_parent.get('native_parent_precision_report'),deferred_policy=deferred_precision)
                    for key in ('kaon_source_count_authority','leptonic_kinematic_precision','native_channel_precision_reports'):
                        if key in parent or key in checked_parent:
                            same_resolved_meson_model(parent.get(key),checked_parent.get(key))
                    shift = parent["coefficient_relative_precision_shift"]
                    maximum_precision_shift = max(maximum_precision_shift, abs(shift))
                    accumulate("coefficient_precision_difference_sum", parent["coefficient_difference_from_kinematic_mass"])
                elif not math.isclose(invariant, mass**2, rel_tol=1e-6, abs_tol=1e-8):
                    raise ValueError("Source parent four-vector and mass disagree")
                if unified:
                    if precision is None:
                        raise ValueError("Unified meson sources require pinned native precision support")
                    from dark_photon_meson_source import parent_branching_coefficient
                    owned_weights=[]
                    for channel in exclusive_channels(precision_models[pdg]):
                        owner=radiative_cascade_owner(parent,channel,precision_models,row['parents'])
                        if owner is not None:
                            coherent_replacements.append(dict(parent_index=identity,channel=channel['channel'],owner=owner))
                        else:
                            owned_weights.append(parent_branching_coefficient(mass,contract['mass_gev'],channel,branching))
                    weights[identity]=math.fsum(owned_weights)
                    continue
                if mass <= contract["mass_gev"]:
                    weights[identity] = 0.
                    continue
                model = parents[pdg]
                form_factor = transition_form_factor_abs2(contract["mass_gev"], model["transition_form_factor"])
                weights[identity] = radiative_branching(mass, contract["mass_gev"], 1e-3,
                    model["br_gamma_gamma"], math.sqrt(form_factor)) / 1e-6 * branching
            if row.get('coherently_replaced_cascades',[])!=coherent_replacements:
                raise ValueError('Coherent radiative-cascade ownership differs from its native source inventory')
            expected = math.fsum(weights.values())
            same_number(coefficient, expected, "per-collision parent branching sum")
            same_number(probability, epsilon**2 * coefficient, "epsilon-squared probability")
            same_number(acceptance, coefficient / bound, "conditional source acceptance")
            if acceptance > 1. + 1e-12 or probability > 1.:
                raise ValueError("Meson collision probability exceeds its physical/analytic bound")
            if expected == 0:
                zeroes += 1
            if row["accepted"]:
                slot = row.get("accepted_slot")
                if type(slot) is not int or slot != len(slots) + 1 or weights.get(row.get("parent_index"), 0.) <= 0:
                    raise ValueError("Accepted meson signal lacks a unique physical parent/slot")
                if unified:
                    from dark_photon_meson_source import exclusive_channels
                    parent, = [item for item in row['parents'] if item['index'] == row['parent_index']]
                    channels = exclusive_channels(precision_models[parent['pdg_id']])
                    if len(channels) > 1 or row.get('selected_channel'):
                        chosen = [item for item in channels if item.get('channel') == row.get('selected_channel')]
                        if len(chosen) != 1 or parent_branching_coefficient(parent['mass_gev'],
                                contract['mass_gev'], chosen[0], branching) <= 0:
                            raise ValueError('Accepted meson signal lacks a unique open exclusive channel')
                        if radiative_cascade_owner(parent,chosen[0],precision_models,row['parents']) is not None:
                            raise ValueError('A coherently replaced vector cascade cannot own a second signal')
                slots.append(slot)
            elif row.get("accepted_slot") is not None or row.get("parent_index") is not None:
                raise ValueError("Rejected source collision owns an emitted signal")
            accumulate("coefficient_sum", coefficient)
            accumulate("coefficient_sum_squared", coefficient**2)
            accumulate("physical_probability_sum", probability)
            accumulate("physical_probability_sum_squared", probability**2)
    if successes != count:
        raise ValueError("Meson source ledger does not cover its full fixed exposure")
    expected = dict(source_calls=calls, source_collisions=successes, generator_failures=failures,
        zero_eligible_parent_collisions=zeroes, sum_native_weights=float(successes),
        sum_native_weights_squared=float(successes),
        **{name: value[0] for name, value in totals.items()},
        accepted_signal_events=len(slots))
    for name, value in expected.items():
        if type(value) is int and type(exposure[name]) is not int:
            raise ValueError("Meson source counter must be an integer: " + name)
        if name == "coefficient_precision_difference_sum":
            same_signed_number(exposure[name], value, name,absolute_roundoff=precision_difference_roundoff)
        else:
            same_number(exposure[name], value, name)
    if deferred_precision:
        summary=full_source_native_precision_summary(precision_policy,expected['coefficient_sum'],
            expected['conservative_native_precision_measure_sum'],deferred_parent_count)
        if not summary['complete']:raise ValueError('Complete source native-precision bound exceeds0.1%')
        stored=exposure.get('full_source_native_precision')
        if not isinstance(stored,dict) or set(stored)!=set(summary):
            raise ValueError('Full-source native-precision completion proof is missing or changed')
        for key,value in summary.items():
            if key in ('schema','complete','exceptional_parent_count'):
                if type(stored[key]) is not type(value) or stored[key]!=value:
                    raise ValueError('Full-source native-precision completion proof changed: '+key)
            else:same_number(stored[key],value,'full-source native-precision '+key)
    if precision is not None:
        same_signed_number(exposure["maximum_coefficient_relative_precision_shift"], maximum_precision_shift,
            "maximum parent precision shift",absolute_roundoff=32*math.ulp(1.))
        same_signed_number(exposure["relative_rate_precision_shift"],
            expected["coefficient_precision_difference_sum"]/expected["coefficient_sum"], "full-source relative rate precision shift",
            absolute_roundoff=precision_difference_roundoff/expected['coefficient_sum'])
    if len(slots) != contract["requested_events"] or not slots:
        raise ValueError("Meson output count differs from its accepted source slots")
    sigma = positive(exposure["sigma_source_mb"], "unfiltered source cross section in mb")
    error = nonnegative(exposure["sigma_source_error_mb"], "source cross-section error in mb")
    mean = expected["physical_probability_sum"] / count
    residual = expected["physical_probability_sum_squared"] - expected["physical_probability_sum"]**2 / count
    tolerance = 1e-12 * expected["physical_probability_sum_squared"]
    if residual < -tolerance:
        raise ValueError("Meson source variance is negative")
    variance = max(0., residual) / (count * (count - 1))
    source_error = error * 1e9 * mean
    collision_error = sigma * 1e9 * math.sqrt(variance)
    rate = dict(value_pb=sigma * 1e9 * mean, error_pb=math.hypot(source_error, collision_error))
    return exposure, rate, dict(source_cross_section_error_pb=source_error,
        collision_probability_error_pb=collision_error, mean_collision_probability=mean,
        variance_of_mean_collision_probability=variance,
        error_combination="Source cross-section and collision-probability statistical errors in quadrature; correlation not evaluated")


def meson_rate_record(contract, audit, *, point, campaign, source_hashes, gen_path, gen_sha256, source_directory):
    if source_directory is None:
        raise ValueError("Meson rate retention requires its full frozen source directory")
    exposure, rate, uncertainty = meson_source_exposure(contract, source_directory)
    events = audit["events"]
    if (contract.get("schema") != "shift-dark-photon-gen-v1" or contract.get("decay_mode") != "mumu"
            or contract.get("filtering") != "none" or audit.get("runtime_validated") is not True):
        raise ValueError("Require a completed, unfiltered meson signal runtime audit")
    if type(events) is not int or events <= 0 or events != contract["requested_events"]:
        raise ValueError("Full meson GEN output count differs from its contract")
    expected_ids = [[contract["run_number"], 1, slot] for slot in range(1, events + 1)]
    if audit["event_ids"] != expected_ids or audit["sum_weights"] != events or audit["sum_weights_squared"] != events:
        raise ValueError("Meson output identities or unit-weight denominators differ")
    ledger = audit["proposal_ledger"]
    count = exposure["source_collisions"]
    if (ledger["n_accepted"] != count or ledger["n_tried"] != exposure["native_n_tried"]
            or ledger["n_accepted"] != exposure["native_n_accepted"]
            or not ledger["n_tried"] >= ledger["n_selected"] >= count):
        raise ValueError("Native Pythia source counters were replaced by signal output counts")
    # Printed Pythia statistics have fewer significant figures than sigmaGen.
    if not math.isclose(ledger["printed_cross_section_mb"], exposure["sigma_source_mb"], rel_tol=1e-3):
        raise ValueError("Native source cross section differs from its printed mb diagnostic")
    width, branching = contract["physical_width_table"], dimuon_branching(contract["physical_width_table"])
    identity_digest = hashlib.sha256(json.dumps(audit["event_ids"], separators=(",", ":")).encode()).hexdigest()
    return dict(schema=SCHEMA, point=point, campaign=campaign,
        mass_gev=contract["mass_gev"], epsilon=contract["epsilon"], sampling_epsilon=contract["epsilon"],
        proper_ctau_mm=width["ctau_mm"], physical_br_mumu=branching,
        corrections=dict(common_amplitude=contract["epsilon"]**2, branching_ratio=1.,
            combined=uncertainty["mean_collision_probability"], applications=1,
            parent_branching_and_dimuon_branching_in_source_coefficient=True,
            extra_decay_branching_fraction_applied=False, extra_detector_or_generator_filter_efficiency_applied=False),
        native_source_cross_sections_pb=[dict(value_pb=exposure["sigma_source_mb"]*1e9,
            error_pb=exposure["sigma_source_error_mb"]*1e9)],
        physical_selected_cross_sections_pb=[rate],
        physical_inclusive_cross_sections_pb=[dict(value_pb=rate["value_pb"]/branching, error_pb=rate["error_pb"]/branching)],
        inclusive_rate_derivation="Single-A-prime exclusive meson rate before the forced dimuon channel; selected rate divided by physical BR_mumu once",
        cross_section_authority="Full-precision unfiltered Pythia sigmaGen [mb] times the fixed all-collision mean physical radiative probability; converted to pb",
        cross_section_convention="sigma_source_mb * 1e9 * sum_collision_probability / fixed_source_collisions; probability includes radiative and dimuon BRs once",
        source_exposure=exposure, source_exposure_sha256=contract["source_exposure_sha256"],
        source_ledger_sha256=contract["source_ledger_sha256"], rate_uncertainty=uncertainty,
        combination_rule="Independent identical meson-source estimates require fixed source-collision exposure; never use signal N or Pythia n_tried weights",
        full_gen=dict(accepted_events=events, sum_weights=float(events), sum_weights_squared=float(events),
            event_ids_sha256=identity_digest, event_ids_file="metadata/GEN/validation.json",
            n_tried=ledger["n_tried"], n_selected=ledger["n_selected"], n_accepted=ledger["n_accepted"],
            source_collisions=count, full_gen_denominator=audit["full_gen_denominator"],
            interpretation="accepted_events counts conditional unit-weight signal outputs; native counters count the unfiltered parent source; rate denominator is fixed source_collisions"),
        proposal_ledger=ledger, physical_width_authority=width, meson_model_card=contract["meson_model_card"],
        meson_model_card_sha256=contract["meson_model_card_sha256"], mechanism=contract["production_mechanism"],
        exclusive_channels=contract.get("exclusive_channels", ["eta->gamma Aprime", "eta-prime->gamma Aprime"]),
        model=contract["model"], generated_mass_support_gev=contract["generated_mass_support_gev"],
        beam_energy_GeV=contract["beam_energy_GeV"], source_model=contract["source_model"],
        seed=contract["seed"], run_number=contract["run_number"], rate_scope=audit["rate_scope"],
        runtime_validated=True, physics_valid=False, normalization_ready=False, production_rate_validated=False,
        low_mass_mechanisms_included=True, prompt_interference_included=False,
        gen_path=gen_path, gen_sha256=gen_sha256, source_sha256=source_hashes, exporter_sha256=sha256(__file__))


def rate_record(contract, audit, *, point, campaign, source_hashes, gen_path, gen_sha256, source_directory=None):
    """Keep proposal and physical dimuon rates distinct; correct exactly once."""
    if contract.get("production_mechanism") in ("eta-etaprime-radiative", "meson-radiative"):
        return meson_rate_record(contract, audit, point=point, campaign=campaign,
            source_hashes=source_hashes, gen_path=gen_path, gen_sha256=gen_sha256, source_directory=source_directory)
    if contract.get("schema") != "shift-dark-photon-gen-v1" or contract.get("decay_mode") != "mumu":
        raise ValueError("Exporter currently requires the audited dimuon GEN contract")
    if audit.get("runtime_validated") is not True or contract.get("filtering") != "none":
        raise ValueError("Require an unfiltered, completed GEN runtime audit")
    epsilon = positive(abs(contract["epsilon"]), "physical epsilon")
    scale = positive(contract["sampling_amplitude_scale"], "proposal amplitude scale")
    if scale < 1:
        raise ValueError("Proposal amplitude cannot be smaller than the physical amplitude")
    physical_br = dimuon_branching(contract["width_authority"])
    native_br = dimuon_branching(contract["sampling_width_authority"])
    amplitude = 1 / scale**2
    # Spectral-width adapters state their BR replacement explicitly. Native
    # common-amplitude pilots have the same BR, apart from roundoff.
    if "cross_section_correction_components" in contract:
        components = contract["cross_section_correction_components"]
        branching = physical_br / native_br
        for name, expected in [("common_amplitude", amplitude), ("branching_ratio", branching)]:
            if not math.isclose(positive(components[name], name), expected, rel_tol=1e-10):
                raise ValueError("Cross-section correction component changed: " + name)
    else:
        if not math.isclose(physical_br, native_br, rel_tol=1e-6):
            raise ValueError("Different physical/native branching fractions require an explicit correction")
        branching = 1.
    combined = amplitude * branching
    if not math.isclose(positive(contract["production_rate_correction"], "rate correction"), combined, rel_tol=1e-10):
        raise ValueError("Physical rate correction does not match its amplitude/BR factors")
    native = validate_rates(audit["native_cross_sections"])
    physical = validate_rates(audit["target_cross_sections"])
    if len(native) != len(physical):
        raise ValueError("Native and physical GEN run counts differ")
    for proposal, target in zip(native, physical):
        for key in ("value_pb", "error_pb"):
            if not math.isclose(proposal[key]*combined, target[key], rel_tol=1e-10, abs_tol=0.):
                raise ValueError("Physical GEN run rate does not apply the correction exactly once")
    events = audit["events"]
    if type(events) is not int or events <= 0 or events != contract["requested_events"]:
        raise ValueError("Full accepted GEN denominator differs from its contract")
    if len(audit["event_ids"]) != events or len(set(map(tuple, audit["event_ids"]))) != events:
        raise ValueError("Full GEN identity ledger is incomplete or duplicated")
    sumw = positive(audit["sum_weights"], "full GEN sum of weights")
    sumw2 = positive(audit["sum_weights_squared"], "full GEN sum of squared weights")
    if sumw != events or sumw2 != events:
        raise ValueError("Native dark-photon GEN requires unit weights for every accepted event")
    ledger = audit["proposal_ledger"]
    if ledger["n_accepted"] != events or not ledger["n_tried"] >= ledger["n_selected"] >= events:
        raise ValueError("Pythia trial ledger differs from the full GEN denominator")
    printed_pb = positive(ledger["printed_cross_section_mb"], "printed generator rate") * 1e9
    # Four-significant-digit printed statistics are diagnostic, never the
    # rate authority. This still catches mb/pb or proposal/target mixups.
    if len(native) == 1 and not math.isclose(native[0]["value_pb"], printed_pb, rel_tol=1e-3):
        raise ValueError("Native GenRunInfo rate differs from the printed mb-to-pb diagnostic")
    identity_digest = hashlib.sha256(json.dumps(audit["event_ids"], separators=(",", ":")).encode()).hexdigest()
    return dict(schema=SCHEMA, point=point, campaign=campaign,
        mass_gev=contract["mass_gev"], epsilon=contract["epsilon"],
        sampling_epsilon=epsilon*scale, proper_ctau_mm=contract["width_authority"]["ctau_mm"],
        physical_br_mumu=physical_br, native_proposal_br_mumu=native_br,
        corrections=dict(common_amplitude=amplitude, branching_ratio=branching, combined=combined,
                         applications=1, extra_decay_branching_fraction_applied=False,
                         extra_detector_or_generator_filter_efficiency_applied=False),
        native_selected_cross_sections_pb=native, physical_selected_cross_sections_pb=physical,
        physical_inclusive_cross_sections_pb=[dict(value_pb=row["value_pb"]/physical_br,
            error_pb=row["error_pb"]/physical_br) for row in physical],
        inclusive_rate_derivation="Selected physical dimuon rate divided by physical BR_mumu; same pure-DY model and generated mass support",
        cross_section_authority="GEN Runs: generator/GenRunInfoProduct/internalXSec; units pb",
        cross_section_convention=contract["cross_section_convention"],
        full_gen=dict(accepted_events=events, sum_weights=sumw, sum_weights_squared=sumw2,
            event_ids_sha256=identity_digest, event_ids_file="metadata/GEN/validation.json",
            n_tried=ledger["n_tried"], n_selected=ledger["n_selected"], n_accepted=ledger["n_accepted"],
            full_gen_denominator=audit["full_gen_denominator"],
            interpretation="Pythia internal trials are not proton collisions or effective luminosity"),
        proposal_ledger=ledger, physical_width_authority=contract["width_authority"],
        native_proposal_width_authority=contract["sampling_width_authority"],
        mechanism=contract["mechanism"], model=contract["model"],
        generated_mass_support_gev=contract["generated_mass_support_gev"],
        beam_energy_GeV=contract["beam_energy_GeV"], source_model=contract["source_model"],
        seed=contract["seed"], run_number=contract["run_number"],
        rate_scope=audit["rate_scope"], runtime_validated=True, physics_valid=False,
        normalization_ready=False, production_rate_validated=False,
        low_mass_mechanisms_included=contract["low_mass_mechanisms_included"],
        prompt_interference_included=contract["prompt_interference_included"],
        gen_path=gen_path, gen_sha256=gen_sha256, source_sha256=source_hashes,
        exporter_sha256=sha256(__file__))


def input_files(directory, manifest):
    """Retain source/config/log authorities and spectral inputs; no ROOT copies."""
    directory = Path(directory)
    names = {"contract.json", "manifest.json", "validation.json", "cmsRun.log", "resolved_cfg.py", "audit.log"}
    if manifest.get('worker_local_runtime') is not None:
        runtime = directory / 'runtime_provenance.json'
        if sha256(runtime) != manifest.get('runtime_provenance_sha256'):
            raise ValueError('Worker-local runtime provenance changed')
        names.add('runtime_provenance.json')
    if manifest.get('native_source_configuration_sha256') is not None:
        if sha256(directory/'source_configuration.json')!=manifest['native_source_configuration_sha256']:
            raise ValueError('Initialized native source configuration changed')
        names.add('source_configuration.json')
    contract=read(directory/'contract.json')
    if contract.get("production_mechanism") in ("eta-etaprime-radiative", "meson-radiative"):
        names.update({"source_exposure.json", "meson_model.json"})
        names.update(meson_physics_input_files(directory,contract))
        if (directory / "meson_source_ledger.jsonl").is_file():
            names.add("meson_source_ledger.jsonl")
        else:
            names.add("meson_ledger_publication.json")
    for name, expected in manifest.get("source_sha256", {}).items():
        path = directory / name
        if not path.is_file() or sha256(path) != expected:
            raise ValueError("Frozen generator source changed: " + str(path))
        names.add(name)
    for relative in ("audit_revisions", "width_inputs"):
        for path in (directory / relative).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                names.add(str(path.relative_to(directory)))
    return sorted(names)


def publish_file(source, destination):
    """Publish a small sidecar on mounted EOS and hash its readback."""
    source, destination = Path(source), Path(destination)
    if not str(destination).startswith("/eos/user/") or ".." in destination.parts:
        raise ValueError("Publication requires a canonical /eos/user destination")
    expected = sha256(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256(destination) != expected:
            raise ValueError("Refuse to overwrite a different published sidecar: " + str(destination))
    else:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
    actual = sha256(destination)
    if actual != expected or destination.stat().st_size != source.stat().st_size:
        raise ValueError("Published sidecar failed SHA-256/size readback: " + str(destination))
    return dict(path=str(destination), bytes=destination.stat().st_size,
                sha256=expected, readback_sha256=actual)


def publish_file_rpc(source, destination):
    """Publish without an EOS mount, refusing different bytes at an existing path."""
    source, destination = Path(source), Path(destination)
    if not str(destination).startswith("/eos/user/") or ".." in destination.parts:
        raise ValueError("Publication requires a canonical /eos/user destination")
    host = "root://eosuser.cern.ch"
    environment = {key: value for key, value in os.environ.items() if key not in
                   ("PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH", "LD_PRELOAD", "ROOTSYS")}
    environment["PATH"] = "/usr/bin:/bin"

    def command(argv, *, check=True):
        return subprocess.run(list(map(str, argv)), env=environment, check=check,
                              capture_output=True, text=True, timeout=300)

    expected, size = sha256(source), source.stat().st_size
    state = command(["/usr/bin/xrdfs", host, "stat", destination], check=False)
    if state.returncode != 0:
        if "[3011]" not in state.stderr and "No such file" not in state.stderr:
            raise RuntimeError("Unknown EOS sidecar state: " + state.stderr)
        command(["/usr/bin/xrdfs", host, "mkdir", "-p", destination.parent])
        command(["/usr/bin/xrdcp", "--silent", "--posc", "--cksum", "adler32",
                 source, host + "/" + str(destination)])
    with tempfile.TemporaryDirectory(prefix="dp-rate-readback-") as temporary:
        readback = Path(temporary) / "sidecar"
        command(["/usr/bin/xrdcp", "--silent", "--cksum", "adler32",
                 host + "/" + str(destination), readback])
        actual = sha256(readback)
        if actual != expected or readback.stat().st_size != size:
            raise ValueError("Refuse different EOS sidecar bytes: " + str(destination))
    return dict(path=str(destination), bytes=size, sha256=expected, readback_sha256=actual)


def export_plan(plan_path, output, *, publish=False, eos_rpc=False):
    plan_path, output = Path(plan_path).resolve(), Path(output).resolve()
    plan = read(plan_path)
    campaign = plan["campaign"]
    if not SAFE_NAME.fullmatch(campaign):
        raise ValueError("Unsafe production campaign name")
    inventory_path = plan_path.with_name("gen_inventory.json")
    inventory = read(inventory_path)
    if inventory.get("complete") is not True:
        raise ValueError("Require the completed full GEN inventory")
    publication_path = plan_path.with_name("gen_publication.json")
    publication = read(publication_path) if publication_path.exists() else None
    published = {row["point"]: row for row in publication["inputs"]} if publication else {}
    records = []
    publisher = publish_file_rpc if eos_rpc else publish_file
    for row in plan["points"]:
        name = row["point"]
        if not SAFE_NAME.fullmatch(name):
            raise ValueError("Unsafe signal point name")
        gen = Path(row["gen_directory"])
        parent = inventory["points"][name]
        contract, audit, manifest = [read(gen / filename) for filename in ("contract.json", "validation.json", "manifest.json")]
        source_hashes = {filename: sha256(gen / filename) for filename in ("contract.json", "validation.json", "manifest.json", "cmsRun.log")}
        for filename, key in [("contract.json", "contract_sha256"), ("validation.json", "validation_sha256"), ("manifest.json", "manifest_sha256")]:
            if parent[key] != source_hashes[filename]:
                raise ValueError("Pinned production GEN metadata changed: " + name + "/" + filename)
        if (audit["event_ids"] != parent["event_ids"] or audit["sum_weights"] != parent["native_sumw"] or
                audit["sum_weights_squared"] != parent["native_sumw2"] or manifest["output_sha256"] != parent["gen_sha256"]):
            raise ValueError("Production GEN population differs from its retained audit")
        meson = contract.get("production_mechanism") in ("eta-etaprime-radiative", "meson-radiative")
        expected_native = contract["source_exposure"]["source_collisions"] if meson else audit["events"]
        fresh_ledger = read_pythia_generation_ledger(gen / "cmsRun.log", expected_accepted=expected_native)
        # Scratch-only workers relocate the unchanged log before handoff.
        ledger_matches = same_ledger_content(fresh_ledger, audit["proposal_ledger"])
        if not ledger_matches:
            raise ValueError("Generator log differs from the retained trial ledger")
        if publication:
            proof = published[name]
            if (publication.get("complete") is not True or proof["sha256"] != parent["gen_sha256"] or
                    proof["readback_sha256"] != parent["gen_sha256"] or proof["path"] != parent["gen_path"]):
                raise ValueError("Canonical GEN publication differs from its retained readback")
        elif sha256(parent["gen_path"]) != parent["gen_sha256"]:
            raise ValueError("Retained local GEN differs from its full audit")
        record = rate_record(contract, audit, point=name, campaign=campaign,
            source_hashes=source_hashes, gen_path=parent["gen_path"], gen_sha256=parent["gen_sha256"], source_directory=gen)
        base = Path(row["final_output_base"])
        if base.name != campaign or base.parent.name != "darkphoton_" + name:
            raise ValueError("Point publication directory does not match its campaign")
        stage = output / "points" / campaign / name
        retained = []
        for filename in input_files(gen, manifest):
            source = gen / filename
            local = stage / "metadata" / "GEN" / filename
            if meson and filename == "meson_source_ledger.jsonl":
                local.parent.mkdir(parents=True, exist_ok=True)
                if local.exists():
                    if sha256(local) != sha256(source):
                        raise ValueError("Refuse to overwrite different retained meson source ledger")
                else:
                    with source.open("rb") as incoming, local.open("xb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                if local.stat().st_size != source.stat().st_size or sha256(local) != sha256(source):
                    raise ValueError("Retained meson source ledger failed SHA-256/size readback")
            else:
                retain_bytes(local, source.read_bytes())
            retained.append(dict(local_path=str(local), path=str(base / "metadata" / "GEN" / filename),
                                 bytes=local.stat().st_size, sha256=sha256(local)))
        record["retained_gen_metadata"] = [dict(path=item["path"], bytes=item["bytes"], sha256=item["sha256"]) for item in retained]
        rate_path = stage / "cross_sections.json"
        retain_bytes(rate_path, canonical_bytes(record))
        # The usual two-column rate/error representation is diagnostic for
        # multi-run files: each row remains distinct, never blindly summed.
        text = "# physical selected dimuon cross section [pb], statistical error [pb]\n"
        text += "".join(f"{item['value_pb']:.17g} {item['error_pb']:.17g}\n" for item in record["physical_selected_cross_sections_pb"])
        retain_bytes(stage / "cross_sections.txt", text.encode())
        artifacts = retained + [dict(local_path=str(stage / filename), path=str(base / filename),
            bytes=(stage / filename).stat().st_size, sha256=sha256(stage / filename))
            for filename in ("cross_sections.txt", "cross_sections.json")]
        point_receipt = dict(point=name, campaign=campaign, rate_path=str(base / "cross_sections.json"),
                             record=record, artifacts=artifacts, published=False)
        if publish:
            point_receipt["publication_readbacks"] = [publisher(item["local_path"], item["path"]) for item in artifacts]
            point_receipt["published"] = True
            retain_bytes(stage / "publication_receipt.json", canonical_bytes(point_receipt))
            point_receipt["receipt_publication"] = publisher(stage / "publication_receipt.json", base / "metadata" / "GEN" / "cross_sections_publication.json")
        records.append(point_receipt)
        print(json.dumps(dict(point=name, campaign=campaign, sigma_mumu_pb=record["physical_selected_cross_sections_pb"][0]["value_pb"], published=publish)), flush=True)
    receipt = dict(schema="shift-dark-photon-rate-retention-v1", complete=True, published=publish,
        campaign=campaign, plan_path=str(plan_path), plan_sha256=sha256(plan_path),
        gen_inventory_path=str(inventory_path), gen_inventory_sha256=sha256(inventory_path), points=records,
        source_deployments_unchanged=True)
    retain_bytes(output / (campaign + ("_published" if publish else "_prepared") + ".json"), canonical_bytes(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publish", action="store_true", help="Publish new rate/GEN sidecars with SHA-256 readback")
    parser.add_argument("--eos-rpc", action="store_true", help="Publish with native XRootD rather than the EOS mount")
    args = parser.parse_args()
    if args.eos_rpc and not args.publish:
        parser.error("--eos-rpc requires --publish")
    for path in args.plan:
        export_plan(path, args.output, publish=args.publish, eos_rpc=args.eos_rpc)


if __name__ == "__main__":
    main()
