import json

import pytest

from app.cluster.contract import ContractLoadError, load_targets


def _bot(name: str, pm2_name: str, port: int, *, enabled: bool | None = None) -> dict:
    value = {
        "name": name,
        "appId": "cli_sensitive_identifier",
        "engine": "claude",
        "model": "claude-opus-4-8",
        "backend": "pty",
        "workdir": f"/runtime/{name}",
        "instance": {"pm2Name": pm2_name, "apiPort": port},
    }
    if enabled is not None:
        value["enabled"] = enabled
    return value


def _write_contract(tmp_path, *, bots: list[dict], test_bot: dict | None = None):
    payload = {"schemaVersion": 1, "bots": bots}
    if test_bot is not None:
        payload["testBot"] = test_bot
    path = tmp_path / "metabot.runtime-contract.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_targets_includes_bots_and_enabled_test_bot(tmp_path):
    path = _write_contract(
        tmp_path,
        bots=[_bot("hr-bot", "metabot-hr", 9101)],
        test_bot=_bot("test-bot", "metabot-test", 9106, enabled=True),
    )

    targets = load_targets(str(path))

    assert [(item.id, item.pm2_name, item.port) for item in targets] == [
        ("hr-bot", "metabot-hr", 9101),
        ("test-bot", "metabot-test", 9106),
    ]
    assert targets[0].health_url == "http://127.0.0.1:9101/api/health"
    assert targets[0].runtime_url == "http://127.0.0.1:9101/api/observability/runtime"
    assert targets[0].engine == "claude"
    assert targets[0].declared_model == "claude-opus-4-8"
    assert targets[0].backend == "pty"
    assert targets[0].channel == "Feishu"
    assert targets[0].workdir == "/runtime/hr-bot"
    assert "cli_sensitive_identifier" not in targets[0].model_dump_json()


def test_disabled_test_bot_is_ignored(tmp_path):
    path = _write_contract(
        tmp_path,
        bots=[],
        test_bot=_bot("test-bot", "metabot-test", 9106, enabled=False),
    )

    assert load_targets(str(path)) == []


@pytest.mark.parametrize(
    ("bots", "message"),
    [
        (
            [_bot("duplicate", "metabot-a", 9100), _bot("duplicate", "metabot-b", 9101)],
            "duplicate bot id",
        ),
        (
            [_bot("one", "metabot-one", 9100), _bot("two", "metabot-two", 9100)],
            "duplicate api port",
        ),
    ],
)
def test_duplicate_ids_or_ports_are_rejected(tmp_path, bots, message):
    path = _write_contract(tmp_path, bots=bots)

    with pytest.raises(ContractLoadError, match=message):
        load_targets(str(path))


def test_invalid_entry_uses_safe_error_message(tmp_path):
    path = _write_contract(tmp_path, bots=[{"name": "broken"}])

    with pytest.raises(ContractLoadError, match="invalid bot entry") as error:
        load_targets(str(path))

    assert str(path) not in str(error.value)
