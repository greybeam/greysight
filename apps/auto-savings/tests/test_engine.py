import logging
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from snowflake.connector.errors import OperationalError

from auto_savings.config import WorkerConfig
from auto_savings.engine import CycleResult, run_cycle
from auto_savings.snowflake_session import (
    ConnectorErrorMetadata,
    SuspendOutcome,
    SuspendResult,
)
from auto_savings.store import EnrollmentRow, SavingsEvent, StoreError

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
CREATED_ON = NOW - timedelta(days=1)
UPDATED_AT = NOW - timedelta(minutes=1)
CONFIG = WorkerConfig(
    supabase_url="u",
    supabase_service_role_key="k",
    uptime_floor_seconds=62,
)
ACCEPTED_RESULT = SuspendResult(SuspendOutcome.ACCEPTED)


def _unknown_result() -> SuspendResult:
    return SuspendResult(
        outcome=SuspendOutcome.UNKNOWN_IDEMPOTENT,
        connector_error=ConnectorErrorMetadata(
            error_type="ProgrammingError",
            errno=90064,
            sqlstate="57014",
            message="observed race",
        ),
    )


class TrackingStore:
    def __init__(
        self,
        *,
        authorized: bool = True,
        enrollment: EnrollmentRow | None = None,
        audit_error: Exception | None = None,
        disable_after_authorize: bool = False,
    ) -> None:
        self.authorized = authorized
        self.enrollments = [enrollment or _enrollment()]
        self.audit_error = audit_error
        self.disable_after_authorize = disable_after_authorize
        self.operations: list[str] = []
        self.authorizations: list[tuple] = []
        self.deletions: list[tuple] = []
        self.events: list[SavingsEvent] = []

    def list_enrollments(self, organization_id: str) -> list[EnrollmentRow]:
        assert organization_id == "org-1"
        return list(self.enrollments)

    def authorize_suspend(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        warehouse_created_on: datetime,
        enrollment_updated_at: datetime,
    ) -> bool:
        self.operations.append("authorize_suspend")
        self.authorizations.append(
            (
                organization_id,
                warehouse_name,
                warehouse_created_on,
                enrollment_updated_at,
            )
        )
        result = self.authorized
        if self.disable_after_authorize:
            self.authorized = False
        return result

    def delete_stale_enrollment(
        self,
        organization_id: str,
        warehouse_name: str,
        *,
        warehouse_created_on: datetime,
        enrollment_updated_at: datetime,
    ) -> bool:
        self.operations.append("delete_stale_enrollment")
        self.deletions.append(
            (
                organization_id,
                warehouse_name,
                warehouse_created_on,
                enrollment_updated_at,
            )
        )
        return True

    def record_event(self, event: SavingsEvent) -> None:
        self.operations.append("record_event")
        if self.audit_error is not None:
            raise self.audit_error
        self.events.append(event)


def _enrollment(**overrides) -> EnrollmentRow:
    values = {
        "organization_id": "org-1",
        "warehouse_name": "WH1",
        "enabled": True,
        "warehouse_created_on": CREATED_ON,
        "updated_at": UPDATED_AT,
    }
    values.update(overrides)
    return EnrollmentRow(**values)


def _rows(**overrides) -> list[dict]:
    row = {
        "name": "WH1",
        "state": "STARTED",
        "type": "STANDARD",
        "size": "X-Small",
        "started_clusters": 1,
        "min_cluster_count": 1,
        "max_cluster_count": 1,
        "running": 0,
        "queued": 0,
        "quiescing": 0,
        "auto_suspend": 300,
        "auto_resume": "true",
        "resumed_on": NOW - timedelta(seconds=90),
        "created_on": CREATED_ON,
    }
    row.update(overrides)
    return [row]


_BASE_LOG_RECORD_KEYS = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime"}


def _structured_fields(record: logging.LogRecord) -> dict[str, object]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _BASE_LOG_RECORD_KEYS
    }


def _expected_outcome_fields(
    *,
    attempt_id: str,
    outcome: str,
    error_type: str | None,
    errno: int | None,
    sqlstate: str | None,
    message: str | None,
) -> dict[str, object]:
    return {
        "event": "suspend_outcome",
        "attempt_id": attempt_id,
        "organization_id": "org-1",
        "warehouse_name": "WH1",
        "state": "STARTED",
        "uptime_seconds": 90.0,
        "running": 0,
        "queued": 0,
        "quiescing": 0,
        "started_clusters": 1,
        "min_cluster_count": 1,
        "max_cluster_count": 1,
        "resumed_on": (NOW - timedelta(seconds=90)).isoformat(),
        "outcome": outcome,
        "connector_error_type": error_type,
        "connector_errno": errno,
        "connector_sqlstate": sqlstate,
        "connector_message": message,
    }


def _run(
    store: TrackingStore,
    *,
    rows: list[dict] | None = None,
    suspend=None,
) -> CycleResult:
    if suspend is None:
        suspend = Mock(return_value=ACCEPTED_RESULT)
    return run_cycle(
        "org-1",
        rows=_rows() if rows is None else rows,
        store=store,
        config=CONFIG,
        now=NOW,
        suspend=suspend,
    )


def test_authorizes_immediately_before_suspend_and_audits_acceptance():
    store = TrackingStore()

    def suspend(name: str) -> SuspendResult:
        assert name == "WH1"
        assert store.operations == ["authorize_suspend"]
        store.operations.append("suspend")
        return ACCEPTED_RESULT

    result = _run(store, suspend=suspend)

    assert result is CycleResult.NORMAL
    assert store.operations == ["authorize_suspend", "suspend", "record_event"]
    assert store.authorizations == [("org-1", "WH1", CREATED_ON, UPDATED_AT)]
    [event] = store.events
    assert event == SavingsEvent(
        organization_id="org-1",
        warehouse_name="WH1",
        action="suspend",
        reason="idle",
        observed_state="STARTED",
        observed_running=0,
        observed_queued=0,
        observed_quiescing=0,
        observed_resumed_on=NOW - timedelta(seconds=90),
        observed_started_clusters=1,
        observed_min_cluster_count=1,
        observed_max_cluster_count=1,
        observed_at=NOW,
    )


def test_failed_or_stale_authorization_prevents_command():
    store = TrackingStore(authorized=False)
    suspend = Mock()

    assert _run(store, suspend=suspend) is CycleResult.NORMAL

    suspend.assert_not_called()
    assert store.operations == ["authorize_suspend"]
    assert store.events == []


def test_disable_after_authorization_does_not_cancel_in_flight_command():
    store = TrackingStore(disable_after_authorize=True)
    suspend = Mock(return_value=ACCEPTED_RESULT)

    _run(store, suspend=suspend)

    suspend.assert_called_once_with("WH1")


def test_audit_store_failure_after_acceptance_does_not_reissue():
    store = TrackingStore(audit_error=StoreError("unavailable"))
    suspend = Mock(return_value=ACCEPTED_RESULT)

    assert _run(store, suspend=suspend) is CycleResult.NORMAL

    suspend.assert_called_once_with("WH1")
    assert store.operations == ["authorize_suspend", "record_event"]


def test_non_store_audit_failure_propagates_without_reissuing_in_cycle():
    store = TrackingStore(audit_error=RuntimeError("programming bug"))
    suspend = Mock(return_value=ACCEPTED_RESULT)

    with pytest.raises(RuntimeError, match="programming bug"):
        _run(store, suspend=suspend)

    suspend.assert_called_once_with("WH1")


def test_unknown_90064_requests_backoff_with_actual_connector_metadata(caplog):
    store = TrackingStore()

    with caplog.at_level(logging.INFO, logger="auto_savings.engine"):
        result = _run(store, suspend=lambda _name: _unknown_result())

    assert result is CycleResult.RETRY_BACKOFF
    assert store.events == []
    outcome = next(record for record in caplog.records if record.event == "suspend_outcome")
    assert outcome.connector_error_type == "ProgrammingError"
    assert outcome.connector_errno == 90064
    assert outcome.connector_sqlstate == "57014"
    assert outcome.connector_message == "observed race"
    assert _structured_fields(outcome) == _expected_outcome_fields(
        attempt_id=outcome.attempt_id,
        outcome="unknown_idempotent",
        error_type="ProgrammingError",
        errno=90064,
        sqlstate="57014",
        message="observed race",
    )
    error_metric = next(
        record
        for record in caplog.records
        if getattr(record, "metric_name", None)
        == "auto_savings_suspend_error_total"
    )
    attempt_metric = next(
        record
        for record in caplog.records
        if getattr(record, "metric_name", None)
        == "auto_savings_suspend_attempt_total"
    )
    assert (error_metric.errno, error_metric.sqlstate) == (90064, "57014")
    assert error_metric.attempt_id == outcome.attempt_id
    assert _structured_fields(error_metric) == {
        "event": "metric",
        "metric_name": "auto_savings_suspend_error_total",
        "attempt_id": outcome.attempt_id,
        "errno": 90064,
        "sqlstate": "57014",
    }
    assert _structured_fields(attempt_metric) == {
        "event": "metric",
        "metric_name": "auto_savings_suspend_attempt_total",
        "attempt_id": outcome.attempt_id,
        "outcome": "unknown_idempotent",
    }


def test_ambiguous_connection_error_writes_no_event_and_propagates(caplog):
    store = TrackingStore()
    error = OperationalError(msg="secret connector detail", errno=250001, sqlstate="08001")

    with caplog.at_level(logging.ERROR, logger="auto_savings.engine"):
        with pytest.raises(OperationalError):
            _run(store, suspend=Mock(side_effect=error))

    assert store.events == []
    outcome = next(record for record in caplog.records if record.event == "suspend_outcome")
    assert outcome.connector_error_type == "OperationalError"
    assert outcome.connector_errno == 250001
    assert outcome.connector_sqlstate == "08001"
    assert "secret connector detail" not in outcome.getMessage()
    assert _structured_fields(outcome) == _expected_outcome_fields(
        attempt_id=outcome.attempt_id,
        outcome="error",
        error_type="OperationalError",
        errno=250001,
        sqlstate="08001",
        message="secret connector detail",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "SUSPENDED", "resumed_on": None},
        {"running": 1},
        {"running": "malformed"},
        {"created_on": None},
    ],
)
def test_ineligible_snapshots_never_authorize(overrides):
    store = TrackingStore()
    suspend = Mock()

    assert _run(store, rows=_rows(**overrides), suspend=suspend) is CycleResult.NORMAL

    assert store.authorizations == []
    assert store.deletions == []
    suspend.assert_not_called()


def test_missing_snapshot_and_disabled_enrollment_skip():
    missing = TrackingStore()
    disabled = TrackingStore(enrollment=_enrollment(enabled=False))
    suspend = Mock()

    _run(missing, rows=[], suspend=suspend)
    _run(disabled, suspend=suspend)

    assert missing.operations == []
    assert disabled.operations == []
    suspend.assert_not_called()


def test_multi_cluster_snapshot_is_eligible_and_audited():
    store = TrackingStore()

    _run(
        store,
        rows=_rows(
            started_clusters=3,
            min_cluster_count=1,
            max_cluster_count=4,
        ),
    )

    [event] = store.events
    assert (
        event.observed_started_clusters,
        event.observed_min_cluster_count,
        event.observed_max_cluster_count,
    ) == (3, 1, 4)


def test_unknown_cluster_counts_are_eligible_and_audited_as_null():
    store = TrackingStore()

    _run(
        store,
        rows=_rows(
            started_clusters="unknown",
            min_cluster_count=None,
            max_cluster_count=-1,
        ),
    )

    [event] = store.events
    assert (
        event.observed_started_clusters,
        event.observed_min_cluster_count,
        event.observed_max_cluster_count,
    ) == (None, None, None)


def test_identity_mismatch_guardedly_deletes_stale_enrollment():
    store = TrackingStore()
    suspend = Mock()

    _run(store, rows=_rows(created_on=CREATED_ON + timedelta(seconds=1)), suspend=suspend)

    assert store.deletions == [("org-1", "WH1", CREATED_ON, UPDATED_AT)]
    assert store.authorizations == []
    suspend.assert_not_called()


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_same_name_snapshots_fail_closed_regardless_of_order(
    reverse,
    caplog,
):
    store = TrackingStore()
    suspend = Mock()
    duplicate_rows = _rows() + _rows(
        running=1,
        created_on=CREATED_ON + timedelta(seconds=1),
    )
    if reverse:
        duplicate_rows.reverse()

    with caplog.at_level(logging.WARNING, logger="auto_savings.engine"):
        result = _run(store, rows=duplicate_rows, suspend=suspend)

    assert result is CycleResult.NORMAL
    assert store.authorizations == []
    assert store.deletions == []
    suspend.assert_not_called()
    ambiguity = next(
        record for record in caplog.records if record.event == "snapshot_ambiguous"
    )
    assert ambiguity.organization_id == "org-1"
    assert ambiguity.warehouse_name == "WH1"
    assert ambiguity.row_count == 2


def test_duplicate_name_fails_closed_while_other_unique_snapshot_proceeds():
    store = TrackingStore()
    store.enrollments = [
        _enrollment(),
        _enrollment(warehouse_name="WH2"),
    ]
    suspend = Mock(return_value=ACCEPTED_RESULT)
    rows = _rows() + _rows(running=1) + _rows(name="WH2")

    result = _run(store, rows=rows, suspend=suspend)

    assert result is CycleResult.NORMAL
    suspend.assert_called_once_with("WH2")
    assert [event.warehouse_name for event in store.events] == ["WH2"]
    assert [authorization[1] for authorization in store.authorizations] == ["WH2"]


def test_missing_enrollment_identity_fails_closed_without_delete_or_suspend():
    enrollment = _enrollment()
    object.__setattr__(enrollment, "warehouse_created_on", None)
    store = TrackingStore(enrollment=enrollment)
    suspend = Mock()

    _run(store, suspend=suspend)

    assert store.operations == []
    suspend.assert_not_called()


@pytest.mark.parametrize(
    "restart_rows",
    [
        _rows(state="SUSPENDING"),
        _rows(state="SUSPENDED", resumed_on=None),
        _rows(quiescing=1),
    ],
)
def test_restart_uses_only_fresh_ineligible_snapshot(restart_rows):
    first_store = TrackingStore()
    second_store = TrackingStore()
    first_suspend = Mock(return_value=ACCEPTED_RESULT)
    restarted_suspend = Mock(return_value=ACCEPTED_RESULT)

    _run(first_store, suspend=first_suspend)
    _run(second_store, rows=restart_rows, suspend=restarted_suspend)

    first_suspend.assert_called_once_with("WH1")
    restarted_suspend.assert_not_called()


def test_restart_may_idempotently_reissue_from_fresh_eligible_snapshot():
    first_suspend = Mock(return_value=ACCEPTED_RESULT)
    restarted_suspend = Mock(return_value=ACCEPTED_RESULT)

    _run(TrackingStore(), suspend=first_suspend)
    _run(TrackingStore(), suspend=restarted_suspend)

    first_suspend.assert_called_once_with("WH1")
    restarted_suspend.assert_called_once_with("WH1")


def test_snapshot_request_and_outcome_logs_share_attempt_uuid(caplog):
    with caplog.at_level(logging.INFO, logger="auto_savings.engine"):
        _run(TrackingStore())

    correlated = [
        record
        for record in caplog.records
        if record.event in {"snapshot", "suspend_request", "suspend_outcome"}
    ]
    assert [record.event for record in correlated] == [
        "snapshot",
        "suspend_request",
        "suspend_outcome",
    ]
    assert len({record.attempt_id for record in correlated}) == 1
    assert uuid.UUID(correlated[0].attempt_id)
    snapshot = correlated[0]
    assert snapshot.organization_id == "org-1"
    assert snapshot.warehouse_name == "WH1"
    assert snapshot.state == "STARTED"
    assert snapshot.uptime_seconds == 90.0
    assert snapshot.running == 0
    assert snapshot.queued == 0
    assert snapshot.quiescing == 0
    assert snapshot.started_clusters == 1
    assert snapshot.resumed_on == (NOW - timedelta(seconds=90)).isoformat()
    outcome = correlated[-1]
    assert _structured_fields(outcome) == _expected_outcome_fields(
        attempt_id=outcome.attempt_id,
        outcome="accepted",
        error_type=None,
        errno=None,
        sqlstate=None,
        message=None,
    )


def test_observability_uses_named_metric_events_with_required_labels(caplog):
    with caplog.at_level(logging.INFO, logger="auto_savings.engine"):
        _run(TrackingStore())

    metrics = {
        record.metric_name: record
        for record in caplog.records
        if record.event == "metric"
    }
    authorization = metrics["auto_savings_authorization_total"]
    attempt = metrics["auto_savings_suspend_attempt_total"]
    assert _structured_fields(authorization) == {
        "event": "metric",
        "metric_name": "auto_savings_authorization_total",
        "attempt_id": authorization.attempt_id,
        "result": "authorized",
    }
    assert _structured_fields(attempt) == {
        "event": "metric",
        "metric_name": "auto_savings_suspend_attempt_total",
        "attempt_id": attempt.attempt_id,
        "outcome": "accepted",
    }


def test_ambiguous_error_emits_named_error_and_attempt_metrics(caplog):
    error = OperationalError(msg="lost", errno=250001, sqlstate="08001")

    with caplog.at_level(logging.INFO, logger="auto_savings.engine"):
        with pytest.raises(OperationalError):
            _run(TrackingStore(), suspend=Mock(side_effect=error))

    metrics = [record for record in caplog.records if record.event == "metric"]
    error_metric = next(
        record
        for record in metrics
        if record.metric_name == "auto_savings_suspend_error_total"
    )
    attempt_metric = next(
        record
        for record in metrics
        if record.metric_name == "auto_savings_suspend_attempt_total"
    )
    assert (error_metric.errno, error_metric.sqlstate) == (250001, "08001")
    assert attempt_metric.outcome == "error"
    assert _structured_fields(error_metric) == {
        "event": "metric",
        "metric_name": "auto_savings_suspend_error_total",
        "attempt_id": error_metric.attempt_id,
        "errno": 250001,
        "sqlstate": "08001",
    }
    assert _structured_fields(attempt_metric) == {
        "event": "metric",
        "metric_name": "auto_savings_suspend_attempt_total",
        "attempt_id": attempt_metric.attempt_id,
        "outcome": "error",
    }
