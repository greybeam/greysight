from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_ROOT_PATH = Path(__file__).resolve().parents[4]
_REGISTRY_PATH = _ROOT_PATH / "sql" / "dashboard_sources.yml"
_SQL_ROOT = _ROOT_PATH / "sql"


@dataclass(frozen=True)
class DashboardSource:
    id: str
    kind: str
    sql_path: Path
    grain: tuple[str, ...]
    resolved_sql_path: Path

    @property
    def sql(self) -> str:
        return self.resolved_sql_path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class DerivedDataset:
    id: str
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class DashboardRegistry:
    root_path: Path
    sources: dict[str, DashboardSource]
    derived_datasets: dict[str, DerivedDataset]


def load_dashboard_registry() -> DashboardRegistry:
    raw_registry = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw_registry, dict):
        raise ValueError("Dashboard registry must be a mapping")

    sources = _load_sources(raw_registry.get("sources"))
    derived_datasets = _load_derived_datasets(raw_registry.get("derived_datasets"))
    _validate_derived_dependencies(sources, derived_datasets)

    return DashboardRegistry(
        root_path=_ROOT_PATH,
        sources=sources,
        derived_datasets=derived_datasets,
    )


def _load_sources(raw_sources: Any) -> dict[str, DashboardSource]:
    if not isinstance(raw_sources, list):
        raise ValueError("Dashboard registry sources must be a list")

    sources: dict[str, DashboardSource] = {}
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            raise ValueError("Dashboard registry source entries must be mappings")

        source_id = _required_str(raw_source, "id")
        sql_path = Path(_required_str(raw_source, "sql_path"))
        resolved_sql_path = (_ROOT_PATH / sql_path).resolve()
        if sql_path.is_absolute() or not resolved_sql_path.is_relative_to(_SQL_ROOT):
            raise ValueError(f"SQL path for {source_id} must stay under sql/")
        if source_id in sources:
            raise ValueError(f"Duplicate dashboard source: {source_id}")

        sources[source_id] = DashboardSource(
            id=source_id,
            kind=_required_str(raw_source, "kind"),
            sql_path=sql_path,
            grain=_required_str_tuple(raw_source, "grain"),
            resolved_sql_path=resolved_sql_path,
        )

    return sources


def _load_derived_datasets(raw_datasets: Any) -> dict[str, DerivedDataset]:
    if not isinstance(raw_datasets, list):
        raise ValueError("Dashboard registry derived_datasets must be a list")

    datasets: dict[str, DerivedDataset] = {}
    for raw_dataset in raw_datasets:
        if not isinstance(raw_dataset, dict):
            raise ValueError(
                "Dashboard registry derived dataset entries must be mappings"
            )

        dataset_id = _required_str(raw_dataset, "id")
        if dataset_id in datasets:
            raise ValueError(f"Duplicate derived dataset: {dataset_id}")

        datasets[dataset_id] = DerivedDataset(
            id=dataset_id,
            depends_on=_required_str_tuple(raw_dataset, "depends_on"),
        )

    return datasets


def _validate_derived_dependencies(
    sources: dict[str, DashboardSource],
    derived_datasets: dict[str, DerivedDataset],
) -> None:
    valid_dataset_ids = set(sources) | set(derived_datasets)
    for dataset in derived_datasets.values():
        missing_dependencies = [
            dependency
            for dependency in dataset.depends_on
            if dependency not in valid_dataset_ids
        ]
        if missing_dependencies:
            missing = ", ".join(sorted(missing_dependencies))
            raise ValueError(
                f"Derived dataset {dataset.id} depends on unknown source(s): {missing}"
            )


def _required_str(raw_value: dict[str, Any], key: str) -> str:
    value = raw_value.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Dashboard registry field {key} must be a non-empty string")
    return value


def _required_str_tuple(raw_value: dict[str, Any], key: str) -> tuple[str, ...]:
    value = raw_value.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"Dashboard registry field {key} must be a non-empty list")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"Dashboard registry field {key} must only contain strings")
    return tuple(value)
