from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import respx

from app.execution_relay import metabot_client
from app.execution_relay.metabot_client import (
    MetaBotClient,
    MetaBotClientError,
    MetaBotRuntimeMap,
)
from app.execution_relay.models import RelayJobPayload


RUN_ID = UUID("00000000-0000-4000-8000-000000000101")
CONVERSATION_ID = UUID("00000000-0000-4000-8000-000000000102")
TRIGGER_MESSAGE_ID = UUID("00000000-0000-4000-8000-000000000103")
APPROVED_BOTS = (
    "hr-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "fae-bot",
    "marketing-gtm-bot",
    "marketing-intelligence-bot",
    "agent-brain-bot",
)
CALLBACK_URL = (
    "http://127.0.0.1:9120/callbacks/"
    "00000000-0000-4000-8000-000000000101/"
    "bm9uLXNlY3JldC10ZXN0LXRva2Vu"
)
EXECUTION_CHAT_ID = "platform-00000000-0000-4000-8000-000000000102-hr-bot"


def _contract(path: Path, *, entries: list[dict[str, object]] | None = None) -> Path:
    bots = entries or [
        ({
            "name": name,
            "platform": "web",
            "platformOnly": True,
            "engine": "claude",
            "model": "claude-opus-5",
            "backend": "pty",
            "toolPolicy": "none",
            "workdir": "/Users/agentops/Developer/work/Orbbec-Agent-Team/bots/agent-brain",
            "instance": {
                "pm2Name": "metabot-agent-brain",
                "apiPort": 9110,
                "stateDir": "/Users/agentops/AgentRuntime/instances/agent-brain-bot/state",
                "configPath": "/Users/agentops/AgentRuntime/instances/agent-brain-bot/bots.json",
                "logDir": "/Users/agentops/AgentRuntime/instances/agent-brain-bot/logs",
            },
        } if name == "agent-brain-bot" else {
            "name": name,
            "instance": {"apiPort": 9200 + index},
        })
        for index, name in enumerate(APPROVED_BOTS)
    ]
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "bots": bots
                + [
                    {"name": "feishu-default", "instance": {"apiPort": 9301}},
                    {"name": "codex-assistant", "instance": {"apiPort": 9302}},
                    {"name": "test-bot", "instance": {"apiPort": 9303}},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _secret_file(tmp_path: Path, value: str = "local-bearer-secret") -> Path:
    parent = tmp_path / "secrets"
    parent.mkdir(mode=0o700, exist_ok=True)
    path = parent / "metabot.token"
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _payload(agent_id: str = "hr-bot") -> RelayJobPayload:
    return RelayJobPayload(
        run_id=RUN_ID,
        conversation_id=CONVERSATION_ID,
        trigger_message_id=TRIGGER_MESSAGE_ID,
        agent_id=agent_id,
        prompt="请根据岗位要求形成候选人画像。",
        max_turns=24,
    )


def test_runtime_map_requires_schema_v2_all_approved_bots_and_unique_ports(
    tmp_path: Path,
) -> None:
    runtime_map = MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json"))
    assert runtime_map.port_for("agent-brain-bot") == 9110
    assert runtime_map.port_for("hr-bot") == 9200
    assert set(runtime_map.agent_ids) == set(APPROVED_BOTS)
    for bad_payload in (
        {"schemaVersion": 1, "bots": []},
        {"schemaVersion": 2, "bots": []},
        {
            "schemaVersion": 2,
            "bots": [
                {"name": name, "instance": {"apiPort": 9200}}
                for name in APPROVED_BOTS
            ],
        },
    ):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad_payload), encoding="utf-8")
        with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
            MetaBotRuntimeMap.from_contract(path)


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("platform",), "feishu"),
        (("platformOnly",), False),
        (("engine",), "codex"),
        (("model",), "claude-opus-4-8"),
        (("backend",), "sdk"),
        (("toolPolicy",), "default"),
        (("workdir",), "/tmp/brain"),
        (("instance", "pm2Name"), "other"),
        (("instance", "apiPort"), 9111),
        (("instance", "stateDir"), "/tmp/state"),
        (("instance", "configPath"), "/tmp/config"),
        (("instance", "logDir"), "/tmp/log"),
    ),
)
def test_runtime_map_rejects_any_agent_brain_identity_drift(
    tmp_path: Path, path: tuple[str, ...], value: object
) -> None:
    contract = json.loads(_contract(tmp_path / "runtime.json").read_text())
    brain = next(entry for entry in contract["bots"] if entry["name"] == "agent-brain-bot")
    target = brain
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value
    candidate = tmp_path / "drift.json"
    candidate.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
        MetaBotRuntimeMap.from_contract(candidate)


@pytest.mark.parametrize("port", [0, 65536, True, "9200"])
def test_runtime_map_rejects_non_integer_or_out_of_range_ports(
    tmp_path: Path, port: object
) -> None:
    entries = [
        {
            "name": name,
            "instance": {"apiPort": port if name == "hr-bot" else 9200 + index},
        }
        for index, name in enumerate(APPROVED_BOTS)
    ]
    with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
        MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json", entries=entries))


def test_runtime_map_rejects_duplicate_approved_name(tmp_path: Path) -> None:
    entries = [
        {"name": name, "instance": {"apiPort": 9200 + index}}
        for index, name in enumerate(APPROVED_BOTS)
    ]
    entries.append({"name": "hr-bot", "instance": {"apiPort": 9400}})
    with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
        MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json", entries=entries))


def test_direct_runtime_map_construction_enforces_and_freezes_invariants() -> None:
    valid = {
        name: 9200 + index for index, name in enumerate(APPROVED_BOTS)
    }
    runtime_map = MetaBotRuntimeMap(valid)
    valid["hr-bot"] = 65000
    assert runtime_map.port_for("hr-bot") == 9200

    invalid_maps = (
        {name: 9200 + index for index, name in enumerate(APPROVED_BOTS[:-1])},
        {**{name: 9200 + index for index, name in enumerate(APPROVED_BOTS)}, "other": 9400},
        {name: 9200 for name in APPROVED_BOTS},
        {**{name: 9200 + index for index, name in enumerate(APPROVED_BOTS)}, "hr-bot": True},
        {**{name: 9200 + index for index, name in enumerate(APPROVED_BOTS)}, "hr-bot": 0},
    )
    for ports in invalid_maps:
        with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
            MetaBotRuntimeMap(ports)


@respx.mock
def test_start_run_sends_exact_contract_and_callback_bridge_identity(
    tmp_path: Path,
) -> None:
    runtime_map = MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json"))
    route = respx.post("http://127.0.0.1:9200/api/core-chat/runs").mock(
        return_value=httpx.Response(
            202,
            json={"status": "accepted", "runId": str(RUN_ID), "targetBot": "hr-bot"},
        )
    )
    client = MetaBotClient(runtime_map, _secret_file(tmp_path))

    client.start_run(_payload(), CALLBACK_URL)

    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer local-bearer-secret"
    assert json.loads(request.content) == {
        "runId": "00000000-0000-4000-8000-000000000101",
        "conversationId": "00000000-0000-4000-8000-000000000102",
        "triggerMessageId": "00000000-0000-4000-8000-000000000103",
        "targetBot": "hr-bot",
        "prompt": "请根据岗位要求形成候选人画像。",
        "eventCallbackUrl": CALLBACK_URL,
        "executionChatId": EXECUTION_CHAT_ID,
        "userId": "platform-user",
        "maxTurns": 24,
    }


@respx.mock
def test_agent_brain_request_declares_no_tools_and_omits_legacy_overrides(
    tmp_path: Path,
) -> None:
    route = respx.post("http://127.0.0.1:9110/api/core-chat/runs").mock(
        return_value=httpx.Response(
            202,
            json={
                "status": "accepted",
                "runId": str(RUN_ID),
                "targetBot": "agent-brain-bot",
            },
        )
    )
    client = MetaBotClient(
        MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json")),
        _secret_file(tmp_path),
    )

    client.start_run(_payload("agent-brain-bot"), CALLBACK_URL)

    body = json.loads(route.calls.last.request.content)
    assert body["toolPolicy"] == "none"
    assert "maxTurns" not in body
    assert "allowedTools" not in body


def test_http_client_policy_disables_environment_proxies_and_uses_ten_seconds(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, json):
            return httpx.Response(
                202,
                json={
                    "status": "accepted",
                    "runId": str(RUN_ID),
                    "targetBot": "hr-bot",
                },
            )

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:65530")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:65530")
    monkeypatch.setattr(metabot_client.httpx, "Client", FakeClient)
    client = MetaBotClient(
        MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json")),
        _secret_file(tmp_path),
    )

    client.start_run(_payload(), CALLBACK_URL)

    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (
        10.0,
        10.0,
        10.0,
        10.0,
    )
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
    assert "proxy" not in captured


@respx.mock
def test_start_redirect_is_not_followed_or_sent_to_second_host(
    tmp_path: Path,
) -> None:
    first = respx.post("http://127.0.0.1:9200/api/core-chat/runs").mock(
        return_value=httpx.Response(
            302, headers={"Location": "http://127.0.0.1:9400/stolen"}
        )
    )
    second = respx.post("http://127.0.0.1:9400/stolen").mock(
        return_value=httpx.Response(202)
    )
    client = MetaBotClient(
        MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json")),
        _secret_file(tmp_path),
    )

    with pytest.raises(MetaBotClientError, match="metabot request failed"):
        client.start_run(_payload(), CALLBACK_URL)

    assert first.call_count == 1
    assert second.call_count == 0


@respx.mock
def test_cancel_run_uses_same_agent_port_and_requires_matching_200(
    tmp_path: Path,
) -> None:
    runtime_map = MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json"))
    route = respx.post(
        f"http://127.0.0.1:9200/api/core-chat/runs/{RUN_ID}/cancel"
    ).mock(return_value=httpx.Response(200, json={"runId": str(RUN_ID)}))
    client = MetaBotClient(runtime_map, _secret_file(tmp_path))

    client.cancel_run(RUN_ID, "hr-bot")

    assert route.called
    assert route.calls.last.request.headers["Authorization"] == (
        "Bearer local-bearer-secret"
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(202, json={"runId": str(RUN_ID)}),
        httpx.Response(200, json={"runId": str(UUID(int=0))}),
        httpx.Response(200, content=b"not-json"),
        httpx.Response(
            302, headers={"Location": "http://127.0.0.1:9400/cancel"}
        ),
    ],
)
@respx.mock
def test_cancel_run_rejects_every_negative_response_contract(
    tmp_path: Path, response: httpx.Response
) -> None:
    route = respx.post(
        f"http://127.0.0.1:9200/api/core-chat/runs/{RUN_ID}/cancel"
    ).mock(return_value=response)
    redirected = respx.post("http://127.0.0.1:9400/cancel").mock(
        return_value=httpx.Response(200, json={"runId": str(RUN_ID)})
    )
    client = MetaBotClient(
        MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json")),
        _secret_file(tmp_path),
    )

    with pytest.raises(MetaBotClientError) as error:
        client.cancel_run(RUN_ID, "hr-bot")

    assert str(error.value) == "metabot request failed"
    assert error.value.__cause__ is None
    assert route.call_count == 1
    assert redirected.call_count == 0


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"status": "accepted", "runId": str(RUN_ID), "targetBot": "hr-bot"}),
        httpx.Response(202, json={"status": "queued", "runId": str(RUN_ID), "targetBot": "hr-bot"}),
        httpx.Response(202, json={"status": "accepted", "runId": str(UUID(int=0)), "targetBot": "hr-bot"}),
        httpx.Response(202, json={"status": "accepted", "runId": str(RUN_ID), "targetBot": "fae-bot"}),
        httpx.Response(202, content=b"not-json"),
        httpx.Response(302, headers={"Location": "http://example.com/secret"}),
    ],
)
@respx.mock
def test_start_run_collapses_all_response_contract_failures(
    tmp_path: Path, response: httpx.Response
) -> None:
    MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json"))
    respx.post("http://127.0.0.1:9200/api/core-chat/runs").mock(return_value=response)
    prompt = "protected prompt body"
    client = MetaBotClient(
        MetaBotRuntimeMap.from_contract(tmp_path / "runtime.json"),
        _secret_file(tmp_path, "protected-bearer"),
    )
    payload = _payload().model_copy(update={"prompt": prompt})

    with pytest.raises(MetaBotClientError) as error:
        client.start_run(payload, CALLBACK_URL)
    assert str(error.value) == "metabot request failed"
    assert prompt not in str(error.value)
    assert "protected-bearer" not in str(error.value)


@respx.mock
def test_transport_failure_is_sanitized_and_not_retried(tmp_path: Path) -> None:
    route = respx.post("http://127.0.0.1:9200/api/core-chat/runs").mock(
        side_effect=httpx.ConnectError("protected-bearer protected prompt body")
    )
    client = MetaBotClient(
        MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json")),
        _secret_file(tmp_path, "protected-bearer"),
    )

    with pytest.raises(MetaBotClientError) as error:
        client.start_run(
            _payload().model_copy(update={"prompt": "protected prompt body"}),
            CALLBACK_URL,
        )
    assert str(error.value) == "metabot request failed"
    assert error.value.__cause__ is None
    assert route.call_count == 1


def test_bearer_secret_requires_absolute_regular_0600_file(tmp_path: Path) -> None:
    runtime_map = MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json"))
    secret = _secret_file(tmp_path)

    with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
        MetaBotClient(runtime_map, Path("relative.token"))
    secret.chmod(0o640)
    with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
        MetaBotClient(runtime_map, secret)
    secret.chmod(0o600)
    secret.parent.chmod(0o750)
    with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
        MetaBotClient(runtime_map, secret)
    secret.parent.chmod(0o700)

    secret_link = secret.parent / "linked.token"
    secret_link.symlink_to(secret)
    with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
        MetaBotClient(runtime_map, secret_link)

    real_parent = tmp_path / "real-secret-parent"
    real_parent.mkdir(mode=0o700)
    real_secret = real_parent / "token"
    real_secret.write_text("secret", encoding="utf-8")
    real_secret.chmod(0o600)
    parent_link = tmp_path / "linked-secret-parent"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
        MetaBotClient(runtime_map, parent_link / "token")

    directory_secret = secret.parent / "directory-token"
    directory_secret.mkdir(mode=0o700)
    with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
        MetaBotClient(runtime_map, directory_secret)

    oversized = secret.parent / "oversized.token"
    oversized.write_text("x" * 16_385, encoding="utf-8")
    oversized.chmod(0o600)
    with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
        MetaBotClient(runtime_map, oversized)


def test_bearer_path_swap_after_open_reads_only_opened_descriptor(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_map = MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json"))
    secret = _secret_file(tmp_path, "original-secret")
    original_open = metabot_client.os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == secret.name:
            secret.unlink()
            secret.write_text("replacement-secret", encoding="utf-8")
            secret.chmod(0o600)
            swapped = True
        return descriptor

    monkeypatch.setattr(metabot_client.os, "open", swapping_open)
    client = MetaBotClient(runtime_map, secret)

    assert swapped is True
    assert client._bearer_secret == "original-secret"


def test_bearer_descriptor_close_failures_are_sanitized_and_independent(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_map = MetaBotRuntimeMap.from_contract(_contract(tmp_path / "runtime.json"))
    secret = _secret_file(tmp_path)
    original_close = metabot_client.os.close
    closed: list[int] = []

    def first_close_fails(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)
        if len(closed) == 1:
            raise OSError("raw close failure")

    monkeypatch.setattr(metabot_client.os, "close", first_close_fails)
    with pytest.raises(MetaBotClientError) as error:
        MetaBotClient(runtime_map, secret)

    assert str(error.value) == "metabot configuration invalid"
    assert error.value.__cause__ is None
    assert len(closed) == 2
