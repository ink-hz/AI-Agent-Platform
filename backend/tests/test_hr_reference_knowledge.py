from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from app.hr.reference_knowledge import HrKnowledgeError, HrKnowledgeRepository
from app.hr.reference_knowledge_release import build_release


RESOURCE_IDS = (
    "job-and-context",
    "requirement-calibration",
    "competency-and-profile",
    "sourcing-and-transfer",
    "evidence-informed-judgment",
    "selection-and-work-samples",
    "comparison-and-learning",
)


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _resource(resource_id: str, *, revision: int = 1, marker: str = "body") -> str:
    return (
        "---\n"
        f"id: {resource_id}\n"
        f"title: Title {resource_id}\n"
        f"revision: {revision}\n"
        "domains: [recruiting]\n"
        "knowledge_forms: [thinking-model, methodology]\n"
        "---\n\n"
        f"# {resource_id}\n\n{marker}\n"
    )


def _commit_knowledge(repo: Path, *, marker: str = "body", revision: int = 1) -> str:
    knowledge = repo / "bots/hr/knowledge"
    recruiting = knowledge / "recruiting"
    sources = knowledge / "sources"
    recruiting.mkdir(parents=True, exist_ok=True)
    sources.mkdir(exist_ok=True)
    index_rows = []
    for resource_id in RESOURCE_IDS:
        (recruiting / f"{resource_id}.md").write_text(
            _resource(resource_id, revision=revision, marker=marker), encoding="utf-8"
        )
        index_rows.append(f"- `{resource_id}`: Title {resource_id}")
    (knowledge / "README.md").write_text(
        "# Index\n\n" + "\n".join(index_rows) + "\n", encoding="utf-8"
    )
    (sources / "2026-09-08-hr-methodology-sources.md").write_text(
        "# Sources\n", encoding="utf-8"
    )
    _git(repo, "add", "bots/hr/knowledge")
    _git(repo, "commit", "-m", f"knowledge revision {revision}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def test_release_uses_only_committed_blobs_and_writes_complete_manifest(
    source_repo: Path, tmp_path: Path
) -> None:
    commit = _commit_knowledge(source_repo, marker="committed")
    changed = source_repo / "bots/hr/knowledge/recruiting/job-and-context.md"
    changed.write_text(
        _resource("job-and-context", marker="uncommitted"), encoding="utf-8"
    )

    release = build_release(source_repo, commit, tmp_path / "releases")

    assert "committed" in (release / "recruiting/job-and-context.md").read_text()
    assert "uncommitted" not in (release / "recruiting/job-and-context.md").read_text()
    manifest = json.loads((release / "manifest.json").read_text())
    assert manifest["source_commit"] == commit
    assert manifest["index_path"] == "README.md"
    assert len(manifest["resources"]) == 7
    assert set(manifest["files"]) == {
        "README.md",
        "sources/2026-09-08-hr-methodology-sources.md",
        *(f"recruiting/{resource_id}.md" for resource_id in RESOURCE_IDS),
    }
    for relative, digest in manifest["files"].items():
        assert hashlib.sha256((release / relative).read_bytes()).hexdigest() == digest


def test_repeat_build_is_idempotent_but_changed_existing_release_is_rejected(
    source_repo: Path, tmp_path: Path
) -> None:
    commit = _commit_knowledge(source_repo)
    releases = tmp_path / "releases"
    first = build_release(source_repo, commit, releases)
    assert build_release(source_repo, commit, releases) == first

    (first / "README.md").write_text("changed", encoding="utf-8")
    with pytest.raises(HrKnowledgeError, match="immutable"):
        build_release(source_repo, commit, releases)


def test_repeat_build_rejects_unexpected_content_in_existing_release(
    source_repo: Path, tmp_path: Path
) -> None:
    commit = _commit_knowledge(source_repo)
    releases = tmp_path / "releases"
    release = build_release(source_repo, commit, releases)
    (release / "unexpected.md").write_text("changed content", encoding="utf-8")

    with pytest.raises(HrKnowledgeError, match="immutable"):
        build_release(source_repo, commit, releases)


def test_release_rejects_committed_symlink(source_repo: Path, tmp_path: Path) -> None:
    knowledge = source_repo / "bots/hr/knowledge"
    (knowledge / "recruiting").mkdir(parents=True)
    (knowledge / "sources").mkdir()
    (knowledge / "README.md").write_text("# Index\n", encoding="utf-8")
    (knowledge / "sources/2026-09-08-hr-methodology-sources.md").write_text(
        "# Sources\n", encoding="utf-8"
    )
    (knowledge / "recruiting/job-and-context.md").symlink_to("../../CLAUDE.md")
    _git(source_repo, "add", "bots/hr/knowledge")
    _git(source_repo, "commit", "-m", "symlink")
    commit = _git(source_repo, "rev-parse", "HEAD")

    with pytest.raises(HrKnowledgeError, match="path"):
        build_release(source_repo, commit, tmp_path / "releases")


def test_repository_supports_historical_selection_and_checks_identity(
    source_repo: Path, tmp_path: Path
) -> None:
    first_commit = _commit_knowledge(source_repo, marker="historical", revision=1)
    first_release = build_release(source_repo, first_commit, tmp_path / "releases")
    first_resource = json.loads((first_release / "manifest.json").read_text())[
        "resources"
    ][0]

    for path in (source_repo / "bots/hr/knowledge").rglob("*"):
        if path.is_file():
            path.unlink()
    second_commit = _commit_knowledge(source_repo, marker="active", revision=2)
    build_release(source_repo, second_commit, tmp_path / "releases")
    repository = HrKnowledgeRepository(
        tmp_path / "releases", "/srv/hr/.knowledge-releases", second_commit
    )
    selection = {
        "source_commit": first_commit,
        "id": first_resource["id"],
        "revision": first_resource["revision"],
        "sha256": first_resource["sha256"],
    }

    context = repository.prompt_context((selection,))

    assert context["source_commit"] == first_commit
    assert (
        context["agent_release_path"] == f"/srv/hr/.knowledge-releases/{first_commit}"
    )
    assert context["user_selected_resources"] == [selection]
    assert "historical" not in json.dumps(context, ensure_ascii=False)
    article = repository.article(first_commit, first_resource["id"])
    assert "historical" in article["markdown"]
    with pytest.raises(HrKnowledgeError, match="identity"):
        repository.prompt_context(({**selection, "sha256": "0" * 64},))
    with pytest.raises(HrKnowledgeError, match="one source_commit"):
        repository.prompt_context(
            (selection, {**selection, "source_commit": second_commit})
        )


def test_index_and_prompt_context_enforce_utf8_budgets(
    source_repo: Path, tmp_path: Path
) -> None:
    commit = _commit_knowledge(source_repo)
    release = build_release(source_repo, commit, tmp_path / "releases")
    repository = HrKnowledgeRepository(
        tmp_path / "releases", "/srv/hr/.knowledge-releases", commit
    )
    index = repository.index()
    context = repository.prompt_context()
    assert len(index["index"].encode("utf-8")) <= 4 * 1024
    assert len(json.dumps(context, ensure_ascii=False).encode("utf-8")) <= 8 * 1024
    assert "Read" in context["instructions"]
    assert "reference" in context["instructions"].lower()
    assert "self-report" in context["instructions"].lower()
    assert "optional" in context["instructions"].lower()

    (release / "README.md").write_text("界" * 1400, encoding="utf-8")
    manifest_path = release / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["README.md"] = hashlib.sha256(
        (release / "README.md").read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(HrKnowledgeError, match="4 KiB"):
        repository.index()


@pytest.mark.parametrize(
    "mutation", ["hash", "untracked-path", "duplicate-id", "parent-symlink"]
)
def test_manifest_binds_resources_to_verified_unique_files(
    source_repo, tmp_path, mutation
):
    commit = _commit_knowledge(source_repo)
    release = build_release(source_repo, commit, tmp_path / "releases")
    manifest_path = release / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    resource = manifest["resources"][0]
    if mutation == "hash":
        resource["sha256"] = "0" * 64
    elif mutation == "untracked-path":
        del manifest["files"][resource["path"]]
    elif mutation == "duplicate-id":
        manifest["resources"].append(dict(resource))
    else:
        outside = tmp_path / "outside-resources"
        (release / "recruiting").rename(outside)
        (release / "recruiting").symlink_to(outside, target_is_directory=True)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(HrKnowledgeError):
        HrKnowledgeRepository(tmp_path / "releases", "/agent/releases", commit).index()


def test_resource_discovery_extends_to_new_hr_domains_without_code_changes(
    source_repo, tmp_path
):
    _commit_knowledge(source_repo)
    performance = source_repo / "bots/hr/knowledge/performance"
    performance.mkdir()
    (performance / "goal-dialogue.md").write_text(_resource("goal-dialogue"))
    _git(source_repo, "add", ".")
    _git(source_repo, "commit", "-m", "add performance resource")
    commit = _git(source_repo, "rev-parse", "HEAD")
    build_release(source_repo, commit, tmp_path / "releases")
    indexed = HrKnowledgeRepository(
        tmp_path / "releases", "/agent/releases", commit
    ).index()
    assert "goal-dialogue" in {item["id"] for item in indexed["resources"]}
