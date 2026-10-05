"""Scheduler policy, separate from the immutable scientific campaign plan."""
import math

TIERS = (50, 75, 100, 150, 200, 300)


def decide(previous, health, queue, now, policy):
    """Grow on measured success; freeze or reduce admissions on real pressure.

    Reductions affect future submissions only. Running jobs are never removed.
    Unknown service state never authorizes a capacity increase.
    """
    ceiling = int(policy.get('ceiling', 300))
    tiers = [x for x in TIERS if x <= ceiling]
    current = int(previous.get('target', tiers[0]))
    answer = dict(target=current, changed_at=previous.get('changed_at', now),
                  baseline_completed=previous.get('baseline_completed', 0),
                  stop_admissions=previous.get('stop_admissions', False),
                  reason='waiting for measured healthy progress')
    if not queue.get('verified'):
        return dict(answer, reason='scheduler state unknown; no admission changes')
    if queue.get('held', 0) or queue.get('failed_nodes', 0) or (health and (health.get('failed_receipts') or health.get('integrity_failure'))):
        return dict(answer, stop_admissions=True, reason='verified failure evidence; hold managers for review')
    if not health or not health.get('healthy') or now-health.get('checked_at_epoch', 0) > policy.get('audit_max_age', 1500):
        return dict(answer, reason='fresh successful EOS audit required')
    reservation = policy.get('reservation_bytes',
        ceiling*policy.get('peak_bytes_per_worker', 250000000)+policy.get('minimum_free_bytes', 50000000000))
    if (health['quota']['free_bytes'] < reservation or
            health['quota']['free_files'] < policy.get('minimum_free_files', 20000)):
        return dict(answer, stop_admissions=True,
                    reason='EOS headroom below full approved reservation; hold managers for review')
    completed = int(health.get('completed_chunks', 0))
    if previous.get('stop_admissions'):
        return dict(answer, reason='failure or quota stop requires reviewed recovery')
    if queue.get('idle', 0) > max(10, current//3):
        return dict(answer, reason='waiting for CERN to grant queued capacity')
    if now-answer['changed_at'] < policy.get('growth_interval', 1200):
        return answer
    if completed-answer['baseline_completed'] < policy.get('minimum_new_completions', 10):
        return answer
    if queue.get('running', 0) < max(1, math.floor(current*0.7)):
        return dict(answer, reason='allocation does not yet support further growth')
    next_tier = next((x for x in tiers if x > current), current)
    if next_tier != current:
        return dict(answer, target=next_tier, changed_at=now,
                    baseline_completed=completed, reason='healthy publication, quota and allocation permit gradual growth')
    return dict(answer, reason='at approved scheduling ceiling')


def allocations(target, managers):
    """Share the project budget; unused high-bin capacity returns to other work."""
    active = [m for m in managers if m.get('active') and m.get('ready', True)]
    if not active:
        return {}
    budget = int(target)
    if budget < len(active):
        raise ValueError('Budget cannot give each active manager a positive limit')
    if len(active) == 1:
        manager = active[0]
        return {manager['name']: min(budget, manager.get('ceiling', 300))}
    weights = sum(m.get('weight', 1) for m in active)
    result = {m['name']: max(1, min(m.get('ceiling', 300), int(budget*m.get('weight', 1)/weights))) for m in active}
    spare = budget-sum(result.values())
    while spare > 0:
        changed = False
        for manager in active:
            key = manager['name']
            if result[key] < manager.get('ceiling', 300):
                result[key] += 1
                spare -= 1
                changed = True
                if spare == 0:
                    break
        if not changed:
            break
    return result


def safe_limits(target, desired, managers):
    """Reserve submitted jobs before granting capacity to another manager.

    A None limit means manager standby: zero would mean unlimited to DAGMan.
    Existing workers keep running even when a lower future limit is assigned.
    """
    selected = [m for m in managers if m['name'] in desired]
    occupied = {m['name']: int(m.get('active_workers', 0)) for m in selected}
    result = {m['name']: min(desired[m['name']], occupied[m['name']]) for m in selected}
    available = max(0, int(target)-sum(occupied.values()))
    for manager in selected:
        name = manager['name']
        extra = min(available, desired[name]-result[name])
        result[name] += extra
        available -= extra
    return {name: cap or None for name, cap in result.items()}


def storage_reservation(policy, managers):
    """Reserve the full scheduling ceiling using each worker's measured size."""
    future = [dict(m, active=True) for m in managers if not m.get('retired')]
    shares = allocations(policy.get('ceiling', 300), future)
    return policy.get('minimum_free_bytes', 50000000000)+sum(
        shares.get(m['name'], 0)*m.get('peak_bytes_per_worker', policy.get('peak_bytes_per_worker', 250000000))
        for m in future)
