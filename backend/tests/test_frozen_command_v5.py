from copy import deepcopy

import pytest
from test_execution_contract_v5 import _v5_command

from app.execution_relay.contracts_v5 import V5ContractError
from app.execution_relay.frozen_command_v5 import (
    hydrate_frozen_command,
    parse_frozen_command,
)


def document(wire):
    result = deepcopy(wire)
    for key in ("leaseEpoch", "eventCallbackUrl", "commandHash"):
        result.pop(key)
    result["inputAttachments"] = [
        {
            "index": index,
            **{
                key: grant[key]
                for key in (
                    "attachmentId",
                    "displayName",
                    "detectedMime",
                    "sizeBytes",
                    "sha256",
                )
            },
        }
        for index, grant in enumerate(result.pop("inputAttachmentGrants"))
    ]
    output = result.pop("outputWriteGrant")
    result["outputScope"] = (
        None
        if output is None
        else {
            key: output[key]
            for key in ("taskId", "agentId", "maxFiles", "maxTotalBytes")
        }
    )
    return result


def test_freezes_actual_p01_preimage_without_callback_or_grant_credentials():
    wire = _v5_command()
    frozen = parse_frozen_command(document(wire))
    assert frozen is not None
    assert frozen.command_hash == wire["commandHash"]
    assert frozen.document == document(wire)


@pytest.mark.parametrize(
    "key,value",
    [
        ("leaseEpoch", 1),
        ("eventCallbackUrl", "forbidden"),
        ("commandSeq", True),
        ("commandSeq", 0),
        ("attemptNo", 0),
        ("contextMode", "resume"),
        ("contextHash", "bad"),
        ("taskSessionId", "wrong"),
        ("prompt", ""),
        ("commandId", "ABCDEFAB-0000-4000-8000-000000000001"),
        ("principalRef", ""),
        ("targetBot", "other-bot"),
    ],
)
def test_frozen_template_rejects_transport_and_invalid_business_fields(key, value):
    raw = document(_v5_command())
    raw[key] = value
    with pytest.raises(V5ContractError, match="^v5 frozen command invalid$"):
        parse_frozen_command(raw)


def test_frozen_snapshot_is_not_mutated_through_original_or_returned_document():
    raw = document(_v5_command())
    frozen = parse_frozen_command(raw)
    original = frozen.command_hash
    raw["prompt"] = "changed"
    frozen.document["permissionScope"]["principalRef"] = "changed"
    assert frozen.command_hash == original
    assert frozen.document["prompt"] != "changed"


def hydrate(frozen, wire):
    return hydrate_frozen_command(
        frozen,
        lease_epoch=wire["leaseEpoch"],
        event_callback_url=wire["eventCallbackUrl"],
        input_attachment_grants=wire["inputAttachmentGrants"],
        output_write_grant=wire["outputWriteGrant"],
    )


def test_hydration_uses_actual_grants_and_preserves_hash_through_rotation():
    wire = _v5_command()
    frozen = parse_frozen_command(document(wire))
    first = hydrate(frozen, wire)
    assert first is not None
    assert first.command_hash == frozen.command_hash
    wire["leaseEpoch"] += 1
    wire["eventCallbackUrl"] = wire["eventCallbackUrl"].replace("A" * 43, "B" * 43)
    wire["inputAttachmentGrants"][0]["bearerToken"] = "Z" * 43
    wire["inputAttachmentGrants"][0]["expiresAt"] = "2026-09-08T08:15:00Z"
    assert hydrate(frozen, wire).command_hash == first.command_hash


@pytest.mark.parametrize(
    "mutate",
    [
        lambda w: w["inputAttachmentGrants"].reverse(),
        lambda w: w["inputAttachmentGrants"][0].update(sha256="a" * 64),
        lambda w: w["outputWriteGrant"].update(maxFiles=1),
        lambda w: w.update(eventCallbackUrl="http://example.com/unsafe"),
    ],
)
def test_hydration_rejects_changed_business_grants(mutate):
    wire = _v5_command()
    frozen = parse_frozen_command(document(wire))
    mutate(wire)
    with pytest.raises(V5ContractError):
        hydrate(frozen, wire)


@pytest.mark.parametrize(
    "key,value",
    [
        ("displayName", "../unsafe"),
        ("detectedMime", "bad mime"),
        ("sha256", "z" * 64),
    ],
)
def test_frozen_attachment_validation_retains_original_grant_business_constraints(
    key, value
):
    raw = document(_v5_command())
    raw["inputAttachments"][0][key] = value
    with pytest.raises(V5ContractError):
        parse_frozen_command(raw)
