"""Local Auto Savings CLI: drive the same state transitions the web app drives,
without Supabase.

Subcommands mirror the web app's actions:

    agree                              set agreed_at (once)
    enable-global / disable-global     org-wide switch
    enroll --warehouse WH --warehouse-created-on ISO   upsert enrollment
    enable --warehouse WH              per-warehouse switch on
    disable --warehouse WH             per-warehouse switch off
    events                             tail the local audit log

Every subcommand exits 0 on success, 2 on a user error (bad args), 1 on an
unexpected StoreError.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import duckdb

from auto_savings.config import WorkerConfig
from auto_savings.local_store import LOCAL_ORG_ID, LocalDuckDBStore
from auto_savings.store import StoreError


def _iso_arg(raw: str) -> datetime:
    text = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "warehouse-created-on must include a UTC offset, e.g. "
            "2026-06-01T00:00:00Z"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="greysight-auto-savings",
        description=(
            "Local Auto Savings CLI (DuckDB backend). "
            "Requires AUTO_SAVINGS_BACKEND=duckdb and "
            "AUTO_SAVINGS_DUCKDB_PATH set."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("agree", help="Record feature agreement (once).")
    subparsers.add_parser("enable-global", help="Enable org-wide switch.")
    subparsers.add_parser("disable-global", help="Disable org-wide switch.")
    subparsers.add_parser("events", help="Print recent audit events.")

    enroll = subparsers.add_parser(
        "enroll",
        help="Upsert a warehouse enrollment (disabled by default).",
    )
    enroll.add_argument("--warehouse", required=True)
    enroll.add_argument(
        "--warehouse-created-on",
        required=True,
        type=_iso_arg,
        help="ISO-8601 timestamp with UTC offset (e.g. 2026-06-01T00:00:00Z).",
    )

    for verb in ("enable", "disable"):
        sub = subparsers.add_parser(
            verb, help=f"{verb.capitalize()} a specific warehouse enrollment."
        )
        sub.add_argument("--warehouse", required=True)

    return parser


def _open_store(config: WorkerConfig) -> LocalDuckDBStore:
    if config.backend != "duckdb" or config.duckdb_path is None:
        raise SystemExit(
            "The local CLI requires AUTO_SAVINGS_BACKEND=duckdb and "
            "AUTO_SAVINGS_DUCKDB_PATH to be set."
        )
    return LocalDuckDBStore(config.duckdb_path)


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = WorkerConfig.from_environment()
    store = _open_store(config)

    try:
        if args.command == "agree":
            store.set_agreed()
            print("agreed")
        elif args.command == "enable-global":
            store.set_global_enabled(True)
            print("global-enabled")
        elif args.command == "disable-global":
            store.set_global_enabled(False)
            print("global-disabled")
        elif args.command == "enroll":
            store.upsert_warehouse(args.warehouse, args.warehouse_created_on)
            print(f"enrolled {args.warehouse}")
        elif args.command == "enable":
            if not store.set_warehouse_enabled(args.warehouse, True):
                print(
                    f"no enrollment for {args.warehouse} — run "
                    "'enroll' first",
                    file=sys.stderr,
                )
                return 2
            print(f"enabled {args.warehouse}")
        elif args.command == "disable":
            if not store.set_warehouse_enabled(args.warehouse, False):
                print(
                    f"no enrollment for {args.warehouse}", file=sys.stderr,
                )
                return 2
            print(f"disabled {args.warehouse}")
        elif args.command == "events":
            assert config.duckdb_path is not None
            for row in _read_events(config.duckdb_path):
                print(row)
    except StoreError as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


def _read_events(path: Path) -> list[str]:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT created_at, warehouse_name, observed_state,
                   observed_running, observed_queued
              FROM automated_savings_events
             WHERE organization_id = ?
             ORDER BY created_at DESC
             LIMIT 20
            """,
            (LOCAL_ORG_ID,),
        ).fetchall()
    finally:
        conn.close()
    return [
        f"{created_at.isoformat()}  {warehouse:<24}  state={state:<8} "
        f"running={running} queued={queued}"
        for created_at, warehouse, state, running, queued in rows
    ]


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
