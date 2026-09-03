from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

import psycopg
import pytest


ROOT = Path(__file__).parents[2]
RUNNER = ROOT / "deploy/migrate-observability"
MIGRATION = ROOT / "backend/migrations/011_admin_session_subject_links.sql"


def runner_source() -> str:
    return RUNNER.read_text(encoding="utf-8")


def test_runner_has_a_strict_checksummed_single_migration_contract() -> None:
    source = runner_source()

    assert "set -Eeuo pipefail" in source
    assert "[[ $# -eq 2 ]]" in source
    assert "011_admin_session_subject_links.sql" in source
    assert "sha256" in source.lower()
    assert "pg_advisory_lock" in source
    assert "platform_sync.schema_migrations" in source
    assert "checksum_ok" in source
    assert "psql -X -v ON_ERROR_STOP=1" in source


def test_runner_secures_credentials_and_temporary_control_files() -> None:
    source = runner_source()

    assert "[[ \"$owner_dsn_file\" == /* ]]" in source
    assert "! -L \"$owner_dsn_file\"" in source
    assert "stat -f '%Lp %u'" in source
    assert '"600 $(id -u)"' in source
    assert "mktemp -d" in source
    assert "trap cleanup EXIT" in source
    assert 'PGSERVICE="observability-migrator"' in source
    assert 'PGSERVICEFILE="$service_file"' in source
    assert '--dbname "$database_url"' not in source
    assert "owner_database_url" not in source.split("CONTROL_SQL", 1)[-1]


def test_runner_verifies_the_required_relation_and_never_downgrades_failure() -> None:
    source = runner_source()

    assert "to_regclass('platform_identity.session_subject_links')" in source
    assert "required_relation_ok" in source
    assert "\\quit 3" in source
    assert "OBSERVABILITY_MIGRATION_FAILED" in source
    assert "|| true" not in source


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="module")
def observability_database():
    if not all(shutil.which(command) for command in ("initdb", "pg_ctl", "psql")):
        pytest.fail("disposable PostgreSQL requires initdb, pg_ctl, and psql")

    # PostgreSQL's Unix socket path limit is shorter than macOS's default
    # per-user temporary directory, so keep this disposable test cluster short.
    root = Path(tempfile.mkdtemp(prefix="obs-pg-", dir="/tmp"))
    data = root / "data"
    socket_dir = root / "socket"
    socket_dir.mkdir()
    port = _available_port()
    subprocess.run(
        [
            "initdb", "-D", str(data), "--auth=trust", "--encoding=UTF8",
            "--no-locale", "--username=observability_test_admin",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "pg_ctl", "-D", str(data), "-l", str(root / "postgres.log"),
            "-o", f"-F -h 127.0.0.1 -p {port} -k {socket_dir}", "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    dsn = f"postgresql://observability_test_admin@127.0.0.1:{port}/postgres"
    try:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute("create role flywheel_owner nologin")
            connection.execute("create role platform_sync_writer nologin")
            connection.execute(
                "create schema platform_identity authorization flywheel_owner"
            )
            connection.execute("create schema platform_sync authorization flywheel_owner")
        yield dsn
    finally:
        subprocess.run(
            ["pg_ctl", "-D", str(data), "stop", "-m", "immediate"],
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.postgres
def test_runner_is_idempotent_records_checksum_and_applies_exact_grants(
    observability_database: str, tmp_path: Path
) -> None:
    dsn_file = tmp_path / "owner-dsn"
    dsn_file.write_text(observability_database + "\n", encoding="utf-8")
    dsn_file.chmod(0o600)

    for _attempt in range(2):
        result = subprocess.run(
            [str(RUNNER), str(dsn_file), str(MIGRATION)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("OBSERVABILITY_MIGRATION_OK version=011")
        assert observability_database not in result.stdout + result.stderr

    with psycopg.connect(observability_database) as connection:
        row = connection.execute(
            "select count(*), min(length(sha256)) "
            "from platform_sync.schema_migrations where version = 11"
        ).fetchone()
        assert row == (1, 64)
        relation = connection.execute(
            "select to_regclass('platform_identity.session_subject_links')::text"
        ).fetchone()
        assert relation == ("platform_identity.session_subject_links",)
        privileges = connection.execute(
            "select privilege_type from information_schema.role_table_grants "
            "where grantee = 'platform_sync_writer' "
            "and table_schema = 'platform_identity' "
            "and table_name = 'session_subject_links' order by privilege_type"
        ).fetchall()
        assert [row[0] for row in privileges] == [
            "DELETE", "INSERT", "SELECT", "UPDATE"
        ]
        assert connection.execute(
            "select has_table_privilege('public', "
            "'platform_identity.session_subject_links', 'select')"
        ).fetchone() == (False,)
