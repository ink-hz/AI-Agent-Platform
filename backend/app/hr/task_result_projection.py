from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.agent_brain.conversation_repository import message_subject
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)

from .candidate_models import CreateCandidateAnalysis
from .candidate_repository import (
    CandidateConflict,
    CandidateNotFound,
    CandidateUnavailable,
)
from .candidate_service import CandidateScopeViolation
from .position_intelligence_repository import (
    PositionContextConflict,
    PositionContextNotFound,
    PositionIntelligenceUnavailable,
)

logger = logging.getLogger(__name__)

_WORKER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_POSITION_MODULES = {
    "jd": "jd",
    "jr": "jr",
    "talent_profile": "talent_profile",
    "sourcing_strategy": "sourcing_strategy",
    "position_interview_plan": "interview_standard",
}
_CANDIDATE_ANALYSES = {
    "candidate_match": "match",
    "candidate_interview_plan": "candidate_interview_plan",
}


class HrTaskResultProjectionError(RuntimeError):
    pass


class HrTaskResultProjectionUnavailable(HrTaskResultProjectionError):
    pass


@dataclass(frozen=True, slots=True)
class ClaimedHrTaskResult:
    task_record_id: UUID
    task_request_id: UUID
    projection_request_id: UUID
    owner_id: UUID
    position_id: UUID
    task_kind: str
    official_version_id: UUID | None
    context_version_id: UUID | None
    material_attachment_ids: tuple[UUID, ...]
    candidate_id: UUID | None
    position_candidate_id: UUID | None
    document_ids: tuple[UUID, ...]
    feedback_ids: tuple[UUID, ...]
    conversation_id: UUID
    turn_id: UUID
    output_artifact_version_id: UUID | None
    assistant_message_id: UUID
    agent_id: str
    content_ciphertext: bytes
    encryption_key_version: int

    def __post_init__(self) -> None:
        required = (
            self.task_record_id,
            self.task_request_id,
            self.projection_request_id,
            self.owner_id,
            self.position_id,
            self.conversation_id,
            self.turn_id,
            self.assistant_message_id,
        )
        optional = (
            self.official_version_id,
            self.context_version_id,
            self.candidate_id,
            self.position_candidate_id,
            self.output_artifact_version_id,
        )
        collections = (
            self.material_attachment_ids,
            self.document_ids,
            self.feedback_ids,
        )
        if (
            any(not isinstance(value, UUID) for value in required)
            or any(
                value is not None and not isinstance(value, UUID) for value in optional
            )
            or any(
                not isinstance(values, tuple)
                or len(values) != len(set(values))
                or any(not isinstance(value, UUID) for value in values)
                for values in collections
            )
            or self.task_kind not in {*_POSITION_MODULES, *_CANDIDATE_ANALYSES}
            or self.agent_id != "hr-bot"
            or not isinstance(self.content_ciphertext, bytes)
            or not self.content_ciphertext
            or isinstance(self.encryption_key_version, bool)
            or not isinstance(self.encryption_key_version, int)
            or self.encryption_key_version < 1
        ):
            raise ValueError("HR task result claim invalid")
        is_candidate = self.task_kind in _CANDIDATE_ANALYSES
        if is_candidate != (
            self.candidate_id is not None
            and self.position_candidate_id is not None
            and self.context_version_id is not None
            and bool(self.document_ids)
        ):
            raise ValueError("HR task result claim invalid")


class HrTaskResultProjectionRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("HR task result database URL required")
        if not callable(connect):
            raise TypeError("HR task result database connection required")
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def claim(self, worker_id: str, lease_seconds: int) -> ClaimedHrTaskResult | None:
        if (
            not isinstance(worker_id, str)
            or _WORKER_ID.fullmatch(worker_id) is None
            or isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 30 <= lease_seconds <= 900
        ):
            raise ValueError("HR task result claim identity invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.claim_hr_task_result_projection_v71("
                    "%s,%s)",
                    (worker_id, lease_seconds),
                ).fetchone()
            if row is None:
                return None
            return ClaimedHrTaskResult(
                task_record_id=row["task_record_id"],
                task_request_id=row["task_request_id"],
                projection_request_id=row["projection_request_id"],
                owner_id=row["owner_internal_user_id"],
                position_id=row["position_id"],
                task_kind=row["task_kind"],
                official_version_id=row["official_position_version_id"],
                context_version_id=row["context_version_id"],
                material_attachment_ids=tuple(row["material_attachment_ids"]),
                candidate_id=row["candidate_id"],
                position_candidate_id=row["position_candidate_id"],
                document_ids=tuple(row["document_ids"]),
                feedback_ids=tuple(row["human_feedback_ids"]),
                conversation_id=row["conversation_id"],
                turn_id=row["turn_id"],
                output_artifact_version_id=row["output_artifact_version_id"],
                assistant_message_id=row["assistant_message_id"],
                agent_id=row["agent_id"],
                content_ciphertext=bytes(row["content_ciphertext"]),
                encryption_key_version=row["encryption_key_version"],
            )
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise HrTaskResultProjectionUnavailable(
                "HR task result claim unavailable"
            ) from None

    def _transition(
        self,
        function_name: str,
        claim: ClaimedHrTaskResult,
        worker_id: str,
        value: UUID | str,
    ) -> None:
        if not isinstance(claim, ClaimedHrTaskResult):
            raise TypeError("HR task result claim required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    f"select platform_hr.{function_name}(%s,%s,%s,%s)",
                    (
                        claim.task_record_id,
                        worker_id,
                        claim.projection_request_id,
                        value,
                    ),
                ).fetchone()
            if row is None or tuple(row.values()) != (True,):
                raise HrTaskResultProjectionUnavailable(
                    "HR task result transition unavailable"
                )
        except HrTaskResultProjectionError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise HrTaskResultProjectionUnavailable(
                "HR task result transition unavailable"
            ) from None

    def complete(
        self, claim: ClaimedHrTaskResult, worker_id: str, resource_id: UUID
    ) -> None:
        if not isinstance(resource_id, UUID):
            raise TypeError("HR task result resource required")
        self._transition(
            "complete_hr_task_result_projection_v71", claim, worker_id, resource_id
        )

    def fail(self, claim: ClaimedHrTaskResult, worker_id: str, error_code: str) -> None:
        self._transition(
            "fail_hr_task_result_projection_v71", claim, worker_id, error_code
        )

    def release(
        self, claim: ClaimedHrTaskResult, worker_id: str, error_code: str
    ) -> None:
        self._transition(
            "release_hr_task_result_projection_v71", claim, worker_id, error_code
        )


class HrTaskResultReconciler:
    def __init__(
        self,
        repository: object,
        position_intelligence: object,
        candidates: object,
        content_codec: ContentCodec,
        *,
        worker_id: str,
        model_version: str,
        lease_seconds: int = 300,
    ) -> None:
        if any(
            not callable(getattr(repository, name, None))
            for name in ("claim", "complete", "fail", "release")
        ):
            raise ValueError("HR task result repository required")
        if not callable(getattr(position_intelligence, "create_draft", None)):
            raise TypeError("position intelligence service required")
        if not callable(getattr(candidates, "add_analysis", None)):
            raise TypeError("candidate service required")
        if not isinstance(content_codec, ContentCodec):
            raise TypeError("HR task result content codec required")
        normalized_model = (
            model_version.strip() if isinstance(model_version, str) else ""
        )
        if (
            not normalized_model
            or len(normalized_model) > 128
            or not isinstance(worker_id, str)
            or _WORKER_ID.fullmatch(worker_id) is None
            or isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 30 <= lease_seconds <= 900
        ):
            raise ValueError("HR task result runtime identity invalid")
        self._repository = repository
        self._positions = position_intelligence
        self._candidates = candidates
        self._content_codec = content_codec
        self._worker_id = worker_id
        self._model_version = normalized_model
        self._lease_seconds = lease_seconds

    def _text(self, claim: ClaimedHrTaskResult) -> str:
        value = self._content_codec.unseal_json(
            message_subject(claim.conversation_id, claim.assistant_message_id),
            SealedContent(
                claim.content_ciphertext,
                claim.encryption_key_version,
            ),
        )
        if set(value) != {"text"} or not isinstance(value["text"], str):
            raise ValueError("HR task result content invalid")
        text = value["text"]
        if not text.strip():
            raise ValueError("HR task result content invalid")
        return text

    def _project(self, claim: ClaimedHrTaskResult, text: str) -> UUID:
        if claim.task_kind in _POSITION_MODULES:
            module = _POSITION_MODULES[claim.task_kind]
            result = self._positions.create_draft(
                owner_id=claim.owner_id,
                position_id=claim.position_id,
                request_id=claim.projection_request_id,
                base_context_version_id=claim.context_version_id,
                official_version_id=claim.official_version_id,
                modules={module: {"text": text}},
                summary=text,
                source_conversation_id=claim.conversation_id,
                source_turn_id=claim.turn_id,
                source_artifact_version_id=claim.output_artifact_version_id,
                source_material_attachment_ids=claim.material_attachment_ids,
                agent_id=claim.agent_id,
                model_version=self._model_version,
                created_by=claim.owner_id,
            )
            resource_id = getattr(result, "context_version_id", None)
        else:
            result = self._candidates.add_analysis(
                CreateCandidateAnalysis(
                    owner_id=claim.owner_id,
                    position_candidate_id=claim.position_candidate_id,
                    context_version_id=claim.context_version_id,
                    document_ids=claim.document_ids,
                    analysis_kind=_CANDIDATE_ANALYSES[claim.task_kind],
                    client_request_id=claim.projection_request_id,
                    result={"text": text},
                    evidence=(),
                    unknowns=(),
                    conflicts=(),
                    verification_questions=(),
                    agent_version=claim.agent_id,
                    model_version=self._model_version,
                    feedback_ids=claim.feedback_ids,
                )
            )
            resource_id = getattr(result, "analysis_version_id", None)
        if not isinstance(resource_id, UUID):
            raise TypeError("HR task result projection invalid")
        return resource_id

    def reconcile_one(self) -> bool:
        claim = self._repository.claim(self._worker_id, self._lease_seconds)
        if claim is None:
            return False
        if not isinstance(claim, ClaimedHrTaskResult):
            raise HrTaskResultProjectionUnavailable("HR task result claim unavailable")
        try:
            text = self._text(claim)
            resource_id = self._project(claim, text)
        except (ContentCryptoError, TypeError, ValueError):
            self._repository.fail(claim, self._worker_id, "result_invalid")
            return True
        except (
            CandidateConflict,
            CandidateNotFound,
            CandidateScopeViolation,
            PositionContextConflict,
            PositionContextNotFound,
        ):
            self._repository.fail(claim, self._worker_id, "projection_scope_invalid")
            return True
        except (CandidateUnavailable, PositionIntelligenceUnavailable):
            self._repository.release(claim, self._worker_id, "projection_unavailable")
            return True
        self._repository.complete(claim, self._worker_id, resource_id)
        return True


async def hr_task_result_projection_loop(
    reconciler: object,
    *,
    idle_seconds: float = 0.5,
) -> None:
    if not callable(getattr(reconciler, "reconcile_one", None)):
        raise TypeError("HR task result reconciler required")
    if (
        isinstance(idle_seconds, bool)
        or not isinstance(idle_seconds, (int, float))
        or idle_seconds <= 0
    ):
        raise ValueError("HR task result projection interval invalid")
    while True:
        try:
            changed = await asyncio.to_thread(reconciler.reconcile_one)
        except Exception:
            logger.exception("HR task result projection pass failed")
            await asyncio.sleep(idle_seconds)
            continue
        if not changed:
            await asyncio.sleep(idle_seconds)
