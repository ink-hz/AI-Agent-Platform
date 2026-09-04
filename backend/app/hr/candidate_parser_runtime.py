from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from psycopg.rows import dict_row

from app.agent_brain.conversation_repository import message_subject
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)

from .candidate_models import (
    AttachCandidateDraftExecution,
    CandidateDraftProcessingAttempt,
    ClaimNextCandidateDraft,
    CompleteCandidateDraft,
    FailCandidateDraft,
)
from .candidate_repository import (
    CandidateConflict,
    CandidateNotFound,
    CandidateUnavailable,
)

_PARSER_PROMPT = """你正在执行一份候选人简历的结构化提取任务。
只读取本任务获得授权的唯一附件；不要使用其他会话、岗位或候选人材料。
只返回一个 JSON 对象，不要 Markdown 代码围栏、解释或前后缀。
对象必须且只能包含两个键：
1. extracted_facts：可核验的候选人事实对象，只允许 stable_name、summary、contact、education、experiences、projects、skills、certifications、languages、awards、publications、unknowns、sources。
2. identity_candidate_ids：必须始终为空数组；禁止猜测或生成数据库候选人 ID。
不得提取或推断年龄、出生日期、性别、民族、宗教、婚育、健康、残障、政治面貌等受保护信息，也不得输出 ATS、流程阶段、Offer、排期、自动淘汰或存储定位字段。
材料没有证明的内容写入 unknowns，不得当作负面能力结论。"""

_TERMINAL_NAMESPACE = uuid5(NAMESPACE_URL, "orbbec:hr:candidate-parser:terminal")
_TERMINAL_EXECUTION_STATES = frozenset(
    {"completed", "failed", "cancelled", "interrupted"}
)
logger = logging.getLogger(__name__)


class CandidateParserProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateParserSubmission:
    attempt_id: UUID
    owner_id: UUID
    draft_id: UUID
    attachment_id: UUID
    draft_client_request_id: UUID
    client_request_id: UUID
    request_collision: bool = False

    @classmethod
    def from_attempt(
        cls, attempt: CandidateDraftProcessingAttempt
    ) -> CandidateParserSubmission:
        if not isinstance(attempt, CandidateDraftProcessingAttempt):
            raise ValueError("candidate parser attempt required")
        return cls(
            attempt.attempt_id,
            attempt.owner_id,
            attempt.draft_id,
            attempt.attachment_id,
            attempt.draft_client_request_id,
            attempt.attempt_id,
        )


@dataclass(frozen=True, slots=True)
class DecodedCandidateParserResponse:
    extracted_facts: dict[str, object]
    identity_candidate_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class CandidateParserExecutionResult:
    execution_status: Literal["completed", "failed", "cancelled", "interrupted"]
    turn_status: Literal["completed", "failed", "cancelled", "interrupted"]
    assistant_content: str | None

    def __post_init__(self) -> None:
        if (
            self.execution_status not in _TERMINAL_EXECUTION_STATES
            or self.turn_status not in _TERMINAL_EXECUTION_STATES
            or (
                self.assistant_content is not None
                and not isinstance(self.assistant_content, str)
            )
        ):
            raise ValueError("candidate parser execution result invalid")


def decode_candidate_parser_response(value: str) -> DecodedCandidateParserResponse:
    try:
        if not isinstance(value, str) or not value.strip():
            raise ValueError
        document = json.loads(value.strip())
        if type(document) is not dict or set(document) != {
            "extracted_facts",
            "identity_candidate_ids",
        }:
            raise ValueError
        raw_facts = document["extracted_facts"]
        raw_identities = document["identity_candidate_ids"]
        if type(raw_facts) is not dict or type(raw_identities) is not list:
            raise ValueError
        if raw_identities:
            raise ValueError
        identities = tuple(UUID(item) for item in raw_identities)
        # Reuse the public candidate command as the single protected/ATS field,
        # size, UUID, duplicate, and facts-shape validator.
        validated = CompleteCandidateDraft(
            UUID(int=1), UUID(int=2), UUID(int=3), 1,
            raw_facts, identities,
        )
        return DecodedCandidateParserResponse(
            validated.extracted_facts,
            validated.identity_candidates,
        )
    except (
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        raise CandidateParserProtocolError(
            "candidate parser response invalid"
        ) from None


class CandidateParserAppRepository:
    """App-role discovery for submission and exact parser attachment scope."""

    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("candidate parser database URL required")
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def next_submission(self) -> CandidateParserSubmission | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select attempt.attempt_id,attempt.owner_internal_user_id,"
                    "attempt.draft_id,attempt.attachment_id,"
                    "attempt.draft_client_request_id,conversation.conversation_id,"
                    "conversation.mode,conversation.direct_agent_id,"
                    "exists(select 1 from platform_hr.position_conversations scope "
                    "where scope.conversation_id=conversation.conversation_id) "
                    "as position_bound,"
                    "exists(select 1 from platform_control.conversation_turns turn "
                    "where turn.conversation_id=conversation.conversation_id "
                    "and turn.client_request_id=attempt.attempt_id) "
                    "as exact_turn "
                    "from platform_hr.candidate_draft_processing_attempts attempt "
                    "left join platform_control.conversations conversation on "
                    "conversation.owner_internal_user_id="
                    "attempt.owner_internal_user_id and "
                    "conversation.started_by_client_request_id=attempt.attempt_id "
                    "where attempt.state='processing' "
                    "and attempt.lease_expires_at>now() and ("
                    "conversation.conversation_id is null or conversation.mode<>"
                    "'direct_agent' or conversation.direct_agent_id<>'hr-bot' or "
                    "exists(select 1 from platform_hr.position_conversations scope "
                    "where scope.conversation_id=conversation.conversation_id) or "
                    "not exists(select 1 from platform_control.conversation_turns turn "
                    "where turn.conversation_id=conversation.conversation_id and "
                    "turn.client_request_id=attempt.attempt_id)) "
                    "order by attempt.claimed_at,attempt.attempt_id limit 1"
                ).fetchone()
            if row is None:
                return None
            return CandidateParserSubmission(
                row["attempt_id"],
                row["owner_internal_user_id"],
                row["draft_id"],
                row["attachment_id"],
                row["draft_client_request_id"],
                row["attempt_id"],
                row["conversation_id"] is not None,
            )
        except (CandidateConflict, CandidateUnavailable):
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise CandidateUnavailable("candidate parser submission unavailable") from None

    def candidate_parser_input_for_turn(
        self, owner_id: UUID, conversation_id: UUID, turn_id: UUID
    ) -> UUID | None:
        if any(not isinstance(value, UUID) for value in (
            owner_id, conversation_id, turn_id
        )):
            raise ValueError("candidate parser turn identity invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select attempt.attachment_id,attempt.state,"
                    "attempt.lease_expires_at,attachment.state as attachment_state,"
                    "attachment.retained_until,attachment.immutable_locator,"
                    "exists(select 1 from platform_attachments.erasure_jobs erasure "
                    "where erasure.attachment_id=attachment.attachment_id) "
                    "as erasure_pending,"
                    "exists(select 1 from platform_hr.position_conversations scope "
                    "where scope.conversation_id=conversation.conversation_id) "
                    "as position_bound,"
                    "exists(select 1 from platform_attachments.bindings binding "
                    "where binding.owner_internal_user_id=attempt.owner_internal_user_id "
                    "and binding.conversation_id=conversation.conversation_id "
                    "and binding.turn_id=turn.turn_id and binding.kind='turn_input' "
                    "and binding.attachment_id<>attempt.attachment_id) as wrong_input "
                    "from platform_control.conversations conversation "
                    "join platform_control.conversation_turns turn on "
                    "turn.conversation_id=conversation.conversation_id "
                    "join platform_hr.candidate_draft_processing_attempts attempt on "
                    "attempt.owner_internal_user_id="
                    "conversation.owner_internal_user_id and "
                    "attempt.attempt_id=turn.client_request_id and "
                    "attempt.attempt_id=conversation.started_by_client_request_id "
                    "join platform_hr.candidate_drafts draft on "
                    "draft.draft_id=attempt.draft_id and "
                    "draft.owner_internal_user_id=attempt.owner_internal_user_id and "
                    "draft.client_request_id=attempt.draft_client_request_id "
                    "join platform_attachments.attachments attachment on "
                    "attachment.attachment_id=attempt.attachment_id and "
                    "attachment.owner_internal_user_id="
                    "attempt.owner_internal_user_id "
                    "where conversation.owner_internal_user_id=%s and "
                    "conversation.conversation_id=%s and turn.turn_id=%s and "
                    "conversation.mode='direct_agent' and "
                    "conversation.direct_agent_id='hr-bot' and "
                    "attempt.state='processing' and "
                    "attempt.lease_expires_at>now() "
                    "order by attempt.claimed_at desc,attempt.attempt_id limit 2",
                    (owner_id, conversation_id, turn_id),
                ).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise CandidateConflict("candidate parser input is ambiguous")
            row = rows[0]
            if (
                row["state"] != "processing"
                or row["lease_expires_at"] <= datetime.now().astimezone()
                or row["attachment_state"] != "ready"
                or row["retained_until"] <= datetime.now().astimezone()
                or row["immutable_locator"] is None
                or row["erasure_pending"]
                or row["position_bound"]
                or row["wrong_input"]
            ):
                raise CandidateConflict("candidate parser input unavailable")
            return row["attachment_id"]
        except (CandidateConflict, CandidateUnavailable):
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise CandidateUnavailable("candidate parser input unavailable") from None

    def fail_submission_collision(
        self, submission: CandidateParserSubmission
    ) -> None:
        if (
            not isinstance(submission, CandidateParserSubmission)
            or not submission.request_collision
        ):
            raise ValueError("candidate parser collision required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.fail_candidate_parser_submission_collision_v70("
                    "%s,%s)).*",
                    (submission.owner_id, submission.attempt_id),
                ).fetchone()
            if row is None or row["draft_id"] != submission.draft_id:
                raise CandidateUnavailable("candidate parser collision unavailable")
        except CandidateUnavailable:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise CandidateUnavailable("candidate parser collision unavailable") from None


class CandidateParserSubmissionCoordinator:
    def __init__(self, repository: object, commands: object) -> None:
        if not callable(getattr(repository, "next_submission", None)):
            raise ValueError("candidate parser submission repository required")
        if not callable(getattr(commands, "start", None)):
            raise ValueError("conversation command service required")
        self._repository = repository
        self._commands = commands

    def submit_one(self) -> bool:
        selected = self._repository.next_submission()
        if selected is None:
            return False
        if not isinstance(selected, CandidateParserSubmission):
            raise CandidateUnavailable("candidate parser submission unavailable")
        if selected.request_collision:
            fail_collision = getattr(
                self._repository, "fail_submission_collision", None
            )
            if not callable(fail_collision):
                raise CandidateUnavailable("candidate parser collision unavailable")
            fail_collision(selected)
            return True
        self._commands.start(
            selected.owner_id,
            selected.client_request_id,
            _PARSER_PROMPT,
            mode="direct_agent",
            direct_agent_id="hr-bot",
        )
        return True


class CandidateParserInputProvider:
    def __init__(self, repository: object) -> None:
        if not callable(getattr(repository, "candidate_parser_input_for_turn", None)):
            raise ValueError("candidate parser input repository required")
        self._repository = repository

    def for_turn(
        self, owner_id: UUID, conversation_id: UUID, turn_id: UUID
    ) -> UUID | None:
        try:
            selected = self._repository.candidate_parser_input_for_turn(
                owner_id, conversation_id, turn_id
            )
        except (CandidateConflict, CandidateUnavailable):
            raise ValueError("candidate parser input invalid") from None
        if selected is not None and not isinstance(selected, UUID):
            raise ValueError("candidate parser input invalid")
        return selected


class PostgresCandidateParserResultReader:
    def __init__(
        self,
        database_url: str,
        content_codec: ContentCodec,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("candidate parser database URL required")
        if not isinstance(content_codec, ContentCodec):
            raise ValueError("candidate parser content codec required")
        self._database_url = database_url
        self._content_codec = content_codec
        self._connect = connect

    def read(self, attempt_id: UUID, worker_id: str) -> CandidateParserExecutionResult:
        try:
            with self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
                row_factory=dict_row,
            ) as connection:
                row = connection.execute(
                    "select * from "
                    "platform_hr.read_candidate_draft_execution_result_v70(%s,%s)",
                    (attempt_id, worker_id),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("candidate parser result not found")
            content = None
            if row["content_ciphertext"] is not None:
                value = self._content_codec.unseal_json(
                    message_subject(row["conversation_id"], row["assistant_message_id"]),
                    SealedContent(
                        bytes(row["content_ciphertext"]),
                        row["encryption_key_version"],
                    ),
                )
                if set(value) != {"text"} or not isinstance(value["text"], str):
                    raise CandidateParserProtocolError()
                content = value["text"]
            return CandidateParserExecutionResult(
                row["execution_status"], row["turn_status"], content
            )
        except (CandidateNotFound, CandidateParserProtocolError):
            raise
        except ContentCryptoError:
            raise CandidateParserProtocolError(
                "candidate parser response invalid"
            ) from None
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise CandidateUnavailable("candidate parser result unavailable") from None


class CandidateParserRuntime:
    def __init__(
        self,
        queue: object,
        result_reader: object,
        *,
        worker_id: str,
        lease_seconds: int = 900,
    ) -> None:
        required_queue = (
            "recover_next", "claim_next", "discover_execution",
            "attach_execution", "complete", "fail",
        )
        if any(not callable(getattr(queue, name, None)) for name in required_queue):
            raise ValueError("candidate parser queue required")
        if not callable(getattr(result_reader, "read", None)):
            raise ValueError("candidate parser result reader required")
        if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 64:
            raise ValueError("candidate parser worker invalid")
        if not isinstance(lease_seconds, int) or not 30 <= lease_seconds <= 900:
            raise ValueError("candidate parser lease invalid")
        self._queue = queue
        self._result_reader = result_reader
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds

    @staticmethod
    def terminal_request_id(attempt_id: UUID) -> UUID:
        if not isinstance(attempt_id, UUID):
            raise ValueError("candidate parser attempt required")
        return uuid5(_TERMINAL_NAMESPACE, str(attempt_id))

    def _fail(
        self, attempt: CandidateDraftProcessingAttempt, error_code: str
    ) -> None:
        self._queue.fail(
            attempt.attempt_id,
            self._worker_id,
            FailCandidateDraft(
                attempt.owner_id,
                attempt.draft_id,
                self.terminal_request_id(attempt.attempt_id),
                attempt.claimed_row_version,
                error_code,
            ),
        )

    def tick(self) -> bool:
        claimed = False
        try:
            attempt = self._queue.recover_next(self._worker_id)
        except CandidateNotFound:
            try:
                attempt = self._queue.claim_next(ClaimNextCandidateDraft(
                    uuid4(), self._worker_id, self._lease_seconds
                ))
                claimed = True
            except CandidateNotFound:
                return False
        if attempt.execution_job_id is not None:
            identity = AttachCandidateDraftExecution(
                attempt.attempt_id,
                self._worker_id,
                attempt.execution_job_id,
                attempt.conversation_id,
                attempt.turn_id,
            )
        else:
            try:
                identity = self._queue.discover_execution(
                    attempt.attempt_id, self._worker_id
                )
            except CandidateNotFound:
                return claimed
        self._queue.attach_execution(identity)
        try:
            result = self._result_reader.read(attempt.attempt_id, self._worker_id)
            if not isinstance(result, CandidateParserExecutionResult):
                raise CandidateParserProtocolError()
            if (
                result.execution_status == "completed"
                and result.turn_status == "completed"
            ):
                if result.assistant_content is None:
                    raise CandidateParserProtocolError()
                decoded = decode_candidate_parser_response(result.assistant_content)
                self._queue.complete(
                    attempt.attempt_id,
                    self._worker_id,
                    CompleteCandidateDraft(
                        attempt.owner_id,
                        attempt.draft_id,
                        self.terminal_request_id(attempt.attempt_id),
                        attempt.claimed_row_version,
                        decoded.extracted_facts,
                        decoded.identity_candidate_ids,
                    ),
                )
                return True
        except CandidateParserProtocolError:
            self._fail(attempt, "parser_response_invalid")
            return True
        self._fail(attempt, "execution_failed")
        return True


async def candidate_parser_submission_loop(
    coordinator: CandidateParserSubmissionCoordinator,
    *,
    idle_seconds: float = 0.5,
) -> None:
    if not isinstance(coordinator, CandidateParserSubmissionCoordinator):
        raise ValueError("candidate parser submission coordinator required")
    if not isinstance(idle_seconds, (int, float)) or idle_seconds <= 0:
        raise ValueError("candidate parser submission interval invalid")
    while True:
        try:
            changed = await asyncio.to_thread(coordinator.submit_one)
        except Exception:
            logger.exception("candidate parser submission pass failed")
            await asyncio.sleep(idle_seconds)
            continue
        if not changed:
            await asyncio.sleep(idle_seconds)
