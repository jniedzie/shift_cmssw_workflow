"""Auditable 2023 GEN pilot settings; no detector or production configuration."""

import math


def process_settings(sample, lower, upper):
    if not all(math.isfinite(x) for x in (lower, upper)) or not 0 <= lower < upper:
        raise ValueError("Require finite 0 <= lower < upper")
    if sample == "qcd":
        if lower <= 0:
            raise ValueError("HardQCD needs a positive pThat cutoff; soft QCD is separate")
        return ["HardQCD:all = on", f"PhaseSpace:pTHatMin = {lower}",
                f"PhaseSpace:pTHatMax = {upper}"]
    if sample == "dy":
        if lower < 1:
            raise ValueError("This perturbative DY pilot requires mHat >= 1 GeV")
        return ["WeakSingleBoson:ffbar2gmZ = on", "WeakZ0:gmZmode = 0",
                # Particle-data default mMin=10 GeV otherwise excludes low-mass DY.
                f"23:mMin = {lower}",
                f"PhaseSpace:mHatMin = {lower}", f"PhaseSpace:mHatMax = {upper}",
                "23:onMode = off", "23:onIfMatch = 13 -13"]
    if sample == "jpsi":
        channels = ["gg2ccbar(3S1)[3S1(1)]g", "gg2ccbar(3S1)[3S1(1)]gm"]
        channels += [f"{initial}2ccbar(3S1)[{state}]{out}"
                     for state in ("3S1(8)", "1S0(8)", "3PJ(8)")
                     for initial, out in (("gg", "g"), ("qg", "q"), ("qqbar", "g"))]
        return [f"Charmonium:{channel} = {{on,off}}" for channel in channels] + [
            f"PhaseSpace:pTHatMin = {lower}", f"PhaseSpace:pTHatMax = {upper}",
            "443:onMode = off", "443:onIfMatch = 13 -13"]
    raise ValueError(f"Unknown sample {sample}")


def beam_settings(energy):
    if not math.isfinite(energy) or energy <= 1:
        raise ValueError("Beam energy must be finite and above proton rest energy")
    # Pythia frameType=2 treats an energy below the mass as a stationary beam.
    # A is at rest; B moves towards negative z. Placement happens in VtxSmeared.
    return ["Beams:frameType = 2", "Beams:idA = 2212", "Beams:idB = 2212",
            "Beams:eA = 0.", f"Beams:eB = {energy}"]


def canonical_settings(settings):
    """Compare CMS tune vectors independent of whitespace and '=' spelling."""
    return [" ".join(item.replace("=", " ").split()) for item in settings]
