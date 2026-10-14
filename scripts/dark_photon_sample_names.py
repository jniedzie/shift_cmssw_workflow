#!/usr/bin/env python3
# Code style: AI-code. AI-generated canonical physical DP point naming.
"""Name samples by mass and proper lifetime; retain exact values in metadata."""
import math


def number_token(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Sample name parameters must be finite and positive')
    return format(value, '.9g').replace('.', 'p').replace('e-', 'em').replace('e+', 'ep')


def physical_point_name(mass_gev, proper_ctau_mm, *, prompt=False):
    mass = number_token(mass_gev)
    lifetime = 'prompt' if prompt else 'ctau_' + number_token(proper_ctau_mm) + 'mm'
    return 'm' + mass + '_' + lifetime
