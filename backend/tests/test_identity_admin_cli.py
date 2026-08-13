from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.control_plane.admin_cli import (
    AdminRequest,
    OfflineOwnerAdministrator,
    build_parser,
    render_error,
    render_result,
)
from app.control_plane.audit import IndeterminateMutationError
from app.control_plane.audit import AuditWriter
from app.control_plane.repository import ControlRepository
from test_control_plane_migration import control_database
from test_identity_crypto import _codec


def test_parser_uses_provider_file_and_never_name_mobile_or_web_owner_route() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    bind_help = parser._subparsers._group_actions[0].choices[
        "bind-owner"
    ].format_help()
    replace_help = parser._subparsers._group_actions[0].choices[
        "replace-owner"
    ].format_help()

    assert "bind-owner" in help_text
    assert "replace-owner" in help_text
    assert "show-directory-generation" in help_text
    assert "--provider-id-file" in bind_help
    assert "--generation-id" in bind_help
    assert "--display-name" not in bind_help
    assert "--name" not in bind_help
    assert "--mobile" not in bind_help
    replace_actions = {
        action.dest for action in parser._subparsers._group_actions[0].choices[
            "replace-owner"
        ]._actions
    }
    assert "approver" in replace_actions
    assert "--backup-confirmed" in replace_help
    assert "--confirm" in replace_help

    routes = (
        Path(__file__).parents[1] / "app/control_plane/routes_manage.py"
    ).read_text()
    assert "bind-owner" not in routes
    assert "replace-owner" not in routes


def test_machine_output_never_contains_provider_identity() -> None:
    provider_id = "synthetic-provider-secret"
    output = render_result(
        {
            "status": "ok",
            "internal_user_id": str(uuid4()),
            "audit_event_id": str(uuid4()),
        }
    )
    assert json.loads(output)["status"] == "ok"
    assert provider_id not in output

    request_id = uuid4()
    audit_event_id = uuid4()
    error_output = render_error(
        IndeterminateMutationError(request_id, audit_event_id)
    )
    assert json.loads(error_output) == {
        "audit_event_id": str(audit_event_id),
        "code": "management_mutation_indeterminate",
        "request_id": str(request_id),
        "status": "error",
    }
    assert provider_id not in error_output


def test_replacement_requires_two_distinct_named_approvers() -> None:
    parser = build_parser()
    base = [
        "replace-owner",
        "--provider-id-file",
        "/private/provider-id",
        "--generation-id",
        str(uuid4()),
        "--reason",
        "owner departure incident",
        "--backup-confirmed",
        "--confirm",
    ]
    parsed = parser.parse_args(base + ["--approver", "alice", "--approver", "bob"])
    assert parsed.approver == ["alice", "bob"]

    with pytest.raises(ValueError, match="two distinct approvers required"):
        AdminRequest.from_namespace(
            parser.parse_args(
                base + ["--approver", "alice", "--approver", "alice"]
            )
        )


@pytest.mark.postgres
def test_offline_bind_and_replace_select_stable_mapping_and_keep_one_owner(
    control_database,
    tmp_path: Path,
) -> None:
    environment = control_database["environments"]["production"]
    codec = _codec(tmp_path)
    app_repository = ControlRepository(
        environment["urls"]["platform_control_app"], identity_codec=codec
    )
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance"]
    ) as connection:
        connection.execute(
            "select platform_control.set_provider_identity_key_policy("
            "'dingtalk', array[1,2])"
        )

    first_provider = "stable-owner-one"
    second_provider = "stable-owner-two"
    first_id = app_repository.create_internal_user(
        codec.seal("employee", first_provider), "Same Display Name"
    )
    second_id = app_repository.create_internal_user(
        codec.seal("employee", second_provider), "Same Display Name"
    )
    generation_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id, status, completed_at) values (%s, 'complete', now())",
            (generation_id,),
        )
        for index, (internal_id, protected) in enumerate(
            (
                (first_id, codec.seal("employee", first_provider)),
                (second_id, codec.seal("employee", second_provider)),
            )
        ):
            connection.execute(
                "insert into platform_control.directory_members "
                "(generation_id, member_key, internal_user_id, subject_kind, "
                "lookup_hmac, lookup_key_version, encrypted_provider_id, "
                "encryption_key_version, display_name, status) values "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')",
                (
                    generation_id,
                    uuid4(),
                    internal_id,
                    protected.subject_kind,
                    protected.lookup_hmac,
                    protected.lookup_key_version,
                    protected.ciphertext,
                    protected.encryption_key_version,
                    "Same Display Name",
                ),
            )

    administrator = OfflineOwnerAdministrator(
        environment["urls"]["platform_control_migrator"],
        owner_role="platform_control_owner",
        identity_codec=codec,
        audit_writer=AuditWriter.from_database_url(
            environment["urls"]["platform_audit_append"]
        ),
    )
    bound = administrator.bind_owner(
        provider_id=first_provider,
        subject_kind="employee",
        generation_id=generation_id,
        reason="initial approved owner binding",
        os_operator="root",
    )
    replaced = administrator.replace_owner(
        provider_id=second_provider,
        subject_kind="employee",
        generation_id=generation_id,
        reason="approved owner departure incident",
        os_operator="root",
        approvers=("alice", "bob"),
        backup_confirmed=True,
        confirmed=True,
    )

    assert bound["internal_user_id"] == str(first_id)
    assert replaced["internal_user_id"] == str(second_id)
    assert first_provider not in json.dumps(bound)
    assert second_provider not in json.dumps(replaced)
    with psycopg.connect(environment["admin"]) as connection:
        owners = connection.execute(
            "select internal_user_id from platform_control.internal_users "
            "where role = 'platform_owner' and status = 'active'"
        ).fetchall()
        assert owners == [(second_id,)]
        links = connection.execute(
            "select role_audit_event_id from platform_control.internal_users "
            "where internal_user_id in (%s, %s)",
            (first_id, second_id),
        ).fetchall()
        assert all(row[0] is not None for row in links)


@pytest.mark.postgres
def test_bind_refuses_target_outside_selected_complete_generation(
    control_database,
    tmp_path: Path,
) -> None:
    environment = control_database["environments"]["preview"]
    codec = _codec(tmp_path)
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance_preview"]
    ) as connection:
        connection.execute(
            "select platform_control.set_provider_identity_key_policy("
            "'dingtalk', array[1,2])"
        )
    repository = ControlRepository(
        environment["urls"]["platform_control_app_preview"],
        identity_codec=codec,
    )
    provider_id = "not-in-generation"
    repository.create_internal_user(
        codec.seal("employee", provider_id), "Absent Target"
    )
    generation_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id, status, completed_at) values (%s, 'complete', now())",
            (generation_id,),
        )

    administrator = OfflineOwnerAdministrator(
        environment["urls"]["platform_control_migrator_preview"],
        owner_role="platform_control_owner_preview",
        identity_codec=codec,
        audit_writer=AuditWriter.from_database_url(
            environment["urls"]["platform_audit_append_preview"]
        ),
    )
    with pytest.raises(ValueError, match="target unavailable in selected generation"):
        administrator.bind_owner(
            provider_id=provider_id,
            subject_kind="employee",
            generation_id=generation_id,
            reason="initial approved owner binding",
            os_operator="root",
        )
