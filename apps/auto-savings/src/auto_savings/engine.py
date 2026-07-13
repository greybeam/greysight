from __future__ import annotations

from datetime import datetime
from typing import Callable

from auto_savings.config import WorkerConfig
from auto_savings.decision import should_force_suspend
from auto_savings.reconcile import reconcile
from auto_savings.store import Store
from auto_savings.warehouse_snapshot import parse_warehouses

ApplyAlter = Callable[[str, int], None]


def run_cycle(
    org_id: str,
    *,
    rows: list[dict],
    store: Store,
    config: WorkerConfig,
    now: datetime,
    apply_alter: ApplyAlter,
) -> bool:
    """Run one per-tenant engine cycle over a single ``SHOW WAREHOUSES`` result.

    Order of operations: snapshot → reconcile-always → decide-when-enabled → act.
    Reconcile drains outstanding intents regardless of the global switch; the
    decide step (new force-suspends) runs only when the kill switch is on.

    Returns ``True`` when any restore-intent remains outstanding after the cycle
    (the caller should fast-poll to shrink the ``AUTO_SUSPEND=1``-live window).
    """
    snapshots = parse_warehouses(rows, now=now)

    # Single snapshot of store state for the whole cycle.
    enrollments = store.list_enrollments(org_id)
    intents = store.list_intents(org_id)

    # Reconcile ALWAYS — drains intents even when the switch is off.
    #
    # ``reconcile`` returns the set of warehouse names settled this tick —
    # every warehouse it mutated, cleared, or otherwise finished deciding
    # about. A healthy idle warehouse at its managed value (no intent) is the
    # one case left OUT of that set, so decide is still free to force-suspend
    # it (see ``test_idle_warehouse_gets_intent_then_alter_in_order``). The
    # skip-gate below closes the race where reconcile marks/clears a
    # warehouse this tick but the engine's top-of-cycle enrollment snapshot
    # is still stale.
    skip = reconcile(
        org_id,
        snapshots,
        enrollments,
        intents,
        store,
        now=now,
        cooldown_seconds=config.cooldown_seconds,
        intent_hold_seconds=config.max_intent_hold_ticks * config.poll_interval_seconds,
        orphan_grace_seconds=config.orphan_grace_seconds,
        apply_alter=apply_alter,
    )

    # Decide ONLY when the kill switch is on.
    settings = store.get_settings(org_id)
    if settings is None or not settings.global_enabled:
        return bool(store.list_intents(org_id))

    snapshot_by_name = {snap.name: snap for snap in snapshots}
    intent_names = {intent.warehouse_name for intent in intents}

    for enrollment in enrollments:
        name = enrollment.warehouse_name
        if name in skip:
            continue
        if not enrollment.enabled:
            continue
        snapshot = snapshot_by_name.get(name)
        if snapshot is None:
            continue

        in_cooldown = (
            enrollment.cooldown_ts is not None and enrollment.cooldown_ts > now
        )
        is_drifted = enrollment.drift_state != "ok"
        has_outstanding_intent = name in intent_names

        if should_force_suspend(
            snapshot,
            now=now,
            uptime_floor_seconds=config.uptime_floor_seconds,
            in_cooldown=in_cooldown,
            is_drifted=is_drifted,
            has_outstanding_intent=has_outstanding_intent,
        ):
            # Durability before mutation: write the restore-intent row FIRST
            # (restore target is the LIVE managed default), THEN the ALTER.
            store.write_intent(
                org_id,
                name,
                restore_to=enrollment.managed_auto_suspend,
                set_at=now,
                baseline_resumed_on=snapshot.resumed_on,
            )
            apply_alter(name, 1)

    # Fast-poll while any intent is outstanding to shrink the live window.
    return bool(store.list_intents(org_id))
