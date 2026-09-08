"""Internal frozen business input; it is not a dispatchable wire command."""

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass

from pydantic import ConfigDict, Field, create_model

from .contracts_v5 import (
    CoreChatCommandV5,
    V5ContractError,
    canonical_command_bytes,
    parse_v5_command,
)
from .models import (
    _MIME,
    _SHA256,
    OutputWriteGrantPayload,
    TaskAttachmentGrantPayload,
    _bounded_text,
)


def _fields(source, *, excluded=(), included=None):
    return {
        name: (field.annotation, deepcopy(field))
        for name, field in source.model_fields.items()
        if (field.alias or name) not in excluded
        and (included is None or (field.alias or name) in included)
    }


_CONFIG = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
_Attachment = create_model(
    "FrozenInputAttachmentV5",
    __config__=_CONFIG,
    **_fields(
        TaskAttachmentGrantPayload,
        included={
            "attachmentId",
            "displayName",
            "detectedMime",
            "sizeBytes",
            "sha256",
        },
    ),
    index=(int, Field(ge=0, le=31)),
)
_Output = create_model(
    "FrozenOutputScopeV5",
    __config__=_CONFIG,
    **_fields(
        OutputWriteGrantPayload,
        included={"taskId", "agentId", "maxFiles", "maxTotalBytes"},
    ),
)
_Template = create_model(
    "FrozenCommandTemplateV5",
    __config__=_CONFIG,
    **_fields(
        CoreChatCommandV5,
        excluded={
            "leaseEpoch",
            "eventCallbackUrl",
            "commandHash",
            "inputAttachmentGrants",
            "outputWriteGrant",
        },
    ),
    input_attachments=(
        tuple[_Attachment, ...],
        Field(alias="inputAttachments", max_length=32),
    ),
    output_scope=(_Output | None, Field(alias="outputScope")),
)


def _exact(value, model):
    return type(value) is dict and set(value) == {
        field.alias or name for name, field in model.model_fields.items()
    }


@dataclass(frozen=True, repr=False)
class FrozenCommandV5:
    _encoded: bytes

    @property
    def document(self) -> dict:
        return json.loads(self._encoded)

    @property
    def command_hash(self) -> str:
        return hashlib.sha256(self._encoded).hexdigest()


def parse_frozen_command(value) -> FrozenCommandV5:
    try:
        if not _exact(value, _Template):
            raise ValueError
        encoded = canonical_command_bytes(value)
        if len(encoded) > 1024 * 1024:
            raise ValueError
        model = _Template.model_validate_json(encoded, strict=True)
        attachments = value["inputAttachments"]
        output = value["outputScope"]
        scope = value["permissionScope"]
        if (
            not _exact(
                scope, CoreChatCommandV5.model_fields["permission_scope"].annotation
            )
            or type(attachments) is not list
            or any(not _exact(grant, _Attachment) for grant in attachments)
            or (output is not None and not _exact(output, _Output))
            or [grant["index"] for grant in attachments]
            != list(range(len(attachments)))
            or len({grant["attachmentId"] for grant in attachments}) != len(attachments)
            or any(
                _bounded_text(grant["displayName"], maximum=1024)
                != grant["displayName"]
                or "/" in grant["displayName"]
                or "\\" in grant["displayName"]
                or _MIME.fullmatch(grant["detectedMime"]) is None
                or _SHA256.fullmatch(grant["sha256"]) is None
                for grant in attachments
            )
            or scope["principalRef"] != value["principalRef"]
            or scope["conversationId"] != value["conversationId"]
            or scope["agentId"] != value["targetBot"]
            or value["taskSessionId"] != f"platform:{value['conversationId']}:hr-bot"
            or re.fullmatch(r"[0-9a-f]{64}", value["contextHash"]) is None
            or value["retryOf"] in {value["commandId"], value["attemptId"]}
            or (
                output is not None
                and (
                    output["taskId"] != value["runId"] or output["agentId"] != "hr-bot"
                )
            )
        ):
            raise ValueError
        # JSON-strict UUID parsing accepts alternate spellings; P01 wire identity does not.
        normalized = model.model_dump(mode="json", by_alias=True)
        uuid_paths = [
            "runId",
            "commandId",
            "attemptId",
            "turnId",
            "conversationId",
            "triggerMessageId",
        ]
        if any(value[key] != normalized[key] for key in uuid_paths):
            raise ValueError
        if scope["conversationId"] != normalized["permissionScope"]["conversationId"]:
            raise ValueError
        if any(
            a["attachmentId"] != b["attachmentId"]
            for a, b in zip(attachments, normalized["inputAttachments"], strict=True)
        ):
            raise ValueError
        if value["retryOf"] != normalized["retryOf"] or (
            output is not None
            and output["taskId"] != normalized["outputScope"]["taskId"]
        ):
            raise ValueError
        return FrozenCommandV5(encoded)
    except (TypeError, ValueError, KeyError):
        raise V5ContractError("v5 frozen command invalid") from None


def hydrate_frozen_command(
    frozen,
    *,
    lease_epoch,
    event_callback_url,
    input_attachment_grants,
    output_write_grant,
):
    # Revalidate even a manually constructed internal object; only actual complete
    # transport is passed to the frozen P01 parser, never invented credentials.
    frozen = parse_frozen_command(frozen.document)
    value = frozen.document
    value.pop("inputAttachments")
    value.pop("outputScope")
    value.update(
        commandHash=frozen.command_hash,
        leaseEpoch=lease_epoch,
        eventCallbackUrl=event_callback_url,
        inputAttachmentGrants=input_attachment_grants,
        outputWriteGrant=output_write_grant,
    )
    return parse_v5_command(value)
