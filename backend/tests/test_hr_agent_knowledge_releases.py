import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from app.hr_agent.types import HrAgentProblem
from tests import test_hr_agent_context as context_fixtures
from tests.test_hr_agent_repository import request

publication = context_fixtures.publication
repo = context_fixtures.repo
database = context_fixtures.database
_FIXTURES = (publication, repo, database)


def two_releases(root):
    root.mkdir(exist_ok=True)
    (root / "releases").mkdir()
    first = publication(root / "releases" / "r1")
    second_dir = root / "releases" / "r2"
    publication(second_dir)
    manifest = json.loads((second_dir / "manifest.json").read_text())
    manifest["release_id"] = "r2"
    manifest["resources"][0]["ref"]["revision"] = "r2"
    (second_dir / "manifest.json").write_text(json.dumps(manifest))
    (root / "current.json").write_text(json.dumps({"release_id": "r1"}))
    return first, manifest["resources"][0]["ref"]


def test_frozen_work_keeps_old_release_after_publish(repo, tmp_path):
    from app.hr_agent.knowledge import KnowledgeReleases
    from app.hr_agent.resources import ResourceReader

    first, second = two_releases(tmp_path)
    releases = KnowledgeReleases(tmp_path)
    resources = ResourceReader(repo, releases, authorize_objects=lambda *a: None)
    repo.release_provider = releases.metadata
    repo.release_validator = releases.validate_record
    repo.scope_validator = resources.validate_scope
    owner = uuid4()
    work = repo.submit(owner, request(references=[first]), uuid4())
    (tmp_path / "current.json").write_text(json.dumps({"release_id": "r2"}))
    fence = repo.claim("reader", 60)
    assert resources.knowledge_for(fence).release_id == "r1"
    items = resources.list_resources(fence, {"kinds": ["method"]})["items"]
    assert [i["ref"] for i in items] == [first]
    assert resources.read_resource(fence, {"ref": first})["text"]
    with pytest.raises(HrAgentProblem):
        resources.read_resource(fence, {"ref": second})
    repo.cancel(owner, work["work_id"], "done", uuid4())
    second_work = repo.submit(owner, request(references=[second]), uuid4())
    assert second_work["work_id"] != work["work_id"]
    assert resources.knowledge_for(repo.claim("new", 60)).release_id == "r2"


def test_missing_old_release_blocks_instead_of_using_current(repo, tmp_path):
    from app.hr_agent.knowledge import KnowledgeReleases

    first, _ = two_releases(tmp_path)
    releases = KnowledgeReleases(tmp_path)
    repo.release_provider = releases.metadata
    repo.release_validator = releases.validate_record
    repo.scope_validator = lambda *a: None
    owner = uuid4()
    work = repo.submit(owner, request(references=[first]), uuid4())
    assert work["work_id"]
    (tmp_path / "current.json").write_text(json.dumps({"release_id": "r2"}))
    shutil.rmtree(tmp_path / "releases" / "r1")
    fence = repo.claim("reader", 60)
    with pytest.raises(HrAgentProblem) as error:
        repo.context_input(fence)
    assert error.value.problem["code"] == "configuration_unavailable"


def test_release_pointer_rejects_paths_and_aliases(tmp_path):
    from app.hr_agent.knowledge import KnowledgeReleases

    two_releases(tmp_path)
    for value in ["../r1", "/tmp/r1", "current", "latest"]:
        (tmp_path / "current.json").write_text(json.dumps({"release_id": value}))
        with pytest.raises(HrAgentProblem):
            KnowledgeReleases(tmp_path).metadata()


def test_reviewed_content_build_is_reproducible_and_immutable(tmp_path):
    from app.hr_agent.knowledge import KnowledgeReleases
    from tools.hr_agent.build_knowledge_release import build

    source = Path(__file__).parents[1] / "hr_agent_knowledge"
    manifest = build(source, tmp_path)
    assert build(source, tmp_path) == manifest
    releases = KnowledgeReleases(tmp_path)
    current = releases.current()
    core = [item for item in current.items if item["path"].startswith("methods/")]
    assert len(core) == 7
    assert all(current.read(item["ref"]).startswith("---") for item in core)
    assert len(current.items) == 9
    assert "hr.confirm_standard" not in current.role
    assert "standard_proposal" in current.role
    (current.directory / "methods" / "requirement-calibration.md").write_text(
        "tampered"
    )
    with pytest.raises(ValueError):
        build(source, tmp_path)
