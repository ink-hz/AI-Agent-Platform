from datetime import UTC, datetime
import io

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.cloud_replica.canary import CANARY_VALUES, create_synthetic_canary
from app.cloud_replica.crypto import BatchSigner, BatchVerifier
from app.cloud_replica.protocol import BatchLimits, decode_and_verify_batch
from app.cloud_replica.sanitize import SanitizationPolicy


def test_synthetic_canary_batch_is_signed_and_contains_no_raw_fixture(tmp_path):
    private = Ed25519PrivateKey.generate()
    output = tmp_path / "canary.jsonl"
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

    create_synthetic_canary(
        output,
        policy=SanitizationPolicy(version="test-canary"),
        identity_key=b"i" * 32,
        signer=BatchSigner(private),
        created_at=now,
    )

    payload = output.read_bytes()
    batch = decode_and_verify_batch(
        io.BytesIO(payload), BatchVerifier(private.public_key()), BatchLimits()
    )
    serialized = payload.decode("utf-8")
    assert batch.header.source_instance_id == "synthetic-acceptance"
    assert batch.header.sequence == 1
    assert len(batch.records) == 1
    assert output.stat().st_mode & 0o777 == 0o600
    assert batch.records[0]["turns"][0]["attachments"][0]["display_label"] == "附件 1"
    for value in CANARY_VALUES:
        assert value not in serialized


def test_synthetic_canary_never_overwrites_an_existing_file(tmp_path):
    private = Ed25519PrivateKey.generate()
    output = tmp_path / "canary.jsonl"
    output.write_bytes(b"keep")

    with pytest.raises(FileExistsError):
        create_synthetic_canary(
            output,
            policy=SanitizationPolicy(),
            identity_key=b"i" * 32,
            signer=BatchSigner(private),
            created_at=datetime(2026, 8, 11, tzinfo=UTC),
        )

    assert output.read_bytes() == b"keep"
