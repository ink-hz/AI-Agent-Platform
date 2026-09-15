import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "contracts/platform-v1/content-crypto-vectors.json"


def test_crypto_module_is_standalone():
    assert importlib.util.find_spec("app.execution_relay.content_crypto") is not None


def test_frozen_fixture_digest():
    # Keep this release identity in both repositories; retain vectors across upgrades.
    assert json.loads(FIXTURE.read_text())["format_version"] == 1
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == "4b8cff62ed9bf442a248a01fc79564a729dc37b9b27f967dccb4a1dc5ba7338e"
    assert (
        hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        == FIXTURE.with_suffix(".sha256").read_text().split()[0]
    )


@pytest.mark.parametrize("index", range(6))
def test_exact_historical_ciphertext_and_negative_aad(index, monkeypatch):
    from app.execution_relay.content_crypto import (
        ContentCodec,
        ContentCryptoError,
        SealedContent,
    )
    from app.control_plane.crypto import IdentityKeyring

    doc = json.loads(FIXTURE.read_text())
    vector = doc["vectors"][index]
    keys = {int(k): bytes.fromhex(v) for k, v in doc["keys_hex"].items()}
    version = vector["key_version"]
    codec = ContentCodec(
        IdentityKeyring(
            active_version=version, purpose="platform-content-encryption", _keys=keys
        )
    )
    sealed = SealedContent(bytes.fromhex(vector["ciphertext_hex"]), version)
    assert codec.unseal_json(vector["subject"], sealed) == vector["plaintext"]
    assert codec._aad(vector["subject"], version).hex() == vector["aad_hex"]
    monkeypatch.setattr(
        "app.execution_relay.content_crypto.secrets.token_bytes",
        lambda n: bytes.fromhex(vector["nonce_hex"]),
    )
    assert (
        codec.seal_json(vector["subject"], vector["plaintext"]).ciphertext
        == sealed.ciphertext
    )
    if vector["subject"].startswith("content-key-canary:"):
        codec.verify_key_canary(sealed)
        assert codec.seal_key_canary(version) == sealed
    for subject, candidate in [
        (vector["subject"] + ":other-owner", sealed),
        (vector["subject"], SealedContent(sealed.ciphertext, 7 if version == 1 else 1)),
        (vector["subject"], SealedContent(sealed.ciphertext, 99)),
        (
            vector["subject"],
            SealedContent(
                sealed.ciphertext[:-1] + bytes([sealed.ciphertext[-1] ^ 1]), version
            ),
        ),
        (vector["subject"], SealedContent(sealed.ciphertext[:27], version)),
    ]:
        with pytest.raises(ContentCryptoError, match="content decrypt failed"):
            codec.unseal_json(subject, candidate)
    wrong = ContentCodec(
        IdentityKeyring(
            active_version=version,
            purpose="platform-content-encryption",
            _keys={version: b"x" * 32},
        )
    )
    with pytest.raises(ContentCryptoError, match="content decrypt failed"):
        wrong.unseal_json(vector["subject"], sealed)
