"""Read-only, generation-bound organization projections using app credentials."""
from __future__ import annotations

from uuid import UUID

import psycopg

from ..control_plane.dsn import validate_control_dsn


class OrganizationDirectoryError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class OrganizationDirectoryRepository:
    def __init__(self, database_url: str):
        validate_control_dsn(database_url, purpose="app")
        self._database_url = database_url

    def tree(self) -> dict:
        return self._read(None, None, None, 50)

    def department(self, department_id: UUID, generation_id: UUID,
                   cursor: UUID | None = None, limit: int = 50) -> dict:
        try:
            department_id = UUID(str(department_id))
            generation_id = UUID(str(generation_id))
            cursor = UUID(str(cursor)) if cursor is not None else None
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise OrganizationDirectoryError(422, "invalid organization request") from None
        return self._read(department_id, generation_id, cursor, limit)

    def _read(self, department_id, generation_id, cursor, limit):
        try:
            with psycopg.connect(self._database_url, connect_timeout=3) as connection:
                connection.execute("set transaction read only")
                connection.execute("set local statement_timeout='3000ms'")
                result = connection.execute(
                    "select platform_control.read_organization_directory_v109(%s,%s,%s,%s)",
                    (department_id, generation_id, cursor, limit),
                ).fetchone()[0]
        except psycopg.Error:
            raise OrganizationDirectoryError(503, "organization directory unavailable") from None
        if "error" in result:
            code = {"invalid_input": 422, "generation_changed": 409,
                    "department_not_found": 404}.get(result["error"], 503)
            raise OrganizationDirectoryError(code, result["error"])
        return result
