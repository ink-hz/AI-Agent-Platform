"""W7 through authenticated HTTP and disposable PostgreSQL; no model calls."""

import hashlib
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.hr_agent.knowledge import KnowledgeReleases
from app.hr_agent.resources import ResourceReader
from app.hr_agent.types import HrAgentProblem
from tests import test_hr_agent_routes as route_fixtures
from tests.test_hr_agent_context import publication

secured = route_fixtures.secured
database = route_fixtures.database
_FIXTURES = (secured, database)


def release(root, revision):
    path = root / "releases" / revision
    path.parent.mkdir(parents=True, exist_ok=True)
    publication(path)
    body = f"# 公开公司研究 {revision}\n\n证据：https://example.com/jobs\n未知：实际编制。\n"
    (path / "research.md").write_text(body)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["release_id"] = revision
    ref = {
        "kind": "intelligence",
        "id": "company:example",
        "revision": revision,
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    manifest["resources"].append(
        {
            "ref": ref,
            "path": "research.md",
            "title": "公开公司研究",
            "description": f"{revision}，单次公开观测，非实际编制",
            "objects": [{"kind": "company", "id": "example"}],
        }
    )
    (path / "manifest.json").write_text(json.dumps(manifest))
    (root / "current.json").write_text(json.dumps({"release_id": revision}))
    return ref, body


def read(client, ref, **params):
    return client.get(
        f"/api/hr/agent/knowledge/{ref['id']}/revisions/{ref['revision']}",
        params={"sha256": ref["sha256"], "kind": "intelligence", **params},
    )


@pytest.mark.parametrize("old_change", ["removed", "tampered"])
def test_w7_current_catalog_keeps_selected_old_body(secured, tmp_path, old_change):
    client, headers, repo, owner = secured
    root = tmp_path / "publications"
    first, first_body = release(root, "b1")
    knowledge = KnowledgeReleases(root)
    service = client.app.state.hr_agent_service
    service.knowledge = knowledge
    resources = ResourceReader(repo, knowledge)
    repo.scope_validator = resources.validate_scope
    repo.release_provider = knowledge.metadata
    repo.release_validator = knowledge.validate_record
    assert read(client, first).json().get("text") == first_body
    second, second_body = release(root, "b2")
    catalog = client.get("/api/hr/agent/knowledge", params={"kind": "intelligence"})
    assert catalog.status_code == 200, catalog.text
    assert [item["ref"] for item in catalog.json()["items"]] == [second]
    assert catalog.json()["items"][0]["objects"] == [
        {"kind": "company", "id": "example"}
    ]
    assert read(client, first).json()["text"] == first_body
    assert read(client, second).json()["text"] == second_body
    # Default method callers keep the original contract.
    assert {
        item["ref"]["kind"]
        for item in client.get("/api/hr/agent/knowledge").json()["items"]
    } == {"method"}
    payload = {
        "thread_id": None,
        "text": "使用选中的旧公司研究",
        "objects": [],
        "references": [first],
        "budget_profile": "test",
    }
    submitted = client.post("/api/hr/agent/works", json=payload, headers=headers)
    assert submitted.status_code == 201, submitted.text
    assert (
        client.post("/api/hr/agent/works", json=payload, headers=headers).status_code
        == 200
    )
    fence = repo.claim("w7-reader", 60)
    assert resources.read_resource(fence, {"ref": first})["text"] == first_body
    assert [
        item["ref"]
        for item in resources.list_resources(fence, {"kinds": ["intelligence"]})[
            "items"
        ]
    ] == [second]
    current, record, _, actual_owner = repo.context_input(fence)
    assert actual_owner == owner
    assert current["references"] == [first]
    assert record["knowledge_release"] == "b2"
    if old_change == "removed":
        shutil.rmtree(root / "releases" / "b1")
        expected_status = 410
    else:
        (root / "releases" / "b1" / "research.md").write_text("changed")
        expected_status = 503
    unavailable = read(client, first)
    assert unavailable.status_code == expected_status, unavailable.text
    assert "text" not in unavailable.json()
    assert read(client, second).json()["text"] == second_body
    with pytest.raises(HrAgentProblem) as failure:
        resources.read_resource(fence, {"ref": first})
    assert failure.value.http_status == expected_status
    repo.cancel(owner, submitted.json()["work_id"], "test complete", uuid4())


def test_intelligence_http_auth_and_closed_kind(secured, tmp_path):
    client, _, _, _ = secured
    ref, _ = release(tmp_path / "publications", "b1")
    client.app.state.hr_agent_service.knowledge = KnowledgeReleases(
        tmp_path / "publications"
    )
    for kind in ("candidate", "material", "result", "all"):
        assert (
            client.get("/api/hr/agent/knowledge", params={"kind": kind}).status_code
            == 422
        )
        assert read(client, ref, kind=kind).status_code == 422
    assert read(client, ref, sha256="0" * 64).status_code == 410
    service = client.app.state.hr_agent_service
    service.access.agent_use_authorization = SimpleNamespace(
        decide_for_user_id=lambda *_: SimpleNamespace(allowed=False)
    )
    assert read(client, ref).status_code == 403
    assert (
        client.get(
            "/api/hr/agent/knowledge", params={"kind": "intelligence"}
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert read(client, ref).status_code == 401
    assert (
        client.get(
            "/api/hr/agent/knowledge", params={"kind": "intelligence"}
        ).status_code
        == 401
    )


def test_explicit_real_bundle_company_topic_http(secured, tmp_path):
    configured = os.environ.get("HR_TEST_INTELLIGENCE_BUNDLE")
    if not configured:
        pytest.skip("explicit public local Bundle required")
    from tests.test_hr_agent_intelligence_build import check_build

    bundle = Path(configured)
    published, _ = check_build(bundle, tmp_path / "publication")
    client, _, _, _ = secured
    client.app.state.hr_agent_service.knowledge = KnowledgeReleases(
        tmp_path / "publication"
    )
    catalog = client.get("/api/hr/agent/knowledge", params={"kind": "intelligence"})
    assert catalog.status_code == 200, catalog.text
    for category in ("companies", "topics"):
        item = next(i for i in published.items if f"agent/{category}/" in i["path"])
        assert item["ref"] in [i["ref"] for i in catalog.json()["items"]]
        response = read(client, item["ref"])
        assert response.status_code == 200, response.text
        original = bundle / item["path"].removeprefix("intelligence/")
        assert response.json()["text"].encode() == original.read_bytes()
