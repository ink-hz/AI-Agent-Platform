from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import psycopg
import pytest

from app.agent_brain.authorization import AgentUseAuthorization
from app.agent_catalog import CANONICAL_AGENT_IDS, load_agent_catalog
from app.agent_catalog.routes import build_agent_catalog_router
from app.control_plane.models import AuthContext, Role


WEB_PATHS = {
    "ai-fae-agent": "/fae/",
    "ai-daq-fae-agent": "/daq/",
    "hr-bot": "/hr/",
    "voc": "/voc/",
    "ai-admin-agent": "/office/?view=services",
}
MARKETING_IDS = tuple(
    agent_id for agent_id in CANONICAL_AGENT_IDS if agent_id.startswith("marketing-")
)


class _DecisionConnection:
    """Replace only the database boundary; use the real catalog authorization."""

    def __init__(self, allowed_ids):
        self.allowed_ids = set(allowed_ids)
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.queries.append((sql, params))
        return self

    def fetchall(self):
        _owner, requested_ids = self.queries[-1][1]
        return [
            {"agent_id": agent_id, "allowed": agent_id in self.allowed_ids}
            for agent_id in requested_ids
        ]


def _client(allowed_ids=(), *, context=None, unavailable=False):
    decisions = _DecisionConnection(allowed_ids)

    def connect(*_args, **_kwargs):
        if unavailable:
            raise psycopg.OperationalError("test database unavailable")
        return decisions

    authorization = AgentUseAuthorization(
        "postgresql://platform_control_app:secret@127.0.0.1/agent_platform_control",
        connect=connect,
    )
    app = FastAPI()

    @app.middleware("http")
    async def test_identity_boundary(request, call_next):
        # Inject an already authenticated context, without claiming SSO coverage.
        if context is not None:
            request.state.auth_context = context
        return await call_next(request)

    app.include_router(build_agent_catalog_router(authorization))
    return TestClient(app), decisions


def _auth(role=Role.MEMBER):
    return AuthContext(uuid4(), role, uuid4(), False)


def _assert_no_store(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_catalog_requires_authentication_before_consulting_grants():
    client, decisions = _client(CANONICAL_AGENT_IDS)

    response = client.get("/api/v1/catalog/agents")

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication required"}
    _assert_no_store(response)
    assert decisions.queries == []


@pytest.mark.parametrize("role", list(Role))
def test_web_projection_contains_only_cards_permitted_for_the_authenticated_user(role):
    context = _auth(role)
    client, decisions = _client({"hr-bot", "marketing-gtm-bot"}, context=context)

    response = client.get("/api/v1/catalog/agents")

    assert response.status_code == 200
    _assert_no_store(response)
    cards = response.json()["agents"]
    assert [card["agent_id"] for card in cards] == ["hr-bot", "marketing-gtm-bot"]
    assert cards[0]["web_entry"] == {"kind": "independent_service", "path": "/hr/"}
    assert cards[1]["web_entry"] is None
    assert len(decisions.queries) == 1
    sql, params = decisions.queries[0]
    assert "platform_control.resolve_agent_use_decision_v41" in sql
    assert params == (context.internal_user_id, list(CANONICAL_AGENT_IDS))
    assert isinstance(params[0], UUID)


def test_no_grants_returns_a_real_empty_catalog():
    client, _decisions = _client(context=_auth())

    response = client.get("/api/v1/catalog/agents")

    assert response.status_code == 200
    assert response.json() == {"agents": []}
    _assert_no_store(response)


def test_grant_service_failure_is_explicit_and_never_publishes_entries():
    client, _decisions = _client(CANONICAL_AGENT_IDS, context=_auth(), unavailable=True)

    response = client.get("/api/v1/catalog/agents")

    assert response.status_code == 503
    assert response.json() == {"detail": "Agent catalog unavailable"}
    _assert_no_store(response)


@pytest.mark.parametrize(("agent_id", "path"), WEB_PATHS.items())
def test_authenticated_catalog_publishes_exact_native_web_path(agent_id, path):
    client, _decisions = _client({agent_id}, context=_auth())

    response = client.get("/api/v1/catalog/agents")

    assert response.status_code == 200
    assert len(response.json()["agents"]) == 1
    card = response.json()["agents"][0]
    assert card["agent_id"] == agent_id
    assert card["web_entry"] == {"kind": "independent_service", "path": path}


@pytest.mark.parametrize("agent_id", MARKETING_IDS)
def test_metabot_marketing_cards_have_explicit_null_web_entry(agent_id):
    client, _decisions = _client({agent_id}, context=_auth())

    response = client.get("/api/v1/catalog/agents")

    assert response.status_code == 200
    card = response.json()["agents"][0]
    assert card["agent_id"] == agent_id
    assert card["web_entry"] is None
    assert card["adapter_kind"] == "metabot_local"
    assert card["dispatchable"] is True


def test_web_projection_preserves_every_existing_execution_field():
    original_cards = load_agent_catalog()
    original_payloads = [card.model_dump(mode="json") for card in original_cards]
    client, _decisions = _client(CANONICAL_AGENT_IDS, context=_auth())

    response = client.get("/api/v1/catalog/agents")

    assert response.status_code == 200
    public_cards = response.json()["agents"]
    assert len(public_cards) == len(original_cards)
    for public_card, card in zip(public_cards, original_cards, strict=True):
        assert "web_entry" in public_card
        legacy_payload = {
            key: value for key, value in public_card.items() if key != "web_entry"
        }
        assert legacy_payload == {
            **card.model_dump(mode="json"), "dispatchable": card.dispatchable,
        }
    hr = next(card for card in public_cards if card["agent_id"] == "hr-bot")
    assert hr["web_entry"] == {"kind": "independent_service", "path": "/hr/"}
    assert hr["workspace_url"] is None
    assert hr["adapter_id"] == "metabot-core-chat"
    assert hr["adapter_kind"] == "metabot_local"
    assert hr["interaction_modes"] == ["direct_chat", "brain_delegation"]
    assert hr["execution_pool"] == "metabot_local"
    assert hr["pool_concurrency"] == 1
    assert hr["dispatchable"] is True
    assert [card.model_dump(mode="json") for card in original_cards] == original_payloads


@pytest.mark.parametrize(
    "unpublished_id",
    [
        "unknown-agent", "fae-bot", "/hr/", "https://evil.example/",
        "//evil.example/", "hr-bot/../evil",
    ],
)
def test_web_publication_rejects_unknown_ids_urls_and_aliases(unpublished_id):
    from app.agent_catalog.web_entries import web_entry_for

    assert web_entry_for(unpublished_id) is None


def test_mutating_a_projection_does_not_change_the_publication_mapping():
    from app.agent_catalog.web_entries import web_entry_for

    entry = web_entry_for("hr-bot")
    entry["path"] = "https://evil.example/"

    assert web_entry_for("hr-bot") == {"kind": "independent_service", "path": "/hr/"}
