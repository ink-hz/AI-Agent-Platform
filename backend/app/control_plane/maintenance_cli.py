from __future__ import annotations

import argparse
import json
import os
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.local_secrets import read_secret_file


_DATABASES = frozenset(
    {"agent_platform_control", "agent_platform_control_preview"}
)


class MaintenanceHealthError(RuntimeError):
    """Retention cannot run without confirmed host time and WAL health."""


class MaintenanceRepository:
    def __init__(
        self,
        maintenance_database_url: str,
        *,
        connect=psycopg.connect,
    ) -> None:
        try:
            database = conninfo_to_dict(maintenance_database_url).get("dbname")
        except (TypeError, ValueError, psycopg.Error):
            raise ValueError("control maintenance database DSN required") from None
        if database not in _DATABASES:
            raise ValueError("control maintenance database DSN required")
        self._database_url = maintenance_database_url
        self._connect = connect

    def purge_expired(
        self,
        *,
        time_health: str,
        wal_health: str,
    ) -> dict[str, int]:
        if time_health != "healthy" or wal_health != "healthy":
            raise MaintenanceHealthError("retention health is not confirmed")
        try:
            with self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=30000",
                row_factory=dict_row,
            ) as connection:
                row = connection.execute(
                    "select * from "
                    "platform_control.purge_expired_control_state()"
                ).fetchone()
                if row is None:
                    raise RuntimeError("control retention unavailable")
            return {
                "audit_events": row["audit_events"],
                "login_attempts": row["login_attempts"],
                "sessions": row["web_sessions"],
                "rate_buckets": row["rate_buckets"],
            }
        except psycopg.Error:
            raise RuntimeError("control retention unavailable") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control-plane maintenance")
    parser.add_argument("--database-url-file")
    subparsers = parser.add_subparsers(dest="command", required=True)
    purge = subparsers.add_parser(
        "purge-expired",
        help="Purge only data expired under fixed retention policy",
    )
    purge.add_argument(
        "--time-health",
        choices=("healthy", "unknown", "breached"),
        required=True,
    )
    purge.add_argument(
        "--wal-health",
        choices=("healthy", "unknown", "breached"),
        required=True,
    )
    return parser


def _database_url_file(namespace: argparse.Namespace) -> str:
    path = namespace.database_url_file or os.getenv(
        "PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE", ""
    )
    if not path:
        raise ValueError("control maintenance database URL file required")
    return path


def main(argv: list[str] | None = None) -> int:
    namespace = build_parser().parse_args(argv)
    repository = MaintenanceRepository(
        read_secret_file(_database_url_file(namespace))
    )
    result = repository.purge_expired(
        time_health=namespace.time_health,
        wal_health=namespace.wal_health,
    )
    print(json.dumps({"status": "ok", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
