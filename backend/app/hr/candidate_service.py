from __future__ import annotations

from uuid import UUID, uuid5

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


class CandidateServiceError(RuntimeError):
    pass


class CandidateIdentityConflict(CandidateServiceError):
    pass


class CandidateScopeViolation(CandidateServiceError):
    pass


def _derived(namespace: UUID, label: str) -> UUID:
    return uuid5(namespace, label)


class CandidateService:
    def __init__(self, repository) -> None:
        required = (
            "create_draft",
            "draft_for_owner",
            "retry_draft",
            "confirm_draft",
            "position_candidate_for_owner",
            "feedback_for_position_candidate",
            "add_analysis",
            "append_feedback",
            "latest_analysis",
        )
        if any(not callable(getattr(repository, name, None)) for name in required):
            raise ValueError("candidate repository required")
        self._repository = repository

    def create_drafts(
        self, command: CreateCandidateDraftBatch
    ) -> tuple[CandidateDraft, ...]:
        if not isinstance(command, CreateCandidateDraftBatch):
            raise ValueError("candidate draft batch required")
        return tuple(
            self._repository.create_draft(
                _derived(command.client_request_id, f"draft:{attachment_id}"),
                _derived(command.client_request_id, f"item:{attachment_id}"),
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

    def start_draft(
        self, owner_id: UUID, draft_id: UUID, request_id: UUID,
        expected_row_version: int,
    ) -> CandidateDraft:
        return self._repository.start_draft(
            owner_id, draft_id, request_id, expected_row_version
        )

    def complete_draft(
        self, owner_id: UUID, draft_id: UUID, request_id: UUID,
        expected_row_version: int, extracted_facts: dict[str, object],
        identity_candidates: tuple[UUID, ...] = (),
    ) -> CandidateDraft:
        return self._repository.complete_draft(
            owner_id, draft_id, request_id, expected_row_version,
            extracted_facts, identity_candidates,
        )

    def fail_draft(
        self, owner_id: UUID, draft_id: UUID, request_id: UUID,
        expected_row_version: int, error_code: str,
    ) -> CandidateDraft:
        return self._repository.fail_draft(
            owner_id, draft_id, request_id, expected_row_version, error_code
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
        draft = self._repository.draft_for_owner(command.owner_id, command.draft_id)
        if draft.state != "ready" or draft.row_version != command.expected_row_version:
            raise CandidateScopeViolation("candidate draft is not confirmable")
        if draft.identity_candidates and command.merge_candidate_id is None:
            raise CandidateIdentityConflict(
                "candidate identity requires an explicit merge target"
            )
        if (
            command.merge_candidate_id is not None
            and command.merge_candidate_id not in draft.identity_candidates
        ):
            raise CandidateIdentityConflict("candidate merge target was not proposed")
        return self._repository.confirm_draft(
            command,
            document_id=_derived(command.client_request_id, "document"),
            position_candidate_id=_derived(
                command.client_request_id, "position-candidate"
            ),
            context_version_id=context_version_id,
        )

    def add_analysis(
        self, command: CreateCandidateAnalysis
    ) -> CandidateAnalysisVersion:
        if not isinstance(command, CreateCandidateAnalysis):
            raise ValueError("candidate analysis command required")
        relation = self._repository.position_candidate_for_owner(
            command.owner_id, command.position_candidate_id
        )
        self._require_analysis_scope(command, relation)
        feedback = self._repository.feedback_for_position_candidate(
            command.owner_id, command.position_candidate_id
        )
        return self._repository.add_analysis(
            command,
            analysis_version_id=_derived(command.client_request_id, "analysis"),
            feedback_ids=tuple(item.feedback_id for item in feedback),
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
            feedback_id=_derived(command.client_request_id, "human-feedback"),
        )

    def compare(
        self, command: ComparePositionCandidates
    ) -> CandidateAnalysisVersion:
        if not isinstance(command, ComparePositionCandidates):
            raise ValueError("candidate comparison command required")
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
                for item in self._repository.feedback_for_position_candidate(
                    command.owner_id, relation.position_candidate_id
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
        )
        return self._repository.add_analysis(
            comparison,
            analysis_version_id=_derived(command.client_request_id, "comparison"),
            feedback_ids=feedback,
        )
