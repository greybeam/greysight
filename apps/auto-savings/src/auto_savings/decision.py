from __future__ import annotations

from datetime import datetime

from auto_savings.warehouse_snapshot import WarehouseSnapshot, uptime_seconds


def should_force_suspend(
    snapshot: WarehouseSnapshot,
    *,
    now: datetime,
    uptime_floor_seconds: int,
    in_cooldown: bool,
    is_drifted: bool,
    has_outstanding_intent: bool,
) -> bool:
    if snapshot.type != "STANDARD":
        return False
    if snapshot.state != "STARTED":
        return False
    if snapshot.started_clusters != snapshot.min_cluster_count:
        return False
    uptime = uptime_seconds(snapshot, now=now)
    if uptime is None or uptime < uptime_floor_seconds:
        return False
    if snapshot.running != 0 or snapshot.queued != 0:
        return False
    if not snapshot.auto_resume:
        return False
    if in_cooldown or is_drifted or has_outstanding_intent:
        return False
    return True
