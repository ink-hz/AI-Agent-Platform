import json
import textwrap

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


REGISTRY = textwrap.dedent(
    """
    version: 1
    agents:
      - id: fae
        name: "AI FAE Agent"
        domain: "技术支持"
        entry_url: "http://fae/app/"
        health: {url: "http://fae/health", type: "fae"}
        api_base: "http://fae"
      - id: admin
        name: "AI ADMIN Agent"
        domain: "行政"
        entry_url: "http://admin/app/"
        health: {url: "http://admin/health", type: "admin"}
    """
)


@pytest.fixture()
def client(tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_text(REGISTRY, encoding="utf-8")
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "bots": [
                    {
                        "name": "hr-bot",
                        "workdir": "/runtime/hr-bot",
                        "instance": {"pm2Name": "metabot-hr", "apiPort": 9101},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        registry_path=str(path),
        cluster_contract_path=str(contract_path),
        start_poller=False,
    )
    return TestClient(app)


def test_platform_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_agents_hides_internal_fields(client):
    response = client.get("/api/agents")
    assert response.status_code == 200
    agents = response.json()
    assert [agent["id"] for agent in agents] == ["fae", "admin"]
    assert "health" not in agents[0]
    assert "api_base" not in agents[0]
    assert agents[0]["entry_url"] == "http://fae/app/"


def test_get_single_agent_and_404(client):
    assert client.get("/api/agents/fae").json()["name"] == "AI FAE Agent"
    assert client.get("/api/agents/missing").status_code == 404


def test_batch_health_returns_list_not_single_agent(client):
    response = client.get("/api/agents/health")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    ids = {status["id"] for status in body}
    assert ids == {"fae", "admin"}
    assert body[0]["online"] is None


def test_per_agent_health_and_404(client):
    assert client.get("/api/agents/fae/health").json()["id"] == "fae"
    assert client.get("/api/agents/missing/health").status_code == 404


def test_cluster_status_returns_snapshot_without_workdir(client):
    response = client.get("/api/cluster/status")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"summary", "source", "instances"}
    assert body["summary"]["total"] == 1
    assert body["instances"][0]["id"] == "hr-bot"
    assert body["instances"][0]["status"] == "checking"
    assert "workdir" not in body["instances"][0]
