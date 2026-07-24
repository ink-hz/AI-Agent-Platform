from datetime import datetime, timedelta, timezone

import pytest

from app.observability.models import SessionFilters
from app.observability.repository import PsycopgObservabilityRepository
from app.fleet.catalog import AgentCatalog, AgentProfile


NOW = datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc)


def visibility_catalog() -> AgentCatalog:
    return AgentCatalog(
        {
            "business-agent": AgentProfile(
                id="business-agent", name="Business Agent", domain="Operations",
                description="Business work", glyph="BA", accent="blue",
                visibility="business",
            ),
            "test-bot": AgentProfile(
                id="test-bot", name="Test", domain="System",
                description="Integration testing", glyph="T", accent="testing",
                visibility="system",
            ),
        },
        {},
        set(),
    )


class FakeConnect:
    def __init__(self, responses):
        self.responses = list(responses)
        self.executed: list[tuple[str, tuple | None]] = []
        self.current = []

    def __call__(self, *_args, **_kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        self.current = self.responses.pop(0)
        return self

    def fetchall(self):
        return self.current

    def fetchone(self):
        return self.current[0] if self.current else None


def test_list_agents_includes_catalog_agents_without_conversations() -> None:
    fake = FakeConnect([[]])
    catalog = AgentCatalog(
        {
            "quiet-agent": AgentProfile(
                id="quiet-agent", name="Quiet Agent", domain="Operations",
                description="Ready for its first conversation", glyph="QA", accent="blue",
                visibility="business",
            )
        },
        {},
        set(),
    )
    repository = PsycopgObservabilityRepository(
        "postgresql://unused", connect=fake, catalog=catalog,
    )

    agents = repository.list_agents()

    assert len(agents) == 1
    assert agents[0].id == "quiet-agent"
    assert agents[0].session_count == 0
    assert agents[0].total_turns == 0
    assert agents[0].visibility == "business"


def test_sync_status_preserves_latest_run_and_true_last_success() -> None:
    last_success_at = NOW - timedelta(hours=35)
    failed_at = NOW - timedelta(minutes=1)
    fake = FakeConnect(
        [[
            {
                "source_kind": "fae",
                "status": "failed",
                "started_at": failed_at - timedelta(minutes=1),
                "completed_at": failed_at,
                "last_success_at": last_success_at,
                "source_counts": {},
                "applied_counts": {},
                "validation": {},
                "error_summary": "remote unavailable",
            }
        ]]
    )
    repository = PsycopgObservabilityRepository(
        "postgresql://unused", connect=fake, now=lambda: NOW
    )

    status = repository.get_sync_status()[0]

    assert status.status == "failed"
    assert status.completed_at == failed_at
    assert status.last_success_at == last_success_at
    assert status.freshness == "fresh"


def test_sync_status_remains_compatible_before_view_upgrade() -> None:
    fake = FakeConnect(
        [[
            {
                "source_kind": "admin",
                "status": "succeeded",
                "started_at": NOW - timedelta(minutes=2),
                "completed_at": NOW - timedelta(minutes=1),
                "source_counts": {},
                "applied_counts": {},
                "validation": {},
                "error_summary": None,
            }
        ]]
    )
    repository = PsycopgObservabilityRepository(
        "postgresql://unused", connect=fake, now=lambda: NOW
    )

    status = repository.get_sync_status()[0]

    assert status.last_success_at == status.completed_at
    assert status.freshness == "fresh"


@pytest.mark.parametrize("run_status", ["running", "failed"])
def test_old_sync_view_non_success_exposes_missing_history(run_status) -> None:
    fake = FakeConnect(
        [[
            {
                "source_kind": "fae",
                "status": run_status,
                "started_at": NOW - timedelta(minutes=2),
                "completed_at": (
                    NOW - timedelta(minutes=1)
                    if run_status == "failed"
                    else None
                ),
                "source_counts": {},
                "applied_counts": {},
                "validation": {},
                "error_summary": None,
            }
        ]]
    )
    repository = PsycopgObservabilityRepository(
        "postgresql://unused", connect=fake, now=lambda: NOW
    )

    status = repository.get_sync_status()[0]

    assert status.last_success_at is None
    assert status.freshness == "stale"


def test_default_session_list_uses_business_agent_allowlist() -> None:
    fake = FakeConnect([[{"count": 0}], []])
    repository = PsycopgObservabilityRepository(
        "postgresql://unused", connect=fake, catalog=visibility_catalog(),
    )

    repository.list_sessions(SessionFilters(), limit=25, offset=0)

    sql_text = " ".join(statement for statement, _ in fake.executed).lower()
    assert "s.agent_id = any(%s)" in sql_text
    assert fake.executed[0][1] == (["business-agent"],)
    assert fake.executed[1][1] == (["business-agent"], 25, 0)


def test_explicit_system_agent_session_list_bypasses_business_allowlist() -> None:
    fake = FakeConnect([[{"count": 0}], []])
    repository = PsycopgObservabilityRepository(
        "postgresql://unused", connect=fake, catalog=visibility_catalog(),
    )

    repository.list_sessions(
        SessionFilters(agent_id="test-bot"), limit=25, offset=0,
    )

    sql_text = " ".join(statement for statement, _ in fake.executed).lower()
    assert "s.agent_id = %s" in sql_text
    assert "any(%s)" not in sql_text
    assert fake.executed[0][1] == ("test-bot",)
    assert fake.executed[1][1] == ("test-bot", 25, 0)


def test_list_sessions_uses_canonical_view_filters_and_pagination() -> None:
    fake = FakeConnect(
        [
            [{"count": 1}],
            [
                {
                    "session_key": "fae:session-1",
                    "agent_id": "ai-fae-agent",
                    "source_kind": "fae",
                    "channel": "fae",
                    "title": "Gemini 335L 如何排查？",
                    "created_at": NOW,
                    "last_active_at": NOW,
                    "turn_count": 2,
                    "feedback_count": 1,
                    "review_count": 1,
                    "latest_outcome": "resolved",
                    "source_synced_at": NOW,
                    "participant_count": None,
                    "primary_sender_name": None,
                    "primary_sender_department": None,
                    "sender_identity_status": "unavailable",
                }
            ],
        ]
    )
    repository = PsycopgObservabilityRepository(
        "postgresql://unused", connect=fake, now=lambda: NOW
    )

    page = repository.list_sessions(
        SessionFilters(agent_id="ai-fae-agent", query="Gemini"),
        limit=25,
        offset=0,
    )

    sql_text = " ".join(statement for statement, _ in fake.executed).lower()
    assert "platform_read.sessions" in sql_text
    assert "platform_read.turns" in sql_text
    assert "limit %s offset %s" in sql_text
    assert page.total == 1
    assert page.items[0].title == "Gemini 335L 如何排查？"
    assert page.items[0].freshness == "fresh"
    assert page.items[0].sender_identity_status == "unavailable"


def test_metabot_session_and_turn_map_safe_sender_presentation_fields() -> None:
    repository = PsycopgObservabilityRepository(
        "postgresql://unused", connect=FakeConnect([]), now=lambda: NOW,
    )
    session = repository._session_summary({
        "session_key": "metabot:hr-bot:session-1",
        "agent_id": "hr-bot",
        "source_kind": "metabot",
        "channel": "feishu",
        "title": "Question",
        "created_at": NOW,
        "last_active_at": NOW,
        "turn_count": 1,
        "feedback_count": 0,
        "review_count": 0,
        "latest_outcome": None,
        "source_synced_at": None,
        "participant_count": 3,
        "primary_sender_name": "Lina",
        "primary_sender_department": "Marketing",
        "sender_identity_status": "resolved",
    })
    turn = repository._turn_detail({
        "turn_key": "metabot:hr-bot:turn-1",
        "session_key": session.session_key,
        "agent_id": "hr-bot",
        "source_kind": "metabot",
        "turn_index": 0,
        "question": "Question",
        "answer": "Answer",
        "created_at": NOW,
        "trace_key": None,
        "outcome": None,
        "fallback_used": False,
        "duration_ms": None,
        "sources": [],
        "sender_name": "Lina",
        "sender_department": None,
        "sender_identity_status": "name_only",
        "details": {},
    }, [], [], [])

    assert session.participant_count == 3
    assert session.primary_sender_name == "Lina"
    assert session.primary_sender_department == "Marketing"
    assert session.sender_identity_status == "resolved"
    assert turn.sender_name == "Lina"
    assert turn.sender_department is None
    assert turn.sender_identity_status == "name_only"
    dumped = {**session.model_dump(), **turn.model_dump()}
    assert "open_id" not in dumped
    assert "union_id" not in dumped
    assert "staff_id" not in dumped


def test_get_fae_trace_maps_stage_and_span_without_losing_hierarchy() -> None:
    fake = FakeConnect(
        [
            [
                {
                    "trace_key": "fae:trace-1",
                    "turn_key": "fae:turn-1",
                    "agent_id": "ai-fae-agent",
                    "source_kind": "fae",
                    "status": "completed",
                    "started_at": NOW,
                    "completed_at": NOW,
                    "duration_ms": 1200,
                    "engine": None,
                    "backend": None,
                    "model": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "cost_usd": None,
                    "error_class": None,
                    "error_message": None,
                    "detail_availability": "available",
                    "source_synced_at": NOW,
                    "details": {},
                }
            ],
            [
                {
                    "step_key": "fae:stage:1",
                    "trace_key": "fae:trace-1",
                    "kind": "stage",
                    "name": "schema_extract",
                    "status": "completed",
                    "parent_step_key": None,
                    "seq": 1,
                    "started_at": NOW,
                    "duration_ms": 20,
                    "input_summary": {},
                    "output_summary": {},
                    "safe_metadata": {},
                    "error_summary": None,
                },
                {
                    "step_key": "fae:span:child",
                    "trace_key": "fae:trace-1",
                    "kind": "span",
                    "name": "llm_call",
                    "status": "completed",
                    "parent_step_key": "fae:span:root",
                    "seq": None,
                    "started_at": NOW,
                    "duration_ms": 1100,
                    "input_summary": {"messages": 2},
                    "output_summary": {"text_len": 300},
                    "safe_metadata": {},
                    "error_summary": None,
                },
            ],
        ]
    )
    repository = PsycopgObservabilityRepository("postgresql://unused", connect=fake)

    trace = repository.get_trace("fae:turn-1")

    assert trace is not None
    assert trace.detail_availability == "available"
    assert [step.kind for step in trace.steps] == ["stage", "span"]
    assert trace.steps[1].parent_step_key == "fae:span:root"


def test_get_admin_trace_marks_engineering_detail_unavailable() -> None:
    fake = FakeConnect(
        [
            [
                {
                    "trace_key": "admin:trace-1",
                    "turn_key": "admin:turn-1",
                    "agent_id": "ai-admin-agent",
                    "source_kind": "admin",
                    "status": "completed",
                    "started_at": NOW,
                    "completed_at": NOW,
                    "duration_ms": 500,
                    "engine": None,
                    "backend": None,
                    "model": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "cost_usd": None,
                    "error_class": None,
                    "error_message": None,
                    "detail_availability": "unavailable",
                    "source_synced_at": NOW,
                    "details": {},
                }
            ],
            [],
        ]
    )
    repository = PsycopgObservabilityRepository("postgresql://unused", connect=fake)

    trace = repository.get_trace("admin:turn-1")

    assert trace is not None
    assert trace.detail_availability == "unavailable"
    assert trace.steps == []


def test_latest_runtime_observation_reads_only_bounded_trace_facts() -> None:
    fake = FakeConnect([[
        {
            "agent_id": "marketing-inbound-bot",
            "source_kind": "metabot",
            "engine": "claude",
            "backend": "pty",
            "model": "claude-opus-4-8",
            "observed_at": NOW,
        }
    ]])
    repository = PsycopgObservabilityRepository(
        "postgresql://unused", connect=fake, now=lambda: NOW,
    )

    observation = repository.get_latest_runtime_observation(
        "marketing-inbound-bot"
    )

    assert observation is not None
    assert observation.model == "claude-opus-4-8"
    assert observation.backend == "pty"
    assert observation.observed_at == NOW
    statement, params = fake.executed[0]
    normalized = " ".join(statement.lower().split())
    assert "from platform_read.traces" in normalized
    assert "coalesce(completed_at, started_at) desc" in normalized
    assert "limit 1" in normalized
    assert "question" not in normalized
    assert "answer" not in normalized
    assert params == ("marketing-inbound-bot",)


def test_latest_runtime_observation_is_none_without_usable_trace() -> None:
    repository = PsycopgObservabilityRepository(
        "postgresql://unused", connect=FakeConnect([[]]), now=lambda: NOW,
    )

    assert repository.get_latest_runtime_observation("quiet-agent") is None


def test_flywheel_pending_review_counts_unreviewed_negative_feedback() -> None:
    fake = FakeConnect(
        [[{
            "feedback_total": 25,
            "negative_feedback": 24,
            "pending_reviews": 23,
            "evaluation_candidates": 0,
            "knowledge_tasks": 0,
            "qa_candidates": 0,
        }]]
    )
    repository = PsycopgObservabilityRepository("postgresql://unused", connect=fake)

    overview = repository.get_flywheel_overview()

    statement = fake.executed[0][0].lower()
    assert "left join platform_read.reviews" in statement
    assert "f.sentiment='negative'" in statement
    assert "r.review_key is null" in statement
    assert overview.pending_reviews == 23
