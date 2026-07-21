import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.observability.models import (
    AgentSummary,
    Page,
    SessionDetail,
    SessionSummary,
    TraceDetail,
)


NOW = datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc)


class StaticObservabilityService:
    async def list_agents(self):
        return [AgentSummary(
            id="ai-fae-agent", name="AI FAE", domain="Field Application Engineering",
            description="Production engineering Agent", glyph="FAE", accent="cyan",
            source_kind="fae", deployment="Alibaba Cloud", session_count=168,
            total_turns=236, last_activity_at=NOW, last_synced_at=NOW, freshness="fresh",
        )]

    async def get_agent(self, agent_id):
        return None

    async def list_sessions(self, filters, limit, offset):
        return Page[SessionSummary](
            items=[
                SessionSummary(
                    session_key="fae:session-1",
                    agent_id="ai-fae-agent",
                    source_kind="fae",
                    channel="fae",
                    title="请保留这段中文原文",
                    created_at=NOW,
                    last_active_at=NOW,
                    turn_count=1,
                    feedback_count=0,
                    review_count=0,
                    latest_outcome="resolved",
                    source_synced_at=NOW,
                    freshness="fresh",
                )
            ],
            total=1,
            limit=limit,
            offset=offset,
        )

    async def get_session(self, session_key):
        if session_key == "missing":
            return None
        summary = (await self.list_sessions(None, 1, 0)).items[0]
        return SessionDetail(**summary.model_dump(), turns=[])

    async def get_trace(self, turn_key):
        if turn_key == "missing":
            return None
        return TraceDetail(
            trace_key="admin:trace-1",
            turn_key=turn_key,
            agent_id="ai-admin-agent",
            source_kind="admin",
            status="completed",
            started_at=NOW,
            completed_at=NOW,
            duration_ms=500,
            detail_availability="unavailable",
            source_synced_at=NOW,
            details={},
            steps=[],
        )

    async def flywheel_overview(self):
        return {
            "feedback_total": 0,
            "negative_feedback": 0,
            "pending_reviews": 0,
            "evaluation_candidates": 0,
            "knowledge_tasks": 0,
            "qa_candidates": 0,
        }

    async def list_improvement_items(self, filters, limit, offset):
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    async def sync_status(self):
        return []


def make_client(tmp_path) -> TestClient:
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"bots": []}), encoding="utf-8")
    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        observability_service=StaticObservabilityService(),
    )
    return TestClient(app)


def test_sessions_api_preserves_original_language_and_pagination(tmp_path) -> None:
    response = make_client(tmp_path).get("/api/sessions?agent_id=ai-fae-agent&limit=25")

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["title"] == "请保留这段中文原文"
    assert body["limit"] == 25
    assert body["total"] == 1


def test_agents_api_uses_unified_observability_model(tmp_path) -> None:
    response = make_client(tmp_path).get("/api/agents")

    assert response.status_code == 200
    assert response.json()[0]["source_kind"] == "fae"
    assert response.json()[0]["session_count"] == 168


def test_session_and_trace_missing_return_404(tmp_path) -> None:
    client = make_client(tmp_path)

    assert client.get("/api/sessions/missing").status_code == 404
    assert client.get("/api/turns/missing/trace").status_code == 404


def test_admin_trace_exposes_unavailable_instead_of_empty_success(tmp_path) -> None:
    response = make_client(tmp_path).get("/api/turns/admin%3Aturn-1/trace")

    assert response.status_code == 200
    assert response.json()["detail_availability"] == "unavailable"


def test_session_limit_is_bounded(tmp_path) -> None:
    response = make_client(tmp_path).get("/api/sessions?limit=101")

    assert response.status_code == 422


def test_session_source_kind_is_validated_by_fastapi(tmp_path) -> None:
    response = make_client(tmp_path).get("/api/sessions?source_kind=legacy")

    assert response.status_code == 422
