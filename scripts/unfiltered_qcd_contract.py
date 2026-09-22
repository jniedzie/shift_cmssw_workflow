#!/usr/bin/env python3
"""Fail-closed controls for the unfiltered QCD chain; never selects events."""
import argparse
import math
import os

PROCESS = 'QCD_UnfilteredDecays_FixedTarget_pThat_1to5GeV_13p6TeV'
JPSI_PROCESS = 'Charmonium_Unfiltered_FixedTarget_pThat_1to5GeV_13p6TeV'
PROCESSES = {PROCESS, JPSI_PROCESS}
BINS = {(1., 2.), (2., 5.), (5., 10.), (10., 20.), (20., -1.)}


def check(env):
    for name in ('WORKFLOW_LOCAL_GENERATOR', 'CLEANUP_PREVIOUS_STEP'):
        if env.get(name, '0') not in ('0', '1'):
            raise ValueError(f'{name} must be 0 or 1')
    lower, upper = env.get('GEN_PTHAT_MIN', ''), env.get('GEN_PTHAT_MAX', '')
    if lower or upper or env.get('PROCESS') in PROCESSES:
        bounds = (float(lower), float(upper))
        if env.get('PROCESS') not in PROCESSES or bounds not in BINS:
            raise ValueError('Explicit bounds require an audited unfiltered process and a supported pThat bin >=1 GeV')
        if any(not math.isfinite(v) for v in bounds):
            raise ValueError('Non-finite bounds')
        for key in ('N_EVENTS', 'N_JOBS'):
            if key in env and (not env[key].isdigit() or int(env[key]) <= 0):
                raise ValueError(f'{key} must be a positive integer')
        if int(env.get('N_JOBS', '1')) > 100000:
            raise ValueError('Too many chunks for the profile seed separation; assign new seed ranges explicitly')
    if env.get('CLEANUP_PREVIOUS_STEP') == '1':
        if env.get('PROCESS') not in PROCESSES:
            raise ValueError('Retirement requires an audited unfiltered process')
        required = dict(PILEUP_MODE='none', TRIGGER_SCENARIO='none',
                        TRIGGER_TIMELINE_MODE='none', STEP4_INPUTS_PER_JOB='1', ENABLE_EXONANOAOD='0')
        for key, value in required.items():
            if env.get(key) != value:
                raise ValueError(f'Validated retirement currently requires {key}={value}')
        if env.get('SHIFT_SIMHIT_REFERENCE_INPUT', ''):
            raise ValueError('Cannot retire shared same-SimHit inputs')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', required=True)
    parser.parse_args()
    check(os.environ)
