from __future__ import annotations

import math
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
    # Per-request timeout for the worker's Supabase (httpx) store calls. Bounds
    # EVERY blocking store op the pool thread can run, so an abandoned timed-out
    # tick is guaranteed to terminate promptly (Snowflake is bounded by
    # socket_timeout_seconds; Supabase by this) and the guaranteed drain in
    # run_tenant_once cannot hang.
    store_timeout_seconds: float = 5.0
    uptime_floor_seconds: int = 62
    tenant_refresh_seconds: int = 30
    num_replicas: int = 1
    replica_index: int = 0
    max_workers: int = 64
    auth_required: bool = True
    query_timeout_seconds: int = 120

    # Every interval/duration must be finite and strictly positive — a zero,
    # negative, NaN, or infinite value here means a mistyped/mis-set env var
    # silently turns into a busy-loop, an instantly-expiring guard, or a
    # never-firing one (findings #9 / #16).
    _INTERVAL_FIELDS = (
        "poll_interval_seconds",
        "poll_timeout_seconds",
        "socket_timeout_seconds",
        "store_timeout_seconds",
        "uptime_floor_seconds",
        "tenant_refresh_seconds",
        "query_timeout_seconds",
    )

    def __post_init__(self) -> None:
        # The socket read timeout MUST fire before the watchdog, or the watchdog trips
        # while the pool thread is still blocked → thread leak (Codex R2.1 MED).
        if self.socket_timeout_seconds >= self.poll_timeout_seconds:
            raise ValueError(
                f"socket_timeout_seconds ({self.socket_timeout_seconds}) must be < "
                f"poll_timeout_seconds ({self.poll_timeout_seconds})"
            )
        for name in self._INTERVAL_FIELDS:
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(
                    f"{name} ({value!r}) must be a finite, strictly positive number"
                )
        if self.num_replicas < 1:
            raise ValueError(f"num_replicas ({self.num_replicas}) must be >= 1")
        if self.max_workers < 1:
            raise ValueError(f"max_workers ({self.max_workers}) must be >= 1")
        if not (0 <= self.replica_index < self.num_replicas):
            raise ValueError(
                f"replica_index ({self.replica_index}) must satisfy "
                f"0 <= replica_index < num_replicas ({self.num_replicas})"
            )

    @classmethod
    def from_environment(cls) -> "WorkerConfig":
        return cls(
            supabase_url=os.environ.get("SUPABASE_URL", ""),
            supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            poll_interval_seconds=_float("AUTO_SAVINGS_POLL_INTERVAL_SECONDS", 3.0),
            poll_timeout_seconds=_float("AUTO_SAVINGS_POLL_TIMEOUT_SECONDS", 20.0),
            socket_timeout_seconds=_int("AUTO_SAVINGS_SOCKET_TIMEOUT_SECONDS", 15),
            store_timeout_seconds=_float("AUTO_SAVINGS_STORE_TIMEOUT_SECONDS", 5.0),
            uptime_floor_seconds=_int("AUTO_SAVINGS_UPTIME_FLOOR_SECONDS", 62),
            tenant_refresh_seconds=_int("AUTO_SAVINGS_TENANT_REFRESH_SECONDS", 30),
            num_replicas=_int("AUTO_SAVINGS_NUM_REPLICAS", 1),
            replica_index=_int("AUTO_SAVINGS_REPLICA_INDEX", 0),
            max_workers=_int("AUTO_SAVINGS_MAX_WORKERS", 64),
            query_timeout_seconds=_int("GREYSIGHT_QUERY_TIMEOUT_SECONDS", 120),
        )
