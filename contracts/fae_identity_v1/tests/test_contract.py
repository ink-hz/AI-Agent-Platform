"""The contract validates its own examples, and hides nothing in them.

This suite runs standalone inside `contracts/fae_identity_v1` (`pytest` from
that directory) and needs nothing from the Platform backend: the point is that
the contract is executable on its own, so both repositories can trust it. Live
endpoint conformance is proved next door, in
`backend/tests/test_fae_identity_contract_asset.py`.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import orbbec_fae_identity_contract as contract
import pytest
from jsonschema import Draft202012Validator, ValidationError

FIXTURES = ("enterprise.json", "partner.json")
EXCHANGE_FIELDS = {
    "contract_version",
    "subject_id",
    "subject_type",
    "internal_user_id",
    "identity_binding_id",
    "agent_id",
    "display_name",
    "partner_display_name",
}
VALIDATE_FIELDS = EXCHANGE_FIELDS | {"active"}
NAMES = ("Contract Example Member", "Contract Example Operator", "Contract Example Partner")


def test_the_schema_itself_is_a_valid_draft_2020_12_schema() -> None:
    schema = contract.load_schema()

    Draft202012Validator.check_schema(schema)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$ref"] == "#/$defs/contract_document"


@pytest.mark.parametrize("name", contract.MESSAGES)
def test_every_message_of_the_contract_has_a_usable_validator(name: str) -> None:
    assert isinstance(contract.validator(name), Draft202012Validator)


def test_an_unknown_message_name_is_refused() -> None:
    with pytest.raises(ValueError, match="fae_identity_contract_unknown_message"):
        contract.message_schema("identity_dump")


@pytest.mark.parametrize("fixture", FIXTURES)
def test_every_checked_in_example_validates_and_holds_the_invariants(
    fixture: str,
) -> None:
    document = contract.load_fixture(fixture)

    contract.validator("contract_document").validate(document)
    contract.validator("exchange_response").validate(document["exchange"])
    contract.validator("validate_response").validate(document["validate"])
    contract.validator("capabilities_response").validate(document["capabilities"])
    contract.check_subject_invariants(document["exchange"])
    contract.check_subject_invariants(document["validate"])

    assert set(document["exchange"]) == EXCHANGE_FIELDS
    assert set(document["validate"]) == VALIDATE_FIELDS
    assert set(document["capabilities"]) == {"partner_login_available"}
    assert document["validate"]["active"] is True


def test_the_two_examples_pin_the_two_generic_subjects() -> None:
    enterprise = contract.load_fixture("enterprise.json")["exchange"]
    partner = contract.load_fixture("partner.json")["exchange"]

    assert enterprise["subject_type"] == "enterprise_member"
    assert enterprise["subject_id"] == enterprise["internal_user_id"]
    assert enterprise["partner_display_name"] is None
    assert partner["subject_type"] == "partner_operator"
    assert partner["internal_user_id"] is None
    assert partner["subject_id"] != enterprise["subject_id"]
    assert {enterprise["agent_id"], partner["agent_id"]} == {contract.AGENT_ID}
    # Both public capability examples are covered, and only by the boolean.
    assert contract.load_fixture("enterprise.json")["capabilities"] == {
        "partner_login_available": False
    }
    assert contract.load_fixture("partner.json")["capabilities"] == {
        "partner_login_available": True
    }


@pytest.mark.parametrize("fixture", FIXTURES)
def test_display_projections_appear_only_in_the_two_back_channel_messages(
    fixture: str,
) -> None:
    """A name is identity, so it may not travel with a launch code or a capability.

    The examples are the reference for what each message is allowed to carry:
    if a name showed up in the public capability response here, an Agent
    implementation would be entitled to render it.
    """
    document = contract.load_fixture(fixture)
    name_fields = {"display_name", "partner_display_name"}

    for message in ("exchange", "validate"):
        assert name_fields <= set(document[message])
    assert not name_fields & set(document["capabilities"])
    capabilities = json.dumps(document["capabilities"], ensure_ascii=False)
    for name in NAMES:
        assert name not in capabilities
    # Nor may an example smuggle a launch code, a redirect target, a session
    # id, or any audit-log field into a message.
    serialized = json.dumps(document, ensure_ascii=False).lower()
    for forbidden in (
        "department",
        "role",
        "provider",
        "cookie",
        "token",
        "csrf",
        "launch",
        "http",
        "session",
        "audit",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "mutation",
    [
        {"contract_version": "orbbec-fae-identity/v2"},
        {"agent_id": "another-agent"},
        {"subject_type": "public_customer"},
        {"subject_id": "3F0D5C62-9B1E-4A7D-8C15-2AD4E6F70B11"},
        {"subject_id": "3f0d5c62-9b1e-4a7d-8c15-2ad4e6f70b11\n"},
        {"identity_binding_id": "not-a-uuid"},
        {"department": "R&D"},
        {"role": "member"},
        {"provider_subject": "provider-staff-1"},
        {"csrf_token": "t"},
        {"session_token": "t"},
        {"active": True},
        {"display_name": "line\nbreak"},
        {"display_name": "Name\n"},
        {"display_name": " padded"},
        {"display_name": "n" * 65},
        {"display_name": ""},
    ],
)
def test_the_schema_refuses_forbidden_or_malformed_exchange_material(
    mutation: dict,
) -> None:
    payload = {**contract.load_fixture("enterprise.json")["exchange"], **mutation}

    with pytest.raises(ValidationError):
        contract.validator("exchange_response").validate(payload)


def test_the_schema_refuses_a_subject_that_mixes_the_two_shapes() -> None:
    enterprise = contract.load_fixture("enterprise.json")["exchange"]
    partner = contract.load_fixture("partner.json")["exchange"]

    with pytest.raises(ValidationError):
        contract.validator("exchange_response").validate(
            {**enterprise, "internal_user_id": None}
        )
    with pytest.raises(ValidationError):
        contract.validator("exchange_response").validate(
            {**partner, "internal_user_id": partner["subject_id"]}
        )
    with pytest.raises(ValidationError):
        contract.validator("exchange_response").validate(
            {**enterprise, "partner_display_name": "Contract Example Partner"}
        )


def test_the_invariants_catch_what_the_schema_cannot_state() -> None:
    enterprise = contract.load_fixture("enterprise.json")["exchange"]

    with pytest.raises(ValueError, match="enterprise_subject_mismatch"):
        contract.check_subject_invariants(
            {**enterprise, "internal_user_id": "0b62e8d5-1f34-4a97-8d20-6c5b9e1743af"}
        )
    with pytest.raises(ValueError, match="version_mismatch"):
        contract.check_subject_invariants({**enterprise, "contract_version": "v1"})
    with pytest.raises(ValueError, match="agent_mismatch"):
        contract.check_subject_invariants({**enterprise, "agent_id": "other"})
    with pytest.raises(ValueError, match="unknown_subject_type"):
        contract.check_subject_invariants({**enterprise, "subject_type": "guest"})
    with pytest.raises(ValueError, match="partner_internal_user"):
        contract.check_subject_invariants(
            {
                **contract.load_fixture("partner.json")["exchange"],
                "internal_user_id": "3f0d5c62-9b1e-4a7d-8c15-2ad4e6f70b11",
            }
        )


def test_a_validation_is_only_a_response_while_it_is_active() -> None:
    payload = contract.load_fixture("partner.json")["validate"]

    contract.validator("validate_response").validate(payload)
    for broken in ({**payload, "active": False}, {k: v for k, v in payload.items() if k != "active"}):
        with pytest.raises(ValidationError):
            contract.validator("validate_response").validate(broken)


def test_the_capability_response_stays_the_lone_unversioned_boolean() -> None:
    validate = contract.validator("capabilities_response")

    validate.validate({"partner_login_available": True})
    for broken in (
        {"partner_login_available": "yes"},
        {"partner_login_available": True, "contract_version": contract.CONTRACT_VERSION},
        {"partner_login_available": True, "display_name": "Contract Example Operator"},
        {"partner_login_available": True, "partner_auth_start_url": "https://example.invalid"},
        {},
    ):
        with pytest.raises(ValidationError):
            validate.validate(broken)


def test_the_digest_is_the_frozen_path_and_bytes_chain(tmp_path: Path) -> None:
    (tmp_path / "schema").mkdir()
    (tmp_path / "fixtures" / "nested").mkdir(parents=True)
    (tmp_path / "schema" / "s.json").write_bytes(b"S")
    (tmp_path / "fixtures" / "a.json").write_bytes(b"A")
    (tmp_path / "fixtures" / "nested" / "b.json").write_bytes(b"B")

    expected = sha256(
        b"fixtures/a.json\0A\0fixtures/nested/b.json\0B\0schema/s.json\0S\0"
    ).hexdigest()

    assert contract.DIGEST_DIRECTORIES == ("fixtures", "schema")
    assert contract.contract_digest(tmp_path) == expected
    # This package's own code and metadata are outside the pin on purpose.
    (tmp_path / "pyproject.toml").write_text("[project]\n", "utf-8")
    assert contract.contract_digest(tmp_path) == expected


def test_the_digest_of_this_contract_is_stable_and_covers_every_file() -> None:
    first = contract.contract_digest()

    assert first == contract.contract_digest(contract.ROOT)
    assert len(first) == 64 and first == first.lower()
    covered = {
        path.relative_to(contract.ROOT).as_posix()
        for directory in contract.DIGEST_DIRECTORIES
        for path in (contract.ROOT / directory).rglob("*")
        if path.is_file()
    }
    assert covered == {
        "fixtures/enterprise.json",
        "fixtures/partner.json",
        "schema/fae-identity-v1.schema.json",
    }


def test_an_incomplete_contract_has_no_digest(tmp_path: Path) -> None:
    (tmp_path / "schema").mkdir()

    with pytest.raises(ValueError, match="fae_identity_contract_incomplete"):
        contract.contract_digest(tmp_path)
