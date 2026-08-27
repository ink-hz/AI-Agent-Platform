"""Minimal Platform directory reader for VOC management display names."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn


class VocDirectoryUnavailable(RuntimeError):
    """The Platform identity directory could not be read safely."""


@dataclass(frozen=True, slots=True)
class SubmitterOption:
    internal_user_id: UUID
    display_name: str


class VocSubmitterDirectory:
    def __init__(self, database_url: str, *, connect=psycopg.connect) -> None:
        parsed = validate_control_dsn(database_url, purpose="app")
        self.environment = parsed.environment
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def names_for(self, ids: frozenset[UUID]) -> dict[UUID, str]:
        if len(ids) > 100:
            raise ValueError("too many VOC submitters")
        if not ids:
            return {}
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select internal_user_id, display_name "
                    "from platform_control.internal_users "
                    "where internal_user_id = any(%s)",
                    (list(ids),),
                ).fetchall()
            return {row["internal_user_id"]: row["display_name"] for row in rows}
        except psycopg.Error:
            raise VocDirectoryUnavailable("VOC directory unavailable") from None

    def list_submitters(self) -> tuple[SubmitterOption, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select internal_user_id, display_name "
                    "from platform_control.internal_users "
                    "order by display_name, internal_user_id"
                ).fetchall()
            return tuple(SubmitterOption(**row) for row in rows)
        except psycopg.Error:
            raise VocDirectoryUnavailable("VOC directory unavailable") from None
