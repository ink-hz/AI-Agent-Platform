from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.request import urlopen

from .evidence import GitEvidenceVerifier, VerificationResult


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDEMPOTENCY_KEY = re.compile(r"^sha256:[0-9a-f]{64}$")
FAILURE_LAYERS = frozenset({
    "channel",
    "context",
    "guardrail",
    "schema",
    "planner",
    "capability_evidence",
    "coverage",
    "synthesis",
    "outcome",
    "trace_eval",
})


class OutboxItemError(RuntimeError):
    pass


class _Blocked(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidatedIssue:
    issue_key: str
    title: str
    failure_layer: str
    secondary_layers: tuple[str, ...]
    expected_repair: str


@dataclass(frozen=True)
class ValidatedItem:
    turn_key: str
    issue_key: str


@dataclass(frozen=True)
class ValidatedHandoff:
    idempotency_key: str
    batch_id: str
    agent_id: str
    payload_sha256: str
    remediation_commit: str
    release_name: str
    deployment_sha: str
    release_manifest_ref: str
    repository_path: str
    issues: tuple[ValidatedIssue, ...]
    items: tuple[ValidatedItem, ...]
    merge_verification: Mapping[str, Any]
    deployment_verification: Mapping[str, Any]


@dataclass(frozen=True)
class ImportResult:
    state: str
    reason: str
    issue_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


def load_outbox_item(path: Path) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise OutboxItemError("outbox item path must be absolute")
    if candidate.is_symlink():
        raise OutboxItemError("outbox item must not be a symlink")
    if not candidate.is_file():
        raise OutboxItemError("outbox item must be a regular file")
    if stat.S_IMODE(candidate.stat().st_mode) != 0o600:
        raise OutboxItemError("outbox item must have mode 0600")
    if candidate.stat().st_size > 1024 * 1024:
        raise OutboxItemError("outbox item exceeds size limit")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OutboxItemError("outbox item is not valid JSON") from error
    if not isinstance(payload, dict):
        raise OutboxItemError("outbox item must be an object")
    return payload


def list_outbox_items(directory: Path) -> list[Path]:
    candidate = Path(directory).expanduser()
    if not candidate.is_absolute():
        raise OutboxItemError("outbox directory path must be absolute")
    current = candidate
    while True:
        if current.is_symlink():
            raise OutboxItemError("outbox directory must not contain symlinks")
        if current.parent == current:
            break
        current = current.parent
    if not candidate.exists():
        return []
    if not candidate.is_dir():
        raise OutboxItemError("outbox path must be a directory")
    if stat.S_IMODE(candidate.stat().st_mode) != 0o700:
        raise OutboxItemError("outbox directory must have mode 0700")
    return sorted(candidate.glob("*.json"))


def _fetch_json(url: str) -> Any:
    with urlopen(url, timeout=10) as response:
        return json.load(response)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    immutable = {
        "schema_version": payload["schema_version"],
        "idempotency_key": payload["idempotency_key"],
        "batch": payload["batch"],
        "release": payload["release"],
    }
    encoded = json.dumps(
        immutable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _mapping(value: Any, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _Blocked(reason)
    return value


def _string(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Blocked(reason)
    return value


def _relative_path(value: Any, reason: str) -> str:
    raw = _string(value, reason)
    pure = PurePosixPath(raw)
    if (
        "\\" in raw
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise _Blocked(reason)
    return raw


def _read_json(path: Path, reason: str) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise _Blocked(reason)
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), reason)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _Blocked(reason) from error


class HandoffImporter:
    def __init__(
        self,
        repository,
        registry,
        *,
        fetch_json: Callable[[str], Any] = _fetch_json,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.fetch_json = fetch_json

    def import_item(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str = "closure-importer",
    ) -> ImportResult:
        try:
            normalized = self._reconcile_prepared(payload)
            validated = self._validate(normalized)
        except _Blocked as error:
            return ImportResult("blocked", str(error))
        try:
            result = self.repository.import_release_handoff(
                validated,
                actor=actor,
            )
        except Exception:
            return ImportResult("blocked", "repository_unavailable")
        state = str(result.get("state", "blocked"))
        return ImportResult(
            state=state,
            reason=str(result.get("reason", "")),
            issue_ids=tuple(str(value) for value in result.get("issue_ids", ())),
            evidence_ids=tuple(
                str(value) for value in result.get("evidence_ids", ())
            ),
        )

    def import_path(
        self,
        path: Path,
        *,
        actor: str = "closure-importer",
    ) -> ImportResult:
        payload = load_outbox_item(path)
        try:
            normalized = self._reconcile_prepared(payload)
        except _Blocked:
            normalized = payload
        result = self.import_item(normalized, actor=actor)
        updated = copy.deepcopy(dict(normalized))
        handoff = updated.get("handoff")
        if not isinstance(handoff, dict):
            raise OutboxItemError("outbox item handoff must be an object")
        handoff["attempt_count"] = int(handoff.get("attempt_count") or 0) + 1
        handoff["result"] = {
            "state": result.state,
            "reason": result.reason,
            "issue_ids": list(result.issue_ids),
            "evidence_ids": list(result.evidence_ids),
        }
        if result.state == "imported":
            handoff["state"] = "acknowledged"
            handoff["acknowledged_at"] = _utc_now()
            handoff["last_error"] = None
        else:
            handoff["state"] = result.state
            handoff["last_error"] = result.reason
        updated["updated_at"] = _utc_now()
        _atomic_write(Path(path), updated)
        return result

    def _resolve_batch(
        self,
        batch_ref: Mapping[str, Any],
    ) -> tuple[Any, Mapping[str, Any], Path]:
        batch_id = _string(batch_ref.get("id"), "invalid_payload")
        relative = _relative_path(batch_ref.get("path"), "invalid_batch_path")
        claimed_hash = _string(batch_ref.get("sha256"), "invalid_payload")
        if not SHA256.fullmatch(claimed_hash):
            raise _Blocked("invalid_payload")
        wrong_agent = False
        found_batch = False
        for agent in self.registry.list_agents():
            evidence = getattr(agent, "review_evidence", None)
            if evidence is None:
                continue
            root = Path(evidence.repository_path).resolve()
            unresolved = root / relative
            if unresolved.is_symlink():
                continue
            candidate = unresolved.resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                continue
            batch = _read_json(candidate, "batch_unreadable")
            if batch.get("batch_id") != batch_id:
                continue
            found_batch = True
            if batch.get("agent_id") != agent.flywheel_agent_id:
                wrong_agent = True
                continue
            if _sha256(candidate) != claimed_hash:
                raise _Blocked("batch_hash_mismatch")
            return agent, batch, candidate
        if wrong_agent:
            raise _Blocked("agent_mismatch")
        if found_batch:
            raise _Blocked("unknown_agent")
        raise _Blocked("batch_unreadable")

    def _reconcile_prepared(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        release = _mapping(payload.get("release"), "invalid_payload")
        handoff = _mapping(payload.get("handoff"), "invalid_payload")
        release_state = release.get("transaction_status")
        handoff_state = handoff.get("state")
        if release_state != "prepared" and handoff_state != "prepared":
            return payload
        if release_state != "prepared" or handoff_state not in {
            "prepared",
            "blocked",
        }:
            raise _Blocked("prepared_release_unverified")
        try:
            batch_ref = _mapping(payload.get("batch"), "invalid_payload")
            agent, _, _ = self._resolve_batch(batch_ref)
            manifest_path = Path(
                _string(release.get("manifest_path"), "prepared_release_unverified")
            )
            manifest_root = Path(
                agent.review_evidence.release_manifest_dir
            ).resolve()
            if manifest_path.is_symlink():
                raise _Blocked("prepared_release_unverified")
            manifest_path = manifest_path.resolve()
            if not manifest_path.is_relative_to(manifest_root):
                raise _Blocked("prepared_release_unverified")
            manifest = _read_json(manifest_path, "prepared_release_unverified")
            if manifest.get("status") != "succeeded":
                raise _Blocked("prepared_release_unverified")
            release_name = _string(release.get("name"), "prepared_release_unverified")
            release_sha = _string(release.get("git_sha"), "prepared_release_unverified")
            health = self.fetch_json(
                _string(agent.health.url, "prepared_release_unverified")
            )
            build = _mapping(health.get("build"), "prepared_release_unverified")
            if not (
                build.get("available") is True
                and build.get("git_sha") == release_sha
                and build.get("release_name") == release_name
                and manifest.get("git_sha") == release_sha
                and manifest.get("release_name") == release_name
            ):
                raise _Blocked("prepared_release_unverified")
        except _Blocked:
            raise
        except Exception as error:
            raise _Blocked("prepared_release_unverified") from error
        normalized = copy.deepcopy(dict(payload))
        normalized["release"]["transaction_status"] = "succeeded"
        normalized["release"]["manifest_sha256"] = _sha256(manifest_path)
        normalized["handoff"]["state"] = "pending"
        return normalized

    def _validate(self, payload: Mapping[str, Any]) -> ValidatedHandoff:
        if payload.get("schema_version") != 1:
            raise _Blocked("invalid_payload")
        idempotency_key = _string(
            payload.get("idempotency_key"),
            "invalid_payload",
        )
        if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise _Blocked("invalid_payload")
        batch_ref = _mapping(payload.get("batch"), "invalid_payload")
        release = _mapping(payload.get("release"), "invalid_payload")
        handoff = _mapping(payload.get("handoff"), "invalid_payload")
        batch_id = _string(batch_ref.get("id"), "invalid_payload")
        release_name = _string(release.get("name"), "invalid_payload")
        deployment_sha = _string(release.get("git_sha"), "invalid_payload")
        expected_key = "sha256:" + hashlib.sha256(
            f"{batch_id}\0{release_name}\0{deployment_sha}".encode("utf-8")
        ).hexdigest()
        if idempotency_key != expected_key:
            raise _Blocked("idempotency_key_mismatch")
        if (
            release.get("transaction_status") != "succeeded"
            or handoff.get("state") not in {
                "pending",
                "acknowledged",
                "blocked",
            }
        ):
            raise _Blocked("release_not_succeeded")
        if not FULL_SHA.fullmatch(deployment_sha):
            raise _Blocked("invalid_deployment_sha")

        selected, batch_payload, batch_path = self._resolve_batch(batch_ref)
        agent_id = _string(batch_payload.get("agent_id"), "invalid_batch")

        repository_root = Path(selected.review_evidence.repository_path).resolve()
        manifest_root = Path(
            selected.review_evidence.release_manifest_dir
        ).resolve()
        unresolved_manifest = Path(
            _string(release.get("manifest_path"), "invalid_release_manifest")
        )
        if unresolved_manifest.is_symlink():
            raise _Blocked("manifest_outside_allowlist")
        manifest_path = unresolved_manifest.resolve()
        if not manifest_path.is_relative_to(manifest_root):
            raise _Blocked("manifest_outside_allowlist")
        manifest_sha = release.get("manifest_sha256")
        if not isinstance(manifest_sha, str) or _sha256(manifest_path) != manifest_sha:
            raise _Blocked("release_manifest_hash_mismatch")
        manifest_ref = manifest_path.relative_to(manifest_root).as_posix()

        remediation = _string(
            batch_payload.get("remediation_commit"),
            "invalid_batch",
        )
        verifier = GitEvidenceVerifier(str(repository_root), str(manifest_root))
        merge_result = verifier.verify_merge(remediation)
        self._require_verified(merge_result)
        for field, reason in (
            ("review_ref", "review_ref_missing"),
            ("testset_ref", "testset_ref_missing"),
        ):
            path_result = verifier.verify_commit_path(
                remediation,
                _string(batch_payload.get(field), reason),
            )
            if path_result.status != "verified":
                raise _Blocked(reason)
        deployment_result = verifier.verify_deployment(
            manifest_ref,
            merge_sha=remediation,
        )
        self._require_verified(deployment_result)
        if (
            deployment_result.details.get("deployment_sha") != deployment_sha
            or deployment_result.details.get("manifest_release_name") != release_name
        ):
            raise _Blocked("release_identity_mismatch")

        issues = self._issues(batch_payload.get("issues"))
        issue_keys = {issue.issue_key for issue in issues}
        items = self._items(batch_payload.get("items"), issue_keys)
        return ValidatedHandoff(
            idempotency_key=idempotency_key,
            batch_id=batch_id,
            agent_id=agent_id,
            payload_sha256=_canonical_hash(payload),
            remediation_commit=remediation,
            release_name=release_name,
            deployment_sha=deployment_sha,
            release_manifest_ref=manifest_ref,
            repository_path=str(repository_root),
            issues=issues,
            items=items,
            merge_verification=merge_result.details,
            deployment_verification=deployment_result.details,
        )

    @staticmethod
    def _require_verified(result: VerificationResult) -> None:
        if result.status != "verified":
            raise _Blocked(str(result.details.get("reason", "evidence_rejected")))

    @staticmethod
    def _issues(value: Any) -> tuple[ValidatedIssue, ...]:
        if not isinstance(value, list) or not value:
            raise _Blocked("invalid_batch")
        rows = []
        keys = set()
        for raw in value:
            row = _mapping(raw, "invalid_batch")
            key = _string(row.get("issue_key"), "invalid_batch")
            layer = _string(row.get("failure_layer"), "invalid_batch")
            secondary = row.get("secondary_layers")
            if (
                key in keys
                or layer not in FAILURE_LAYERS
                or not isinstance(secondary, list)
                or any(item not in FAILURE_LAYERS for item in secondary)
            ):
                raise _Blocked("invalid_batch")
            keys.add(key)
            rows.append(ValidatedIssue(
                issue_key=key,
                title=_string(row.get("title"), "invalid_batch"),
                failure_layer=layer,
                secondary_layers=tuple(secondary),
                expected_repair=_string(
                    row.get("expected_repair"),
                    "invalid_batch",
                ),
            ))
        return tuple(rows)

    @staticmethod
    def _items(
        value: Any,
        issue_keys: set[str],
    ) -> tuple[ValidatedItem, ...]:
        if not isinstance(value, list) or not value:
            raise _Blocked("invalid_batch")
        rows = []
        turns = set()
        for raw in value:
            row = _mapping(raw, "invalid_batch")
            turn_key = _string(row.get("turn_key"), "invalid_batch")
            issue_key = _string(row.get("issue_key"), "invalid_batch")
            if issue_key not in issue_keys:
                raise _Blocked("unknown_issue_key")
            if (
                not turn_key.startswith("fae:")
                or turn_key in turns
            ):
                raise _Blocked("invalid_batch")
            turns.add(turn_key)
            rows.append(ValidatedItem(turn_key=turn_key, issue_key=issue_key))
        return tuple(rows)
