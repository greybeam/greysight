from __future__ import annotations

from datetime import datetime

from auto_savings.warehouse_snapshot import WarehouseSnapshot, uptime_seconds


def should_suspend(
    snapshot: WarehouseSnapshot,
    *,
    now: datetime,
    uptime_floor_seconds: int,
    enrolled_created_on: datetime,
) -> bool:
    if snapshot.type != "STANDARD" or snapshot.state != "STARTED":
        return False
    if not snapshot.activity_valid or not snapshot.quiescing_valid:
        return False
    if snapshot.created_on is None or snapshot.created_on != enrolled_created_on:
        return False
    uptime = uptime_seconds(snapshot, now=now)
    if uptime is None or uptime < uptime_floor_seconds:
        return False
    if snapshot.running != 0 or snapshot.queued != 0:
        return False
    if snapshot.quiescing != 0:
        return False
    if not snapshot.auto_resume:
        return False
    return True
