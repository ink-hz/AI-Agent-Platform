from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import respx

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
)
CALLBACK_URL = (
    "http://127.0.0.1:9120/callbacks/"
    "00000000-0000-4000-8000-000000000101/"
    "bm9uLXNlY3JldC10ZXN0LXRva2Vu"
)


def _contract(path: Path, *, entries: list[dict[str, object]] | None = None) -> Path:
    bots = entries or [
        {"name": name, "instance": {"apiPort": 9200 + index}}
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


@respx.mock
def test_start_run_sends_exact_contract_to_exact_loopback_port(tmp_path: Path) -> None:
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
        "executionChatId": (
            "platform-00000000-0000-4000-8000-000000000102-hr-bot"
        ),
        "userId": "platform-user",
        "maxTurns": 24,
    }


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
    secret.chmod(0o640)
    with pytest.raises(MetaBotClientError, match="metabot configuration invalid"):
        MetaBotClient(runtime_map, secret)
