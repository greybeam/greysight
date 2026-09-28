from __future__ import annotations

from pathlib import Path

from app.models.waste_alert_state import WasteAlertState

_WASTE_ALERT_DDL = """
CREATE TABLE IF NOT EXISTS waste_alert_state (
    warehouse_name TEXT PRIMARY KEY,
    last_alerted_at TIMESTAMP NOT NULL,
    last_projected_monthly_idle_spend DOUBLE NOT NULL
);
"""


class DuckDBWasteAlertStore:
    """File-backed waste-alert state. duckdb is imported lazily so the API
    venv does not need it unless ``WASTE_ALERT_BACKEND=duckdb``.
    """

    def __init__(self, path: str) -> None:
        import duckdb

        self._conn = duckdb.connect(str(Path(path)))
        self._conn.execute(_WASTE_ALERT_DDL)

    async def get_waste_alert_state(
        self, warehouse_name: str
    ) -> WasteAlertState | None:
        row = self._conn.execute(
            """
            SELECT warehouse_name,
                   last_alerted_at,
                   last_projected_monthly_idle_spend
              FROM waste_alert_state
             WHERE warehouse_name = ?
            """,
            [warehouse_name],
        ).fetchone()
        if row is None:
            return None
        return WasteAlertState(
            warehouse_name=row[0],
            last_alerted_at=row[1],
            last_projected_monthly_idle_spend=row[2],
        )

    async def set_waste_alert_state(self, state: WasteAlertState) -> None:
        self._conn.execute(
            """
            INSERT INTO waste_alert_state (
                warehouse_name,
                last_alerted_at,
                last_projected_monthly_idle_spend
            ) VALUES (?, ?, ?)
            ON CONFLICT (warehouse_name) DO UPDATE SET
                last_alerted_at = EXCLUDED.last_alerted_at,
                last_projected_monthly_idle_spend =
                    EXCLUDED.last_projected_monthly_idle_spend
            """,
            [
                state.warehouse_name,
                state.last_alerted_at,
                state.last_projected_monthly_idle_spend,
            ],
        )
