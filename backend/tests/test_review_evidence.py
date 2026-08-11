import json
import subprocess

from app.review.evidence import GitEvidenceVerifier


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo, name, content):
    path = repo / name
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


def _manifest(root, name, sha, **overrides):
    payload = {
        "status": "succeeded",
        "git_sha": sha,
        "release_name": name.removesuffix(".json"),
        **overrides,
    }
    (root / name).write_text(json.dumps(payload), encoding="utf-8")


def test_later_commit_without_merge_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    manifests = tmp_path / "manifests"
    repo.mkdir()
    manifests.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "review@example.com")
    _git(repo, "config", "user.name", "Review Test")
    base = _commit(repo, "base", "base")
    _git(repo, "checkout", "-b", "fix")
    merge_sha = _commit(repo, "fix", "fixed")
    _git(repo, "checkout", "-b", "release", base)
    deployment_sha = _commit(repo, "later", "unrelated")
    _manifest(manifests, "later.json", deployment_sha)

    verifier = GitEvidenceVerifier(str(repo), str(manifests))
    result = verifier.verify_deployment("later.json", merge_sha=merge_sha)

    assert result.status == "rejected"
    assert result.details["reason"] == "merge_not_ancestor"
    assert result.details["contains_merge"] is False


def test_manifest_path_cannot_escape_allowlisted_directory(tmp_path):
    repo = tmp_path / "repo"
    manifests = tmp_path / "manifests"
    repo.mkdir()
    manifests.mkdir()
    verifier = GitEvidenceVerifier(str(repo), str(manifests))

    result = verifier.verify_deployment(
        "../../secret.json",
        merge_sha="a" * 40,
    )

    assert result.status == "rejected"
    assert result.details["reason"] == "manifest_outside_allowlist"


def test_merged_deployment_is_verified_and_uses_exact_ancestry_command(tmp_path):
    repo = tmp_path / "repo"
    manifests = tmp_path / "manifests"
    repo.mkdir()
    manifests.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "review@example.com")
    _git(repo, "config", "user.name", "Review Test")
    merge_sha = _commit(repo, "fix", "fixed")
    deployment_sha = _commit(repo, "release", "release")
    _manifest(manifests, "release.json", deployment_sha)
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.run(args, **kwargs)

    verifier = GitEvidenceVerifier(str(repo), str(manifests), runner=runner)
    result = verifier.verify_deployment("release.json", merge_sha=merge_sha)

    assert result.status == "verified"
    assert result.details == {
        "merge_sha": merge_sha,
        "deployment_sha": deployment_sha,
        "contains_merge": True,
        "manifest_release_name": "release",
    }
    assert calls[-1][0] == [
        "git",
        "-C",
        str(repo),
        "merge-base",
        "--is-ancestor",
        merge_sha,
        deployment_sha,
    ]
    assert calls[-1][1]["shell"] is False
    assert calls[-1][1]["timeout"] == 10


def test_merge_requires_full_lowercase_sha_and_existing_commit(tmp_path):
    repo = tmp_path / "repo"
    manifests = tmp_path / "manifests"
    repo.mkdir()
    manifests.mkdir()
    _git(repo, "init")
    verifier = GitEvidenceVerifier(str(repo), str(manifests))

    assert verifier.verify_merge("abc").details["reason"] == "invalid_merge_sha"
    assert verifier.verify_merge("A" * 40).details["reason"] == "invalid_merge_sha"
    missing = verifier.verify_merge("a" * 40)
    assert missing.status == "rejected"
    assert missing.details["reason"] == "merge_commit_not_found"


def test_commit_path_verification_rejects_traversal_and_requires_exact_object(tmp_path):
    repo = tmp_path / "repo"
    manifests = tmp_path / "manifests"
    repo.mkdir()
    manifests.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "review@example.com")
    _git(repo, "config", "user.name", "Review Test")
    sha = _commit(repo, "review.md", "review")
    verifier = GitEvidenceVerifier(str(repo), str(manifests))

    assert verifier.verify_commit_path(sha, "review.md").status == "verified"
    assert verifier.verify_commit_path(sha, "../secret").details["reason"] == "invalid_git_path"
    assert verifier.verify_commit_path(sha, "missing.md").details["reason"] == "commit_path_not_found"


def test_manifest_must_be_successful_object_with_valid_sha(tmp_path):
    repo = tmp_path / "repo"
    manifests = tmp_path / "manifests"
    repo.mkdir()
    manifests.mkdir()
    (manifests / "array.json").write_text("[]", encoding="utf-8")
    (manifests / "failed.json").write_text(
        json.dumps({"status": "failed", "git_sha": "a" * 40}),
        encoding="utf-8",
    )

    verifier = GitEvidenceVerifier(str(repo), str(manifests))

    assert verifier.verify_deployment(
        "array.json", merge_sha="a" * 40
    ).details["reason"] == "invalid_manifest"
    assert verifier.verify_deployment(
        "failed.json", merge_sha="a" * 40
    ).details["reason"] == "deployment_not_succeeded"
