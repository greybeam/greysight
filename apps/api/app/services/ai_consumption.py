"""Resilient fetch for AI/Cortex consumption detail (the deferred AI source).

Each branch is one standalone query against an account_usage usage-history
table, normalized to (usage_date, service_type, consumption_type, credits_used).
Branches run independently: a branch whose table does not exist or is not
authorized for the account is skipped, and the rest still load. A single static
UNION cannot do this, which is why the source is modeled as branch queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.services.parallel_source_runner import SourceJob, run_sources_parallel
from app.services.snowflake_client import SnowflakeObjectUnavailableError

ExecuteFn = Callable[[str, dict[str, Any]], list[dict[str, Any]]]

# Service types summed for the AI KPI (built from service_spend_daily, which is
# already loaded in the main run). Kept here so the KPI family and the detail
# branches stay defined side by side.
AI_KPI_SERVICE_TYPES: frozenset[str] = frozenset(
    {
        "CORTEX_AGENTS",
        "CORTEX_SEARCH",
        "CORTEX_CODE_SNOWSIGHT",
        "AI_SERVICES",
        "AI_FUNCTIONS",
        "AI_INFERENCE",
        "CORTEX_CODE_CLI",
        "SNOWFLAKE_INTELLIGENCE",
    }
)

_SQL_ROOT = Path(__file__).resolve().parents[4] / "sql"


@dataclass(frozen=True)
class AIConsumptionBranch:
    id: str
    service_type: str
    consumption_type: str  # representative label (cortex_search is dynamic in-SQL)
    table: str  # fully-qualified; must appear verbatim in the .sql file
    sql_path: str  # relative to the repo `sql/` root

    @property
    def sql(self) -> str:
        resolved = (_SQL_ROOT / self.sql_path).resolve()
        if not resolved.is_relative_to(_SQL_ROOT):
            raise ValueError(f"AI branch sql_path escapes sql/: {self.sql_path}")
        return resolved.read_text(encoding="utf-8")


AI_CONSUMPTION_BRANCHES: tuple[AIConsumptionBranch, ...] = (
    AIConsumptionBranch(
        "cortex_agents",
        "CORTEX_AGENTS",
        "CORTEX_AGENTS",
        "snowflake.account_usage.cortex_agent_usage_history",
        "snowflake/ai/cortex_agents.sql",
    ),
    AIConsumptionBranch(
        "cortex_ai_functions",
        "AI_FUNCTIONS",
        "CORTEX_AI_FUNCTIONS",
        "snowflake.account_usage.cortex_ai_functions_usage_history",
        "snowflake/ai/cortex_ai_functions.sql",
    ),
    AIConsumptionBranch(
        "cortex_analyst",
        "AI_SERVICES",
        "CORTEX_ANALYST",
        "snowflake.account_usage.cortex_analyst_usage_history",
        "snowflake/ai/cortex_analyst.sql",
    ),
    AIConsumptionBranch(
        "cortex_search",
        "CORTEX_SEARCH",
        "CORTEX_SEARCH",
        "snowflake.account_usage.cortex_search_daily_usage_history",
        "snowflake/ai/cortex_search.sql",
    ),
    AIConsumptionBranch(
        "cortex_document_processing",
        "AI_SERVICES",
        "CORTEX_DOCUMENT_PROCESSING",
        "snowflake.account_usage.cortex_document_processing_usage_history",
        "snowflake/ai/cortex_document_processing.sql",
    ),
    AIConsumptionBranch(
        "cortex_fine_tuning",
        "AI_SERVICES",
        "CORTEX_FINE_TUNING",
        "snowflake.account_usage.cortex_fine_tuning_usage_history",
        "snowflake/ai/cortex_fine_tuning.sql",
    ),
    AIConsumptionBranch(
        "cortex_code_snowsight",
        "CORTEX_CODE_SNOWSIGHT",
        "CORTEX_CODE_SNOWSIGHT",
        "snowflake.account_usage.cortex_code_snowsight_usage_history",
        "snowflake/ai/cortex_code_snowsight.sql",
    ),
    AIConsumptionBranch(
        "cortex_code_cli",
        "CORTEX_CODE_CLI",
        "CORTEX_CODE_CLI",
        "snowflake.account_usage.cortex_code_cli_usage_history",
        "snowflake/ai/cortex_code_cli.sql",
    ),
    AIConsumptionBranch(
        "snowflake_intelligence",
        "SNOWFLAKE_INTELLIGENCE",
        "SNOWFLAKE_INTELLIGENCE",
        "snowflake.account_usage.snowflake_intelligence_usage_history",
        "snowflake/ai/snowflake_intelligence.sql",
    ),
    AIConsumptionBranch(
        "ai_inference",
        "AI_INFERENCE",
        "AI_INFERENCE",
        "snowflake.account_usage.metering_daily_history",
        "snowflake/ai/ai_inference.sql",
    ),
)


def fetch_ai_consumption_daily(
    execute: ExecuteFn,
    *,
    window_days: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run each branch concurrently; skip branches whose table is unavailable.

    Returns (rows, skipped_branch_ids). ONLY a SnowflakeObjectUnavailableError
    (the table genuinely does not exist or is not authorized for this account)
    is collapsed to a skipped branch. Any other SnowflakeQueryError (connection
    failure, timeout, SQL regression) — and any other exception — PROPAGATES to
    the caller so the deferred AI source fails loudly instead of reporting a
    partial/empty success. Rows are returned in deterministic branch order.
    """
    bind_params = {"window_days": window_days}
    jobs = [
        SourceJob(branch.id, branch.sql, bind_params)
        for branch in AI_CONSUMPTION_BRANCHES
    ]
    outcomes = run_sources_parallel(
        jobs, execute, unavailable_exc=SnowflakeObjectUnavailableError
    )
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for branch in AI_CONSUMPTION_BRANCHES:  # deterministic order
        outcome = outcomes[branch.id]
        if outcome.available:
            # rows is always a list when available=True; assert narrows the type
            assert outcome.rows is not None
            rows.extend(outcome.rows)
        else:
            skipped.append(branch.id)
    return rows, skipped
