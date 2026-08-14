from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Iterable

import psycopg

from .database import read_control_migrator_database_url
from .dsn import validate_control_dsn


_MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{3})_[a-z0-9_]+\.sql$")
_ADVISORY_LOCK = 0x41504331
_CONTROL_OWNER_ROLES = frozenset(
    {
        "platform_control_owner",
        "platform_control_owner_preview",
    }
)


class MigrationChecksumMismatch(RuntimeError):
    """An applied migration no longer has its recorded contents."""


@dataclass(frozen=True)
class NumberedMigration:
    version: int
    sha256: str
    sql: str


def load_numbered_migrations(migration_dir: Path) -> Iterable[NumberedMigration]:
    seen: set[int] = set()
    for path in sorted(migration_dir.iterdir()):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None or not path.is_file():
            continue
        version = int(match.group("version"))
        if version in seen:
            raise ValueError(f"duplicate control migration version: {version}")
        seen.add(version)
        payload = path.read_bytes()
        yield NumberedMigration(
            version=version,
            sha256=hashlib.sha256(payload).hexdigest(),
            sql=payload.decode("utf-8"),
        )


def verify_or_apply(cursor, version: int, sha256: str, sql: str) -> None:
    cursor.execute(
        "select sha256 from platform_control.schema_migrations where version = %s",
        (version,),
    )
    row = cursor.fetchone()
    if row is not None:
        if row[0] != sha256:
            raise MigrationChecksumMismatch(
                f"control migration {version:03d} checksum mismatch"
            )
        return
    cursor.execute(sql)
    cursor.execute(
        "insert into platform_control.schema_migrations "
        "(version, sha256, applied_at) values (%s, %s, now())",
        (version, sha256),
    )


def migrate_control_database(
    database_url: str,
    migration_dir: Path,
    *,
    owner_role: str,
) -> None:
    if owner_role not in _CONTROL_OWNER_ROLES:
        raise ValueError(f"unsupported control owner role: {owner_role!r}")
    parsed = validate_control_dsn(database_url, purpose="migrator")
    expected_owner = "platform_control_owner" + (
        "_preview" if parsed.environment == "preview" else ""
    )
    if owner_role != expected_owner:
        raise ValueError("control owner role environment mismatch")
    with psycopg.connect(database_url, autocommit=False) as connection:
        with connection.cursor() as cursor:
            lock_acquired = False
            try:
                cursor.execute(
                    "select pg_advisory_lock(%s)", (_ADVISORY_LOCK,)
                )
                lock_acquired = True
                cursor.execute(
                    psycopg.sql.SQL("set local role {}").format(
                        psycopg.sql.Identifier(owner_role)
                    )
                )
                cursor.execute("create schema if not exists platform_control")
                cursor.execute(
                    "create table if not exists "
                    "platform_control.schema_migrations ("
                    "version integer primary key, "
                    "sha256 text not null check (length(sha256) = 64), "
                    "applied_at timestamptz not null)"
                )
                connection.commit()
                for migration in load_numbered_migrations(migration_dir):
                    cursor.execute(
                        psycopg.sql.SQL("set local role {}").format(
                            psycopg.sql.Identifier(owner_role)
                        )
                    )
                    verify_or_apply(
                        cursor,
                        migration.version,
                        migration.sha256,
                        migration.sql,
                    )
                    connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if lock_acquired:
                    cursor.execute(
                        "select pg_advisory_unlock(%s)", (_ADVISORY_LOCK,)
                    )
                    connection.commit()


def main() -> int:
    secret_file = os.getenv("PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE", "")
    if not secret_file:
        raise RuntimeError("control migrator database secret unavailable")
    owner_role = os.getenv("PLATFORM_CONTROL_OWNER_ROLE", "")
    if not owner_role:
        raise RuntimeError("control database owner role unavailable")
    migration_dir = Path(
        os.getenv(
            "PLATFORM_CONTROL_MIGRATION_DIR",
            str(Path(__file__).parents[2] / "control_migrations"),
        )
    )
    migrate_control_database(
        read_control_migrator_database_url(secret_file),
        migration_dir,
        owner_role=owner_role,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
