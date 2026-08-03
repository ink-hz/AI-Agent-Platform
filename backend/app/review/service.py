from __future__ import annotations

import asyncio
import inspect
from uuid import UUID

from .evidence import GitEvidenceVerifier
from .repository import PsycopgReviewRepository, ReviewRepositoryError


class ReviewUnavailable(RuntimeError):
    pass


class UnavailableReviewService:
    """Fault-isolated service used when the dedicated writer is unavailable."""

    def __getattr__(self, _name: str):
        async def unavailable(*_args, **_kwargs):
            raise ReviewUnavailable("feedback review unavailable")

        return unavailable


class ReviewService:
    def __init__(
        self,
        repository: PsycopgReviewRepository,
        *,
        registry=None,
        evidence_verifier=None,
        replay_runner=None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.evidence_verifier = evidence_verifier
        self.replay_runner = replay_runner

    async def _run(self, method, *args, **kwargs):
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except ReviewRepositoryError:
            raise
        except Exception as error:
            raise ReviewUnavailable("feedback review unavailable") from error

    async def _detail(self, issue_id: UUID) -> dict:
        detail = await self._run(self.repository.get_issue_detail, issue_id)
        if detail is None:
            from .repository import ReviewNotFound

            raise ReviewNotFound("issue not found")
        return detail

    async def _recalculate(
        self,
        issue_id: UUID,
        *,
        actor: str,
        reason: str = "",
    ) -> dict:
        await self._run(
            self.repository.recalculate_and_record_transition,
            issue_id,
            actor=actor,
            reason=reason,
        )
        return await self._detail(issue_id)

    async def overview(self) -> dict:
        return await self._run(self.repository.overview)

    async def inbox(self, *, limit: int, offset: int) -> list[dict]:
        return await self._run(
            self.repository.list_inbox,
            limit=limit,
            offset=offset,
        )

    async def list_issues(self, *, limit: int, offset: int) -> list[dict]:
        return await self._run(
            self.repository.list_issues,
            limit=limit,
            offset=offset,
        )

    async def issue_detail(self, issue_id: UUID) -> dict:
        return await self._detail(issue_id)

    async def create_issue(self, payload, *, actor: str) -> dict:
        data = payload.model_dump(exclude={"reason"}, exclude_none=True)
        row = await self._run(
            self.repository.create_issue,
            data,
            actor=actor,
            reason=payload.reason,
        )
        return await self._recalculate(
            row["id"],
            actor=actor,
            reason=payload.reason,
        )

    async def update_issue(self, issue_id: UUID, payload, *, actor: str) -> dict:
        updates = payload.model_dump(
            exclude={"row_version", "reason"},
            exclude_unset=True,
            exclude_none=False,
        )
        await self._run(
            self.repository.update_issue,
            issue_id,
            updates,
            expected_row_version=payload.row_version,
            actor=actor,
            reason=payload.reason,
        )
        return await self._recalculate(
            issue_id,
            actor=actor,
            reason=payload.reason,
        )

    async def link_turn(self, issue_id: UUID, payload, *, actor: str) -> dict:
        await self._run(
            self.repository.link_turn,
            issue_id,
            agent_id=payload.agent_id,
            source_turn_key=payload.source_turn_key,
            source_feedback_keys=payload.source_feedback_keys,
            link_role=payload.link_role,
            actor=actor,
            reason=payload.reason,
        )
        return await self._recalculate(
            issue_id,
            actor=actor,
            reason=payload.reason,
        )

    async def merge_issue(self, issue_id: UUID, payload, *, actor: str) -> dict:
        await self._run(
            self.repository.merge_issue,
            issue_id,
            payload.target_issue_id,
            expected_row_version=payload.row_version,
            actor=actor,
            reason=payload.reason,
        )
        await self._run(
            self.repository.recalculate_and_record_transition,
            issue_id,
            actor=actor,
            reason=payload.reason,
        )
        return await self._recalculate(
            payload.target_issue_id,
            actor=actor,
            reason=payload.reason,
        )

    async def mark_fix_ready(self, issue_id: UUID, payload, *, actor: str) -> dict:
        await self._run(
            self.repository.mark_fix_ready,
            issue_id,
            expected_row_version=payload.row_version,
            actor=actor,
            reason=payload.reason,
        )
        return await self._recalculate(
            issue_id,
            actor=actor,
            reason=payload.reason,
        )

    async def add_evidence(self, issue_id: UUID, payload, *, actor: str) -> dict:
        data = payload.model_dump(exclude={"reason"}, exclude_none=True)
        await self._run(
            self.repository.add_evidence,
            issue_id,
            data,
            actor=actor,
            reason=payload.reason,
        )
        return await self._recalculate(
            issue_id,
            actor=actor,
            reason=payload.reason,
        )

    async def verify_evidence(self, evidence_id: UUID, payload, *, actor: str) -> dict:
        evidence = await self._run(self.repository.get_evidence, evidence_id)
        if evidence is None:
            from .repository import ReviewNotFound

            raise ReviewNotFound("evidence not found")
        detail = await self._detail(evidence["issue_id"])
        verifier = self.evidence_verifier
        if verifier is None:
            if self.registry is None:
                raise ReviewUnavailable("evidence verifier unavailable")
            agent = self.registry.get_agent_by_flywheel_id(
                detail["issue"]["agent_id"]
            )
            config = agent.review_evidence if agent is not None else None
            if config is None:
                raise ReviewUnavailable("evidence verifier unavailable")
            verifier = GitEvidenceVerifier(
                config.repository_path,
                config.release_manifest_dir,
            )
        if evidence["evidence_type"] == "merge":
            method = verifier.verify_merge
            arguments = (evidence["commit_sha"],)
            keyword_arguments = {}
        elif evidence["evidence_type"] == "deployment":
            merges = [
                row
                for row in detail["evidence"]
                if row["evidence_type"] == "merge"
                and row["verification_status"] == "verified"
            ]
            if not merges:
                from .repository import InvalidReviewMutation

                raise InvalidReviewMutation("verified merge evidence required")
            merge_sha = merges[-1]["commit_sha"]
            method = verifier.verify_deployment
            arguments = (evidence["release_manifest_ref"],)
            keyword_arguments = {"merge_sha": merge_sha}
        else:
            from .repository import InvalidReviewMutation

            raise InvalidReviewMutation("evidence type has no machine verifier")
        if inspect.iscoroutinefunction(method):
            result = await method(*arguments, **keyword_arguments)
        else:
            result = await asyncio.to_thread(
                method,
                *arguments,
                **keyword_arguments,
            )
        row = await self._run(
            self.repository.record_evidence_verification,
            evidence_id,
            status=result.status,
            details=result.details,
            actor=actor,
            reason=payload.reason,
        )
        return await self._recalculate(
            row["issue_id"],
            actor=actor,
            reason=payload.reason,
        )

    async def start_replay(self, issue_id: UUID, payload, *, actor: str) -> dict:
        if self.replay_runner is None:
            raise ReviewUnavailable("replay runner unavailable")
        return await self.replay_runner.run(
            issue_id=issue_id,
            issue_link_id=payload.issue_link_id,
            idempotency_key=payload.idempotency_key,
            actor=actor,
        )

    async def semantic_review(self, replay_id: UUID, payload, *, actor: str) -> dict:
        row = await self._run(
            self.repository.review_replay,
            replay_id,
            verdict=payload.verdict,
            method=payload.method,
            reviewer=payload.reviewer,
            reason=payload.reason,
            actor=actor,
        )
        return await self._recalculate(
            row["issue_id"],
            actor=actor,
            reason=payload.reason,
        )

    async def set_disposition(self, issue_id: UUID, payload, *, actor: str) -> dict:
        await self._run(
            self.repository.set_disposition,
            issue_id,
            disposition=payload.disposition,
            canonical_issue_id=payload.canonical_issue_id,
            owner=payload.owner,
            disposition_reason=payload.reason,
            expected_row_version=payload.row_version,
            actor=actor,
        )
        return await self._recalculate(
            issue_id,
            actor=actor,
            reason=payload.reason,
        )
