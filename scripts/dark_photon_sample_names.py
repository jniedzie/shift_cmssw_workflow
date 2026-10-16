#!/usr/bin/env python3
# Code style: AI-code. AI-generated canonical physical DP point naming.
"""Name samples by mass and reference mean lab flight; keep exact metadata."""
import math


def number_token(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Sample name parameters must be finite and positive')
    return format(value, '.9g').replace('.', 'p').replace('e-', 'em').replace('e+', 'ep')


def physical_point_name(mass_gev, mean_lab_flight_m, *, prompt=False):
    mass = number_token(mass_gev)
    if prompt:
        return 'm' + mass + '_prompt'
    flight = float(mean_lab_flight_m)
    if not math.isfinite(flight) or flight <= 0:
        raise ValueError('Mean lab flight must be finite and positive')
    # Keep the familiar anchors exact; other names are concise display values.
    token = str(int(flight)) if flight.is_integer() else format(flight, '.3g')
    token = token.replace('.', 'p').replace('e-', 'em').replace('e+', 'ep')
    lifetime = 'lab_' + token + 'm'
    return 'm' + mass + '_' + lifetime


def point_mean_lab_flight_m(row):
    """Use an explicit anchor or its width-qualified reference boost estimate."""
    original = row.get('original_point', row['point'])
    if original.endswith('_prompt'):
        return None
    for key in ('target_mean_lab_flight_m', 'mean_lab_flight_m',
                'mean_lab_flight_reference_estimate_m', 'reference_mean_lab_flight_m'):
        value = row.get(key)
        if value is not None:
            value = float(value)
            if not math.isfinite(value) or value <= 0:
                raise ValueError('Invalid reference mean lab flight for ' + original)
            return value
    raise ValueError('No width-qualified reference mean lab flight for ' + original)


def set_lab_point_name(row):
    """Update a new plan row without changing its physical parameters or IDs."""
    original = row.setdefault('original_point', row['point'])
    flight = point_mean_lab_flight_m(row)
    row.setdefault('selector_kind', 'epsilon' if '_epsilon_' in original else 'mean_lab_flight')
    row['mean_lab_flight_m'] = flight
    row['reference_mean_lab_flight_m'] = row.get('mean_lab_flight_reference_estimate_m', flight)
    row['point'] = physical_point_name(row['mass_gev'], flight, prompt=original.endswith('_prompt'))
