import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

import psycopg
import pytest

ROOT = Path(__file__).parents[2]
RUNNER = ROOT / "deploy/migrate-observability"
MIGRATION = ROOT / "backend/migrations/011_admin_session_subject_links.sql"
SENDER_MIGRATION = ROOT / "backend/migrations/014_admin_verified_sender_view.sql"


def runner_source() -> str:
    return RUNNER.read_text(encoding="utf-8")


def test_runner_has_a_strict_checksummed_allowlist() -> None:
    source = runner_source()

    assert "set -Eeuo pipefail" in source
    assert "[[ $# -eq 2 ]]" in source
    assert "011_admin_session_subject_links.sql" in source
    assert "014_admin_verified_sender_view.sql" in source
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


@pytest.mark.postgres
def test_runner_accepts_encoded_unix_socket_host(
    observability_database: str, tmp_path: Path
) -> None:
    with psycopg.connect(observability_database) as connection:
        socket_dir = connection.execute("show unix_socket_directories").fetchone()[0]
    encoded_host = quote(socket_dir, safe="")
    port = urlsplit(observability_database).port
    dsn = f"postgresql://observability_test_admin@{encoded_host}:{port}/postgres"
    dsn_file = tmp_path / "socket-owner-dsn"
    dsn_file.write_text(dsn + "\n", encoding="utf-8")
    dsn_file.chmod(0o600)

    result = subprocess.run(
        [str(RUNNER), str(dsn_file), str(MIGRATION)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("OBSERVABILITY_MIGRATION_OK version=011")
    assert dsn not in result.stdout + result.stderr


@pytest.mark.postgres
def test_runner_applies_verified_admin_sender_view_without_naming_legacy_sessions(
    observability_database: str, tmp_path: Path
) -> None:
    dsn_file = tmp_path / "owner-dsn"
    dsn_file.write_text(observability_database + "\n", encoding="utf-8")
    dsn_file.chmod(0o600)
    with psycopg.connect(observability_database, autocommit=True) as connection:
        connection.execute("create role flywheel_analyst nologin")
        connection.execute("create role flywheel_ingest nologin")
        connection.execute("create schema platform_read authorization flywheel_owner")
        connection.execute("create schema flywheel_analytics authorization flywheel_owner")
        connection.execute("create schema flywheel_identity authorization flywheel_owner")
        connection.execute(
            "create table platform_read.sessions_raw_identity ("
            "session_key text, agent_id text, source_kind text, native_id text, "
            "channel text, title text, user_identity text, created_at timestamptz, "
            "last_active_at timestamptz, turn_count bigint, feedback_count bigint, "
            "review_count bigint, latest_outcome text, source_synced_at timestamptz, "
            "details jsonb, participant_count bigint, primary_sender_name text, "
            "primary_sender_department text, sender_identity_status text)"
        )
        connection.execute("alter table platform_read.sessions_raw_identity owner to flywheel_owner")
        connection.execute(
            "create table flywheel_analytics.messages (conversation_id uuid, "
            "sender_user_id uuid, role text, occurred_at timestamptz, id uuid)"
        )
        connection.execute("alter table flywheel_analytics.messages owner to flywheel_owner")
        connection.execute(
            "create table flywheel_identity.resolved_user_names (user_id uuid, "
            "name_source text, preferred_name text)"
        )
        connection.execute("alter table flywheel_identity.resolved_user_names owner to flywheel_owner")
        connection.execute(
            "insert into platform_read.sessions_raw_identity "
            "(session_key, agent_id, source_kind, native_id, channel, details, "
            "participant_count, sender_identity_status) values "
            "('admin:verified', 'ai-admin-agent', 'admin', 'verified', 'admin', "
            "'{\"display_name\":\"测试员工\",\"primary_department\":\"行政部\"}', 1, 'unavailable'), "
            "('admin:legacy', 'ai-admin-agent', 'admin', 'legacy', 'admin', "
            "'{\"display_name\":\"伪造姓名\"}', 1, 'unavailable'), "
            "('admin:verified-peer', 'ai-admin-agent', 'admin', 'verified-peer', 'admin', "
            "'{\"display_name\":\"另一员工\"}', 1, 'unavailable')"
        )
    for migration in (MIGRATION, SENDER_MIGRATION, SENDER_MIGRATION):
        result = subprocess.run(
            [str(RUNNER), str(dsn_file), str(migration)],
            check=False, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
    with psycopg.connect(observability_database) as connection:
        connection.execute(
            "insert into platform_identity.session_subject_links "
            "(source_kind, native_session_id, internal_user_id, verification_method, "
            "verified_at, source_synced_at) values "
            "('admin', 'verified', '00000000-0000-4000-8000-000000000123', "
            "'platform_session', now(), now()), "
            "('admin', 'verified-peer', '11111111-1111-4111-8111-111111110123', "
            "'platform_session', now(), now())"
        )
        rows = connection.execute(
            "select session_key, primary_sender_name, primary_sender_department, "
            "sender_identity_status from platform_read.sessions order by session_key"
        ).fetchall()
        assert rows == [
            ("admin:legacy", None, None, "unavailable"),
            ("admin:verified", "测试员工", "行政部", "resolved"),
            ("admin:verified-peer", "另一员工", None, "name_only"),
        ]
        subjects = connection.execute(
            "select user_identity from platform_read.sessions "
            "where source_kind='admin' and primary_sender_name is not null "
            "order by session_key"
        ).fetchall()
        assert len({subject[0] for subject in subjects}) == 2
        assert connection.execute(
            "select count(*) from platform_sync.schema_migrations where version=14"
        ).fetchone() == (1,)
