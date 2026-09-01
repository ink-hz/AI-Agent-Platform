from __future__ import annotations

import asyncio
import inspect
from uuid import UUID

from .evidence import GitEvidenceVerifier
from .repository import (
    InvalidReviewMutation,
    PsycopgReviewRepository,
    ReviewRepositoryError,
)


class ReviewUnavailable(RuntimeError):
    pass


class UnavailableReviewService:
    """Fault-isolated service used when the dedicated writer is unavailable."""

    def __getattr__(self, _name: str):
        async def unavailable(*_args, **_kwargs):
            raise ReviewUnavailable("feedback review unavailable")

        return unavailable

    async def close(self) -> None:
        return None


class ReviewService:
    def __init__(
        self,
        read_repository: PsycopgReviewRepository,
        *,
        write_repository: PsycopgReviewRepository | None = None,
        registry=None,
        evidence_verifier=None,
        replay_runner=None,
    ) -> None:
        self.read_repository = read_repository
        self.write_repository = write_repository
        self.registry = registry
        self.evidence_verifier = evidence_verifier
        self.replay_runner = replay_runner

    def _writer(self) -> PsycopgReviewRepository:
        if self.write_repository is None:
            raise ReviewUnavailable("feedback review is read-only")
        return self.write_repository

    async def close(self) -> None:
        if self.replay_runner is not None and hasattr(self.replay_runner, "close"):
            await asyncio.to_thread(self.replay_runner.close)

    async def _run(self, operation, *args, **kwargs):
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except ReviewRepositoryError:
            raise
        except Exception as error:
            raise ReviewUnavailable("feedback review unavailable") from error

    async def _detail(self, issue_id: UUID) -> dict:
        detail = await self._run(self.read_repository.get_issue_detail, issue_id)
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
        writer = self._writer()
        await self._run(
            writer.recalculate_and_record_transition,
            issue_id,
            actor=actor,
            reason=reason,
        )
        return await self._detail(issue_id)

    async def overview(self, *, agent_id: str | None = None) -> dict:
        result = await self._run(
            self.read_repository.overview,
            agent_id=agent_id,
        )
        return {**result, "write_available": self.write_repository is not None}

    async def inbox(
        self,
        *,
        agent_id: str | None = None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        return await self._run(
            self.read_repository.list_inbox,
            agent_id=agent_id,
            limit=limit,
            offset=offset,
        )

    async def list_issues(
        self,
        *,
        agent_id: str | None = None,
        limit: int,
        offset: int,
        status: str | None = None,
        disposition: str | None = None,
    ) -> list[dict]:
        filters = {
            **({"status": status} if status is not None else {}),
            **({"disposition": disposition} if disposition is not None else {}),
        }
        return await self._run(
            self.read_repository.list_issues,
            agent_id=agent_id,
            limit=limit,
            offset=offset,
            **filters,
        )

    async def list_issue_page(self, **filters) -> dict:
        return await self._run(self.read_repository.list_issue_page, **filters)

    async def turn_summaries(self, *, turn_keys: list[str]) -> list[dict]:
        return await self._run(
            self.read_repository.get_turn_summaries,
            turn_keys,
        )

    async def issue_detail(self, issue_id: UUID) -> dict:
        return await self._detail(issue_id)

    async def evidence_issue_id(self, evidence_id: UUID) -> UUID:
        evidence = await self._run(
            self.read_repository.get_evidence_owner, evidence_id
        )
        if evidence is None:
            from .repository import ReviewNotFound

            raise ReviewNotFound("evidence not found")
        return evidence["issue_id"]

    async def replay_issue_id(self, replay_id: UUID) -> UUID:
        replay = await self._run(self.read_repository.get_replay, replay_id)
        if replay is None:
            from .repository import ReviewNotFound

            raise ReviewNotFound("replay not found")
        return replay["issue_id"]

    async def move_link_has_replay(self, issue_id: UUID, link_id: UUID) -> bool:
        self._writer()
        return bool(await self._run(
            self.read_repository.move_link_has_replay,
            issue_id,
            link_id,
        ))

    async def merge_relocation_has_replay(
        self,
        source_issue_id: UUID,
        target_issue_id: UUID,
    ) -> bool:
        self._writer()
        return bool(await self._run(
            self.read_repository.merge_relocation_has_replay,
            source_issue_id,
            target_issue_id,
        ))

    async def agent_issue_scope_valid(self, agent_id: str) -> bool:
        return bool(await self._run(
            self.read_repository.agent_issue_scope_valid, agent_id
        ))

    async def create_issue(self, payload, *, actor: str) -> dict:
        writer = self._writer()
        data = payload.model_dump(exclude={"reason"}, exclude_none=True)
        origin_turn_key = data.get("origin_turn_key")
        if origin_turn_key is not None:
            metadata = await self._run(
                self.read_repository.feedback_keys_for_turn,
                data["agent_id"],
                origin_turn_key,
            )
            if metadata is None:
                raise InvalidReviewMutation(
                    "origin turn does not belong to requested agent"
                )
        row = await self._run(
            writer.create_issue,
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
        writer = self._writer()
        updates = payload.model_dump(
            exclude={"row_version", "reason"},
            exclude_unset=True,
            exclude_none=False,
        )
        await self._run(
            writer.update_issue,
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
        writer = self._writer()
        detail = await self._detail(issue_id)
        if (detail.get("issue") or {}).get("agent_id") != payload.agent_id:
            raise InvalidReviewMutation(
                "target issue does not belong to requested agent"
            )
        metadata = await self._run(
            self.read_repository.feedback_keys_for_turn,
            payload.agent_id,
            payload.source_turn_key,
        )
        if metadata is None:
            raise InvalidReviewMutation("feedback source turn was not found")
        exact_keys = sorted(set(metadata.get("feedback_keys") or []))
        provided_keys = set(payload.source_feedback_keys or [])
        if not provided_keys.issubset(exact_keys):
            raise InvalidReviewMutation(
                "feedback keys do not belong to the exact source turn"
            )
        await self._run(
            writer.link_turn,
            issue_id,
            agent_id=payload.agent_id,
            source_turn_key=payload.source_turn_key,
            source_feedback_keys=exact_keys,
            link_role=payload.link_role,
            actor=actor,
            reason=payload.reason,
        )
        return await self._recalculate(
            issue_id,
            actor=actor,
            reason=payload.reason,
        )

    async def move_link(
        self,
        issue_id: UUID,
        link_id: UUID,
        payload,
        *,
        actor: str,
    ) -> dict:
        writer = self._writer()
        detail = await self._detail(issue_id)
        if not any(
            str(link["id"]) == str(link_id) and link["active"]
            for link in detail["links"]
        ):
            from .repository import InvalidReviewMutation

            raise InvalidReviewMutation("active link does not belong to source issue")
        if issue_id == payload.target_issue_id:
            from .repository import InvalidReviewMutation

            raise InvalidReviewMutation("target issue must differ from source issue")
        await self._detail(payload.target_issue_id)
        await self._run(
            writer.move_link,
            issue_id,
            link_id,
            payload.target_issue_id,
            actor=actor,
            reason=payload.reason,
        )
        await self._run(
            writer.recalculate_and_record_transition,
            issue_id,
            actor=actor,
            reason=payload.reason,
        )
        return await self._recalculate(
            payload.target_issue_id,
            actor=actor,
            reason=payload.reason,
        )

    async def merge_issue(self, issue_id: UUID, payload, *, actor: str) -> dict:
        writer = self._writer()
        await self._run(
            writer.merge_issue,
            issue_id,
            payload.target_issue_id,
            expected_row_version=payload.row_version,
            actor=actor,
            reason=payload.reason,
        )
        await self._run(
            writer.recalculate_and_record_transition,
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
        writer = self._writer()
        await self._run(
            writer.mark_fix_ready,
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
        writer = self._writer()
        data = payload.model_dump(exclude={"reason"}, exclude_none=True)
        await self._run(
            writer.add_evidence,
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
        writer = self._writer()
        evidence = await self._run(self.read_repository.get_evidence, evidence_id)
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
            writer.record_evidence_verification,
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
        writer = self._writer()
        if self.replay_runner is None:
            raise ReviewUnavailable("replay runner unavailable")
        detail = await self._detail(issue_id)
        if not any(
            str(link["id"]) == str(payload.issue_link_id) and link["active"]
            for link in detail["links"]
        ):
            from .repository import InvalidReviewMutation

            raise InvalidReviewMutation("replay link does not belong to issue")
        result = await asyncio.to_thread(
            self.replay_runner.run,
            issue_link_id=payload.issue_link_id,
            idempotency_key=payload.idempotency_key,
            actor=actor,
        )
        await self._run(
            writer.recalculate_and_record_transition,
            issue_id,
            actor=actor,
            reason="replay completed",
        )
        return result

    async def semantic_review(self, replay_id: UUID, payload, *, actor: str) -> dict:
        valid_identity = (
            payload.method == "codex"
            and actor == "codex"
            and payload.reviewer == actor
        ) or (
            payload.method == "human_fae"
            and actor.startswith(("fae:", "corp:"))
            and bool(actor.split(":", 1)[1].strip())
            and payload.reviewer == actor
        )
        if not valid_identity:
            raise InvalidReviewMutation("semantic review identity mismatch")
        writer = self._writer()
        row = await self._run(
            writer.review_replay,
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
        writer = self._writer()
        await self._run(
            writer.set_disposition,
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
