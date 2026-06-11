from collections import defaultdict
from typing import Any


TOP_USER_COUNT = 100
OTHER_USER_NAME = "Other"


def bound_user_compute_rows(
    rows: list[dict[str, Any]],
    top_n: int = TOP_USER_COUNT,
) -> list[dict[str, Any]]:
    totals_by_user: dict[str, float] = defaultdict(float)
    for row in rows:
        totals_by_user[str(row["user_name"])] += float(
            row["credits_attributed_compute"]
        )

    if len(totals_by_user) <= top_n:
        return rows

    top_users = {
        user_name
        for user_name, _credits in sorted(
            totals_by_user.items(),
            key=lambda item: (-item[1], item[0]),
        )[:top_n]
    }
    bounded_rows = [row for row in rows if str(row["user_name"]) in top_users]

    other_credits_by_date_warehouse: dict[tuple[Any, str], float] = defaultdict(float)
    for row in rows:
        if str(row["user_name"]) in top_users:
            continue
        key = (row["usage_date"], str(row["warehouse_name"]))
        other_credits_by_date_warehouse[key] += float(row["credits_attributed_compute"])

    other_rows = [
        {
            "usage_date": usage_date,
            "user_name": OTHER_USER_NAME,
            "warehouse_name": warehouse_name,
            "credits_attributed_compute": credits,
        }
        for (usage_date, warehouse_name), credits in sorted(
            other_credits_by_date_warehouse.items(),
            key=lambda item: (item[0][0], item[0][1]),
        )
    ]

    return bounded_rows + other_rows
