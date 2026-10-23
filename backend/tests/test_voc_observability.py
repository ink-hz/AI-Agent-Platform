import json
from datetime import datetime, timezone

import pytest

from app.sync_remote.config import default_sources
from app.sync_remote.export import ExportError, build_remote_program, parse_export
from app.sync_remote.importer import ImportValidationError, normalize_row, validate_bundle

NOW = datetime(2026, 10, 23, 8, tzinfo=timezone.utc)
OWNER = "11111111-1111-4111-8111-111111111111"


def source_rows():
    return [
        dict(id=1, internal_user_id=OWNER, conversation_id="private-conversation",
             conversation_type=1, sender_name="员工一", sender_department="FAE",
             received_at="2026-10-23T07:30:00+00:00", question_at="2026-10-23T07:29:59+00:00",
             question="客户正在评估传感器替换\n主要考虑价格", answer="已整理草稿，请确认。",
             answer_at="2026-10-23T07:30:08+00:00", job_status="done", delivery_status="sent"),
        dict(id=2, internal_user_id=OWNER, conversation_id="private-conversation",
             conversation_type=1, sender_name="员工一", sender_department="FAE",
             received_at="2026-10-23T07:31:00+00:00", question_at=None,
             question="补充客户信息", answer="must-not-show-unsent", answer_at=None,
             job_status="pending", delivery_status="pending"),
    ]


def bundle_text(tables):
    records = [dict(record_type="source_identity", agent_id="voc", schema="voc_raw/voc_core")]
    for table, rows in tables.items():
        records.extend(dict(record_type="table_row", table=table, row=row) for row in rows)
        records.append(dict(record_type="table_complete", table=table, count=len(rows)))
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in records)


def test_voc_export_is_fixed_readonly_and_has_no_attachment_or_credential_reads():
    source = default_sources("host", "key")["voc"]
    program = build_remote_program(source)
    assert source.remote_container is None
    assert "REPEATABLE READ READ ONLY" in program
    assert "orbbec-voc-postgres-1" in program
    assert "voc_raw.inbound_messages" in program
    assert "voc_core.outbox_messages" in program
    assert "020_employee_chatbot_experience.sql" in program
    assert "select *" not in program.lower()
    for forbidden in ("sender_staff_id", "raw_payload_sanitized", "attachments", "client_secret", "DATABASE_URL", "sessionWebhook", "downloadCode"):
        assert forbidden not in program
    compile(program, "<voc-export>", "exec")


def test_voc_projection_preserves_real_delivery_and_stable_owner_scoped_keys():
    from app.sync_remote.voc_export import project_voc_rows

    tables = project_voc_rows(source_rows())
    session = tables["chat_sessions"][0]
    first, second = tables["chat_turns"]
    assert len(tables["chat_sessions"]) == 1
    assert session["owner_subject_id"] == OWNER
    assert first["answer"] == "已整理草稿，请确认。"
    assert first["duration_ms"] == 8000
    assert second["answer"] == "" and second["answer_at"] is None
    assert second["outcome"] == "delivery_pending"
    assert first["trace_id"] is None and first["fallback_used"] is None
    assert "private-conversation" not in json.dumps(tables)
    assert project_voc_rows(list(reversed(source_rows()))) == tables
    rows = source_rows()
    rows[1]["internal_user_id"] = "22222222-2222-4222-8222-222222222222"
    assert len(project_voc_rows(rows)["chat_sessions"]) == 2
    for row in rows:
        row["internal_user_id"] = None
    anonymous = project_voc_rows(rows)["chat_sessions"]
    assert len(anonymous) == 2
    assert all(s["owner_subject_id"] is None for s in anonymous)


def test_voc_parser_requires_complete_authenticated_source_snapshot():
    from app.sync_remote.voc_export import project_voc_rows

    text = bundle_text(project_voc_rows(source_rows()))
    bundle = parse_export("voc", text)
    assert bundle.malformed_lines == 0
    assert validate_bundle(bundle)["orphan_turn_sessions"] == 0
    with pytest.raises(ExportError):
        parse_export("voc", "")
    with pytest.raises(ExportError):
        parse_export("voc", text.replace('"agent_id": "voc"', '"agent_id": "fae"', 1))
    with pytest.raises(ExportError):
        parse_export("voc", "\n".join(text.splitlines()[:-1]))


def test_voc_normalization_uses_only_its_two_tables_and_canonical_owner():
    from app.sync_remote.voc_export import project_voc_rows

    tables = project_voc_rows(source_rows())
    normalized = normalize_row("voc", "chat_sessions", tables["chat_sessions"][0], NOW)
    assert normalized.target_schema == "platform_source_voc"
    assert normalized.values["owner_subject_id"] == OWNER
    assert "verified_subject" not in normalized.values["details"]
    spoofed = dict(tables["chat_sessions"][0], owner_subject_id=None, metadata={
        "verified_subject": {"subject_id": OWNER, "subject_type": "enterprise_member"}
    })
    assert "verified_subject" not in normalize_row("voc", "chat_sessions", spoofed, NOW).values["details"]
    with pytest.raises(ImportValidationError):
        normalize_row("voc", "turn_feedback", {}, NOW)


def test_voc_cli_has_no_review_writer_dependency(monkeypatch, capsys):
    from types import SimpleNamespace
    from app.sync_remote import cli
    from app.sync_remote.importer import SyncResult
    from app.sync_remote.voc_export import project_voc_rows

    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(
        sync_database_url="test-dsn", remote_ssh_host="host", remote_ssh_key_path="key"
    ))
    monkeypatch.setattr(cli, "resolve_review_database_url", lambda *_: pytest.fail("VOC must not request review credentials"))
    monkeypatch.setattr(cli, "required_voc_schema_available", lambda _: True)
    bundle = parse_export("voc", bundle_text(project_voc_rows(source_rows())))
    monkeypatch.setattr(cli, "export_source", lambda _: bundle)
    monkeypatch.setattr(cli, "import_bundle", lambda *_: SyncResult("run", "voc", "succeeded", {}, {}, {}))
    assert cli.main(["--source", "voc"]) == 0
    assert '"reason": "voc_observability_only"' in capsys.readouterr().out


def test_voc_schema_preflight_precedes_source_export(monkeypatch, capsys):
    from types import SimpleNamespace
    from app.sync_remote import cli

    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(
        sync_database_url="test-dsn", remote_ssh_host="host", remote_ssh_key_path="key"
    ))
    monkeypatch.setattr(cli, "required_voc_schema_available", lambda _: False)
    monkeypatch.setattr(cli, "export_source", lambda _: pytest.fail("No export before schema verification"))
    assert cli.main(["--source", "voc"]) == 1
    assert capsys.readouterr().err == "voc: schema_preflight_failed\n"
