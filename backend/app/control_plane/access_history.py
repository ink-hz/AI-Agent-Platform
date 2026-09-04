from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .dsn import validate_control_dsn
from .models import AuthContext


class AccessHistoryError(RuntimeError):
    pass


class AccessHistoryInvalid(AccessHistoryError):
    pass


class AccessHistoryForbidden(AccessHistoryError):
    pass


class AccessHistoryUnavailable(AccessHistoryError):
    pass


@dataclass(frozen=True)
class PageAccessDescriptor:
    workspace_key: str
    page_key: str
    agent_id: str | None


@dataclass(frozen=True)
class AccessHistoryFilter:
    date_from: datetime
    date_to: datetime
    display_name: str | None
    workspace_key: str | None
    event_kind: Literal["login_succeeded", "page_view"] | None
    limit: int
    offset: int


@dataclass(frozen=True)
class AccessHistoryEvent:
    access_event_id: UUID
    display_name: str
    departments: tuple[str, ...]
    event_kind: str
    login_kind: str | None
    workspace_key: str | None
    page_key: str | None
    module_display_name: str | None
    page_display_name: str | None
    agent_id: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class AccessHistorySubject:
    display_name: str
    departments: tuple[str, ...]
    event_count: int
    latest_occurred_at: datetime
    latest_event_kind: str
    latest_workspace_key: str | None
    latest_module_display_name: str | None
    latest_page_display_name: str | None
    latest_agent_id: str | None


class AccessHistoryRepository:
    def __init__(
        self, control_database_url: str, *, connect=psycopg.connect
    ) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="app")
        self.environment = parsed.environment
        self._database_url = control_database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def record_page_view(
        self,
        event_id: UUID,
        context: AuthContext,
        page: PageAccessDescriptor,
    ) -> Literal["inserted", "duplicate", "rate_limited"]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control.append_page_view_v65("
                    "%s,%s,%s,%s,%s,%s)",
                    (
                        event_id,
                        context.internal_user_id,
                        context.session_id,
                        page.workspace_key,
                        page.page_key,
                        page.agent_id,
                    ),
                ).fetchone()
            if row is None or row["outcome"] not in {
                "inserted",
                "duplicate",
                "rate_limited",
            }:
                raise AccessHistoryUnavailable("page access outcome unavailable")
            return row["outcome"]
        except AccessHistoryUnavailable:
            raise
        except psycopg.errors.InsufficientPrivilege:
            raise AccessHistoryForbidden("page access rejected") from None
        except (psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation):
            raise AccessHistoryInvalid("page access event invalid") from None
        except psycopg.Error:
            raise AccessHistoryUnavailable("page access unavailable") from None

    def list_events(
        self, context: AuthContext, filters: AccessHistoryFilter
    ) -> list[AccessHistoryEvent]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_control.read_user_access_events_v67("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        context.internal_user_id,
                        context.session_id,
                        filters.date_from,
                        filters.date_to,
                        filters.display_name,
                        filters.workspace_key,
                        filters.event_kind,
                        filters.limit + 1,
                        filters.offset,
                    ),
                ).fetchall()
            return [
                AccessHistoryEvent(
                    access_event_id=row["access_event_id"],
                    display_name=row["display_name"],
                    departments=tuple(row["departments"] or ()),
                    event_kind=row["event_kind"],
                    login_kind=row["login_kind"],
                    workspace_key=row["workspace_key"],
                    page_key=row["page_key"],
                    module_display_name=row["module_display_name"],
                    page_display_name=row["page_display_name"],
                    agent_id=row["agent_id"],
                    occurred_at=row["occurred_at"],
                )
                for row in rows
            ]
        except psycopg.errors.InsufficientPrivilege:
            raise AccessHistoryForbidden("platform owner required") from None
        except psycopg.errors.CheckViolation:
            raise AccessHistoryInvalid("access history query invalid") from None
        except psycopg.Error:
            raise AccessHistoryUnavailable("access history unavailable") from None

    def list_subjects(
        self, context: AuthContext, filters: AccessHistoryFilter
    ) -> list[AccessHistorySubject]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_control.read_access_subjects_v67("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        context.internal_user_id,
                        context.session_id,
                        filters.date_from,
                        filters.date_to,
                        filters.display_name,
                        filters.workspace_key,
                        filters.event_kind,
                        filters.limit + 1,
                        filters.offset,
                    ),
                ).fetchall()
            return [
                AccessHistorySubject(
                    display_name=row["display_name"],
                    departments=tuple(row["departments"] or ()),
                    event_count=int(row["event_count"]),
                    latest_occurred_at=row["latest_occurred_at"],
                    latest_event_kind=row["latest_event_kind"],
                    latest_workspace_key=row["latest_workspace_key"],
                    latest_module_display_name=row["latest_module_display_name"],
                    latest_page_display_name=row["latest_page_display_name"],
                    latest_agent_id=row["latest_agent_id"],
                )
                for row in rows
            ]
        except psycopg.errors.InsufficientPrivilege:
            raise AccessHistoryForbidden("platform owner required") from None
        except psycopg.errors.CheckViolation:
            raise AccessHistoryInvalid("access subject query invalid") from None
        except psycopg.Error:
            raise AccessHistoryUnavailable("access history unavailable") from None


class UnavailableAccessHistoryRepository:
    def record_page_view(self, *_args, **_kwargs):
        raise AccessHistoryUnavailable("page access unavailable")

    def list_events(self, *_args, **_kwargs):
        raise AccessHistoryUnavailable("access history unavailable")

    def list_subjects(self, *_args, **_kwargs):
        raise AccessHistoryUnavailable("access history unavailable")
