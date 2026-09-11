import json
from pathlib import Path

import pytest


def test_manifest_has_six_bounded_hashed_synthetic_requests(tmp_path: Path) -> None:
    from tools.hr_agent.metering_validation import prepare_manifest

    manifest = prepare_manifest(tmp_path / "requests.json", profile_revision="r1")

    assert [item["case"] for item in manifest["requests"]] == [
        "english", "chinese", "mixed_json", "tool_schema", "multi_turn", "long_chinese"
    ]
    assert manifest["call_budget"] == 6
    assert all(item["max_output_tokens"] == 128 for item in manifest["requests"])
    assert all(item["unicode_characters"] <= 12_000 for item in manifest["requests"])
    assert all(len(item["request_sha256"]) == 64 for item in manifest["requests"])
    assert json.loads((tmp_path / "requests.json").read_text()) == manifest


def test_attempt_record_keeps_only_safe_usage_and_reported_model() -> None:
    from app.hr_agent.types import ModelEvent
    from tools.hr_agent.metering_validation import collect_metering_events

    result = collect_metering_events(
        [
            ModelEvent("usage", {"provider_protocol": "anthropic_messages_sse", "raw": {
                "input_tokens": 17,
                "cache_creation_input_tokens": 3,
                "secret": "PROMPT_SENTINEL",
            }}),
            ModelEvent("usage", {"provider_protocol": "anthropic_messages_sse", "raw": {
                "output_tokens": 4,
            }}),
            ModelEvent("usage", {"provider_protocol": "anthropic_messages_sse", "raw": {
                "_response_metadata": {"reported_models": ["claude-opus-5"]}
            }}),
            ModelEvent("text_delta", {"text": "PROMPT_SENTINEL"}),
            ModelEvent("stop", {"reason": "end_turn"}),
        ]
    )

    assert result == {
        "response_model": "claude-opus-5",
        "usage": {
            "input_tokens": 17,
            "cache_creation_input_tokens": 3,
            "output_tokens": 4,
        },
        "raw_usage": {
            "input_tokens": 17,
            "cache_creation_input_tokens": 3,
            "output_tokens": 4,
        },
        "stop_reason": "end_turn",
    }
    assert "PROMPT_SENTINEL" not in json.dumps(result)


def test_missing_usage_is_a_failed_attempt() -> None:
    from app.hr_agent.types import ModelEvent
    from tools.hr_agent.metering_validation import collect_metering_events

    with pytest.raises(ValueError, match="usage_missing"):
        collect_metering_events([ModelEvent("stop", {"reason": "end_turn"})])
