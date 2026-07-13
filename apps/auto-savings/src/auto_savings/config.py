from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class WorkerConfig:
    supabase_url: str
    supabase_service_role_key: str
    poll_interval_seconds: float = 3.0
    poll_timeout_seconds: float = 20.0
    socket_timeout_seconds: int = 15
    cooldown_seconds: int = 60
    uptime_floor_seconds: int = 62
    max_intent_hold_ticks: int = 8
    orphan_grace_seconds: int = 120
    tenant_refresh_seconds: int = 30
    num_replicas: int = 1
    replica_index: int = 0
    max_workers: int = 64
    auth_required: bool = True
    query_timeout_seconds: int = 120

    def __post_init__(self) -> None:
        # The socket read timeout MUST fire before the watchdog, or the watchdog trips
        # while the pool thread is still blocked → thread leak (Codex R2.1 MED).
        if self.socket_timeout_seconds >= self.poll_timeout_seconds:
            raise ValueError(
                f"socket_timeout_seconds ({self.socket_timeout_seconds}) must be < "
                f"poll_timeout_seconds ({self.poll_timeout_seconds})"
            )

    @classmethod
    def from_environment(cls) -> "WorkerConfig":
        return cls(
            supabase_url=os.environ.get("SUPABASE_URL", ""),
            supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            poll_interval_seconds=_float("AUTO_SAVINGS_POLL_INTERVAL_SECONDS", 3.0),
            poll_timeout_seconds=_float("AUTO_SAVINGS_POLL_TIMEOUT_SECONDS", 20.0),
            socket_timeout_seconds=_int("AUTO_SAVINGS_SOCKET_TIMEOUT_SECONDS", 15),
            cooldown_seconds=_int("AUTO_SAVINGS_COOLDOWN_SECONDS", 60),
            uptime_floor_seconds=_int("AUTO_SAVINGS_UPTIME_FLOOR_SECONDS", 62),
            max_intent_hold_ticks=_int("AUTO_SAVINGS_MAX_INTENT_HOLD_TICKS", 8),
            orphan_grace_seconds=_int("AUTO_SAVINGS_ORPHAN_GRACE_SECONDS", 120),
            tenant_refresh_seconds=_int("AUTO_SAVINGS_TENANT_REFRESH_SECONDS", 30),
            num_replicas=_int("AUTO_SAVINGS_NUM_REPLICAS", 1),
            replica_index=_int("AUTO_SAVINGS_REPLICA_INDEX", 0),
            max_workers=_int("AUTO_SAVINGS_MAX_WORKERS", 64),
            query_timeout_seconds=_int("GREYSIGHT_QUERY_TIMEOUT_SECONDS", 120),
        )
