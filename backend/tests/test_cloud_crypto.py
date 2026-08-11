import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

from app.cloud_replica.crypto import (
    BatchSigner,
    BatchVerifier,
    FieldCipher,
    ReplicaCryptoError,
    read_key_file,
    stable_id,
)


def _private(path: Path, value: bytes) -> Path:
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def test_stable_ids_are_deterministic_scoped_and_irreversible():
    key = b"k" * 32
    first = stable_id("feishu-user", "on_sensitive", key)

    assert first == stable_id("feishu-user", "on_sensitive", key)
    assert first != stable_id("dingtalk-user", "on_sensitive", key)
    assert first != stable_id("feishu-user", "another", key)
    assert "on_sensitive" not in first
    assert first == first.lower()
    assert all(character.isalnum() for character in first)


def test_stable_id_rejects_invalid_inputs_without_echoing_them():
    with pytest.raises(ReplicaCryptoError, match="identity_failed") as error:
        stable_id("scope", "do-not-echo", b"short")

    assert "do-not-echo" not in str(error.value)


def test_field_cipher_round_trips_only_with_matching_associated_data():
    cipher = FieldCipher(b"a" * 32)
    encrypted = cipher.encrypt(
        "sanitized display value", "1:turn:cloud-turn-1"
    )

    assert cipher.decrypt(encrypted, "1:turn:cloud-turn-1") == "sanitized display value"
    assert "sanitized display value" not in encrypted["ciphertext"]

    for wrong_cipher, aad in (
        (FieldCipher(b"b" * 32), "1:turn:cloud-turn-1"),
        (cipher, "1:turn:cloud-turn-2"),
    ):
        with pytest.raises(ReplicaCryptoError, match="decrypt_failed") as error:
            wrong_cipher.decrypt(encrypted, aad)
        assert "sanitized display value" not in str(error.value)


def test_field_cipher_rejects_tampered_nonce_and_ciphertext():
    cipher = FieldCipher(b"a" * 32)
    encrypted = cipher.encrypt("safe", "1:session:s1")

    for field in ("nonce", "ciphertext"):
        tampered = dict(encrypted)
        raw = bytearray(base64.urlsafe_b64decode(tampered[field] + "=="))
        raw[0] ^= 1
        tampered[field] = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
        with pytest.raises(ReplicaCryptoError, match="decrypt_failed"):
            cipher.decrypt(tampered, "1:session:s1")


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda root: root / "missing", "regular"),
        (lambda root: root, "regular"),
    ],
)
def test_key_file_rejects_missing_or_directory(tmp_path, factory, message):
    with pytest.raises(ReplicaCryptoError, match=message):
        read_key_file(factory(tmp_path), expected_size=32)


def test_key_file_rejects_relative_symlink_permissive_empty_and_wrong_size(
    tmp_path, monkeypatch
):
    valid = _private(tmp_path / "valid", b"x" * 32)
    link = tmp_path / "link"
    link.symlink_to(valid)
    permissive = _private(tmp_path / "permissive", b"x" * 32)
    permissive.chmod(0o644)
    empty = _private(tmp_path / "empty", b"")
    short = _private(tmp_path / "short", b"x" * 31)

    for path, message in (
        (link, "regular"),
        (permissive, "0600"),
        (empty, "size"),
        (short, "size"),
    ):
        with pytest.raises(ReplicaCryptoError, match=message):
            read_key_file(path, expected_size=32)

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ReplicaCryptoError, match="absolute"):
        read_key_file(Path("valid"), expected_size=32)


def test_ed25519_file_signer_and_verifier(tmp_path):
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    private_path = _private(tmp_path / "private", private_bytes)
    public_path = _private(tmp_path / "public", public_bytes)

    signer = BatchSigner.from_private_key_file(private_path)
    verifier = BatchVerifier.from_public_key_file(public_path)
    signature = signer.sign(b"canonical batch")

    verifier.verify(b"canonical batch", signature)
    with pytest.raises(ReplicaCryptoError, match="signature_failed"):
        verifier.verify(b"mutated batch", signature)
