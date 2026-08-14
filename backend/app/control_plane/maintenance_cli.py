from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.local_secrets import read_secret_file
from .crypto import IdentityKeyring
from .dsn import validate_control_dsn


class MaintenanceHealthError(RuntimeError):
    """Retention cannot run without confirmed host time and WAL health."""


class MaintenanceRepository:
    RATE_BUCKET_BATCH_SIZE = 100
    RATE_BUCKET_MAX_BATCHES = 10
    RATE_BUCKET_TIME_BUDGET_SECONDS = 5.0

    def __init__(
        self,
        maintenance_database_url: str,
        *,
        connect=psycopg.connect,
    ) -> None:
        validate_control_dsn(maintenance_database_url, purpose="maintenance")
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
                environment = validate_control_dsn(
                    self._database_url, purpose="maintenance"
                ).environment
                deleted_rate_buckets = 0
                deadline = time.monotonic() + self.RATE_BUCKET_TIME_BUDGET_SECONDS
                for _ in range(self.RATE_BUCKET_MAX_BATCHES):
                    if time.monotonic() >= deadline:
                        break
                    batch = connection.execute(
                        "select platform_control.maintain_auth_rate_buckets("
                        "%s,%s,%s,%s) as deleted",
                        (
                            environment,
                            1,
                            86_400,
                            self.RATE_BUCKET_BATCH_SIZE,
                        ),
                    ).fetchone()
                    if batch is None:
                        raise RuntimeError("control retention unavailable")
                    count = batch["deleted"]
                    deleted_rate_buckets += count
                    if count < self.RATE_BUCKET_BATCH_SIZE:
                        break
            return {
                "audit_events": row["audit_events"],
                "login_attempts": row["login_attempts"],
                "sessions": row["web_sessions"],
                "rate_buckets": deleted_rate_buckets,
            }
        except psycopg.Error:
            raise RuntimeError("control retention unavailable") from None

    def sync_identity_key_policy(
        self, transition_versions: tuple[int, ...]
    ) -> tuple[int, ...]:
        if (
            len(transition_versions) not in {1, 2, 3}
            or tuple(sorted(set(transition_versions))) != transition_versions
            or any(
                isinstance(version, bool)
                or not isinstance(version, int)
                or version <= 0
                for version in transition_versions
            )
        ):
            raise ValueError("identity policy transition versions invalid")
        try:
            with self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
                row_factory=dict_row,
            ) as connection:
                connection.execute(
                    "select platform_control.set_provider_identity_key_policy(%s,%s)",
                    ("dingtalk", list(transition_versions)),
                )
            return transition_versions
        except psycopg.Error:
            raise RuntimeError("identity policy synchronization unavailable") from None


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
    policy = subparsers.add_parser(
        "sync-identity-policy",
        help="Synchronize the database identity-key policy to a private keyring",
    )
    policy.add_argument("--keyring-file", required=True)
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
    if namespace.command == "purge-expired":
        result = repository.purge_expired(
            time_health=namespace.time_health,
            wal_health=namespace.wal_health,
        )
        print(json.dumps({"status": "ok", **result}, sort_keys=True))
        return 0
    keyring = IdentityKeyring.from_file(
        namespace.keyring_file,
        expected_purpose="provider-lookup-hmac",
        expected_key_length=32,
    )
    versions = repository.sync_identity_key_policy(
        keyring.transition_versions or ()
    )
    print(
        json.dumps(
            {
                "provider": "dingtalk",
                "status": "ok",
                "transition_versions": list(versions),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
