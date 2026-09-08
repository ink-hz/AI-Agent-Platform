"""v6 boundary checks; no HTTP, database, provider or production calls."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.execution_relay import contracts_v6 as v6

IDS = [f"00000000-0000-4000-8000-{i:012}" for i in range(1,12)]


def command():
    value = {
        "contractVersion": "core_chat_collaboration_v6",
        "runId": IDS[0], "commandId": IDS[1], "attemptId": IDS[2],
        "attemptNo": 1, "leaseEpoch": 1, "turnId": IDS[3], "turnSeq": 1,
        "commandSeq": 1, "conversationId": IDS[4], "triggerMessageId": IDS[5],
        "principalRef": "principal:hr-user", "targetBot": "hr-bot",
        "prompt": "按当前岗位分析材料，保留未知项。", "contextMode": "frozen_intent_with_tools",
        "contextHash": "0" * 64, "commandHash": "0" * 64,
        "eventCallbackUrl": f"http://127.0.0.1:9120/callbacks/{IDS[0]}/{'A'*43}",
        "taskSessionId": f"platform:{IDS[4]}:hr-bot", "resultMode": "public_markdown",
        "permissionScope": {"principalRef": "principal:hr-user", "conversationId": IDS[4],
                            "agentId": "hr-bot", "toolPolicy": "default"},
        "inputAttachmentGrants": [], "outputWriteGrant": None, "retryOf": None,
        "scope": {"positionId": IDS[6], "positionCandidateIds": [], "attachmentIds": []},
        "rolePackage": {"teamCommit": "a" * 40, "catalogRelease": "recruiting-1", "manifestSha256": "b"*64},
        "methodSelection": None,
        "toolCapabilities": ["hr.read_context", "hr.submit_result", "hr.confirm_standard"],
        "businessToolGrant": {"grantId": IDS[7], "bearerToken": "B"*43, "expiresAt": "2026-09-09T00:00:00Z"},
    }
    model = v6.CoreChatCommandV6.model_validate_json(json.dumps(value))
    value["contextHash"] = v6.core_chat_context_hash(model)
    model = v6.CoreChatCommandV6.model_validate_json(json.dumps(value))
    value["commandHash"] = v6.core_chat_command_hash(model)
    return value


def test_command_binds_role_scope_and_intent_but_allows_credential_renewal():
    original = command()
    parsed = v6.parse_v6_command(original)
    assert parsed.scope.position_id is not None
    renewed = deepcopy(original)
    renewed["leaseEpoch"] += 1
    renewed["businessToolGrant"]["bearerToken"] = "C" * 43
    renewed["businessToolGrant"]["expiresAt"] = "2026-09-10T00:00:00Z"
    assert v6.parse_v6_command(renewed).command_hash == parsed.command_hash
    for field, member in [("scope", "positionId"), ("rolePackage", "teamCommit")]:
        changed = deepcopy(original)
        changed[field][member] = IDS[8] if member == "positionId" else "c" * 40
        with pytest.raises(v6.V6ContractError):
            v6.parse_v6_command(changed)


@pytest.mark.parametrize("mutation", ["unknown", "snake", "duplicate", "cross_owner", "unscoped_candidate", "bad_hash", "secret", "grant_missing"])
def test_untrusted_command_rejected_without_echoing_payload(mutation):
    value = command()
    if mutation == "unknown": value["scope"]["owner"] = "PRIVATE_USER_INPUT"
    if mutation == "snake": value["scope"]["position_id"] = value["scope"].pop("positionId")
    if mutation == "duplicate": value["scope"]["attachmentIds"] = [IDS[8], IDS[8]]
    if mutation == "cross_owner": value["permissionScope"]["principalRef"] = "other"
    if mutation == "unscoped_candidate":
        value["scope"]["positionId"] = None
        value["scope"]["positionCandidateIds"] = [IDS[8]]
    if mutation == "bad_hash": value["commandHash"] = "f" * 64
    if mutation == "secret": value["prompt"] = value["businessToolGrant"]["bearerToken"]
    if mutation == "grant_missing": value.pop("businessToolGrant")
    with pytest.raises(v6.V6ContractError) as caught:
        v6.parse_v6_command(value)
    assert str(caught.value) == "v6 command invalid"


def test_result_refs_have_schema_and_unique_identity():
    payload = {
        "source": "agent_runtime", "sourceRef": "runtime:hr", "terminal": "completed",
        "publicAnswerMarkdown": "分析结果已保存。", "artifactIntents": [],
        "executionRecovery": {"evidenceComplete": True, "toolEffect": "write", "hasOutput": True,
                              "executorStopped": True, "executorStopProofRef": "proof:1", "replayUsed": False},
        "resultRefs": [{"resultId": IDS[9], "schemaId": "hr.analysis.v1", "contentSha256": "a"*64}],
    }
    event = {"contractVersion": "core_chat_collaboration_v6", "runId": IDS[0], "commandId": IDS[1],
             "attemptId": IDS[2], "leaseEpoch": 1, "seq": 1, "type": "result",
             "createdAt": "2026-09-08T10:00:00Z", "payload": payload}
    assert len(v6.parse_v6_event(event).payload.result_refs) == 1
    payload["resultRefs"] *= 2
    with pytest.raises(v6.V6ContractError): v6.parse_v6_event(event)


def test_confirmation_requires_message_version_and_selected_changes():
    request = {"tool": "hr.confirm_standard", "operationId": IDS[0], "proposalResultId": IDS[1],
               "confirmationMessageId": IDS[2], "proposalContentSha256": "c"*64,
               "expectedContextVersionId": None, "selectedChangeIds": ["embedded"]}
    parsed = v6.parse_v6_tool_request(request)
    assert parsed.confirmation_message_id is not None
    request["confirmed"] = True
    with pytest.raises(v6.V6ContractError): v6.parse_v6_tool_request(request)
    del request["confirmed"]
    request["selectedChangeIds"] = []
    with pytest.raises(v6.V6ContractError): v6.parse_v6_tool_request(request)


def test_published_command_schema_matches_wire():
    root = Path(__file__).parents[2] / "contracts/hr-execution/v6"
    schema = json.loads((root / "command.schema.json").read_text())
    validator = Draft202012Validator(schema)
    validator.validate(command())
    invalid = command()
    invalid["scope"]["owner"] = "forged"
    assert not validator.is_valid(invalid)
