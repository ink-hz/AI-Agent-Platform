from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid5

from app.attachments.download_service import DownloadNotFound, DownloadUnavailable

from .candidate_models import (
    AppendHumanFeedback,
    CandidateAnalysisVersion,
    CandidateDraft,
    ComparePositionCandidates,
    ConfirmCandidateDraft,
    ConfirmedCandidate,
    CreateCandidateAnalysis,
    CreateCandidateDraftBatch,
    HumanFeedback,
    PositionCandidate,
    RetryCandidateDraft,
)
from .candidate_repository import CandidateNotFound, CandidateUnavailable


class CandidateServiceError(RuntimeError):
    pass


class CandidateIdentityConflict(CandidateServiceError):
    pass


class CandidateScopeViolation(CandidateServiceError):
    pass


def _derived(owner_id: UUID, request_id: UUID, label: str) -> UUID:
    return uuid5(owner_id, f"{request_id}:{label}")


class CandidateService:
    def __init__(self, repository, *, document_tickets=None) -> None:
        required = (
            "register_batch",
            "create_draft",
            "draft_for_owner",
            "retry_draft",
            "confirm_draft",
            "position_candidate_for_owner",
            "feedback_for_position_candidate",
            "feedback_for_candidate_context",
            "add_analysis",
            "append_feedback",
            "latest_analysis",
            "analysis_for_request",
            "document_for_owner",
        )
        if any(not callable(getattr(repository, name, None)) for name in required):
            raise ValueError("candidate repository required")
        if document_tickets is not None and not callable(
            getattr(document_tickets, "issue_ticket", None)
        ):
            raise ValueError("candidate document ticket service required")
        self._repository = repository
        self._document_tickets = document_tickets

    def create_drafts(
        self, command: CreateCandidateDraftBatch
    ) -> tuple[CandidateDraft, ...]:
        if not isinstance(command, CreateCandidateDraftBatch):
            raise ValueError("candidate draft batch required")
        self._repository.register_batch(command)
        return tuple(
            self._repository.create_draft(
                _derived(command.owner_id, command.client_request_id, f"draft:{attachment_id}"),
                _derived(command.owner_id, command.client_request_id, f"item:{attachment_id}"),
                command,
                attachment_id,
            )
            for attachment_id in command.attachment_ids
        )

    def draft(self, owner_id: UUID, draft_id: UUID) -> CandidateDraft:
        return self._repository.draft_for_owner(owner_id, draft_id)

    def list_drafts(
        self, owner_id: UUID, position_id: UUID, *, batch_request_id: UUID | None = None
    ) -> tuple[CandidateDraft, ...]:
        return self._repository.list_drafts(
            owner_id, position_id, batch_request_id=batch_request_id
        )

    def list_position_candidates(
        self, owner_id: UUID, position_id: UUID
    ) -> tuple[PositionCandidate, ...]:
        return self._repository.list_position_candidates(owner_id, position_id)

    def candidate(self, owner_id: UUID, candidate_id: UUID):
        return self._repository.candidate_for_owner(owner_id, candidate_id)

    def documents(self, owner_id: UUID, candidate_id: UUID):
        return self._repository.documents_for_candidate(owner_id, candidate_id)

    def candidate_document(self, owner_id: UUID, document_id: UUID):
        return self._repository.document_for_owner(owner_id, document_id)

    def candidate_document_ticket(
        self,
        owner_id: UUID,
        document_id: UUID,
        purpose: Literal["preview", "download"],
    ):
        if not isinstance(owner_id, UUID) or not isinstance(document_id, UUID):
            raise ValueError("candidate document ticket identity invalid")
        if purpose not in {"preview", "download"}:
            raise ValueError("candidate document ticket purpose invalid")
        if self._document_tickets is None:
            raise CandidateUnavailable("candidate document ticket unavailable")
        document = self._repository.document_for_owner(owner_id, document_id)
        if (
            document.owner_id != owner_id
            or document.document_id != document_id
            or document.status != "active"
        ):
            raise CandidateNotFound("candidate document not found")
        try:
            return self._document_tickets.issue_ticket(
                owner_id, document.attachment_id, purpose
            )
        except DownloadNotFound:
            raise CandidateNotFound("candidate document not found") from None
        except DownloadUnavailable:
            raise CandidateUnavailable(
                "candidate document ticket unavailable"
            ) from None

    def position_candidate(
        self, owner_id: UUID, position_candidate_id: UUID
    ) -> PositionCandidate:
        return self._repository.position_candidate_for_owner(
            owner_id, position_candidate_id
        )

    def list_analyses(
        self, owner_id: UUID, position_candidate_id: UUID
    ) -> tuple[CandidateAnalysisVersion, ...]:
        return self._repository.list_analyses(owner_id, position_candidate_id)

    def list_feedback(
        self, owner_id: UUID, position_candidate_id: UUID
    ) -> tuple[HumanFeedback, ...]:
        return self._repository.feedback_for_position_candidate(
            owner_id, position_candidate_id
        )

    def retry_draft(self, command: RetryCandidateDraft) -> CandidateDraft:
        if not isinstance(command, RetryCandidateDraft):
            raise ValueError("candidate retry command required")
        return self._repository.retry_draft(command)

    def dismiss_draft(self, command: RetryCandidateDraft) -> CandidateDraft:
        if not isinstance(command, RetryCandidateDraft):
            raise ValueError("candidate dismiss command required")
        return self._repository.dismiss_draft(command)

    def confirm_draft(
        self, command: ConfirmCandidateDraft, *, context_version_id: UUID
    ) -> ConfirmedCandidate:
        if not isinstance(command, ConfirmCandidateDraft):
            raise ValueError("candidate confirmation command required")
        if not isinstance(context_version_id, UUID):
            raise ValueError("candidate context version required")
        return self._repository.confirm_draft(
            command,
            document_id=_derived(
                command.owner_id, command.client_request_id, "document"
            ),
            position_candidate_id=_derived(
                command.owner_id, command.client_request_id, "position-candidate"
            ),
            context_version_id=context_version_id,
        )

    def add_analysis(
        self, command: CreateCandidateAnalysis
    ) -> CandidateAnalysisVersion:
        if not isinstance(command, CreateCandidateAnalysis):
            raise ValueError("candidate analysis command required")
        replay = self._repository.analysis_for_request(command)
        if replay is not None:
            return replay
        relation = self._repository.position_candidate_for_owner(
            command.owner_id, command.position_candidate_id
        )
        self._require_analysis_scope(command, relation)
        return self._repository.add_analysis(
            command,
            analysis_version_id=_derived(
                command.owner_id, command.client_request_id, "analysis"
            ),
            feedback_ids=command.feedback_ids,
        )

    @staticmethod
    def _require_analysis_scope(
        command: CreateCandidateAnalysis, relation: PositionCandidate
    ) -> None:
        if (
            relation.status != "active"
            or relation.context_version_id != command.context_version_id
        ):
            raise CandidateScopeViolation("candidate analysis scope mismatch")

    def append_feedback(self, command: AppendHumanFeedback) -> HumanFeedback:
        if not isinstance(command, AppendHumanFeedback):
            raise ValueError("candidate feedback command required")
        return self._repository.append_feedback(
            command,
            feedback_id=_derived(
                command.owner_id, command.client_request_id, "human-feedback"
            ),
        )

    def compare(
        self, command: ComparePositionCandidates
    ) -> CandidateAnalysisVersion:
        if not isinstance(command, ComparePositionCandidates):
            raise ValueError("candidate comparison command required")
        replay = self._repository.analysis_for_request(command)
        if replay is not None:
            return replay
        relations = tuple(
            self._repository.position_candidate_for_owner(command.owner_id, value)
            for value in command.position_candidate_ids
        )
        if any(
            relation.position_id != command.position_id
            or relation.context_version_id != command.context_version_id
            or relation.status != "active"
            for relation in relations
        ):
            raise CandidateScopeViolation("candidate comparison scope mismatch")
        analyses = tuple(
            self._repository.latest_analysis(
                command.owner_id,
                relation.position_candidate_id,
                command.context_version_id,
                kind="match",
            )
            for relation in relations
        )
        documents = tuple(
            dict.fromkeys(
                document_id
                for analysis in analyses
                for document_id in analysis.document_ids
            )
        )
        feedback = tuple(
            dict.fromkeys(
                item.feedback_id
                for relation in relations
                for item in self._repository.feedback_for_candidate_context(
                    command.owner_id, relation.position_candidate_id,
                    command.context_version_id,
                )
            )
        )
        unknowns = tuple(
            dict.fromkeys(value for analysis in analyses for value in analysis.unknowns)
        )
        conflicts = tuple(
            dict.fromkeys(value for analysis in analyses for value in analysis.conflicts)
        )
        questions = tuple(
            dict.fromkeys(
                value
                for analysis in analyses
                for value in analysis.verification_questions
            )
        )
        comparison = CreateCandidateAnalysis(
            owner_id=command.owner_id,
            position_candidate_id=relations[0].position_candidate_id,
            context_version_id=command.context_version_id,
            document_ids=documents,
            analysis_kind="comparison",
            client_request_id=command.client_request_id,
            result={
                "candidates": [
                    {
                        "position_candidate_id": str(relation.position_candidate_id),
                        "candidate_id": str(relation.candidate_id),
                        "summary": analysis.result.get("summary"),
                        "evidence_coverage": len(analysis.evidence),
                        "unknown_count": len(analysis.unknowns),
                    }
                    for relation, analysis in zip(relations, analyses, strict=True)
                ],
                "ranking": None,
                "comparison_basis": "same_position_context",
            },
            evidence=tuple(
                {
                    "position_candidate_id": str(relation.position_candidate_id),
                    "items": list(analysis.evidence),
                }
                for relation, analysis in zip(relations, analyses, strict=True)
            ),
            unknowns=unknowns,
            conflicts=conflicts,
            verification_questions=questions,
            agent_version=command.agent_version,
            model_version=command.model_version,
            feedback_ids=(),
        )
        return self._repository.add_analysis(
            comparison,
            analysis_version_id=_derived(
                command.owner_id, command.client_request_id, "comparison"
            ),
            feedback_ids=feedback,
        )
