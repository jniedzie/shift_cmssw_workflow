#!/usr/bin/env python3
# Code style: AI-code. AI-generated spectral-width dark-photon GEN adapter.
"""Bounded 2--12 GeV DY generation with an explicit spectral width authority.

DarkCast supplies the physical visible width and branching fraction. Native
Pythia supplies an amplified, resolvable DY proposal with its own natural
width. Correct its selected dimuon cross section by the common amplitude
ratio and the physical/native branching-fraction ratio exactly once.

This implements only DY. Meson and proton-bremsstrahlung contributions are
absent, and no physical rate/limit validation is claimed. It never rebuilds,
submits, overwrites an existing output, or relaxes the shared native backend.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from dark_photon_model import ElectroweakInputs, pythia_couplings, validate_native_proposal_support
from fixed_target_generation import beam_settings

SOURCES = (
    "run_dark_photon_spectral_gen.py", "dark_photon_gen_cfg.py", "dark_photon_generation.py",
    "dark_photon_model.py", "dark_photon_pythia.py", "audit_dark_photon_gen.py",
    "pythia_generation_ledger.py", "fixed_target_generation.py",
)
PINNED_DARKCAST_COMMIT = "5a2a53a8984827be06e2939e6c3705f79d180048"
NATIVE_EW = ElectroweakInputs(sin2_theta_w=.2312, m_z_gev=91.1876)


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def validate_point(mass, epsilon, sampling_epsilon, experimental_low_mass_dy=False):
    minimum = 1.4 if experimental_low_mass_dy else 2.
    if not math.isfinite(mass) or not minimum <= mass < 12.:
        raise ValueError("Spectral DY pilot requires mass >=2 GeV, or >=1.4 GeV with the explicit experimental low-mass DY option, and mass <12 GeV")
    if not math.isfinite(epsilon) or not 0 < epsilon <= .01:
        raise ValueError("Physical epsilon must be finite and in (0, 0.01]")
    if not math.isfinite(sampling_epsilon) or not epsilon <= sampling_epsilon <= .01:
        raise ValueError("Require physical epsilon <= sampling epsilon <= 0.01")


def native_proposal_settings(mass, epsilon, sampling_epsilon, experimental_low_mass_dy=False):
    validate_point(mass, epsilon, sampling_epsilon, experimental_low_mass_dy)
    couplings = pythia_couplings(mass, epsilon, NATIVE_EW)
    scale = sampling_epsilon / epsilon
    settings = [
        "NewGaugeBoson:ffbar2gmZZprime = on", "Zprime:gmZmode = 3",
        "Zprime:universality = off", "Zprime:coup2gen4 = off", "Zprime:coup2WW = 0",
        f"StandardModel:sin2thetaW = {NATIVE_EW.sin2_theta_w:.17g}",
        f"23:m0 = {NATIVE_EW.m_z_gev:.17g}",
        f"32:m0 = {mass:.17g}", f"32:mMin = {.99*mass:.17g}", f"32:mMax = {1.01*mass:.17g}",
        f"PhaseSpace:mHatMin = {.99*mass:.17g}", f"PhaseSpace:mHatMax = {1.01*mass:.17g}",
        "13:m0 = 0.1056583755", "13:mayDecay = off",
    ]
    for species, coupling in couplings.items():
        settings.extend([f"Zprime:v{species} = {scale*coupling['vector']:.17g}",
                         f"Zprime:a{species} = {scale*coupling['axial']:.17g}"])
    return settings


def cross_section_correction(epsilon, sampling_epsilon, physical_br, native_br):
    if not all(math.isfinite(value) and value > 0 for value in
               (epsilon, sampling_epsilon, physical_br, native_br)):
        raise ValueError("Cross-section correction requires positive finite couplings and branching fractions")
    if physical_br > 1 or native_br > 1 or epsilon > sampling_epsilon:
        raise ValueError("Invalid branching fraction or amplitude proposal")
    return (epsilon / sampling_epsilon)**2 * physical_br / native_br


def freeze_spectral_inputs(package_directory, receipt_path, output):
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("commit") != PINNED_DARKCAST_COMMIT:
        raise ValueError("Require the pinned DarkCast 2.1 release commit")
    package = package_directory / "darkcast"
    if not (package / "model.py").is_file():
        raise ValueError("DarkCast input path must contain the complete darkcast package")
    copied = output / "width_inputs" / "darkcast"
    shutil.copytree(package, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    files = {}
    for path in copied.rglob("*"):
        if not path.is_file():
            continue
        relative = str(Path("darkcast") / path.relative_to(copied))
        expected = receipt["files"].get(relative)
        actual = digest(path)
        if expected is None or actual != expected:
            raise ValueError("Spectral source differs from its pinned receipt: " + relative)
        files[relative] = actual
    missing = [name for name in receipt["files"] if name.startswith("darkcast/") and name not in files]
    if missing:
        raise ValueError("Incomplete pinned spectral source: " + ", ".join(missing))
    shutil.copy2(receipt_path, output / "width_inputs" / "source_receipt.json")
    return dict(provider="DarkCast 2.1", commit=PINNED_DARKCAST_COMMIT, files_sha256=files,
                source_receipt_sha256=digest(receipt_path),
                references=["https://arxiv.org/abs/1801.04847", "https://gitlab.com/darkcast/releases"])


def physical_width_table(model, mass, epsilon, authority):
    channels = ["e_e", "mu_mu", "tau_tau", "hadrons", "quarks", "neutrinos", "dark"]
    partials = {channel: model.width(channel, mass, epsilon) for channel in channels}
    total = model.width("total", mass, epsilon)
    if not math.isfinite(total) or total <= 0 or not math.isclose(math.fsum(partials.values()), total, rel_tol=1.e-12):
        raise ValueError("Spectral total and complete channel sum differ")
    if any(not math.isfinite(value) or value < 0 for value in partials.values()) or partials["dark"] != 0:
        raise ValueError("Require a finite visible-only spectral width table")
    branching = partials["mu_mu"] / total
    if not math.isclose(branching, model.bfrac("mu_mu", mass), rel_tol=1.e-12):
        raise ValueError("Spectral dimuon branching fraction differs from its partial width")
    return dict(schema="shift-dark-photon-spectral-width-v1", mass_gev=mass, epsilon=epsilon,
                total_width_gev=total, ctau_mm=1.973269804e-13 / total, br_mumu=branching,
                partial_widths_gev=partials, width_scope="DarkCast2.1-visible-photon-like-spectral-pilot",
                spectral_source=authority, physical_width_validated=False, production_rate_validated=False,
                electromagnetic_coupling=model.xfs["e"][1](mass),
                neutral_current_width_included=False, hidden_decay_channels=False)


def build_contract(args, width, native, common, tune):
    native_total = float(native["m_width_gev"])
    if not math.isclose(native_total, math.fsum(c["on_shell_width_gev"] for c in native["channels"]), rel_tol=1.e-10):
        raise ValueError("Natural native proposal total and partial widths differ")
    native_br = sum(c["branching_fraction"] for c in native["channels"] if sorted(c["products"]) == [-13, 13])
    correction = cross_section_correction(args.epsilon, args.sampling_epsilon, width["br_mumu"], native_br)
    support = validate_native_proposal_support(native, .99*args.mass, 1.01*args.mass)
    experimental = getattr(args, 'experimental_low_mass_dy', False)
    commands = native_proposal_settings(args.mass, args.epsilon, args.sampling_epsilon, experimental)
    commands.extend(["32:tauCalc = off", f"32:tau0 = {width['ctau_mm']:.17g}",
                     "32:onMode = off", "32:onIfMatch = 13 -13"])
    return dict(schema="shift-dark-photon-gen-v1", model="PBC-BC1-visible-hypercharge",
        backend="native-DY-proposal-with-external-spectral-physical-width",
        mechanism="pure-on-shell-drell-yan", mass_gev=args.mass, epsilon=args.epsilon,
        generated_mass_support_gev=[.99*args.mass, 1.01*args.mass], pdg_id=32, decay_mode="mumu",
        requested_events=args.events, event_budget_mode="outputAccepted", maximum_framework_events=10*args.events,
        seed=args.seed, run_number=args.run_number or args.seed, beam_energy_GeV=6800.,
        source_z_mm=148000., source_sigma_z_mm=500., source_model="provisional on-axis stationary proton target",
        width_authority=width, process_settings=commands, sampling_width_authority=native,
        sampling_pole_support_audit=support, sampling_method="narrow-width-common-amplitude-sampling",
        sampling_amplitude_scale=args.sampling_epsilon/args.epsilon,
        physical_br_mumu=width["br_mumu"], native_proposal_br_mumu=native_br,
        cross_section_correction_components=dict(common_amplitude=(args.epsilon/args.sampling_epsilon)**2,
                                                branching_ratio=width["br_mumu"]/native_br),
        production_rate_correction=correction,
        lifetime_owner="external complete physical spectral width, independent of native sampling amplitude",
        narrow_width_physics_validated=False, beam_settings=beam_settings(6800.), common=list(common), cp5=list(tune),
        hadron_decay_policy="common residual muon-parent closure; natural BR; absolute 8000/151000 mm cylinder",
        signal_decay_policy="native resonance dimuon decay; full physical exponential; outside-envelope outcomes retained",
        signal_parent_transport="completed parent HepMC status 3; transport daughters from physical vertices",
        cross_section_convention="native selected sigma already includes native BR_mumu; apply amplitude times physical/native BR ratio once",
        timing="proper decay after source placement; nominal Beam-B time shift once on complete graph; canonical shiftEventTime metadata",
        filtering="none", physics_valid=False, normalization_ready=False, low_mass_mechanisms_included=False,
        experimental_low_mass_dy=experimental,
        production_scope="DY-only experimental extension; meson/bremsstrahlung absent" if args.mass<2 else "DY-only spectral pilot",
        prompt_interference_included=False, detector_simulated=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mass", type=float, required=True)
    parser.add_argument("--epsilon", type=float, required=True)
    parser.add_argument("--sampling-epsilon", type=float, default=.01)
    parser.add_argument("--experimental-low-mass-dy", action="store_true",
                        help="Allow 1.4-2 GeV DY-only diagnostics; retain native pole-support checks and provisional physics flags")
    parser.add_argument("--darkcast-path", type=Path, required=True)
    parser.add_argument("--darkcast-source-receipt", type=Path, required=True)
    parser.add_argument("--events", type=int, default=100)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--run-number", type=int)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    validate_point(args.mass, args.epsilon, args.sampling_epsilon, args.experimental_low_mass_dy)
    if not 1 <= args.events <= 10000 or not 1 <= args.seed <= 899999823 or args.timeout <= 0:
        parser.error("Require bounded events, valid seed namespace and positive timeout")
    if args.run_number is not None and not 1 <= args.run_number <= 4294967295:
        parser.error("Invalid run number")
    if not shutil.which("cmsRun") or not os.environ.get("CMSSW_BASE"):
        parser.error("Enter the existing built CMSSW runtime first; no build is performed")
    os.environ["LD_LIBRARY_PATH"] = ":".join(p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":") if "/biglib/" not in p)
    environment = os.environ.copy()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = dict(schema="shift-dark-photon-pilot-run-v1", status="preflight",
                    physics_valid=False, normalization_ready=False)
    path = output / "manifest.json"
    here = Path(__file__).resolve().parent
    try:
        for name in SOURCES:
            shutil.copy2(here / name, output / name)
        manifest["source_sha256"] = {name: digest(output / name) for name in SOURCES}
        base = Path(os.environ["CMSSW_BASE"])
        manifest["cmssw_git_head"] = subprocess.check_output(["git", "-C", str(base / "src"), "rev-parse", "HEAD"], text=True).strip()
        manifest["cmssw_git_status"] = subprocess.check_output(["git", "-C", str(base / "src"), "status", "--porcelain"], text=True)
        manifest["pythia_tool"] = subprocess.check_output(["scram", "tool", "info", "pythia8"], cwd=base / "src", text=True)
        authority = freeze_spectral_inputs(args.darkcast_path.resolve(), args.darkcast_source_receipt.resolve(), output)
        manifest["spectral_width_source"] = authority
        sys.path.insert(0, str(output / "width_inputs"))
        import darkcast
        model = darkcast.Model("dark_photon")
        width = physical_width_table(model, args.mass, args.epsilon, authority)
        from Configuration.Generator.Pythia8CommonSettings_cfi import pythia8CommonSettingsBlock
        from Configuration.Generator.MCTunes2017.PythiaCP5Settings_cfi import pythia8CP5SettingsBlock
        from dark_photon_pythia import _native_inspect
        common = list(pythia8CommonSettingsBlock.pythia8CommonSettings)
        tune = list(pythia8CP5SettingsBlock.pythia8CP5Settings)
        native = _native_inspect(common+tune+native_proposal_settings(args.mass,args.epsilon,args.sampling_epsilon,args.experimental_low_mass_dy)+beam_settings(6800.))
        contract = build_contract(args, width, native, common, tune)
        (output / "contract.json").write_text(json.dumps(contract, indent=2, allow_nan=False)+"\n")
        command = ["cmsRun", str(output / "dark_photon_gen_cfg.py"), "pilotDirectory="+str(output)]
        manifest.update(command=command, status="generating")
        path.write_text(json.dumps(manifest, indent=2)+"\n")
        with (output / "cmsRun.log").open("w") as log:
            subprocess.run(command,cwd=output,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=args.timeout,env=environment)
        with (output / "audit.log").open("w") as log:
            subprocess.run([sys.executable,str(output / "audit_dark_photon_gen.py"),str(output)],
                           cwd=output,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180,env=environment)
        manifest.update(status="GEN runtime audit passed; physics provisional",output_sha256=digest(output / "gen.root"),
                        validation=json.loads((output / "validation.json").read_text()))
    except Exception as error:
        manifest.update(status="failed",error=repr(error))
        raise
    finally:
        path.write_text(json.dumps(manifest,indent=2,allow_nan=False)+"\n")
    print(json.dumps(dict(events=args.events,mass_gev=args.mass,epsilon=args.epsilon,output=str(output),status=manifest["status"])))


if __name__ == "__main__":
    main()
