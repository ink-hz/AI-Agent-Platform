from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec, SealedContent
from app.execution_relay.models import (
    CitationPayload,
    CollaborationV4Result,
    OutputWriteGrantPayload,
    RegisteredArtifactPayload,
    RelayEvent,
    RelayJobPayload,
    SearchRecoveryPayload,
    TaskAttachmentGrantPayload,
)
from app.execution_relay.worker import WorkerRuntime, _StrictCallbackEvent

RUN_ID = UUID("00000000-0000-4000-8000-000000000101")
CONVERSATION_ID = UUID("00000000-0000-4000-8000-000000000102")
TRIGGER_MESSAGE_ID = UUID("00000000-0000-4000-8000-000000000103")
ATTACHMENT_ID = UUID("00000000-0000-4000-8000-000000000105")
NOW = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOKEN = "A" * 43


def _input_grant() -> TaskAttachmentGrantPayload:
    return TaskAttachmentGrantPayload(
        attachment_id=ATTACHMENT_ID,
        display_name="candidate.pdf",
        detected_mime="application/pdf",
        size_bytes=1024,
        sha256_hex="a" * 64,
        download_url=(f"/api/v1/execution-worker/attachments/{ATTACHMENT_ID}/content"),
        bearer_token=TOKEN,
        expires_at=NOW,
    )


def _output_grant() -> OutputWriteGrantPayload:
    return OutputWriteGrantPayload(
        task_id=RUN_ID,
        agent_id="hr-bot",
        upload_url=f"/api/v1/execution-worker/tasks/{RUN_ID}/artifacts",
        bearer_token="B" * 43,
        max_files=8,
        max_total_bytes=50 * 1024 * 1024,
    )


def _v3_payload() -> RelayJobPayload:
    return RelayJobPayload(
        run_id=RUN_ID,
        conversation_id=CONVERSATION_ID,
        trigger_message_id=TRIGGER_MESSAGE_ID,
        agent_id="hr-bot",
        prompt="分析附件",
        max_turns=24,
        job_kind="metabot_local",
        collaboration_contract="core_chat_collaboration_v3",
        task_session_id="task-session-000000000101",
    )


def _v4_result() -> CollaborationV4Result:
    return CollaborationV4Result(
        public_answer_markdown="候选人具备视觉算法经验。",
        citations=(
            CitationPayload(
                citation_key="candidate-profile",
                title="候选人公开项目",
                url="https://example.com/profile",
                site="example.com",
                retrieved_at=NOW,
                supports=("视觉算法经验",),
            ),
        ),
        artifacts=(
            RegisteredArtifactPayload(
                attachment_id=ATTACHMENT_ID,
                artifact_key="candidate-evaluation",
                producer_version_id="report-v1",
                display_name="候选人评估.pdf",
                status="ready",
            ),
        ),
        completion="partially_completed",
        recovery=SearchRecoveryPayload(
            status="partial",
            attempt_count=1,
            last_attempt_at=NOW,
            resumable=True,
            coverage_note="缺少一项英文沟通证据。",
        ),
    )


def test_v3_payload_json_bytes_are_unchanged() -> None:
    assert _v3_payload().model_dump_json() == (
        '{"run_id":"00000000-0000-4000-8000-000000000101",'
        '"conversation_id":"00000000-0000-4000-8000-000000000102",'
        '"trigger_message_id":"00000000-0000-4000-8000-000000000103",'
        '"agent_id":"hr-bot","prompt":"分析附件","max_turns":24,'
        '"job_kind":"metabot_local","result_mode":"internal",'
        '"requester_subject":null,'
        '"collaboration_contract":"core_chat_collaboration_v3",'
        '"task_session_id":"task-session-000000000101",'
        '"message_kind":"initial","message_seq":1,"parent_run_id":null}'
    )


def test_only_v4_can_carry_task_scoped_attachment_grants() -> None:
    with pytest.raises(ValidationError, match="v4"):
        RelayJobPayload.model_validate(
            {
                **_v3_payload().model_dump(),
                "input_attachment_grants": (_input_grant(),),
            }
        )

    payload = RelayJobPayload(
        **{
            **_v3_payload().model_dump(),
            "collaboration_contract": "core_chat_collaboration_v4",
            "input_attachment_grants": (_input_grant(),),
            "output_write_grant": _output_grant(),
        }
    )

    assert payload.input_attachment_grants == (_input_grant(),)
    assert payload.output_write_grant == _output_grant()
    assert TOKEN not in repr(payload)
    assert TOKEN not in repr(payload.input_attachment_grants[0])


def test_v4_result_has_separate_answer_citation_and_registered_artifact_channels() -> (
    None
):
    result = _v4_result()

    assert result.public_answer_markdown.startswith("候选人")
    assert result.citations[0].citation_key == "candidate-profile"
    assert result.artifacts[0].attachment_id == ATTACHMENT_ID
    assert result.completion == "partially_completed"


@pytest.mark.parametrize(
    "artifact",
    (
        {
            "attachment_id": str(ATTACHMENT_ID),
            "artifact_key": "bridge-private",
            "producer_version_id": "report-v1",
            "display_name": "candidate.pdf",
            "status": "ready",
        },
        {
            "attachment_id": str(ATTACHMENT_ID),
            "artifact_key": "candidate-evaluation",
            "producer_version_id": "report-v1",
            "display_name": "/tmp/candidate.pdf",
            "status": "ready",
        },
        {
            "artifact_key": "candidate-evaluation",
            "producer_version_id": "report-v1",
            "display_name": "candidate.pdf",
            "status": "ready",
        },
        {
            "attachment_id": str(ATTACHMENT_ID),
            "artifact_key": "candidate-evaluation",
            "producer_version_id": "report-v1",
            "display_name": "candidate.pdf",
            "status": "ready",
            "local_path": "/Users/agentops/private/candidate.pdf",
        },
    ),
)
def test_v4_result_rejects_private_or_unregistered_artifacts(artifact: dict) -> None:
    with pytest.raises(ValidationError):
        CollaborationV4Result.model_validate(
            {
                "public_answer_markdown": "已生成结果。",
                "citations": [],
                "artifacts": [artifact],
                "completion": "completed",
                "recovery": None,
            }
        )


def test_worker_callback_accepts_only_a_valid_v4_registered_result() -> None:
    event = {
        "runId": str(RUN_ID),
        "seq": 1,
        "type": "result",
        "createdAt": NOW.isoformat(),
        "bridge": {
            "botName": "hr-bot",
            "executionChatId": f"platform-{CONVERSATION_ID}-hr-bot",
        },
        "payload": {
            "source": "agent_runtime",
            "sourceRef": f"run:{RUN_ID}",
            "result": {
                "contractVersion": "core_chat_collaboration_v4",
                **_v4_result().model_dump(mode="json", by_alias=True),
            },
        },
    }

    parsed = _StrictCallbackEvent.model_validate_json(json.dumps(event), strict=True)
    assert parsed.payload["result"]["artifacts"][0]["attachmentId"] == str(
        ATTACHMENT_ID
    )

    event["payload"]["result"]["artifacts"][0]["localPath"] = (
        "/Users/agentops/private/candidate.pdf"
    )
    with pytest.raises(ValidationError):
        _StrictCallbackEvent.model_validate_json(json.dumps(event), strict=True)


def test_v3_result_cannot_smuggle_v4_citations_or_attachment_ids() -> None:
    event = {
        "runId": str(RUN_ID),
        "seq": 1,
        "type": "result",
        "createdAt": NOW.isoformat(),
        "bridge": {
            "botName": "hr-bot",
            "executionChatId": f"platform-{CONVERSATION_ID}-hr-bot",
        },
        "payload": {
            "source": "agent_runtime",
            "sourceRef": f"run:{RUN_ID}",
            "result": {
                "contractVersion": "core_chat_result_v2",
                "success": True,
                "outputText": "回答",
                "artifacts": [{"attachmentId": str(ATTACHMENT_ID)}],
            },
        },
    }

    with pytest.raises(ValidationError):
        _StrictCallbackEvent.model_validate_json(json.dumps(event), strict=True)


def test_v4_failed_completion_terminalizes_the_relay_as_failed() -> None:
    event = RelayEvent(
        run_id=RUN_ID,
        seq=1,
        event_type="agent.result",
        created_at=NOW,
        payload={
            "source": "agent_runtime",
            "sourceRef": f"run:{RUN_ID}",
            "result": {
                "contractVersion": "core_chat_collaboration_v4",
                "publicAnswerMarkdown": "",
                "citations": [],
                "artifacts": [],
                "completion": "failed",
                "recovery": None,
            },
        },
    )

    assert WorkerRuntime._terminal_status(event) == "failed"  # noqa: SLF001


def test_v4_bearers_exist_only_inside_encrypted_command_and_safe_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = RelayJobPayload(
        **{
            **_v3_payload().model_dump(),
            "collaboration_contract": "core_chat_collaboration_v4",
            "input_attachment_grants": (_input_grant(),),
            "output_write_grant": _output_grant(),
        }
    )
    codec = ContentCodec(
        IdentityKeyring(
            active_version=1,
            purpose="platform-content-encryption",
            _keys={1: b"k" * 32},
        )
    )
    document = payload.model_dump(mode="json")
    sealed = codec.seal_json(f"execution-job:test:{RUN_ID}", document)

    assert document["input_attachment_grants"][0]["bearer_token"] == TOKEN
    assert TOKEN.encode() not in sealed.ciphertext
    restored = codec.unseal_json(
        f"execution-job:test:{RUN_ID}",
        SealedContent(sealed.ciphertext, sealed.key_version),
    )
    assert restored["input_attachment_grants"][0]["bearer_token"] == TOKEN

    logger = logging.getLogger("test.metabot-collaboration-v4")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        WorkerRuntime._safe_log(
            SimpleNamespace(worker_id="worker-1", logger=logger),
            "dispatch_failed",
            RuntimeError(TOKEN),
            run_id=RUN_ID,
            agent_id="hr-bot",
        )
    assert TOKEN not in caplog.text
