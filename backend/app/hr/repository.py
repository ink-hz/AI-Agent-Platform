from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .models import CreateManualPosition, PositionDetail, PositionRecord


class HrRepositoryError(RuntimeError):
    pass


class HrNotFound(HrRepositoryError):
    pass


class HrConflict(HrRepositoryError):
    pass


class HrUnavailable(HrRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class PositionPage:
    items: tuple[PositionRecord, ...]
    next_cursor: str | None


def _record(row: dict[str, Any]) -> PositionRecord:
    locations = row["locations"]
    if not isinstance(locations, list) or any(
        not isinstance(value, str) for value in locations
    ):
        raise HrUnavailable("position projection invalid")
    return PositionRecord(
        position_id=row["position_id"],
        owner_id=row["owner_internal_user_id"],
        source_kind=row["source_kind"],
        official_job_id=row["official_job_id"],
        title=row["title"],
        department=row["department"],
        locations=tuple(locations),
        official_status=row["official_status"],
        internal_status=row["internal_status"],
        source_version=row["source_version"],
        row_version=row["row_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _cursor(updated_at: datetime, position_id: UUID) -> str:
    raw = json.dumps(
        {"position_id": str(position_id), "updated_at": updated_at.isoformat()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _parse_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        if not isinstance(value, str) or len(value) > 512:
            raise ValueError
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {
            "position_id",
            "updated_at",
        }:
            raise ValueError
        updated_at = datetime.fromisoformat(document["updated_at"])
        if updated_at.tzinfo is None:
            raise ValueError
        return updated_at, UUID(document["position_id"])
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise HrConflict("position cursor invalid") from None


class HrPositionRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("HR database URL required")
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def create_manual(self, command: CreateManualPosition) -> PositionRecord:
        if not isinstance(command, CreateManualPosition):
            raise ValueError("manual position command required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_position_v65("
                    "%s,%s,%s,'manual',null,%s,%s,%s::jsonb,null,null)).*",
                    (
                        command.position_id,
                        command.owner_id,
                        command.client_request_id,
                        command.title,
                        command.department,
                        json.dumps(command.locations, ensure_ascii=False),
                    ),
                ).fetchone()
            if row is None:
                raise HrUnavailable("position creation unavailable")
            return _record(row)
        except HrRepositoryError:
            raise
        except (psycopg.errors.UniqueViolation, psycopg.errors.SerializationFailure):
            raise HrConflict("position mutation conflict") from None
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise HrUnavailable("position repository unavailable") from None

    def position_for_owner(
        self, owner_id: UUID, position_id: UUID
    ) -> PositionDetail:
        if not isinstance(owner_id, UUID) or not isinstance(position_id, UUID):
            raise ValueError("position identifiers required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select position.*,(select count(*) from "
                    "platform_hr.position_conversations binding where "
                    "binding.position_id=position.position_id)::bigint "
                    "as conversation_count,(select count(*) from "
                    "platform_hr.position_materials material where "
                    "material.position_id=position.position_id and material.active)::bigint "
                    "as material_count,(select count(*) from "
                    "platform_hr.position_artifacts artifact where "
                    "artifact.position_id=position.position_id)::bigint "
                    "as artifact_count from platform_hr.positions position "
                    "where position.owner_internal_user_id=%s "
                    "and position.position_id=%s",
                    (owner_id, position_id),
                ).fetchone()
            if row is None:
                raise HrNotFound("position not found")
            return PositionDetail(
                position=_record(row),
                conversation_count=row["conversation_count"],
                material_count=row["material_count"],
                artifact_count=row["artifact_count"],
            )
        except HrRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise HrUnavailable("position repository unavailable") from None

    def list_positions(
        self,
        owner_id: UUID,
        *,
        query: str | None = None,
        source: str | None = None,
        internal_status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> PositionPage:
        if not isinstance(owner_id, UUID):
            raise ValueError("position owner required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("position page limit invalid")
        if source not in {None, "official_site", "manual"}:
            raise ValueError("position source filter invalid")
        if internal_status not in {None, "draft", "active", "archived"}:
            raise ValueError("position status filter invalid")
        normalized_query = query.strip() if isinstance(query, str) else None
        cursor_value = _parse_cursor(cursor) if cursor is not None else None
        clauses = ["owner_internal_user_id=%s"]
        values: list[object] = [owner_id]
        if normalized_query:
            clauses.append("(title ilike %s or official_job_id=%s)")
            values.extend((f"%{normalized_query}%", normalized_query.upper()))
        if source is not None:
            clauses.append("source_kind=%s")
            values.append(source)
        if internal_status is not None:
            clauses.append("internal_status=%s")
            values.append(internal_status)
        if cursor_value is not None:
            clauses.append("(updated_at,position_id)<(%s,%s)")
            values.extend(cursor_value)
        values.append(limit + 1)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.positions where "
                    + " and ".join(clauses)
                    + " order by updated_at desc,position_id desc limit %s",
                    values,
                ).fetchall()
            records = tuple(_record(row) for row in rows[:limit])
            next_cursor = (
                _cursor(records[-1].updated_at, records[-1].position_id)
                if len(rows) > limit
                else None
            )
            return PositionPage(records, next_cursor)
        except HrRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise HrUnavailable("position repository unavailable") from None
