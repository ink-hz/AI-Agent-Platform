from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid5

import psycopg
from psycopg.rows import dict_row

from .position_intelligence_models import (
    TASK_KINDS,
    HrPositionContextEnvelope,
    OfficialPositionVersion,
    PositionContextVersion,
    thaw_json,
)
from .position_intelligence_repository import _context, _official


class HrTaskContextError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HrTaskMaterial:
    attachment_id: UUID
    position_id: UUID
    sha256: str
    state: str
    active: bool
    retained_until: datetime
    erasure_pending: bool

    def __post_init__(self) -> None:
        if not isinstance(self.attachment_id, UUID) or not isinstance(self.position_id, UUID):
            raise ValueError("material identifiers invalid")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(value not in "0123456789abcdef" for value in self.sha256)
        ):
            raise ValueError("material hash invalid")
        if not isinstance(self.state, str) or not isinstance(self.active, bool):
            raise ValueError("material state invalid")
        if not isinstance(self.retained_until, datetime) or self.retained_until.tzinfo is None:
            raise ValueError("material retention invalid")
        if not isinstance(self.erasure_pending, bool):
            raise ValueError("material erasure state invalid")


@dataclass(frozen=True, slots=True)
class HrTaskScope:
    owner_id: UUID
    position_id: UUID
    conversation_id: UUID
    turn_id: UUID
    task_kind: str
    official: OfficialPositionVersion | None
    context: PositionContextVersion | None
    materials: tuple[HrTaskMaterial, ...]
    candidate_id: UUID | None
    position_candidate_id: UUID | None
    position_title: str | None = None
    client_request_id: UUID | None = None

    def __post_init__(self) -> None:
        for value in (
            self.owner_id,
            self.position_id,
            self.conversation_id,
            self.turn_id,
        ):
            if not isinstance(value, UUID):
                raise ValueError("HR task scope identifiers invalid")
        if self.task_kind not in TASK_KINDS:
            raise ValueError("HR task kind invalid")
        if not isinstance(self.materials, tuple) or any(
            not isinstance(value, HrTaskMaterial) for value in self.materials
        ):
            raise ValueError("HR task materials invalid")
        for value in (self.candidate_id, self.position_candidate_id):
            if value is not None and not isinstance(value, UUID):
                raise ValueError("candidate identifiers invalid")
        if (self.candidate_id is None) != (self.position_candidate_id is None):
            raise ValueError("candidate identifiers invalid")
        if self.position_title is not None and (
            not isinstance(self.position_title, str)
            or not self.position_title.strip()
            or len(self.position_title) > 500
        ):
            raise ValueError("position title invalid")
        if self.client_request_id is not None and not isinstance(
            self.client_request_id, UUID
        ):
            raise ValueError("HR task request identifier invalid")


class HrTaskContextSource(Protocol):
    def existing_for_turn(
        self, owner_id: UUID, conversation_id: UUID, turn_id: UUID
    ) -> HrPositionContextEnvelope | None: ...

    def load_for_turn(
        self, owner_id: UUID, conversation_id: UUID, turn_id: UUID
    ) -> HrTaskScope: ...

    def record_for_turn(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        turn_id: UUID,
        envelope: HrPositionContextEnvelope,
    ) -> object: ...


class CandidateEnvelopeProvider(Protocol):
    def for_task(
        self,
        owner_id: UUID,
        position_id: UUID,
        candidate_id: UUID | None,
        position_candidate_id: UUID | None,
    ) -> CandidateEnvelopeFragment: ...


@dataclass(frozen=True, slots=True)
class CandidateEnvelopeFragment:
    candidate_id: UUID
    position_candidate_id: UUID
    context_version_id: UUID
    document_attachment_ids: tuple[UUID, ...]
    human_feedback_ids: tuple[UUID, ...]
    prompt_context: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, UUID) for value in (
            self.candidate_id, self.position_candidate_id, self.context_version_id,
        )):
            raise ValueError("candidate context identifiers invalid")
        for values in (self.document_attachment_ids, self.human_feedback_ids):
            if not isinstance(values, tuple) or any(
                not isinstance(value, UUID) for value in values
            ) or len(values) != len(set(values)) or len(values) > 100:
                raise ValueError("candidate context identifiers invalid")
        if not self.document_attachment_ids:
            raise ValueError("candidate documents unavailable")
        if not isinstance(self.prompt_context, str) or not self.prompt_context.strip():
            raise ValueError("candidate prompt context invalid")


def _canonical_document(envelope: HrPositionContextEnvelope) -> dict[str, object]:
    return {
        "candidate_id": str(envelope.candidate_id) if envelope.candidate_id else None,
        "context_version_id": (
            str(envelope.context_version_id) if envelope.context_version_id else None
        ),
        "document_attachment_ids": [
            str(value) for value in envelope.document_attachment_ids
        ],
        "human_feedback_ids": [str(value) for value in envelope.human_feedback_ids],
        "material_attachment_ids": [
            str(value) for value in envelope.material_attachment_ids
        ],
        "official_version_id": (
            str(envelope.official_version_id) if envelope.official_version_id else None
        ),
        "position_candidate_id": (
            str(envelope.position_candidate_id)
            if envelope.position_candidate_id else None
        ),
        "position_id": str(envelope.position_id),
        "prompt_context": envelope.prompt_context,
        "task_kind": envelope.task_kind,
    }


def canonical_hash(envelope: HrPositionContextEnvelope) -> str:
    if not isinstance(envelope, HrPositionContextEnvelope):
        raise ValueError("HR position context envelope required")
    raw = json.dumps(
        _canonical_document(envelope),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _prompt(scope: HrTaskScope, candidate_fragment: CandidateEnvelopeFragment | None) -> str:
    official = None
    if scope.official is not None:
        official = {
            "version_id": str(scope.official.official_position_version_id),
            "job_id": scope.official.official_job_id,
            "title": scope.official.title,
            "department": scope.official.department,
            "locations": list(scope.official.locations),
            "category": scope.official.category,
            "subcategory": scope.official.subcategory,
            "headcount": scope.official.headcount,
            "degree": scope.official.degree,
            "employment_type": scope.official.employment_type,
            "salary": scope.official.salary,
            "duty": scope.official.duty,
            "requirement": scope.official.requirement,
            "status": scope.official.official_status,
            "status_code": scope.official.official_status_code,
            "status_reason": scope.official.status_reason,
            "consecutive_misses": scope.official.consecutive_misses,
            "source_changed_at": scope.official.source_changed_at.isoformat(),
            "first_observed_at": scope.official.first_observed_at.isoformat(),
            "last_observed_at": scope.official.last_observed_at.isoformat(),
            "source_version": scope.official.source_version,
            "content_hash": scope.official.content_hash,
        }
    context = None
    if scope.context is not None:
        context = {
            "version_id": str(scope.context.context_version_id),
            "version_number": scope.context.version_number,
            "summary": scope.context.summary,
            "modules": thaw_json(scope.context.modules),
        }
    document: dict[str, object] = {
        "boundary": "Use only this position and these explicitly selected inputs.",
        "position_id": str(scope.position_id),
        "position_title": scope.position_title,
        "task_kind": scope.task_kind,
        "official_facts": official,
        "confirmed_context": context,
        "selected_materials": [
            {"attachment_id": str(item.attachment_id), "sha256": item.sha256}
            for item in scope.materials
        ],
    }
    if candidate_fragment is not None:
        document["candidate_context"] = candidate_fragment.prompt_context
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class HrTaskContextProvider:
    def __init__(
        self,
        source: HrTaskContextSource,
        *,
        candidate_provider: CandidateEnvelopeProvider | None = None,
    ) -> None:
        for name in ("existing_for_turn", "load_for_turn", "record_for_turn"):
            if not callable(getattr(source, name, None)):
                raise ValueError("HR task context source invalid")
        if candidate_provider is not None and not callable(
            getattr(candidate_provider, "for_task", None)
        ):
            raise ValueError("candidate envelope provider invalid")
        self._source = source
        self._candidate_provider = candidate_provider

    def build_for_turn(
        self, owner_id: UUID, conversation_id: UUID, turn_id: UUID
    ) -> HrPositionContextEnvelope:
        if any(
            not isinstance(value, UUID)
            for value in (owner_id, conversation_id, turn_id)
        ):
            raise ValueError("HR task context identifiers invalid")
        existing = self._source.existing_for_turn(
            owner_id, conversation_id, turn_id
        )
        if existing is not None:
            if canonical_hash(existing) != existing.canonical_sha256:
                raise HrTaskContextError("recorded HR task context invalid")
            return existing
        scope = self._source.load_for_turn(owner_id, conversation_id, turn_id)
        if (
            not isinstance(scope, HrTaskScope)
            or scope.owner_id != owner_id
            or scope.conversation_id != conversation_id
            or scope.turn_id != turn_id
        ):
            raise HrTaskContextError("HR task scope invalid")
        if scope.official is not None and (
            scope.official.owner_id != owner_id
            or scope.official.position_id != scope.position_id
        ):
            raise HrTaskContextError("official position scope invalid")
        if scope.context is not None and (
            scope.context.owner_id != owner_id
            or scope.context.position_id != scope.position_id
            or scope.context.state != "confirmed"
        ):
            raise HrTaskContextError("confirmed position context invalid")
        candidate_task = scope.task_kind in {"candidate_match", "candidate_interview_plan"}
        if candidate_task and (scope.context is None or scope.candidate_id is None):
            raise HrTaskContextError("candidate context unavailable")
        if scope.candidate_id is not None and scope.context is None:
            raise HrTaskContextError("candidate context unavailable")
        if scope.task_kind == "candidate_comparison" and scope.candidate_id is not None:
            raise HrTaskContextError("candidate comparison scope invalid")
        if scope.official is None and scope.context is None and scope.position_title is None:
            raise HrTaskContextError("position context unavailable")
        now = datetime.now().astimezone()
        for material in scope.materials:
            if material.position_id != scope.position_id:
                raise HrTaskContextError("material scope invalid")
            if (
                material.state != "ready"
                or not material.active
                or material.retained_until <= now
                or material.erasure_pending
            ):
                raise HrTaskContextError("material unavailable")
        candidate_fragment = None
        document_ids: tuple[UUID, ...] = ()
        feedback_ids: tuple[UUID, ...] = ()
        if scope.candidate_id is not None:
            if self._candidate_provider is None:
                raise HrTaskContextError("candidate context unavailable")
            try:
                candidate_fragment = self._candidate_provider.for_task(
                    owner_id,
                    scope.position_id,
                    scope.candidate_id,
                    scope.position_candidate_id,
                )
            except (RuntimeError, ValueError):
                raise HrTaskContextError(
                    "candidate context unavailable"
                ) from None
            if not isinstance(candidate_fragment, CandidateEnvelopeFragment):
                raise HrTaskContextError("candidate context scope invalid")
            if (
                getattr(candidate_fragment, "candidate_id", None) != scope.candidate_id
                or getattr(candidate_fragment, "position_candidate_id", None)
                != scope.position_candidate_id
                or candidate_fragment.context_version_id
                != scope.context.context_version_id
            ):
                raise HrTaskContextError("candidate context scope invalid")
            document_ids = tuple(
                getattr(candidate_fragment, "document_attachment_ids", ())
            )
            feedback_ids = tuple(
                getattr(candidate_fragment, "human_feedback_ids", ())
            )
            if not document_ids:
                raise HrTaskContextError("candidate documents unavailable")
        prompt_context = _prompt(scope, candidate_fragment)
        placeholder = HrPositionContextEnvelope(
            position_id=scope.position_id,
            official_version_id=(
                scope.official.official_position_version_id
                if scope.official is not None else None
            ),
            context_version_id=(
                scope.context.context_version_id if scope.context is not None else None
            ),
            task_kind=scope.task_kind,
            material_attachment_ids=tuple(
                sorted(
                    (item.attachment_id for item in scope.materials),
                    key=str,
                )
            ),
            candidate_id=scope.candidate_id,
            position_candidate_id=scope.position_candidate_id,
            document_attachment_ids=tuple(sorted(document_ids, key=str)),
            human_feedback_ids=tuple(sorted(feedback_ids, key=str)),
            prompt_context=prompt_context,
            canonical_sha256="0" * 64,
        )
        envelope = HrPositionContextEnvelope(
            **{
                **_canonical_document(placeholder),
                "position_id": placeholder.position_id,
                "official_version_id": placeholder.official_version_id,
                "context_version_id": placeholder.context_version_id,
                "material_attachment_ids": placeholder.material_attachment_ids,
                "candidate_id": placeholder.candidate_id,
                "position_candidate_id": placeholder.position_candidate_id,
                "document_attachment_ids": placeholder.document_attachment_ids,
                "human_feedback_ids": placeholder.human_feedback_ids,
                "canonical_sha256": canonical_hash(placeholder),
            }
        )
        self._source.record_for_turn(
            owner_id, conversation_id, turn_id, envelope
        )
        return envelope


class PostgresHrTaskContextSource:
    def __init__(
        self,
        database_url: str,
        *,
        task_selection: Callable[
            [UUID, UUID, UUID, UUID], tuple[str, UUID | None, UUID | None]
        ] | None = None,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("HR task context database URL required")
        if task_selection is not None and not callable(task_selection):
            raise ValueError("HR task selection provider invalid")
        self._database_url = database_url
        self._task_selection = task_selection
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def existing_for_turn(
        self, owner_id: UUID, conversation_id: UUID, turn_id: UUID
    ) -> HrPositionContextEnvelope | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.position_task_records "
                    "where owner_internal_user_id=%s and conversation_id=%s "
                    "and turn_id=%s",
                    (owner_id, conversation_id, turn_id),
                ).fetchone()
            if row is None:
                return None
            return HrPositionContextEnvelope(
                position_id=row["position_id"],
                official_version_id=row["official_position_version_id"],
                context_version_id=row["context_version_id"],
                task_kind=row["task_kind"],
                material_attachment_ids=tuple(row["material_attachment_ids"]),
                candidate_id=row["candidate_id"],
                position_candidate_id=row["position_candidate_id"],
                document_attachment_ids=tuple(row["document_attachment_ids"]),
                human_feedback_ids=tuple(row["human_feedback_ids"]),
                prompt_context=row["prompt_context"],
                canonical_sha256=row["canonical_sha256"],
            )
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise HrTaskContextError("recorded HR task context unavailable") from None

    def load_for_turn(
        self, owner_id: UUID, conversation_id: UUID, turn_id: UUID
    ) -> HrTaskScope:
        try:
            with self._connection() as connection:
                scope = connection.execute(
                    "select position.position_id,position.title,turn.client_request_id,"
                    "position.current_official_version_id,"
                    "position.current_context_version_id "
                    "from platform_control.conversations conversation "
                    "join platform_control.conversation_turns turn "
                    "on turn.conversation_id=conversation.conversation_id "
                    "join platform_hr.position_conversations binding "
                    "on binding.conversation_id=conversation.conversation_id "
                    "and binding.owner_internal_user_id=conversation.owner_internal_user_id "
                    "join platform_hr.positions position "
                    "on position.position_id=binding.position_id "
                    "and position.owner_internal_user_id=binding.owner_internal_user_id "
                    "where conversation.owner_internal_user_id=%s "
                    "and conversation.conversation_id=%s and turn.turn_id=%s "
                    "and conversation.mode='direct_agent' "
                    "and conversation.direct_agent_id='hr-bot'",
                    (owner_id, conversation_id, turn_id),
                ).fetchone()
                if scope is None:
                    raise HrTaskContextError("HR task scope unavailable")
                official_row = None
                if scope["current_official_version_id"] is not None:
                    official_row = connection.execute(
                        "select * from platform_hr.official_position_versions "
                        "where owner_internal_user_id=%s and position_id=%s "
                        "and official_position_version_id=%s",
                        (
                            owner_id,
                            scope["position_id"],
                            scope["current_official_version_id"],
                        ),
                    ).fetchone()
                    if official_row is None:
                        raise HrTaskContextError("official position context unavailable")
                request_row = connection.execute(
                    "select * from platform_hr.read_position_task_request_v69(%s,%s,%s)",
                    (owner_id, scope["position_id"], scope["client_request_id"]),
                ).fetchone()
                if request_row is not None and request_row["status"] != "active":
                    raise HrTaskContextError("HR task selection unavailable")
                context_row = None
                expected_context_id = (
                    scope["current_context_version_id"]
                    if request_row is None
                    else request_row["expected_context_version_id"]
                )
                if expected_context_id is not None:
                    context_row = connection.execute(
                        "select * from platform_hr.position_context_versions "
                        "where owner_internal_user_id=%s and position_id=%s "
                        "and context_version_id=%s and state='confirmed'",
                        (
                            owner_id,
                            scope["position_id"],
                            expected_context_id,
                        ),
                    ).fetchone()
                    if context_row is None:
                        raise HrTaskContextError("confirmed position context unavailable")
                material_rows = connection.execute(
                    "select attachment.attachment_id,material.position_id,"
                    "encode(attachment.sha256,'hex') as sha256,attachment.state,"
                    "coalesce(material.active,false) as active,"
                    "attachment.retained_until,exists(select 1 from "
                    "platform_attachments.erasure_jobs erasure where "
                    "erasure.attachment_id=attachment.attachment_id) as erasure_pending "
                    "from platform_attachments.bindings binding "
                    "join platform_attachments.attachments attachment "
                    "on attachment.attachment_id=binding.attachment_id "
                    "and attachment.owner_internal_user_id=binding.owner_internal_user_id "
                    "left join platform_hr.position_materials material "
                    "on material.attachment_id=attachment.attachment_id "
                    "and material.owner_internal_user_id=attachment.owner_internal_user_id "
                    "and material.position_id=%s "
                    "where binding.owner_internal_user_id=%s "
                    "and binding.conversation_id=%s and binding.turn_id=%s "
                    "and binding.kind='turn_input' order by attachment.attachment_id",
                    (scope["position_id"], owner_id, conversation_id, turn_id),
                ).fetchall()
            materials = []
            for row in material_rows:
                if row["position_id"] is None or row["sha256"] is None:
                    raise HrTaskContextError("material scope invalid")
                materials.append(HrTaskMaterial(
                    attachment_id=row["attachment_id"],
                    position_id=row["position_id"],
                    sha256=row["sha256"],
                    state=row["state"],
                    active=row["active"],
                    retained_until=row["retained_until"],
                    erasure_pending=row["erasure_pending"],
                ))
            task_kind, candidate_id, position_candidate_id = (
                self._task_selection(
                    owner_id, scope["position_id"], conversation_id, turn_id
                )
                if self._task_selection is not None
                else (
                    (
                        request_row["task_kind"], request_row["candidate_id"],
                        request_row["position_candidate_id"],
                    )
                    if request_row is not None
                    else ("freeform", None, None)
                )
            )
            return HrTaskScope(
                owner_id=owner_id,
                position_id=scope["position_id"],
                conversation_id=conversation_id,
                turn_id=turn_id,
                task_kind=task_kind,
                official=_official(official_row) if official_row is not None else None,
                context=_context(context_row) if context_row is not None else None,
                materials=tuple(materials),
                candidate_id=candidate_id,
                position_candidate_id=position_candidate_id,
                position_title=scope["title"],
                client_request_id=scope["client_request_id"],
            )
        except HrTaskContextError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise HrTaskContextError("HR task scope unavailable") from None

    def record_for_turn(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        turn_id: UUID,
        envelope: HrPositionContextEnvelope,
    ) -> object:
        if not isinstance(envelope, HrPositionContextEnvelope):
            raise ValueError("HR task context envelope required")
        try:
            with self._connection() as connection:
                request_row = connection.execute(
                    "select client_request_id from platform_control.conversation_turns "
                    "where conversation_id=%s and turn_id=%s",
                    (conversation_id, turn_id),
                ).fetchone()
                if request_row is None:
                    raise HrTaskContextError("HR task selection unavailable")
                return connection.execute(
                    "select (platform_hr.create_position_task_record_v69("
                    "%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s,%s::uuid[],"
                    "%s::uuid[],%s,%s,%s,%s)).*",
                    (
                        uuid5(envelope.position_id, f"task-record:{turn_id}"),
                        owner_id,
                        envelope.position_id,
                        request_row["client_request_id"],
                        envelope.task_kind,
                        envelope.official_version_id,
                        envelope.context_version_id,
                        list(envelope.material_attachment_ids),
                        envelope.candidate_id,
                        envelope.position_candidate_id,
                        list(envelope.document_attachment_ids),
                        list(envelope.human_feedback_ids),
                        conversation_id,
                        turn_id,
                        envelope.prompt_context,
                        envelope.canonical_sha256,
                    ),
                ).fetchone()
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise HrTaskContextError("HR task context could not be recorded") from None
