#!/usr/bin/env python3
"""Fail-closed controls for the unfiltered QCD chain; never selects events."""
import argparse
import math
import os

PROCESS = 'QCD_UnfilteredDecays_FixedTarget_pThat_1to5GeV_13p6TeV'
JPSI_PROCESS = 'Charmonium_Unfiltered_FixedTarget_pThat_1to5GeV_13p6TeV'
MPI_QCD_PROCESS = 'QCD_SoftMpiPartition_FixedTarget_13p6TeV'
MPI_JPSI_PROCESS = 'Charmonium_SoftMpiPartition_FixedTarget_13p6TeV'
MPI_PROCESSES = {
    MPI_QCD_PROCESS: 'qcd',
    MPI_JPSI_PROCESS: 'direct_jpsi',
}
HARD_PROCESSES = {PROCESS, JPSI_PROCESS}
PROCESSES = HARD_PROCESSES | set(MPI_PROCESSES)
HARD_BINS = {(1., 2.), (2., 5.), (5., 10.), (10., 20.), (20., -1.)}
MPI_BINS = {(0., 1.)} | HARD_BINS


def check(env):
    for name in ('WORKFLOW_LOCAL_GENERATOR', 'CLEANUP_PREVIOUS_STEP'):
        if env.get(name, '0') not in ('0', '1'):
            raise ValueError(f'{name} must be 0 or 1')
    lower, upper = env.get('GEN_PTHAT_MIN', ''), env.get('GEN_PTHAT_MAX', '')
    if lower or upper or env.get('PROCESS') in PROCESSES:
        bounds = (float(lower), float(upper))
        process = env.get('PROCESS')
        if process not in PROCESSES:
            raise ValueError('Explicit bounds require an audited production process')
        supported = MPI_BINS if process in MPI_PROCESSES else HARD_BINS
        if bounds not in supported:
            raise ValueError('Unsupported pThat bin for the selected production process')
        if process in MPI_PROCESSES:
            expected_class = MPI_PROCESSES[process]
            if env.get('GEN_EVENT_CLASS') != expected_class:
                raise ValueError(f'{process} requires GEN_EVENT_CLASS={expected_class}')
        elif env.get('GEN_EVENT_CLASS', ''):
            raise ValueError('GEN_EVENT_CLASS is only valid for the MPI-partitioned processes')
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
