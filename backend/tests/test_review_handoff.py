from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.review.handoff import (
    HandoffImporter,
    OutboxItemError,
    load_outbox_item,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class Repository:
    def __init__(self):
        self.calls = []
        self.result = {
            "state": "imported",
            "reason": "",
            "issue_ids": ["issue-1"],
            "evidence_ids": ["merge-1", "deployment-1"],
        }

    def import_release_handoff(self, validated, *, actor):
        self.calls.append((validated, actor))
        return self.result


@pytest.fixture
def handoff_fixture(tmp_path):
    repo = tmp_path / "repo"
    manifests = repo / "dist/release/manifests"
    batch_dir = repo / "docs/reviews/feedback-closure-batches"
    review = repo / "docs/reviews/review.md"
    testset = repo / "evals/testsets/cases.jsonl"
    manifests.mkdir(parents=True)
    batch_dir.mkdir(parents=True)
    review.parent.mkdir(parents=True, exist_ok=True)
    testset.parent.mkdir(parents=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    review.write_text("review", encoding="utf-8")
    testset.write_text("{}\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "remediation")
    remediation_sha = git(repo, "rev-parse", "HEAD")
    marker = repo / "release.txt"
    marker.write_text("release", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "release")
    release_sha = git(repo, "rev-parse", "HEAD")

    batch = {
        "schema_version": 1,
        "batch_id": "batch-one",
        "agent_id": "ai-fae-agent",
        "remediation_commit": remediation_sha,
        "review_ref": "docs/reviews/review.md",
        "testset_ref": "evals/testsets/cases.jsonl",
        "issues": [{
            "issue_key": "issue-one",
            "title": "Issue one",
            "failure_layer": "coverage",
            "secondary_layers": ["synthesis"],
            "expected_repair": "complete evidence",
        }],
        "items": [{"turn_key": "fae:opaque-one", "issue_key": "issue-one"}],
    }
    batch_path = batch_dir / "batch-one.json"
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    manifest = {
        "status": "succeeded",
        "release_name": "release-one",
        "git_sha": release_sha,
    }
    manifest_path = manifests / "release-one.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    key_raw = f"batch-one\0release-one\0{release_sha}".encode()
    payload = {
        "schema_version": 1,
        "idempotency_key": f"sha256:{hashlib.sha256(key_raw).hexdigest()}",
        "batch": {
            "id": "batch-one",
            "path": "docs/reviews/feedback-closure-batches/batch-one.json",
            "sha256": hashlib.sha256(batch_path.read_bytes()).hexdigest(),
        },
        "release": {
            "name": "release-one",
            "git_sha": release_sha,
            "branch": "release/test",
            "transaction_status": "succeeded",
            "manifest_path": str(manifest_path),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        },
        "handoff": {
            "state": "pending",
            "attempt_count": 0,
            "last_error": None,
            "acknowledged_at": None,
            "result": None,
        },
        "created_at": "2026-08-11T00:00:00Z",
        "updated_at": "2026-08-11T00:00:00Z",
    }
    agent = SimpleNamespace(
        flywheel_agent_id="ai-fae-agent",
        health=SimpleNamespace(url="https://fae.example/health"),
        review_evidence=SimpleNamespace(
            repository_path=str(repo),
            release_manifest_dir=str(manifests),
        ),
    )
    registry = SimpleNamespace(
        list_agents=lambda: [agent],
        get_agent_by_flywheel_id=lambda agent_id: (
            agent if agent_id == "ai-fae-agent" else None
        ),
    )
    return repo, batch_path, payload, registry


def test_same_handoff_is_idempotent(handoff_fixture):
    _, _, payload, registry = handoff_fixture
    repository = Repository()
    importer = HandoffImporter(repository, registry)

    first = importer.import_item(payload)
    second = importer.import_item(payload)

    assert second == first
    assert first.state == "imported"
    assert repository.calls[0][0].items[0].turn_key == "fae:opaque-one"
    assert all(call[1] == "closure-importer" for call in repository.calls)


def test_no_fuzzy_issue_merge(handoff_fixture):
    _, batch_path, payload, registry = handoff_fixture
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    batch["items"][0]["issue_key"] = "missing-key"
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    payload["batch"]["sha256"] = hashlib.sha256(batch_path.read_bytes()).hexdigest()
    importer = HandoffImporter(Repository(), registry)

    result = importer.import_item(payload)

    assert result.state == "blocked"
    assert result.reason == "unknown_issue_key"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda payload: payload["release"].update(transaction_status="rolled_back"), "release_not_succeeded"),
        (lambda payload: payload["batch"].update(sha256="0" * 64), "batch_hash_mismatch"),
        (lambda payload: payload["release"].update(git_sha="f" * 40), "idempotency_key_mismatch"),
    ],
)
def test_invalid_handoff_is_blocked(handoff_fixture, mutation, reason):
    _, _, payload, registry = handoff_fixture
    mutation(payload)

    result = HandoffImporter(Repository(), registry).import_item(payload)

    assert result.state == "blocked"
    assert result.reason == reason


def test_prepared_item_requires_manifest_and_production_build_proof(handoff_fixture):
    _, _, payload, registry = handoff_fixture
    payload["release"]["transaction_status"] = "prepared"
    payload["release"]["manifest_sha256"] = None
    payload["handoff"]["state"] = "prepared"
    release_sha = payload["release"]["git_sha"]
    importer = HandoffImporter(
        Repository(),
        registry,
        fetch_json=lambda _url: {"build": {
            "available": True,
            "git_sha": release_sha,
            "release_name": "release-one",
        }},
    )

    assert importer.import_item(payload).state == "imported"

    mismatch = HandoffImporter(
        Repository(),
        registry,
        fetch_json=lambda _url: {"build": {"available": True, "git_sha": "f" * 40}},
    ).import_item(payload)
    assert mismatch.state == "blocked"
    assert mismatch.reason == "prepared_release_unverified"


def test_outbox_file_requires_absolute_private_regular_file(tmp_path):
    path = tmp_path / "item.json"
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)

    assert load_outbox_item(path) == {}
    with pytest.raises(OutboxItemError, match="symlink"):
        load_outbox_item(link)
    with pytest.raises(OutboxItemError, match="absolute"):
        load_outbox_item(Path("relative.json"))
    os.chmod(path, 0o644)
    with pytest.raises(OutboxItemError, match="0600"):
        load_outbox_item(path)


def test_outbox_acknowledges_only_after_repository_commit(handoff_fixture, tmp_path):
    _, _, payload, registry = handoff_fixture
    path = tmp_path / "handoff.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)

    result = HandoffImporter(Repository(), registry).import_path(path)

    saved = load_outbox_item(path)
    assert result.state == "imported"
    assert saved["handoff"]["state"] == "acknowledged"
    assert saved["handoff"]["attempt_count"] == 1
    assert saved["handoff"]["acknowledged_at"]


def test_transaction_failure_never_acknowledges_outbox(handoff_fixture, tmp_path):
    _, _, payload, registry = handoff_fixture
    path = tmp_path / "handoff.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)

    class FailingRepository:
        def import_release_handoff(self, _validated, *, actor):
            raise RuntimeError(actor)

    result = HandoffImporter(FailingRepository(), registry).import_path(path)

    saved = load_outbox_item(path)
    assert result.state == "blocked"
    assert saved["handoff"]["state"] == "blocked"
    assert saved["handoff"]["acknowledged_at"] is None
    assert saved["handoff"]["last_error"] == "repository_unavailable"

    retry = HandoffImporter(Repository(), registry).import_path(path)
    retried = load_outbox_item(path)
    assert retry.state == "imported"
    assert retried["handoff"]["state"] == "acknowledged"
    assert retried["handoff"]["attempt_count"] == 2
