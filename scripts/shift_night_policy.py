# Code style: AI-code. AI-generated scheduling policy for immutable night reconstruction jobs.
"""Decide admissions without selecting events or changing scientific weights."""
from datetime import datetime, timedelta, timezone
import math
import re
from zoneinfo import ZoneInfo

LIVE_STATUSES = (1, 2, 6, 7)
CAPACITY_STEPS = (50, 100, 200, 400, 600, 800, 1000)
NIGHT_HOLD_REASON = 'SHIFT night admission expired before reconstruction started'


def validate_policy(policy):
    """Refuse unbounded or contradictory scheduling settings."""
    required = {'timezone', 'window_start', 'window_end', 'stop_launches_before_end_seconds',
                'worker_ceiling', 'initial_workers', 'lease_seconds', 'poll_seconds',
                'observation_max_age_seconds', 'growth_interval_seconds',
                'growth_minimum_completions', 'growth_max_idle_fraction',
                'minimum_free_bytes', 'minimum_free_files', 'reserved_bytes_per_worker',
                'reserved_files_per_worker', 'maximum_wave_jobs'}
    if required - set(policy):
        raise ValueError('Missing night scheduling settings: ' + ', '.join(sorted(required - set(policy))))
    ZoneInfo(policy['timezone'])
    for key in ('window_start', 'window_end'):
        if not isinstance(policy[key], str) or not re.fullmatch(r'\d\d:\d\d', policy[key]):
            raise ValueError('Night hours must use HH:MM')
        hour, minute = map(int, policy[key].split(':'))
        if hour > 23 or minute > 59:
            raise ValueError('Invalid night hour')
    if policy['window_start'] == policy['window_end']:
        raise ValueError('Night window must not cover a full day')
    integer_positive = ('worker_ceiling', 'initial_workers', 'lease_seconds', 'poll_seconds',
                        'observation_max_age_seconds', 'growth_interval_seconds',
                        'growth_minimum_completions', 'reserved_bytes_per_worker',
                        'reserved_files_per_worker', 'maximum_wave_jobs')
    for key in integer_positive:
        if type(policy[key]) is not int or policy[key] <= 0:
            raise ValueError(key + ' must be a positive integer')
    for key in ('minimum_free_bytes', 'minimum_free_files', 'stop_launches_before_end_seconds'):
        if type(policy[key]) is not int or policy[key] < 0:
            raise ValueError(key + ' must be a nonnegative integer')
    if not 1 <= policy['initial_workers'] <= policy['worker_ceiling'] <= 1000:
        raise ValueError('Night worker limits must be between 1 and 1000')
    if policy['lease_seconds'] < 2 * policy['poll_seconds'] or policy['lease_seconds'] > 600:
        raise ValueError('Admission lease must cover two polls and last at most ten minutes')
    if not 0 <= policy['growth_max_idle_fraction'] <= 1:
        raise ValueError('Invalid idle fraction')
    if policy['maximum_wave_jobs'] > policy['worker_ceiling']:
        raise ValueError('A submission wave cannot exceed the worker ceiling')
    return dict(policy)


def local_boundary(day, clock, zone, *, last=False):
    """Handle the repeated autumn hour explicitly; refuse nonexistent local times."""
    hour, minute = map(int, clock.split(':'))
    naive = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    possibilities = []
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        utc = local.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            possibilities.append(utc.timestamp())
    if not possibilities:
        raise ValueError('Configured night boundary does not exist during the DST change: ' + str(naive))
    return max(possibilities) if last else min(possibilities)


def night_window(now, policy):
    """Return the current or next complete local night and its earlier admission stop."""
    if not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError('Current time must be a finite epoch')
    zone = ZoneInfo(policy['timezone'])
    today = datetime.fromtimestamp(now, zone).date()
    crossing = policy['window_end'] < policy['window_start']
    windows = []
    for offset in (-1, 0, 1, 2):
        start_day = today + timedelta(days=offset)
        end_day = start_day + timedelta(days=1 if crossing else 0)
        start = local_boundary(start_day, policy['window_start'], zone)
        end = local_boundary(end_day, policy['window_end'], zone, last=True)
        stop = end - policy['stop_launches_before_end_seconds']
        if not start < stop <= end:
            raise ValueError('Launch stop leaves no usable night window')
        windows.append({'local_date': str(start_day), 'start_epoch': int(start),
                        'stop_epoch': int(stop), 'end_epoch': int(end),
                        'duration_seconds': int(end - start)})
    current = next((window for window in windows if window['start_epoch'] <= now < window['end_epoch']), None)
    window = current or min((window for window in windows if window['start_epoch'] > now),
                            key=lambda item: item['start_epoch'])
    return {**window, 'inside_night': current is not None,
            'admissions_allowed': window['start_epoch'] <= now < window['stop_epoch'],
            'next_admission_epoch': window['start_epoch'] if current is None else (
                window['start_epoch'] if now < window['stop_epoch'] else next(
                    future['start_epoch'] for future in windows if future['start_epoch'] > now))}


def capacity_decision(previous, policy, *, now, completed_jobs, own_ads,
                      external_workers, observations_verified, healthy):
    """Count reservations and increase capacity only after healthy measured progress."""
    current = int(previous.get('current_capacity', policy['initial_workers']))
    current = min(policy['worker_ceiling'], max(policy['initial_workers'], current))
    changed = previous.get('last_capacity_change_epoch', now)
    baseline = previous.get('completed_at_change', completed_jobs)
    live = [ad for ad in own_ads if ad.get('JobStatus') in LIVE_STATUSES]
    idle = sum(ad.get('JobStatus') == 1 for ad in live)
    idle_fraction = idle / len(live) if live else 0.
    reason = 'waiting_for_completed_outputs'
    if not observations_verified:
        reason = 'scheduler_or_quota_state_unknown'
    elif not healthy:
        reason = 'worker_failure_blocks_new_admissions'
    elif idle_fraction > policy['growth_max_idle_fraction']:
        reason = 'waiting_for_queued_workers_to_start'
    elif current < policy['worker_ceiling'] and (
            now - changed >= policy['growth_interval_seconds'] and
            completed_jobs - baseline >= policy['growth_minimum_completions']):
        current = min(policy['worker_ceiling'], next((value for value in CAPACITY_STEPS if value > current),
                                                    policy['worker_ceiling']))
        changed, baseline, reason = now, completed_jobs, 'healthy_completed_outputs_allow_growth'
    elif current == policy['worker_ceiling']:
        reason = 'at_approved_worker_ceiling'
    available = max(0, current - external_workers - len(live)) if observations_verified and healthy else 0
    return {'current_capacity': current, 'last_capacity_change_epoch': changed,
            'completed_at_change': baseline, 'completed_jobs': completed_jobs,
            'external_workers': external_workers if observations_verified else None,
            'own_live_workers': len(live), 'idle_fraction': idle_fraction,
            'available_new_slots': min(available, policy['maximum_wave_jobs']), 'reason': reason}


def observations_safe(observation, policy, now, *, reserved_workers):
    """A missing, stale or failed observation cannot authorize a launch."""
    if not observation.get('complete') or not observation.get('account_coverage_verified'):
        return False, 'quota or account coverage is unknown'
    for key in ('quota_checked_at_epoch', 'account_checked_at_epoch'):
        value = observation.get(key)
        if (type(value) not in (int, float) or not math.isfinite(value) or
                not 0 <= now - value <= policy['observation_max_age_seconds']):
            return False, 'quota or account observation is stale'
    free_bytes, free_files = observation.get('free_bytes'), observation.get('free_files')
    if type(free_bytes) is not int or type(free_files) is not int:
        return False, 'quota result has invalid counts'
    minimum_bytes = policy['minimum_free_bytes'] + reserved_workers * policy['reserved_bytes_per_worker']
    minimum_files = policy['minimum_free_files'] + reserved_workers * policy['reserved_files_per_worker']
    if free_bytes < minimum_bytes or free_files < minimum_files:
        return False, 'fresh quota is below the full reserved output budget'
    if not isinstance(observation.get('account_workers'), list):
        return False, 'account worker inventory is missing'
    return True, 'fresh quota and account coverage allow admissions'


def admission_valid(ad, now):
    """The same start gate is used by the controller and the worker wrapper."""
    fields = ('ShiftNightAdmissionStartEpoch', 'ShiftNightAdmissionStopEpoch', 'ShiftNightAdmissionLeaseEpoch')
    if any(type(ad.get(field)) is not int for field in fields):
        return False
    start, stop, lease = (ad[field] for field in fields)
    return start <= now < min(stop, lease)
