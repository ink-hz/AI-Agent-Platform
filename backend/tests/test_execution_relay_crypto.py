from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)
from app.execution_relay.models import RelayEvent, RelayJobPayload, RelayLease


def _keyring() -> IdentityKeyring:
    return IdentityKeyring(
        active_version=2,
        purpose="platform-content-encryption",
        _keys={1: b"a" * 32, 2: b"b" * 32},
    )


def test_relay_models_expose_the_bounded_public_contract() -> None:
    run_id = uuid4()
    payload = RelayJobPayload(
        run_id=run_id,
        conversation_id=uuid4(),
        trigger_message_id=uuid4(),
        agent_id="hr-bot",
        prompt="prepare a profile",
        max_turns=24,
    )
    event = RelayEvent(
        run_id=run_id,
        seq=1,
        event_type="state",
        created_at=datetime.now(timezone.utc),
        payload={"state": "running"},
    )
    lease = RelayLease(
        job_id=uuid4(),
        payload=payload,
        lease_expires_at=datetime.now(timezone.utc),
        cancel_requested=False,
    )

    assert lease.payload == payload
    assert event.payload == {"state": "running"}
    with pytest.raises(ValidationError):
        RelayJobPayload(**{**payload.model_dump(), "max_turns": 0})
    with pytest.raises(ValidationError):
        RelayJobPayload(**{**payload.model_dump(), "max_turns": 25})
    with pytest.raises(ValidationError):
        RelayEvent(**{**event.model_dump(), "seq": 0})


def test_content_codec_round_trips_object_with_active_version_and_redaction() -> None:
    codec = ContentCodec(_keyring())
    value = {"prompt": "sensitive prompt", "nested": {"answer": 7}}

    sealed = codec.seal_json("execution-job:job:run", value)

    assert sealed.key_version == 2
    assert b"sensitive prompt" not in sealed.ciphertext
    assert len(sealed.ciphertext) >= 28
    assert repr(sealed) == "SealedContent(ciphertext=<redacted>, key_version=2)"
    assert codec.unseal_json("execution-job:job:run", sealed) == value
    with pytest.raises(FrozenInstanceError):
        sealed.key_version = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "subject,sealed",
    [
        (
            "execution-job:wrong:subject",
            lambda valid: valid,
        ),
        (
            "execution-job:job:run",
            lambda valid: SealedContent(valid.ciphertext, 999),
        ),
        (
            "execution-job:job:run",
            lambda valid: SealedContent(
                valid.ciphertext[:-1] + bytes([valid.ciphertext[-1] ^ 1]),
                valid.key_version,
            ),
        ),
        (
            "execution-job:job:run",
            lambda valid: SealedContent(b"x" * 27, valid.key_version),
        ),
    ],
)
def test_content_codec_collapses_decrypt_failures(subject, sealed) -> None:
    codec = ContentCodec(_keyring())
    valid = codec.seal_json("execution-job:job:run", {"prompt": "protected"})

    with pytest.raises(ContentCryptoError) as raised:
        codec.unseal_json(subject, sealed(valid))

    assert str(raised.value) == "content decrypt failed"
    assert "protected" not in repr(raised.value)


def test_content_codec_rejects_invalid_subjects_and_non_object_values() -> None:
    codec = ContentCodec(_keyring())

    with pytest.raises(ContentCryptoError, match="^content encrypt failed$"):
        codec.seal_json("execution-job:\0:run", {"prompt": "protected"})
    with pytest.raises(ContentCryptoError, match="^content encrypt failed$"):
        codec.seal_json("execution-job:job:run", ["not", "an", "object"])  # type: ignore[arg-type]

    valid = codec.seal_json("execution-job:job:run", {"prompt": "protected"})
    with pytest.raises(ContentCryptoError, match="^content decrypt failed$"):
        codec.unseal_json("execution-job:\0:run", valid)


@pytest.mark.parametrize(
    "keyring",
    [
        IdentityKeyring(1, "provider-encryption", {1: b"a" * 32}),
        IdentityKeyring(
            1, "platform-content-encryption", {1: b"short"}
        ),
        IdentityKeyring(
            1,
            "platform-content-encryption",
            {1: b"a" * 32},
            transition_versions=(1,),
        ),
    ],
)
def test_content_codec_rejects_wrong_keyring_contract(keyring) -> None:
    with pytest.raises(ContentCryptoError, match="^content keyring invalid$"):
        ContentCodec(keyring)
