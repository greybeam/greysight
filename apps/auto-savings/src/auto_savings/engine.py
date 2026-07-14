from __future__ import annotations

import logging
import uuid
from datetime import datetime
from enum import Enum, auto
from typing import Callable

from auto_savings.config import WorkerConfig
from auto_savings.decision import should_suspend
from auto_savings.snowflake_session import (
    ConnectorErrorMetadata,
    SuspendOutcome,
    SuspendResult,
    connector_error_metadata,
)
from auto_savings.store import EnrollmentRow, SavingsEvent, Store, StoreError
from auto_savings.warehouse_snapshot import (
    WarehouseSnapshot,
    parse_warehouses,
    uptime_seconds,
)

logger = logging.getLogger(__name__)

SuspendWarehouse = Callable[[str], SuspendResult]


class CycleResult(Enum):
    NORMAL = auto()
    RETRY_BACKOFF = auto()


def _observation_fields(
    org_id: str,
    snapshot: WarehouseSnapshot,
    *,
    attempt_id: str,
    now: datetime,
) -> dict[str, object]:
    return {
        "attempt_id": attempt_id,
        "organization_id": org_id,
        "warehouse_name": snapshot.name,
        "state": snapshot.state,
        "uptime_seconds": uptime_seconds(snapshot, now=now),
        "running": snapshot.running,
        "queued": snapshot.queued,
        "quiescing": snapshot.quiescing,
        "started_clusters": snapshot.started_clusters,
        "min_cluster_count": snapshot.min_cluster_count,
        "max_cluster_count": snapshot.max_cluster_count,
        "resumed_on": (
            snapshot.resumed_on.isoformat()
            if snapshot.resumed_on is not None
            else None
        ),
    }


def _log_metric(metric_name: str, *, attempt_id: str, **labels: object) -> None:
    logger.info(
        "Automated Savings metric",
        extra={
            "event": "metric",
            "metric_name": metric_name,
            "attempt_id": attempt_id,
            **labels,
        },
    )


def _correlate_snapshots(
    org_id: str,
    snapshots: list[WarehouseSnapshot],
    *,
    now: datetime,
) -> tuple[dict[str, WarehouseSnapshot], dict[str, dict[str, object]]]:
    grouped: dict[str, list[WarehouseSnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(snapshot.name, []).append(snapshot)

    unique: dict[str, WarehouseSnapshot] = {}
    observations: dict[str, dict[str, object]] = {}
    for name, matching_snapshots in grouped.items():
        if len(matching_snapshots) != 1:
            logger.warning(
                "Automated Savings ambiguous warehouse snapshot",
                extra={
                    "event": "snapshot_ambiguous",
                    "attempt_id": str(uuid.uuid4()),
                    "organization_id": org_id,
                    "warehouse_name": name,
                    "row_count": len(matching_snapshots),
                },
            )
            continue

        snapshot = matching_snapshots[0]
        observation = _observation_fields(
            org_id,
            snapshot,
            attempt_id=str(uuid.uuid4()),
            now=now,
        )
        unique[name] = snapshot
        observations[name] = observation
        logger.info(
            "Automated Savings warehouse snapshot",
            extra={
                "event": "snapshot",
                **observation,
            },
        )
    return unique, observations


def _log_suspend_outcome(
    level: int,
    observation: dict[str, object],
    *,
    outcome: str,
    metadata: ConnectorErrorMetadata | None,
) -> None:
    logger.log(
        level,
        "Automated Savings suspend outcome",
        extra={
            "event": "suspend_outcome",
            **observation,
            "outcome": outcome,
            "connector_error_type": metadata.error_type if metadata else None,
            "connector_errno": metadata.errno if metadata else None,
            "connector_sqlstate": metadata.sqlstate if metadata else None,
            "connector_message": metadata.message if metadata else None,
        },
    )


def _attempt_id(observation: dict[str, object]) -> str:
    attempt_id = observation["attempt_id"]
    assert isinstance(attempt_id, str)
    return attempt_id


def _log_suspend_metrics(
    attempt_id: str,
    *,
    outcome: str,
    metadata: ConnectorErrorMetadata | None = None,
) -> None:
    _log_metric(
        "auto_savings_suspend_attempt_total",
        attempt_id=attempt_id,
        outcome=outcome,
    )
    if metadata is not None:
        _log_metric(
            "auto_savings_suspend_error_total",
            attempt_id=attempt_id,
            errno=metadata.errno,
            sqlstate=metadata.sqlstate,
        )


def _classify_suspend_result(
    result: SuspendResult,
    *,
    observation: dict[str, object],
) -> CycleResult:
    attempt_id = _attempt_id(observation)
    if result.outcome is SuspendOutcome.UNKNOWN_IDEMPOTENT:
        metadata = result.connector_error
        assert metadata is not None
        _log_suspend_outcome(
            logging.WARNING,
            observation,
            outcome="unknown_idempotent",
            metadata=metadata,
        )
        _log_suspend_metrics(
            attempt_id,
            outcome="unknown_idempotent",
            metadata=metadata,
        )
        return CycleResult.RETRY_BACKOFF

    if result.outcome is not SuspendOutcome.ACCEPTED:
        raise RuntimeError("unexpected suspend outcome")
    _log_suspend_outcome(
        logging.INFO,
        observation,
        outcome="accepted",
        metadata=None,
    )
    _log_suspend_metrics(attempt_id, outcome="accepted")
    return CycleResult.NORMAL


def _suspend_once(
    warehouse_name: str,
    *,
    suspend: SuspendWarehouse,
    observation: dict[str, object],
) -> CycleResult:
    attempt_id = _attempt_id(observation)
    logger.info(
        "Automated Savings suspend request",
        extra={"event": "suspend_request", **observation},
    )
    try:
        result = suspend(warehouse_name)
    except Exception as exc:
        metadata = connector_error_metadata(exc)
        _log_suspend_outcome(
            logging.ERROR,
            observation,
            outcome="error",
            metadata=metadata,
        )
        _log_suspend_metrics(attempt_id, outcome="error", metadata=metadata)
        raise
    return _classify_suspend_result(
        result,
        observation=observation,
    )


def _record_accepted_event(
    org_id: str,
    snapshot: WarehouseSnapshot,
    *,
    now: datetime,
    store: Store,
    observation: dict[str, object],
) -> None:
    assert snapshot.resumed_on is not None
    event = SavingsEvent(
        organization_id=org_id,
        warehouse_name=snapshot.name,
        action="suspend",
        reason="idle",
        observed_state=snapshot.state,
        observed_running=snapshot.running,
        observed_queued=snapshot.queued,
        observed_quiescing=snapshot.quiescing,
        observed_resumed_on=snapshot.resumed_on,
        observed_started_clusters=snapshot.started_clusters,
        observed_min_cluster_count=snapshot.min_cluster_count,
        observed_max_cluster_count=snapshot.max_cluster_count,
        observed_at=now,
    )
    try:
        store.record_event(event)
    except StoreError:
        logger.warning(
            "Automated Savings event audit failed after accepted suspend",
            extra={"event": "audit_error", **observation},
        )


def _eligible_created_on(
    org_id: str,
    enrollment: EnrollmentRow,
    snapshot: WarehouseSnapshot,
    *,
    store: Store,
    config: WorkerConfig,
    now: datetime,
) -> datetime | None:
    enrolled_created_on = enrollment.warehouse_created_on
    if enrolled_created_on is None or snapshot.created_on is None:
        return None
    if snapshot.created_on != enrolled_created_on:
        store.delete_stale_enrollment(
            org_id,
            enrollment.warehouse_name,
            warehouse_created_on=enrolled_created_on,
            enrollment_updated_at=enrollment.updated_at,
        )
        return None
    if not enrollment.enabled:
        return None
    if not should_suspend(
        snapshot,
        now=now,
        uptime_floor_seconds=config.uptime_floor_seconds,
        enrolled_created_on=enrolled_created_on,
    ):
        return None
    return snapshot.created_on


def run_cycle(
    org_id: str,
    *,
    rows: list[dict],
    store: Store,
    config: WorkerConfig,
    now: datetime,
    suspend: SuspendWarehouse,
) -> CycleResult:
    """Evaluate one immutable warehouse snapshot and issue direct suspends.

    Authorization is the final state read before each Snowflake command. An
    authorization that succeeds owns the in-flight command: a later disable
    takes effect on the next authorization and does not cancel that command.
    """
    snapshot_by_name, observation_by_name = _correlate_snapshots(
        org_id,
        parse_warehouses(rows, now=now),
        now=now,
    )

    enrollments = store.list_enrollments(org_id)
    backoff_required = False
    for enrollment in enrollments:
        snapshot = snapshot_by_name.get(enrollment.warehouse_name)
        if snapshot is None:
            continue
        observation = observation_by_name[snapshot.name]
        attempt_id = _attempt_id(observation)

        created_on = _eligible_created_on(
            org_id,
            enrollment,
            snapshot,
            store=store,
            config=config,
            now=now,
        )
        if created_on is None:
            continue

        authorized = store.authorize_suspend(
            org_id,
            enrollment.warehouse_name,
            warehouse_created_on=created_on,
            enrollment_updated_at=enrollment.updated_at,
        )
        _log_metric(
            "auto_savings_authorization_total",
            attempt_id=attempt_id,
            result="authorized" if authorized else "rejected",
        )
        if not authorized:
            continue

        result = _suspend_once(
            enrollment.warehouse_name,
            suspend=suspend,
            observation=observation,
        )
        if result is CycleResult.RETRY_BACKOFF:
            backoff_required = True
            continue
        _record_accepted_event(
            org_id,
            snapshot,
            now=now,
            store=store,
            observation=observation,
        )

    return CycleResult.RETRY_BACKOFF if backoff_required else CycleResult.NORMAL
