"""Verified FAE ownership stays typed, scoped and encrypted at the replica boundary."""

import base64
import io
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.cloud_replica.crypto import BatchSigner, BatchVerifier, FieldCipher
from app.cloud_replica.exporter import ReplicaExporter, build_session_record
from app.cloud_replica.protocol import BatchLimits, decode_and_verify_batch
from app.cloud_replica.repository import ReplicaObservabilityRepository
from app.cloud_replica.sanitize import SanitizationPolicy, sanitize_session
from app.cloud_replica.source import SESSION_SQL, ReplicaSource
from app.cloud_replica.store import ReplicaStore, ReplicaStoreError
from app.sync_remote.importer import ImportValidationError, normalize_row
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

NOW = datetime(2026, 10, 23, 8, tzinfo=UTC)
PARTNER = "00000000-0000-4000-8000-000000000123"
OTHER = "00000000-0000-4000-8000-000000000124"
POLICY = SanitizationPolicy()
IDENTITY_KEY = b"i" * 32


def _row(subject_type="partner_operator", subject_id=PARTNER):
    return {
        "id": "fae-native",
        "user_id": subject_id if subject_type == "enterprise_member" else None,
        "owner_subject_id": subject_id,
        "owner_subject_type": subject_type,
        "metadata": {
            "authentication_mode": "platform_enterprise"
            if subject_type == "enterprise_member"
            else "platform_partner"
        },
    }


def _import(row):
    return normalize_row("fae", "chat_sessions", row, NOW).values["details"]


@pytest.mark.parametrize("subject_type", ["partner_operator", "enterprise_member"])
def test_import_derives_reference_from_authoritative_owner_columns(subject_type):
    row = _row(subject_type)
    # Even a claimed reference with another identity must be replaced.
    row["metadata"]["verified_subject"] = {
        "subject_id": OTHER,
        "subject_type": "enterprise_member",
    }
    details = _import(row)
    assert details["verified_subject"] == {
        "subject_id": PARTNER,
        "subject_type": subject_type,
    }
    assert details["owner_subject_id"] == PARTNER
    assert details["owner_subject_type"] == subject_type


def test_import_canonicalizes_authoritative_owner_uuid():
    row = _row("enterprise_member", PARTNER.replace("-", ""))
    assert _import(row)["verified_subject"]["subject_id"] == PARTNER


def test_unowned_legacy_row_does_not_gain_metadata_claim():
    details = _import(
        {
            "id": "legacy",
            "metadata": {
                "verified_subject": {
                    "subject_id": PARTNER,
                    "subject_type": "partner_operator",
                },
                "owner_subject_id": PARTNER,
                "owner_subject_type": "partner_operator",
                "authentication_mode": "platform_partner",
            },
        }
    )
    assert "verified_subject" not in details


@pytest.mark.parametrize(
    "change",
    [
        {"owner_subject_id": None},
        {"owner_subject_type": None},
        {"owner_subject_id": "not-a-uuid"},
        {"owner_subject_id": 123},
        {"owner_subject_type": "employee"},
        {"user_id": PARTNER},
        {"metadata": {"authentication_mode": "platform_enterprise"}},
        {"metadata": {}},
    ],
)
def test_import_rejects_partial_malformed_or_mismatched_partner_owner(change):
    with pytest.raises(ImportValidationError, match="fae_session_owner_invalid"):
        _import({**_row(), **change})


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": OTHER},
        {"user_id": None},
        {"user_id": "malformed"},
        {"metadata": {"authentication_mode": "platform_partner"}},
        {"internal_user_id": OTHER},
    ],
)
def test_import_rejects_enterprise_internal_identity_mismatch(change):
    with pytest.raises(ImportValidationError, match="fae_session_owner_invalid"):
        _import({**_row("enterprise_member"), **change})


@pytest.mark.parametrize(
    "source,table", [("admin", "admin_chat_sessions")]
)
def test_other_imports_keep_existing_behavior(source, table):
    row = {
        **_row(),
        "owner_subject_id": "not-a-uuid",
        "metadata": {"verified_subject": "untrusted"},
    }
    assert (
        normalize_row(source, table, row, NOW).values["details"]["verified_subject"]
        == "untrusted"
    )


def _raw(subject_type="partner_operator", subject_id=PARTNER, session_key="fae:s1"):
    details = _import(_row(subject_type, subject_id))
    source_row = {
        "session_key": session_key,
        "agent_id": "ai-fae-agent",
        "source_kind": "fae",
        "channel": "dingtalk",
        "title": "camera consultation",
        "user_identity": "untrusted-nickname",
        "primary_sender_name": "Same nickname",
        "primary_sender_department": None,
        "created_at": NOW,
        "last_active_at": NOW,
        "replica_updated_at": NOW,
        "verified_subject": details.get("verified_subject"),
    }
    turn_row = {
        "session_key": session_key,
        "turn_key": f"{session_key}:t1",
        "turn_index": 1,
        "question": "camera?",
        "answer": "Use the matching SDK.",
        "created_at": NOW,
        "question_at": NOW,
        "answer_at": NOW,
        "question_time_status": "exact",
        "answer_time_status": "exact",
        "outcome": "success",
        "fallback_used": False,
        "duration_ms": 10,
    }
    return ReplicaSource._assemble([source_row], [turn_row], [], [], [])[0]


def _record(raw, **kwargs):
    return build_session_record(
        raw, sanitize_session(raw, POLICY), IDENTITY_KEY, **kwargs
    )


def test_source_selects_only_reference_and_passes_typed_field_to_sanitizer():
    assert "details->'verified_subject'" in SESSION_SQL
    raw = _raw()
    assert asdict(raw.verified_subject) == {
        "subject_id": PARTNER,
        "subject_type": "partner_operator",
    }
    assert sanitize_session(raw, POLICY).verified_subject == raw.verified_subject
    assert raw.details == {}


@pytest.mark.parametrize(
    "changes",
    [{"agent_id": "daq-agent"}, {"source_kind": "metabot"}, {"source_kind": "admin"}],
)
def test_reference_cannot_cross_agent_or_source_scope(changes):
    with pytest.raises(ValueError, match="verified_subject"):
        sanitize_session(replace(_raw(), **changes), POLICY)


def test_default_writer_retains_exact_legacy_shape():
    raw = _raw()
    record = _record(raw)
    assert "verified_subject" not in record
    # The default writer also retains its old identity grouping.
    from app.cloud_replica.crypto import stable_id

    assert record["user_id"] == stable_id("user:fae", raw.user_identity, IDENTITY_KEY)


def test_opted_in_writer_groups_same_subject_across_sessions_not_nicknames():
    first = _record(_raw(), include_verified_subject=True)
    second = _record(
        replace(_raw(session_key="fae:s2"), user_identity="another-raw-id"),
        include_verified_subject=True,
    )
    other = _record(_raw(subject_id=OTHER), include_verified_subject=True)
    assert first["user_id"] == second["user_id"]
    assert first["user_id"] != other["user_id"]
    assert first["verified_subject"] == {
        "subject_id": PARTNER,
        "subject_type": "partner_operator",
    }
    assert PARTNER not in first["user_id"]


@pytest.mark.parametrize("option", ["true", "false", 1, 0, None])
def test_writer_opt_in_requires_actual_boolean(option):
    with pytest.raises(ValueError, match="include_verified_subject"):
        _record(_raw(), include_verified_subject=option)


def _exporter(tmp_path, include_verified_subject=False):
    private = Ed25519PrivateKey.generate()
    return ReplicaExporter(
        source=SimpleNamespace(fetch_sessions=lambda **_: (_raw(),)),
        policy=POLICY,
        identity_key=IDENTITY_KEY,
        signer=BatchSigner(private),
        source_instance_id="test",
        state_path=tmp_path / "state",
        queue_dir=tmp_path / "queue",
        clock=lambda: NOW,
        include_verified_subject=include_verified_subject,
    ), private


@pytest.mark.parametrize("enabled", [False, True])
def test_reference_survives_signed_export_encrypted_store_and_observability(
    tmp_path, enabled
):
    exporter, private = _exporter(tmp_path, enabled)
    result = exporter.export_batch(
        after=NOW - timedelta(days=1), through=NOW, limit=100
    )
    batch = decode_and_verify_batch(
        io.BytesIO(result.batch_path.read_bytes()),
        BatchVerifier(private.public_key()),
        BatchLimits(),
    )
    record = batch.records[0]
    assert ("verified_subject" in record) is enabled
    cipher = FieldCipher(b"e" * 32)
    prepared = ReplicaStore("unused", cipher=cipher).prepare_session(record)
    assert not hasattr(prepared, "verified_subject")
    assert PARTNER not in json.dumps(prepared.encrypted)
    decode = lambda v: base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))
    row = {
        **{
            k: getattr(prepared, k)
            for k in (
                "session_key",
                "user_id",
                "agent_id",
                "source_kind",
                "channel",
                "created_at",
                "last_active_at",
                "payload_sha256",
            )
        },
        "payload_nonce": decode(prepared.encrypted["nonce"]),
        "display_payload": decode(prepared.encrypted["ciphertext"]),
    }
    repository = ReplicaObservabilityRepository("unused", cipher=cipher)
    restored = repository._decrypt(row)
    summary = repository._summary(
        restored, {"last_success_at": NOW, "freshness": "fresh"}
    )
    turn = repository._turn(restored, restored["turns"][0])
    expected = record.get("verified_subject")
    assert summary.model_dump(mode="json").get("verified_subject") == expected
    assert turn.model_dump(mode="json").get("verified_subject") == expected


@pytest.mark.parametrize(
    "ref",
    [
        None,
        {},
        {"subject_id": PARTNER},
        {"subject_type": "partner_operator"},
        {"subject_id": PARTNER, "subject_type": "customer"},
        {"subject_id": "not-a-uuid", "subject_type": "partner_operator"},
        {"subject_id": PARTNER.replace("-", ""), "subject_type": "partner_operator"},
        {
            "subject_id": PARTNER,
            "subject_type": "partner_operator",
            "display_name": "forged",
        },
    ],
)
def test_store_rejects_malformed_or_extended_reference(ref):
    record = {**_record(_raw()), "verified_subject": ref}
    with pytest.raises(ReplicaStoreError, match="record_invalid"):
        ReplicaStore("unused", cipher=FieldCipher(b"e" * 32)).prepare_session(record)


@pytest.mark.parametrize(
    "extra",
    [
        {"agent_id": "daq-agent"},
        {"source_kind": "admin"},
        {"source_kind": "metabot"},
        {"arbitrary_metadata": {"token": "secret"}},
    ],
)
def test_store_rejects_wrong_scope_and_unknown_root_fields(extra):
    record = {**_record(_raw(), include_verified_subject=True), **extra}
    with pytest.raises(ReplicaStoreError, match="record_invalid"):
        ReplicaStore("unused", cipher=FieldCipher(b"e" * 32)).prepare_session(record)


@pytest.mark.parametrize(
    "change",
    [
        {"verified_subject": {"subject_id": "bad", "subject_type": "partner_operator"}},
        {
            "verified_subject": {
                "subject_id": PARTNER,
                "subject_type": "partner_operator",
                "name": "forged",
            }
        },
        {
            "verified_subject": {
                "subject_id": PARTNER,
                "subject_type": "partner_operator",
            },
            "agent_id": "daq-agent",
        },
        {"unrecognized": "metadata"},
    ],
)
def test_reader_rejects_invalid_new_reference_or_extra_root_fields(change):
    from test_cloud_repository import _row as encrypted_row

    record = {**_record(_raw()), **change}
    cipher = FieldCipher(b"e" * 32)
    repository = ReplicaObservabilityRepository("unused", cipher=cipher)
    with pytest.raises((ValueError, ReplicaStoreError)):
        repository._decrypt(encrypted_row(cipher, record))


@pytest.mark.parametrize(
    "args,expected",
    [(["export"], False), (["export", "--include-verified-subject"], True)],
)
def test_cli_verified_subject_flag_is_explicit_opt_in(monkeypatch, args, expected):
    from app.cloud_replica import cli

    captured = {}

    def export(clock, *, include_verified_subject=False):
        captured["enabled"] = include_verified_subject
        return 0

    monkeypatch.setattr(cli, "_export", export)
    assert cli.main(args) == 0
    assert captured["enabled"] is expected


@pytest.mark.parametrize("subject_type", ["enterprise_member", "partner_operator"])
def test_enabled_writer_keeps_unowned_legacy_record_in_old_shape(subject_type):
    raw = replace(_raw(subject_type), verified_subject=None)
    assert "verified_subject" not in _record(raw, include_verified_subject=True)


@pytest.mark.parametrize("option", ["true", "false", 1, 0, None])
def test_exporter_constructor_rejects_truthy_or_non_boolean_flags(tmp_path, option):
    with pytest.raises(ValueError, match="include_verified_subject"):
        _exporter(tmp_path, option)


@pytest.mark.parametrize(
    "ref", ["claimed", {"subject_id": PARTNER, "subject_type": "partner_operator"}]
)
def test_sanitizer_requires_typed_reference(ref):
    with pytest.raises(ValueError, match="verified_subject"):
        sanitize_session(replace(_raw(), verified_subject=ref), POLICY)
