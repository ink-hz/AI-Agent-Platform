from __future__ import annotations

import base64
import json
import stat
from pathlib import Path
from uuid import uuid4

import pytest
from app.control_plane.crypto import (
    EncryptedDirectoryAttribute,
    IdentityCryptoError,
    IdentityKeyring,
    ProtectedProviderId,
    ProviderIdentityCodec,
)


def _write_keyring(
    path: Path,
    *,
    purpose: str,
    active_version: int,
    keys: dict[int, bytes],
    transition_versions: tuple[int, ...] | None = None,
) -> Path:
    document = {
        "purpose": purpose,
        "active_version": active_version,
        "keys": {
            str(version): base64.b64encode(key).decode("ascii")
            for version, key in keys.items()
        },
    }
    if transition_versions is not None:
        document["transition_versions"] = list(transition_versions)
    path.write_text(
        json.dumps(document),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _keyring(
    tmp_path: Path,
    name: str,
    purpose: str,
    active_version: int,
    keys: dict[int, bytes],
    transition_versions: tuple[int, ...] | None = None,
) -> IdentityKeyring:
    return IdentityKeyring.from_file(
        _write_keyring(
            tmp_path / name,
            purpose=purpose,
            active_version=active_version,
            keys=keys,
            transition_versions=transition_versions,
        ),
        expected_purpose=purpose,
        expected_key_length=32,
    )


def _codec(tmp_path: Path, *, active_version: int = 2) -> ProviderIdentityCodec:
    versions = {1: b"e" * 32, 2: b"E" * 32}
    hmac_versions = {1: b"h" * 32, 2: b"H" * 32}
    return ProviderIdentityCodec(
        encryption=_keyring(
            tmp_path,
            "encryption.json",
            "provider-encryption",
            active_version,
            versions,
        ),
        hmac_keyring=_keyring(
            tmp_path,
            "hmac.json",
            "provider-lookup-hmac",
            active_version,
            hmac_versions,
            transition_versions=(1, 2),
        ),
    )


def test_seal_has_deterministic_lookup_and_randomized_ciphertext(tmp_path: Path) -> None:
    codec = _codec(tmp_path)

    first = codec.seal("employee", "  synthetic-provider-id  ")
    second = codec.seal("employee", "synthetic-provider-id")

    assert first.lookup_hmac == second.lookup_hmac
    assert first.lookup_key_version == second.lookup_key_version == 2
    assert first.encryption_key_version == second.encryption_key_version == 2
    assert first.ciphertext != second.ciphertext
    assert len(first.ciphertext) >= 12 + 16
    assert codec.unseal(first) == "synthetic-provider-id"


def test_directory_attribute_round_trip_is_randomized_and_redacted(
    tmp_path: Path,
) -> None:
    codec = _codec(tmp_path)
    generation_id = uuid4()
    member_id = uuid4()

    first = codec.seal_attribute(
        "test-corp",
        generation_id,
        member_id,
        "real_name",
        "Private Real Name",
    )
    second = codec.seal_attribute(
        "test-corp",
        generation_id,
        member_id,
        "real_name",
        "Private Real Name",
    )

    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert first.encryption_key_version == second.encryption_key_version == 2
    assert first.nonce not in first.ciphertext
    assert codec.open_attribute(
        first,
        "test-corp",
        generation_id,
        member_id,
        "real_name",
    ) == "Private Real Name"
    assert "Private Real Name" not in repr(first)
    assert first.ciphertext.hex() not in repr(first)
    assert first.nonce.hex() not in repr(first)


@pytest.mark.parametrize("wrong_binding", ["directory", "generation", "member", "purpose"])
def test_directory_attribute_aad_binds_every_context_value(
    tmp_path: Path,
    wrong_binding: str,
) -> None:
    codec = _codec(tmp_path)
    generation_id = uuid4()
    member_id = uuid4()
    protected = codec.seal_attribute(
        "test-corp",
        generation_id,
        member_id,
        "mobile",
        "13800138000",
    )
    context = {
        "directory_id": "other-corp" if wrong_binding == "directory" else "test-corp",
        "generation_id": uuid4() if wrong_binding == "generation" else generation_id,
        "member_id": uuid4() if wrong_binding == "member" else member_id,
        "purpose": "real_name" if wrong_binding == "purpose" else "mobile",
    }

    with pytest.raises(IdentityCryptoError, match="attribute decrypt failed") as caught:
        codec.open_attribute(protected, **context)

    assert "13800138000" not in str(caught.value)


def test_directory_attribute_rejects_tampering_and_supports_key_rotation(
    tmp_path: Path,
) -> None:
    old_codec = ProviderIdentityCodec(
        _keyring(
            tmp_path,
            "attribute-old-encryption.json",
            "provider-encryption",
            1,
            {1: b"e" * 32},
        ),
        _keyring(
            tmp_path,
            "attribute-old-hmac.json",
            "provider-lookup-hmac",
            1,
            {1: b"h" * 32},
            transition_versions=(1,),
        ),
    )
    generation_id = uuid4()
    member_id = uuid4()
    protected = old_codec.seal_attribute(
        "test-corp",
        generation_id,
        member_id,
        "primary_department",
        "Project Management",
    )
    rotated_codec = _codec(tmp_path)
    tampered = EncryptedDirectoryAttribute(
        purpose=protected.purpose,
        ciphertext=protected.ciphertext[:-1] + bytes([protected.ciphertext[-1] ^ 1]),
        nonce=protected.nonce,
        encryption_key_version=protected.encryption_key_version,
    )

    assert rotated_codec.open_attribute(
        protected,
        "test-corp",
        generation_id,
        member_id,
        "primary_department",
    ) == "Project Management"
    with pytest.raises(IdentityCryptoError, match="attribute decrypt failed"):
        rotated_codec.open_attribute(
            tampered,
            "test-corp",
            generation_id,
            member_id,
            "primary_department",
        )


def test_subject_kind_and_version_are_authenticated_aad(tmp_path: Path) -> None:
    codec = _codec(tmp_path)
    protected = codec.seal("employee", "synthetic-provider-id")

    wrong_kind = ProtectedProviderId(
        subject_kind="organization-member",
        lookup_hmac=protected.lookup_hmac,
        lookup_key_version=protected.lookup_key_version,
        ciphertext=protected.ciphertext,
        encryption_key_version=protected.encryption_key_version,
    )
    wrong_version = ProtectedProviderId(
        subject_kind=protected.subject_kind,
        lookup_hmac=protected.lookup_hmac,
        lookup_key_version=protected.lookup_key_version,
        ciphertext=protected.ciphertext,
        encryption_key_version=1,
    )

    for malformed in (wrong_kind, wrong_version):
        with pytest.raises(IdentityCryptoError, match="identity decrypt failed") as caught:
            codec.unseal(malformed)
        assert "synthetic-provider-id" not in str(caught.value)
        assert protected.ciphertext.hex() not in str(caught.value)


def test_lookup_candidates_follow_the_explicit_active_previous_transition(
    tmp_path: Path,
) -> None:
    encryption = _keyring(
        tmp_path,
        "encryption.json",
        "provider-encryption",
        3,
        {1: b"a" * 32, 2: b"b" * 32, 3: b"c" * 32},
    )
    lookup = _keyring(
        tmp_path,
        "hmac.json",
        "provider-lookup-hmac",
        3,
        {2: b"e" * 32, 3: b"f" * 32},
        transition_versions=(2, 3),
    )
    codec = ProviderIdentityCodec(encryption=encryption, hmac_keyring=lookup)

    candidates = codec.lookup_candidates("employee", "synthetic-provider-id")

    assert tuple(version for version, _ in candidates) == (2, 3)
    assert candidates[0][1] != candidates[1][1]
    assert codec.matches_lookup(
        subject_kind="employee",
        provider_id="synthetic-provider-id",
        lookup_hmac=candidates[0][1],
        lookup_key_version=2,
    )
    assert not codec.matches_lookup(
        subject_kind="employee",
        provider_id="another-synthetic-id",
        lookup_hmac=candidates[0][1],
        lookup_key_version=2,
    )


@pytest.mark.parametrize(
    ("active_version", "keys", "transition_versions"),
    [
        (2, {1: b"a" * 32, 2: b"b" * 32}, None),
        (2, {1: b"a" * 32, 2: b"b" * 32}, (1,)),
        (2, {1: b"a" * 32, 2: b"b" * 32}, (1, 1)),
        (2, {1: b"a" * 32, 2: b"b" * 32}, (2, 1)),
        (3, {1: b"a" * 32, 3: b"c" * 32}, (1, 3)),
        (2, {1: b"a" * 32, 2: b"b" * 32, 3: b"c" * 32}, (2, 3)),
        (2, {1: b"a" * 32, 2: b"b" * 32, 3: b"c" * 32}, (1, 2)),
        (
            3,
            {1: b"a" * 32, 2: b"b" * 32, 3: b"c" * 32, 4: b"d" * 32},
            (1, 2, 3, 4),
        ),
    ],
)
def test_provider_codec_rejects_unsafe_hmac_transition_layouts(
    tmp_path: Path,
    active_version: int,
    keys: dict[int, bytes],
    transition_versions: tuple[int, ...] | None,
) -> None:
    encryption = _keyring(
        tmp_path,
        "encryption.json",
        "provider-encryption",
        active_version,
        keys,
    )
    lookup = _keyring(
        tmp_path,
        "hmac.json",
        "provider-lookup-hmac",
        active_version,
        {version: bytes([key[0] + 10]) * 32 for version, key in keys.items()},
        transition_versions=transition_versions,
    )

    with pytest.raises(IdentityCryptoError, match="identity transition invalid"):
        ProviderIdentityCodec(encryption, lookup)


def test_adjacent_rollout_nodes_derive_the_same_transition_candidates(
    tmp_path: Path,
) -> None:
    encryption_keys = {1: b"e" * 32, 2: b"E" * 32, 3: b"f" * 32}
    lookup_keys = {1: b"h" * 32, 2: b"H" * 32, 3: b"i" * 32}

    old_codec = ProviderIdentityCodec(
        _keyring(
            tmp_path,
            "old-encryption.json",
            "provider-encryption",
            2,
            encryption_keys,
        ),
        _keyring(
            tmp_path,
            "old-hmac.json",
            "provider-lookup-hmac",
            2,
            lookup_keys,
            transition_versions=(1, 2, 3),
        ),
    )
    new_codec = ProviderIdentityCodec(
        _keyring(
            tmp_path,
            "new-encryption.json",
            "provider-encryption",
            3,
            encryption_keys,
        ),
        _keyring(
            tmp_path,
            "new-hmac.json",
            "provider-lookup-hmac",
            3,
            lookup_keys,
            transition_versions=(1, 2, 3),
        ),
    )

    assert old_codec.lookup_candidates(
        "employee", "synthetic-provider-id"
    ) == new_codec.lookup_candidates("employee", "synthetic-provider-id")


def test_provider_codec_rejects_any_encryption_hmac_key_overlap(
    tmp_path: Path,
) -> None:
    shared_key = b"shared-key-material".ljust(32, b"!")
    encryption = _keyring(
        tmp_path,
        "encryption.json",
        "provider-encryption",
        2,
        {1: shared_key, 2: b"E" * 32},
    )
    lookup = _keyring(
        tmp_path,
        "hmac.json",
        "provider-lookup-hmac",
        2,
        {1: b"h" * 32, 2: shared_key},
        transition_versions=(1, 2),
    )

    with pytest.raises(IdentityCryptoError, match="identity key separation invalid"):
        ProviderIdentityCodec(encryption, lookup)


def test_provider_codec_rejects_transition_metadata_on_encryption_keyring(
    tmp_path: Path,
) -> None:
    encryption = _keyring(
        tmp_path,
        "encryption.json",
        "provider-encryption",
        2,
        {1: b"e" * 32, 2: b"E" * 32},
        transition_versions=(1, 2),
    )
    lookup = _keyring(
        tmp_path,
        "hmac.json",
        "provider-lookup-hmac",
        2,
        {1: b"h" * 32, 2: b"H" * 32},
        transition_versions=(1, 2),
    )

    with pytest.raises(IdentityCryptoError, match="identity transition invalid"):
        ProviderIdentityCodec(encryption, lookup)


@pytest.mark.parametrize(
    ("payload", "mode", "purpose", "length"),
    [
        ({"purpose": "provider-encryption", "active_version": 1, "keys": {}}, 0o600, "provider-encryption", 32),
        ({"purpose": "provider-encryption", "active_version": 1, "keys": {"1": "not-base64!"}}, 0o600, "provider-encryption", 32),
        ({"purpose": "provider-encryption", "active_version": 1, "keys": {"1": base64.b64encode(b"short").decode()}}, 0o600, "provider-encryption", 32),
        ({"purpose": "provider-lookup-hmac", "active_version": 1, "keys": {"1": base64.b64encode(b"k" * 32).decode()}}, 0o600, "provider-encryption", 32),
        ({"purpose": "provider-encryption", "active_version": 1, "keys": {"1": base64.b64encode(b"k" * 32).decode()}}, 0o644, "provider-encryption", 32),
    ],
)
def test_malformed_or_insecure_keyrings_are_rejected_without_secret_details(
    tmp_path: Path,
    payload: dict,
    mode: int,
    purpose: str,
    length: int,
) -> None:
    path = tmp_path / "keyring.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(mode)

    with pytest.raises(IdentityCryptoError, match="identity keyring unavailable") as caught:
        IdentityKeyring.from_file(
            path,
            expected_purpose=purpose,
            expected_key_length=length,
        )

    assert "not-base64" not in str(caught.value)
    assert base64.b64encode(b"k" * 32).decode() not in str(caught.value)


def test_keyring_requires_exact_mode_0600_and_redacts_repr(tmp_path: Path) -> None:
    keyring = _keyring(
        tmp_path,
        "encryption.json",
        "provider-encryption",
        1,
        {1: b"sensitive-key-material".ljust(32, b"!")},
    )

    rendered = repr(keyring)

    assert stat.S_IMODE((tmp_path / "encryption.json").stat().st_mode) == 0o600
    assert "sensitive-key-material" not in rendered
    assert "<redacted>" in rendered


def test_provider_codec_rejects_non_256_bit_purpose_bound_keys(
    tmp_path: Path,
) -> None:
    encryption = IdentityKeyring.from_file(
        _write_keyring(
            tmp_path / "short-encryption.json",
            purpose="provider-encryption",
            active_version=1,
            keys={1: b"e" * 16},
        ),
        expected_purpose="provider-encryption",
        expected_key_length=16,
    )
    lookup = _keyring(
        tmp_path,
        "hmac.json",
        "provider-lookup-hmac",
        1,
        {1: b"h" * 32},
    )

    with pytest.raises(IdentityCryptoError, match="identity key length invalid"):
        ProviderIdentityCodec(encryption, lookup)


def test_protected_provider_id_repr_redacts_all_protected_values(tmp_path: Path) -> None:
    protected = _codec(tmp_path).seal("employee", "synthetic-provider-id")

    rendered = repr(protected)

    assert "synthetic-provider-id" not in rendered
    assert protected.lookup_hmac.hex() not in rendered
    assert protected.ciphertext.hex() not in rendered
    assert "<redacted>" in rendered


def test_rotation_decrypts_old_value_and_rederives_both_active_versions(tmp_path: Path) -> None:
    old_encryption = _keyring(
        tmp_path,
        "old-encryption.json",
        "provider-encryption",
        1,
        {1: b"e" * 32},
    )
    old_lookup = _keyring(
        tmp_path,
        "old-hmac.json",
        "provider-lookup-hmac",
        1,
        {1: b"h" * 32},
        transition_versions=(1,),
    )
    old_codec = ProviderIdentityCodec(old_encryption, old_lookup)
    old = old_codec.seal("employee", "synthetic-provider-id")
    new_codec = _codec(tmp_path)

    rotated = new_codec.rotate(old)

    assert rotated.subject_kind == old.subject_kind
    assert rotated.lookup_key_version == 2
    assert rotated.encryption_key_version == 2
    assert rotated.lookup_hmac != old.lookup_hmac
    assert rotated.ciphertext != old.ciphertext
    assert new_codec.unseal(rotated) == "synthetic-provider-id"


def test_identity_errors_and_records_never_expose_provider_values(tmp_path: Path) -> None:
    codec = _codec(tmp_path)
    provider_value = "synthetic-provider-id"
    malformed = ProtectedProviderId(
        subject_kind="employee",
        lookup_hmac=b"x" * 32,
        lookup_key_version=2,
        ciphertext=b"too-short",
        encryption_key_version=2,
    )

    with pytest.raises(IdentityCryptoError) as caught:
        codec.equivalent(malformed, codec.seal("employee", provider_value))

    assert provider_value not in str(caught.value)
    assert provider_value not in repr(caught.value)
    assert uuid4().hex not in repr(malformed)
