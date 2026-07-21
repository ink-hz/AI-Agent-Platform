from datetime import datetime, timezone

from app.observability.models import SessionFilters
from app.observability.repository import PsycopgObservabilityRepository
from app.fleet.catalog import AgentCatalog, AgentProfile


NOW = datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc)


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
                }
            ],
        ]
    )
    repository = PsycopgObservabilityRepository("postgresql://unused", connect=fake)

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
