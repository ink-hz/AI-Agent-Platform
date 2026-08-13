from __future__ import annotations

import json
import base64
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.control_plane.admin_cli import (
    AdminRequest,
    OfflineOwnerAdministrator,
    consume_confirmation_receipt,
    read_confirmation_journal,
    write_confirmation_receipt,
    build_parser,
    render_error,
    render_result,
)
from app.control_plane.audit import AuditUnavailableError, IndeterminateMutationError
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
    assert "reconcile-owner" in help_text
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
    assert "--backup-reference" in replace_help
    assert "--incident-reference" in replace_help
    assert "--receipt-file" in replace_help
    assert "--receipt-key-file" in replace_help
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
        "--incident-reference",
        "INC_2026_001",
        "--backup-reference",
        "BACKUP_2026_001",
        "--receipt-file",
        "/private/receipt.json",
        "--receipt-key-file",
        "/private/receipt-key.json",
        "--receipt-key-version",
        "1",
    ]
    parsed = parser.parse_args(
        base + ["--approver", "uid:1001", "--approver", "uid:1002"]
    )
    assert parsed.approver == ["uid:1001", "uid:1002"]

    with pytest.raises(ValueError, match="two distinct approvers required"):
        AdminRequest.from_namespace(
            parser.parse_args(
                base + ["--approver", "uid:1001", "--approver", "uid:1001"]
            )
        )

    with pytest.raises(ValueError, match="stable approver identity required"):
        AdminRequest.from_namespace(
            parser.parse_args(
                base + ["--approver", "Alice Smith", "--approver", "uid:1002"]
            )
        )


def _receipt_key_file(tmp_path: Path) -> Path:
    path = tmp_path / "offline-receipt-key.json"
    path.write_text(
        json.dumps(
            {
                "purpose": "offline-owner-receipt-hmac",
                "keys": {"1": base64.b64encode(b"r" * 32).decode("ascii")},
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _receipt_payload() -> dict[str, object]:
    return {
        "operation_id": str(uuid4()),
        "action": "replace",
        "protected_target_lookup_hash": "a" * 64,
        "protected_target_lookup_version": 2,
        "generation_id": str(uuid4()),
        "backup_reference": "BACKUP_2026_001",
        "incident_reference": "INC_2026_001",
        "approvers": ["uid:1001", "uid:1002"],
        "os_operator": "root",
        "directory_generation_digest": "b" * 64,
        "current_owner_internal_user_id": str(uuid4()),
        "current_owner_row_version": 7,
        "target_row_version": 3,
    }


def test_dry_run_receipt_is_authenticated_mode_0600_and_single_use(
    tmp_path: Path,
) -> None:
    key_file = _receipt_key_file(tmp_path)
    receipt_file = tmp_path / "owner-receipt.json"
    payload = _receipt_payload()
    issued_at = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)

    written = write_confirmation_receipt(
        receipt_file,
        key_file,
        key_version=1,
        payload=payload,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=15),
        expected_uid=os.getuid(),
    )

    assert receipt_file.stat().st_mode & 0o777 == 0o600
    assert written["key_version"] == 1
    assert len(written["payload_hash"]) == 64
    assert len(written["mac"]) == 64
    assert "provider" not in receipt_file.read_text(encoding="utf-8").lower()
    consumed = consume_confirmation_receipt(
        receipt_file,
        key_file,
        expected_payload=payload,
        now=issued_at + timedelta(minutes=1),
        expected_uid=os.getuid(),
    )
    assert consumed["operation_id"] == payload["operation_id"]
    assert not receipt_file.exists()
    journal_file = receipt_file.with_name(
        f"{receipt_file.name}.consumed-{payload['operation_id']}"
    )
    assert read_confirmation_journal(
        journal_file,
        key_file,
        expected_uid=os.getuid(),
    ) == payload
    with pytest.raises(ValueError, match="confirmation receipt unavailable"):
        consume_confirmation_receipt(
            receipt_file,
            key_file,
            expected_payload=payload,
            now=issued_at + timedelta(minutes=2),
            expected_uid=os.getuid(),
        )


@pytest.mark.parametrize("failure", ["tamper", "expired", "parameters"])
def test_confirmation_receipt_rejects_tamper_expiry_and_state_change(
    tmp_path: Path, failure: str
) -> None:
    key_file = _receipt_key_file(tmp_path)
    receipt_file = tmp_path / f"receipt-{failure}.json"
    payload = _receipt_payload()
    issued_at = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)
    write_confirmation_receipt(
        receipt_file,
        key_file,
        key_version=1,
        payload=payload,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=15),
        expected_uid=os.getuid(),
    )
    expected = dict(payload)
    now = issued_at + timedelta(minutes=1)
    if failure == "tamper":
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        receipt["payload"]["target_row_version"] = 99
        receipt_file.write_text(json.dumps(receipt), encoding="utf-8")
        receipt_file.chmod(0o600)
    elif failure == "expired":
        now = issued_at + timedelta(minutes=16)
    else:
        expected["directory_generation_digest"] = "c" * 64

    with pytest.raises(ValueError, match="confirmation receipt invalid"):
        consume_confirmation_receipt(
            receipt_file,
            key_file,
            expected_payload=expected,
            now=now,
            expected_uid=os.getuid(),
        )
    assert receipt_file.exists()


def test_receipt_and_key_symlinks_are_rejected_without_following(
    tmp_path: Path,
) -> None:
    key_file = _receipt_key_file(tmp_path)
    key_link = tmp_path / "key-link"
    key_link.symlink_to(key_file)
    receipt_file = tmp_path / "receipt.json"
    payload = _receipt_payload()
    issued_at = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="confirmation receipt key invalid"):
        write_confirmation_receipt(
            receipt_file,
            key_link,
            key_version=1,
            payload=payload,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(minutes=15),
            expected_uid=os.getuid(),
        )
    write_confirmation_receipt(
        receipt_file,
        key_file,
        key_version=1,
        payload=payload,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=15),
        expected_uid=os.getuid(),
    )
    receipt_link = tmp_path / "receipt-link"
    receipt_link.symlink_to(receipt_file)
    with pytest.raises(ValueError, match="confirmation receipt unavailable"):
        consume_confirmation_receipt(
            receipt_link,
            key_file,
            expected_payload=payload,
            now=issued_at + timedelta(minutes=1),
            expected_uid=os.getuid(),
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
            "(generation_id, status, completed_at, content_sha256) "
            "values (%s, 'complete', now(), %s)",
            (generation_id, "d" * 64),
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
        os_operator="root",
        approvers=("uid:1001", "uid:1002"),
        backup_reference="BACKUP_2026_001",
        incident_reference="INC_2026_001",
    )
    replaced = administrator.replace_owner(
        provider_id=second_provider,
        subject_kind="employee",
        generation_id=generation_id,
        os_operator="root",
        approvers=("uid:1001", "uid:1002"),
        backup_reference="BACKUP_2026_002",
        incident_reference="INC_2026_002",
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
            "(generation_id, status, completed_at, content_sha256) "
            "values (%s, 'complete', now(), %s)",
            (generation_id, "e" * 64),
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
            os_operator="root",
            approvers=("uid:1001", "uid:1002"),
            backup_reference="BACKUP_2026_003",
            incident_reference="INC_2026_003",
        )


@pytest.mark.postgres
@pytest.mark.parametrize("committed", [True, False])
def test_offline_owner_reconcile_uses_signed_prestate_without_second_mutation(
    control_database,
    tmp_path: Path,
    committed: bool,
) -> None:
    environment = control_database["environments"]["preview"]
    codec = _codec(tmp_path)
    provider_id = f"reconcile-target-{committed}"
    repository = ControlRepository(
        environment["urls"]["platform_control_app_preview"],
        identity_codec=codec,
    )
    target = repository.create_internal_user(
        codec.seal("employee", provider_id), "Reconcile Target"
    )
    generation_id = uuid4()
    protected = codec.seal("employee", provider_id)
    with psycopg.connect(environment["admin"]) as connection:
        current_owner = connection.execute(
            "select internal_user_id from platform_control.internal_users "
            "where role='platform_owner'"
        ).fetchone()
        if current_owner is None:
            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,status,role) "
                "values (%s,'Reconcile Prior Owner','active','platform_owner')",
                (uuid4(),),
            )
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,completed_at,content_sha256) "
            "values (%s,'complete',now(),%s)",
            (generation_id, "f" * 64),
        )
        connection.execute(
            "insert into platform_control.directory_members "
            "(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version,"
            "display_name,status) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,'active')",
            (generation_id, uuid4(), target, protected.subject_kind,
             protected.lookup_hmac, protected.lookup_key_version,
             protected.ciphertext, protected.encryption_key_version,
             "Reconcile Target"),
        )
    real_writer = AuditWriter.from_database_url(
        environment["urls"]["platform_audit_append_preview"]
    )

    class OutcomeFailure:
        environment = "preview"

        def append(self, command):
            if command.event_type.endswith("_completed"):
                raise AuditUnavailableError("simulated outcome failure")
            return real_writer.append(command)

    administrator = OfflineOwnerAdministrator(
        environment["urls"]["platform_control_migrator_preview"],
        owner_role="platform_control_owner_preview",
        identity_codec=codec,
        audit_writer=OutcomeFailure() if committed else real_writer,
    )
    payload = administrator.prepare_owner_change(
        action="replace", provider_id=provider_id, subject_kind="employee",
        generation_id=generation_id, os_operator="root",
        approvers=("uid:1001", "uid:1002"),
        backup_reference="BACKUP_RECONCILE", incident_reference="INC_RECONCILE",
    )
    if committed:
        with pytest.raises(IndeterminateMutationError):
            administrator._change_owner(
                payload=payload, provider_id=provider_id, subject_kind="employee"
            )
    administrator.audit_writer = real_writer
    result = administrator.reconcile_owner_change(
        payload=payload, provider_id=provider_id, subject_kind="employee"
    )
    assert result["status"] == ("completed" if committed else "not_committed")
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.management_mutations "
            "where operation_id=%s", (payload["operation_id"],)
        ).fetchone() == ((1 if committed else 0),)
        assert connection.execute(
            "select event_type from platform_control.audit_events "
            "where request_id=%s order by event_type", (payload["operation_id"],)
        ).fetchall() == ([
            ("owner_replacement_completed",), ("owner_replacement_requested",)
        ] if committed else [
            ("owner_replacement_failed",), ("owner_replacement_requested",)
        ])
