from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.fae_workbench.models import (
    FAE_AGENT_ID,
    FAE_SOURCE_KIND,
    FaeOverview,
    FaeOperationalSnapshot,
    FaeSessionAttention,
    FaeTrendPoint,
)
from app.fae_workbench.service import FaeWorkbenchService
from app.observability.models import Page, SessionDetail, SessionFilters, SessionSummary
from app.review.repository import ReviewNotFound, ReviewRepositoryError


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
ISSUE_ID = UUID("00000000-0000-0000-0000-000000000001")
TARGET_ID = UUID("00000000-0000-0000-0000-000000000002")
LINK_ID = UUID("00000000-0000-0000-0000-000000000003")
EVIDENCE_ID = UUID("00000000-0000-0000-0000-000000000004")
REPLAY_ID = UUID("00000000-0000-0000-0000-000000000005")
LOCAL_PERIOD_END = datetime(2026, 9, 7, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
LOCAL_PERIOD_START = LOCAL_PERIOD_END - timedelta(days=7)


def fae_snapshot(*, data_as_of: datetime | None = NOW) -> FaeOperationalSnapshot:
    return FaeOperationalSnapshot(
        period_start=LOCAL_PERIOD_START,
        period_end=LOCAL_PERIOD_END,
        data_as_of=data_as_of,
        session_count=12,
        active_subject_count=7,
        negative_feedback_events=3,
        negative_turn_count=2,
        abnormal_session_count=1,
        p50_duration_ms=820,
        p95_duration_ms=3100,
        trend=[FaeTrendPoint(day=date(2026, 9, 6), sessions=12, negative_turns=2)],
        attention=[FaeSessionAttention(
            session_key="fae:session-1",
            title="设备掉线",
            last_active_at=NOW,
            reason="fallback",
        )],
    )


def admin_session() -> SessionDetail:
    return SessionDetail(
        session_key="admin:session-1",
        agent_id="ai-admin-agent",
        source_kind="admin",
        channel="admin",
        created_at=NOW,
        last_active_at=NOW,
        turn_count=1,
        feedback_count=0,
        review_count=0,
        freshness="fresh",
    )


class StaticRepository:
    def __init__(self, snapshot: FaeOperationalSnapshot | Exception | None = None) -> None:
        self.value = snapshot or fae_snapshot()
        self.period: tuple[datetime, datetime] | None = None
        self.batch_calls: list[list[str]] = []

    def snapshot(self, period_start: datetime, period_end: datetime) -> FaeOperationalSnapshot:
        self.period = (period_start, period_end)
        if isinstance(self.value, Exception):
            raise self.value
        return self.value

    def fae_turn_exists(self, turn_key: str) -> bool:
        return turn_key.startswith("fae:") and turn_key != "fae:missing"

    def fae_turn_keys(self, turn_keys: list[str]) -> set[str]:
        self.batch_calls.append(list(turn_keys))
        return {key for key in turn_keys if self.fae_turn_exists(key)}


class RecordingObservability:
    def __init__(self, session: SessionDetail | None = None) -> None:
        self.filters: SessionFilters | None = None
        self.session = session

    async def list_sessions(self, filters: SessionFilters, limit: int, offset: int):
        self.filters = filters
        return Page[SessionSummary](items=[], total=0, limit=limit, offset=offset)

    async def get_session(self, _session_key: str):
        return self.session


class StaticReview:
    def __init__(self, statuses: dict[str, int] | None = None) -> None:
        self.agent_id: str | None = None
        self.statuses = statuses or {
            "pending_triage": 2,
            "closed": 1,
            "duplicate": 1,
            "not_actionable": 1,
            "wont_fix": 1,
        }

    async def overview(self, *, agent_id: str | None = None) -> dict:
        self.agent_id = agent_id
        return {"statuses": self.statuses}

    async def agent_issue_scope_valid(self, agent_id: str) -> bool:
        return agent_id == FAE_AGENT_ID


class UnavailableReview:
    async def overview(self, *, agent_id: str | None = None) -> dict:
        raise RuntimeError("review unavailable")


class RecordingIssueReview:
    def __init__(self) -> None:
        self.details = {
            ISSUE_ID: {"issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID}},
            TARGET_ID: {"issue": {"id": TARGET_ID, "agent_id": FAE_AGENT_ID}},
        }
        self.calls: list[tuple] = []
        self.evidence_owner = ISSUE_ID
        self.replay_owner = ISSUE_ID
        self.summary_issue_id = None
        self.scope_valid = True
        self.detail_loads: list[UUID] = []

    async def agent_issue_scope_valid(self, agent_id):
        self.calls.append(("scope_valid", agent_id))
        return self.scope_valid

    async def issue_detail(self, issue_id):
        self.detail_loads.append(issue_id)
        detail = self.details.get(issue_id)
        if detail is None:
            raise ReviewNotFound("issue not found")
        return detail

    async def overview(self, *, agent_id=None):
        self.calls.append(("overview", agent_id))
        return {"statuses": {}}

    async def inbox(self, *, agent_id=None, limit, offset):
        self.calls.append(("inbox", agent_id, limit, offset))
        return []

    async def list_issues(self, *, agent_id=None, limit, offset):
        self.calls.append(("issues", agent_id, limit, offset))
        return []

    async def turn_summaries(self, *, turn_keys):
        self.calls.append(("turn_summaries", turn_keys))
        return [
            {
                "turn_key": key,
                **(
                    {"issue_id": self.summary_issue_id}
                    if self.summary_issue_id is not None
                    else {}
                ),
            }
            for key in turn_keys
        ]

    async def evidence_issue_id(self, evidence_id):
        self.calls.append(("evidence_owner", evidence_id))
        if self.evidence_owner is None:
            raise ReviewNotFound("evidence not found")
        return self.evidence_owner

    async def replay_issue_id(self, replay_id):
        self.calls.append(("replay_owner", replay_id))
        if self.replay_owner is None:
            raise ReviewNotFound("replay not found")
        return self.replay_owner

    def __getattr__(self, name):
        async def record(*args, **kwargs):
            self.calls.append((name, *args, kwargs))
            issue_id = kwargs.get("issue_id") or (args[0] if args else ISSUE_ID)
            return {"issue": {"id": issue_id, "agent_id": FAE_AGENT_ID}}

        return record


class Payload(SimpleNamespace):
    def model_dump(self, **_kwargs):
        return dict(vars(self))


def service_for(
    *,
    repository: StaticRepository | None = None,
    observability: RecordingObservability | None = None,
    review: StaticReview | UnavailableReview | RecordingIssueReview | None = None,
) -> FaeWorkbenchService:
    return FaeWorkbenchService(
        repository or StaticRepository(),
        observability or RecordingObservability(),
        review or StaticReview(),
    )


@pytest.mark.asyncio
async def test_issue_reads_always_inject_fae_agent_scope():
    review = RecordingIssueReview()
    service = service_for(review=review)

    await service.issue_overview()
    await service.issue_inbox(limit=20, offset=3)
    await service.list_issues(limit=10, offset=4)
    summaries = await service.turn_summaries(
        ["fae:turn-ordinary", "admin:turn-1", "fae:missing"]
    )

    assert review.calls == [
        ("scope_valid", FAE_AGENT_ID),
        ("overview", FAE_AGENT_ID),
        ("inbox", FAE_AGENT_ID, 20, 3),
        ("scope_valid", FAE_AGENT_ID),
        ("issues", FAE_AGENT_ID, 10, 4),
        ("turn_summaries", ["fae:turn-ordinary"]),
    ]
    assert summaries == [{"turn_key": "fae:turn-ordinary"}]


@pytest.mark.asyncio
async def test_create_overrides_agent_scope_server_side():
    review = RecordingIssueReview()
    payload = Payload(title="scoped issue", priority="P1", reason="inspect")

    await service_for(review=review).create_issue(payload, actor="fae:owner")

    name, created, call = review.calls[-1]
    assert name == "create_issue"
    assert created.agent_id == FAE_AGENT_ID
    assert call == {"actor": "fae:owner"}


@pytest.mark.asyncio
async def test_create_rejects_unknown_origin_turn_before_review_write():
    review = RecordingIssueReview()
    payload = Payload(
        origin_turn_key="fae:missing",
        title="scoped issue",
        priority="P1",
        reason="inspect",
    )

    with pytest.raises(ReviewNotFound, match="turn not found"):
        await service_for(review=review).create_issue(payload, actor="fae:owner")

    assert all(call[0] != "create_issue" for call in review.calls)


@pytest.mark.asyncio
async def test_link_accepts_real_turn_without_feedback_and_overrides_agent_scope():
    review = RecordingIssueReview()
    payload = Payload(
        source_turn_key="fae:turn-ordinary",
        source_feedback_keys=[],
        link_role="primary",
        reason="inspect",
    )

    await service_for(review=review).link_turn(ISSUE_ID, payload, actor="fae:owner")

    name, issue_id, linked, call = review.calls[-1]
    assert (name, issue_id, linked.agent_id) == (
        "link_turn",
        ISSUE_ID,
        FAE_AGENT_ID,
    )
    assert linked.source_feedback_keys == []
    assert call == {"actor": "fae:owner"}


@pytest.mark.asyncio
async def test_unknown_turn_is_hidden_before_review_write():
    review = RecordingIssueReview()
    payload = Payload(
        source_turn_key="fae:missing",
        source_feedback_keys=[],
        link_role="primary",
        reason="inspect",
    )

    with pytest.raises(ReviewNotFound, match="turn not found"):
        await service_for(review=review).link_turn(
            ISSUE_ID, payload, actor="fae:owner"
        )

    assert all(call[0] != "link_turn" for call in review.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["link_turn", "move_link", "merge_issue"])
async def test_cross_agent_source_issue_is_hidden_before_link_move_or_merge(
    operation,
):
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": "ai-admin-agent"}
    }
    service = service_for(review=review)
    payload = Payload(
        source_turn_key="fae:turn-ordinary",
        source_feedback_keys=[],
        link_role="primary",
        target_issue_id=TARGET_ID,
        row_version=1,
        reason="scope",
    )

    with pytest.raises(ReviewNotFound, match="issue not found"):
        if operation == "link_turn":
            await service.link_turn(ISSUE_ID, payload, actor="fae:owner")
        elif operation == "move_link":
            await service.move_link(
                ISSUE_ID, LINK_ID, payload, actor="fae:owner"
            )
        else:
            await service.merge_issue(ISSUE_ID, payload, actor="fae:owner")

    assert all(call[0] != operation for call in review.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["issue_detail", "move_link", "start_replay"])
async def test_foreign_nested_link_hides_issue_before_read_move_or_replay(operation):
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
        "links": [
            {"id": LINK_ID, "agent_id": "ai-admin-agent", "active": True}
        ],
    }
    service = service_for(review=review)
    payload = Payload(
        target_issue_id=TARGET_ID,
        issue_link_id=LINK_ID,
        idempotency_key="one",
        reason="scope",
    )

    with pytest.raises(ReviewNotFound, match="issue not found"):
        if operation == "issue_detail":
            await service.issue_detail(ISSUE_ID)
        elif operation == "move_link":
            await service.move_link(
                ISSUE_ID, LINK_ID, payload, actor="fae:owner"
            )
        else:
            await service.start_replay(ISSUE_ID, payload, actor="fae:owner")

    assert all(call[0] != operation for call in review.calls)


@pytest.mark.asyncio
async def test_fae_turn_summary_hides_link_to_cross_agent_issue():
    review = RecordingIssueReview()
    review.summary_issue_id = TARGET_ID
    review.details[TARGET_ID] = {
        "issue": {"id": TARGET_ID, "agent_id": "ai-admin-agent"}
    }
    service = service_for(review=review)

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service.turn_summaries(["fae:turn-ordinary"])

    assert review.calls == [
        ("turn_summaries", ["fae:turn-ordinary"]),
        ("scope_valid", FAE_AGENT_ID),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("read_name", ["issue_overview", "list_issues"])
async def test_malformed_agent_issue_scope_fails_reads_closed(read_name):
    review = RecordingIssueReview()
    review.scope_valid = False
    service = service_for(review=review)

    with pytest.raises(ReviewNotFound, match="issue not found"):
        if read_name == "issue_overview":
            await service.issue_overview()
        else:
            await service.list_issues(limit=100, offset=0)

    assert not any(call[0] in {"overview", "issues"} for call in review.calls)


@pytest.mark.asyncio
async def test_malformed_agent_issue_scope_hides_composite_overview_issue_aggregate():
    review = RecordingIssueReview()
    review.scope_valid = False

    overview = await service_for(review=review).overview(NOW)

    assert overview.issues.state.status == "unavailable"
    assert overview.summary.data is not None
    assert overview.summary.data.open_issue_count is None
    assert not any(call[0] == "overview" for call in review.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detail",
    [
        {"issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID,
                   "origin_turn_key": "admin:turn-1"}},
        {"issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
         "links": [{"id": LINK_ID, "agent_id": FAE_AGENT_ID,
                    "source_turn_key": "admin:turn-1", "active": True}]},
    ],
)
async def test_real_foreign_turn_ownership_fails_before_issue_write(detail):
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = detail

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).update_issue(
            ISSUE_ID, Payload(row_version=1, reason="edit"), actor="corp:owner"
        )

    assert all(call[0] != "update_issue" for call in review.calls)


@pytest.mark.asyncio
async def test_inactive_foreign_link_fails_detail_closed():
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
        "links": [{
            "id": LINK_ID,
            "agent_id": "ai-admin-agent",
            "source_turn_key": "admin:turn-1",
            "active": False,
        }],
    }

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).issue_detail(ISSUE_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("read_name", ["issue_overview", "list_issues"])
async def test_inactive_foreign_link_scope_fails_aggregate_reads_closed(read_name):
    review = RecordingIssueReview()
    review.scope_valid = False
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
        "links": [{
            "id": LINK_ID,
            "agent_id": "ai-admin-agent",
            "source_turn_key": "admin:turn-1",
            "active": False,
        }],
    }
    service = service_for(review=review)

    with pytest.raises(ReviewNotFound, match="issue not found"):
        if read_name == "issue_overview":
            await service.issue_overview()
        else:
            await service.list_issues(limit=100, offset=0)

    assert not any(call[0] in {"overview", "issues"} for call in review.calls)


@pytest.mark.asyncio
async def test_foreign_historical_link_event_fails_before_issue_write():
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
        "events": [{
            "event_type": "link_moved_out",
            "before": {
                "agent_id": "ai-admin-agent",
                "source_turn_key": "admin:turn-1",
            },
            "after": {
                "agent_id": "ai-admin-agent",
                "source_turn_key": "admin:turn-1",
            },
        }],
    }

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).update_issue(
            ISSUE_ID, Payload(row_version=1, reason="edit"), actor="corp:owner"
        )

    assert all(call[0] != "update_issue" for call in review.calls)


def move_snapshot(
    *, link_id=LINK_ID, issue_id=ISSUE_ID, source_turn_key="fae:turn-ordinary"
):
    return {
        "id": link_id,
        "issue_id": issue_id,
        "agent_id": FAE_AGENT_ID,
        "source_turn_key": source_turn_key,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "before_issue_id", "after_issue_id"),
    [
        ("link_moved_out", ISSUE_ID, TARGET_ID),
        ("link_moved_in", TARGET_ID, ISSUE_ID),
    ],
)
async def test_move_event_foreign_referenced_issue_fails_before_write(
    event_type, before_issue_id, after_issue_id
):
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
        "events": [{
            "event_type": event_type,
            "before": move_snapshot(issue_id=before_issue_id),
            "after": move_snapshot(issue_id=after_issue_id),
        }],
    }
    review.details[TARGET_ID] = {
        "issue": {"id": TARGET_ID, "agent_id": "ai-admin-agent"}
    }

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).update_issue(
            ISSUE_ID, Payload(row_version=1, reason="edit"), actor="corp:owner"
        )

    assert all(call[0] != "update_issue" for call in review.calls)


@pytest.mark.asyncio
async def test_move_event_mismatched_link_identity_fails_before_write():
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
        "events": [{
            "event_type": "link_moved_out",
            "before": move_snapshot(link_id=LINK_ID, issue_id=ISSUE_ID),
            "after": move_snapshot(link_id=EVIDENCE_ID, issue_id=TARGET_ID),
        }],
    }

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).update_issue(
            ISSUE_ID, Payload(row_version=1, reason="edit"), actor="corp:owner"
        )

    assert all(call[0] != "update_issue" for call in review.calls)


@pytest.mark.asyncio
async def test_turn_linked_event_identity_must_bind_to_current_or_moved_link():
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
        "links": [],
        "events": [{
            "event_type": "turn_linked",
            "after": move_snapshot(link_id=LINK_ID, issue_id=ISSUE_ID),
        }],
    }

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).update_issue(
            ISSUE_ID, Payload(row_version=1, reason="edit"), actor="corp:owner"
        )

    assert all(call[0] != "update_issue" for call in review.calls)


@pytest.mark.asyncio
async def test_turn_linked_identity_must_match_later_move_of_same_link():
    review = RecordingIssueReview()
    before = move_snapshot(issue_id=ISSUE_ID)
    after = move_snapshot(issue_id=TARGET_ID)
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
        "events": [
            {
                "event_type": "turn_linked",
                "after": move_snapshot(
                    issue_id=ISSUE_ID, source_turn_key="fae:other-real-turn"
                ),
            },
            {"event_type": "link_moved_out", "before": before, "after": after},
        ],
    }
    review.details[TARGET_ID] = {
        "issue": {"id": TARGET_ID, "agent_id": FAE_AGENT_ID},
        "links": [{**after, "active": True}],
    }

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).update_issue(
            ISSUE_ID, Payload(row_version=1, reason="edit"), actor="corp:owner"
        )

    assert all(call[0] != "update_issue" for call in review.calls)


@pytest.mark.asyncio
async def test_valid_fae_move_history_preserves_issue_write():
    review = RecordingIssueReview()
    before = move_snapshot(issue_id=ISSUE_ID)
    after = move_snapshot(issue_id=TARGET_ID)
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
        "links": [],
        "events": [{
            "event_type": "link_moved_out", "before": before, "after": after
        }],
    }
    review.details[TARGET_ID] = {
        "issue": {"id": TARGET_ID, "agent_id": FAE_AGENT_ID},
        "links": [{**after, "active": True}],
        "events": [{
            "event_type": "link_moved_in", "before": before, "after": after
        }],
    }

    await service_for(review=review).update_issue(
        ISSUE_ID, Payload(row_version=1, reason="edit"), actor="corp:owner"
    )

    assert any(call[0] == "update_issue" for call in review.calls)


@pytest.mark.asyncio
async def test_move_event_foreign_target_blocks_semantic_review_writer():
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
        "events": [{
            "event_type": "link_moved_out",
            "before": move_snapshot(issue_id=ISSUE_ID),
            "after": move_snapshot(issue_id=TARGET_ID),
        }],
    }
    review.details[TARGET_ID] = {
        "issue": {"id": TARGET_ID, "agent_id": "ai-admin-agent"}
    }
    payload = Payload(
        verdict="passed", method="human_fae", reviewer="corp:owner", reason="review"
    )

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).semantic_review(
            REPLAY_ID, payload, actor="corp:owner"
        )

    assert all(call[0] != "semantic_review" for call in review.calls)


@pytest.mark.asyncio
async def test_replay_whose_link_moved_away_blocks_semantic_review_writer():
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID},
        "links": [],
        "replays": [{"id": REPLAY_ID, "issue_link_id": LINK_ID}],
    }
    payload = Payload(
        verdict="passed",
        method="human_fae",
        reviewer="corp:owner",
        reason="review",
    )

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).semantic_review(
            REPLAY_ID, payload, actor="corp:owner"
        )

    assert all(call[0] != "semantic_review" for call in review.calls)


@pytest.mark.asyncio
async def test_issue_inbox_propagates_projection_unavailable():
    class UnavailableInboxReview(RecordingIssueReview):
        async def inbox(self, *, agent_id, limit, offset):
            raise ReviewRepositoryError("replica inbox scope unavailable")

    with pytest.raises(ReviewRepositoryError, match="scope unavailable"):
        await service_for(review=UnavailableInboxReview()).issue_inbox(
            limit=100, offset=0
        )


@pytest.mark.asyncio
async def test_foreign_canonical_target_fails_before_issue_write():
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID,
                  "canonical_issue_id": TARGET_ID}
    }
    review.details[TARGET_ID] = {
        "issue": {"id": TARGET_ID, "agent_id": "ai-admin-agent"}
    }

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).update_issue(
            ISSUE_ID, Payload(row_version=1, reason="edit"), actor="corp:owner"
        )

    assert all(call[0] != "update_issue" for call in review.calls)


@pytest.mark.asyncio
async def test_canonical_cycle_fails_closed_before_issue_write():
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": FAE_AGENT_ID,
                  "canonical_issue_id": TARGET_ID}
    }
    review.details[TARGET_ID] = {
        "issue": {"id": TARGET_ID, "agent_id": FAE_AGENT_ID,
                  "canonical_issue_id": ISSUE_ID}
    }

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).update_issue(
            ISSUE_ID, Payload(row_version=1, reason="edit"), actor="corp:owner"
        )

    assert all(call[0] != "update_issue" for call in review.calls)


@pytest.mark.asyncio
async def test_two_hundred_turn_summaries_use_one_batch_and_one_detail_per_issue():
    keys = [f"fae:turn-{index}" for index in range(200)]
    repository = StaticRepository()
    review = RecordingIssueReview()
    review.summary_issue_id = ISSUE_ID

    summaries = await service_for(repository=repository, review=review).turn_summaries(keys)

    assert len(summaries) == 200
    assert repository.batch_calls == [keys]
    assert review.detail_loads == [ISSUE_ID]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        ("update_issue", Payload(row_version=1, reason="edit")),
        ("mark_fix_ready", Payload(row_version=1, reason="ready")),
        ("add_evidence", Payload(evidence_type="commit", reference="abc", reason="proof")),
        ("start_replay", Payload(issue_link_id=LINK_ID, idempotency_key="one")),
        (
            "set_disposition",
            Payload(
                disposition="actionable",
                canonical_issue_id=None,
                owner=None,
                row_version=1,
                reason="triage",
            ),
        ),
    ],
)
async def test_cross_agent_source_issue_is_hidden_before_each_issue_write(
    operation, payload
):
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": "ai-admin-agent"}
    }
    service = service_for(review=review)

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await getattr(service, operation)(ISSUE_ID, payload, actor="fae:owner")

    assert all(call[0] != operation for call in review.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["move_link", "merge_issue"])
async def test_cross_agent_target_issue_is_hidden_before_move_or_merge_write(
    operation,
):
    review = RecordingIssueReview()
    review.details[TARGET_ID] = {
        "issue": {"id": TARGET_ID, "agent_id": "ai-admin-agent"}
    }
    payload = Payload(target_issue_id=TARGET_ID, row_version=1, reason="group")
    service = service_for(review=review)

    with pytest.raises(ReviewNotFound, match="issue not found"):
        if operation == "move_link":
            await service.move_link(
                ISSUE_ID, LINK_ID, payload, actor="fae:owner"
            )
        else:
            await service.merge_issue(ISSUE_ID, payload, actor="fae:owner")

    assert all(call[0] != operation for call in review.calls)


@pytest.mark.asyncio
async def test_duplicate_disposition_scope_checks_canonical_issue_before_write():
    review = RecordingIssueReview()
    review.details[TARGET_ID] = {
        "issue": {"id": TARGET_ID, "agent_id": "ai-admin-agent"}
    }
    payload = Payload(
        disposition="duplicate",
        canonical_issue_id=TARGET_ID,
        owner=None,
        row_version=1,
        reason="duplicate",
    )

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await service_for(review=review).set_disposition(
            ISSUE_ID, payload, actor="fae:owner"
        )

    assert all(call[0] != "set_disposition" for call in review.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner_lookup", "operation", "entity_id", "payload"),
    [
        (
            "evidence_owner",
            "verify_evidence",
            EVIDENCE_ID,
            Payload(reason="verify"),
        ),
        (
            "replay_owner",
            "semantic_review",
            REPLAY_ID,
            Payload(
                verdict="passed",
                method="human_fae",
                reviewer="fae:owner",
                reason="review",
            ),
        ),
    ],
)
async def test_cross_agent_evidence_and_replay_are_hidden_before_review_write(
    owner_lookup, operation, entity_id, payload
):
    review = RecordingIssueReview()
    review.details[ISSUE_ID] = {
        "issue": {"id": ISSUE_ID, "agent_id": "ai-admin-agent"}
    }
    service = service_for(review=review)

    with pytest.raises(ReviewNotFound, match="issue not found"):
        await getattr(service, operation)(entity_id, payload, actor="fae:owner")

    assert review.calls[0][0] == owner_lookup
    assert all(call[0] != operation for call in review.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner_attribute", "operation", "entity_id", "payload"),
    [
        ("evidence_owner", "verify_evidence", EVIDENCE_ID, Payload(reason="verify")),
        (
            "replay_owner",
            "semantic_review",
            REPLAY_ID,
            Payload(
                verdict="passed",
                method="human_fae",
                reviewer="fae:owner",
                reason="review",
            ),
        ),
    ],
)
async def test_unknown_evidence_and_replay_are_hidden_before_review_write(
    owner_attribute, operation, entity_id, payload
):
    review = RecordingIssueReview()
    setattr(review, owner_attribute, None)
    service = service_for(review=review)

    with pytest.raises(ReviewNotFound):
        await getattr(service, operation)(entity_id, payload, actor="fae:owner")

    assert all(call[0] != operation for call in review.calls)


@pytest.mark.asyncio
async def test_list_sessions_overrides_browser_agent_and_source():
    observability = RecordingObservability()
    service = service_for(observability=observability)
    supplied = SessionFilters(
        agent_id="ai-admin-agent", source_kind="admin", query="Gemini"
    )

    await service.list_sessions(supplied, limit=50, offset=0)

    assert observability.filters is not None
    assert observability.filters.agent_id == FAE_AGENT_ID
    assert observability.filters.source_kind == FAE_SOURCE_KIND
    assert observability.filters.query == "Gemini"


@pytest.mark.asyncio
async def test_non_fae_session_is_hidden_as_missing():
    service = service_for(observability=RecordingObservability(admin_session()))

    assert await service.get_session("admin:session-1") is None


@pytest.mark.asyncio
async def test_overview_composes_available_operational_and_review_sections():
    repository = StaticRepository()
    review = StaticReview()

    overview = await service_for(repository=repository, review=review).overview(NOW)

    assert repository.period == (LOCAL_PERIOD_START, LOCAL_PERIOD_END)
    assert review.agent_id == FAE_AGENT_ID
    assert overview.summary.state.status == "available"
    assert overview.summary.data.session_count == 12
    assert overview.summary.data.open_issue_count == 2
    assert overview.attention.items[0].session_key == "fae:session-1"
    assert overview.trends.points[0].negative_turns == 2
    assert overview.issues.statuses == {"pending_triage": 2, "closed": 1, "duplicate": 1, "not_actionable": 1, "wont_fix": 1}
    assert overview.freshness.status == "fresh"
    assert overview.reports.state.error_code == "reports_not_integrated"


@pytest.mark.asyncio
async def test_review_failure_does_not_remove_operational_summary():
    overview = await service_for(review=UnavailableReview()).overview(NOW)

    assert overview.summary.state.status == "available"
    assert overview.summary.data.open_issue_count is None
    assert overview.issues.state.status == "unavailable"
    assert overview.reports.state.error_code == "reports_not_integrated"


@pytest.mark.asyncio
async def test_operational_failure_preserves_available_issue_state():
    overview = await service_for(
        repository=StaticRepository(RuntimeError("aggregate unavailable"))
    ).overview(NOW)

    assert overview.summary.state.status == "unavailable"
    assert overview.issues.state.status == "available"
    assert overview.summary.data is None


@pytest.mark.asyncio
async def test_overview_marks_old_operational_data_stale():
    overview = await service_for(
        repository=StaticRepository(fae_snapshot(data_as_of=NOW - timedelta(hours=37)))
    ).overview(NOW)

    assert overview.freshness.status == "stale"
    assert overview.freshness.data_as_of == NOW - timedelta(hours=37)


@pytest.mark.asyncio
async def test_overview_marks_missing_operational_data_as_unavailable():
    overview = await service_for(
        repository=StaticRepository(fae_snapshot(data_as_of=None))
    ).overview(NOW)

    assert overview.freshness.status == "unavailable"
    assert overview.freshness.data_as_of is None


@pytest.mark.asyncio
async def test_overview_treats_exactly_36_hour_old_data_as_fresh():
    overview = await service_for(
        repository=StaticRepository(fae_snapshot(data_as_of=NOW - timedelta(hours=36)))
    ).overview(NOW)

    assert overview.freshness.status == "fresh"


@pytest.mark.asyncio
async def test_overview_api_rejects_naive_period_start():
    overview = await service_for().overview(NOW)
    payload = overview.model_dump()
    payload["period_start"] = overview.period_start.replace(tzinfo=None)

    with pytest.raises(ValidationError):
        FaeOverview.model_validate(payload)


@pytest.mark.asyncio
async def test_overview_api_rejects_naive_freshness_timestamp():
    overview = await service_for().overview(NOW)
    payload = overview.model_dump()
    payload["freshness"]["data_as_of"] = NOW.replace(tzinfo=None)

    with pytest.raises(ValidationError):
        FaeOverview.model_validate(payload)


@pytest.mark.asyncio
async def test_overview_api_rejects_naive_attention_timestamp():
    overview = await service_for().overview(NOW)
    payload = overview.model_dump()
    payload["attention"]["items"][0]["last_active_at"] = NOW.replace(tzinfo=None)

    with pytest.raises(ValidationError):
        FaeOverview.model_validate(payload)
