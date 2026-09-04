from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid5

from .models import (
    BindPositionConversation,
    ProjectOfficialPosition,
    ProposePositionDraft,
)
from .position_intelligence_models import ProjectOfficialVersion

_REGISTRY_FIELDS = {"version", "lastSuccessfulSyncAt", "jobs"}
_JOB_FIELDS = {
    "canonicalId", "jobAdId", "sourceRecordIds", "title", "category",
    "subcategory", "locations", "organization", "headcount", "degree",
    "employmentType", "salary", "duty", "requirement", "sourceChangedAt",
    "firstSeenAt", "lastSeenAt", "status", "statusReason",
    "consecutiveMisses", "contentHash", "officialStatus",
}
_STATUSES = {"active", "stale", "suspected_inactive", "inactive"}
_JOB_ID = re.compile(r"J[0-9]{4,12}\Z")
_JOB_REFERENCE = re.compile(r"(?<![A-Z0-9])J[0-9]{4,12}(?![A-Z0-9])")
_POSITION_INTENT = re.compile(r"岗位|招聘|工程师|人才画像|任职要求|JD", re.I)


def _instant(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not re.match(
        r"^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:?\d{2})$", value
    ):
        raise ValueError(f"{label} invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label} invalid") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{label} invalid")
    return parsed


def _string(value: object, label: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{label} invalid")
    return value.strip()


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} invalid")
    return value


@dataclass(frozen=True, slots=True)
class OfficialJob:
    canonical_id: str
    job_ad_id: str | int
    source_record_ids: tuple[str, ...]
    title: str
    category: str
    subcategory: str | None
    locations: tuple[str, ...]
    organization: str
    headcount: int
    degree: str | None
    employment_type: str
    salary: str
    duty: str
    requirement: str
    source_changed_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    status: str
    status_reason: str
    consecutive_misses: int
    content_hash: str
    official_status: int

    @classmethod
    def parse(cls, value: object) -> "OfficialJob":
        if not isinstance(value, dict) or set(value) != _JOB_FIELDS:
            raise ValueError("registry job fields invalid")
        raw_id = _string(value["canonicalId"], "official job id")
        canonical_id = raw_id.upper()
        if _JOB_ID.fullmatch(canonical_id) is None:
            raise ValueError("official job id invalid")
        job_ad_id = value["jobAdId"]
        if isinstance(job_ad_id, bool) or not (
            isinstance(job_ad_id, int) and job_ad_id >= 0
            or isinstance(job_ad_id, str) and bool(job_ad_id.strip())
        ):
            raise ValueError("registry job jobAdId invalid")
        source_ids = value["sourceRecordIds"]
        locations = value["locations"]
        if not isinstance(source_ids, list) or not source_ids or any(
            not isinstance(item, str) or not item.strip() for item in source_ids
        ):
            raise ValueError("registry job sourceRecordIds invalid")
        if not isinstance(locations, list) or any(
            not isinstance(item, str) or not item.strip() for item in locations
        ):
            raise ValueError("registry job locations invalid")
        status = _string(value["status"], "registry job status")
        if status not in _STATUSES:
            raise ValueError("registry job status invalid")
        content_hash = _string(value["contentHash"], "registry job contentHash")
        if re.fullmatch(r"[a-f0-9]{64}", content_hash) is None:
            raise ValueError("registry job contentHash invalid")
        return cls(
            canonical_id=canonical_id,
            job_ad_id=job_ad_id,
            source_record_ids=tuple(item.strip() for item in source_ids),
            title=_string(value["title"], "registry job title"),
            category=_string(value["category"], "registry job category"),
            subcategory=_string(value["subcategory"], "registry job subcategory", nullable=True),
            locations=tuple(item.strip() for item in locations),
            organization=_string(value["organization"], "registry job organization"),
            headcount=_integer(value["headcount"], "registry job headcount"),
            degree=_string(value["degree"], "registry job degree", nullable=True),
            employment_type=_string(value["employmentType"], "registry job employmentType"),
            salary=_string(value["salary"], "registry job salary"),
            duty=_string(value["duty"], "registry job duty"),
            requirement=_string(value["requirement"], "registry job requirement"),
            source_changed_at=_instant(value["sourceChangedAt"], "registry job sourceChangedAt"),
            first_seen_at=_instant(value["firstSeenAt"], "registry job firstSeenAt"),
            last_seen_at=_instant(value["lastSeenAt"], "registry job lastSeenAt"),
            status=status,
            status_reason=_string(value["statusReason"], "registry job statusReason"),
            consecutive_misses=_integer(value["consecutiveMisses"], "registry job consecutiveMisses"),
            content_hash=content_hash,
            official_status=_integer(value["officialStatus"], "registry job officialStatus"),
        )


@dataclass(frozen=True, slots=True)
class OfficialJobSnapshot:
    version: str
    last_successful_sync_at: datetime
    jobs: tuple[OfficialJob, ...]

    @classmethod
    def parse(cls, payload: bytes) -> "OfficialJobSnapshot":
        if not isinstance(payload, bytes) or not payload or len(payload) > 32_000_000:
            raise ValueError("official snapshot bytes invalid")
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("official snapshot JSON invalid") from None
        if not isinstance(value, dict) or set(value) != _REGISTRY_FIELDS:
            raise ValueError("registry fields invalid")
        version = _string(value["version"], "registry version")
        raw_jobs = value["jobs"]
        if not isinstance(raw_jobs, list):
            raise ValueError("registry jobs invalid")
        jobs = tuple(OfficialJob.parse(item) for item in raw_jobs)
        if len({job.canonical_id for job in jobs}) != len(jobs):
            raise ValueError("duplicate official job id")
        return cls(
            version,
            _instant(value["lastSuccessfulSyncAt"], "registry sync time"),
            jobs,
        )


class OfficialProjectionRepository(Protocol):
    def project_official(
        self,
        command: ProjectOfficialPosition,
        *,
        import_evidence: dict[str, object] | None = None,
    ): ...

    def project_official_version(self, command: ProjectOfficialVersion): ...


@dataclass(frozen=True, slots=True)
class OfficialPositionProjection:
    position: object
    official_version: object

    @property
    def position_id(self) -> UUID:
        value = getattr(self.position, "position_id", None)
        if not isinstance(value, UUID):
            raise ValueError("official position projection invalid")
        return value


def project_official_jobs(
    snapshot: OfficialJobSnapshot,
    repository: OfficialProjectionRepository,
    owner_id: UUID,
    request_id: UUID,
) -> tuple[object, ...]:
    if not isinstance(snapshot, OfficialJobSnapshot):
        raise ValueError("official snapshot required")
    if not isinstance(owner_id, UUID) or not isinstance(request_id, UUID):
        raise ValueError("official projection identifiers invalid")
    projected = []
    for job in snapshot.jobs:
        position_id = uuid5(owner_id, f"official-position:{job.canonical_id}")
        evidence = {
            "evidence_id": uuid5(
                owner_id,
                f"official-evidence:{job.canonical_id}:{job.content_hash}",
            ),
            "owner_id": owner_id,
            "position_id": position_id,
            "draft_id": None,
            "source_conversation_id": None,
            "source_message_seq": None,
            "source_kind": "official_snapshot",
            "source_key": f"{job.canonical_id}:{job.content_hash}",
            "rule_version": "official-registry-v1",
            "evidence": {
                "content_hash": job.content_hash,
                "last_successful_sync_at": snapshot.last_successful_sync_at.isoformat(),
                "snapshot_version": snapshot.version,
            },
        }
        record = repository.project_official(ProjectOfficialPosition(
            owner_id=owner_id,
            position_id=position_id,
            client_request_id=uuid5(request_id, f"official-position:{job.canonical_id}"),
            official_job_id=job.canonical_id,
            title=job.title,
            department=job.organization,
            locations=job.locations,
            official_status=job.status,
            source_version=snapshot.version,
            content_hash=job.content_hash,
            source_synced_at=snapshot.last_successful_sync_at,
        ), import_evidence=evidence)
        project_version = getattr(repository, "project_official_version", None)
        if not callable(project_version):
            projected.append(record)
            continue
        official_version = project_version(ProjectOfficialVersion(
            official_position_version_id=uuid5(
                position_id,
                f"official-version:{snapshot.version}:{job.content_hash}",
            ),
            owner_id=owner_id,
            position_id=position_id,
            client_request_id=uuid5(
                request_id, f"official-version:{job.canonical_id}:{job.content_hash}"
            ),
            official_job_id=job.canonical_id,
            title=job.title,
            department=job.organization,
            locations=job.locations,
            category=job.category,
            subcategory=job.subcategory,
            headcount=job.headcount,
            degree=job.degree,
            employment_type=job.employment_type,
            salary=job.salary,
            duty=job.duty,
            requirement=job.requirement,
            source_version=snapshot.version,
            source_changed_at=job.source_changed_at,
            content_hash=job.content_hash,
            first_observed_at=job.first_seen_at,
            last_observed_at=job.last_seen_at,
            official_status=job.status,
            status_reason=job.status_reason,
            evidence={
                "job_ad_id": str(job.job_ad_id),
                "source_record_ids": list(job.source_record_ids),
                "snapshot_version": snapshot.version,
                "last_successful_sync_at": snapshot.last_successful_sync_at.isoformat(),
            },
            consecutive_misses=job.consecutive_misses,
            official_status_code=job.official_status,
            source_snapshot_at=snapshot.last_successful_sync_at,
        ))
        projected.append(OfficialPositionProjection(record, official_version))
    return tuple(projected)


@dataclass(frozen=True, slots=True)
class HistoricalMessage:
    sequence: int
    text: str

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("historical message sequence invalid")
        _string(self.text, "historical message text")


@dataclass(frozen=True, slots=True)
class HistoricalConversation:
    conversation_id: UUID
    title: str
    messages: tuple[HistoricalMessage, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, UUID):
            raise ValueError("historical conversation id invalid")
        _string(self.title, "historical conversation title")
        if not isinstance(self.messages, tuple) or any(
            not isinstance(message, HistoricalMessage) for message in self.messages
        ):
            raise ValueError("historical conversation messages invalid")


@dataclass(frozen=True, slots=True)
class HistoricalExactLink:
    conversation_id: UUID
    official_job_id: str
    message_sequence: int | None
    source_location: str
    rule_version: str


@dataclass(frozen=True, slots=True)
class HistoricalDraftProposal:
    conversation_id: UUID
    title: str
    source_key: str
    evidence: dict[str, object]
    rule_version: str


@dataclass(frozen=True, slots=True)
class HistoricalDiscovery:
    exact_links: tuple[HistoricalExactLink, ...]
    drafts: tuple[HistoricalDraftProposal, ...]
    skipped_conversation_ids: tuple[UUID, ...]


class HistoricalImportRepository(Protocol):
    def bind_conversation(
        self,
        command: BindPositionConversation,
        *,
        import_evidence: dict[str, object] | None = None,
    ): ...

    def propose_draft(
        self,
        command: ProposePositionDraft,
        *,
        import_evidence: dict[str, object] | None = None,
    ): ...


def discover_historical_positions(
    conversations: list[HistoricalConversation],
    official_positions: dict[str, str],
    *,
    rule_version: str,
) -> HistoricalDiscovery:
    rule = _string(rule_version, "historical rule version")
    normalized_official = {
        key.upper(): _string(title, "official position title")
        for key, title in official_positions.items()
        if isinstance(key, str) and _JOB_ID.fullmatch(key.upper())
    }
    if len(normalized_official) != len(official_positions):
        raise ValueError("historical official positions invalid")
    exact: list[HistoricalExactLink] = []
    drafts: list[HistoricalDraftProposal] = []
    skipped: list[UUID] = []
    for conversation in conversations:
        if not isinstance(conversation, HistoricalConversation):
            raise ValueError("historical conversation invalid")
        evidence_text = "\n".join(
            [conversation.title, *(message.text for message in conversation.messages)]
        ).upper()
        ids = tuple(dict.fromkeys(_JOB_REFERENCE.findall(evidence_text)))
        known = tuple(job_id for job_id in ids if job_id in normalized_official)
        title_text = conversation.title.upper()
        matching_evidence = {
            job_id: (
                (None, "title")
                if job_id in title_text
                else next(
                    (
                        (message.sequence, "message")
                        for message in conversation.messages
                        if job_id in message.text.upper()
                    ),
                    (None, "title"),
                )
            )
            for job_id in ids
        }
        if len(ids) == 1 and len(known) == 1:
            message_sequence, source_location = matching_evidence[known[0]]
            exact.append(HistoricalExactLink(
                conversation.conversation_id, known[0],
                message_sequence, source_location, rule,
            ))
            continue
        if len(ids) > 1:
            for job_id in ids:
                message_sequence, source_location = matching_evidence[job_id]
                drafts.append(HistoricalDraftProposal(
                    conversation.conversation_id,
                    normalized_official.get(job_id, conversation.title.strip()),
                    f"conversation:{conversation.conversation_id}:job:{job_id}",
                    {
                        "job_id": job_id,
                        "message_seq": message_sequence,
                        "source_location": source_location,
                        "multi_position": True,
                    },
                    rule,
                ))
            continue
        if ids or _POSITION_INTENT.search(evidence_text):
            if ids:
                message_sequence, source_location = matching_evidence[ids[0]]
            elif _POSITION_INTENT.search(title_text):
                message_sequence, source_location = None, "title"
            else:
                message_sequence, source_location = next(
                    (
                        (message.sequence, "message")
                        for message in conversation.messages
                        if _POSITION_INTENT.search(message.text.upper())
                    ),
                    (None, "message"),
                )
            drafts.append(HistoricalDraftProposal(
                conversation.conversation_id,
                conversation.title.strip(),
                f"conversation:{conversation.conversation_id}:draft:1",
                {
                    "job_ids": list(ids),
                    "message_seq": message_sequence,
                    "source_location": source_location,
                },
                rule,
            ))
        else:
            skipped.append(conversation.conversation_id)
    return HistoricalDiscovery(tuple(exact), tuple(drafts), tuple(skipped))


def apply_historical_discovery(
    discovery: HistoricalDiscovery,
    official_position_ids: dict[str, UUID],
    repository: HistoricalImportRepository,
    owner_id: UUID,
    request_id: UUID,
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    if not isinstance(discovery, HistoricalDiscovery):
        raise ValueError("historical discovery required")
    if not isinstance(owner_id, UUID) or not isinstance(request_id, UUID):
        raise ValueError("historical import identifiers invalid")
    normalized_positions = {
        job_id.upper(): position_id
        for job_id, position_id in official_position_ids.items()
        if isinstance(job_id, str)
        and _JOB_ID.fullmatch(job_id.upper())
        and isinstance(position_id, UUID)
    }
    if len(normalized_positions) != len(official_position_ids):
        raise ValueError("historical official position identities invalid")
    bindings = []
    for link in discovery.exact_links:
        position_id = normalized_positions.get(link.official_job_id)
        if position_id is None:
            raise ValueError("historical exact position missing")
        key = f"exact:{link.conversation_id}:{link.official_job_id}"
        evidence = {
            "evidence_id": uuid5(owner_id, f"historical-evidence:{key}:{link.rule_version}"),
            "owner_id": owner_id,
            "position_id": position_id,
            "draft_id": None,
            "source_conversation_id": link.conversation_id,
            "source_message_seq": link.message_sequence,
            "source_kind": "historical_exact",
            "source_key": key,
            "rule_version": link.rule_version,
            "evidence": {
                "official_job_id": link.official_job_id,
                "source_location": link.source_location,
            },
        }
        binding = repository.bind_conversation(BindPositionConversation(
            owner_id=owner_id,
            position_id=position_id,
            conversation_id=link.conversation_id,
            client_request_id=uuid5(request_id, key),
            binding_kind="historical_exact",
        ), import_evidence=evidence)
        bindings.append(binding)
    drafts = []
    for proposal in discovery.drafts:
        draft_id = uuid5(owner_id, proposal.source_key)
        evidence = {
            "evidence_id": uuid5(
                owner_id,
                f"historical-evidence:{proposal.source_key}:{proposal.rule_version}",
            ),
            "owner_id": owner_id,
            "position_id": None,
            "draft_id": draft_id,
            "source_conversation_id": proposal.conversation_id,
            "source_message_seq": proposal.evidence.get("message_seq"),
            "source_kind": "historical_draft",
            "source_key": proposal.source_key,
            "rule_version": proposal.rule_version,
            "evidence": proposal.evidence,
        }
        draft = repository.propose_draft(ProposePositionDraft(
            owner_id=owner_id,
            draft_id=draft_id,
            client_request_id=uuid5(request_id, proposal.source_key),
            source_kind="historical_conversation",
            source_key=proposal.source_key,
            source_conversation_id=proposal.conversation_id,
            title=proposal.title,
            proposal={},
            evidence=proposal.evidence,
            discovery_rule_version=proposal.rule_version,
        ), import_evidence=evidence)
        drafts.append(draft)
    return tuple(bindings), tuple(drafts)
