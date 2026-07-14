from datetime import datetime, timedelta, timezone

import pytest

from auto_savings.reconcile import reconcile
from auto_savings.store import EnrollmentRow, InMemoryStore, StoreError
from auto_savings.warehouse_snapshot import WarehouseSnapshot

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _wh(name="WH1", auto_suspend=1, state="SUSPENDED", type="STANDARD"):
    return WarehouseSnapshot(
        name=name, state=state, type=type, size="X-Small",
        started_clusters=1, min_cluster_count=1, max_cluster_count=1,
        running=0, queued=0, auto_suspend=auto_suspend, auto_resume=True,
        resumed_on=None, created_on=NOW - timedelta(days=1),
    )


def _enroll(name="WH1", managed=300, stored=300, created=None, enabled=True):
    return EnrollmentRow(
        organization_id="org-1", warehouse_name=name, enabled=enabled,
        managed_auto_suspend=managed, stored_default_auto_suspend=stored,
        warehouse_created_on=created or (NOW - timedelta(days=1)), cooldown_ts=None,
        drift_state="ok", drifted_value=None,
    )


def _reconcile(store, snaps, enrolls, **kw):
    for e in enrolls:
        store.seed_enrollment(e)
    defaults = dict(now=NOW, cooldown_seconds=60, intent_hold_seconds=15.0, orphan_grace_seconds=120.0)
    defaults.update(kw)
    calls = []
    skip = reconcile("org-1", snaps, enrolls, store.list_intents("org-1"), store,
                     apply_alter=lambda name, val: calls.append((name, val)), **defaults)
    return skip, calls


def test_intent_restores_and_cools_down_when_suspended():
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, cycle_id="c1")
    skip, calls = _reconcile(store, [_wh(auto_suspend=1, state="SUSPENDED")], [_enroll()])
    assert calls == [("WH1", 300)]  # restore target (managed default)
    assert store.list_intents("org-1") == []
    assert store.list_enrollments("org-1")[0].cooldown_ts == NOW + timedelta(seconds=60)  # cooldown_seconds=60
    assert "WH1" in skip
    [event] = store.list_events("org-1")
    assert (event.action, event.reason, event.from_value, event.to_value) == (
        "restore", "suspended", 1, 300
    )
    assert event.cycle_id == "c1"


def test_reapply_intent_overwrites_drifted_value_without_flagging_drift():
    # Admin "re-apply old default": intent.kind='reapply', live sits at the drifted
    # value (120), restore_to is the managed default (300). The worker MUST ALTER
    # 120 -> 300 and clear the intent — not re-flag drift (the accept=False bug fix).
    store = InMemoryStore()
    store.write_intent(
        "org-1", "WH1", restore_to=300, expected_from=120, kind="reapply"
    )
    drifted = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                                started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                                running=0, queued=0, auto_suspend=120, auto_resume=True,
                                resumed_on=None, created_on=NOW - timedelta(days=1))
    _, calls = _reconcile(store, [drifted], [_enroll(managed=300)])
    assert calls == [("WH1", 300)]                                  # applied, not stomped-as-drift
    assert store.list_intents("org-1") == []                        # intent cleared
    assert store.list_enrollments("org-1")[0].drift_state == "ok"   # NOT re-flagged drifted
    assert store.list_enrollments("org-1")[0].cooldown_ts is None   # correction, no cooldown
    [event] = store.list_events("org-1")
    assert event.action == "restore" and event.reason == "reconcile_reapply"
    assert event.from_value == 120 and event.to_value == 300


def test_reapply_intent_idempotent_when_already_at_target():
    # Worker already applied it last tick (live == restore_to) → just clear, no ALTER.
    store = InMemoryStore()
    store.write_intent(
        "org-1", "WH1", restore_to=300, expected_from=120, kind="reapply"
    )
    _, calls = _reconcile(store, [_wh(auto_suspend=300, state="STARTED")], [_enroll(managed=300)])
    assert calls == []
    assert store.list_intents("org-1") == []
    assert store.list_events("org-1") == []


def test_failed_restore_alter_records_no_audit_event():
    # apply_alter raises → no mutation happened → no audit row, intent kept for retry.
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)
    store.seed_enrollment(_enroll())

    def boom(name, val):
        raise RuntimeError("ALTER failed")

    with pytest.raises(RuntimeError, match="ALTER failed"):
        reconcile("org-1", [_wh(auto_suspend=1, state="SUSPENDED")], [_enroll()],
                  store.list_intents("org-1"), store, now=NOW, cooldown_seconds=60,
                  intent_hold_seconds=15.0, orphan_grace_seconds=120.0, apply_alter=boom)
    assert store.list_events("org-1") == []       # no event for a mutation that didn't happen
    assert store.list_intents("org-1") != []      # intent kept


def test_unconfirmed_sentinel_retries_when_live_still_matches_expected_value():
    # The initial ALTER may have failed, or SHOW may be stale after it succeeded.
    # Re-applying the sentinel is safe only while live still equals the value that
    # was observed before the durable intent was written.
    store = InMemoryStore()
    store.write_intent(
        "org-1", "WH1", restore_to=300, expected_from=300
    )
    stale = _wh(auto_suspend=300, state="STARTED")
    _, calls = _reconcile(store, [stale], [_enroll()])
    assert calls == [("WH1", 1)]
    assert store.list_intents("org-1") != []
    assert store.list_enrollments("org-1")[0].cooldown_ts is None  # no cooldown
    assert store.list_enrollments("org-1")[0].drift_state == "ok"  # no drift flagged


def test_aged_unconfirmed_sentinel_authoritatively_restores_and_clears():
    store = InMemoryStore()
    store.write_intent(
        "org-1", "WH1", restore_to=300, expected_from=300,
        set_at=NOW, cycle_id="cycle-1",
    )
    live_expected = _wh(auto_suspend=300, state="STARTED")

    _, fresh_calls = _reconcile(
        store, [live_expected], [_enroll()], now=NOW,
        intent_hold_seconds=15.0,
    )
    _, aged_calls = _reconcile(
        store, [live_expected], [_enroll()],
        now=NOW + timedelta(seconds=16), intent_hold_seconds=15.0,
    )

    assert fresh_calls == [("WH1", 1)]
    assert aged_calls == [("WH1", 300)]
    assert store.list_intents("org-1") == []
    assert store.list_enrollments("org-1")[0].cooldown_ts == (
        NOW + timedelta(seconds=76)
    )
    assert store.list_events("org-1")[-1].reason == "aged_out"


def test_unconfirmed_sentinel_treats_changed_live_value_as_customer_drift():
    store = InMemoryStore()
    store.write_intent(
        "org-1", "WH1", restore_to=300, expected_from=300
    )
    drifting = _wh(auto_suspend=120, state="STARTED")
    _, calls = _reconcile(store, [drifting], [_enroll()])
    assert calls == []
    assert store.list_intents("org-1") == []
    enrollment = store.list_enrollments("org-1")[0]
    assert enrollment.drift_state == "drifted"
    assert enrollment.drifted_value == 120


def test_reapply_refuses_to_overwrite_value_changed_after_enqueue():
    store = InMemoryStore()
    store.write_intent(
        "org-1", "WH1", restore_to=300, expected_from=120, kind="reapply"
    )

    _, calls = _reconcile(
        store, [_wh(auto_suspend=180, state="STARTED")], [_enroll()]
    )

    assert calls == []
    assert store.list_intents("org-1") == []
    enrollment = store.list_enrollments("org-1")[0]
    assert enrollment.drift_state == "drifted"
    assert enrollment.drifted_value == 180


def test_live_one_durably_confirms_ownership_before_restore_executes():
    # Multi-tick: the first observation of AUTO_SUSPEND=1 proves the ALTER landed.
    # We must durably confirm ownership on that tick — even when the outcome is a
    # HOLD (idle, not aged) rather than a restore — so a later stale SHOW cannot
    # strand the sentinel. A subsequent tick then restores on the confirmed intent.
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, set_at=NOW, baseline_resumed_on=None)

    # Tick 1: live == 1, STARTED + idle + not aged -> confirm, then HOLD.
    idle = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                             started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                             running=0, queued=0, auto_suspend=1, auto_resume=True,
                             resumed_on=None, created_on=NOW - timedelta(days=1))
    _, calls = _reconcile(store, [idle], [_enroll()], now=NOW, intent_hold_seconds=15.0)
    assert calls == []                                          # no restore yet (held)
    [held] = store.list_intents("org-1")
    assert held.sentinel_confirmed is True                      # ownership durably confirmed
    assert store.list_events("org-1") == []

    # Tick 2: confirmed intent, live == 1 + SUSPENDED -> restore executes.
    _, calls2 = _reconcile(store, [_wh(auto_suspend=1, state="SUSPENDED")], [_enroll()], now=NOW)
    assert calls2 == [("WH1", 300)]
    assert store.list_intents("org-1") == []
    assert store.list_events("org-1")[0].reason == "suspended"


def test_replaced_intent_during_confirmation_prevents_terminal_restore():
    class ReplacedOnConfirmationStore(InMemoryStore):
        def confirm_sentinel(
            self,
            organization_id: str,
            warehouse_name: str,
            *args: object,
        ) -> None:
            self.write_intent(
                organization_id,
                warehouse_name,
                restore_to=600,
                cycle_id="replacement-cycle",
            )
            super().confirm_sentinel(organization_id, warehouse_name, *args)

    store = ReplacedOnConfirmationStore()
    store.write_intent(
        "org-1", "WH1", restore_to=300, cycle_id="original-cycle"
    )

    with pytest.raises(StoreError):
        _reconcile(
            store,
            [_wh(auto_suspend=1, state="SUSPENDED")],
            [_enroll()],
        )

    assert store.list_intents("org-1")[0].cycle_id == "replacement-cycle"
    assert store.list_events("org-1") == []
    assert store.list_enrollments("org-1")[0].cooldown_ts is None


def test_stale_worker_cannot_delete_replacement_intent():
    class ReplacedBeforeDeleteStore(InMemoryStore):
        def delete_intent(
            self,
            organization_id: str,
            warehouse_name: str,
            cycle_id: str,
            kind: str,
        ) -> bool:
            self.write_intent(
                organization_id,
                warehouse_name,
                restore_to=600,
                expected_from=300,
                cycle_id="replacement-cycle",
            )
            return super().delete_intent(
                organization_id, warehouse_name, cycle_id, kind
            )

    store = ReplacedBeforeDeleteStore()
    store.write_intent(
        "org-1",
        "WH1",
        restore_to=300,
        expected_from=300,
        cycle_id="original-cycle",
    )
    store.confirm_sentinel("org-1", "WH1", "original-cycle")

    _reconcile(
        store,
        [_wh(auto_suspend=300, state="STARTED")],
        [_enroll()],
    )

    [remaining] = store.list_intents("org-1")
    assert remaining.cycle_id == "replacement-cycle"


def test_started_busy_restores_with_backoff_cooldown():
    # A query landed under our sentinel → restore, then back off with a cooldown:
    # a warehouse that resumed proved it is bursty, so bound how often it can
    # re-enter the AUTO_SUSPEND=1-live window (intended contract change).
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, baseline_resumed_on=None)
    busy = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                             started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                             running=1, queued=0, auto_suspend=1, auto_resume=True,
                             resumed_on=None, created_on=NOW - timedelta(days=1))
    _, calls = _reconcile(store, [busy], [_enroll()])
    assert calls == [("WH1", 300)]
    assert store.list_enrollments("org-1")[0].cooldown_ts == NOW + timedelta(seconds=60)


def test_resume_aware_restore_when_resumed_on_advanced():
    # STARTED & idle & live==1 but resumed_on advanced past the baseline → the
    # warehouse already completed a suspend→resume cycle under our sentinel.
    # Restore early (holding just invites another costly cycle), set cooldown.
    store = InMemoryStore()
    t0 = NOW - timedelta(seconds=30)
    t1 = NOW - timedelta(seconds=2)  # resumed AFTER we set the sentinel
    store.write_intent("org-1", "WH1", restore_to=300, set_at=NOW, baseline_resumed_on=t0)
    cycled = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                               started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                               running=0, queued=0, auto_suspend=1, auto_resume=True,
                               resumed_on=t1, created_on=NOW - timedelta(days=1))
    # Fresh intent (not aged) — would normally HOLD, but the resume advance forces restore.
    _, calls = _reconcile(store, [cycled], [_enroll()], now=NOW, intent_hold_seconds=15.0)
    assert calls == [("WH1", 300)]
    assert store.list_intents("org-1") == []
    assert store.list_enrollments("org-1")[0].cooldown_ts == NOW + timedelta(seconds=60)
    assert store.list_events("org-1")[0].reason == "resume_aware"


def test_started_idle_holds_when_resumed_on_unchanged_from_baseline():
    # resumed_on matches the baseline (no new cycle) and intent is fresh → HOLD.
    store = InMemoryStore()
    t0 = NOW - timedelta(seconds=5)
    store.write_intent("org-1", "WH1", restore_to=300, set_at=NOW, baseline_resumed_on=t0)
    idle = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                             started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                             running=0, queued=0, auto_suspend=1, auto_resume=True,
                             resumed_on=t0, created_on=NOW - timedelta(days=1))
    _, calls = _reconcile(store, [idle], [_enroll()], now=NOW, intent_hold_seconds=15.0)
    assert calls == []
    assert store.list_intents("org-1") != []  # still held


def test_started_idle_still_one_holds_intent_until_age_exceeds_bound():
    # Suspend hasn't landed yet — HOLD, don't guillotine it (finding #4).
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, set_at=NOW)  # deterministic age
    idle_started = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                                     started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                                     running=0, queued=0, auto_suspend=1, auto_resume=True,
                                     resumed_on=None, created_on=NOW - timedelta(days=1))
    # Fresh intent → held, no ALTER.
    _, calls = _reconcile(store, [idle_started], [_enroll()], now=NOW)
    assert calls == []
    assert store.list_intents("org-1") != []  # still held
    # Age it past intent_hold_seconds → force-restore (anti-stranding backstop).
    _, calls2 = _reconcile(store, [idle_started], [_enroll()],
                           now=NOW + timedelta(seconds=30), intent_hold_seconds=15.0)
    assert calls2 == [("WH1", 300)]
    assert store.list_events("org-1")[0].reason == "aged_out"


def test_intent_restore_detects_customer_edit_mid_suspend():
    # A customer edit (live=120) is only distinguishable from a stale SHOW once we
    # have confirmed the sentinel landed — so confirm first, then 120 is genuine drift.
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, cycle_id="c1")
    store.confirm_sentinel("org-1", "WH1", "c1")
    _, calls = _reconcile(store, [_wh(auto_suspend=120, state="STARTED")], [_enroll()])
    assert calls == []  # not stomped
    assert store.list_enrollments("org-1")[0].drift_state == "drifted"


def test_dropped_warehouse_with_stale_intent_is_cleaned_up():
    # Warehouse fully dropped: absent from snapshot + intent older than the grace →
    # delete intent + enrollment so the org can leave worker_tenants() (finding #10).
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, set_at=NOW)  # deterministic age
    # Empty snapshot (WH1 dropped); evaluate well past the grace window.
    _, calls = _reconcile(store, [], [_enroll()],
                          now=NOW + timedelta(seconds=200), orphan_grace_seconds=120.0)
    assert calls == []                       # nothing to ALTER
    assert store.list_intents("org-1") == []  # intent cleaned
    assert store.list_enrollments("org-1") == []  # enrollment cleared


def test_cleanup_keeps_replacement_intent_and_enrollment():
    class ReplacedBeforeCleanupStore(InMemoryStore):
        def cleanup_intent_and_enrollment(
            self, organization_id, warehouse_name, cycle_id, kind
        ):
            self.write_intent(
                organization_id, warehouse_name, restore_to=600,
                expected_from=300, cycle_id="replacement-cycle",
            )
            return super().cleanup_intent_and_enrollment(
                organization_id, warehouse_name, cycle_id, kind
            )

    store = ReplacedBeforeCleanupStore()
    store.write_intent(
        "org-1", "WH1", restore_to=300, expected_from=300,
        set_at=NOW, cycle_id="original-cycle",
    )

    _reconcile(
        store, [], [_enroll()], now=NOW + timedelta(seconds=200),
        orphan_grace_seconds=120.0,
    )

    [remaining] = store.list_intents("org-1")
    assert remaining.cycle_id == "replacement-cycle"
    assert store.list_enrollments("org-1") != []


@pytest.mark.parametrize("missing_field", ["managed", "created_on"])
def test_partial_enabled_enrollment_with_intent_is_quarantined(missing_field):
    store = InMemoryStore()
    enrollment = EnrollmentRow(
        organization_id="org-1", warehouse_name="WH1", enabled=True,
        managed_auto_suspend=None if missing_field == "managed" else 300,
        stored_default_auto_suspend=300,
        warehouse_created_on=(
            None if missing_field == "created_on" else NOW - timedelta(days=1)
        ),
        cooldown_ts=None, drift_state="ok", drifted_value=None,
    )
    store.write_intent(
        "org-1", "WH1", restore_to=300, expected_from=300,
        cycle_id="cycle-1",
    )

    _, calls = _reconcile(
        store, [_wh(auto_suspend=1, state="SUSPENDED")], [enrollment]
    )

    assert calls == []
    [remaining] = store.list_intents("org-1")
    assert remaining.cycle_id == "cycle-1"
    assert store.list_enrollments("org-1")[0].drift_state == "unsupported"


def test_dropped_warehouse_within_grace_is_left_alone():
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, set_at=NOW)
    _, _ = _reconcile(store, [], [_enroll()],
                      now=NOW + timedelta(seconds=10), orphan_grace_seconds=120.0)
    assert store.list_intents("org-1") != []  # transient absence — keep


def test_already_restored_intent_is_cleared_idempotently():
    # apply_alter succeeded but delete_intent failed last tick → live already == restore_to.
    # No matching subcase would leave the intent stuck forever (Codex R2.1 HIGH).
    # Such a leftover intent is necessarily confirmed (we confirm before restoring),
    # so live == restore_to here is a genuine completed restore, not a stale SHOW.
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, cycle_id="c1")
    store.confirm_sentinel("org-1", "WH1", "c1")
    live_restored = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                                      started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                                      running=0, queued=0, auto_suspend=300, auto_resume=True,
                                      resumed_on=None, created_on=NOW - timedelta(days=1))
    _, calls = _reconcile(store, [live_restored], [_enroll()])
    assert calls == []                             # no ALTER — already at restore_to
    assert store.list_intents("org-1") == []       # intent cleared idempotently
    assert store.list_enrollments("org-1")[0].cooldown_ts == NOW + timedelta(seconds=60)


def test_drain_runs_even_when_disabled():
    # Unenroll mid-suspend: enabled=False but intent must still drain (finding #5).
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)
    _, calls = _reconcile(store, [_wh(auto_suspend=1, state="SUSPENDED")],
                          [_enroll(enabled=False)])
    assert calls == [("WH1", 300)]
    assert store.list_intents("org-1") == []


def test_created_on_mismatch_invalidates_stale_enrollment_and_intent():
    # Name reused by a recreated warehouse (finding M2/#8); a stale intent from the OLD
    # warehouse must be deleted, never applied to the new one (Codex R2.1 HIGH).
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)  # belongs to the dropped warehouse
    fresh = WarehouseSnapshot(name="WH1", state="STARTED", type="STANDARD", size="X-Small",
                              started_clusters=1, min_cluster_count=1, max_cluster_count=1,
                              running=0, queued=0, auto_suspend=300, auto_resume=True,
                              resumed_on=None, created_on=NOW)  # created just now
    skip, calls = _reconcile(store, [fresh],
                             [_enroll(created=NOW - timedelta(days=30))])  # old enrollment
    assert calls == []                            # stale intent NOT applied to the new warehouse
    assert store.list_intents("org-1") == []      # stale intent deleted
    assert store.list_enrollments("org-1") == []  # stale enrollment dropped
    assert "WH1" in skip


def test_created_on_mismatch_without_snapshot_intent_keeps_fresh_intent():
    class FreshIntentBeforeEnrollmentCleanupStore(InMemoryStore):
        def cleanup_enrollment_if_no_intent(
            self, organization_id: str, warehouse_name: str
        ) -> bool:
            self.write_intent(
                organization_id,
                warehouse_name,
                restore_to=600,
                expected_from=300,
                cycle_id="fresh-cycle",
                kind="reapply",
            )
            return super().cleanup_enrollment_if_no_intent(
                organization_id, warehouse_name
            )

    store = FreshIntentBeforeEnrollmentCleanupStore()
    recreated = _wh(auto_suspend=300, state="STARTED")
    recreated = WarehouseSnapshot(
        **{**recreated.__dict__, "created_on": NOW}
    )

    skip, calls = _reconcile(
        store,
        [recreated],
        [_enroll(created=NOW - timedelta(days=30))],
    )

    assert calls == []
    [intent] = store.list_intents("org-1")
    assert intent.cycle_id == "fresh-cycle"
    assert store.list_enrollments("org-1") != []
    assert "WH1" in skip


def test_independent_one_without_intent_is_left_untouched():
    store = InMemoryStore()
    skip, calls = _reconcile(store, [_wh(auto_suspend=1, state="SUSPENDED")], [_enroll()])
    assert calls == []
    assert store.list_enrollments("org-1")[0].drift_state == "ok"  # not flagged
    assert "WH1" in skip


def test_drift_baseline_is_managed_default_not_stored_capture():
    # managed edited to 90; live at 90 is CORRECT, not drift. Live at 120 IS drift.
    store = InMemoryStore()
    _, _ = _reconcile(store, [_wh(auto_suspend=90, state="SUSPENDED")],
                      [_enroll(managed=90, stored=300)])
    assert store.list_enrollments("org-1")[0].drift_state == "ok"
    store2 = InMemoryStore()
    _reconcile(store2, [_wh(auto_suspend=120, state="SUSPENDED")],
               [_enroll(managed=90, stored=300)])
    assert store2.list_enrollments("org-1")[0].drift_state == "drifted"


def test_non_standard_marked_unsupported():
    store = InMemoryStore()
    _reconcile(store, [_wh(type="SNOWPARK-OPTIMIZED", auto_suspend=300)], [_enroll()])
    assert store.list_enrollments("org-1")[0].drift_state == "unsupported"


def test_healthy_idle_warehouse_is_not_in_skip_but_independent_one_is():
    # A healthy STANDARD warehouse sitting at its managed value with no
    # outstanding intent is NOT settled — decide must be free to evaluate it
    # for a force-suspend. An independent live==1 sentinel (no intent) IS
    # settled — reconcile must protect it from decide claiming ownership.
    store = InMemoryStore()
    skip, calls = _reconcile(
        store,
        [_wh(name="WH-HEALTHY", auto_suspend=300, state="STARTED"),
         _wh(name="WH-INDEPENDENT", auto_suspend=1, state="SUSPENDED")],
        [_enroll(name="WH-HEALTHY", managed=300), _enroll(name="WH-INDEPENDENT", managed=300)],
    )
    assert calls == []
    assert "WH-HEALTHY" not in skip
    assert "WH-INDEPENDENT" in skip
