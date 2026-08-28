from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.agent_brain.task_identity import SignedTaskTokenIssuer

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
USER_ID = UUID("00000000-0000-4000-8000-000000000101")
TASK_ID = UUID("00000000-0000-4000-8000-000000000102")
REQUEST_ID = UUID("00000000-0000-4000-8000-000000000103")


def _private_key_file(tmp_path):
    path = tmp_path / "task-token-ed25519.key"
    path.write_bytes(Ed25519PrivateKey.generate().private_bytes_raw())
    path.chmod(0o600)
    return path


def _decode_segment(segment: str) -> dict[str, object]:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


def test_task_token_binds_actor_agent_task_scope_version_and_deadlines(
    tmp_path,
) -> None:
    issuer = SignedTaskTokenIssuer.from_file(
        _private_key_file(tmp_path), kid="platform-task-v1"
    )
    task_deadline = NOW + timedelta(minutes=15)
    action_deadline = NOW + timedelta(minutes=5)

    token = issuer.issue(
        audience="ai-fae-agent",
        internal_user_id=USER_ID,
        agent_id="ai-fae-agent",
        agent_task_id=TASK_ID,
        capability_version=2,
        authorized_scopes=("fae.answer",),
        task_deadline_at=task_deadline,
        action_execution_deadline_at=action_deadline,
        now=NOW,
        request_id=REQUEST_ID,
    )

    header, payload, _signature = token.split(".")
    assert _decode_segment(header) == {
        "alg": "EdDSA",
        "kid": "platform-task-v1",
        "typ": "JWT",
    }
    claims = issuer.verify(token, audience="ai-fae-agent", now=NOW)
    assert claims["iss"] == "orbbec-agent-platform"
    assert claims["internal_user_id"] == str(USER_ID)
    assert claims["agent_id"] == "ai-fae-agent"
    assert claims["agent_task_id"] == str(TASK_ID)
    assert claims["capability_version"] == 2
    assert claims["authorized_scopes"] == ["fae.answer"]
    assert claims["task_deadline_at"] == "2026-08-27T10:15:00Z"
    assert claims["action_execution_deadline_at"] == "2026-08-27T10:05:00Z"
    assert claims["request_id"] == str(REQUEST_ID)
    assert _decode_segment(payload) == claims


def test_task_token_expiry_is_bounded_by_task_deadline(tmp_path) -> None:
    issuer = SignedTaskTokenIssuer.from_file(
        _private_key_file(tmp_path), kid="platform-task-v1", ttl_seconds=60
    )
    deadline = NOW + timedelta(seconds=25)

    claims = issuer.verify(
        issuer.issue(
            audience="ai-fae-agent",
            internal_user_id=USER_ID,
            agent_id="ai-fae-agent",
            agent_task_id=TASK_ID,
            capability_version=2,
            authorized_scopes=("fae.answer",),
            task_deadline_at=deadline,
            now=NOW,
            request_id=REQUEST_ID,
        ),
        audience="ai-fae-agent",
        now=NOW,
    )

    assert claims["iat"] == int(NOW.timestamp())
    assert claims["exp"] == int(deadline.timestamp())


def test_task_token_rejects_expired_task_and_unsorted_scopes(tmp_path) -> None:
    issuer = SignedTaskTokenIssuer.from_file(
        _private_key_file(tmp_path), kid="platform-task-v1"
    )
    common = {
        "audience": "ai-fae-agent",
        "internal_user_id": USER_ID,
        "agent_id": "ai-fae-agent",
        "agent_task_id": TASK_ID,
        "capability_version": 2,
        "task_deadline_at": NOW + timedelta(minutes=1),
        "now": NOW,
        "request_id": REQUEST_ID,
    }

    with pytest.raises(ValueError, match="scopes"):
        issuer.issue(authorized_scopes=("fae.read", "fae.answer"), **common)
    with pytest.raises(ValueError, match="deadline"):
        issuer.issue(
            authorized_scopes=("fae.answer",),
            **{**common, "task_deadline_at": NOW},
        )


def test_task_signing_key_must_be_owned_regular_0600_file(tmp_path) -> None:
    key = _private_key_file(tmp_path)
    link = tmp_path / "task-token-current.key"
    link.symlink_to(key)

    with pytest.raises(RuntimeError, match="regular mode 0600"):
        SignedTaskTokenIssuer.from_file(link, kid="platform-task-v1")

    key.chmod(0o640)
    with pytest.raises(RuntimeError, match="mode 0600"):
        SignedTaskTokenIssuer.from_file(key, kid="platform-task-v1")
