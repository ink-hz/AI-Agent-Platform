from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from app.execution_relay import acceptance_cli
from app.execution_relay import register_worker


RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
CONVERSATION_ID = UUID("22222222-2222-4222-8222-222222222222")
MESSAGE_ID = UUID("33333333-3333-4333-8333-333333333333")


def _enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "acceptance"
    root.mkdir(mode=0o700)
    marker = root / "enabled"
    marker.write_text("AGENT_EXECUTION_RELAY_ACCEPTANCE_V1\n")
    marker.chmod(0o600)
    monkeypatch.setenv("PLATFORM_EXECUTION_RELAY_ACCEPTANCE_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_EXECUTION_RELAY_ACCEPTANCE_ROOT", str(root))
    monkeypatch.setenv(
        "PLATFORM_EXECUTION_RELAY_ACCEPTANCE_MARKER_FILE", str(marker)
    )
    monkeypatch.setenv(
        "PLATFORM_CONTROL_DATABASE_URL_FILE", str(root / "control-database-url")
    )
    monkeypatch.setenv(
        "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE", str(root / "content-keyring")
    )
    for name in ("control-database-url", "content-keyring"):
        path = root / name
        path.write_text("placeholder")
        path.chmod(0o600)
    return root


def test_cli_is_default_unavailable_and_never_echoes_exception(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PLATFORM_EXECUTION_RELAY_ACCEPTANCE_ENABLED", raising=False)
    assert acceptance_cli.main(["inspect", str(RUN_ID)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "EXECUTION_RELAY_ACCEPTANCE_FAILED\n"


def test_enqueue_is_exact_agent_and_fixed_synthetic_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _enabled(tmp_path, monkeypatch)
    captured_payloads = []

    class Repository:
        def enqueue(self, payload):
            captured_payloads.append(payload)
            return UUID("44444444-4444-4444-8444-444444444444")

    monkeypatch.setattr(acceptance_cli, "_repository", lambda _root: Repository())
    arguments = [
        "enqueue",
        "hr-bot",
        str(RUN_ID),
        str(CONVERSATION_ID),
        str(MESSAGE_ID),
    ]
    assert acceptance_cli.main(arguments) == 0
    assert json.loads(capsys.readouterr().out) == {
        "job_id": "44444444-4444-4444-8444-444444444444",
        "run_id": str(RUN_ID),
        "status": "queued",
    }
    payload = captured_payloads[0]
    assert payload.agent_id == "hr-bot"
    assert payload.prompt == f"relay acceptance synthetic run {RUN_ID}"
    assert payload.max_turns == 2

    arguments[1] = "fae-bot"
    assert acceptance_cli.main(arguments) == 1
    assert "fae-bot" not in capsys.readouterr().err


def test_inspect_returns_only_bounded_state_and_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _enabled(tmp_path, monkeypatch)
    monkeypatch.setattr(
        acceptance_cli,
        "_inspect",
        lambda selected_root, run_id: {
            "run_id": str(run_id),
            "agent_id": "marketing-intelligence-bot",
            "status": "completed",
            "event_count": 3,
            "first_seq": 1,
            "last_seq": 3,
            "ordered_terminal": True,
        }
        if selected_root == root
        else pytest.fail("wrong root"),
    )
    assert acceptance_cli.main(["inspect", str(RUN_ID)]) == 0
    assert json.loads(capsys.readouterr().out)["ordered_terminal"] is True


def test_interrupt_is_bounded_to_acceptance_tagged_exact_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _enabled(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        acceptance_cli,
        "_interrupt",
        lambda selected_root, run_id: calls.append((selected_root, run_id)) or True,
    )
    assert acceptance_cli.main(["interrupt", str(RUN_ID)]) == 0
    assert calls == [(root, RUN_ID)]
    assert json.loads(capsys.readouterr().out) == {
        "run_id": str(RUN_ID),
        "status": "interrupted",
    }


def test_cli_rejects_bad_root_marker_permissions_extra_args_and_unknown_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _enabled(tmp_path, monkeypatch)
    (root / "enabled").chmod(0o644)
    assert acceptance_cli.main(["inspect", str(RUN_ID)]) == 1
    (root / "enabled").chmod(0o600)
    root.chmod(0o755)
    assert acceptance_cli.main(["inspect", str(RUN_ID)]) == 1
    root.chmod(0o700)
    assert acceptance_cli.main(["inspect", str(RUN_ID), "extra"]) == 1
    assert acceptance_cli.main(["delete", str(RUN_ID)]) == 1


def test_registration_accepts_only_bounded_disposable_worker_shape(tmp_path: Path) -> None:
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    document = root / "worker.json"
    base = {
        "worker_id": "relay-acceptance-0123456789abcdef",
        "key_id": "worker-v1",
        "public_key_base64url": "A" * 43,
        "allowed_agent_ids": ["hr-bot"],
    }

    document.write_text(json.dumps(base))
    document.chmod(0o600)
    worker_id, key_id, public_key, agents = register_worker._public_document(
        str(document)
    )
    assert (worker_id, key_id, len(public_key), agents) == (
        base["worker_id"],
        "worker-v1",
        32,
        ("hr-bot",),
    )

    for mutation in (
        {"worker_id": "relay-acceptance-too-short"},
        {"key_id": "worker-v2"},
        {"allowed_agent_ids": ["marketing-intelligence-bot"]},
        {"allowed_agent_ids": ["hr-bot", "fae-bot"]},
        {"allowed_agent_ids": list(register_worker._ALLOWED_AGENTS)},
    ):
        document.write_text(json.dumps({**base, **mutation}))
        document.chmod(0o600)
        with pytest.raises(ValueError):
            register_worker._public_document(str(document))
