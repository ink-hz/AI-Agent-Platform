"""Exact downloads retain real result storage and current source checks."""

import hashlib
from uuid import uuid4

from app.hr_agent.types import HrAgentProblem
from tests import test_hr_agent_routes as route_fixtures
from tests.test_hr_agent_repository_views import save_result

secured = route_fixtures.secured
database = route_fixtures.database
_FIXTURES = (secured, database)


def test_file_matches_exact_saved_revision_across_entrypoints(secured):
    client, headers, repo, owner = secured
    _work, result = save_result(repo, owner)
    ref = result["ref"]
    path = f"/api/hr/agent/results/{ref['id']}/revisions/{ref['revision']}"
    response = client.get(path + "/file")
    assert response.status_code == 200, response.text
    assert result["body"] in response.text
    assert response.headers["content-type"].startswith("text/markdown")
    assert "no-store" in response.headers["cache-control"]
    info = client.get(path + "/file-info").json()
    assert info["result_ref"] == ref
    assert info["sha256"] == hashlib.sha256(response.content).hexdigest()
    assert info["size_bytes"] == len(response.content)
    assert result["title"] not in info["filename"]
    position = {"kind": "position", "id": str(uuid4())}
    repo.scope_validator = lambda *a: None
    linked = client.post(
        f"/api/hr/agent/results/{ref['id']}/links",
        json={"objects": [position], "expected_result_revision": ref["revision"]},
        headers=headers,
    )
    assert linked.status_code == 200, linked.text
    page = client.get(
        "/api/hr/agent/results",
        params={"object_kind": "position", "object_id": position["id"]},
    ).json()
    assert page["items"][0]["ref"] == ref
    assert client.get(path + "/file").content == response.content
    assert (
        client.get(path.replace(ref["revision"], "current") + "/file").status_code
        == 422
    )


def test_file_and_metadata_recheck_sources_and_identity(secured):
    client, _headers, repo, owner = secured
    _, result = save_result(repo, owner)
    ref = result["ref"]
    path = f"/api/hr/agent/results/{ref['id']}/revisions/{ref['revision']}"
    missing = f"/api/hr/agent/results/{uuid4()}/revisions/{ref['revision']}"
    for suffix in ["/file", "/file-info"]:
        assert client.get(missing + suffix).status_code == 404
    source = {"kind": "method", "id": "revoked", "revision": "1", "sha256": "a" * 64}
    with repo.transaction() as c:
        repo._edges(c, owner, "result", ref["id"], ref["revision"], [source])

    def revoked(*args):
        raise HrAgentProblem(
            {
                "code": "dependency_revoked",
                "message": "revoked",
                "retryable": False,
                "details": {},
            },
            403,
        )

    repo.scope_validator = revoked
    for suffix in ["/file", "/file-info"]:
        response = client.get(path + suffix)
        assert response.status_code == 403, response.text
        assert result["body"] not in response.text
    client.cookies.clear()
    assert client.get(path + "/file").status_code == 401


def test_real_other_owner_download_is_hidden(secured):
    client, _headers, repo, _owner = secured
    _, other = save_result(repo, uuid4())
    ref = other["ref"]
    for suffix in ["file", "file-info"]:
        response = client.get(
            f"/api/hr/agent/results/{ref['id']}/revisions/{ref['revision']}/{suffix}"
        )
        assert response.status_code == 404
        assert other["body"] not in response.text
