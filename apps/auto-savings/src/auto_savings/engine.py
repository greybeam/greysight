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
) -> None:
    """Run one per-tenant engine cycle over a single ``SHOW WAREHOUSES`` result.

    Order of operations: snapshot → reconcile-always → decide-when-enabled → act.
    Reconcile drains outstanding intents regardless of the global switch; the
    decide step (new force-suspends) runs only when the kill switch is on.
    """
    snapshots = parse_warehouses(rows, now=now)

    # Single snapshot of store state for the whole cycle.
    enrollments = store.list_enrollments(org_id)
    intents = store.list_intents(org_id)

    # Reconcile ALWAYS — drains intents even when the switch is off.
    #
    # NOTE: ``reconcile`` returns the set of warehouse names it processed this
    # tick, and the plan describes gating the decide step on ``name not in
    # skip``. But the committed reconcile (Task 8) adds *every* enrolled name to
    # that set — including quiet no-ops (its case 5 / no-op case 4) that it
    # never mutated. Honoring it as a hard decide gate would suppress *every*
    # force-suspend, including a fresh idle warehouse (the authoritative
    # ``test_idle_warehouse_gets_intent_then_alter_in_order`` case). The real
    # double-action protection lives in the per-warehouse guards below, all
    # derived from the single top-of-cycle store read: ``has_outstanding_intent``
    # covers every warehouse reconcile drained/held (branch 2), ``in_cooldown``
    # covers a just-restored one, ``is_drifted`` covers a flagged one, and
    # ``should_force_suspend``'s own state/type checks cover unsupported/busy.
    reconcile(
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
        return

    snapshot_by_name = {snap.name: snap for snap in snapshots}
    intent_names = {intent.warehouse_name for intent in intents}

    for enrollment in enrollments:
        name = enrollment.warehouse_name
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
            )
            apply_alter(name, 1)
