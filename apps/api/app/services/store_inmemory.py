from __future__ import annotations

from app.models.waste_alert_state import WasteAlertState


class InMemoryStore:
    def __init__(self) -> None:
        self._waste_alert_state: dict[str, WasteAlertState] = {}

    async def get_waste_alert_state(
        self, warehouse_name: str
    ) -> WasteAlertState | None:
        return self._waste_alert_state.get(warehouse_name)

    async def set_waste_alert_state(self, state: WasteAlertState) -> None:
        self._waste_alert_state[state.warehouse_name] = state
