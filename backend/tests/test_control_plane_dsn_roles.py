from __future__ import annotations

import pytest

from app.control_plane.admin_cli import OfflineOwnerAdministrator
from app.control_plane.audit import AuditWriter
from app.control_plane.dsn import validate_control_dsn
from app.control_plane.routes_manage import ManagementRepository, ManagementService


@pytest.mark.parametrize(
    ("dsn", "purpose", "environment"),
    [
        (
            "postgresql://platform_control_app@127.0.0.1/agent_platform_control",
            "app",
            "production",
        ),
        (
            "dbname=agent_platform_control_preview "
            "user=platform_audit_append_preview host=127.0.0.1",
            "audit",
            "preview",
        ),
        (
            "postgresql://platform_control_migrator@127.0.0.1/"
            "agent_platform_control",
            "migrator",
            "production",
        ),
        (
            "dbname=agent_platform_control_preview "
            "user=platform_control_maintenance_preview",
            "maintenance",
            "preview",
        ),
        (
            "dbname=agent_platform_control_preview "
            "user=platform_directory_worker_preview",
            "directory",
            "preview",
        ),
    ],
)
def test_exact_control_dsn_accepts_uri_and_keyword_pairings(
    dsn, purpose, environment
) -> None:
    parsed = validate_control_dsn(dsn, purpose=purpose)
    assert parsed.environment == environment


@pytest.mark.parametrize(
    ("dsn", "purpose"),
    [
        ("postgresql:///agent_platform_control", "app"),
        (
            "postgresql://platform_control_owner@127.0.0.1/"
            "agent_platform_control",
            "app",
        ),
        (
            "postgresql://control_test_admin@127.0.0.1/agent_platform_control",
            "maintenance",
        ),
        (
            "postgresql://platform_control_app_preview@127.0.0.1/"
            "agent_platform_control",
            "app",
        ),
        (
            "dbname=agent_platform_control_preview user=platform_audit_append",
            "audit",
        ),
        (
            "dbname=agent_platform_control user=platform_control_migrator_preview",
            "migrator",
        ),
        (
            "dbname=postgres user=platform_control_app",
            "app",
        ),
        (
            "dbname=agent_platform_control user=platform_directory_worker_preview",
            "directory",
        ),
    ],
)
def test_exact_control_dsn_rejects_missing_overprivileged_and_wrong_environment(
    dsn, purpose
) -> None:
    with pytest.raises(ValueError, match="exact control .* DSN required"):
        validate_control_dsn(dsn, purpose=purpose)


def test_exact_control_dsn_rejects_unknown_purpose() -> None:
    with pytest.raises(ValueError, match="control DSN purpose invalid"):
        validate_control_dsn(
            "postgresql://platform_control_app@127.0.0.1/agent_platform_control",
            purpose="owner",
        )


def test_management_service_rejects_cross_environment_control_and_audit() -> None:
    repository = ManagementRepository(
        "postgresql://platform_control_app@127.0.0.1/agent_platform_control"
    )
    audit = AuditWriter.from_database_url(
        "postgresql://platform_audit_append_preview@127.0.0.1/"
        "agent_platform_control_preview"
    )
    with pytest.raises(ValueError, match="control and audit environment mismatch"):
        ManagementService(repository, audit)


def test_offline_owner_rejects_cross_environment_migrator_and_audit() -> None:
    audit = AuditWriter.from_database_url(
        "postgresql://platform_audit_append_preview@127.0.0.1/"
        "agent_platform_control_preview"
    )
    with pytest.raises(ValueError, match="control and audit environment mismatch"):
        OfflineOwnerAdministrator(
            "postgresql://platform_control_migrator@127.0.0.1/"
            "agent_platform_control",
            owner_role="platform_control_owner",
            identity_codec=object(),
            audit_writer=audit,
        )
