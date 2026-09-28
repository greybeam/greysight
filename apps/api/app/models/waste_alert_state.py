"""Dedup state for the waste alert digest.

One row per warehouse. Written after a warehouse's item has been delivered
in a digest. Read on the next run to decide whether to re-alert.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class WasteAlertState(BaseModel):
    warehouse_name: str = Field(..., min_length=1)
    last_alerted_at: datetime
    last_projected_monthly_idle_spend: float = Field(..., ge=0.0)

    model_config = {"frozen": True}
