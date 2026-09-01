from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from app.review.credentials import CredentialResolver, CredentialUnavailable
from app.review.replay import (
    ReplayInput,
    ReplayRunner,
    RuntimeExchange,
    evaluate_runtime_gate,
    parse_sse,
)
from app.review.repository import InvalidReviewMutation


LINK_ID = UUID("00000000-0000-0000-0000-000000000011")
ISSUE_ID = UUID("00000000-0000-0000-0000-000000000012")
DEPLOYMENT_SHA = "a" * 40


@pytest.fixture
def valid_exchange():
    return RuntimeExchange(
        target_safe=True,
        health={
            "status": "ok",
            "environment": "development",
            "llm_model": "claude-opus-4-8",
            "build": {
                "available": True,
                "release_name": "ai-fae-agent-test",
                "git_sha": DEPLOYMENT_SHA,
            },
        },
        expected_version="ai-fae-agent-test",
        expected_git_sha=DEPLOYMENT_SHA,
        execution_status="succeeded",
        answer="可交付答案",
        sources=[{"title": "source"}],
        done={
            "fallback_used": False,
            "trace_id": "trace-1",
            "loop": {
                "truncation_rounds": 0,
                "configured_model": "claude-opus-4-8",
                "actual_provider_model": "claude-opus-4-8",
                "provider_model_echo": {"complete": True, "consistent": True},
            },
        },
        trace_id="trace-1",
    )


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (lambda item: setattr(item, "health", {"status": "ok"}), "build_identity_mismatch"),
        (
            lambda item: item.health["build"].update({"git_sha": "b" * 40}),
            "build_identity_mismatch",
        ),
        (
            lambda item: item.done["loop"].pop("provider_model_echo"),
            "model_echo_unavailable",
        ),
        (
            lambda item: item.done["loop"].update(
                {"actual_provider_model": "different-model"}
            ),
            "actual_model_mismatch",
        ),
        (lambda item: item.done.update({"fallback_used": True}), "fallback_used"),
        (lambda item: setattr(item, "answer", ""), "empty_answer"),
        (
            lambda item: item.done["loop"].update({"truncation_rounds": 1}),
            "truncated",
        ),
        (lambda item: setattr(item, "trace_id", ""), "trace_missing"),
        (
            lambda item: item.done.update({"protocol_error": "bad event"}),
            "protocol_error",
        ),
    ],
)
def test_runtime_gate_rejects_incomplete_execution(
    valid_exchange, mutator, reason
):
    exchange = deepcopy(valid_exchange)
    mutator(exchange)

    result = evaluate_runtime_gate(exchange)

    assert result.passed is False
    assert result.reason == reason


def test_runtime_gate_ignores_empty_planner_observability(valid_exchange):
    valid_exchange.done["planned_capabilities"] = []
    valid_exchange.done["capability_coverage"] = {}

    assert evaluate_runtime_gate(valid_exchange).passed is True


class FakeRepository:
    def __init__(self, attachment_manifest=None):
        self.finished = []
        self.expired = []
        self.input = ReplayInput(
            issue_id=ISSUE_ID,
            issue_link_id=LINK_ID,
            agent_id="ai-fae-agent",
            source_turn_key="fae:turn-1",
            question="问题",
            prior_turns=[],
            attachment_manifest=attachment_manifest or [],
        )

    def load_replay_input(self, issue_link_id):
        assert issue_link_id == LINK_ID
        return self.input

    def get_verified_deployment(self, issue_id):
        assert issue_id == ISSUE_ID
        return {
            "version": "release",
            "git_sha": DEPLOYMENT_SHA,
        }

    def create_or_get_replay(self, issue_link_id, **kwargs):
        assert kwargs["expected_ownership"] == {
            "issue_id": ISSUE_ID,
            "agent_id": "ai-fae-agent",
            "source_turn_key": "fae:turn-1",
        }
        return (
            {
                "id": UUID("00000000-0000-0000-0000-000000000013"),
                "issue_id": ISSUE_ID,
                "issue_link_id": issue_link_id,
                "execution_status": "running",
                **kwargs["expected"],
            },
            True,
        )

    def expire_stale_replays(self, issue_link_id, *, expected_ownership, timeout_seconds, actor):
        assert expected_ownership == {
            "issue_id": ISSUE_ID, "agent_id": "ai-fae-agent",
            "source_turn_key": "fae:turn-1",
        }
        self.expired.append((issue_link_id, timeout_seconds, actor))
        return 0

    def finish_replay(self, replay_id, result, *, actor):
        row = {"id": replay_id, "issue_id": ISSUE_ID, **result}
        self.finished.append((row, actor))
        return row


def test_move_between_replay_load_and_insert_is_rejected_before_network(monkeypatch):
    monkeypatch.setenv("FAE_DEV_KEY", "secret")
    repository = FakeRepository()
    repository.create_or_get_replay = Mock(
        side_effect=InvalidReviewMutation("replay link ownership changed")
    )
    client = Mock()

    with pytest.raises(InvalidReviewMutation, match="ownership changed"):
        ReplayRunner(repository, _registry("http://dev.example"), http_client=client).run(
            LINK_ID, idempotency_key="raced", actor="corp:owner"
        )

    client.get.assert_not_called()
    client.post.assert_not_called()


def _registry(dev_url, prod_url="http://prod.example"):
    agent = SimpleNamespace(
        flywheel_agent_id="ai-fae-agent",
        api_base=prod_url,
        health=SimpleNamespace(url=f"{prod_url}/health"),
        replay_targets=[
            SimpleNamespace(
                environment="dev",
                api_base=dev_url,
                health_url=f"{dev_url}/health",
                credential_ref="env:FAE_DEV_KEY",
            )
        ],
    )
    return SimpleNamespace(
        get_agent_by_flywheel_id=lambda agent_id: (
            agent if agent_id == "ai-fae-agent" else None
        )
    )


def test_default_replay_http_client_never_uses_environment_proxy():
    runner = ReplayRunner(None, None)
    try:
        assert runner.http_client._trust_env is False
    finally:
        runner.close()


@pytest.mark.parametrize(
    ("dev_url", "prod_url"),
    [
        ("http://prod.example", "http://prod.example"),
        ("https://prod.example:8443", "http://prod.example"),
        ("not-a-url", "http://prod.example"),
    ],
)
def test_production_equivalent_target_is_blocked_without_network(
    dev_url, prod_url, monkeypatch
):
    monkeypatch.setenv("FAE_DEV_KEY", "secret")
    repository = FakeRepository()
    client = Mock()
    runner = ReplayRunner(
        repository,
        _registry(dev_url, prod_url),
        http_client=client,
    )

    run = runner.run(LINK_ID, idempotency_key="req-1", actor="codex")

    assert run.execution_status == "blocked"
    assert run.runtime_failure_reason == "unsafe_replay_target"
    assert run.done["safety_stage"] == "static_target"
    client.get.assert_not_called()
    client.post.assert_not_called()


def test_target_matching_production_health_host_is_blocked_without_network(
    monkeypatch,
):
    monkeypatch.setenv("FAE_DEV_KEY", "secret")
    repository = FakeRepository()
    client = Mock()
    registry = _registry("http://health.prod.example", "http://api.prod.example")
    agent = registry.get_agent_by_flywheel_id("ai-fae-agent")
    agent.health.url = "http://health.prod.example/health"

    run = ReplayRunner(repository, registry, http_client=client).run(
        LINK_ID,
        idempotency_key="req-1",
        actor="codex",
    )

    assert run.runtime_failure_reason == "unsafe_replay_target"
    client.get.assert_not_called()


def test_idempotent_replay_returns_existing_row_without_network(monkeypatch):
    monkeypatch.setenv("FAE_DEV_KEY", "secret")
    repository = FakeRepository()
    existing = {
        "id": UUID("00000000-0000-0000-0000-000000000013"),
        "issue_id": ISSUE_ID,
        "issue_link_id": LINK_ID,
        "execution_status": "succeeded",
        "runtime_gate": "passed",
        "runtime_failure_reason": "",
    }
    repository.create_or_get_replay = Mock(return_value=(existing, False))
    client = Mock()

    run = ReplayRunner(
        repository,
        _registry("http://dev.example"),
        http_client=client,
    ).run(LINK_ID, idempotency_key="same-request", actor="codex")

    assert run.id == existing["id"]
    assert run.runtime_gate == "passed"
    client.get.assert_not_called()
    client.post.assert_not_called()
    assert repository.expired == [(LINK_ID, 1200, "codex")]


def test_credential_resolver_supports_env_without_serializing_secret(monkeypatch):
    monkeypatch.setenv("FAE_DEV_KEY", "secret")
    credential = CredentialResolver().resolve("env:FAE_DEV_KEY")

    assert credential.headers() == {"Authorization": "Bearer secret"}
    assert "secret" not in repr(credential)


def test_missing_credential_records_safe_diagnostic_stage(monkeypatch):
    monkeypatch.delenv("FAE_DEV_KEY", raising=False)
    repository = FakeRepository()
    client = Mock()

    run = ReplayRunner(
        repository,
        _registry("http://dev.example"),
        http_client=client,
    ).run(LINK_ID, idempotency_key="req-no-credential", actor="codex")

    assert run.runtime_failure_reason == "unsafe_replay_target"
    assert run.done == {"safety_stage": "credential"}
    client.get.assert_not_called()


def test_health_identity_failure_records_only_sanitized_diagnostics(monkeypatch):
    monkeypatch.setenv("FAE_DEV_KEY", "secret")
    repository = FakeRepository()
    client = Mock()
    client.get.side_effect = [
        SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"status": "ok", "environment": "production"},
        ),
        SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"status": "ok", "environment": "production"},
        ),
    ]

    run = ReplayRunner(
        repository,
        _registry("http://dev.example"),
        http_client=client,
    ).run(LINK_ID, idempotency_key="req-health-mismatch", actor="codex")

    assert run.runtime_failure_reason == "unsafe_replay_target"
    assert run.done == {
        "safety_stage": "health_identity",
        "safety_details": {
            "category": "identity_mismatch",
            "dev_status": "ok",
            "dev_environment": "production",
            "prod_status": "ok",
            "prod_environment": "production",
        },
    }
    assert "secret" not in str(run.done)


def test_credential_resolver_reads_private_file_reference(tmp_path):
    path = tmp_path / "replay-token"
    path.write_text("token\n", encoding="utf-8")
    path.chmod(0o600)

    credential = CredentialResolver().resolve(f"file:{path}")

    assert credential.headers() == {"Authorization": "Bearer token"}


def test_credential_resolver_rejects_insecure_file(tmp_path):
    path = tmp_path / "replay-token"
    path.write_text("token\n", encoding="utf-8")
    path.chmod(0o644)

    with pytest.raises(CredentialUnavailable):
        CredentialResolver().resolve(f"file:{path}")


def test_credential_resolver_rejects_retired_keychain_reference():
    retired_reference = "key" + "chain:ai-fae-dev-api/neo"

    with pytest.raises(CredentialUnavailable, match="unsupported"):
        CredentialResolver().resolve(retired_reference)


def test_parse_sse_collects_named_json_events():
    events = parse_sse(
        [
            "event: session",
            'data: {"session_id":"s1"}',
            "",
            "event: text_delta",
            'data: {"delta":"答案"}',
            "",
            "event: done",
            'data: {"trace_id":"t1"}',
            "",
        ]
    )

    assert [event["event"] for event in events] == [
        "session",
        "text_delta",
        "done",
    ]
    assert events[1]["data"]["delta"] == "答案"


def test_attachment_without_approved_asset_blocks_before_chat(monkeypatch):
    monkeypatch.setenv("FAE_DEV_KEY", "secret")
    repository = FakeRepository(
        [{"source_id": "attachment-1", "available": False}]
    )
    client = Mock()
    client.get.side_effect = [
        SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"status": "ok", "environment": "development"},
        ),
        SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"status": "ok", "environment": "production"},
        ),
    ]
    runner = ReplayRunner(
        repository,
        _registry("http://dev.example"),
        http_client=client,
    )

    run = runner.run(LINK_ID, idempotency_key="req-1", actor="codex")

    assert run.execution_status == "blocked"
    assert run.runtime_failure_reason == "missing_replay_input"
    client.post.assert_not_called()


def test_approved_attachment_hash_must_match(tmp_path):
    asset = tmp_path / "image.jpg"
    asset.write_bytes(b"not the expected content")
    manifest = {
        "path": str(asset),
        "sha256": "0" * 64,
        "approved_for_dev": True,
    }

    assert ReplayRunner.validate_attachment(manifest) is False


def test_successful_sse_replay_persists_answer_and_runtime_identity(monkeypatch):
    monkeypatch.setenv("FAE_DEV_KEY", "secret")
    repository = FakeRepository()

    class Response:
        def __init__(self, payload=None, lines=None):
            self.payload = payload
            self.lines = lines or []

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_lines(self):
            return iter(self.lines)

    class Client:
        def get(self, url, **_kwargs):
            environment = "development" if "dev.example" in url else "production"
            return Response(
                {
                    "status": "ok",
                    "environment": environment,
                    "llm_model": "claude-opus-4-8",
                    "build": {
                        "available": True,
                        "release_name": "release",
                        "git_sha": DEPLOYMENT_SHA,
                    },
                }
            )

        def stream(self, *_args, **_kwargs):
            done = {
                "fallback_used": False,
                "trace_id": "trace-real",
                "loop": {
                    "truncation_rounds": 0,
                    "configured_model": "claude-opus-4-8",
                    "actual_provider_model": "claude-opus-4-8",
                    "provider_model_echo": {
                        "complete": True,
                        "consistent": True,
                    },
                },
            }
            return Response(
                lines=[
                    "event: session",
                    'data: {"session_id":"dev-session"}',
                    "",
                    "event: text_delta",
                    'data: {"delta":"最新答案"}',
                    "",
                    "event: sources",
                    'data: [{"title":"official"}]',
                    "",
                    "event: done",
                    f"data: {__import__('json').dumps(done)}",
                    "",
                ]
            )

    run = ReplayRunner(
        repository,
        _registry("http://dev.example"),
        http_client=Client(),
    ).run(LINK_ID, idempotency_key="real-request", actor="codex")

    assert run.execution_status == "succeeded"
    assert run.runtime_gate == "passed"
    assert run.answer == "最新答案"
    assert run.sources == [{"title": "official"}]
    assert run.trace_id == "trace-real"
    assert run.actual_model == "claude-opus-4-8"
    assert run.actual_model_source == "provider_message_start"
