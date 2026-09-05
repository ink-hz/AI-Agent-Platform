# ruff: noqa: TRY004
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg.rows import dict_row

from app.agent_brain.conversation_repository import (
    ConversationRepositoryConflict,
    ConversationRepositoryNotFound,
    ConversationTurnInProgress,
)

from .panorama_models import (
    CreatePublicJobSnapshot,
    CreateTalentInsightVersion,
    PanoramaRun,
    TalentSource,
    TransitionPanoramaRun,
    canonical_panorama_url,
)
from .structured_output import extract_hr_envelope

logger = logging.getLogger(__name__)

_RUNTIME_NAMESPACE = uuid5(NAMESPACE_URL, "orbbec:hr:panorama-runtime:v1")
_NON_NATIVE_IPV6_PREFIXES = tuple(
    ipaddress.ip_network(value)
    for value in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)


class _PanoramaPromptInvalid(ValueError):
    pass


PANORAMA_REPORT_CONTRACT_V1 = """Return a human-readable Markdown report, then exactly one hidden `panorama_report` envelope using `<!-- platform-hr-v1:<unpadded-base64url-canonical-json> -->`.
The payload has exactly these top-level keys: `companies`, `jobs`, `facts`, `direction_clusters`, `inferences`, `unknowns`, `summary`.
Each company has exactly `source_id`, `canonical_name`, `approved_urls`, `status`, `error_code`; status is `completed` with null error_code or `failed` with error_code `SEARCH_UNAVAILABLE`.
Each job has exactly `company`, `public_job_key`, `title`, `location`, `duty_excerpt`, `requirement_excerpt`, `source_url`, `observed_at`, `content_sha256` (lowercase SHA-256).
Each fact has exactly `fact_id`, `text`, `company`, `public_job_key`, `source_url`, `observed_at` and references one returned job.
Each inference has exactly `text`, `basis_fact_ids` and at least one existing fact id. Each unknown has exactly `text`.
If any source is completed, the report must contain at least one evidenced job and fact; never invent a completed source when no public evidence was retrieved.
Every completed company must have its own returned job or fact evidence; otherwise mark that company failed with `SEARCH_UNAVAILABLE` so a mixed result is partial.
Preserve the supplied company order, job discovery order, fact order, and inference order."""


def _system_resolver(hostname: str, port: int) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item[4][0]
            for item in socket.getaddrinfo(
                hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
            )
        )
    )


def _is_public_unicast(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and any(
        address in prefix for prefix in _NON_NATIVE_IPV6_PREFIXES
    ):
        return False
    candidates = [address]
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        candidates.append(address.ipv4_mapped)
    return all(
        candidate.is_global
        and not candidate.is_multicast
        and not candidate.is_unspecified
        and not candidate.is_loopback
        and not candidate.is_link_local
        and not candidate.is_private
        and not candidate.is_reserved
        for candidate in candidates
    )


def validate_panorama_destination(
    raw: str,
    *,
    resolver: Callable[[str, int], tuple[str, ...] | list[str]] = _system_resolver,
) -> str:
    """Re-parse and resolve one Platform-approved destination before dispatch/use."""
    try:
        selected = canonical_panorama_url(raw)
        hostname = urlsplit(selected).hostname
    except ValueError:
        raise ValueError("panorama destination invalid") from None
    if hostname is None:
        raise ValueError("panorama destination invalid")
    try:
        addresses = tuple(resolver(hostname, 443))
        parsed_addresses = tuple(ipaddress.ip_address(value) for value in addresses)
    except Exception:  # noqa: BLE001 - injected DNS failures must fail closed
        raise ValueError("panorama destination invalid") from None
    if not parsed_addresses or any(
        not _is_public_unicast(address) for address in parsed_addresses
    ):
        raise ValueError("panorama destination invalid")
    return selected


def _approved(url: str, approved_urls: tuple[str, ...]) -> bool:
    return any(
        url == prefix
        or (
            "?" not in prefix
            and "#" not in prefix
            and (
                url.startswith(prefix)
                if prefix.endswith("/")
                else url.startswith(prefix + "/")
            )
        )
        for prefix in approved_urls
    )


def _runtime_id(run_id: UUID, purpose: str) -> UUID:
    return uuid5(_RUNTIME_NAMESPACE, f"{run_id}:{purpose}")


@dataclass(frozen=True, slots=True)
class PanoramaRunRuntime:
    run: PanoramaRun
    sources: tuple[TalentSource, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.run, PanoramaRun)
            or not isinstance(self.sources, tuple)
            or any(not isinstance(source, TalentSource) for source in self.sources)
            or tuple(source.source_id for source in self.sources)
            != self.run.selected_source_ids
            or any(source.owner_id != self.run.owner_id for source in self.sources)
        ):
            raise ValueError("panorama runtime context invalid")


@dataclass(frozen=True, slots=True)
class PanoramaExecutionResult:
    attempt: int
    status: str
    turn_id: UUID
    assistant_content: str | None
    citation_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.attempt not in {1, 2}
            or self.status not in {"completed", "failed", "cancelled", "interrupted"}
            or not isinstance(self.turn_id, UUID)
            or (
                self.assistant_content is not None
                and not isinstance(self.assistant_content, str)
            )
            or not isinstance(self.citation_urls, tuple)
            or any(not isinstance(value, str) for value in self.citation_urls)
        ):
            raise ValueError("panorama execution result invalid")


@dataclass(frozen=True, slots=True)
class ProjectedPanoramaReport:
    companies: tuple[Mapping[str, object], ...]
    jobs: tuple[Mapping[str, object], ...]
    facts: tuple[Mapping[str, object], ...]
    direction_clusters: Mapping[str, object]
    inferences: tuple[Mapping[str, object], ...]
    unknowns: tuple[Mapping[str, object], ...]
    summary: str
    successful_source_ids: tuple[UUID, ...]
    source_failures: Mapping[str, str]


class PanoramaRunCoordinator:
    def __init__(
        self,
        repository: object,
        commands: object,
        *,
        resolver: Callable[[str, int], tuple[str, ...] | list[str]] = _system_resolver,
    ) -> None:
        if (
            not callable(getattr(repository, "runtime_context", None))
            or not callable(getattr(repository, "sources_for_run", None))
            or not callable(getattr(repository, "transition_run", None))
            or not callable(getattr(commands, "append_turn", None))
            or not callable(resolver)
        ):
            raise ValueError("panorama coordinator dependency invalid")
        self._repository = repository
        self._commands = commands
        self._resolver = resolver

    def _fail(self, runtime: PanoramaRunRuntime, error_code: str) -> None:
        self._repository.transition_run(
            TransitionPanoramaRun(
                runtime.run.owner_id,
                runtime.run.run_id,
                _runtime_id(runtime.run.run_id, f"transition-{error_code}"),
                runtime.run.row_version,
                "failed",
                error_code,
                {},
            )
        )

    def preflight(
        self, owner_id: UUID, source_ids: tuple[UUID, ...]
    ) -> tuple[TalentSource, ...]:
        if not isinstance(owner_id, UUID) or not isinstance(source_ids, tuple):
            raise ValueError("panorama preflight scope invalid")
        sources = self._repository.sources_for_run(owner_id, source_ids)
        if (
            not isinstance(sources, tuple)
            or tuple(source.source_id for source in sources) != source_ids
            or any(
                source.owner_id != owner_id or not source.active for source in sources
            )
        ):
            raise ValueError("panorama preflight scope invalid")
        for source in sources:
            for url in source.approved_urls:
                validate_panorama_destination(url, resolver=self._resolver)
        return sources

    def _prompt(self, runtime: PanoramaRunRuntime, *, retry: bool) -> str:
        as_of = runtime.run.created_at
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise _PanoramaPromptInvalid("panorama as-of invalid")
        sources = []
        for source in runtime.sources:
            urls = tuple(
                validate_panorama_destination(url, resolver=self._resolver)
                for url in source.approved_urls
            )
            sources.append(
                {
                    "source_id": str(source.source_id),
                    "canonical_name": source.canonical_name,
                    "approved_urls": urls,
                }
            )
        prompt = (
            "你正在执行公开招聘全景研究。仅研究下面明确批准的公司，并且只可读取每家公司 approved_urls 的字面值或其斜杠路径边界内 URL；"
            "禁止登录、绕过访问控制、访问其他公司或使用未批准 URL。通过现有共享 Web Research Search/Fetch 工作流执行，不创建 crawler。"
            "逐一尝试所有 approved_urls，并在允许的路径边界内继续发现公开岗位详情；不能因其中一个渠道失败就停止该公司的其他渠道。"
            "对每家公司分别检查社招、校招和实习入口；岗位标题、职责、要求或来源页能明确证明类型时，在可读报告中分类展示，"
            "证据不足时写入 unknowns 并标记为待确认，不得强行归类。某类入口未覆盖或采集失败时必须写“未采集/待确认”，"
            "不得写成“没有岗位”；只有成功读取对应入口且有明确证据时，才能写“本次未发现公开岗位”。"
            "每个来源的瞬时 SEARCH_UNAVAILABLE 最多重试 1 次；仍失败则只标记该来源失败，不推断为停止招聘。"
            f"本次 as-of 时间：{as_of.isoformat()}。"
            + ("这是模型输出恢复重试，禁止再次进行整轮恢复。" if retry else "")
            + "\n批准范围："
            + json.dumps(
                sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n输出合同："
            + PANORAMA_REPORT_CONTRACT_V1
        )
        if len(prompt) > 32768:
            raise _PanoramaPromptInvalid("panorama prompt too large")
        return prompt

    def _submit(self, run_id: UUID, *, retry: bool) -> UUID:
        runtime = self._repository.runtime_context(run_id)
        if not isinstance(runtime, PanoramaRunRuntime) or runtime.run.state not in {
            "queued",
            "running",
        }:
            raise ValueError("panorama run is not submittable")
        try:
            prompt = self._prompt(runtime, retry=retry)
        except ValueError as error:
            error_code = (
                "prompt_invalid"
                if isinstance(error, _PanoramaPromptInvalid)
                else "destination_invalid"
            )
            self._fail(runtime, error_code)
            raise
        try:
            result = self._commands.append_turn(
                runtime.run.owner_id,
                runtime.run.conversation_id,
                _runtime_id(
                    run_id, "conversation-retry" if retry else "conversation-turn"
                ),
                prompt,
            )
        except ConversationTurnInProgress:
            raise
        except ConversationRepositoryNotFound:
            self._fail(runtime, "conversation_unavailable")
            raise
        except ConversationRepositoryConflict:
            self._fail(runtime, "conversation_rejected")
            raise
        conversation = getattr(result, "conversation", None)
        if (
            getattr(conversation, "conversation_id", None)
            != runtime.run.conversation_id
            or getattr(conversation, "owner_internal_user_id", None)
            != runtime.run.owner_id
            or getattr(conversation, "mode", None) != "direct_agent"
            or getattr(conversation, "direct_agent_id", None) != "hr-bot"
            or getattr(conversation, "status", None) != "active"
        ):
            self._fail(runtime, "conversation_mismatch")
            raise ValueError("panorama conversation mismatch")
        if runtime.run.state == "queued":
            self._repository.transition_run(
                TransitionPanoramaRun(
                    runtime.run.owner_id,
                    run_id,
                    _runtime_id(run_id, "transition-running"),
                    runtime.run.row_version,
                    "running",
                    None,
                    {},
                )
            )
        return runtime.run.conversation_id

    def submit(self, run_id: UUID) -> UUID:
        return self._submit(run_id, retry=False)

    def retry(self, run_id: UUID) -> UUID:
        return self._submit(run_id, retry=True)


class PanoramaConversationResultReader:
    """Read only this run's deterministic Conversation turns, owner scoped."""

    def __init__(
        self,
        database_url: str,
        conversations: object,
        *,
        connect: Callable[..., object] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("panorama result database URL required")
        if not callable(getattr(conversations, "messages_after", None)):
            raise ValueError("panorama conversation reader required")
        if not callable(connect):
            raise ValueError("panorama result connection factory invalid")
        self._database_url = database_url
        self._conversations = conversations
        self._connect = connect

    def read(self, runtime: PanoramaRunRuntime) -> PanoramaExecutionResult | None:
        if not isinstance(runtime, PanoramaRunRuntime):
            raise ValueError("panorama runtime context required")
        primary_id = _runtime_id(runtime.run.run_id, "conversation-turn")
        retry_id = _runtime_id(runtime.run.run_id, "conversation-retry")
        try:
            with self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
                row_factory=dict_row,
            ) as connection:
                row = connection.execute(
                    "select turn.turn_id,turn.client_request_id,turn.status,"
                    "assistant.message_id as assistant_message_id,"
                    "assistant.seq as assistant_seq "
                    "from platform_control.conversations conversation "
                    "join platform_control.conversation_turns turn on "
                    "turn.conversation_id=conversation.conversation_id "
                    "left join platform_control.conversation_messages assistant on "
                    "assistant.conversation_id=turn.conversation_id and "
                    "assistant.message_id=turn.assistant_message_id "
                    "where conversation.owner_internal_user_id=%s and "
                    "conversation.conversation_id=%s and "
                    "turn.client_request_id in (%s,%s) "
                    "order by case when turn.client_request_id=%s then 0 else 1 end "
                    "limit 1",
                    (
                        runtime.run.owner_id,
                        runtime.run.conversation_id,
                        primary_id,
                        retry_id,
                        retry_id,
                    ),
                ).fetchone()
            if row is None or row["status"] not in {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
            }:
                return None
            content = None
            citations: tuple[str, ...] = ()
            if row["assistant_message_id"] is not None:
                messages = self._conversations.messages_after(
                    runtime.run.owner_id,
                    runtime.run.conversation_id,
                    after=row["assistant_seq"] - 1,
                    limit=1,
                )
                if (
                    len(messages) != 1
                    or messages[0].message_id != row["assistant_message_id"]
                    or messages[0].turn_id != row["turn_id"]
                    or messages[0].role != "assistant"
                ):
                    raise ValueError("panorama result message mismatch")
                content = messages[0].content
                citations = tuple(citation.url for citation in messages[0].citations)
            return PanoramaExecutionResult(
                2 if row["client_request_id"] == retry_id else 1,
                row["status"],
                row["turn_id"],
                content,
                citations,
            )
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise RuntimeError("panorama result unavailable") from None


class PanoramaResultProjector:
    def __init__(
        self,
        repository: object,
        result_reader: object,
        coordinator: object,
        *,
        resolver: Callable[[str, int], tuple[str, ...] | list[str]] = _system_resolver,
        model_version: str,
    ) -> None:
        if (
            any(
                not callable(getattr(repository, name, None))
                for name in (
                    "claim_next_runtime",
                    "runtime_context",
                    "publish_report",
                    "transition_run",
                    "create_snapshot",
                    "create_insight",
                )
            )
            or not callable(getattr(result_reader, "read", None))
            or any(
                not callable(getattr(coordinator, name, None))
                for name in ("submit", "retry")
            )
        ):
            raise ValueError("panorama projector dependency invalid")
        if not isinstance(model_version, str) or not model_version.strip():
            raise ValueError("panorama model version invalid")
        self._repository = repository
        self._result_reader = result_reader
        self._coordinator = coordinator
        self._resolver = resolver
        self._model_version = model_version.strip()

    def _validate_url(self, value: object, sources: tuple[TalentSource, ...]) -> str:
        if not isinstance(value, str):
            raise ValueError("panorama report URL invalid")
        selected = validate_panorama_destination(value, resolver=self._resolver)
        if not any(_approved(selected, source.approved_urls) for source in sources):
            raise ValueError("panorama report URL is not approved")
        return selected

    def project(
        self,
        completed_answer: str,
        *,
        runtime: PanoramaRunRuntime | None = None,
        citation_urls: tuple[str, ...] = (),
    ) -> ProjectedPanoramaReport:
        envelope = extract_hr_envelope(completed_answer, "panorama_report")
        if envelope is None or not envelope.visible_markdown.strip():
            raise ValueError("panorama report envelope invalid")
        payload = envelope.payload
        raw_companies = payload["companies"]
        raw_jobs = payload["jobs"]
        raw_facts = payload["facts"]
        raw_inferences = payload["inferences"]
        raw_unknowns = payload["unknowns"]
        if not all(
            isinstance(value, list)
            for value in (
                raw_companies,
                raw_jobs,
                raw_facts,
                raw_inferences,
                raw_unknowns,
            )
        ):
            raise ValueError("panorama report invalid")
        successes: list[UUID] = []
        failures: dict[str, str] = {}
        if runtime is not None:
            if len(raw_companies) != len(runtime.sources):
                raise ValueError("panorama company scope invalid")
            for item, source in zip(raw_companies, runtime.sources, strict=True):
                if (
                    item["source_id"] != str(source.source_id)
                    or item["canonical_name"] != source.canonical_name
                    or tuple(item["approved_urls"]) != source.approved_urls
                ):
                    raise ValueError("panorama company scope invalid")
                for approved_url in source.approved_urls:
                    validate_panorama_destination(approved_url, resolver=self._resolver)
                if item["status"] == "completed":
                    successes.append(source.source_id)
                else:
                    failures[str(source.source_id)] = "search_unavailable"
            completed_names = {
                source.canonical_name
                for source in runtime.sources
                if source.source_id in successes
            }
            source_by_name = {
                source.canonical_name: source for source in runtime.sources
            }
            for job in raw_jobs:
                if job["company"] not in completed_names:
                    raise ValueError("panorama job company invalid")
                if (
                    datetime.fromisoformat(
                        str(job["observed_at"]).replace("Z", "+00:00")
                    )
                    > runtime.run.created_at
                ):
                    raise ValueError("panorama evidence is after panorama as-of")
                self._validate_url(
                    job["source_url"], (source_by_name[str(job["company"])],)
                )
            for fact in raw_facts:
                if fact["company"] not in completed_names:
                    raise ValueError("panorama fact company invalid")
                if (
                    datetime.fromisoformat(
                        str(fact["observed_at"]).replace("Z", "+00:00")
                    )
                    > runtime.run.created_at
                ):
                    raise ValueError("panorama evidence is after panorama as-of")
                self._validate_url(
                    fact["source_url"], (source_by_name[str(fact["company"])],)
                )
            validated_citations = {
                self._validate_url(url, runtime.sources) for url in citation_urls
            }
            evidence_urls = {
                str(item["source_url"]) for item in (*raw_jobs, *raw_facts)
            }
            if evidence_urls and (
                not citation_urls or not evidence_urls.issubset(validated_citations)
            ):
                raise ValueError("panorama citations incomplete")
        return ProjectedPanoramaReport(
            tuple(MappingProxyType(dict(item)) for item in raw_companies),
            tuple(MappingProxyType(dict(item)) for item in raw_jobs),
            tuple(MappingProxyType(dict(item)) for item in raw_facts),
            MappingProxyType(dict(payload["direction_clusters"])),
            tuple(MappingProxyType(dict(item)) for item in raw_inferences),
            tuple(MappingProxyType(dict(item)) for item in raw_unknowns),
            str(payload["summary"]),
            tuple(successes),
            MappingProxyType(failures),
        )

    def _terminal(
        self,
        runtime: PanoramaRunRuntime,
        *,
        state: str,
        error_code: str | None,
        failures: Mapping[str, str],
        repository: object | None = None,
    ) -> None:
        target = self._repository if repository is None else repository
        target.transition_run(
            TransitionPanoramaRun(
                runtime.run.owner_id,
                runtime.run.run_id,
                _runtime_id(runtime.run.run_id, f"transition-{state}"),
                runtime.run.row_version,
                state,  # type: ignore[arg-type]
                error_code,
                failures,
            )
        )

    def _invalid_result(
        self, runtime: PanoramaRunRuntime, result: PanoramaExecutionResult
    ) -> bool:
        if result.attempt == 1:
            self._coordinator.retry(runtime.run.run_id)
        else:
            self._terminal(
                runtime,
                state="failed",
                error_code="model_output_invalid",
                failures={},
            )
        return True

    def reconcile_one(self) -> bool:
        runtime = self._repository.claim_next_runtime(claim_seconds=5)
        if runtime is None:
            return False
        if not isinstance(runtime, PanoramaRunRuntime) or runtime.run.state not in {
            "queued",
            "running",
        }:
            raise ValueError("panorama runtime context invalid")
        if runtime.run.state == "queued":
            self._coordinator.submit(runtime.run.run_id)
            return True
        result = self._result_reader.read(runtime)
        if result is None:
            return False
        if not isinstance(result, PanoramaExecutionResult):
            raise ValueError("panorama execution result invalid")
        if (
            result.status != "completed"
            or result.assistant_content is None
            or not result.assistant_content.strip()
        ):
            return self._invalid_result(runtime, result)
        try:
            report = self.project(
                result.assistant_content,
                runtime=runtime,
                citation_urls=result.citation_urls,
            )
        except (KeyError, TypeError, ValueError):
            return self._invalid_result(runtime, result)
        if not report.successful_source_ids:
            self._terminal(
                runtime,
                state="failed",
                error_code="search_unavailable",
                failures=report.source_failures,
            )
            return True

        def publish(writer: object) -> None:
            source_by_name = {
                source.canonical_name: source for source in runtime.sources
            }
            snapshots = []
            snapshot_by_job: dict[tuple[str, str], tuple[object, UUID]] = {}
            for ordinal, job in enumerate(report.jobs, 1):
                source = source_by_name[str(job["company"])]
                request_id = _runtime_id(runtime.run.run_id, f"snapshot:{ordinal}")
                snapshot = writer.create_snapshot(
                    CreatePublicJobSnapshot(
                        _runtime_id(runtime.run.run_id, f"snapshot-resource:{ordinal}"),
                        runtime.run.owner_id,
                        request_id,
                        runtime.run.run_id,
                        source.source_id,
                        str(job["public_job_key"]),
                        str(job["title"]),
                        str(job["location"]),
                        str(job["duty_excerpt"]),
                        str(job["requirement_excerpt"]),
                        str(job["source_url"]),
                        datetime.fromisoformat(
                            str(job["observed_at"]).replace("Z", "+00:00")
                        ),
                        str(job["content_sha256"]),
                        "open",
                    )
                )
                snapshots.append(snapshot)
                snapshot_by_job[(source.canonical_name, str(job["public_job_key"]))] = (
                    snapshot,
                    request_id,
                )
            facts = []
            for fact in report.facts:
                snapshot, observation_id = snapshot_by_job[
                    (str(fact["company"]), str(fact["public_job_key"]))
                ]
                facts.append(
                    {
                        "fact_id": fact["fact_id"],
                        "text": fact["text"],
                        "snapshot_id": str(snapshot.snapshot_id),
                        "observation_id": str(observation_id),
                        "source_url": fact["source_url"],
                        "observed_at": fact["observed_at"],
                    }
                )
            writer.create_insight(
                CreateTalentInsightVersion(
                    _runtime_id(runtime.run.run_id, "insight-resource"),
                    runtime.run.owner_id,
                    _runtime_id(runtime.run.run_id, "insight-request"),
                    runtime.run.run_id,
                    runtime.run.selected_source_ids,
                    tuple(snapshot.snapshot_id for snapshot in snapshots),
                    tuple(facts),
                    tuple(dict(item) for item in report.inferences),
                    tuple(dict(item) for item in report.unknowns),
                    dict(report.direction_clusters),
                    report.summary,
                    runtime.run.conversation_id,
                    result.turn_id,
                    "hr-bot",
                    self._model_version,
                )
            )
            self._terminal(
                runtime,
                state=(
                    "partially_completed" if report.source_failures else "completed"
                ),
                error_code=None,
                failures=report.source_failures,
                repository=writer,
            )

        self._repository.publish_report(publish)
        return True


async def panorama_projection_loop(
    projector: object, *, idle_seconds: float = 0.5
) -> None:
    if not callable(getattr(projector, "reconcile_one", None)):
        raise TypeError("panorama projector required")
    if (
        isinstance(idle_seconds, bool)
        or not isinstance(idle_seconds, (int, float))
        or idle_seconds <= 0
    ):
        raise ValueError("panorama projection interval invalid")
    while True:
        try:
            changed = await asyncio.to_thread(projector.reconcile_one)
        except Exception:
            logger.exception("panorama projection pass failed")
            await asyncio.sleep(idle_seconds)
            continue
        if not changed:
            await asyncio.sleep(idle_seconds)


__all__ = [
    "PANORAMA_REPORT_CONTRACT_V1",
    "PanoramaConversationResultReader",
    "PanoramaExecutionResult",
    "PanoramaResultProjector",
    "PanoramaRunCoordinator",
    "PanoramaRunRuntime",
    "ProjectedPanoramaReport",
    "panorama_projection_loop",
    "validate_panorama_destination",
]
