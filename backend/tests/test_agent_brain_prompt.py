from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_brain.prompt import BrainPromptIntegrityError, BrainSystemPrompt


ROOT = Path(__file__).parents[2]
PROMPT = ROOT / "backend/app/agent_brain/prompts/brain_v1.md"
MANIFEST = ROOT / "deploy/cloud/brain-model.release.json"


def test_brain_prompt_matches_release_manifest_and_is_byte_stable() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    prompt = BrainSystemPrompt.load(
        PROMPT,
        expected_sha256=manifest["system_prompt_sha256"],
    )

    raw = PROMPT.read_bytes()
    assert prompt.sha256 == manifest["system_prompt_sha256"]
    assert prompt.text.encode("utf-8") == raw
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert "Delegate only when" in prompt.text
    assert "Only submit_answer completes the turn" in prompt.text


def test_prompt_names_exact_tools_and_enforces_behavioral_boundaries() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    prompt = BrainSystemPrompt.load(
        PROMPT,
        expected_sha256=manifest["system_prompt_sha256"],
    ).text

    for name in (
        "list_agents",
        "delegate_task",
        "await_agent_events",
        "send_agent_message",
        "stop_agent_task",
        "request_user_input",
        "submit_answer",
    ):
        assert name in prompt
    for required in (
        "public_reason",
        "Delegate only when",
        "Stay within the user's request",
        "Keep the answer concise",
        "Do not repeat a task for reassurance",
        "Never expose hidden reasoning",
        "Never manufacture progress",
        "concrete Agent event",
        "material specialist value",
        "irreversible authorization",
    ):
        assert required in prompt
    assert "cancel_task" not in prompt
    assert "reveal chain-of-thought" not in prompt.lower()
    assert "narrate self-correction" in prompt


@pytest.mark.parametrize(
    "raw",
    [
        b"\xef\xbb\xbf# Agent Brain\n",
        b"# Agent Brain\r\n",
        b"# Agent Brain",
        b"# Agent Brain\n\n",
        b"\xff\xfe",
    ],
)
def test_prompt_rejects_malformed_artifacts(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "brain.md"
    path.write_bytes(raw)
    with pytest.raises(BrainPromptIntegrityError):
        BrainSystemPrompt.load(path, expected_sha256="0" * 64)


def test_prompt_digest_mismatch_blocks_startup(tmp_path: Path) -> None:
    path = tmp_path / "brain.md"
    path.write_text("changed\n", encoding="utf-8")

    with pytest.raises(BrainPromptIntegrityError, match="sha256 mismatch"):
        BrainSystemPrompt.load(path, expected_sha256="0" * 64)
