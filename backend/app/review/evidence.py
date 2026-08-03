from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class VerificationResult:
    status: Literal["verified", "rejected"]
    details: dict[str, Any]


def _rejected(reason: str, **details: Any) -> VerificationResult:
    return VerificationResult("rejected", {"reason": reason, **details})


class GitEvidenceVerifier:
    def __init__(
        self,
        repository_path: str,
        release_manifest_dir: str,
        *,
        runner: Runner = subprocess.run,
    ) -> None:
        self.repository_path = str(Path(repository_path).resolve())
        self.release_manifest_dir = Path(release_manifest_dir).resolve()
        self.runner = runner

    def _git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return self.runner(
            args,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=10,
        )

    def verify_merge(self, commit_sha: str) -> VerificationResult:
        if not FULL_SHA.fullmatch(commit_sha):
            return _rejected("invalid_merge_sha", merge_sha=commit_sha)
        try:
            result = self._git(
                [
                    "git",
                    "-C",
                    self.repository_path,
                    "cat-file",
                    "-e",
                    f"{commit_sha}^{{commit}}",
                ]
            )
        except (OSError, subprocess.SubprocessError):
            return _rejected("git_verification_failed", merge_sha=commit_sha)
        if result.returncode != 0:
            return _rejected("merge_commit_not_found", merge_sha=commit_sha)
        return VerificationResult(
            "verified",
            {"merge_sha": commit_sha, "commit_exists": True},
        )

    def _manifest_path(self, manifest_ref: str) -> Path | None:
        candidate = (self.release_manifest_dir / manifest_ref).resolve()
        if not candidate.is_relative_to(self.release_manifest_dir):
            return None
        return candidate

    def verify_deployment(
        self,
        manifest_ref: str,
        *,
        merge_sha: str,
    ) -> VerificationResult:
        if not FULL_SHA.fullmatch(merge_sha):
            return _rejected("invalid_merge_sha", merge_sha=merge_sha)
        manifest_path = self._manifest_path(manifest_ref)
        if manifest_path is None:
            return _rejected("manifest_outside_allowlist", merge_sha=merge_sha)
        try:
            if manifest_path.stat().st_size > 1024 * 1024:
                return _rejected("invalid_manifest", merge_sha=merge_sha)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return _rejected("manifest_unreadable", merge_sha=merge_sha)
        if not isinstance(payload, dict):
            return _rejected("invalid_manifest", merge_sha=merge_sha)
        release_name = payload.get("release_name", "")
        deployment_sha = payload.get("git_sha", "")
        if payload.get("status") != "succeeded":
            return _rejected(
                "deployment_not_succeeded",
                merge_sha=merge_sha,
                deployment_sha=deployment_sha,
                contains_merge=False,
                manifest_release_name=release_name,
            )
        if not isinstance(deployment_sha, str) or not FULL_SHA.fullmatch(
            deployment_sha
        ):
            return _rejected(
                "invalid_deployment_sha",
                merge_sha=merge_sha,
                deployment_sha=deployment_sha,
                contains_merge=False,
                manifest_release_name=release_name,
            )
        command = [
            "git",
            "-C",
            self.repository_path,
            "merge-base",
            "--is-ancestor",
            merge_sha,
            deployment_sha,
        ]
        try:
            result = self._git(command)
        except (OSError, subprocess.SubprocessError):
            return _rejected(
                "git_verification_failed",
                merge_sha=merge_sha,
                deployment_sha=deployment_sha,
                contains_merge=False,
                manifest_release_name=release_name,
            )
        details = {
            "merge_sha": merge_sha,
            "deployment_sha": deployment_sha,
            "contains_merge": result.returncode == 0,
            "manifest_release_name": release_name,
        }
        if result.returncode == 0:
            return VerificationResult("verified", details)
        if result.returncode == 1:
            return _rejected("merge_not_ancestor", **details)
        return _rejected("git_verification_failed", **details)
