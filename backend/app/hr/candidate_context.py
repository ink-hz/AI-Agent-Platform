from __future__ import annotations

import json
from uuid import UUID

from .candidate_models import CandidateAnalysisVersion, CandidateEnvelopeFragment
from .candidate_service import CandidateScopeViolation

_PROMPT_BUDGET_BYTES = 65_536


class CandidateEnvelopeProvider:
    def __init__(self, repository, context_is_confirmed) -> None:
        required = (
            "position_candidate_for_owner",
            "candidate_for_owner",
            "documents_for_candidate",
            "attachment_state_for_document",
            "feedback_for_candidate_context",
        )
        if any(not callable(getattr(repository, name, None)) for name in required):
            raise ValueError("candidate context repository required")
        if not callable(context_is_confirmed):
            raise ValueError("confirmed position context resolver required")
        self._repository = repository
        self._context_is_confirmed = context_is_confirmed

    def for_task(
        self,
        owner_id: UUID,
        position_id: UUID,
        candidate_id: UUID | None,
        position_candidate_id: UUID | None,
        *,
        task_kind: str = "candidate_match",
    ) -> CandidateEnvelopeFragment:
        if any(not isinstance(value, UUID) for value in (owner_id, position_id)):
            raise ValueError("candidate task identifiers invalid")
        if not isinstance(candidate_id, UUID) or not isinstance(
            position_candidate_id, UUID
        ):
            raise CandidateScopeViolation("candidate task scope is incomplete")
        if task_kind not in {"candidate_match", "candidate_interview_plan"}:
            raise CandidateScopeViolation("candidate task kind is invalid")
        relation = self._repository.position_candidate_for_owner(
            owner_id, position_candidate_id
        )
        if (
            relation.status != "active"
            or relation.position_id != position_id
            or relation.candidate_id != candidate_id
        ):
            raise CandidateScopeViolation("candidate task relation mismatch")
        if self._context_is_confirmed(
            owner_id, position_id, relation.context_version_id
        ) is not True:
            raise CandidateScopeViolation("candidate position context is not confirmed")
        candidate = self._repository.candidate_for_owner(owner_id, candidate_id)
        if candidate.owner_id != owner_id or candidate.candidate_id != candidate_id:
            raise CandidateScopeViolation("candidate identity mismatch")
        documents = tuple(
            document
            for document in self._repository.documents_for_candidate(
                owner_id, candidate_id
            )
            if document.status == "active"
        )
        if not documents:
            raise CandidateScopeViolation("candidate has no active document")
        ordered_documents = tuple(
            sorted(documents, key=lambda value: (value.version_number, value.document_id))
        )
        for document in ordered_documents:
            if (
                document.owner_id != owner_id
                or document.candidate_id != candidate_id
                or self._repository.attachment_state_for_document(
                    owner_id, document.document_id
                )
                != "ready"
            ):
                raise CandidateScopeViolation("candidate document unavailable")
        feedback = tuple(
            reversed(sorted(
                self._repository.feedback_for_candidate_context(
                    owner_id, position_candidate_id, relation.context_version_id
                ),
                key=lambda value: (value.created_at, value.feedback_id),
            ))
        )
        if any(
            item.owner_id != owner_id
            or item.position_candidate_id != position_candidate_id
            for item in feedback
        ):
            raise CandidateScopeViolation("candidate feedback scope mismatch")
        match_analysis = None
        if task_kind == "candidate_interview_plan":
            reader = getattr(self._repository, "latest_analysis", None)
            if not callable(reader):
                raise CandidateScopeViolation("matching analysis unavailable")
            try:
                match_analysis = reader(
                    owner_id, position_candidate_id, relation.context_version_id,
                    kind="match",
                )
            except (RuntimeError, ValueError):
                raise CandidateScopeViolation("matching analysis unavailable") from None
            if (
                not isinstance(match_analysis, CandidateAnalysisVersion)
                or match_analysis.owner_id != owner_id
                or match_analysis.position_id != position_id
                or match_analysis.position_candidate_id != position_candidate_id
                or match_analysis.candidate_id != candidate_id
                or match_analysis.context_version_id != relation.context_version_id
                or match_analysis.analysis_kind != "match"
                or match_analysis.document_ids
                != tuple(document.document_id for document in ordered_documents)
            ):
                raise CandidateScopeViolation("matching analysis unavailable")
        prompt_sections = [
                "CONFIRMED_CANDIDATE_FACTS",
                json.dumps(
                    {
                        "candidate_id": str(candidate.candidate_id),
                        "stable_name": candidate.stable_name,
                        "facts": candidate.facts,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "EXACT_CANDIDATE_DOCUMENT_VERSIONS",
                json.dumps(
                    [
                        {
                            "document_id": str(document.document_id),
                            "attachment_id": str(document.attachment_id),
                            "version_number": document.version_number,
                            "content_sha256": document.content_sha256,
                        }
                        for document in ordered_documents
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "HUMAN_FEEDBACK_DO_NOT_REWRITE_AS_AI_FACT",
        ]
        if match_analysis is not None:
            prompt_sections.extend((
                "LATEST_MATCH_ANALYSIS_FOR_INTERVIEW",
                json.dumps({
                    "analysis_version_id": str(match_analysis.analysis_version_id),
                    "version_number": match_analysis.version_number,
                    "result": match_analysis.result,
                    "evidence": match_analysis.evidence,
                    "unknowns": match_analysis.unknowns,
                    "verification_questions": match_analysis.verification_questions,
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ))
        feedback_payloads = tuple(
            {
                "feedback_id": str(item.feedback_id),
                "analysis_version_id": str(item.analysis_version_id),
                "feedback_kind": item.feedback_kind,
                "conclusion_key": item.conclusion_key,
                "correction": item.correction,
                "reason": item.reason,
            }
            for item in feedback[:100]
        )
        selected_feedback = []
        prompt = ""
        for item_payload in feedback_payloads:
            candidate_prompt = "\n".join((*prompt_sections, json.dumps(
                [*selected_feedback, item_payload], ensure_ascii=False,
                sort_keys=True, separators=(",", ":"),
            )))
            if len(candidate_prompt.encode("utf-8")) > _PROMPT_BUDGET_BYTES:
                break
            selected_feedback.append(item_payload)
            prompt = candidate_prompt
        if not prompt:
            prompt = "\n".join((*prompt_sections, "[]"))
        if len(prompt.encode("utf-8")) > _PROMPT_BUDGET_BYTES:
            raise CandidateScopeViolation("candidate context exceeds prompt budget")
        selected_feedback_ids = tuple(
            UUID(item["feedback_id"]) for item in selected_feedback
        )
        try:
            return CandidateEnvelopeFragment(
                candidate_id=candidate_id,
                position_candidate_id=position_candidate_id,
                context_version_id=relation.context_version_id,
                document_ids=tuple(
                    document.document_id for document in ordered_documents
                ),
                document_attachment_ids=tuple(
                    document.attachment_id for document in ordered_documents
                ),
                human_feedback_ids=selected_feedback_ids,
                prompt_context=prompt,
                match_analysis_version_id=(
                    match_analysis.analysis_version_id
                    if match_analysis is not None else None
                ),
            )
        except ValueError:
            raise CandidateScopeViolation("candidate context is not usable") from None
