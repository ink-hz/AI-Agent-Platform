from __future__ import annotations

import hashlib
import hmac

import pytest

from app.control_plane.crypto import IdentityKeyring
from app.control_plane.partner_identity_crypto import (
    PartnerProviderIdentityCodec,
    PartnerProviderIdentityCryptoError,
    ProtectedPartnerProviderIdentity,
)


def _codec(*, active_version: int = 2) -> PartnerProviderIdentityCodec:
    return PartnerProviderIdentityCodec(
        encryption=IdentityKeyring(
            active_version,
            "partner-provider-encryption",
            {1: b"e" * 32, 2: b"E" * 32},
        ),
        hmac_keyring=IdentityKeyring(
            active_version,
            "partner-provider-lookup-hmac",
            {1: b"h" * 32, 2: b"H" * 32},
            transition_versions=(1, 2),
        ),
    )


def test_partner_provider_codec_uses_independent_domain_and_redacts_identity() -> None:
    codec = _codec()

    first = codec.seal("qianniu", "  synthetic-seat-42  ")
    second = codec.seal("qianniu", "synthetic-seat-42")

    expected = hmac.digest(
        b"H" * 32,
        b"partner-provider:qianniu:synthetic-seat-42",
        hashlib.sha256,
    )
    enterprise_domain = hmac.digest(
        b"H" * 32,
        b"dingtalk:qianniu:synthetic-seat-42",
        hashlib.sha256,
    )
    assert first.lookup_hmac == second.lookup_hmac == expected
    assert first.lookup_hmac != enterprise_domain
    assert first.ciphertext != second.ciphertext
    assert first.lookup_key_version == first.encryption_key_version == 2
    assert codec.unseal(first) == "synthetic-seat-42"
    assert "synthetic-seat-42" not in repr(first)
    assert first.lookup_hmac.hex() not in repr(first)
    assert first.ciphertext.hex() not in repr(first)


def test_partner_provider_codec_authenticates_kind_and_version_without_leaks() -> None:
    codec = _codec()
    protected = codec.seal("qianniu", "synthetic-seat-42")
    wrong_kind = ProtectedPartnerProviderIdentity(
        provider_kind="partner-sso",
        provider_subject_lookup_hmac=protected.provider_subject_lookup_hmac,
        lookup_key_version=protected.lookup_key_version,
        provider_subject_ciphertext=protected.provider_subject_ciphertext,
        encryption_key_version=protected.encryption_key_version,
    )
    wrong_version = ProtectedPartnerProviderIdentity(
        provider_kind=protected.provider_kind,
        provider_subject_lookup_hmac=protected.provider_subject_lookup_hmac,
        lookup_key_version=protected.lookup_key_version,
        provider_subject_ciphertext=protected.provider_subject_ciphertext,
        encryption_key_version=1,
    )

    for malformed in (wrong_kind, wrong_version):
        with pytest.raises(
            PartnerProviderIdentityCryptoError,
            match="^partner identity decrypt failed$",
        ) as caught:
            codec.unseal(malformed)
        assert "synthetic-seat-42" not in str(caught.value)


def test_partner_provider_lookup_candidates_follow_ordered_transition_window() -> None:
    codec = PartnerProviderIdentityCodec(
        encryption=IdentityKeyring(
            3,
            "partner-provider-encryption",
            {2: b"e" * 32, 3: b"E" * 32},
        ),
        hmac_keyring=IdentityKeyring(
            3,
            "partner-provider-lookup-hmac",
            {2: b"h" * 32, 3: b"H" * 32},
            transition_versions=(2, 3),
        ),
    )

    candidates = codec.lookup_candidates("partner-sso", "seat-7")

    assert tuple(version for version, _lookup in candidates) == (2, 3)
    assert candidates[0][1] != candidates[1][1]
    assert codec.matches_lookup(
        provider_kind="partner-sso",
        provider_subject="seat-7",
        lookup_hmac=candidates[0][1],
        lookup_key_version=2,
    )


@pytest.mark.parametrize(
    ("encryption", "lookup", "message"),
    [
        (
            IdentityKeyring(1, "provider-encryption", {1: b"e" * 32}),
            IdentityKeyring(
                1,
                "partner-provider-lookup-hmac",
                {1: b"h" * 32},
                transition_versions=(1,),
            ),
            "partner identity keyring purpose invalid",
        ),
        (
            IdentityKeyring(
                2,
                "partner-provider-encryption",
                {1: b"e" * 32, 2: b"E" * 32},
            ),
            IdentityKeyring(
                2,
                "partner-provider-lookup-hmac",
                {1: b"h" * 32, 2: b"H" * 32},
                transition_versions=(2, 1),
            ),
            "partner identity transition invalid",
        ),
        (
            IdentityKeyring(1, "partner-provider-encryption", {1: b"x" * 32}),
            IdentityKeyring(
                1,
                "partner-provider-lookup-hmac",
                {1: b"x" * 32},
                transition_versions=(1,),
            ),
            "partner identity key separation invalid",
        ),
    ],
)
def test_partner_provider_codec_rejects_unsafe_keyrings(
    encryption: IdentityKeyring,
    lookup: IdentityKeyring,
    message: str,
) -> None:
    with pytest.raises(PartnerProviderIdentityCryptoError, match=f"^{message}$"):
        PartnerProviderIdentityCodec(encryption, lookup)
