from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from auto_savings.store import EnrollmentRow, RestoreIntent, Store
from auto_savings.warehouse_snapshot import WarehouseSnapshot

ApplyAlter = Callable[[str, int], None]


def reconcile(
    org_id: str,
    snapshots: list[WarehouseSnapshot],
    enrollments: list[EnrollmentRow],
    intents: list[RestoreIntent],
    store: Store,
    *,
    now: datetime,
    cooldown_seconds: int,
    intent_hold_seconds: float,
    orphan_grace_seconds: float,
    apply_alter: ApplyAlter,
) -> set[str]:
    """Reconcile outstanding restore-intents and drift over a single snapshot.

    Returns the set of warehouse names settled this tick (skip in decide).
    Runs for every managed warehouse regardless of ``enabled``. Every store
    mutation goes through ``store``; every ALTER through ``apply_alter`` (which
    may raise — the intent is not deleted then, so the next tick retries).
    """
    snapshot_by_name = {snap.name: snap for snap in snapshots}
    intent_by_name = {intent.warehouse_name: intent for intent in intents}
    enrollment_by_name = {row.warehouse_name: row for row in enrollments}

    skip: set[str] = set()
    for name in sorted(set(enrollment_by_name) | set(intent_by_name)):
        enrollment = enrollment_by_name.get(name)
        intent = intent_by_name.get(name)
        snapshot = snapshot_by_name.get(name)

        _reconcile_one(
            org_id,
            name,
            snapshot,
            enrollment,
            intent,
            store,
            now=now,
            cooldown_seconds=cooldown_seconds,
            intent_hold_seconds=intent_hold_seconds,
            orphan_grace_seconds=orphan_grace_seconds,
            apply_alter=apply_alter,
        )
        skip.add(name)
    return skip


def _reconcile_one(
    org_id: str,
    name: str,
    snapshot: WarehouseSnapshot | None,
    enrollment: EnrollmentRow | None,
    intent: RestoreIntent | None,
    store: Store,
    *,
    now: datetime,
    cooldown_seconds: int,
    intent_hold_seconds: float,
    orphan_grace_seconds: float,
    apply_alter: ApplyAlter,
) -> None:
    # Branch 0: absent from snapshot entirely (customer dropped it).
    if snapshot is None:
        if intent is not None and (now - intent.set_at).total_seconds() > orphan_grace_seconds:
            store.delete_intent(org_id, name)
            store.clear_enrollment(org_id, name)
        return

    # Branch 1: created_on mismatch — name reused by a recreated warehouse.
    if (
        enrollment is not None
        and enrollment.warehouse_created_on is not None
        and snapshot.created_on is not None
        and snapshot.created_on != enrollment.warehouse_created_on
    ):
        if intent is not None:
            store.delete_intent(org_id, name)
        store.clear_enrollment(org_id, name)
        return

    live = snapshot.auto_suspend

    # Branch 2: outstanding restore-intent — we own it.
    if intent is not None:
        _reconcile_intent(
            org_id,
            name,
            snapshot,
            intent,
            store,
            now=now,
            cooldown_seconds=cooldown_seconds,
            intent_hold_seconds=intent_hold_seconds,
            apply_alter=apply_alter,
        )
        return

    # Branch 3: no intent, warehouse became non-STANDARD.
    if snapshot.type != "STANDARD":
        store.mark_unsupported(org_id, name)
        return

    # Branch 4: no intent, drift vs. the LIVE managed restore target.
    managed = enrollment.managed_auto_suspend if enrollment is not None else None
    if live != 1 and live != managed:
        if live is not None:
            store.mark_drifted(org_id, name, drifted_value=live)
        return

    # Branch 5: no intent, live == 1 (independent sentinel) — leave untouched.
    return


def _reconcile_intent(
    org_id: str,
    name: str,
    snapshot: WarehouseSnapshot,
    intent: RestoreIntent,
    store: Store,
    *,
    now: datetime,
    cooldown_seconds: int,
    intent_hold_seconds: float,
    apply_alter: ApplyAlter,
) -> None:
    live = snapshot.auto_suspend
    restore_to = intent.restore_to

    # Already restored (idempotent terminal): a prior delete_intent failed, or
    # the warehouse resumed to the restored value. No ALTER.
    if live == restore_to:
        store.delete_intent(org_id, name)
        store.set_cooldown(org_id, name, now + timedelta(seconds=cooldown_seconds))
        return

    # Customer edited mid-suspend — don't stomp their edit.
    if live != 1:
        if live is not None:
            store.mark_drifted(org_id, name, drifted_value=live)
        store.delete_intent(org_id, name)
        return

    # live == 1: decide restore vs. hold by live state.
    if snapshot.state == "SUSPENDED":
        # Savings captured — restore then cooldown.
        apply_alter(name, restore_to)
        store.delete_intent(org_id, name)
        store.set_cooldown(org_id, name, now + timedelta(seconds=cooldown_seconds))
        return

    if snapshot.state == "STARTED" and (snapshot.running > 0 or snapshot.queued > 0):
        # A query landed — restore, no cooldown (backed off).
        apply_alter(name, restore_to)
        store.delete_intent(org_id, name)
        return

    # STARTED and idle and live == 1: suspend hasn't landed yet → HOLD,
    # unless the intent has aged past the hold bound (anti-stranding backstop).
    if (now - intent.set_at).total_seconds() > intent_hold_seconds:
        apply_alter(name, restore_to)
        store.delete_intent(org_id, name)
        store.set_cooldown(org_id, name, now + timedelta(seconds=cooldown_seconds))
    return
