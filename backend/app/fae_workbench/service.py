from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.observability.models import SessionFilters
from app.review.http_models import CreateIssue, LinkTurn
from app.review.repository import ReviewNotFound

from .models import (
    FAE_AGENT_ID,
    FAE_SOURCE_KIND,
    FaeAttentionSection,
    FaeFreshness,
    FaeIssueSection,
    FaeOverview,
    FaeReportPreviewSection,
    FaeSectionState,
    FaeSummary,
    FaeSummarySection,
    FaeTrendSection,
)
from .repository import FaeWorkbenchRepository


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CLOSED_ISSUE_STATUSES = frozenset({"closed", "duplicate", "not_actionable", "wont_fix"})


def _fae_filters(filters: SessionFilters) -> SessionFilters:
    return filters.model_copy(
        update={"agent_id": FAE_AGENT_ID, "source_kind": FAE_SOURCE_KIND}
    )


def _default_period(now: datetime) -> tuple[datetime, datetime]:
    local_now = now.astimezone(_SHANGHAI)
    period_end = datetime.combine(local_now.date(), time.min, tzinfo=_SHANGHAI)
    return period_end - timedelta(days=7), period_end


class FaeWorkbenchService:
    def __init__(self, repository: FaeWorkbenchRepository, observability, review) -> None:
        self._repository = repository
        self._observability = observability
        self._review = review

    async def list_sessions(self, filters: SessionFilters, limit: int, offset: int):
        return await self._observability.list_sessions(_fae_filters(filters), limit, offset)

    async def get_session(self, session_key: str):
        value = await self._observability.get_session(session_key)
        if value is None:
            return None
        if value.agent_id != FAE_AGENT_ID or value.source_kind != FAE_SOURCE_KIND:
            return None
        return value

    async def _assert_fae_issue_scope(self) -> None:
        if not await self._review.agent_issue_scope_valid(FAE_AGENT_ID):
            raise ReviewNotFound("issue not found")

    async def _fae_issue(
        self,
        issue_id: UUID,
        *,
        validate_agent: bool = True,
        path: frozenset[UUID] = frozenset(),
        validated: dict[UUID, dict] | None = None,
        historical_path: frozenset[UUID] = frozenset(),
        loaded: dict[UUID, dict] | None = None,
    ) -> dict:
        if validate_agent:
            await self._assert_fae_issue_scope()
        if issue_id in path:
            raise ReviewNotFound("issue not found")
        loaded = loaded if loaded is not None else {}
        if issue_id in historical_path:
            if issue_id not in loaded:
                raise ReviewNotFound("issue not found")
            return loaded[issue_id]
        if validated is not None and issue_id in validated:
            return validated[issue_id]
        detail = await self._review.issue_detail(issue_id)
        try:
            issue = detail["issue"]
            agent_id = issue["agent_id"]
        except (KeyError, TypeError):
            raise ReviewNotFound("issue not found") from None
        if agent_id != FAE_AGENT_ID:
            raise ReviewNotFound("issue not found")
        loaded[issue_id] = detail
        links = detail.get("links") or ()
        if not isinstance(links, (list, tuple)) or any(
            not isinstance(link, dict) for link in links
        ):
            raise ReviewNotFound("issue not found")
        if any(link.get("agent_id") != FAE_AGENT_ID for link in links):
            raise ReviewNotFound("issue not found")
        link_ids: set[str] = set()
        link_identities: dict[str, tuple[object, object]] = {}
        for link in links:
            try:
                link_id = str(UUID(str(link["id"])))
                link_issue_id = UUID(str(link["issue_id"]))
            except (KeyError, TypeError, ValueError):
                raise ReviewNotFound("issue not found") from None
            if link_issue_id != issue_id or link_id in link_ids:
                raise ReviewNotFound("issue not found")
            link_ids.add(link_id)
            link_identities[link_id] = (
                link.get("agent_id"),
                link.get("source_turn_key"),
            )
        replays = detail.get("replays") or ()
        if not isinstance(replays, (list, tuple)) or any(
            not isinstance(replay, dict) for replay in replays
        ):
            raise ReviewNotFound("issue not found")
        if any(str(replay.get("issue_link_id")) not in link_ids for replay in replays):
            raise ReviewNotFound("issue not found")
        events = detail.get("events") or ()
        if not isinstance(events, (list, tuple)) or any(
            not isinstance(event, dict) for event in events
        ):
            raise ReviewNotFound("issue not found")
        historical_links: list[dict] = []
        historical_move_link_ids: set[str] = set()
        linked_event_ids: set[str] = set()
        referenced_issue_ids: set[UUID] = set()
        for event in events:
            event_type = event.get("event_type")
            if event_type in {"turn_linked", "turn_linked_from_release_handoff"}:
                snapshots = (event.get("after"),)
            elif event_type in {"link_moved_in", "link_moved_out"}:
                snapshots = (event.get("before"), event.get("after"))
            else:
                continue
            if any(not isinstance(snapshot, dict) for snapshot in snapshots):
                raise ReviewNotFound("issue not found")
            try:
                snapshot_ids = [
                    str(UUID(str(snapshot["id"]))) for snapshot in snapshots
                ]
                snapshot_issue_ids = [
                    UUID(str(snapshot["issue_id"])) for snapshot in snapshots
                ]
            except (KeyError, TypeError, ValueError):
                raise ReviewNotFound("issue not found") from None
            for snapshot_id, snapshot in zip(snapshot_ids, snapshots, strict=True):
                identity = (
                    snapshot.get("agent_id"),
                    snapshot.get("source_turn_key"),
                )
                if (
                    snapshot_id in link_identities
                    and link_identities[snapshot_id] != identity
                ):
                    raise ReviewNotFound("issue not found")
                link_identities[snapshot_id] = identity
            if event_type in {"turn_linked", "turn_linked_from_release_handoff"}:
                if snapshot_issue_ids[0] != issue_id:
                    raise ReviewNotFound("issue not found")
                linked_event_ids.add(snapshot_ids[0])
            else:
                if snapshot_ids[0] != snapshot_ids[1]:
                    raise ReviewNotFound("issue not found")
                if (
                    snapshots[0].get("agent_id") != snapshots[1].get("agent_id")
                    or snapshots[0].get("source_turn_key")
                    != snapshots[1].get("source_turn_key")
                ):
                    raise ReviewNotFound("issue not found")
                if event_type == "link_moved_out":
                    if snapshot_issue_ids[0] != issue_id:
                        raise ReviewNotFound("issue not found")
                    referenced_issue_ids.add(snapshot_issue_ids[1])
                else:
                    if snapshot_issue_ids[1] != issue_id:
                        raise ReviewNotFound("issue not found")
                    referenced_issue_ids.add(snapshot_issue_ids[0])
                historical_move_link_ids.add(snapshot_ids[0])
            historical_links.extend(snapshots)
        if not linked_event_ids.issubset(link_ids | historical_move_link_ids):
            raise ReviewNotFound("issue not found")
        if any(
            snapshot.get("agent_id") != FAE_AGENT_ID
            for snapshot in historical_links
        ):
            raise ReviewNotFound("issue not found")
        turn_keys = [
            key
            for key in [
                issue.get("origin_turn_key"),
                *(link.get("source_turn_key") for link in links),
                *(snapshot.get("source_turn_key") for snapshot in historical_links),
            ]
            if key is not None
        ]
        if len(turn_keys) != (
            int(issue.get("origin_turn_key") is not None)
            + len(links)
            + len(historical_links)
        ):
            raise ReviewNotFound("issue not found")
        if len(await self._fae_turn_keys(turn_keys)) != len(set(turn_keys)):
            raise ReviewNotFound("issue not found")
        for referenced_issue_id in referenced_issue_ids:
            await self._fae_issue(
                referenced_issue_id,
                validate_agent=False,
                path=path,
                validated=validated,
                historical_path=historical_path | {issue_id},
                loaded=loaded,
            )
        canonical = issue.get("canonical_issue_id")
        if canonical is not None:
            try:
                canonical_id = UUID(str(canonical))
            except (TypeError, ValueError):
                raise ReviewNotFound("issue not found") from None
            await self._fae_issue(
                canonical_id,
                validate_agent=False,
                path=path | {issue_id},
                validated=validated,
                historical_path=historical_path,
                loaded=loaded,
            )
        if validated is not None:
            validated[issue_id] = detail
        return detail

    async def _fae_turn_keys(self, turn_keys: list[str]) -> set[str]:
        if not turn_keys:
            return set()
        return await asyncio.to_thread(self._repository.fae_turn_keys, turn_keys)

    async def _fae_turn_exists(self, turn_key: str) -> bool:
        return turn_key in await self._fae_turn_keys([turn_key])

    async def issue_overview(self):
        await self._assert_fae_issue_scope()
        return await self._review.overview(agent_id=FAE_AGENT_ID)

    async def issue_inbox(self, *, limit: int, offset: int):
        return await self._review.inbox(
            agent_id=FAE_AGENT_ID,
            limit=limit,
            offset=offset,
        )

    async def list_issues(self, *, limit: int, offset: int):
        await self._assert_fae_issue_scope()
        return await self._review.list_issues(
            agent_id=FAE_AGENT_ID,
            limit=limit,
            offset=offset,
        )

    async def issue_detail(self, issue_id: UUID):
        return await self._fae_issue(issue_id)

    async def turn_summaries(self, turn_keys: list[str]):
        unique_keys = list(dict.fromkeys(turn_keys))
        found_keys = await self._fae_turn_keys(unique_keys)
        scoped_keys = [key for key in unique_keys if key in found_keys]
        if not scoped_keys:
            return []
        summaries = await self._review.turn_summaries(turn_keys=scoped_keys)
        issue_ids: list[UUID] = []
        for summary in summaries:
            issue_id = summary.get("issue_id")
            if issue_id is not None:
                try:
                    scoped_issue_id = UUID(str(issue_id))
                except (TypeError, ValueError):
                    raise ReviewNotFound("issue not found") from None
                issue_ids.append(scoped_issue_id)
        if issue_ids:
            await self._assert_fae_issue_scope()
            validated: dict[UUID, dict] = {}
            for scoped_issue_id in dict.fromkeys(issue_ids):
                await self._fae_issue(
                    scoped_issue_id,
                    validate_agent=False,
                    validated=validated,
                )
        return summaries

    async def create_issue(self, payload, *, actor: str):
        data = payload.model_dump()
        data["agent_id"] = FAE_AGENT_ID
        scoped = CreateIssue.model_validate(data)
        if scoped.origin_turn_key is not None and not await self._fae_turn_exists(
            scoped.origin_turn_key
        ):
            raise ReviewNotFound("turn not found")
        return await self._review.create_issue(scoped, actor=actor)

    async def update_issue(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        return await self._review.update_issue(issue_id, payload, actor=actor)

    async def link_turn(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        if not await self._fae_turn_exists(payload.source_turn_key):
            raise ReviewNotFound("turn not found")
        data = payload.model_dump()
        data["agent_id"] = FAE_AGENT_ID
        scoped = LinkTurn.model_validate(data)
        return await self._review.link_turn(issue_id, scoped, actor=actor)

    async def move_link(
        self,
        issue_id: UUID,
        link_id: UUID,
        payload,
        *,
        actor: str,
    ):
        await self._fae_issue(issue_id)
        await self._fae_issue(payload.target_issue_id)
        return await self._review.move_link(
            issue_id,
            link_id,
            payload,
            actor=actor,
        )

    async def merge_issue(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        await self._fae_issue(payload.target_issue_id)
        return await self._review.merge_issue(issue_id, payload, actor=actor)

    async def mark_fix_ready(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        return await self._review.mark_fix_ready(issue_id, payload, actor=actor)

    async def add_evidence(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        return await self._review.add_evidence(issue_id, payload, actor=actor)

    async def verify_evidence(self, evidence_id: UUID, payload, *, actor: str):
        issue_id = await self._review.evidence_issue_id(evidence_id)
        await self._fae_issue(issue_id)
        return await self._review.verify_evidence(evidence_id, payload, actor=actor)

    async def start_replay(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        return await self._review.start_replay(issue_id, payload, actor=actor)

    async def semantic_review(self, replay_id: UUID, payload, *, actor: str):
        issue_id = await self._review.replay_issue_id(replay_id)
        await self._fae_issue(issue_id)
        return await self._review.semantic_review(replay_id, payload, actor=actor)

    async def set_disposition(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        if payload.canonical_issue_id is not None:
            await self._fae_issue(payload.canonical_issue_id)
        return await self._review.set_disposition(issue_id, payload, actor=actor)

    async def overview(self, now: datetime) -> FaeOverview:
        period_start, period_end = _default_period(now)
        snapshot, review_overview = await asyncio.gather(
            asyncio.to_thread(self._repository.snapshot, period_start, period_end),
            self.issue_overview(),
            return_exceptions=True,
        )

        if isinstance(review_overview, BaseException):
            issues = FaeIssueSection(
                state=FaeSectionState(
                    status="unavailable", error_code="issues_unavailable"
                )
            )
            open_issue_count = None
        else:
            statuses = dict(review_overview.get("statuses") or {})
            open_issue_count = sum(
                int(count)
                for status, count in statuses.items()
                if status not in _CLOSED_ISSUE_STATUSES
            )
            issues = FaeIssueSection(
                state=FaeSectionState(status="available"), statuses=statuses
            )

        if isinstance(snapshot, BaseException):
            state = FaeSectionState(
                status="unavailable", error_code="operational_summary_unavailable"
            )
            summary = FaeSummarySection(state=state)
            attention = FaeAttentionSection(state=state)
            trends = FaeTrendSection(state=state)
            freshness = FaeFreshness(status="unavailable", data_as_of=None)
        else:
            state = FaeSectionState(status="available", as_of=snapshot.data_as_of)
            summary = FaeSummarySection(
                state=state,
                data=FaeSummary(
                    session_count=snapshot.session_count,
                    active_subject_count=snapshot.active_subject_count,
                    negative_feedback_events=snapshot.negative_feedback_events,
                    negative_turn_count=snapshot.negative_turn_count,
                    abnormal_session_count=snapshot.abnormal_session_count,
                    open_issue_count=open_issue_count,
                    p50_duration_ms=snapshot.p50_duration_ms,
                    p95_duration_ms=snapshot.p95_duration_ms,
                ),
            )
            attention = FaeAttentionSection(state=state, items=snapshot.attention)
            trends = FaeTrendSection(state=state, points=snapshot.trend)
            freshness = self._freshness(snapshot.data_as_of, now)

        return FaeOverview(
            period_start=period_start,
            period_end=period_end,
            freshness=freshness,
            summary=summary,
            attention=attention,
            trends=trends,
            issues=issues,
            reports=FaeReportPreviewSection(
                state=FaeSectionState(
                    status="unavailable", error_code="reports_not_integrated"
                )
            ),
        )

    @staticmethod
    def _freshness(data_as_of: datetime | None, now: datetime) -> FaeFreshness:
        if data_as_of is None:
            return FaeFreshness(status="unavailable", data_as_of=None)
        if now - data_as_of > timedelta(hours=36):
            return FaeFreshness(status="stale", data_as_of=data_as_of)
        return FaeFreshness(status="fresh", data_as_of=data_as_of)
