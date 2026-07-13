from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from auto_savings.store import EnrollmentRow, RestoreIntent, SavingsEvent, Store
from auto_savings.warehouse_snapshot import WarehouseSnapshot

ApplyAlter = Callable[[str, int], None]


def _restore(
    store: Store,
    org_id: str,
    name: str,
    snapshot: WarehouseSnapshot,
    intent: RestoreIntent,
    *,
    reason: str,
    now: datetime,
    cooldown_seconds: int | None,
    apply_alter: ApplyAlter,
) -> None:
    """Restore the intent's target value, audit the mutation, cool down, then
    clear the intent.

    Ordering is deliberate: ``apply_alter`` may raise (leaving the intent for the
    next tick to retry), and the audit event is recorded BEFORE ``delete_intent``
    so a failed event write also leaves the intent to retry rather than losing the
    record of a mutation we already made. ``set_cooldown`` also runs BEFORE
    ``delete_intent`` (finding #8): if the cooldown write failed AFTER the intent
    was already deleted, the warehouse could immediately re-enter the sentinel
    window with no anti-thrash guard. Persisting cooldown first means a failure
    there just leaves the intent for the next tick to retry, same as the other
    mutations. ``cooldown_seconds=None`` skips the anti-thrash cooldown (used by
    the admin reapply path, which is a one-off config correction, not a suspend
    cycle).
    """
    apply_alter(name, intent.restore_to)
    store.record_event(
        SavingsEvent(
            organization_id=org_id,
            warehouse_name=name,
            action="restore",
            reason=reason,
            from_value=snapshot.auto_suspend,
            to_value=intent.restore_to,
            observed_state=snapshot.state,
            observed_running=snapshot.running,
            observed_queued=snapshot.queued,
            observed_resumed_on=snapshot.resumed_on,
            observed_at=now,
            cycle_id=intent.cycle_id,
        )
    )
    if cooldown_seconds is not None:
        store.set_cooldown(org_id, name, now + timedelta(seconds=cooldown_seconds))
    store.delete_intent(org_id, name)


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

        settled = _reconcile_one(
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
        if settled:
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
) -> bool:
    """Reconcile a single warehouse.

    Returns True when this warehouse is "settled" this tick — meaning the
    decide step must not evaluate it for a new force-suspend (it belongs in
    the skip-set). Returns False only for a healthy enrolled warehouse
    sitting at its managed value with no outstanding intent — the one case
    decide is allowed to act on.
    """
    # Branch 0: absent from snapshot entirely (customer dropped it).
    if snapshot is None:
        if intent is not None and (now - intent.set_at).total_seconds() > orphan_grace_seconds:
            store.delete_intent(org_id, name)
            store.clear_enrollment(org_id, name)
        return True

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
        return True

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
        return True

    # Branch 3: no intent, warehouse became non-STANDARD.
    if snapshot.type != "STANDARD":
        store.mark_unsupported(org_id, name)
        return True

    # Branch 4: no intent, drift vs. the LIVE managed restore target.
    managed = enrollment.managed_auto_suspend if enrollment is not None else None
    if live != 1 and live != managed:
        if live is not None:
            store.mark_drifted(org_id, name, drifted_value=live)
        return True

    # Branch 5: no intent, live == 1 (independent sentinel) — protect it. Only
    # a durable intent row proves we own a sentinel; without one we must not
    # let decide claim it.
    if live == 1:
        return True

    # Healthy enrolled warehouse at its managed value, no intent, STANDARD —
    # the one case decide MUST be allowed to evaluate for suspension.
    return False


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

    # Admin-requested re-apply of the managed default over a drifted value (via the
    # API's reconcile(accept=False)). Unlike a worker sentinel, the live value here
    # is EXPECTED to differ from restore_to (it's the drifted value) and we DO
    # overwrite it — never treat the mismatch as drift. Idempotent if already applied.
    if intent.kind == "reapply":
        if live != restore_to:
            _restore(store, org_id, name, snapshot, intent, reason="reconcile_reapply",
                     now=now, cooldown_seconds=None, apply_alter=apply_alter)
        else:
            store.delete_intent(org_id, name)
        return

    # Already restored (idempotent terminal): a prior delete_intent failed, or
    # the warehouse resumed to the restored value. No ALTER.
    if live == restore_to:
        # Cooldown before delete (finding #8) — same rationale as ``_restore``.
        store.set_cooldown(org_id, name, now + timedelta(seconds=cooldown_seconds))
        store.delete_intent(org_id, name)
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
        _restore(store, org_id, name, snapshot, intent, reason="suspended",
                 now=now, cooldown_seconds=cooldown_seconds, apply_alter=apply_alter)
        return

    if snapshot.state == "STARTED" and (snapshot.running > 0 or snapshot.queued > 0):
        # A query landed — restore, then back off with a cooldown. A warehouse
        # that resumed under our sentinel just proved it is bursty; the cooldown
        # bounds how often it can re-enter the AUTO_SUSPEND=1-live window.
        _restore(store, org_id, name, snapshot, intent, reason="busy",
                 now=now, cooldown_seconds=cooldown_seconds, apply_alter=apply_alter)
        return

    # STARTED and idle and live == 1: HOLD unless (a) it already completed a
    # suspend→resume cycle since we set the sentinel (resumed_on advanced), or
    # (b) the intent aged past the backstop. Guard both-non-None so a snapshot
    # with resumed_on=None (or a baseline-less intent) still HOLDs until aged.
    resumed_since = (
        intent.baseline_resumed_on is not None
        and snapshot.resumed_on is not None
        and snapshot.resumed_on != intent.baseline_resumed_on
    )
    aged_out = (now - intent.set_at).total_seconds() > intent_hold_seconds
    if resumed_since or aged_out:
        _restore(store, org_id, name, snapshot, intent,
                 reason="resume_aware" if resumed_since else "aged_out",
                 now=now, cooldown_seconds=cooldown_seconds, apply_alter=apply_alter)
    return
