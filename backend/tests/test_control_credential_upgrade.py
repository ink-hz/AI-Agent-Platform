from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
HELPER = ROOT / "deploy" / "cloud" / "control-db-credential-state.sh"
BOOTSTRAP = ROOT / "deploy" / "cloud" / "bootstrap-control-db.sh"
STAGE = ROOT / "deploy" / "cloud" / "remote-stage.sh"

PRODUCTION_ROLES = (
    "platform_control_migrator",
    "platform_control_app",
    "platform_directory_worker",
    "platform_stream_ingest",
    "platform_audit_append",
    "platform_control_maintenance",
)
PRODUCTION_PASSWORD_FILES = (
    "control-migrator-password",
    "control-app-password",
    "control-directory-worker-password",
    "control-stream-ingest-password",
    "control-audit-append-password",
    "control-maintenance-password",
)
PRODUCTION_DSN_FILES = (
    "control-migrator-database-url",
    "control-database-url",
    "control-directory-worker-database-url",
    "control-stream-ingest-database-url",
    "control-audit-database-url",
    "control-maintenance-database-url",
)
PREVIEW_ROLES = tuple(f"{role}_preview" for role in PRODUCTION_ROLES)
PREVIEW_PASSWORD_FILES = tuple(
    f"preview-{name}" for name in PRODUCTION_PASSWORD_FILES
)
PREVIEW_DSN_FILES = tuple(f"preview-{name}" for name in PRODUCTION_DSN_FILES)
STATE_FILE = ".control-database-credentials-v2.state"
WORK_DIR = ".control-database-credentials-v2"


def _bash_array(script: str, name: str) -> tuple[str, ...]:
    body = script.split(f"{name}=(\n", 1)[1].split("\n)", 1)[0]
    return tuple(line.strip() for line in body.splitlines() if line.strip())


def _run_helper(*args: str | Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(HELPER), *(str(arg) for arg in args)],
        check=check,
        capture_output=True,
        text=True,
    )


def _password(seed: int) -> str:
    return f"{seed:064x}"


def _write_secret(path: Path, value: str) -> None:
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)


def _dsn(role: str, password: str, database: str) -> str:
    return (
        f"postgresql://{role}:{password}@platform-postgres:5432/{database}"
    )


def _install_legacy_shared_layout(private: Path) -> tuple[str, ...]:
    passwords = tuple(_password(index + 1) for index in range(6))
    for index, role in enumerate(PRODUCTION_ROLES):
        _write_secret(private / PRODUCTION_PASSWORD_FILES[index], passwords[index])
        _write_secret(
            private / PRODUCTION_DSN_FILES[index],
            _dsn(role, passwords[index], "agent_platform_control"),
        )
        _write_secret(
            private / PREVIEW_DSN_FILES[index],
            _dsn(role, passwords[index], "agent_platform_control_preview"),
        )
    return passwords


def _install_isolated_layout(private: Path) -> tuple[str, ...]:
    passwords = tuple(_password(index + 1) for index in range(12))
    roles = PRODUCTION_ROLES + PREVIEW_ROLES
    password_files = PRODUCTION_PASSWORD_FILES + PREVIEW_PASSWORD_FILES
    dsn_files = PRODUCTION_DSN_FILES + PREVIEW_DSN_FILES
    databases = ("agent_platform_control",) * 6 + (
        "agent_platform_control_preview",
    ) * 6
    for index, role in enumerate(roles):
        _write_secret(private / password_files[index], passwords[index])
        _write_secret(
            private / dsn_files[index],
            _dsn(role, passwords[index], databases[index]),
        )
    return passwords


def test_exact_legacy_shared_layout_is_detected_once(tmp_path: Path) -> None:
    old_passwords = _install_legacy_shared_layout(tmp_path)

    result = _run_helper("classify", tmp_path)

    assert result.stdout == "legacy-shared\n"
    assert not any((tmp_path / name).exists() for name in PREVIEW_PASSWORD_FILES)
    assert len(set(old_passwords)) == 6

    _write_secret(tmp_path / PREVIEW_PASSWORD_FILES[0], _password(99))
    rejected = _run_helper("classify", tmp_path, check=False)
    assert rejected.returncode != 0
    assert rejected.stdout == ""


def test_fresh_install_requires_an_empty_layout_not_only_marker_absence(
    tmp_path: Path,
) -> None:
    assert _run_helper("classify", tmp_path).stdout == "fresh\n"

    _write_secret(tmp_path / PRODUCTION_PASSWORD_FILES[0], _password(1))
    rejected = _run_helper("classify", tmp_path, check=False)

    assert rejected.returncode != 0
    assert rejected.stdout == ""


@pytest.mark.parametrize("origin", ["legacy-shared", "isolated-unmarked"])
def test_rotation_prepare_is_retryable_and_generates_exactly_twelve_new_values(
    tmp_path: Path,
    origin: str,
) -> None:
    old_passwords = (
        _install_legacy_shared_layout(tmp_path)
        if origin == "legacy-shared"
        else _install_isolated_layout(tmp_path)
    )

    _run_helper("prepare", tmp_path, origin)
    candidate_dir = tmp_path / WORK_DIR
    first_candidates = tuple(
        (candidate_dir / name).read_text(encoding="utf-8").strip()
        for name in PRODUCTION_PASSWORD_FILES + PREVIEW_PASSWORD_FILES
    )
    first_state = (tmp_path / STATE_FILE).read_bytes()

    assert first_state == (
        f"version=2\nstatus=rotating\norigin={origin}\n".encode()
    )
    assert (tmp_path / STATE_FILE).stat().st_mode & 0o777 == 0o600
    assert len(first_candidates) == 12
    assert len(set(first_candidates)) == 12
    assert all(len(value) == 64 for value in first_candidates)
    assert all(
        first_candidates[index] != old_passwords[index] for index in range(6)
    )
    assert set(first_candidates[6:]).isdisjoint(first_candidates[:6])
    assert all(
        (candidate_dir / name).stat().st_mode & 0o777 == 0o600
        for name in PRODUCTION_PASSWORD_FILES + PREVIEW_PASSWORD_FILES
    )

    _run_helper("prepare", tmp_path, origin)
    retried_candidates = tuple(
        (candidate_dir / name).read_text(encoding="utf-8").strip()
        for name in PRODUCTION_PASSWORD_FILES + PREVIEW_PASSWORD_FILES
    )
    assert retried_candidates == first_candidates
    assert _run_helper("classify", tmp_path).stdout == f"rotating:{origin}\n"


def test_atomic_publish_replaces_all_dsns_with_distinct_role_credentials(
    tmp_path: Path,
) -> None:
    old_passwords = _install_legacy_shared_layout(tmp_path)
    _run_helper("prepare", tmp_path, "legacy-shared")

    _run_helper("publish", tmp_path)

    final_passwords = tuple(
        (tmp_path / name).read_text(encoding="utf-8").strip()
        for name in PRODUCTION_PASSWORD_FILES + PREVIEW_PASSWORD_FILES
    )
    roles = PRODUCTION_ROLES + PREVIEW_ROLES
    dsn_files = PRODUCTION_DSN_FILES + PREVIEW_DSN_FILES
    databases = ("agent_platform_control",) * 6 + (
        "agent_platform_control_preview",
    ) * 6
    assert sum(
        final_passwords[index] != old_passwords[index] for index in range(6)
    ) == 6
    for index, dsn_file in enumerate(dsn_files):
        path = tmp_path / dsn_file
        assert path.read_text(encoding="utf-8") == (
            _dsn(roles[index], final_passwords[index], databases[index]) + "\n"
        )
        assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / STATE_FILE).read_text(encoding="utf-8") == (
        "version=2\nstatus=rotating\norigin=legacy-shared\n"
    )


def test_state_only_partial_prepare_resumes_without_a_second_rotation(
    tmp_path: Path,
) -> None:
    old_passwords = _install_legacy_shared_layout(tmp_path)
    _write_secret(
        tmp_path / STATE_FILE,
        "version=2\nstatus=rotating\norigin=legacy-shared",
    )

    assert _run_helper("classify", tmp_path).stdout == (
        "rotating:legacy-shared\n"
    )
    _run_helper("prepare", tmp_path, "legacy-shared")
    first_candidates = tuple(
        (tmp_path / WORK_DIR / name).read_bytes()
        for name in PRODUCTION_PASSWORD_FILES + PREVIEW_PASSWORD_FILES
    )
    _run_helper("prepare", tmp_path, "legacy-shared")

    assert tuple(
        (tmp_path / WORK_DIR / name).read_bytes()
        for name in PRODUCTION_PASSWORD_FILES + PREVIEW_PASSWORD_FILES
    ) == first_candidates
    assert all(
        first_candidates[index].decode().strip() != old_passwords[index]
        for index in range(6)
    )


def test_completion_marker_is_exact_and_prevents_a_second_rotation(
    tmp_path: Path,
) -> None:
    _install_legacy_shared_layout(tmp_path)
    _run_helper("prepare", tmp_path, "legacy-shared")
    _run_helper("publish", tmp_path)
    published = tuple(
        (tmp_path / name).read_bytes()
        for name in PRODUCTION_PASSWORD_FILES + PREVIEW_PASSWORD_FILES
    )

    _run_helper("complete", tmp_path)

    marker = tmp_path / STATE_FILE
    assert marker.read_bytes() == b"version=2\nstatus=complete\n"
    assert marker.stat().st_mode & 0o777 == 0o600
    assert _run_helper("classify", tmp_path).stdout == "complete\n"
    assert _run_helper("prepare", tmp_path, "complete").stdout == "complete\n"
    assert tuple(
        (tmp_path / name).read_bytes()
        for name in PRODUCTION_PASSWORD_FILES + PREVIEW_PASSWORD_FILES
    ) == published


def test_bootstrap_orders_rotation_migrations_verification_and_marker() -> None:
    script = BOOTSTRAP.read_text(encoding="utf-8")

    assert script.count("alter role %I login password %L") == 1
    assert _bash_array(script, "production_rotation_roles") == PRODUCTION_ROLES
    assert "control_catalog_signature" in script
    assert 'fresh:0:0:0:0:0:0' in script
    assert 'legacy-shared:2:6:0:0:2:0' in script
    assert 'isolated-unmarked:2:6:6:2:0:2' in script
    assert "control-db-credential-state.sh" in script
    assert "/bin/chown root:root \"$credential_state_file\"" in script
    assert "CONTROL_DATABASE_CREDENTIALS_READY version=2" in script
    assert "CONTROL_DATABASE_CREDENTIALS_V2_RELOAD_REQUIRED" not in script

    prepare = script.index('"$credential_helper" prepare')
    rotate = script.index("alter role %I login password %L")
    migrate = script.index("python -m app.control_plane.migrate")
    verify = script.index('[[ "$membership_count" == "0" ]] || fail')
    publish = script.index('"$credential_helper" publish')
    complete = script.index('"$credential_helper" complete')
    ready = script.index("CONTROL_DATABASE_CREDENTIALS_READY version=2")
    assert prepare < rotate < migrate < verify < publish < complete < ready


def test_remote_acceptance_requires_exact_marker_and_consumer_recreation() -> None:
    stage = STAGE.read_text(encoding="utf-8")

    assert (
        '[[ "$control_bootstrap_result" == '
        '"CONTROL_DATABASE_CREDENTIALS_READY version=2" ]] || fail'
    ) in stage
    assert "control_secret_consumer_services=(" in stage
    for service in (
        "platform-api",
        "platform-api-preview",
        "platform-directory",
        "platform-directory-preview",
        "platform-dingtalk-stream",
        "platform-dingtalk-stream-preview",
    ):
        assert service in stage
    assert (
        'up -d --force-recreate "${active_control_secret_consumers[@]}"'
        in stage
    )
    bootstrap = stage.index("control_bootstrap_result=")
    recreate = stage.index(
        'up -d --force-recreate "${active_control_secret_consumers[@]}"'
    )
    acceptance = stage.index('echo "CLOUD_PLATFORM_DEPLOY_OK release=')
    assert bootstrap < recreate < acceptance
    assert (
        'if [[ "${#active_control_secret_consumers[@]}" -gt 0 ]]; then'
        in stage
    )
    assert (
        'if [[ "${#previous_control_consumers[@]}" -gt 0 ]]; then'
        in stage
    )
    assert 'docker rm -f "$container_id"' in stage
    assert 'up -d --force-recreate "${previous_control_consumers[@]}"' in stage
    rollback = stage.index("rollback() {")
    stop = stage.index('stop "${previous_control_consumers[@]}"')
    assert rollback < stage.index(
        'up -d --force-recreate "${previous_control_consumers[@]}"'
    ) < stop
    for forbidden in (
        "docker restart ai-fae-backend",
        "docker stop ai-fae-backend",
        "publish-agent-domain.sh",
    ):
        assert forbidden not in stage
    assert "replica-database-url" not in BOOTSTRAP.read_text(encoding="utf-8")
