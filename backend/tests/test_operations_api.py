import inspect
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.operations.models import BrainTurnOperationsRecord
from app.operations.models import EventFilters, RunHealth
from app.operations.repository import OperationsRepository
from app.operations.routes import brief, events
from app.operations.service import OperationsService

from test_operations_service import INTERVALS, event, record_group_runs


def make_client(
    tmp_path, *, available: bool = True, configure_repository=None
) -> TestClient:
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"bots": []}), encoding="utf-8")
    service = None
    if available:
        now = datetime.now(timezone.utc)
        repository = OperationsRepository(str(tmp_path / "operations.db"))
        repository.migrate()
        if configure_repository is None:
            repository.record_historical(event(occurred_at=now))
            repository.record_historical(
                event(
                    agent_id="test-bot",
                    agent_name="Test Bot",
                    visibility="system",
                    occurred_at=now,
                    fingerprint="system:test-bot",
                )
            )
        else:
            configure_repository(repository, now)
        record_group_runs(repository, at=now)
        service = OperationsService(repository, intervals=INTERVALS)
    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        operations_service=service,
    )
    return TestClient(app)


def test_operations_brief_and_events_use_real_service_and_repository(tmp_path):
    client = make_client(tmp_path)

    brief_response = client.get("/api/operations/brief")
    events_response = client.get("/api/operations/events?limit=1&offset=0")
    system_response = client.get("/api/operations/events?agent_id=test-bot")

    assert brief_response.status_code == 200
    assert brief_response.json()["freshness"]["status"] == "current"
    assert len(brief_response.json()["changes"]) == 1
    assert events_response.json()["total"] == 1
    assert events_response.json()["items"][0]["agent_visibility"] == "business"
    assert system_response.json()["items"][0]["agent_id"] == "test-bot"


def test_brief_api_excludes_newer_attention_families_before_change_limit(tmp_path):
    def configure(repo, now):
        for index, family in enumerate(
            ("usage", "lifecycle", "recovery", "usage", "lifecycle", "usage")
        ):
            repo.record_historical(
                event(
                    event_type=f"approved-{index}",
                    family=family,
                    occurred_at=now - timedelta(minutes=index + 10),
                    fingerprint=f"approved:{index}",
                )
            )
        repo.upsert_active(
            event(
                event_type="runtime_offline",
                family="runtime",
                severity="critical",
                occurred_at=now - timedelta(minutes=1),
                fingerprint="runtime:hr-bot:unavailable",
            )
        )
        repo.record_historical(
            event(
                event_type="fallback",
                family="execution",
                severity="attention",
                occurred_at=now - timedelta(minutes=2),
                fingerprint="execution:hr-bot:fallback",
            )
        )

    response = make_client(
        tmp_path, configure_repository=configure
    ).get("/api/operations/brief")

    assert response.status_code == 200
    payload = response.json()
    assert [item["event_type"] for item in payload["changes"]] == [
        f"approved-{index}" for index in range(5)
    ]
    assert [item["event_type"] for item in payload["attention"]] == [
        "runtime_offline"
    ]


@pytest.mark.parametrize(
    ("group", "error"),
    [
        ("runtime", "RuntimeError: required runtime evidence incomplete"),
        ("sync", "RuntimeError: required sync source coverage incomplete"),
    ],
)
def test_brief_api_cannot_claim_healthy_after_incomplete_required_evaluation(
    tmp_path, group, error
):
    def configure(repo, now):
        repo.record_run(
            RunHealth(
                run_name=group,
                status="failed",
                started_at=now + timedelta(microseconds=1),
                finished_at=now + timedelta(microseconds=1),
                cursor={"observed_at": now.isoformat()},
                error_summary=error,
            )
        )

    response = make_client(
        tmp_path, configure_repository=configure
    ).get("/api/operations/brief")

    assert response.status_code == 200
    payload = response.json()
    assert payload["freshness"]["status"] == "partial"
    assert payload["freshness"]["failed_groups"] == [group]
    assert payload["can_claim_healthy"] is False


def test_operations_event_query_validation_is_owned_by_fastapi(tmp_path):
    client = make_client(tmp_path)

    assert client.get("/api/operations/events?severity=warning").status_code == 422
    assert client.get("/api/operations/events?limit=101").status_code == 422
    assert client.get("/api/operations/events?offset=-1").status_code == 422
    assert (
        client.get("/api/operations/events?date_from=not-a-date").status_code
        == 422
    )


def test_operations_unavailable_is_isolated_to_its_two_routes(tmp_path):
    client = make_client(tmp_path, available=False)

    for path in ("/api/operations/brief", "/api/operations/events"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json() == {"detail": "operations unavailable"}
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/agents").status_code == 200


def test_operations_routes_are_async_and_service_contract_is_synchronous():
    assert inspect.iscoroutinefunction(brief)
    assert inspect.iscoroutinefunction(events)
    assert not inspect.iscoroutinefunction(OperationsService.brief)
    assert not inspect.iscoroutinefunction(OperationsService.list_events)


def test_event_filters_contract_remains_typed():
    filters = EventFilters(severity="critical")
    assert filters.severity == "critical"


def test_conversation_metrics_keep_conversations_turns_and_quality_distinct(
    tmp_path,
):
    client = make_client(tmp_path)
    client.app.state.conversation_repository = SimpleNamespace(
        conversation_metrics=lambda: SimpleNamespace(
            conversations=2,
            multi_turn_conversations=1,
            multi_turn_rate=0.5,
            turns=3,
            completed_turns=2,
            turn_completion_rate=2 / 3,
            missions=3,
            rated_missions=1,
            helpful_missions=1,
            mission_quality_rate=1.0,
        )
    )

    response = client.get("/api/operations/conversation-metrics")

    assert response.status_code == 200
    assert response.json() == {
        "conversations": 2,
        "multi_turn_conversations": 1,
        "multi_turn_rate": 0.5,
        "turns": 3,
        "completed_turns": 2,
        "turn_completion_rate": pytest.approx(2 / 3),
        "missions": 3,
        "rated_missions": 1,
        "helpful_missions": 1,
        "mission_quality_rate": 1.0,
    }


def test_brain_metrics_route_returns_aggregates_without_diagnostic_content(tmp_path):
    client = make_client(tmp_path)
    client.app.state.operations_service._repository.record_brain_turn(
        BrainTurnOperationsRecord(
            turn_id="017ddf96-0bf5-7e0d-a385-9c31ab5e639e",
            model_config_version="brain-opus5-v1",
            step_count=2,
            task_count=1,
            batch_count=1,
            continuous_cache_hit_rate=0.8,
            first_waiting_agents_cache_hit_rate=0.5,
            later_waiting_agents_cache_hit_rate=None,
            input_tokens=100,
            output_tokens=20,
            estimated_cost=0.004,
            outcome="resolved",
            fallback_used=False,
            reason_code=None,
            observed_at=datetime.now(timezone.utc),
        )
    )

    response = client.get("/api/operations/brain-metrics?hours=24")

    assert response.status_code == 200
    assert response.json()["turns"] == 1
    serialized = json.dumps(response.json()).lower()
    assert "turn_id" not in serialized
    assert "content" not in serialized
    assert "thinking" not in serialized


def test_no_http_route_exports_raw_brain_provider_responses(tmp_path):
    client = make_client(tmp_path)
    paths = {
        route.path.lower()
        for route in client.app.routes
        if hasattr(route, "path")
    }

    assert not any(
        any(term in path for term in ("provider-response", "decrypt", "thinking"))
        for path in paths
    )
