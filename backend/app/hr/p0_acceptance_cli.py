"""Controlled, status-only acceptance boundary for the HR P0 live workflow.

The orchestration transport is injectable for contract tests.  The production
wrapper uses the fixed Platform HTTP/PostgreSQL gateway in this module.  This module
owns the security boundary: configuration, evidence validation, bounded execution,
exact-ID archival and sanitised process output.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, TextIO
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

from .structured_output import extract_hr_envelope

_CONFIG_KEYS = {
    "schema_version",
    "agent_id",
    "api_base_url",
    "public_origin",
    "owner_id",
    "session_cookie",
    "csrf_token",
    "companies",
    "connect_timeout_seconds",
    "request_timeout_seconds",
    "run_timeout_seconds",
    "poll_interval_seconds",
    "deployment_egress_evidence_sha256",
}
_COMPANY_KEYS = {"canonical_name", "aliases", "approved_urls"}
_CREATED_ID_KEYS = {
    "conversation_ids",
    "position_ids",
    "candidate_ids",
    "position_candidate_ids",
    "candidate_document_ids",
}
_EXPECTED_KINDS = (
    "panorama_report",
    "position_package",
    "panorama_retrieval",
    "position_package",
    "candidate_match",
    "candidate_match",
    "candidate_interview_plan",
)
_FIXTURE_NAMES = {
    "panorama-result.json",
    "recruiting-results.json",
    "resume-adjacent.md",
    "resume-invalid.txt",
    "resume-strong.md",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COOKIE = re.compile(r"__Host-platform_session=[A-Za-z0-9._~+/=-]{1,512}")
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{1,512}")
_DEFAULT_CONFIG = Path("/tmp/hr-p0-acceptance/config.json")
_DEFAULT_FIXTURES = Path("/tmp/hr-p0-acceptance/fixtures")
_DEFAULT_CLEANUP_MANIFEST = Path("/tmp/hr-p0-acceptance/cleanup.json")
_FAILURE_CODES = {
    "API_CONTRACT",
    "API_UNAVAILABLE",
    "ARCHIVE_FAILED",
    "ARGUMENT_INVALID",
    "ARTIFACT_NOT_PDF",
    "ARTIFACT_NOT_READY",
    "ARTIFACT_TICKET",
    "BUSINESS_DELIVERY",
    "CANDIDATE_ISOLATION",
    "CANDIDATE_PARSE",
    "CONFIG_INVALID",
    "CREATED_IDS",
    "CSRF_UNAVAILABLE",
    "EGRESS_EVIDENCE",
    "EMPTY_ASSISTANT",
    "EMPTY_TRACE",
    "EVIDENCE_INVALID",
    "EXECUTION_FAILED",
    "FIXTURE_INVALID",
    "INVALID_ENVELOPE",
    "PANORAMA_FAILED",
    "RUN_ID",
    "SOURCE_SCOPE",
    "TIMEOUT",
    "TURN_EVIDENCE",
    "TURN_FAILED",
    "WRONG_AGENT",
    "WRONG_OWNER",
}


class AcceptanceFailure(RuntimeError):
    """A public, non-sensitive acceptance failure code."""

    def __init__(self, code: str) -> None:
        super().__init__(code if code in _FAILURE_CODES else "INTERNAL")


@dataclass(frozen=True, slots=True)
class CompanyConfig:
    canonical_name: str
    aliases: tuple[str, ...]
    approved_urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    schema_version: int
    agent_id: str
    api_base_url: str
    public_origin: str
    owner_id: UUID
    session_cookie: str
    csrf_token: str
    companies: tuple[CompanyConfig, ...]
    connect_timeout_seconds: int
    request_timeout_seconds: int
    run_timeout_seconds: int
    poll_interval_seconds: int
    deployment_egress_evidence_sha256: str


class AcceptanceGateway(Protocol):
    created_ids: Mapping[str, object]

    def execute(
        self,
        config: AcceptanceConfig,
        *,
        run_id: UUID,
        fixture_root: Path,
        deadline: float,
    ) -> dict[str, object]: ...

    def archive_exact(
        self,
        config: AcceptanceConfig,
        created_ids: dict[str, tuple[UUID, ...]],
        *,
        deadline: float,
    ) -> None: ...


def _invalid() -> AcceptanceFailure:
    return AcceptanceFailure("CONFIG_INVALID")


def _strict_json(raw: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)


def _plain_int(value: object, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("integer out of range")
    return value


def _absolute_url(value: object, *, expected: str | None = None) -> str:
    if not isinstance(value, str) or len(value) > 2048 or value != value.strip():
        raise ValueError("url invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (expected is not None and value != expected)
    ):
        raise ValueError("url invalid")
    return value.rstrip("/")


def load_config(path: Path, *, expected_path: Path) -> AcceptanceConfig:
    """Read one exact, owner-only, non-symlink acceptance config."""
    try:
        if not isinstance(path, Path) or not isinstance(expected_path, Path):
            raise TypeError("path type")
        if (
            not path.is_absolute()
            or not expected_path.is_absolute()
            or path != expected_path
        ):
            raise ValueError("path mismatch")
        # Reject lexical aliases before any resolution follows filesystem links.
        if Path(os.path.normpath(str(path))) != path:
            raise ValueError("path alias")
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_size < 2
            or metadata.st_size > 65_536
        ):
            raise ValueError("unsafe config")
        document = _strict_json(path.read_bytes())
        if not isinstance(document, dict) or set(document) != _CONFIG_KEYS:
            raise ValueError("config shape")
        if document["schema_version"] != 1 or document["agent_id"] != "hr-bot":
            raise ValueError("identity")
        api_base_url = _absolute_url(
            document["api_base_url"], expected="http://127.0.0.1:8080"
        )
        public_origin = _absolute_url(
            document["public_origin"], expected="https://agent.orbbec.com.cn"
        )
        owner_id = UUID(str(document["owner_id"]))
        if str(owner_id) != document["owner_id"]:
            raise ValueError("owner")
        cookie = document["session_cookie"]
        if not isinstance(cookie, str) or _COOKIE.fullmatch(cookie) is None:
            raise ValueError("cookie")
        csrf_token = document["csrf_token"]
        if not isinstance(csrf_token, str) or _TOKEN.fullmatch(csrf_token) is None:
            raise ValueError("csrf")
        raw_companies = document["companies"]
        if not isinstance(raw_companies, list) or len(raw_companies) != 3:
            raise ValueError("companies")
        companies: list[CompanyConfig] = []
        names: set[str] = set()
        approved: set[str] = set()
        for raw_company in raw_companies:
            if not isinstance(raw_company, dict) or set(raw_company) != _COMPANY_KEYS:
                raise ValueError("company shape")
            name = raw_company["canonical_name"]
            aliases = raw_company["aliases"]
            urls = raw_company["approved_urls"]
            if (
                not isinstance(name, str)
                or not name.strip()
                or name != name.strip()
                or len(name) > 128
                or name in names
                or not isinstance(aliases, list)
                or len(aliases) > 8
                or not all(
                    isinstance(alias, str)
                    and alias.strip() == alias
                    and 0 < len(alias) <= 128
                    for alias in aliases
                )
                or not isinstance(urls, list)
                or not 1 <= len(urls) <= 8
            ):
                raise ValueError("company invalid")
            company_urls = tuple(_absolute_url(url) for url in urls)
            if any(
                not url.startswith("https://") or url in approved
                for url in company_urls
            ):
                raise ValueError("company url")
            names.add(name)
            approved.update(company_urls)
            companies.append(CompanyConfig(name, tuple(aliases), company_urls))
        connect_timeout_seconds = _plain_int(document["connect_timeout_seconds"], 1, 10)
        request_timeout_seconds = _plain_int(document["request_timeout_seconds"], 1, 60)
        run_timeout_seconds = _plain_int(document["run_timeout_seconds"], 60, 1200)
        if run_timeout_seconds <= request_timeout_seconds:
            raise ValueError("timeout headroom")
        return AcceptanceConfig(
            schema_version=1,
            agent_id="hr-bot",
            api_base_url=api_base_url,
            public_origin=public_origin,
            owner_id=owner_id,
            session_cookie=cookie,
            csrf_token=csrf_token,
            companies=tuple(companies),
            connect_timeout_seconds=connect_timeout_seconds,
            request_timeout_seconds=request_timeout_seconds,
            run_timeout_seconds=run_timeout_seconds,
            poll_interval_seconds=_plain_int(document["poll_interval_seconds"], 1, 10),
            deployment_egress_evidence_sha256=(
                document["deployment_egress_evidence_sha256"]
                if isinstance(document["deployment_egress_evidence_sha256"], str)
                and _SHA256.fullmatch(document["deployment_egress_evidence_sha256"])
                else (_ for _ in ()).throw(ValueError("digest"))
            ),
        )
    except AcceptanceFailure:
        raise
    except (OSError, TypeError, UnicodeError, ValueError):
        raise _invalid() from None


def _created_ids(value: object) -> dict[str, tuple[UUID, ...]]:
    if not isinstance(value, Mapping) or set(value) != _CREATED_ID_KEYS:
        raise AcceptanceFailure("CREATED_IDS")
    parsed: dict[str, tuple[UUID, ...]] = {}
    try:
        for key in _CREATED_ID_KEYS:
            raw_ids = value[key]
            if not isinstance(raw_ids, (list, tuple)):
                raise TypeError
            ids = tuple(UUID(str(item)) for item in raw_ids)
            if len(ids) != len(set(ids)) or any(
                str(item) != str(raw) for item, raw in zip(ids, raw_ids)
            ):
                raise ValueError
            parsed[key] = ids
    except (KeyError, TypeError, ValueError):
        raise AcceptanceFailure("CREATED_IDS") from None
    return parsed


def _approved_source(config: AcceptanceConfig, source: object) -> bool:
    if not isinstance(source, str) or len(source) > 2048:
        return False
    try:
        canonical = _absolute_url(source)
    except ValueError:
        return False
    return any(
        canonical == base or canonical.startswith(base + "/")
        for company in config.companies
        for base in company.approved_urls
    )


def _validate_fixture_root(fixture_root: Path) -> None:
    try:
        if (
            not isinstance(fixture_root, Path)
            or not fixture_root.is_absolute()
            or fixture_root.is_symlink()
            or {item.name for item in fixture_root.iterdir()} != _FIXTURE_NAMES
        ):
            raise ValueError("fixture root")
        for name in _FIXTURE_NAMES:
            path = fixture_root / name
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or not 1 <= metadata.st_size <= 65_536
                or b"SYNTHETIC TEST DATA" not in path.read_bytes()
            ):
                raise ValueError("fixture")
    except (OSError, TypeError, ValueError):
        raise AcceptanceFailure("FIXTURE_INVALID") from None


def _validate_evidence(config: AcceptanceConfig, value: object) -> None:
    if not isinstance(value, Mapping):
        raise AcceptanceFailure("EVIDENCE_INVALID")
    if value.get("agent_id") != "hr-bot":
        raise AcceptanceFailure("WRONG_AGENT")
    if value.get("business_delivery_calls") != 0:
        raise AcceptanceFailure("BUSINESS_DELIVERY")
    if value.get("egress_evidence_sha256") != config.deployment_egress_evidence_sha256:
        raise AcceptanceFailure("EGRESS_EVIDENCE")
    turns = value.get("turns")
    if not isinstance(turns, list) or len(turns) != len(_EXPECTED_KINDS):
        raise AcceptanceFailure("TURN_EVIDENCE")
    for turn, expected_kind in zip(turns, _EXPECTED_KINDS):
        if not isinstance(turn, Mapping) or turn.get("completed") is not True:
            raise AcceptanceFailure("TURN_EVIDENCE")
        answer = turn.get("assistant_answer")
        trace = turn.get("trace_answer")
        if not isinstance(answer, str) or not answer.strip():
            raise AcceptanceFailure("EMPTY_ASSISTANT")
        if not isinstance(trace, str) or not trace.strip():
            raise AcceptanceFailure("EMPTY_TRACE")
        kind = turn.get("envelope_kind")
        if kind != expected_kind:
            raise AcceptanceFailure("INVALID_ENVELOPE")
        if expected_kind == "panorama_retrieval":
            if any(
                extract_hr_envelope(answer, envelope_kind) is not None
                for envelope_kind in (
                    "position_package",
                    "candidate_match",
                    "candidate_interview_plan",
                    "panorama_report",
                )
            ):
                raise AcceptanceFailure("INVALID_ENVELOPE")
            retrieval = turn.get("retrieval")
            if (
                not isinstance(retrieval, Mapping)
                or retrieval.get("company") != config.companies[0].canonical_name
                or not isinstance(retrieval.get("insight_version_ids"), list)
                or len(retrieval["insight_version_ids"]) != 1
                or not isinstance(retrieval.get("source_id"), str)
                or not isinstance(retrieval.get("as_of"), str)
            ):
                raise AcceptanceFailure("TURN_EVIDENCE")
            try:
                UUID(retrieval["insight_version_ids"][0])
                UUID(retrieval["source_id"])
                selected_as_of = datetime.fromisoformat(retrieval["as_of"])
            except (TypeError, ValueError):
                raise AcceptanceFailure("TURN_EVIDENCE") from None
            if selected_as_of.tzinfo is None:
                raise AcceptanceFailure("TURN_EVIDENCE")
        else:
            if extract_hr_envelope(answer, expected_kind) is None:
                raise AcceptanceFailure("INVALID_ENVELOPE")
            if extract_hr_envelope(trace, expected_kind) is None:
                raise AcceptanceFailure("INVALID_ENVELOPE")
        if (
            type(turn.get("progress_event_count")) is not int
            or turn["progress_event_count"] < 1
        ):
            raise AcceptanceFailure("TURN_EVIDENCE")
        sources = turn.get("source_urls")
        if not isinstance(sources, list) or not all(
            _approved_source(config, source) for source in sources
        ):
            raise AcceptanceFailure("SOURCE_SCOPE")
        if expected_kind == "panorama_report" and not sources:
            raise AcceptanceFailure("SOURCE_SCOPE")
    artifact = value.get("artifact")
    if not isinstance(artifact, Mapping) or artifact.get("state") != "ready":
        raise AcceptanceFailure("ARTIFACT_NOT_READY")
    if artifact.get("media_type") != "application/pdf":
        raise AcceptanceFailure("ARTIFACT_NOT_PDF")
    content = artifact.get("content")
    if not isinstance(content, bytes) or not content.startswith(b"%PDF-"):
        raise AcceptanceFailure("ARTIFACT_NOT_PDF")
    try:
        ticket = UUID(str(artifact.get("ticket_id")))
        downloaded = UUID(str(artifact.get("downloaded_ticket_id")))
    except (TypeError, ValueError):
        raise AcceptanceFailure("ARTIFACT_TICKET") from None
    if artifact.get("fresh_ticket") is not True or ticket != downloaded:
        raise AcceptanceFailure("ARTIFACT_TICKET")
    _created_ids(value.get("created_ids"))


def run_controlled_acceptance(
    config: AcceptanceConfig,
    gateway: AcceptanceGateway,
    *,
    fixture_root: Path,
    uuid_factory: Callable[[], UUID] = uuid4,
    monotonic: Callable[[], float] = time.monotonic,
) -> UUID:
    """Run once, validate durable evidence, and archive only recorded IDs."""
    run_id = uuid_factory()
    if not isinstance(run_id, UUID):
        raise AcceptanceFailure("RUN_ID")
    deadline = monotonic() + config.run_timeout_seconds
    execution_deadline = deadline - min(60, config.request_timeout_seconds)
    _validate_fixture_root(fixture_root)
    primary: BaseException | None = None
    evidence: object = None
    try:
        evidence = gateway.execute(
            config,
            run_id=run_id,
            fixture_root=fixture_root,
            deadline=execution_deadline,
        )
        _validate_evidence(config, evidence)
    except Exception as error:  # noqa: BLE001 - retain primary error through cleanup.
        primary = error
    try:
        ids = _created_ids(gateway.created_ids)
        if (
            isinstance(evidence, Mapping)
            and "created_ids" in evidence
            and _created_ids(evidence["created_ids"]) != ids
            and primary is None
        ):
            primary = AcceptanceFailure("CREATED_IDS")
        gateway.archive_exact(config, ids, deadline=deadline)
    except Exception:  # noqa: BLE001 - archival is best effort after a primary error.
        if primary is None:
            primary = AcceptanceFailure("ARCHIVE_FAILED")
    if primary is not None:
        if isinstance(primary, AcceptanceFailure):
            raise primary
        raise AcceptanceFailure("EXECUTION_FAILED") from None
    return run_id


class PlatformP0AcceptanceGateway:
    """Drive the deployed public API and inspect its durable projections."""

    def __init__(
        self, *, cleanup_manifest_path: Path = _DEFAULT_CLEANUP_MANIFEST
    ) -> None:
        self.created_ids: dict[str, list[str]] = {key: [] for key in _CREATED_ID_KEYS}
        self._client = None
        self._csrf = ""
        self._cleanup_manifest_path = cleanup_manifest_path

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AcceptanceFailure("TIMEOUT")
        return remaining

    def _request(
        self,
        method: str,
        path: str,
        *,
        deadline: float,
        expected: tuple[int, ...] = (200,),
        json_body: object | None = None,
        content: bytes | None = None,
        idempotency_key: UUID | None = None,
        params: Mapping[str, object] | None = None,
    ):
        if self._client is None:
            raise AcceptanceFailure("API_UNAVAILABLE")
        headers: dict[str, str] = {}
        if method not in {"GET", "HEAD"}:
            headers["Origin"] = "https://agent.orbbec.com.cn"
            headers["X-CSRF-Token"] = self._csrf
        if idempotency_key is not None:
            headers["Idempotency-Key"] = str(idempotency_key)
        try:
            self._remaining(deadline)
            response = self._client.request(
                method,
                path,
                headers=headers,
                json=json_body,
                content=content,
                params=params,
            )
        except Exception:  # noqa: BLE001 - HTTP client failures stay opaque.
            raise AcceptanceFailure("API_UNAVAILABLE") from None
        if response.status_code not in expected:
            raise AcceptanceFailure("API_CONTRACT")
        return response

    def _json(self, *args, **kwargs) -> dict[str, object]:
        response = self._request(*args, **kwargs)
        try:
            value = response.json()
        except ValueError:
            raise AcceptanceFailure("API_CONTRACT") from None
        if not isinstance(value, dict):
            raise AcceptanceFailure("API_CONTRACT")
        return value

    def _wait_conversation(
        self, conversation_id: str, turn_id: str, *, deadline: float
    ) -> tuple[str, list[str], object | None, int]:
        progress = 0
        while True:
            detail = self._json(
                "GET", f"/api/v1/conversations/{conversation_id}", deadline=deadline
            )
            turn = detail.get("current_turn")
            if not isinstance(turn, dict) or turn.get("turn_id") != turn_id:
                raise AcceptanceFailure("TURN_EVIDENCE")
            state = turn.get("status")
            if state == "completed":
                break
            if state in {"failed", "cancelled", "interrupted"}:
                raise AcceptanceFailure("TURN_FAILED")
            progress += 1
            time.sleep(min(2.0, self._remaining(deadline)))
        messages = self._json(
            "GET",
            f"/api/v1/conversations/{conversation_id}/messages",
            deadline=deadline,
            params={"limit": 200},
        ).get("items")
        if not isinstance(messages, list):
            raise AcceptanceFailure("TURN_EVIDENCE")
        assistant = next(
            (
                item
                for item in reversed(messages)
                if isinstance(item, dict)
                and item.get("role") == "assistant"
                and item.get("turn_id") == turn_id
            ),
            None,
        )
        if not isinstance(assistant, dict):
            raise AcceptanceFailure("EMPTY_ASSISTANT")
        answer = assistant.get("content")
        if not isinstance(answer, str) or not answer.strip():
            raise AcceptanceFailure("EMPTY_ASSISTANT")
        citations = assistant.get("citations", [])
        if not isinstance(citations, list):
            raise AcceptanceFailure("SOURCE_SCOPE")
        urls = [
            item["url"]
            for item in citations
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]
        return answer, urls, assistant.get("artifact_versions"), progress

    def _durable_execution_evidence(
        self,
        config: AcceptanceConfig,
        turns: list[dict[str, object]],
        conversation_turns: list[tuple[str, str]],
        *,
        deadline: float,
    ) -> int:
        import psycopg

        from app.config import load_config as load_platform_config
        from app.local_secrets import read_secret_file

        platform = load_platform_config()
        database_url = read_secret_file(
            platform.control_plane.control_database_url_file
        )
        self._remaining(deadline)
        with psycopg.connect(
            database_url,
            connect_timeout=max(1, min(10, int(self._remaining(deadline)))),
            options="-c statement_timeout=10000",
        ) as connection:
            for turn, (conversation_id, turn_id) in zip(
                turns, conversation_turns, strict=True
            ):
                progress = connection.execute(
                    "select count(*) from platform_control.conversation_events "
                    "where conversation_id=%s and turn_id=%s and event_type=any(%s)",
                    (
                        UUID(conversation_id),
                        UUID(turn_id),
                        [
                            "agent.progress",
                            "agent.task_progress",
                            "agent.work_update",
                            "agent.thinking_summary",
                        ],
                    ),
                ).fetchone()[0]
                if type(progress) is not int or progress < 1:
                    raise AcceptanceFailure("TURN_EVIDENCE")
                turn["progress_event_count"] = progress
            conversation_ids = [UUID(value) for value, _ in conversation_turns]
            deliveries = connection.execute(
                "select count(*) from platform_brain.agent_action_deliveries delivery "
                "join platform_brain.agent_task_actions action using(action_id) "
                "join platform_brain.agent_tasks task using(task_id) "
                "join platform_brain.brain_loops loop using(loop_id) "
                "where loop.conversation_id=any(%s)",
                (conversation_ids,),
            ).fetchone()[0]
        if type(deliveries) is not int:
            raise AcceptanceFailure("BUSINESS_DELIVERY")
        return deliveries

    def _wait_panorama(self, run_id: str, *, deadline: float) -> dict[str, object]:
        while True:
            run = self._json(
                "GET", f"/api/hr/panorama/runs/{run_id}", deadline=deadline
            )
            state = run.get("state")
            if state in {"completed", "partially_completed"}:
                return run
            if state == "failed":
                raise AcceptanceFailure("PANORAMA_FAILED")
            time.sleep(min(2.0, self._remaining(deadline)))

    def _wait_candidate_draft(
        self, draft_id: str, *, deadline: float
    ) -> dict[str, object]:
        while True:
            draft = self._json(
                "GET", f"/api/hr/candidate-drafts/{draft_id}", deadline=deadline
            )
            if draft.get("state") in {"ready", "failed", "confirmed", "dismissed"}:
                return draft
            time.sleep(min(2.0, self._remaining(deadline)))

    def _wait_task(
        self, position_id: str, task: Mapping[str, object], *, deadline: float
    ) -> dict[str, object]:
        task_id = str(task.get("task_id"))
        while True:
            current = self._json(
                "GET",
                f"/api/hr/positions/{position_id}/tasks/{task_id}",
                deadline=deadline,
            )
            if current.get("status") == "completed":
                return current
            if current.get("status") == "failed":
                raise AcceptanceFailure("TURN_FAILED")
            time.sleep(min(2.0, self._remaining(deadline)))

    def _upload(self, conversation_id: str, path: Path, *, deadline: float) -> str:
        content = path.read_bytes()
        mime = "text/markdown" if path.suffix == ".md" else "text/plain"
        upload = self._json(
            "POST",
            "/api/v1/attachments/uploads",
            deadline=deadline,
            expected=(201,),
            json_body={
                "conversation_id": conversation_id,
                "original_name": path.name,
                "declared_mime": mime,
                "declared_size": len(content),
            },
        )
        upload_id = str(upload.get("upload_id"))
        self._request(
            "PUT",
            f"/api/v1/attachments/uploads/{upload_id}/content",
            deadline=deadline,
            content=content,
        )
        completed = self._json(
            "POST",
            f"/api/v1/attachments/uploads/{upload_id}/complete",
            deadline=deadline,
        )
        return str(completed.get("attachment_id"))

    @staticmethod
    def _aware_time(value: object) -> datetime:
        if not isinstance(value, str):
            raise TypeError
        selected = datetime.fromisoformat(value)
        if selected.tzinfo is None:
            raise ValueError
        return selected

    def _flywheel_answer(
        self,
        config: AcceptanceConfig,
        conversation_id: str,
        turn_id: str,
        answer: str,
        *,
        deadline: float,
    ) -> str:
        # Bind the public Flywheel projection to this exact Platform turn through
        # the durable Mission run id, rather than accepting the first equal answer.
        import psycopg

        from app import local_secrets
        from app.config import load_config as load_platform_config

        self._remaining(deadline)
        try:
            UUID(conversation_id)
            UUID(turn_id)
            platform = load_platform_config()
            database_url = local_secrets.read_secret_file(
                platform.control_plane.control_database_url_file
            )
            with psycopg.connect(
                database_url,
                connect_timeout=max(1, min(10, int(self._remaining(deadline)))),
                options="-c statement_timeout=10000",
            ) as connection:
                binding = connection.execute(
                    "select run.run_id,run.created_at from "
                    "platform_control.missions mission join "
                    "platform_control.mission_runs run on "
                    "run.mission_id=mission.mission_id where "
                    "mission.owner_internal_user_id=%s and "
                    "mission.conversation_id=%s and mission.turn_id=%s and "
                    "mission.mode='direct_agent' and "
                    "mission.direct_agent_id='hr-bot' and "
                    "run.phase='direct' and run.agent_id='hr-bot' and "
                    "run.status='completed' order by run.created_at desc limit 1",
                    (config.owner_id, UUID(conversation_id), UUID(turn_id)),
                ).fetchone()
            if (
                binding is None
                or len(binding) != 2
                or not isinstance(binding[0], UUID)
                or not isinstance(binding[1], datetime)
                or binding[1].tzinfo is None
            ):
                raise ValueError
            run_id, run_created_at = binding
        except (OSError, TypeError, ValueError, psycopg.Error):
            raise AcceptanceFailure("TURN_EVIDENCE") from None
        expected_trace_key = f"metabot:hr-bot:{run_id}"
        query = answer.strip()[:96]
        while True:
            page = self._json(
                "GET",
                "/api/sessions",
                deadline=deadline,
                params={
                    "agent_id": "hr-bot",
                    "source_kind": "metabot",
                    "q": query,
                    "date_from": (run_created_at - timedelta(seconds=5)).isoformat(),
                    "limit": 100,
                },
            )
            items = page.get("items")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict) or not isinstance(
                        item.get("session_key"), str
                    ):
                        continue
                    detail = self._json(
                        "GET",
                        f"/api/sessions/{item['session_key']}",
                        deadline=deadline,
                    )
                    turns = detail.get("turns")
                    if not isinstance(turns, list):
                        continue
                    for turn in turns:
                        if not isinstance(turn, dict) or turn.get("answer") != answer:
                            continue
                        trace_key = turn.get("trace_key")
                        turn_key = turn.get("turn_key")
                        if trace_key != expected_trace_key or not isinstance(
                            turn_key, str
                        ):
                            continue
                        try:
                            observed_at = self._aware_time(turn.get("created_at"))
                        except (TypeError, ValueError):
                            continue
                        if observed_at < run_created_at - timedelta(seconds=5):
                            continue
                        trace = self._json(
                            "GET",
                            f"/api/turns/{turn_key}/trace",
                            deadline=deadline,
                        )
                        steps = trace.get("steps", [])
                        if not isinstance(steps, list):
                            raise AcceptanceFailure("BUSINESS_DELIVERY")
                        names = " ".join(
                            str(step.get("name", "")).casefold()
                            for step in steps
                            if isinstance(step, dict)
                        )
                        if any(
                            marker in names
                            for marker in (
                                "dingtalk",
                                "feishu",
                                "send_business_message",
                                "deliver_message",
                            )
                        ):
                            raise AcceptanceFailure("BUSINESS_DELIVERY")
                        try:
                            started_at = self._aware_time(trace.get("started_at"))
                            completed_at = self._aware_time(trace.get("completed_at"))
                        except (TypeError, ValueError):
                            continue
                        if (
                            trace.get("trace_key") == expected_trace_key
                            and trace.get("turn_key") == turn_key
                            and trace.get("agent_id", "hr-bot") == "hr-bot"
                            and trace.get("source_kind", "metabot") == "metabot"
                            and trace.get("status") == "completed"
                            and started_at >= run_created_at - timedelta(seconds=5)
                            and completed_at >= started_at
                            and completed_at
                            <= datetime.now(timezone.utc) + timedelta(minutes=5)
                        ):
                            return answer
            time.sleep(min(2.0, self._remaining(deadline)))

    def _panorama_retrieval(
        self,
        config: AcceptanceConfig,
        *,
        position_id: str,
        conversation_id: str,
        turn_id: str,
        insight_version_id: str,
        source_id: str,
        company: CompanyConfig,
        expected_facts: list[dict[str, object]],
        deadline: float,
    ) -> dict[str, object]:
        import psycopg

        from app import local_secrets
        from app.config import load_config as load_platform_config

        try:
            expected_insight = UUID(insight_version_id)
            expected_source = UUID(source_id)
            platform = load_platform_config()
            database_url = local_secrets.read_secret_file(
                platform.control_plane.control_database_url_file
            )
            with psycopg.connect(
                database_url,
                connect_timeout=max(1, min(10, int(self._remaining(deadline)))),
                options="-c statement_timeout=10000",
            ) as connection:
                row = connection.execute(
                    "select insight_version_ids,retrieved_excerpts from "
                    "platform_hr.position_insight_retrievals where "
                    "owner_internal_user_id=%s and position_id=%s and "
                    "conversation_id=%s and turn_id=%s",
                    (
                        config.owner_id,
                        UUID(position_id),
                        UUID(conversation_id),
                        UUID(turn_id),
                    ),
                ).fetchone()
            if row is None or len(row) != 2:
                raise ValueError
            insight_ids, excerpts = row
            if (
                list(insight_ids) != [expected_insight]
                or not isinstance(excerpts, list)
                or len(excerpts) != 1
            ):
                raise ValueError
            document = excerpts[0]
            if not isinstance(document, dict):
                raise TypeError
            urls = document.get("source_urls")
            facts = document.get("facts")
            freshness = document.get("freshness")
            expected_pairs = {
                (str(item["source_url"]), str(item["observed_at"]))
                for item in expected_facts
            }
            if not isinstance(facts, list) or not facts:
                raise ValueError
            observed_pairs = [
                (str(fact["source_url"]), str(fact["observed_at"]))
                for fact in facts
                if isinstance(fact, dict)
                and "source_url" in fact
                and "observed_at" in fact
            ]
            allowed_urls = list(dict.fromkeys(pair[0] for pair in observed_pairs))
            expected_as_of = max(self._aware_time(pair[1]) for pair in observed_pairs)
            if (
                document.get("insight_version_ids") != [insight_version_id]
                or len(observed_pairs) != len(facts)
                or any(pair not in expected_pairs for pair in observed_pairs)
                or urls != allowed_urls
                or not isinstance(freshness, dict)
                or self._aware_time(freshness.get("as_of")) != expected_as_of
            ):
                raise ValueError
        except (KeyError, OSError, TypeError, ValueError, psycopg.Error):
            raise AcceptanceFailure("TURN_EVIDENCE") from None
        return {
            "insight_version_ids": [insight_version_id],
            "source_id": str(expected_source),
            "company": company.canonical_name,
            "as_of": expected_as_of.isoformat(),
            "source_urls": list(dict.fromkeys(allowed_urls)),
        }

    def execute(
        self,
        config: AcceptanceConfig,
        *,
        run_id: UUID,
        fixture_root: Path,
        deadline: float,
    ) -> dict[str, object]:
        import httpx

        cookie_name, cookie_value = config.session_cookie.split("=", 1)
        timeout = httpx.Timeout(
            config.request_timeout_seconds,
            connect=config.connect_timeout_seconds,
        )
        turns: list[dict[str, object]] = []
        conversation_turns: list[tuple[str, str]] = []
        with httpx.Client(
            base_url=config.api_base_url,
            cookies={cookie_name: cookie_value},
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            self._client = client
            client.cookies.set("__Host-platform_csrf", config.csrf_token)
            account = self._json("GET", "/api/v1/account", deadline=deadline)
            if account.get("internal_user_id") != str(config.owner_id):
                raise AcceptanceFailure("WRONG_OWNER")
            csrf = account.get("csrf_token")
            if csrf != config.csrf_token:
                raise AcceptanceFailure("CSRF_UNAVAILABLE")
            self._csrf = csrf

            listed_sources = self._json(
                "GET", "/api/hr/panorama/sources", deadline=deadline
            ).get("items")
            if not isinstance(listed_sources, list):
                raise AcceptanceFailure("API_CONTRACT")
            sources: list[dict[str, object]] = []
            for company in config.companies:
                matches = [
                    item
                    for item in listed_sources
                    if isinstance(item, dict)
                    and item.get("canonical_name") == company.canonical_name
                ]
                if len(matches) > 1:
                    raise AcceptanceFailure("SOURCE_SCOPE")
                source = matches[0] if matches else None
                if source is not None and (
                    source.get("active") is not True
                    or source.get("aliases") != list(company.aliases)
                    or source.get("approved_urls") != list(company.approved_urls)
                ):
                    raise AcceptanceFailure("SOURCE_SCOPE")
                if source is None:
                    source = self._json(
                        "POST",
                        "/api/hr/panorama/sources",
                        deadline=deadline,
                        idempotency_key=uuid5(
                            run_id, f"source:{company.canonical_name}"
                        ),
                        json_body={
                            "canonical_name": company.canonical_name,
                            "aliases": list(company.aliases),
                            "approved_urls": list(company.approved_urls),
                        },
                    )
                if not isinstance(source.get("source_id"), str):
                    raise AcceptanceFailure("API_CONTRACT")
                sources.append(source)
            panorama = self._json(
                "POST",
                "/api/hr/panorama/runs",
                deadline=deadline,
                expected=(202,),
                idempotency_key=uuid5(run_id, "panorama"),
                json_body={"source_ids": [item["source_id"] for item in sources]},
            )
            panorama_run_id = str(panorama.get("run_id"))
            panorama_conversation_id = panorama.get("conversation_id")
            if not isinstance(panorama_conversation_id, str):
                raise AcceptanceFailure("PANORAMA_FAILED")
            UUID(panorama_conversation_id)
            self.created_ids["conversation_ids"].append(panorama_conversation_id)
            self._wait_panorama(panorama_run_id, deadline=deadline)
            reports = self._json(
                "GET", "/api/hr/panorama/reports", deadline=deadline
            ).get("items")
            report_summary = next(
                (
                    item
                    for item in (reports if isinstance(reports, list) else [])
                    if isinstance(item, dict) and item.get("run_id") == panorama_run_id
                ),
                None,
            )
            if not isinstance(report_summary, dict):
                raise AcceptanceFailure("PANORAMA_FAILED")
            report = self._json(
                "GET",
                f"/api/hr/panorama/reports/{report_summary['insight_version_id']}",
                deadline=deadline,
            )
            insight = report.get("insight")
            snapshots = report.get("snapshots")
            if not isinstance(insight, dict) or not isinstance(snapshots, list):
                raise AcceptanceFailure("PANORAMA_FAILED")
            panorama_answer, _, _, progress = self._wait_conversation(
                str(insight.get("source_conversation_id")),
                str(insight.get("source_turn_id")),
                deadline=deadline,
            )
            panorama_urls = [
                item["source_url"]
                for item in snapshots
                if isinstance(item, dict) and isinstance(item.get("source_url"), str)
            ]
            turns.append(
                {
                    "completed": True,
                    "assistant_answer": panorama_answer,
                    "trace_answer": self._flywheel_answer(
                        config,
                        str(insight["source_conversation_id"]),
                        str(insight["source_turn_id"]),
                        panorama_answer,
                        deadline=deadline,
                    ),
                    "envelope_kind": "panorama_report",
                    "source_urls": panorama_urls,
                    "progress_event_count": progress,
                }
            )
            conversation_turns.append(
                (
                    str(insight["source_conversation_id"]),
                    str(insight["source_turn_id"]),
                )
            )
            if str(insight["source_conversation_id"]) != panorama_conversation_id:
                raise AcceptanceFailure("PANORAMA_FAILED")

            position_started = self._json(
                "POST",
                "/api/v1/agents/hr-bot/conversations",
                deadline=deadline,
                expected=(201,),
                idempotency_key=uuid5(run_id, "position"),
                json_body={
                    "text": (
                        f"合成验收 {run_id}：拟定一个结构工程岗位初版。"
                        "输出完整可读 Markdown，并追加唯一合法的 "
                        "position_package platform-hr-v1 envelope；不要发送任何业务消息。"
                    )
                },
            )
            position_conversation = str(
                position_started["conversation"]["conversation_id"]
            )
            position_turn = str(position_started["turn"]["turn_id"])
            self.created_ids["conversation_ids"].append(position_conversation)
            answer, urls, _, progress = self._wait_conversation(
                position_conversation, position_turn, deadline=deadline
            )
            turns.append(
                {
                    "completed": True,
                    "assistant_answer": answer,
                    "trace_answer": self._flywheel_answer(
                        config,
                        position_conversation,
                        position_turn,
                        answer,
                        deadline=deadline,
                    ),
                    "envelope_kind": "position_package",
                    "source_urls": urls,
                    "progress_event_count": progress,
                }
            )
            conversation_turns.append((position_conversation, position_turn))
            package = self._json(
                "GET",
                f"/api/hr/conversations/{position_conversation}/position-package",
                deadline=deadline,
            )
            modules_v1 = package.get("modules")
            if (
                package.get("conversation_id") != position_conversation
                or package.get("version_number") != 1
                or not isinstance(modules_v1, dict)
                or set(modules_v1) != {"mission", "jd", "jr"}
                or any(
                    not isinstance(module, dict)
                    or not isinstance(module.get("text"), str)
                    or not module["text"].strip()
                    for module in modules_v1.values()
                )
            ):
                raise AcceptanceFailure("API_CONTRACT")
            confirmed = self._json(
                "POST",
                f"/api/hr/position-drafts/{package['draft_id']}/versions/"
                f"{package['draft_version_id']}/confirm",
                deadline=deadline,
                idempotency_key=uuid5(run_id, "confirm-position"),
                json_body={"expected_row_version": package["row_version"]},
            )
            position_id = str(confirmed.get("position_id"))
            context_v1 = str(confirmed.get("context_version_id"))
            if confirmed.get("conversation_id") != position_conversation:
                raise AcceptanceFailure("API_CONTRACT")
            try:
                UUID(position_id)
                UUID(context_v1)
            except ValueError:
                raise AcceptanceFailure("API_CONTRACT") from None
            self.created_ids["position_ids"].append(position_id)

            first_company = config.companies[0]
            retrieval_started = self._json(
                "POST",
                f"/api/v1/conversations/{position_conversation}/messages",
                deadline=deadline,
                expected=(201,),
                idempotency_key=uuid5(run_id, "position-panorama-retrieval"),
                json_body={
                    "text": (
                        f"仅按需读取并说明{first_company.canonical_name}的最新招聘情报，"
                        "给出来源和截至时间；这是普通问答，不要生成或修改岗位草案，"
                        "不要发送任何业务消息。"
                    )
                },
            )
            retrieval_turn = str(retrieval_started.get("turn", {}).get("turn_id"))
            try:
                UUID(retrieval_turn)
            except ValueError:
                raise AcceptanceFailure("API_CONTRACT") from None
            retrieval_answer, _, _, retrieval_progress = self._wait_conversation(
                position_conversation, retrieval_turn, deadline=deadline
            )
            insight_id = str(report_summary.get("insight_version_id"))
            source_id = str(sources[0].get("source_id"))
            source_urls = {
                snapshot["source_url"]
                for snapshot in snapshots
                if isinstance(snapshot, dict)
                and snapshot.get("source_id") == source_id
                and isinstance(snapshot.get("source_url"), str)
            }
            raw_facts = insight.get("facts")
            expected_facts = [
                fact
                for fact in (raw_facts if isinstance(raw_facts, list) else [])
                if isinstance(fact, dict)
                and isinstance(fact.get("source_url"), str)
                and fact["source_url"] in source_urls
                and any(
                    fact["source_url"] == base
                    or fact["source_url"].startswith(f"{base}/")
                    for base in first_company.approved_urls
                )
            ]
            retrieval = self._panorama_retrieval(
                config,
                position_id=position_id,
                conversation_id=position_conversation,
                turn_id=retrieval_turn,
                insight_version_id=insight_id,
                source_id=source_id,
                company=first_company,
                expected_facts=expected_facts,
                deadline=deadline,
            )
            turns.append(
                {
                    "completed": True,
                    "assistant_answer": retrieval_answer,
                    "trace_answer": self._flywheel_answer(
                        config,
                        position_conversation,
                        retrieval_turn,
                        retrieval_answer,
                        deadline=deadline,
                    ),
                    "envelope_kind": "panorama_retrieval",
                    "retrieval": retrieval,
                    "source_urls": retrieval["source_urls"],
                    "progress_event_count": retrieval_progress,
                }
            )
            conversation_turns.append((position_conversation, retrieval_turn))

            revision_started = self._json(
                "POST",
                f"/api/v1/conversations/{position_conversation}/messages",
                deadline=deadline,
                expected=(201,),
                idempotency_key=uuid5(run_id, "position-revision"),
                json_body={
                    "text": (
                        f"依据刚才读取的{first_company.canonical_name}招聘情报修订 JD 和 JR。"
                        "输出完整可读 Markdown，并追加唯一合法的 position_package "
                        "platform-hr-v1 envelope；不要发送任何业务消息。"
                    )
                },
            )
            revision_turn = str(revision_started.get("turn", {}).get("turn_id"))
            try:
                UUID(revision_turn)
            except ValueError:
                raise AcceptanceFailure("API_CONTRACT") from None
            revision_answer, revision_urls, _, revision_progress = (
                self._wait_conversation(
                    position_conversation, revision_turn, deadline=deadline
                )
            )
            revision_envelope = extract_hr_envelope(revision_answer, "position_package")
            modules_v2 = (
                revision_envelope.payload.get("modules")
                if revision_envelope is not None
                else None
            )
            if (
                not isinstance(modules_v2, dict)
                or set(modules_v2) != {"mission", "jd", "jr"}
                or modules_v2.get("jd") == modules_v1.get("jd")
                or modules_v2.get("jr") == modules_v1.get("jr")
            ):
                raise AcceptanceFailure("INVALID_ENVELOPE")
            context_draft = self._json(
                "POST",
                f"/api/hr/positions/{position_id}/context/drafts",
                deadline=deadline,
                idempotency_key=uuid5(run_id, "context-v2-draft"),
                json_body={
                    "base_context_version_id": context_v1,
                    "official_version_id": None,
                    "modules": modules_v2,
                    "summary": "SYNTHETIC TEST DATA · 根据点名公司情报修订 JD/JR",
                    "source_conversation_id": position_conversation,
                    "source_turn_id": revision_turn,
                    "source_artifact_version_id": None,
                    "source_material_attachment_ids": [],
                    "agent_id": "hr-bot",
                    "model_version": "controlled-live-p0",
                },
            )
            context_v2_draft = str(context_draft.get("context_version_id"))
            if context_draft.get("modules") != modules_v2:
                raise AcceptanceFailure("API_CONTRACT")
            context_v2 = self._json(
                "POST",
                f"/api/hr/positions/{position_id}/context/drafts/"
                f"{context_v2_draft}/confirm",
                deadline=deadline,
                idempotency_key=uuid5(run_id, "confirm-context-v2"),
                json_body={
                    "expected_current_context_version_id": context_v1,
                    "expected_draft_row_version": context_draft.get("row_version"),
                    "module_names": ["mission", "jd", "jr"],
                },
            )
            context_id = str(context_v2.get("context_version_id"))
            if (
                context_id in {context_v1, context_v2_draft}
                or context_v2.get("version_number") != 2
                or context_v2.get("state") != "confirmed"
                or context_v2.get("modules") != modules_v2
            ):
                raise AcceptanceFailure("API_CONTRACT")
            try:
                UUID(context_v2_draft)
                UUID(context_id)
            except ValueError:
                raise AcceptanceFailure("API_CONTRACT") from None
            turns.append(
                {
                    "completed": True,
                    "assistant_answer": revision_answer,
                    "trace_answer": self._flywheel_answer(
                        config,
                        position_conversation,
                        revision_turn,
                        revision_answer,
                        deadline=deadline,
                    ),
                    "envelope_kind": "position_package",
                    "source_urls": revision_urls,
                    "progress_event_count": revision_progress,
                }
            )
            conversation_turns.append((position_conversation, revision_turn))

            attachment_ids = [
                self._upload(
                    position_conversation, fixture_root / name, deadline=deadline
                )
                for name in (
                    "resume-strong.md",
                    "resume-adjacent.md",
                    "resume-invalid.txt",
                )
            ]
            batch = self._json(
                "POST",
                f"/api/hr/positions/{position_id}/candidate-drafts:batch",
                deadline=deadline,
                expected=(202,),
                idempotency_key=uuid5(run_id, "candidate-batch"),
                json_body={"attachment_ids": attachment_ids},
            )
            raw_drafts = batch.get("items")
            if not isinstance(raw_drafts, list) or len(raw_drafts) != 3:
                raise AcceptanceFailure("CANDIDATE_PARSE")
            drafts = [
                self._wait_candidate_draft(str(item["draft_id"]), deadline=deadline)
                for item in raw_drafts
                if isinstance(item, dict)
            ]
            if len(drafts) != 3 or [item.get("state") for item in drafts[:2]] != [
                "ready",
                "ready",
            ]:
                raise AcceptanceFailure("CANDIDATE_PARSE")
            invalid = drafts[2]
            if invalid.get("state") != "failed":
                raise AcceptanceFailure("CANDIDATE_ISOLATION")
            retried = self._json(
                "POST",
                f"/api/hr/candidate-drafts/{invalid['draft_id']}:retry",
                deadline=deadline,
                idempotency_key=uuid5(run_id, "retry-invalid"),
                json_body={"expected_row_version": invalid["row_version"]},
            )
            retry_result = self._wait_candidate_draft(
                str(retried["draft_id"]), deadline=deadline
            )
            if retry_result.get("state") != "failed":
                raise AcceptanceFailure("CANDIDATE_ISOLATION")
            self._json(
                "POST",
                f"/api/hr/candidate-drafts/{retry_result['draft_id']}:dismiss",
                deadline=deadline,
                idempotency_key=uuid5(run_id, "dismiss-invalid"),
                json_body={"expected_row_version": retry_result["row_version"]},
            )

            relations: list[dict[str, object]] = []
            for index, draft in enumerate(drafts[:2]):
                facts = draft.get("extracted_facts")
                if not isinstance(facts, dict) or not isinstance(
                    facts.get("stable_name"), str
                ):
                    raise AcceptanceFailure("CANDIDATE_PARSE")
                result = self._json(
                    "POST",
                    f"/api/hr/candidate-drafts/{draft['draft_id']}:confirm",
                    deadline=deadline,
                    expected=(201,),
                    idempotency_key=uuid5(run_id, f"confirm-candidate:{index}"),
                    json_body={
                        "expected_row_version": draft["row_version"],
                        "stable_name": facts["stable_name"],
                        "confirmed_facts": facts,
                        "merge_candidate_id": None,
                        "context_version_id": context_id,
                    },
                )
                candidate = result.get("candidate")
                document = result.get("document")
                relation = result.get("position_candidate")
                if not all(
                    isinstance(item, dict) for item in (candidate, document, relation)
                ):
                    raise AcceptanceFailure("CANDIDATE_PARSE")
                self.created_ids["candidate_ids"].append(str(candidate["candidate_id"]))
                self.created_ids["candidate_document_ids"].append(
                    str(document["document_id"])
                )
                self.created_ids["position_candidate_ids"].append(
                    str(relation["position_candidate_id"])
                )
                relations.append(relation)

            for index, relation in enumerate(relations):
                task = self._json(
                    "POST",
                    f"/api/hr/positions/{position_id}/tasks",
                    deadline=deadline,
                    expected=(202,),
                    idempotency_key=uuid5(run_id, f"candidate-match:{index}"),
                    json_body={
                        "task_kind": "candidate_match",
                        "context_version_id": context_id,
                        "candidate_id": relation["candidate_id"],
                        "position_candidate_id": relation["position_candidate_id"],
                    },
                )
                conversation_id = str(task.get("conversation_id"))
                turn_id = str(task.get("turn_id"))
                try:
                    UUID(conversation_id)
                    UUID(turn_id)
                except ValueError:
                    raise AcceptanceFailure("API_CONTRACT") from None
                self.created_ids["conversation_ids"].append(conversation_id)
                completed_task = self._wait_task(position_id, task, deadline=deadline)
                if (
                    completed_task.get("conversation_id") != conversation_id
                    or completed_task.get("turn_id") != turn_id
                ):
                    raise AcceptanceFailure("API_CONTRACT")
                answer, urls, _, progress = self._wait_conversation(
                    conversation_id, turn_id, deadline=deadline
                )
                turns.append(
                    {
                        "completed": True,
                        "assistant_answer": answer,
                        "trace_answer": self._flywheel_answer(
                            config,
                            conversation_id,
                            turn_id,
                            answer,
                            deadline=deadline,
                        ),
                        "envelope_kind": "candidate_match",
                        "source_urls": urls,
                        "progress_event_count": progress,
                    }
                )
                conversation_turns.append((conversation_id, turn_id))
                analyses = self._json(
                    "GET",
                    f"/api/hr/position-candidates/"
                    f"{relation['position_candidate_id']}/analyses",
                    deadline=deadline,
                ).get("items")
                match = next(
                    (
                        item
                        for item in (analyses if isinstance(analyses, list) else [])
                        if isinstance(item, dict)
                        and item.get("analysis_kind") == "match"
                    ),
                    None,
                )
                if (
                    not isinstance(match, dict)
                    or match.get("candidate_id") != relation["candidate_id"]
                    or match.get("context_version_id") != context_id
                ):
                    raise AcceptanceFailure("CANDIDATE_PARSE")

            primary = relations[0]
            interview_task = self._json(
                "POST",
                f"/api/hr/positions/{position_id}/tasks",
                deadline=deadline,
                expected=(202,),
                idempotency_key=uuid5(run_id, "candidate-interview"),
                json_body={
                    "task_kind": "candidate_interview_plan",
                    "context_version_id": context_id,
                    "candidate_id": primary["candidate_id"],
                    "position_candidate_id": primary["position_candidate_id"],
                },
            )
            interview_conversation = str(interview_task.get("conversation_id"))
            interview_turn = str(interview_task.get("turn_id"))
            try:
                UUID(interview_conversation)
                UUID(interview_turn)
            except ValueError:
                raise AcceptanceFailure("API_CONTRACT") from None
            self.created_ids["conversation_ids"].append(interview_conversation)
            completed_interview_task = self._wait_task(
                position_id, interview_task, deadline=deadline
            )
            if (
                completed_interview_task.get("conversation_id")
                != interview_conversation
                or completed_interview_task.get("turn_id") != interview_turn
            ):
                raise AcceptanceFailure("API_CONTRACT")
            answer, urls, artifacts, progress = self._wait_conversation(
                interview_conversation, interview_turn, deadline=deadline
            )
            turns.append(
                {
                    "completed": True,
                    "assistant_answer": answer,
                    "trace_answer": self._flywheel_answer(
                        config,
                        interview_conversation,
                        interview_turn,
                        answer,
                        deadline=deadline,
                    ),
                    "envelope_kind": "candidate_interview_plan",
                    "source_urls": urls,
                    "progress_event_count": progress,
                }
            )
            conversation_turns.append((interview_conversation, interview_turn))
            analyses = self._json(
                "GET",
                f"/api/hr/position-candidates/"
                f"{primary['position_candidate_id']}/analyses",
                deadline=deadline,
            ).get("items")
            interview = next(
                (
                    item
                    for item in (analyses if isinstance(analyses, list) else [])
                    if isinstance(item, dict)
                    and item.get("analysis_kind") == "candidate_interview_plan"
                ),
                None,
            )
            if not isinstance(interview, dict) or not isinstance(
                interview.get("source_artifact_version_id"), str
            ):
                raise AcceptanceFailure("ARTIFACT_NOT_READY")
            ready = next(
                (
                    item
                    for item in artifacts
                    if isinstance(artifacts, list)
                    if isinstance(item, dict)
                    and item.get("status") == "ready"
                    and isinstance(item.get("attachment"), dict)
                    and item["attachment"].get("detected_mime") == "application/pdf"
                ),
                None,
            )
            if not isinstance(ready, dict):
                raise AcceptanceFailure("ARTIFACT_NOT_READY")
            attachment_id = str(ready["attachment"]["attachment_id"])
            resources = self._json(
                "GET",
                f"/api/hr/positions/{position_id}/resources",
                deadline=deadline,
            ).get("artifacts")
            durable_artifact = next(
                (
                    item
                    for item in (resources if isinstance(resources, list) else [])
                    if isinstance(item, dict)
                    and item.get("attachment_id") == attachment_id
                    and item.get("artifact_version_id")
                    == interview["source_artifact_version_id"]
                ),
                None,
            )
            if (
                not isinstance(durable_artifact, dict)
                or durable_artifact.get("state") != "ready"
                or durable_artifact.get("media_type") != "application/pdf"
                or durable_artifact.get("download_available") is not True
            ):
                raise AcceptanceFailure("ARTIFACT_NOT_READY")
            tickets = [
                self._json(
                    "POST",
                    f"/api/hr/positions/{position_id}/resources/{attachment_id}/ticket",
                    deadline=deadline,
                    json_body={"purpose": "download"},
                )
                for _ in range(2)
            ]
            if tickets[0].get("content_path") == tickets[1].get("content_path"):
                raise AcceptanceFailure("ARTIFACT_TICKET")
            content_path = tickets[1].get("content_path")
            if not isinstance(content_path, str) or not content_path.startswith(
                "/api/v1/attachments/content/"
            ):
                raise AcceptanceFailure("ARTIFACT_TICKET")
            pdf = self._request("GET", content_path, deadline=deadline).content
            ticket_id = uuid5(run_id, str(content_path))
            self._client = None

        business_delivery_calls = self._durable_execution_evidence(
            config,
            turns,
            conversation_turns,
            deadline=deadline,
        )

        return {
            "agent_id": "hr-bot",
            "business_delivery_calls": business_delivery_calls,
            "egress_evidence_sha256": config.deployment_egress_evidence_sha256,
            "turns": turns,
            "artifact": {
                "state": "ready",
                "media_type": "application/pdf",
                "content": pdf,
                "ticket_id": str(ticket_id),
                "downloaded_ticket_id": str(ticket_id),
                "fresh_ticket": True,
            },
            "created_ids": self.created_ids,
        }

    def archive_exact(
        self,
        config: AcceptanceConfig,
        created_ids: dict[str, tuple[UUID, ...]],
        *,
        deadline: float,
    ) -> None:
        import httpx

        self._remaining(deadline)
        manifest = json.dumps(
            {
                "schema_version": 1,
                "owner_id": str(config.owner_id),
                "created_ids": {
                    key: [str(value) for value in created_ids[key]]
                    for key in sorted(_CREATED_ID_KEYS)
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        path = self._cleanup_manifest_path
        try:
            parent = path.parent
            metadata = parent.lstat()
            if (
                not path.is_absolute()
                or parent.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or metadata.st_uid != os.geteuid()
            ):
                raise OSError
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(manifest)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise AcceptanceFailure("ARCHIVE_FAILED") from None

        archive_failed = False
        cookie_name, cookie_value = config.session_cookie.split("=", 1)
        try:
            with httpx.Client(
                base_url=config.api_base_url,
                cookies={cookie_name: cookie_value},
                timeout=httpx.Timeout(
                    config.request_timeout_seconds,
                    connect=config.connect_timeout_seconds,
                ),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                self._client = client
                client.cookies.set("__Host-platform_csrf", config.csrf_token)
                account = self._json("GET", "/api/v1/account", deadline=deadline)
                csrf = account.get("csrf_token")
                if csrf != config.csrf_token:
                    raise AcceptanceFailure("ARCHIVE_FAILED")
                self._csrf = csrf
                for conversation_id in created_ids["conversation_ids"]:
                    try:
                        self._request(
                            "POST",
                            f"/api/v1/conversations/{conversation_id}/archive",
                            deadline=deadline,
                        )
                    except AcceptanceFailure:
                        archive_failed = True
        except AcceptanceFailure:
            archive_failed = True
        finally:
            self._client = None
        if archive_failed:
            raise AcceptanceFailure("ARCHIVE_FAILED") from None


def build_gateway(_config: AcceptanceConfig) -> AcceptanceGateway:
    """Build the fixed deployed Platform API gateway."""
    return PlatformP0AcceptanceGateway()


def main(
    argv: list[str] | None = None,
    *,
    config_path: Path = _DEFAULT_CONFIG,
    fixture_root: Path = _DEFAULT_FIXTURES,
    gateway_factory: Callable[[AcceptanceConfig], AcceptanceGateway] = build_gateway,
    uuid_factory: Callable[[], UUID] = uuid4,
    monotonic: Callable[[], float] = time.monotonic,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if arguments:
            raise AcceptanceFailure("ARGUMENT_INVALID")
        config = load_config(config_path, expected_path=config_path)
        gateway = gateway_factory(config)
        run_id = run_controlled_acceptance(
            config,
            gateway,
            fixture_root=fixture_root,
            uuid_factory=uuid_factory,
            monotonic=monotonic,
        )
    except AcceptanceFailure as error:
        print(f"HR_P0_ACCEPTANCE_FAILED {error}", file=stderr)
        return 1
    except Exception:  # noqa: BLE001 - process output must stay status-only.
        print("HR_P0_ACCEPTANCE_FAILED INTERNAL", file=stderr)
        return 1
    print(f"HR_P0_ACCEPTANCE_OK {run_id}", file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
