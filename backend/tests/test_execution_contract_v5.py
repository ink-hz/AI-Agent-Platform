import json
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.execution_relay.contracts_v5 import (
    CallbackAckV5,
    CoreChatCommandV5,
    CoreChatEventV5,
    FeishuMessageIdentity,
    core_chat_command_hash,
    core_chat_command_replay_status,
    feishu_request_id,
    intake_replay_status,
    parse_v5_command,
)
from app.execution_relay.models import (
    CollaborationContract,
    CollaborationV4Result,
    CoreChatCollaborationContract,
    RelayJobPayload,
    SearchRecoveryPayload,
)

RUN_ID = "00000000-0000-4000-8000-000000000501"
COMMAND_ID = "00000000-0000-4000-8000-000000000502"
ATTEMPT_ID = "00000000-0000-4000-8000-000000000503"
TURN_ID = "00000000-0000-4000-8000-000000000504"
CONVERSATION_ID = "00000000-0000-4000-8000-000000000505"
TRIGGER_MESSAGE_ID = "00000000-0000-4000-8000-000000000506"
PRINCIPAL_REF = "principal:hr-user-001"
CONTRACT_DIR = Path(__file__).parents[2] / "contracts" / "hr-execution" / "v5"


def _v5_command() -> dict[str, object]:
    return {
        "contractVersion": "core_chat_collaboration_v5",
        "runId": RUN_ID,
        "commandId": COMMAND_ID,
        "attemptId": ATTEMPT_ID,
        "attemptNo": 1,
        "leaseEpoch": 1,
        "turnId": TURN_ID,
        "turnSeq": 7,
        "commandSeq": 3,
        "conversationId": CONVERSATION_ID,
        "triggerMessageId": TRIGGER_MESSAGE_ID,
        "principalRef": PRINCIPAL_REF,
        "targetBot": "hr-bot",
        "prompt": "根据冻结上下文评估候选人。",
        "contextMode": "frozen_prompt",
        "contextHash": "a" * 64,
        "commandHash": "3ff15a1f835a674267c5ad20c3fe6a6d26139edd9233906742b4cd3e0d9424e9",
        "eventCallbackUrl": f"http://127.0.0.1:9120/callbacks/{RUN_ID}/{'A' * 43}",
        "taskSessionId": f"platform:{CONVERSATION_ID}:hr-bot",
        "resultMode": "public_markdown",
        "permissionScope": {
            "principalRef": PRINCIPAL_REF,
            "conversationId": CONVERSATION_ID,
            "agentId": "hr-bot",
            "toolPolicy": "default",
        },
        "inputAttachmentGrants": [
            {
                "attachmentId": "00000000-0000-4000-8000-000000000507",
                "displayName": "candidate.pdf",
                "detectedMime": "application/pdf",
                "sizeBytes": 1024,
                "sha256": "d" * 64,
                "downloadUrl": (
                    "/api/v1/execution-worker/attachments/"
                    "00000000-0000-4000-8000-000000000507/content"
                ),
                "bearerToken": "C" * 43,
                "expiresAt": "2026-09-07T08:15:00Z",
            },
            {
                "attachmentId": "00000000-0000-4000-8000-000000000508",
                "displayName": "job-description.pdf",
                "detectedMime": "application/pdf",
                "sizeBytes": 2048,
                "sha256": "e" * 64,
                "downloadUrl": (
                    "/api/v1/execution-worker/attachments/"
                    "00000000-0000-4000-8000-000000000508/content"
                ),
                "bearerToken": "D" * 43,
                "expiresAt": "2026-09-07T08:15:00Z",
            },
        ],
        "outputWriteGrant": {
            "taskId": RUN_ID,
            "agentId": "hr-bot",
            "uploadUrl": f"/api/v1/execution-worker/tasks/{RUN_ID}/artifacts",
            "bearerToken": "E" * 43,
            "maxFiles": 8,
            "maxTotalBytes": 52428800,
        },
        "retryOf": None,
    }


def _v5_result_event() -> dict[str, object]:
    return {
        "contractVersion": "core_chat_collaboration_v5",
        "runId": RUN_ID,
        "commandId": COMMAND_ID,
        "attemptId": ATTEMPT_ID,
        "leaseEpoch": 1,
        "seq": 12,
        "type": "result",
        "createdAt": "2026-09-07T08:00:00Z",
        "payload": {
            "source": "agent_runtime",
            "sourceRef": f"run:{RUN_ID}",
            "terminal": "completed",
            "publicAnswerMarkdown": "候选人满足岗位的核心要求。",
            "artifactIntents": [
                {
                    "taskId": RUN_ID,
                    "principalRef": PRINCIPAL_REF,
                    "conversationId": CONVERSATION_ID,
                    "index": 0,
                    "sha256": "c" * 64,
                    "mimeType": "application/pdf",
                    "sizeBytes": 4096,
                    "displayName": "候选人评估.pdf",
                    "opaqueSpoolRef": "spool:result-0001",
                }
            ],
            "executionRecovery": {
                "evidenceComplete": True,
                "toolEffect": "read_only",
                "hasOutput": True,
                "executorStopped": True,
                "executorStopProofRef": "executor-stop:worker-0001",
                "replayUsed": False,
            },
        },
    }


def _apply_mutation(base: object, mutation: dict[str, object]) -> object:
    value = deepcopy(base)
    target = value
    path = mutation["path"]
    assert isinstance(path, list)
    for member in path[:-1]:
        target = target[member]
    target[path[-1]] = mutation["value"]
    return value


def test_channel_key_is_stable_uuid_and_bot_scoped() -> None:
    key = FeishuMessageIdentity("t", "app", "hr-bot", "om_123")
    other = FeishuMessageIdentity("t", "app", "other-bot", "om_123")

    assert isinstance(feishu_request_id(key), UUID)
    assert feishu_request_id(key) == feishu_request_id(key)
    assert feishu_request_id(key) != feishu_request_id(other)


def test_channel_key_scopes_tenant_and_app_without_delimiter_collisions() -> None:
    vectors = (
        (("t", "app", "hr-bot", "om_123"), "678b10d4-f624-56e5-8282-9491551290a4"),
        (("租户", "app", "hr-bot", "om_中文"), "f538a985-3e0c-5f3a-8487-7fbe53000d51"),
        (("a|b", "c", "hr-bot", "om_123"), "53534b22-2e73-54a2-be22-33916a884172"),
        (("a", "b|c", "hr-bot", "om_123"), "88eac4ac-d307-5bdc-ac56-68357ee3adb0"),
    )

    actual = tuple(
        str(feishu_request_id(FeishuMessageIdentity(*identity)))
        for identity, _expected in vectors
    )

    assert actual == tuple(expected for _identity, expected in vectors)
    assert len(set(actual)) == len(vectors)


def test_channel_identity_rejects_empty_or_unbounded_members() -> None:
    with pytest.raises(ValueError, match="^v5 intake identity invalid$"):
        FeishuMessageIdentity("", "app", "hr-bot", "om_123")
    with pytest.raises(ValueError, match="^v5 intake identity invalid$"):
        FeishuMessageIdentity("t", "app", "hr-bot", "消" * 200)


def test_v5_command_has_stable_session_independent_command_and_strict_aliases() -> None:
    command = parse_v5_command(_v5_command())

    assert isinstance(command, CoreChatCommandV5)
    assert str(command.command_id) == COMMAND_ID
    assert str(command.attempt_id) == ATTEMPT_ID
    assert command.task_session_id == f"platform:{CONVERSATION_ID}:hr-bot"
    assert command.command_seq == 3
    assert command.model_dump(mode="json", by_alias=True) == _v5_command()

    extra = deepcopy(_v5_command())
    extra["unexpected"] = True
    with pytest.raises((ValidationError, ValueError), match="v5 command invalid"):
        parse_v5_command(extra)


def test_v5_command_rejects_non_loopback_callback() -> None:
    command = _v5_command()
    command["eventCallbackUrl"] = (
        f"http://192.0.2.10:9120/callbacks/{RUN_ID}/{'A' * 43}"
    )

    with pytest.raises(ValueError, match="^v5 command invalid$"):
        parse_v5_command(command)


def test_v5_command_rejects_tampered_permission_scope() -> None:
    command = _v5_command()
    scope = deepcopy(command["permissionScope"])
    assert isinstance(scope, dict)
    scope["principalRef"] = "principal:other-user"
    command["permissionScope"] = scope

    with pytest.raises(ValueError, match="^v5 command invalid$"):
        parse_v5_command(command)


def test_v5_command_rejects_mismatched_business_hash_without_echoing_secrets() -> None:
    command = _v5_command()
    command["commandHash"] = "f" * 64

    with pytest.raises(ValueError, match="^v5 command invalid$") as exc_info:
        parse_v5_command(command)

    assert "C" * 43 not in str(exc_info.value)


def test_v5_result_event_has_typed_artifact_and_execution_recovery() -> None:
    event = CoreChatEventV5.model_validate_json(
        __import__("json").dumps(_v5_result_event()), strict=True
    )

    assert event.event_type == "result"
    assert event.payload.public_answer_markdown.startswith("候选人")
    assert event.payload.artifact_intents[0].opaque_spool_ref == "spool:result-0001"
    assert event.payload.execution_recovery.tool_effect == "read_only"

    extra = deepcopy(_v5_result_event())
    extra["payload"]["artifactIntents"][0]["localPath"] = "/private/result.pdf"
    with pytest.raises(ValidationError):
        CoreChatEventV5.model_validate_json(
            __import__("json").dumps(extra), strict=True
        )


def test_v5_callback_ack_is_strict_and_cursor_is_contiguous() -> None:
    payload = {
        "status": "accepted",
        "runId": RUN_ID,
        "acceptedThrough": 12,
        "expectedSeq": 13,
    }

    ack = CallbackAckV5.model_validate_json(
        __import__("json").dumps(payload), strict=True
    )

    assert ack.status == "accepted"
    assert ack.accepted_through == 12
    assert ack.expected_seq == 13
    assert ack.model_dump(mode="json", by_alias=True) == payload

    invalid = {**payload, "expectedSeq": 14}
    with pytest.raises(ValidationError):
        CallbackAckV5.model_validate_json(
            __import__("json").dumps(invalid), strict=True
        )
    with pytest.raises(ValidationError):
        CallbackAckV5.model_validate_json(
            __import__("json").dumps({**payload, "unexpected": True}), strict=True
        )


def test_v5_callback_terminal_and_private_progress_payloads_are_disjoint() -> None:
    recovery = deepcopy(_v5_result_event()["payload"]["executionRecovery"])
    payloads = {
        "run_heartbeat": {
            "source": "executor",
            "executorRef": "executor:worker-0001",
            "observedAt": "2026-09-07T08:01:00Z",
            "visibility": "private",
        },
        "error": {
            "source": "agent_runtime",
            "sourceRef": f"run:{RUN_ID}",
            "terminal": "failed",
            "errorCode": "provider_failed",
            "message": "模型执行失败。",
        },
        "cancelled": {
            "source": "agent_runtime",
            "sourceRef": f"run:{RUN_ID}",
            "terminal": "cancelled",
            "reasonCode": "user_requested",
        },
        "interrupted": {
            "source": "agent_runtime",
            "sourceRef": f"run:{RUN_ID}",
            "terminal": "interrupted",
            "reasonCode": "executor_stopped",
            "executionRecovery": recovery,
        },
        "raw_progress": {
            "source": "agent_sdk",
            "sourceRef": "sdk-event:42",
            "visibility": "private",
            "kind": "work_update",
            "text": "正在核对候选人材料。",
        },
    }

    parsed_types: list[str | None] = []
    for event_type, payload in payloads.items():
        event = {
            **_v5_result_event(),
            "type": event_type,
            "payload": payload,
        }
        try:
            parsed_types.append(
                CoreChatEventV5.model_validate_json(
                    __import__("json").dumps(event), strict=True
                ).event_type
            )
        except ValidationError:
            parsed_types.append(None)

    assert parsed_types == list(payloads)


def test_v5_is_receive_only_while_legacy_outbound_contracts_stay_frozen() -> None:
    assert "core_chat_collaboration_v5" in get_args(CoreChatCollaborationContract)
    assert get_args(CollaborationContract) == (
        "core_chat_collaboration_v3",
        "core_chat_collaboration_v4",
    )


def test_v4_search_recovery_remains_valid_and_rejects_execution_recovery() -> None:
    recovery = SearchRecoveryPayload(
        status="partial",
        attempt_count=1,
        last_attempt_at=datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc),
        resumable=True,
        coverage_note="部分来源暂不可用。",
    )
    result = CollaborationV4Result(
        public_answer_markdown="现有搜索结果。",
        completion="partially_completed",
        recovery=recovery,
    )

    assert result.recovery == recovery
    with pytest.raises(ValidationError):
        CollaborationV4Result.model_validate(
            {
                **result.model_dump(),
                "executionRecovery": _v5_result_event()["payload"]["executionRecovery"],
            }
        )


def test_v4_direct_second_command_keeps_legacy_rejection_rule() -> None:
    with pytest.raises(ValidationError, match="direct collaboration command invalid"):
        RelayJobPayload(
            run_id=UUID(RUN_ID),
            conversation_id=UUID(CONVERSATION_ID),
            trigger_message_id=UUID(TRIGGER_MESSAGE_ID),
            agent_id="hr-bot",
            prompt="第二轮。",
            max_turns=24,
            job_kind="direct_agent",
            collaboration_contract="core_chat_collaboration_v4",
            task_session_id="task-session-000000000501",
            message_kind="followup",
            message_seq=2,
            parent_run_id=UUID(RUN_ID),
        )


def test_v5_business_hash_tracks_attachment_order_and_ignores_transport_rotation() -> (
    None
):
    baseline = parse_v5_command(_v5_command())
    rotated = baseline.model_copy(
        update={
            "lease_epoch": 2,
            "event_callback_url": (
                f"http://127.0.0.1:9120/callbacks/{RUN_ID}/{'Z' * 43}"
            ),
            "input_attachment_grants": tuple(
                grant.model_copy(update={"bearer_token": "Y" * 43})
                for grant in baseline.input_attachment_grants
            ),
            "output_write_grant": baseline.output_write_grant.model_copy(
                update={"bearer_token": "X" * 43}
            ),
        }
    )
    reordered = baseline.model_copy(
        update={
            "input_attachment_grants": tuple(reversed(baseline.input_attachment_grants))
        }
    )
    changed_sha = baseline.model_copy(
        update={
            "input_attachment_grants": (
                baseline.input_attachment_grants[0].model_copy(
                    update={"sha256_hex": "f" * 64}
                ),
                baseline.input_attachment_grants[1],
            )
        }
    )
    parsed = (baseline, rotated, reordered, changed_sha)
    hashes = tuple(core_chat_command_hash(item) for item in parsed)

    assert hashes[0] == hashes[1]
    assert hashes[0] != hashes[2]
    assert hashes[0] != hashes[3]
    assert core_chat_command_replay_status(parsed[0], parsed[1]) == "duplicate"
    assert core_chat_command_replay_status(parsed[0], parsed[3]) == "conflict"

    new_command = baseline.model_copy(
        update={"command_id": UUID("00000000-0000-4000-8000-000000000509")}
    )
    assert core_chat_command_replay_status(parsed[0], new_command) == "accepted"


def test_v5_command_rejects_boolean_and_unsafe_integer_sequences() -> None:
    rejected: list[bool] = []
    for value in (True, 9_007_199_254_740_992):
        command = {**_v5_command(), "commandSeq": value}
        try:
            CoreChatCommandV5.model_validate_json(
                __import__("json").dumps(command), strict=True
            )
            rejected.append(False)
        except ValidationError:
            rejected.append(True)

    assert rejected == [True, True]


def test_v5_command_wire_rejects_internal_snake_case_names() -> None:
    command = _v5_command()
    command["command_id"] = command.pop("commandId")

    with pytest.raises(ValueError, match="^v5 command invalid$"):
        parse_v5_command(command)


def test_shared_v5_cases_cover_each_complete_schema_category() -> None:
    schema_names = (
        "command",
        "callback",
        "snapshot",
        "channel-bridge",
        "runtime-config",
    )
    missing = [
        name
        for name in (*schema_names, "cases")
        if not (CONTRACT_DIR / f"{name}.schema.json").exists()
        and not (name == "cases" and (CONTRACT_DIR / "cases.json").exists())
    ]

    assert missing == []

    cases = json.loads((CONTRACT_DIR / "cases.json").read_text(encoding="utf-8"))
    validators = {}
    for name in schema_names:
        schema = json.loads(
            (CONTRACT_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        validators[name] = Draft202012Validator(
            schema, format_checker=Draft202012Validator.FORMAT_CHECKER
        )

    assert cases["command"]["valid"] == _v5_command()
    assert cases["callback"]["validEvents"][1] == _v5_result_event()
    assert len(cases["callback"]["validEvents"]) == 6
    assert len(cases["channelBridge"]["validRequests"]) == 6
    assert len(cases["snapshot"]["valid"]) == 3

    validators["command"].validate(cases["command"]["valid"])
    for value in (
        *cases["callback"]["validEvents"],
        *cases["callback"]["validAcks"],
    ):
        validators["callback"].validate(value)
    for value in cases["snapshot"]["valid"]:
        validators["snapshot"].validate(value)
    for value in (
        *cases["channelBridge"]["validRequests"],
        *cases["channelBridge"]["validResponses"],
    ):
        validators["channel-bridge"].validate(value)
    validators["runtime-config"].validate(cases["runtimeConfig"]["valid"])


def test_same_channel_key_with_different_business_content_is_conflict() -> None:
    request_id = UUID("678b10d4-f624-56e5-8282-9491551290a4")

    assert intake_replay_status(request_id, "1" * 64, request_id, "1" * 64) == (
        "duplicate"
    )
    assert intake_replay_status(request_id, "1" * 64, request_id, "2" * 64) == (
        "conflict"
    )
    assert (
        intake_replay_status(
            request_id,
            "1" * 64,
            UUID("f538a985-3e0c-5f3a-8487-7fbe53000d51"),
            "2" * 64,
        )
        == "accepted"
    )


def test_shared_invalid_command_mutations_fail_at_the_strict_parser() -> None:
    cases = json.loads((CONTRACT_DIR / "cases.json").read_text(encoding="utf-8"))

    for mutation in cases["command"]["invalidMutations"]:
        value = deepcopy(cases["command"]["valid"])
        target = value
        for member in mutation["path"][:-1]:
            target = target[member]
        target[mutation["path"][-1]] = mutation["value"]
        with pytest.raises(ValueError, match="^v5 command invalid$"):
            parse_v5_command(value)


def test_typescript_reads_the_same_identity_hash_and_replay_cases() -> None:
    verifier = Path(__file__).with_name("verify_execution_contract_v5.ts")

    assert verifier.exists()

    result = subprocess.run(
        ["node", str(verifier), str(CONTRACT_DIR / "cases.json")],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "v5 cross-language fixtures: ok"


def test_channel_cases_freeze_verified_sender_binding_and_upload_outcomes() -> None:
    cases = json.loads((CONTRACT_DIR / "cases.json").read_text(encoding="utf-8"))
    requests = {
        value["operation"]: value for value in cases["channelBridge"]["validRequests"]
    }
    statuses = {value["status"] for value in cases["channelBridge"]["validResponses"]}

    assert set(requests["resolve-channel"]) >= {
        "identity",
        "senderIdentity",
        "chatId",
        "threadKey",
        "identityOperation",
    }
    assert set(requests["turn-intake"]) >= {
        "identity",
        "senderIdentity",
        "chatId",
        "threadKey",
    }
    assert statuses == {
        "accepted",
        "duplicate",
        "deferred",
        "conflict",
        "bound",
        "binding_required",
        "upload_created",
        "chunk_accepted",
        "attachment_completed",
        "receipts_accepted",
    }


def test_snapshot_cases_reject_terminal_and_attempt_state_contradictions() -> None:
    cases = json.loads((CONTRACT_DIR / "cases.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (CONTRACT_DIR / "snapshot.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(
        schema, format_checker=Draft202012Validator.FORMAT_CHECKER
    )

    assert len(cases["snapshot"].get("invalid", [])) >= 5
    for invalid in cases["snapshot"]["invalid"]:
        value = deepcopy(cases["snapshot"]["valid"][invalid["baseIndex"]])
        target = value
        for member in invalid["path"][:-1]:
            target = target[member]
        target[invalid["path"][-1]] = invalid["value"]
        assert not validator.is_valid(value), invalid["reason"]


def test_shared_invalid_corpus_distinguishes_schema_and_semantic_checks() -> None:
    cases = json.loads((CONTRACT_DIR / "cases.json").read_text(encoding="utf-8"))
    validators = {}
    for name in ("command", "callback", "channel-bridge", "runtime-config"):
        schema = json.loads(
            (CONTRACT_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
        )
        validators[name] = Draft202012Validator(
            schema, format_checker=Draft202012Validator.FORMAT_CHECKER
        )

    for mutation in cases["command"]["invalidMutations"]:
        value = _apply_mutation(cases["command"]["valid"], mutation)
        assert validators["command"].is_valid(value) is not mutation["schemaRejects"]
        with pytest.raises(ValueError, match="^v5 command invalid$"):
            parse_v5_command(value)

    for mutation in cases["callback"]["invalidEventMutations"]:
        value = _apply_mutation(
            cases["callback"]["validEvents"][mutation["baseIndex"]], mutation
        )
        assert validators["callback"].is_valid(value) is not mutation["schemaRejects"]
        with pytest.raises(ValidationError):
            CoreChatEventV5.model_validate_json(json.dumps(value), strict=True)

    for mutation in cases["callback"]["invalidAckMutations"]:
        value = _apply_mutation(
            cases["callback"]["validAcks"][mutation["baseIndex"]], mutation
        )
        assert validators["callback"].is_valid(value) is not mutation["schemaRejects"]
        with pytest.raises(ValidationError):
            CallbackAckV5.model_validate_json(json.dumps(value), strict=True)

    for mutation in cases["channelBridge"]["invalidMutations"]:
        value = _apply_mutation(
            cases["channelBridge"][mutation["collection"]][mutation["baseIndex"]],
            mutation,
        )
        assert not validators["channel-bridge"].is_valid(value), mutation["reason"]

    for mutation in cases["runtimeConfig"]["invalidMutations"]:
        value = _apply_mutation(cases["runtimeConfig"]["valid"], mutation)
        assert not validators["runtime-config"].is_valid(value), mutation["reason"]

    for replay in cases["intakeReplayCases"]:
        assert (
            intake_replay_status(
                UUID(replay["existingRequestId"]),
                replay["existingContentHash"],
                UUID(replay["incomingRequestId"]),
                replay["incomingContentHash"],
            )
            == replay["expectedStatus"]
        )
