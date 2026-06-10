from copy import deepcopy
from threading import RLock
from typing import Any
from uuid import UUID


class InMemoryAuditEventRecorder:
    def __init__(self) -> None:
        self._lock = RLock()
        self._events: list[dict[str, Any]] = []

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def record_event(
        self,
        event_name: str,
        *,
        organization_id: UUID | str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._events.append(
                {
                    "event_name": event_name,
                    "organization_id": str(organization_id)
                    if organization_id is not None
                    else None,
                    "payload": deepcopy(payload or {}),
                }
            )

    def record_org_event(
        self,
        event_name: str,
        *,
        organization_id: UUID | str | None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if organization_id is None:
            return
        self.record_event(event_name, organization_id=organization_id, payload=payload)

    def list_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._events)


audit_event_recorder = InMemoryAuditEventRecorder()
