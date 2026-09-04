from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .candidate_models import (
    AppendHumanFeedback,
    AttachCandidateDraftExecution,
    Candidate,
    CandidateAnalysisVersion,
    CandidateDocument,
    CandidateDraft,
    CandidateDraftProcessingAttempt,
    ClaimNextCandidateDraft,
    ComparePositionCandidates,
    CompleteCandidateDraft,
    ConfirmCandidateDraft,
    ConfirmedCandidate,
    CreateCandidateAnalysis,
    CreateCandidateDraftBatch,
    FailCandidateDraft,
    HumanFeedback,
    PositionCandidate,
    RetryCandidateDraft,
)


class CandidateRepositoryError(RuntimeError):
    pass


class CandidateNotFound(CandidateRepositoryError):
    pass


class CandidateConflict(CandidateRepositoryError):
    pass


class CandidateUnavailable(CandidateRepositoryError):
    pass


def _draft(row: dict[str, Any]) -> CandidateDraft:
    identities = row["identity_candidates"]
    if not isinstance(identities, (list, tuple)):
        raise ValueError("candidate identity projection invalid")
    return CandidateDraft(
        draft_id=row["draft_id"],
        owner_id=row["owner_internal_user_id"],
        position_id=row["position_id"],
        attachment_id=row["attachment_id"],
        batch_request_id=row["batch_request_id"],
        client_request_id=row["client_request_id"],
        state=row["state"],
        extracted_facts=row["extracted_facts"],
        identity_candidates=tuple(identities),
        error_code=row["error_code"],
        row_version=row["row_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _candidate(row: dict[str, Any]) -> Candidate:
    return Candidate(
        candidate_id=row["candidate_id"],
        owner_id=row["owner_internal_user_id"],
        stable_name=row["stable_name"],
        facts=row["facts"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _processing_attempt(row: dict[str, Any]) -> CandidateDraftProcessingAttempt:
    return CandidateDraftProcessingAttempt(
        attempt_id=row["attempt_id"],
        owner_id=row["owner_internal_user_id"],
        draft_id=row["draft_id"],
        position_id=row["position_id"],
        attachment_id=row["attachment_id"],
        draft_client_request_id=row["draft_client_request_id"],
        worker_id=row["worker_id"],
        execution_job_id=row["execution_job_id"],
        conversation_id=row["conversation_id"],
        turn_id=row["turn_id"],
        state=row["state"],
        starting_row_version=row["starting_row_version"],
        claimed_row_version=row["claimed_row_version"],
        claimed_at=row["claimed_at"],
        lease_expires_at=row["lease_expires_at"],
        execution_attached_at=row["execution_attached_at"],
        finished_at=row["finished_at"],
        terminal_request_id=row["terminal_request_id"],
    )


def _document(row: dict[str, Any]) -> CandidateDocument:
    return CandidateDocument(
        document_id=row["document_id"],
        owner_id=row["owner_internal_user_id"],
        candidate_id=row["candidate_id"],
        attachment_id=row["attachment_id"],
        source_draft_id=row["source_draft_id"],
        document_kind=row["document_kind"],
        version_number=row["version_number"],
        content_sha256=row["content_sha256"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _position_candidate(row: dict[str, Any]) -> PositionCandidate:
    return PositionCandidate(
        position_candidate_id=row["position_candidate_id"],
        owner_id=row["owner_internal_user_id"],
        position_id=row["position_id"],
        candidate_id=row["candidate_id"],
        context_version_id=row["context_version_id"],
        source_draft_id=row["source_draft_id"],
        status=row["status"],
        row_version=row["row_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _analysis(row: dict[str, Any]) -> CandidateAnalysisVersion:
    return CandidateAnalysisVersion(
        analysis_version_id=row["analysis_version_id"],
        owner_id=row["owner_internal_user_id"],
        position_candidate_id=row["position_candidate_id"],
        position_id=row["position_id"],
        candidate_id=row["candidate_id"],
        context_version_id=row["context_version_id"],
        version_number=row["version_number"],
        analysis_kind=row["analysis_kind"],
        document_ids=tuple(row["document_ids"]),
        feedback_ids=tuple(row["feedback_ids"]),
        result=row["result"],
        evidence=tuple(row["evidence"]),
        unknowns=tuple(row["unknowns"]),
        conflicts=tuple(row["conflicts"]),
        verification_questions=tuple(row["verification_questions"]),
        agent_version=row["agent_version"],
        model_version=row["model_version"],
        created_at=row["created_at"],
    )


def _feedback(row: dict[str, Any]) -> HumanFeedback:
    return HumanFeedback(
        feedback_id=row["feedback_id"],
        owner_id=row["owner_internal_user_id"],
        position_candidate_id=row["position_candidate_id"],
        analysis_version_id=row["analysis_version_id"],
        feedback_kind=row["feedback_kind"],
        conclusion_key=row["conclusion_key"],
        correction=row["correction"],
        reason=row["reason"],
        created_at=row["created_at"],
    )


_ANALYSIS_SELECT = """
select analysis.*,
  coalesce((
    select array_agg(link.document_id order by link.document_id)
    from platform_hr.candidate_analysis_documents link
    where link.analysis_version_id=analysis.analysis_version_id
  ),'{}'::uuid[]) as document_ids,
  coalesce((
    select array_agg(link.feedback_id order by link.feedback_id)
    from platform_hr.candidate_analysis_feedback link
    where link.analysis_version_id=analysis.analysis_version_id
  ),'{}'::uuid[]) as feedback_ids
from platform_hr.candidate_analysis_versions analysis
"""


class CandidateRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("candidate database URL required")
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    @staticmethod
    def _raise_repository_error(error: Exception) -> None:
        if isinstance(error, psycopg.errors.NoDataFound):
            raise CandidateNotFound("candidate resource not found") from None
        if isinstance(
            error,
            (
                psycopg.errors.UniqueViolation,
                psycopg.errors.SerializationFailure,
                psycopg.errors.CheckViolation,
            ),
        ):
            raise CandidateConflict("candidate mutation conflict") from None
        raise CandidateUnavailable("candidate repository unavailable") from None

    def create_draft(
        self,
        draft_id: UUID,
        request_id: UUID,
        command: CreateCandidateDraftBatch,
        attachment_id: UUID,
    ) -> CandidateDraft:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_candidate_draft_v70("
                    "%s,%s,%s,%s,%s,%s)).*",
                    (
                        draft_id, command.owner_id, command.position_id,
                        attachment_id, command.client_request_id, request_id,
                    ),
                ).fetchone()
            if row is None:
                raise CandidateUnavailable("candidate draft unavailable")
            return _draft(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def register_batch(self, command: CreateCandidateDraftBatch) -> None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.register_candidate_draft_batch_v70("
                    "%s,%s,%s,%s)).*",
                    (
                        command.owner_id, command.position_id,
                        command.client_request_id, list(command.attachment_ids),
                    ),
                ).fetchone()
            if row is None:
                raise CandidateUnavailable("candidate batch unavailable")
            if (
                row["owner_internal_user_id"] != command.owner_id
                or row["position_id"] != command.position_id
                or row["batch_request_id"] != command.client_request_id
                or tuple(row["attachment_ids"]) != command.attachment_ids
            ):
                raise CandidateConflict("candidate batch replay mismatch")
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def claim_next_draft(
        self, command: ClaimNextCandidateDraft
    ) -> CandidateDraftProcessingAttempt:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.claim_next_candidate_draft_v70("
                    "%s,%s,%s)).*",
                    (command.attempt_id, command.worker_id, command.lease_seconds),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("candidate draft claim unavailable")
            return _processing_attempt(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def attach_draft_execution(
        self, command: AttachCandidateDraftExecution
    ) -> CandidateDraftProcessingAttempt:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.attach_candidate_draft_execution_v70("
                    "%s,%s,%s,%s,%s)).*",
                    (
                        command.attempt_id, command.worker_id,
                        command.execution_job_id, command.conversation_id,
                        command.turn_id,
                    ),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("candidate processing execution unavailable")
            return _processing_attempt(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def recover_draft_attempt(
        self, attempt_id: UUID, worker_id: str
    ) -> CandidateDraftProcessingAttempt:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.recover_candidate_draft_attempt_v70("
                    "%s,%s)).*", (attempt_id, worker_id),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("candidate processing attempt not found")
            return _processing_attempt(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def recover_next_draft_attempt(
        self, worker_id: str
    ) -> CandidateDraftProcessingAttempt:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.recover_next_candidate_draft_attempt_v70("
                    "%s)).*", (worker_id,),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("candidate processing attempt not found")
            return _processing_attempt(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def discover_draft_execution(
        self, attempt_id: UUID, worker_id: str
    ) -> AttachCandidateDraftExecution:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.discover_candidate_draft_execution_v70("
                    "%s,%s)", (attempt_id, worker_id),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("candidate processing execution not found")
            return AttachCandidateDraftExecution(
                attempt_id, worker_id, row["execution_job_id"],
                row["conversation_id"], row["turn_id"],
            )
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def complete_claimed_draft(
        self, attempt_id: UUID, worker_id: str, command: CompleteCandidateDraft
    ) -> CandidateDraft:
        return self._claimed_draft_transition(
            "complete_claimed_candidate_draft_v70", attempt_id,
            command.owner_id, command.draft_id, worker_id,
            command.client_request_id, command.expected_row_version,
            json.dumps(command.extracted_facts, ensure_ascii=False),
            list(command.identity_candidates),
        )

    def fail_claimed_draft(
        self, attempt_id: UUID, worker_id: str, command: FailCandidateDraft
    ) -> CandidateDraft:
        return self._claimed_draft_transition(
            "fail_claimed_candidate_draft_v70", attempt_id,
            command.owner_id, command.draft_id, worker_id,
            command.client_request_id, command.expected_row_version,
            command.error_code,
        )

    def _claimed_draft_transition(
        self, function_name: str, attempt_id: UUID, owner_id: UUID,
        draft_id: UUID, worker_id: str, request_id: UUID,
        expected_row_version: int, *extra: object,
    ) -> CandidateDraft:
        placeholders = ",".join("%s" for _ in range(6 + len(extra)))
        try:
            with self._connection() as connection:
                row = connection.execute(
                    f"select (platform_hr.{function_name}({placeholders})).*",
                    (
                        attempt_id, owner_id, draft_id, worker_id, request_id,
                        expected_row_version, *extra,
                    ),
                ).fetchone()
            if row is None:
                raise CandidateUnavailable("candidate draft mutation unavailable")
            return _draft(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def draft_for_owner(self, owner_id: UUID, draft_id: UUID) -> CandidateDraft:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.candidate_drafts "
                    "where owner_internal_user_id=%s and draft_id=%s",
                    (owner_id, draft_id),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("candidate draft not found")
            return _draft(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def list_drafts(
        self,
        owner_id: UUID,
        position_id: UUID,
        *,
        batch_request_id: UUID | None = None,
    ) -> tuple[CandidateDraft, ...]:
        query = (
            "select * from platform_hr.candidate_drafts "
            "where owner_internal_user_id=%s and position_id=%s"
        )
        parameters: tuple[object, ...] = (owner_id, position_id)
        if batch_request_id is not None:
            query += " and batch_request_id=%s"
            parameters += (batch_request_id,)
        query += " order by created_at,draft_id"
        try:
            with self._connection() as connection:
                rows = connection.execute(query, parameters).fetchall()
            return tuple(_draft(row) for row in rows)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def _draft_transition(
        self,
        function_name: str,
        owner_id: UUID,
        draft_id: UUID,
        request_id: UUID,
        expected_row_version: int,
        *extra: object,
    ) -> CandidateDraft:
        placeholders = ",".join("%s" for _ in range(4 + len(extra)))
        try:
            with self._connection() as connection:
                row = connection.execute(
                    f"select (platform_hr.{function_name}({placeholders})).*",
                    (
                        owner_id, draft_id, request_id,
                        expected_row_version, *extra,
                    ),
                ).fetchone()
            if row is None:
                raise CandidateUnavailable("candidate draft mutation unavailable")
            return _draft(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def retry_draft(self, command: RetryCandidateDraft) -> CandidateDraft:
        return self._draft_transition(
            "retry_candidate_draft_v70", command.owner_id, command.draft_id,
            command.client_request_id, command.expected_row_version,
        )

    def dismiss_draft(self, command: RetryCandidateDraft) -> CandidateDraft:
        return self._draft_transition(
            "dismiss_candidate_draft_v70", command.owner_id, command.draft_id,
            command.client_request_id, command.expected_row_version,
        )

    def confirm_draft(
        self,
        command: ConfirmCandidateDraft,
        *,
        document_id: UUID,
        position_candidate_id: UUID,
        context_version_id: UUID,
    ) -> ConfirmedCandidate:
        try:
            with self._connection() as connection:
                relation_row = connection.execute(
                    "select (platform_hr.confirm_candidate_draft_v70("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)).*",
                    (
                        command.owner_id, command.draft_id,
                        command.client_request_id, command.expected_row_version,
                        command.candidate_id, command.merge_candidate_id,
                        document_id, position_candidate_id, context_version_id,
                        command.stable_name,
                        json.dumps(command.confirmed_facts, ensure_ascii=False),
                    ),
                ).fetchone()
                if relation_row is None:
                    raise CandidateUnavailable("candidate confirmation unavailable")
                candidate_row = connection.execute(
                    "select * from platform_hr.candidates "
                    "where owner_internal_user_id=%s and candidate_id=%s",
                    (command.owner_id, relation_row["candidate_id"]),
                ).fetchone()
                document_row = connection.execute(
                    "select * from platform_hr.candidate_documents "
                    "where owner_internal_user_id=%s and candidate_id=%s "
                    "and source_draft_id=%s order by version_number desc limit 1",
                    (
                        command.owner_id, relation_row["candidate_id"],
                        command.draft_id,
                    ),
                ).fetchone()
            if candidate_row is None or document_row is None:
                raise CandidateUnavailable("candidate confirmation projection unavailable")
            return ConfirmedCandidate(
                _candidate(candidate_row), _document(document_row),
                _position_candidate(relation_row),
            )
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def candidate_for_owner(self, owner_id: UUID, candidate_id: UUID) -> Candidate:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.candidates "
                    "where owner_internal_user_id=%s and candidate_id=%s",
                    (owner_id, candidate_id),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("candidate not found")
            return _candidate(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def documents_for_candidate(
        self, owner_id: UUID, candidate_id: UUID
    ) -> tuple[CandidateDocument, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select document.*,(case when document.status='erased' "
                    "or attachment.deleted_at is not null or attachment.state='deleted' "
                    "or exists (select 1 from platform_attachments.erasure_jobs erasure "
                    "where erasure.attachment_id=document.attachment_id) "
                    "then 'erased' else 'active' end) as status "
                    "from platform_hr.candidate_documents document "
                    "join platform_attachments.attachments attachment on "
                    "attachment.attachment_id=document.attachment_id and "
                    "attachment.owner_internal_user_id=document.owner_internal_user_id "
                    "where document.owner_internal_user_id=%s and document.candidate_id=%s "
                    "order by version_number,document_id",
                    (owner_id, candidate_id),
                ).fetchall()
            return tuple(_document(row) for row in rows)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def document_for_owner(
        self, owner_id: UUID, document_id: UUID
    ) -> CandidateDocument:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select document.*,(case when document.status='erased' "
                    "or attachment.deleted_at is not null or attachment.state='deleted' "
                    "or exists (select 1 from platform_attachments.erasure_jobs erasure "
                    "where erasure.attachment_id=document.attachment_id) "
                    "then 'erased' else 'active' end) as status "
                    "from platform_hr.candidate_documents document "
                    "join platform_attachments.attachments attachment on "
                    "attachment.attachment_id=document.attachment_id and "
                    "attachment.owner_internal_user_id=document.owner_internal_user_id "
                    "where document.owner_internal_user_id=%s and document.document_id=%s",
                    (owner_id, document_id),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("candidate document not found")
            return _document(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def attachment_state_for_document(
        self, owner_id: UUID, document_id: UUID
    ) -> str:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select document.status as document_status,"
                    "attachment.state as attachment_state,"
                    "attachment.deleted_at is not null as deleted,"
                    "attachment.retained_until>now() as retained,"
                    "exists (select 1 from platform_attachments.erasure_jobs erasure "
                    "where erasure.attachment_id=document.attachment_id) as erasing "
                    "from platform_hr.candidate_documents document "
                    "join platform_attachments.attachments attachment "
                    "on attachment.attachment_id=document.attachment_id "
                    "and attachment.owner_internal_user_id="
                    "document.owner_internal_user_id "
                    "where document.owner_internal_user_id=%s "
                    "and document.document_id=%s",
                    (owner_id, document_id),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("candidate document not found")
            if row["document_status"] == "erased" or row["deleted"] or row["erasing"]:
                return "erased"
            if not row["retained"]:
                return "expired"
            return row["attachment_state"]
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def position_candidate_for_owner(
        self, owner_id: UUID, position_candidate_id: UUID
    ) -> PositionCandidate:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.position_candidates "
                    "where owner_internal_user_id=%s and position_candidate_id=%s",
                    (owner_id, position_candidate_id),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("position candidate not found")
            return _position_candidate(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def list_position_candidates(
        self, owner_id: UUID, position_id: UUID, *, include_archived: bool = False
    ) -> tuple[PositionCandidate, ...]:
        query = (
            "select * from platform_hr.position_candidates "
            "where owner_internal_user_id=%s and position_id=%s"
        )
        if not include_archived:
            query += " and status='active'"
        query += " order by created_at,position_candidate_id"
        try:
            with self._connection() as connection:
                rows = connection.execute(query, (owner_id, position_id)).fetchall()
            return tuple(_position_candidate(row) for row in rows)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def add_analysis(
        self,
        command: CreateCandidateAnalysis,
        *,
        analysis_version_id: UUID,
        feedback_ids: tuple[UUID, ...],
    ) -> CandidateAnalysisVersion:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_candidate_analysis_v70("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,"
                    "%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)).*",
                    (
                        analysis_version_id, command.owner_id,
                        command.position_candidate_id, command.context_version_id,
                        command.client_request_id, command.analysis_kind,
                        list(command.document_ids), list(feedback_ids),
                        json.dumps(command.result, ensure_ascii=False),
                        json.dumps(command.evidence, ensure_ascii=False),
                        json.dumps(command.unknowns, ensure_ascii=False),
                        json.dumps(command.conflicts, ensure_ascii=False),
                        json.dumps(command.verification_questions, ensure_ascii=False),
                        command.agent_version, command.model_version,
                    ),
                ).fetchone()
                if row is None:
                    raise CandidateUnavailable("candidate analysis unavailable")
                projected = connection.execute(
                    _ANALYSIS_SELECT
                    + " where analysis.owner_internal_user_id=%s "
                    "and analysis.analysis_version_id=%s",
                    (command.owner_id, row["analysis_version_id"]),
                ).fetchone()
            if projected is None:
                raise CandidateUnavailable("candidate analysis projection unavailable")
            return _analysis(projected)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def analysis_for_request(
        self, command: CreateCandidateAnalysis | ComparePositionCandidates
    ) -> CandidateAnalysisVersion | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    _ANALYSIS_SELECT
                    + " where analysis.owner_internal_user_id=%s "
                    "and analysis.client_request_id=%s",
                    (command.owner_id, command.client_request_id),
                ).fetchone()
            if row is None:
                return None
            selected = _analysis(row)
            if isinstance(command, CreateCandidateAnalysis):
                matches = (
                    selected.position_candidate_id == command.position_candidate_id
                    and selected.context_version_id == command.context_version_id
                    and selected.analysis_kind == command.analysis_kind
                    and tuple(sorted(selected.document_ids))
                    == tuple(sorted(command.document_ids))
                    and selected.result == command.result
                    and selected.evidence == command.evidence
                    and selected.unknowns == command.unknowns
                    and selected.conflicts == command.conflicts
                    and selected.verification_questions
                    == command.verification_questions
                    and selected.agent_version == command.agent_version
                    and selected.model_version == command.model_version
                    and tuple(sorted(selected.feedback_ids))
                    == tuple(sorted(command.feedback_ids))
                )
            else:
                candidates = selected.result.get("candidates")
                selected_ids = (
                    tuple(item.get("position_candidate_id") for item in candidates)
                    if isinstance(candidates, list)
                    and all(isinstance(item, dict) for item in candidates)
                    else ()
                )
                matches = (
                    selected.position_id == command.position_id
                    and selected.context_version_id == command.context_version_id
                    and selected.analysis_kind == "comparison"
                    and selected_ids
                    == tuple(str(value) for value in command.position_candidate_ids)
                    and selected.agent_version == command.agent_version
                    and selected.model_version == command.model_version
                )
            if not matches:
                raise CandidateConflict("candidate analysis replay mismatch")
            return selected
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def list_analyses(
        self, owner_id: UUID, position_candidate_id: UUID
    ) -> tuple[CandidateAnalysisVersion, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    _ANALYSIS_SELECT
                    + " where analysis.owner_internal_user_id=%s "
                    "and analysis.position_candidate_id=%s "
                    "order by analysis.version_number,analysis.analysis_version_id",
                    (owner_id, position_candidate_id),
                ).fetchall()
            return tuple(_analysis(row) for row in rows)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def latest_analysis(
        self, owner_id: UUID, position_candidate_id: UUID,
        context_version_id: UUID, *, kind: str,
    ) -> CandidateAnalysisVersion:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    _ANALYSIS_SELECT
                    + " where analysis.owner_internal_user_id=%s "
                    "and analysis.position_candidate_id=%s "
                    "and analysis.context_version_id=%s "
                    "and analysis.analysis_kind=%s "
                    "order by analysis.version_number desc limit 1",
                    (owner_id, position_candidate_id, context_version_id, kind),
                ).fetchone()
            if row is None:
                raise CandidateNotFound("candidate analysis not found")
            return _analysis(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def append_feedback(
        self, command: AppendHumanFeedback, *, feedback_id: UUID
    ) -> HumanFeedback:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.append_human_feedback_v70("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s)).*",
                    (
                        feedback_id, command.owner_id,
                        command.position_candidate_id,
                        command.analysis_version_id, command.client_request_id,
                        command.feedback_kind, command.conclusion_key,
                        command.correction, command.reason,
                    ),
                ).fetchone()
            if row is None:
                raise CandidateUnavailable("candidate feedback unavailable")
            return _feedback(row)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def feedback_for_position_candidate(
        self, owner_id: UUID, position_candidate_id: UUID
    ) -> tuple[HumanFeedback, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.human_feedback "
                    "where owner_internal_user_id=%s and position_candidate_id=%s "
                    "order by created_at desc,feedback_id desc limit 100",
                    (owner_id, position_candidate_id),
                ).fetchall()
            return tuple(_feedback(row) for row in rows)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)

    def feedback_for_candidate_context(
        self, owner_id: UUID, position_candidate_id: UUID,
        context_version_id: UUID,
    ) -> tuple[HumanFeedback, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select feedback.* from platform_hr.human_feedback feedback "
                    "join platform_hr.candidate_analysis_versions analysis on "
                    "analysis.analysis_version_id=feedback.analysis_version_id and "
                    "analysis.owner_internal_user_id=feedback.owner_internal_user_id "
                    "where feedback.owner_internal_user_id=%s and "
                    "feedback.position_candidate_id=%s and "
                    "analysis.context_version_id=%s "
                    "order by feedback.created_at desc,feedback.feedback_id desc limit 100",
                    (owner_id, position_candidate_id, context_version_id),
                ).fetchall()
            return tuple(_feedback(row) for row in rows)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)
