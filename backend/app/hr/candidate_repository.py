from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .candidate_models import (
    AppendHumanFeedback,
    Candidate,
    CandidateAnalysisVersion,
    CandidateDocument,
    CandidateDraft,
    ConfirmCandidateDraft,
    ConfirmedCandidate,
    CreateCandidateAnalysis,
    CreateCandidateDraftBatch,
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
                    "select (platform_hr.create_candidate_draft_v68("
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

    def start_draft(
        self, owner_id: UUID, draft_id: UUID, request_id: UUID,
        expected_row_version: int,
    ) -> CandidateDraft:
        return self._draft_transition(
            "start_candidate_draft_v68", owner_id, draft_id,
            request_id, expected_row_version,
        )

    def complete_draft(
        self, owner_id: UUID, draft_id: UUID, request_id: UUID,
        expected_row_version: int, extracted_facts: dict[str, object],
        identity_candidates: tuple[UUID, ...] = (),
    ) -> CandidateDraft:
        return self._draft_transition(
            "complete_candidate_draft_v68", owner_id, draft_id,
            request_id, expected_row_version,
            json.dumps(extracted_facts, ensure_ascii=False),
            list(identity_candidates),
        )

    def fail_draft(
        self, owner_id: UUID, draft_id: UUID, request_id: UUID,
        expected_row_version: int, error_code: str,
    ) -> CandidateDraft:
        return self._draft_transition(
            "fail_candidate_draft_v68", owner_id, draft_id,
            request_id, expected_row_version, error_code,
        )

    def retry_draft(self, command: RetryCandidateDraft) -> CandidateDraft:
        return self._draft_transition(
            "retry_candidate_draft_v68", command.owner_id, command.draft_id,
            command.client_request_id, command.expected_row_version,
        )

    def dismiss_draft(self, command: RetryCandidateDraft) -> CandidateDraft:
        return self._draft_transition(
            "dismiss_candidate_draft_v68", command.owner_id, command.draft_id,
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
                    "select (platform_hr.confirm_candidate_draft_v68("
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
                    "select * from platform_hr.candidate_documents "
                    "where owner_internal_user_id=%s and candidate_id=%s "
                    "order by version_number,document_id",
                    (owner_id, candidate_id),
                ).fetchall()
            return tuple(_document(row) for row in rows)
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
                    "select (platform_hr.create_candidate_analysis_v68("
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
                    "select (platform_hr.append_human_feedback_v68("
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
                    "order by created_at,feedback_id",
                    (owner_id, position_candidate_id),
                ).fetchall()
            return tuple(_feedback(row) for row in rows)
        except CandidateRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise_repository_error(error)
