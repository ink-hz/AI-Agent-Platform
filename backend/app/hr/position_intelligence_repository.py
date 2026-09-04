from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .position_intelligence_models import (
    ConfirmContextModules,
    CreateContextDraft,
    CreatePositionTaskRequest,
    OfficialPositionVersion,
    PositionContextVersion,
    PositionTaskRequest,
    ProjectOfficialVersion,
    thaw_json,
)


class PositionIntelligenceError(RuntimeError):
    pass


class PositionContextNotFound(PositionIntelligenceError):
    pass


class PositionContextConflict(PositionIntelligenceError):
    pass


class PositionIntelligenceUnavailable(PositionIntelligenceError):
    pass


def _context(row: dict[str, Any]) -> PositionContextVersion:
    materials = row["source_material_attachment_ids"]
    if not isinstance(materials, list):
        materials = list(materials)
    return PositionContextVersion(
        context_version_id=row["context_version_id"],
        owner_id=row["owner_internal_user_id"],
        position_id=row["position_id"],
        version_number=row["version_number"],
        state=row["state"],
        modules=row["modules"],
        summary=row["summary"],
        official_version_id=row["official_position_version_id"],
        base_context_version_id=row["base_context_version_id"],
        source_conversation_id=row["source_conversation_id"],
        source_turn_id=row["source_turn_id"],
        source_artifact_version_id=row["source_artifact_version_id"],
        source_material_attachment_ids=tuple(materials),
        agent_id=row["agent_id"],
        model_version=row["model_version"],
        created_by=row["created_by"],
        confirmed_by=row["confirmed_by"],
        created_at=row["created_at"],
        confirmed_at=row["confirmed_at"],
        row_version=row["row_version"],
    )


def _official(row: dict[str, Any]) -> OfficialPositionVersion:
    locations = row["locations"]
    return OfficialPositionVersion(
        official_position_version_id=row["official_position_version_id"],
        owner_id=row["owner_internal_user_id"],
        position_id=row["position_id"],
        official_job_id=row["official_job_id"],
        title=row["title"],
        department=row["department"],
        locations=tuple(locations),
        category=row["category"],
        subcategory=row["subcategory"],
        headcount=row["headcount"],
        degree=row["degree"],
        employment_type=row["employment_type"],
        salary=row["salary"],
        duty=row["duty"],
        requirement=row["requirement"],
        source_version=row["source_version"],
        source_changed_at=row["source_changed_at"],
        content_hash=row["content_hash"],
        first_observed_at=row["first_observed_at"],
        last_observed_at=row["last_observed_at"],
        official_status=row["official_status"],
        status_reason=row["status_reason"],
        evidence=row["evidence"],
        created_at=row["created_at"],
        consecutive_misses=row["consecutive_misses"],
        official_status_code=row["official_status_code"],
        source_snapshot_at=row["source_snapshot_at"],
    )


def _task_request(row: dict[str, Any]) -> PositionTaskRequest:
    return PositionTaskRequest(
        task_request_id=row["task_request_id"],
        owner_id=row["owner_internal_user_id"],
        position_id=row["position_id"],
        client_request_id=row["client_request_id"],
        canonical_payload_sha256=row["canonical_payload_sha256"],
        task_kind=row["task_kind"],
        expected_context_version_id=row["expected_context_version_id"],
        material_attachment_ids=tuple(row["material_attachment_ids"]),
        candidate_id=row["candidate_id"],
        position_candidate_id=row["position_candidate_id"],
        status=row["status"],
        created_at=row["created_at"],
    )


class PositionIntelligenceRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("position intelligence database URL required")
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def create_task_request(
        self, command: CreatePositionTaskRequest
    ) -> PositionTaskRequest:
        if not isinstance(command, CreatePositionTaskRequest):
            raise ValueError("position task request required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_position_task_request_v69("
                    "%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s)).*",
                    (
                        command.task_request_id, command.owner_id,
                        command.position_id, command.client_request_id,
                        command.canonical_payload_sha256, command.task_kind,
                        command.expected_context_version_id,
                        list(command.material_attachment_ids),
                        command.candidate_id, command.position_candidate_id,
                    ),
                ).fetchone()
            if row is None:
                raise PositionIntelligenceUnavailable("position task request unavailable")
            return _task_request(row)
        except psycopg.errors.NoDataFound:
            raise PositionContextNotFound("position task request inputs unavailable") from None
        except (psycopg.errors.SerializationFailure, psycopg.errors.UniqueViolation):
            raise PositionContextConflict("position task request conflict") from None
        except PositionIntelligenceError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionIntelligenceUnavailable("position task request unavailable") from None

    def task_request(
        self, owner_id: UUID, position_id: UUID, client_request_id: UUID
    ) -> PositionTaskRequest | None:
        if any(not isinstance(value, UUID) for value in (
            owner_id, position_id, client_request_id,
        )):
            raise ValueError("position task request identifiers invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.read_position_task_request_v69(%s,%s,%s)",
                    (owner_id, position_id, client_request_id),
                ).fetchone()
            return None if row is None else _task_request(row)
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionIntelligenceUnavailable("position task request unavailable") from None

    def current(
        self, owner_id: UUID, position_id: UUID
    ) -> PositionContextVersion | None:
        if not isinstance(owner_id, UUID) or not isinstance(position_id, UUID):
            raise ValueError("position context identifiers invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select version.* from platform_hr.positions position "
                    "join platform_hr.position_context_versions version "
                    "on version.context_version_id=position.current_context_version_id "
                    "and version.owner_internal_user_id=position.owner_internal_user_id "
                    "where position.owner_internal_user_id=%s and position.position_id=%s "
                    "and version.state='confirmed'",
                    (owner_id, position_id),
                ).fetchone()
            return None if row is None else _context(row)
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionIntelligenceUnavailable(
                "position context unavailable"
            ) from None

    def project_official_version(
        self, command: ProjectOfficialVersion
    ) -> OfficialPositionVersion:
        if not isinstance(command, ProjectOfficialVersion):
            raise ValueError("official version projection required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.project_official_version_v69("
                    "%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)).*",
                    (
                        command.official_position_version_id,
                        command.owner_id,
                        command.position_id,
                        command.client_request_id,
                        command.official_job_id,
                        command.title,
                        command.department,
                        json.dumps(command.locations, ensure_ascii=False),
                        command.category,
                        command.subcategory,
                        command.headcount,
                        command.degree,
                        command.employment_type,
                        command.salary,
                        command.duty,
                        command.requirement,
                        command.source_version,
                        command.source_changed_at,
                        command.content_hash,
                        command.first_observed_at,
                        command.last_observed_at,
                        command.official_status,
                        command.status_reason,
                        json.dumps(thaw_json(command.evidence), ensure_ascii=False),
                        command.consecutive_misses,
                        command.official_status_code,
                        command.source_snapshot_at,
                    ),
                ).fetchone()
            if row is None:
                raise PositionIntelligenceUnavailable("official version unavailable")
            return _official(row)
        except PositionIntelligenceError:
            raise
        except psycopg.errors.NoDataFound:
            raise PositionContextNotFound("position not found") from None
        except (psycopg.errors.SerializationFailure, psycopg.errors.UniqueViolation):
            raise PositionContextConflict("official version conflict") from None
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionIntelligenceUnavailable("official version unavailable") from None

    def official_versions(
        self, owner_id: UUID, position_id: UUID
    ) -> tuple[OfficialPositionVersion, ...]:
        if not isinstance(owner_id, UUID) or not isinstance(position_id, UUID):
            raise ValueError("official version identifiers invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select version.* from platform_hr.official_position_versions version "
                    "join platform_hr.positions position using (position_id) "
                    "where version.owner_internal_user_id=%s "
                    "and position.owner_internal_user_id=%s and version.position_id=%s "
                    "order by version.source_changed_at desc,version.created_at desc",
                    (owner_id, owner_id, position_id),
                ).fetchall()
            return tuple(_official(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionIntelligenceUnavailable("official versions unavailable") from None

    def official_version(
        self,
        owner_id: UUID,
        position_id: UUID,
        official_version_id: UUID,
    ) -> OfficialPositionVersion:
        if any(
            not isinstance(value, UUID)
            for value in (owner_id, position_id, official_version_id)
        ):
            raise ValueError("official version identifiers invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.official_position_versions "
                    "where owner_internal_user_id=%s and position_id=%s "
                    "and official_position_version_id=%s",
                    (owner_id, position_id, official_version_id),
                ).fetchone()
            if row is None:
                raise PositionContextNotFound("official version not found")
            return _official(row)
        except PositionIntelligenceError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionIntelligenceUnavailable("official version unavailable") from None

    def list_versions(
        self,
        owner_id: UUID,
        position_id: UUID,
        *,
        state: str | None = None,
    ) -> tuple[PositionContextVersion, ...]:
        if not isinstance(owner_id, UUID) or not isinstance(position_id, UUID):
            raise ValueError("position context identifiers invalid")
        if state not in {None, "draft", "confirmed", "superseded"}:
            raise ValueError("context state invalid")
        try:
            statement = (
                "select * from platform_hr.read_position_context_versions_v69(%s,%s)"
            )
            values: tuple[object, ...] = (owner_id, position_id)
            if state is not None:
                statement = f"select * from ({statement}) version where state=%s"
                values = (*values, state)
            with self._connection() as connection:
                rows = connection.execute(statement, values).fetchall()
            return tuple(_context(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionIntelligenceUnavailable(
                "position context unavailable"
            ) from None

    def create_draft(self, command: CreateContextDraft) -> PositionContextVersion:
        if not isinstance(command, CreateContextDraft):
            raise ValueError("context draft command required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_context_draft_v69("
                    "%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::uuid[],%s,%s,%s)).*",
                    (
                        command.context_version_id,
                        command.owner_id,
                        command.position_id,
                        command.client_request_id,
                        command.base_context_version_id,
                        command.official_version_id,
                        json.dumps(thaw_json(command.modules), ensure_ascii=False),
                        command.summary,
                        command.source_conversation_id,
                        command.source_turn_id,
                        command.source_artifact_version_id,
                        list(command.source_material_attachment_ids),
                        command.agent_id,
                        command.model_version,
                        command.created_by,
                    ),
                ).fetchone()
            if row is None:
                raise PositionIntelligenceUnavailable("context draft unavailable")
            return _context(row)
        except PositionIntelligenceError:
            raise
        except psycopg.errors.NoDataFound:
            raise PositionContextNotFound("position context not found") from None
        except (psycopg.errors.SerializationFailure, psycopg.errors.UniqueViolation):
            raise PositionContextConflict("position context conflict") from None
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionIntelligenceUnavailable("context draft unavailable") from None

    def confirm_modules(
        self, command: ConfirmContextModules
    ) -> PositionContextVersion:
        if not isinstance(command, ConfirmContextModules):
            raise ValueError("context confirmation command required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.confirm_context_modules_v69("
                    "%s,%s,%s,%s,%s,%s,%s::text[],%s)).*",
                    (
                        command.owner_id,
                        command.position_id,
                        command.draft_context_version_id,
                        command.client_request_id,
                        command.expected_current_context_version_id,
                        command.expected_draft_row_version,
                        list(command.module_names),
                        command.confirmed_by,
                    ),
                ).fetchone()
            if row is None:
                raise PositionIntelligenceUnavailable("context confirmation unavailable")
            return _context(row)
        except PositionIntelligenceError:
            raise
        except psycopg.errors.NoDataFound:
            raise PositionContextNotFound("position context not found") from None
        except (
            psycopg.errors.CheckViolation,
            psycopg.errors.SerializationFailure,
            psycopg.errors.UniqueViolation,
        ):
            raise PositionContextConflict("position context conflict") from None
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionIntelligenceUnavailable(
                "context confirmation unavailable"
            ) from None

    def compare(
        self,
        owner_id: UUID,
        position_id: UUID,
        left: UUID,
        right: UUID,
    ) -> dict[str, object]:
        if any(not isinstance(value, UUID) for value in (owner_id, position_id, left, right)):
            raise ValueError("position context identifiers invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.position_context_versions "
                    "where owner_internal_user_id=%s and position_id=%s "
                    "and context_version_id=any(%s::uuid[])",
                    (owner_id, position_id, [left, right]),
                ).fetchall()
            by_id = {row["context_version_id"]: _context(row) for row in rows}
            if left not in by_id or right not in by_id:
                raise PositionContextNotFound("position context not found")
            left_record, right_record = by_id[left], by_id[right]
            names = sorted(set(left_record.modules) | set(right_record.modules))
            changed = tuple(
                name for name in names
                if left_record.modules.get(name) != right_record.modules.get(name)
            )
            return {
                "left_version_id": left,
                "right_version_id": right,
                "changed_modules": changed,
                "left": left_record.modules,
                "right": right_record.modules,
            }
        except PositionIntelligenceError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionIntelligenceUnavailable(
                "position context comparison unavailable"
            ) from None
