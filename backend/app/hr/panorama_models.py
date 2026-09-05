# ruff: noqa: TRY004
from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID

SourceKind = Literal["company"]
RunState = Literal["queued", "running", "completed", "partially_completed", "failed"]
JobStatus = Literal["open", "closed", "unknown"]

_COMPANY_KEY = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_DNS_NAME = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\."
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z"
)
_IPV4_STYLE_HOST = re.compile(
    r"(?:0x[a-f0-9]+|[0-9]+)(?:\.(?:0x[a-f0-9]+|[0-9]+)){0,3}\Z"
)
_SECRET_QUERY_KEY = re.compile(
    r"(?:access[ _-]?token|api[ _-]?key|token|key|password|passwd|pass|secret|signature|sig|credential|auth)",
    re.IGNORECASE,
)
_RUN_STATES = {"queued", "running", "completed", "partially_completed", "failed"}
_JOB_STATUSES = {"open", "closed", "unknown"}


def _uuid(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("panorama identifiers invalid")
    return value


def _uuid_tuple(
    values: tuple[UUID, ...], *, minimum: int, maximum: int, message: str
) -> tuple[UUID, ...]:
    if (
        not isinstance(values, tuple)
        or not minimum <= len(values) <= maximum
        or any(not isinstance(value, UUID) for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError(message)
    return values


def _text(value: str, maximum: int, message: str) -> str:
    if not isinstance(value, str):
        raise ValueError(message)
    normalized = value.strip()
    if not normalized or "\0" in normalized or len(normalized) > maximum:
        raise ValueError(message)
    return normalized


def _optional_text(value: str | None, maximum: int, message: str) -> str | None:
    return None if value is None else _text(value, maximum, message)


def _aware(value: datetime, message: str = "panorama timestamp invalid"):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(message)
    return value


def _optional_aware(
    value: datetime | None, message: str = "panorama timestamp invalid"
):
    if value is not None:
        _aware(value, message)
    return value


def _positive(value: int, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(message)
    return value


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    return value


def _json_size(value: object, maximum: int, message: str) -> None:
    try:
        encoded = json.dumps(
            thaw_json(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError(message)


def _strings(
    values: tuple[str, ...],
    *,
    maximum_items: int,
    maximum_text: int,
    message: str,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > maximum_items:
        raise ValueError(message)
    normalized = tuple(_text(value, maximum_text, message) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(message)
    return normalized


def _decoded_url_component(raw: str) -> str:
    index = 0
    while index < len(raw):
        if raw[index] != "%":
            index += 1
            continue
        escape = raw[index + 1 : index + 3]
        if len(escape) != 2 or any(
            character not in "0123456789ABCDEF" for character in escape
        ):
            raise ValueError("source URL invalid")
        if escape in {"25", "2E", "2F", "5C"}:
            raise ValueError("source URL invalid")
        index += 3
    try:
        decoded = unquote(raw, encoding="utf-8", errors="strict")
    except (UnicodeError, ValueError):
        raise ValueError("source URL invalid") from None
    normalized = unicodedata.normalize("NFKC", decoded)
    if any(
        ord(character) < 0x20
        or ord(character) == 0x7F
        or character == "\\"
        or unicodedata.category(character) == "Cc"
        for character in normalized
    ):
        raise ValueError("source URL invalid")
    if any(
        character != folded and folded in {".", "/", "\\"}
        for character in decoded
        for folded in (unicodedata.normalize("NFKC", character),)
    ):
        raise ValueError("source URL invalid")
    return normalized


def canonical_panorama_url(value: str) -> str:
    """Validate one exact canonical URL shape shared by storage and runtime."""
    if (
        not isinstance(value, str)
        or not 9 <= len(value) <= 2048
        or value != value.strip()
        or not value.startswith("https://")
        or "#" in value
        or "\\" in value
        or any(
            character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
    ):
        raise ValueError("source URL invalid")
    remainder = value[len("https://") :]
    boundary = min(
        (position for marker in "/?" if (position := remainder.find(marker)) >= 0),
        default=len(remainder),
    )
    authority = remainder[:boundary]
    if authority.endswith(":443"):
        host = authority[:-4]
    else:
        host = authority
    if authority not in {host, f"{host}:443"} or not _DNS_NAME.fullmatch(host):
        raise ValueError("source URL invalid")
    if authority != authority.lower() or not authority.isascii():
        raise ValueError("source URL invalid")
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise ValueError("source URL invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.netloc != authority
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname != host
        or parsed.fragment
    ):
        raise ValueError("source URL invalid")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("source URL invalid")
    if (
        host == "localhost"
        or host.endswith((".localhost", ".local"))
        or _IPV4_STYLE_HOST.fullmatch(host) is not None
        or not _DNS_NAME.fullmatch(host)
    ):
        raise ValueError("source URL invalid")
    path = _decoded_url_component(parsed.path)
    query = _decoded_url_component(parsed.query)
    if any(part in {".", ".."} for part in path.split("/")):
        raise ValueError("source URL invalid")
    query_keys = query.replace("+", " ")
    if any(
        _SECRET_QUERY_KEY.fullmatch(item.partition("=")[0].strip())
        for item in re.split(r"[&;]", query_keys)
        if item
    ):
        raise ValueError("source URL invalid")
    return value


def _urls(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not 1 <= len(values) <= 20:
        raise ValueError("approved URLs invalid")
    normalized = tuple(canonical_panorama_url(value) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError("approved URLs invalid")
    _json_size(normalized, 65_536, "approved URLs invalid")
    return normalized


def _source_failures(
    value: Mapping[str, str], source_ids: tuple[UUID, ...]
) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or len(value) > 100:
        raise ValueError("source failures invalid")
    allowed = {str(source_id) for source_id in source_ids}
    normalized: dict[str, str] = {}
    for key, reason in value.items():
        if not isinstance(key, str) or key not in allowed:
            raise ValueError("source failures invalid")
        reason = _text(reason, 64, "source failures invalid")
        if _ERROR_CODE.fullmatch(reason) is None:
            raise ValueError("source failures invalid")
        normalized[key] = reason
    _json_size(normalized, 8192, "source failures invalid")
    return MappingProxyType(normalized)


def _json_objects(
    values: tuple[Mapping[str, object], ...],
    *,
    minimum: int,
    maximum: int,
    maximum_bytes: int,
    message: str,
) -> tuple[Mapping[str, object], ...]:
    if (
        not isinstance(values, tuple)
        or not minimum <= len(values) <= maximum
        or any(not isinstance(value, Mapping) for value in values)
    ):
        raise ValueError(message)
    _json_size(values, maximum_bytes, message)
    return tuple(_freeze_json(value) for value in values)  # type: ignore[return-value]


def _facts(
    values: tuple[Mapping[str, object], ...],
) -> tuple[Mapping[str, object], ...]:
    frozen = _json_objects(
        values,
        minimum=1,
        maximum=1000,
        maximum_bytes=262_144,
        message="insight facts invalid",
    )
    fact_ids: set[str] = set()
    for fact in frozen:
        if set(fact) != {
            "fact_id",
            "text",
            "snapshot_id",
            "observation_id",
            "source_url",
            "observed_at",
        }:
            raise ValueError("insight facts invalid")
        fact_id = _text(fact["fact_id"], 128, "insight facts invalid")  # type: ignore[arg-type]
        _text(fact["text"], 8000, "insight facts invalid")  # type: ignore[arg-type]
        try:
            UUID(str(fact["snapshot_id"]))
            UUID(str(fact["observation_id"]))
            canonical_panorama_url(fact["source_url"])  # type: ignore[arg-type]
            observed = datetime.fromisoformat(
                str(fact["observed_at"]).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            raise ValueError("insight facts invalid") from None
        if observed.tzinfo is None or fact_id in fact_ids:
            raise ValueError("insight facts invalid")
        fact_ids.add(fact_id)
    return frozen


def _inferences(
    values: tuple[Mapping[str, object], ...], fact_ids: set[str]
) -> tuple[Mapping[str, object], ...]:
    frozen = _json_objects(
        values,
        minimum=0,
        maximum=1000,
        maximum_bytes=262_144,
        message="insight inferences invalid",
    )
    for inference in frozen:
        if set(inference) != {"text", "basis_fact_ids"}:
            raise ValueError("insight inferences invalid")
        _text(inference["text"], 8000, "insight inferences invalid")  # type: ignore[arg-type]
        basis = inference["basis_fact_ids"]
        if (
            not isinstance(basis, tuple)
            or not 1 <= len(basis) <= 100
            or any(
                not isinstance(value, str) or value not in fact_ids for value in basis
            )
            or len(set(basis)) != len(basis)
        ):
            raise ValueError("insight inferences invalid")
    return frozen


def _unknowns(
    values: tuple[Mapping[str, object], ...],
) -> tuple[Mapping[str, object], ...]:
    frozen = _json_objects(
        values,
        minimum=0,
        maximum=1000,
        maximum_bytes=131_072,
        message="insight unknowns invalid",
    )
    for unknown in frozen:
        if set(unknown) != {"text"}:
            raise ValueError("insight unknowns invalid")
        _text(unknown["text"], 8000, "insight unknowns invalid")  # type: ignore[arg-type]
    return frozen


def _direction_clusters(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("direction clusters invalid")
    _json_size(value, 131_072, "direction clusters invalid")
    return _freeze_json(value)  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class TalentSource:
    source_id: UUID
    owner_id: UUID
    client_request_id: UUID
    source_kind: SourceKind
    company_key: str
    canonical_name: str
    aliases: tuple[str, ...]
    approved_urls: tuple[str, ...]
    active: bool
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for value in (self.source_id, self.owner_id, self.client_request_id):
            _uuid(value)
        if self.source_kind != "company":
            raise ValueError("source kind invalid")
        key = _text(self.company_key, 128, "company key invalid")
        if _COMPANY_KEY.fullmatch(key) is None:
            raise ValueError("company key invalid")
        object.__setattr__(self, "company_key", key)
        object.__setattr__(
            self,
            "canonical_name",
            _text(self.canonical_name, 500, "company name invalid"),
        )
        object.__setattr__(
            self,
            "aliases",
            _strings(
                self.aliases,
                maximum_items=20,
                maximum_text=500,
                message="company aliases invalid",
            ),
        )
        object.__setattr__(self, "approved_urls", _urls(self.approved_urls))
        if type(self.active) is not bool:
            raise ValueError("source active flag invalid")
        _aware(self.created_at)
        _aware(self.updated_at)


@dataclass(frozen=True, slots=True)
class CreateTalentSource:
    source_id: UUID
    owner_id: UUID
    client_request_id: UUID
    company_key: str
    canonical_name: str
    aliases: tuple[str, ...]
    approved_urls: tuple[str, ...]
    active: bool = True

    def __post_init__(self) -> None:
        normalized = TalentSource(
            self.source_id,
            self.owner_id,
            self.client_request_id,
            "company",
            self.company_key,
            self.canonical_name,
            self.aliases,
            self.approved_urls,
            self.active,
            datetime.min.replace(tzinfo=datetime.now().astimezone().tzinfo),
            datetime.min.replace(tzinfo=datetime.now().astimezone().tzinfo),
        )
        for name in ("company_key", "canonical_name", "aliases", "approved_urls"):
            object.__setattr__(self, name, getattr(normalized, name))


@dataclass(frozen=True, slots=True)
class PanoramaRun:
    run_id: UUID
    owner_id: UUID
    client_request_id: UUID
    selected_source_ids: tuple[UUID, ...]
    conversation_id: UUID
    state: RunState
    error_code: str | None
    source_failures: Mapping[str, str]
    row_version: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.run_id,
            self.owner_id,
            self.client_request_id,
            self.conversation_id,
        ):
            _uuid(value)
        _uuid_tuple(
            self.selected_source_ids,
            minimum=1,
            maximum=100,
            message="panorama source selection invalid",
        )
        if self.state not in _RUN_STATES:
            raise ValueError("run state invalid")
        error = _optional_text(self.error_code, 64, "run error code invalid")
        if error is not None and _ERROR_CODE.fullmatch(error) is None:
            raise ValueError("run error code invalid")
        object.__setattr__(self, "error_code", error)
        failures = _source_failures(self.source_failures, self.selected_source_ids)
        object.__setattr__(self, "source_failures", failures)
        _positive(self.row_version, "run row version invalid")
        _optional_aware(self.started_at)
        _optional_aware(self.finished_at)
        _aware(self.created_at)
        _aware(self.updated_at)
        valid = (
            (
                self.state == "queued"
                and self.started_at is None
                and self.finished_at is None
                and error is None
                and not failures
            )
            or (
                self.state == "running"
                and self.started_at is not None
                and self.finished_at is None
                and error is None
                and not failures
            )
            or (
                self.state == "completed"
                and self.started_at is not None
                and self.finished_at is not None
                and error is None
                and not failures
            )
            or (
                self.state == "partially_completed"
                and self.started_at is not None
                and self.finished_at is not None
                and error is None
                and 0 < len(failures) < len(self.selected_source_ids)
            )
            or (
                self.state == "failed"
                and self.started_at is not None
                and self.finished_at is not None
                and error is not None
            )
        )
        if not valid:
            raise ValueError("run state invalid")


@dataclass(frozen=True, slots=True)
class CreatePanoramaRun:
    run_id: UUID
    owner_id: UUID
    client_request_id: UUID
    selected_source_ids: tuple[UUID, ...]
    conversation_id: UUID

    def __post_init__(self) -> None:
        for value in (
            self.run_id,
            self.owner_id,
            self.client_request_id,
            self.conversation_id,
        ):
            _uuid(value)
        _uuid_tuple(
            self.selected_source_ids,
            minimum=1,
            maximum=100,
            message="panorama source selection invalid",
        )


@dataclass(frozen=True, slots=True)
class TransitionPanoramaRun:
    owner_id: UUID
    run_id: UUID
    client_request_id: UUID
    expected_row_version: int
    state: RunState
    error_code: str | None
    source_failures: Mapping[str, str]

    def __post_init__(self) -> None:
        for value in (self.owner_id, self.run_id, self.client_request_id):
            _uuid(value)
        _positive(self.expected_row_version, "run row version invalid")
        if self.state not in {"running", "completed", "partially_completed", "failed"}:
            raise ValueError("run transition invalid")
        error = _optional_text(self.error_code, 64, "run error code invalid")
        if error is not None and _ERROR_CODE.fullmatch(error) is None:
            raise ValueError("run error code invalid")
        object.__setattr__(self, "error_code", error)
        if not isinstance(self.source_failures, Mapping):
            raise ValueError("source failures invalid")
        failures: dict[str, str] = {}
        for key, reason in self.source_failures.items():
            try:
                normalized_key = str(UUID(str(key)))
            except ValueError:
                raise ValueError("source failures invalid") from None
            normalized_reason = _text(reason, 64, "source failures invalid")
            if _ERROR_CODE.fullmatch(normalized_reason) is None:
                raise ValueError("source failures invalid")
            failures[normalized_key] = normalized_reason
        _json_size(failures, 8192, "source failures invalid")
        object.__setattr__(self, "source_failures", MappingProxyType(failures))


@dataclass(frozen=True, slots=True)
class PublicJobSnapshot:
    snapshot_id: UUID
    owner_id: UUID
    origin_request_id: UUID
    run_id: UUID
    source_id: UUID
    public_job_key: str
    title: str
    location: str
    duty_excerpt: str
    requirement_excerpt: str
    source_url: str
    observed_at: datetime
    content_sha256: str
    status: JobStatus
    created_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.snapshot_id,
            self.owner_id,
            self.origin_request_id,
            self.run_id,
            self.source_id,
        ):
            _uuid(value)
        for name, maximum in (
            ("public_job_key", 512),
            ("title", 1000),
            ("location", 1000),
            ("duty_excerpt", 32768),
            ("requirement_excerpt", 32768),
        ):
            object.__setattr__(
                self, name, _text(getattr(self, name), maximum, f"{name} invalid")
            )
        object.__setattr__(self, "source_url", canonical_panorama_url(self.source_url))
        _aware(self.observed_at)
        _aware(self.created_at)
        if (
            not isinstance(self.content_sha256, str)
            or _SHA256.fullmatch(self.content_sha256) is None
        ):
            raise ValueError("snapshot content hash invalid")
        if self.status not in _JOB_STATUSES:
            raise ValueError("snapshot status invalid")


@dataclass(frozen=True, slots=True)
class CreatePublicJobSnapshot:
    snapshot_id: UUID
    owner_id: UUID
    client_request_id: UUID
    run_id: UUID
    source_id: UUID
    public_job_key: str
    title: str
    location: str
    duty_excerpt: str
    requirement_excerpt: str
    source_url: str
    observed_at: datetime
    content_sha256: str
    status: JobStatus

    def __post_init__(self) -> None:
        normalized = PublicJobSnapshot(
            self.snapshot_id,
            self.owner_id,
            self.client_request_id,
            self.run_id,
            self.source_id,
            self.public_job_key,
            self.title,
            self.location,
            self.duty_excerpt,
            self.requirement_excerpt,
            self.source_url,
            self.observed_at,
            self.content_sha256,
            self.status,
            self.observed_at,
        )
        for name in (
            "public_job_key",
            "title",
            "location",
            "duty_excerpt",
            "requirement_excerpt",
            "source_url",
        ):
            object.__setattr__(self, name, getattr(normalized, name))


@dataclass(frozen=True, slots=True)
class TalentInsightVersion:
    insight_version_id: UUID
    owner_id: UUID
    client_request_id: UUID
    run_id: UUID
    version_number: int
    selected_source_ids: tuple[UUID, ...]
    snapshot_ids: tuple[UUID, ...]
    facts: tuple[Mapping[str, object], ...]
    inferences: tuple[Mapping[str, object], ...]
    unknowns: tuple[Mapping[str, object], ...]
    direction_clusters: Mapping[str, object]
    summary: str
    source_conversation_id: UUID
    source_turn_id: UUID
    agent_id: str
    model_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.insight_version_id,
            self.owner_id,
            self.client_request_id,
            self.run_id,
            self.source_conversation_id,
            self.source_turn_id,
        ):
            _uuid(value)
        _positive(self.version_number, "insight version invalid")
        _uuid_tuple(
            self.selected_source_ids,
            minimum=1,
            maximum=100,
            message="insight source selection invalid",
        )
        _uuid_tuple(
            self.snapshot_ids,
            minimum=1,
            maximum=1000,
            message="insight snapshot selection invalid",
        )
        facts = _facts(self.facts)
        object.__setattr__(self, "facts", facts)
        fact_ids = {str(fact["fact_id"]).strip() for fact in facts}
        object.__setattr__(self, "inferences", _inferences(self.inferences, fact_ids))
        object.__setattr__(self, "unknowns", _unknowns(self.unknowns))
        object.__setattr__(
            self, "direction_clusters", _direction_clusters(self.direction_clusters)
        )
        object.__setattr__(
            self, "summary", _text(self.summary, 32768, "summary invalid")
        )
        object.__setattr__(self, "agent_id", _text(self.agent_id, 128, "agent invalid"))
        object.__setattr__(
            self, "model_version", _text(self.model_version, 160, "model invalid")
        )
        _aware(self.created_at)


@dataclass(frozen=True, slots=True)
class CreateTalentInsightVersion:
    insight_version_id: UUID
    owner_id: UUID
    client_request_id: UUID
    run_id: UUID
    selected_source_ids: tuple[UUID, ...]
    snapshot_ids: tuple[UUID, ...]
    facts: tuple[Mapping[str, object], ...]
    inferences: tuple[Mapping[str, object], ...]
    unknowns: tuple[Mapping[str, object], ...]
    direction_clusters: Mapping[str, object]
    summary: str
    source_conversation_id: UUID
    source_turn_id: UUID
    agent_id: str
    model_version: str

    def __post_init__(self) -> None:
        normalized = self.as_version(
            version_number=1, created_at=datetime.now().astimezone()
        )
        for name in (
            "facts",
            "inferences",
            "unknowns",
            "direction_clusters",
            "summary",
            "agent_id",
            "model_version",
        ):
            object.__setattr__(self, name, getattr(normalized, name))

    def as_version(
        self, *, version_number: int, created_at: datetime
    ) -> TalentInsightVersion:
        return TalentInsightVersion(
            self.insight_version_id,
            self.owner_id,
            self.client_request_id,
            self.run_id,
            version_number,
            self.selected_source_ids,
            self.snapshot_ids,
            self.facts,
            self.inferences,
            self.unknowns,
            self.direction_clusters,
            self.summary,
            self.source_conversation_id,
            self.source_turn_id,
            self.agent_id,
            self.model_version,
            created_at,
        )


@dataclass(frozen=True, slots=True)
class PositionInsightRetrieval:
    retrieval_id: UUID
    owner_id: UUID
    client_request_id: UUID
    position_id: UUID
    conversation_id: UUID
    turn_id: UUID
    insight_version_ids: tuple[UUID, ...]
    query_sha256: str
    retrieved_excerpts: tuple[Mapping[str, object], ...]
    created_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.retrieval_id,
            self.owner_id,
            self.client_request_id,
            self.position_id,
            self.conversation_id,
            self.turn_id,
        ):
            _uuid(value)
        _uuid_tuple(
            self.insight_version_ids,
            minimum=1,
            maximum=5,
            message="retrieval insight selection invalid",
        )
        if (
            not isinstance(self.query_sha256, str)
            or _SHA256.fullmatch(self.query_sha256) is None
        ):
            raise ValueError("retrieval query hash invalid")
        object.__setattr__(
            self,
            "retrieved_excerpts",
            _json_objects(
                self.retrieved_excerpts,
                minimum=0,
                maximum=100,
                maximum_bytes=32768,
                message="retrieved excerpts invalid",
            ),
        )
        _aware(self.created_at)


@dataclass(frozen=True, slots=True)
class CreatePositionInsightRetrieval:
    retrieval_id: UUID
    owner_id: UUID
    client_request_id: UUID
    position_id: UUID
    conversation_id: UUID
    turn_id: UUID
    insight_version_ids: tuple[UUID, ...]
    query_sha256: str
    retrieved_excerpts: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        normalized = PositionInsightRetrieval(
            self.retrieval_id,
            self.owner_id,
            self.client_request_id,
            self.position_id,
            self.conversation_id,
            self.turn_id,
            self.insight_version_ids,
            self.query_sha256,
            self.retrieved_excerpts,
            datetime.now().astimezone(),
        )
        object.__setattr__(self, "retrieved_excerpts", normalized.retrieved_excerpts)


@dataclass(frozen=True, slots=True)
class PanoramaReport:
    insight: TalentInsightVersion
    sources: tuple[TalentSource, ...]
    snapshots: tuple[PublicJobSnapshot, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.insight, TalentInsightVersion):
            raise ValueError("panorama report insight invalid")
        if (
            not isinstance(self.sources, tuple)
            or any(not isinstance(source, TalentSource) for source in self.sources)
            or not isinstance(self.snapshots, tuple)
            or any(
                not isinstance(snapshot, PublicJobSnapshot)
                for snapshot in self.snapshots
            )
        ):
            raise ValueError("panorama report contents invalid")
        if any(
            value.owner_id != self.insight.owner_id
            for value in (*self.sources, *self.snapshots)
        ):
            raise ValueError("panorama report owner invalid")

    @property
    def insight_version_id(self) -> UUID:
        return self.insight.insight_version_id

    @property
    def facts(self):
        return self.insight.facts

    @property
    def inferences(self):
        return self.insight.inferences

    @property
    def unknowns(self):
        return self.insight.unknowns

    @property
    def direction_clusters(self):
        return self.insight.direction_clusters

    @property
    def summary(self) -> str:
        return self.insight.summary


__all__ = [
    "CreatePanoramaRun",
    "CreatePositionInsightRetrieval",
    "CreatePublicJobSnapshot",
    "CreateTalentInsightVersion",
    "CreateTalentSource",
    "PanoramaReport",
    "PanoramaRun",
    "PositionInsightRetrieval",
    "PublicJobSnapshot",
    "TalentInsightVersion",
    "TalentSource",
    "TransitionPanoramaRun",
    "canonical_panorama_url",
    "thaw_json",
]
