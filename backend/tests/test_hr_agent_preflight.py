import base64
import hashlib
import json
from pathlib import Path

import pytest
from tests.hr_agent_support import hr_agent_database
from tools.hr_agent.preflight import build_report, main, read_environment_file


def _write_json(path: Path, value) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return path


def _knowledge(root: Path, release="release-1") -> Path:
    root.mkdir()
    role = root / "role.md"
    role.write_text("role", encoding="utf-8")
    method = root / "method.md"
    method.write_text("method", encoding="utf-8")
    manifest = {
        "release_id": release,
        "role": {
            "path": "role.md",
            "sha256": hashlib.sha256(role.read_bytes()).hexdigest(),
        },
        "resources": [
            {
                "ref": {
                    "kind": "method",
                    "id": "method-1",
                    "revision": release,
                    "sha256": hashlib.sha256(method.read_bytes()).hexdigest(),
                },
                "path": "method.md",
                "title": "Method",
                "description": "Method",
                "objects": [],
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _environment(root: Path, *, knowledge=None, attachments=True, revision="r1"):
    root.mkdir()
    work = root / "work"
    work.mkdir(mode=0o700)
    credential = root / "credential"
    credential.write_text("gateway-secret", encoding="utf-8")
    credential.chmod(0o600)
    content = _write_json(
        root / "hr-content.json",
        {
            "purpose": "platform-content-encryption",
            "active_version": 1,
            "keys": {"1": base64.b64encode(b"h" * 32).decode()},
        },
    )
    attachment_content = _write_json(
        root / "attachment-content.json",
        {
            "purpose": "platform-content-encryption",
            "active_version": 1,
            "keys": {"1": base64.b64encode(b"a" * 32).decode()},
        },
    )
    provider = _write_json(
        root / "provider.json",
        {
            "id": "approved-gateway-shape",
            "revision": revision,
            "protocol": "anthropic_messages_sse",
            "endpoint": "https://private-gateway.invalid/v1/messages",
            "model": "claude-opus-5",
            "credential_file": str(credential),
            "tokenizer": "conservative_utf8",
            "context_window_tokens": 200000,
            "timeout_seconds": 300,
        },
    )
    budget = _write_json(
        root / "budget.json",
        {
            "id": "d7-candidate",
            "limits": {
                "model_calls": 32,
                "total_tokens": 600000,
                "active_seconds": 900,
            },
            "service_limits": {
                "model_calls": 64,
                "total_tokens": 1200000,
                "active_seconds": 1800,
            },
            "reserve": {
                "model_calls": 2,
                "total_tokens": 16384,
                "active_seconds": 30,
            },
            "max_output_tokens": 16384,
            "input_target_tokens": 20000,
            "input_trigger_tokens": 30000,
            "work_retention_seconds": 3600,
        },
    )
    diagnostic = _write_json(root / "diagnostic.json", {"enabled": False})
    knowledge = knowledge or _knowledge(root / "knowledge")
    env = {
        "PLATFORM_HR_AGENT_ENABLED": "1",
        "PLATFORM_HR_AGENT_CONTENT_KEYRING_FILE": str(content),
        "PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE": str(provider),
        "PLATFORM_HR_AGENT_BUDGET_PROFILE_FILE": str(budget),
        "PLATFORM_HR_AGENT_DIAGNOSTIC_PROFILE_FILE": str(diagnostic),
        "PLATFORM_HR_AGENT_KNOWLEDGE_DIR": str(knowledge),
        "PLATFORM_HR_AGENT_WORK_DIR": str(work),
        "PLATFORM_CONVERSATION_ATTACHMENT_ENABLED": "1" if attachments else "0",
    }
    if attachments:
        for name in (
            "PLATFORM_ATTACHMENT_S3_ACCESS_KEY_FILE",
            "PLATFORM_ATTACHMENT_S3_SECRET_KEY_FILE",
            "PLATFORM_ATTACHMENT_CONTROL_DATABASE_URL_FILE",
        ):
            secret = root / name.lower()
            secret.write_text("secret", encoding="utf-8")
            secret.chmod(0o600)
            env[name] = str(secret)
        env.update(
            {
                "PLATFORM_ATTACHMENT_S3_ENDPOINT": "http://minio.internal:9000",
                "PLATFORM_ATTACHMENT_S3_BUCKET": "attachments",
                "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE": str(attachment_content),
            }
        )
    return env


def test_environment_file_must_be_private_and_has_no_shell_evaluation(tmp_path):
    path = tmp_path / "runtime.env"
    path.write_text("A=literal $(touch /tmp/must-not-exist)\n", encoding="utf-8")
    path.chmod(0o600)
    assert read_environment_file(path) == {"A": "literal $(touch /tmp/must-not-exist)"}
    path.chmod(0o644)
    with pytest.raises(ValueError, match="runtime environment unavailable"):
        read_environment_file(path)


def test_missing_configuration_is_generic_and_never_echoes_gateway_error():
    report = build_report(
        {
            "PLATFORM_HR_AGENT_ENABLED": "1",
            "PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE": "https://secret.example/token",
        },
        {},
    )
    encoded = json.dumps(report, sort_keys=True)
    assert report["ok"] is False
    assert "configuration_invalid" in report["blockers"]
    assert "secret.example" not in encoded
    assert "token" not in encoded


def test_matching_reports_use_exact_validated_identities_but_do_not_certify_production(
    tmp_path,
):
    knowledge = _knowledge(tmp_path / "knowledge")
    api = _environment(tmp_path / "api", knowledge=knowledge)
    worker = dict(api)
    report = build_report(api, worker)
    assert report["runtime_match"] is True
    assert report["api"]["provider"] == {
        "id": "approved-gateway-shape",
        "revision": "r1",
    }
    assert report["api"]["knowledge"]["release_id"] == "release-1"
    assert len(report["api"]["configuration_fingerprint"]) == 64
    assert report["production_certified"] is False
    assert "d7_product_approval_absent" in report["blockers"]
    assert "personal_processing_authorizer_absent" in report["blockers"]
    assert report["limitations"]["process_check"] == "not_checked_by_tool"
    assert report["limitations"]["process_existence_is_functionality_evidence"] is False


def test_profile_mismatch_and_attachment_asymmetry_block(tmp_path):
    knowledge = _knowledge(tmp_path / "knowledge")
    api = _environment(tmp_path / "api", knowledge=knowledge, revision="api")
    worker = _environment(
        tmp_path / "worker", knowledge=knowledge, revision="worker", attachments=False
    )
    report = build_report(api, worker)
    assert report["runtime_match"] is False
    assert "runtime_identity_mismatch" in report["blockers"]
    assert "attachment_wiring_asymmetric" in report["blockers"]


def test_budget_metadata_is_never_rendered_even_when_it_contains_secrets(tmp_path):
    knowledge = _knowledge(tmp_path / "knowledge")
    environment = _environment(tmp_path / "runtime", knowledge=knowledge)
    budget_path = Path(environment["PLATFORM_HR_AGENT_BUDGET_PROFILE_FILE"])
    budget = json.loads(budget_path.read_text())
    budget["id"] = {
        "endpoint": "https://SECRET-SENTINEL.invalid",
        "credential": "SECRET-SENTINEL",
    }
    budget_path.write_text(json.dumps(budget), encoding="utf-8")
    report = build_report(environment, dict(environment))
    encoded = json.dumps(report, sort_keys=True)
    assert "SECRET-SENTINEL" not in encoded
    assert report["api"]["budget"] == {
        "fingerprint": hashlib.sha256(budget_path.read_bytes()).hexdigest()
    }


def test_public_only_scope_does_not_conflate_candidate_privacy_policy(tmp_path):
    knowledge = _knowledge(tmp_path / "knowledge")
    environment = _environment(tmp_path / "runtime", knowledge=knowledge)
    report = build_report(environment, dict(environment), launch_scope="public-only")
    assert "personal_processing_authorizer_absent" not in report["blockers"]
    assert report["launch_scope"] == "public-only"
    assert report["full_candidate_ready"] is False


def test_knowledge_changed_after_load_fails_closed(tmp_path):
    knowledge = _knowledge(tmp_path / "knowledge")
    api = _environment(tmp_path / "api", knowledge=knowledge)
    worker = _environment(tmp_path / "worker", knowledge=knowledge)
    (knowledge / "method.md").write_text("changed", encoding="utf-8")
    report = build_report(api, worker)
    assert report["ok"] is False
    assert "knowledge_invalid" in report["blockers"]
    assert "changed" not in json.dumps(report)


def test_database_readiness_reports_exact_migrations_without_writes(tmp_path):
    knowledge = _knowledge(tmp_path / "knowledge")
    api = _environment(tmp_path / "api", knowledge=knowledge)
    worker = _environment(tmp_path / "worker", knowledge=knowledge)
    with hr_agent_database() as database:
        with database.connection() as connection:
            before = connection.execute(
                "select version,sha256 from platform_control.schema_migrations order by version"
            ).fetchall()
        report = build_report(api, worker, connection_factory=database.connection)
        with database.connection() as connection:
            after = connection.execute(
                "select version,sha256 from platform_control.schema_migrations order by version"
            ).fetchall()
    assert before == after
    assert report["database"]["schema_ready"] is True
    assert [item["version"] for item in report["database"]["migrations"]] == [
        96,
        97,
        98,
        99,
        100,
        101,
        102,
        103,
        104,
    ]
    assert all(item["match"] for item in report["database"]["migrations"])
    assert report["database"]["permissions_complete"] is True
    assert report["database"]["permissions"] == {
        "app_gate_select": True,
        "app_gate_write": False,
        "app_admin_execute": False,
        "maintenance_admin_execute": True,
    }


def test_database_permission_probe_fails_when_maintenance_cannot_transition(tmp_path):
    knowledge = _knowledge(tmp_path / "knowledge")
    environment = _environment(tmp_path / "runtime", knowledge=knowledge)
    with hr_agent_database(cutover_phase="legacy") as database:
        with database.admin_connection() as connection:
            connection.execute(
                "revoke execute on function "
                "platform_control.transition_hr_execution_cutover_v102(text,uuid) "
                "from platform_control_maintenance"
            )
        report = build_report(
            environment, dict(environment), connection_factory=database.connection
        )
    assert report["database"]["permissions_complete"] is False
    assert "cutover_permissions_incomplete" in report["blockers"]


def test_cli_stdout_is_json_and_scrubs_raw_exceptions(tmp_path, capsys):
    bad = tmp_path / "bad.env"
    bad.write_text("PLATFORM_HR_AGENT_ENABLED=1\n", encoding="utf-8")
    bad.chmod(0o600)
    status = main(
        [
            "--scope",
            "public-only",
            "--api-env-file",
            str(bad),
            "--worker-env-file",
            str(bad),
        ]
    )
    output = capsys.readouterr().out
    assert status == 1
    assert json.loads(output)["ok"] is False
    assert str(tmp_path) not in output


@pytest.mark.parametrize("version", [103, 104])
@pytest.mark.parametrize("receipt", [None, "0" * 64])
def test_database_readiness_requires_exact_drain_occupancy_migration(tmp_path, receipt, version):
    knowledge = _knowledge(tmp_path / "knowledge")
    environment = _environment(tmp_path / "runtime", knowledge=knowledge)
    with hr_agent_database() as database:
        with database.admin_connection() as connection:
            if receipt is None:
                connection.execute("delete from platform_control.schema_migrations where version=%s", (version,))
            else:
                connection.execute("update platform_control.schema_migrations set sha256=%s where version=%s", (receipt, version))
        report = build_report(environment, dict(environment), connection_factory=database.connection)
    assert report["ok"] is False
    assert "migration_identity_mismatch" in report["blockers"]
    entry = next(item for item in report["database"]["migrations"] if item["version"] == version)
    assert entry["match"] is False
