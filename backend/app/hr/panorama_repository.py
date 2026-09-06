from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


class PanoramaRepositoryError(RuntimeError):
    pass


class PanoramaNotFound(PanoramaRepositoryError):
    pass


class PanoramaConflict(PanoramaRepositoryError):
    pass


class PanoramaUnavailable(PanoramaRepositoryError):
    pass


def _identifier(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError("panorama identifier invalid")
    return value


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("panorama limit invalid")
    return value


def _bundle(row: Mapping[str, Any]) -> dict[str, object]:
    required = {
        "bundle_id",
        "owner_internal_user_id",
        "manifest_sha256",
        "bundle_locator",
        "schema_version",
        "generated_at",
        "company_count",
        "job_count",
        "analysis_count",
        "evidence_count",
        "manifest",
        "source_catalog",
        "source_coverage",
        "aggregates",
        "analysis",
        "analysis_usage",
        "evidence_index",
        "document_index",
        "imported_at",
    }
    if not required.issubset(row):
        raise PanoramaUnavailable("published intelligence record invalid")
    return {key: row[key] for key in required}


class PanoramaRepository:
    """Read-only repository for Owner-approved intelligence Bundles."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        connection: Callable[[], object] | None = None,
    ) -> None:
        if connection is None and (
            not isinstance(database_url, str) or not database_url.strip()
        ):
            raise ValueError("panorama database URL required")
        if connection is not None and not callable(connection):
            raise TypeError("panorama connection factory invalid")
        self._database_url = (
            database_url.strip() if isinstance(database_url, str) else None
        )
        self._connection_factory = connection

    @contextmanager
    def _connection(self):
        if self._connection_factory is not None:
            with self._connection_factory() as connection:
                yield connection
            return
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            yield connection

    @staticmethod
    def _raise(error: Exception, action: str) -> None:
        if isinstance(error, psycopg.errors.NoDataFound):
            raise PanoramaNotFound(f"panorama {action} not found") from None
        if isinstance(
            error, (psycopg.errors.SerializationFailure, psycopg.errors.UniqueViolation)
        ):
            raise PanoramaConflict(f"panorama {action} conflict") from None
        raise PanoramaUnavailable(f"panorama {action} unavailable") from error

    def current_bundle(self) -> Mapping[str, object] | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.read_current_intelligence_bundle_v85()"
                ).fetchone()
            return None if row is None else _bundle(row)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "current bundle")

    def list_bundles(self, *, limit: int = 100) -> tuple[Mapping[str, object], ...]:
        selected_limit = _limit(limit)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.list_intelligence_bundles_v85(%s)",
                    (selected_limit,),
                ).fetchall()
            return tuple(_bundle(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "bundles")

    def bundle(self, bundle_id: UUID) -> Mapping[str, object]:
        _identifier(bundle_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.read_intelligence_bundle_v85(%s)",
                    (bundle_id,),
                ).fetchone()
            if row is None:
                raise PanoramaNotFound("panorama bundle not found")
            return _bundle(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "bundle")

    def bundle_jobs(self, bundle_id: UUID) -> tuple[Mapping[str, object], ...]:
        _identifier(bundle_id)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.read_intelligence_bundle_jobs_v85(%s)",
                    (bundle_id,),
                ).fetchall()
            jobs = tuple(row.get("job") for row in rows)
            if any(not isinstance(job, Mapping) for job in jobs):
                raise ValueError("panorama jobs invalid")
            return jobs
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "jobs")

    def bundle_reference_for_turn(
        self, owner_id: UUID, position_id: UUID, turn_id: UUID
    ) -> Mapping[str, object] | None:
        for value in (owner_id, position_id, turn_id):
            _identifier(value)
        try:
            with self._connection() as connection:
                return connection.execute(
                    "select * from platform_hr."
                    "read_intelligence_bundle_reference_for_turn_v86(%s,%s,%s)",
                    (owner_id, position_id, turn_id),
                ).fetchone()
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "bundle task reference")

    def record_bundle_reference(
        self,
        *,
        reference_id: UUID,
        owner_id: UUID,
        client_request_id: UUID,
        position_id: UUID,
        turn_id: UUID,
        bundle_id: UUID,
        observed_at: object,
        context_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        for value in (
            reference_id, owner_id, client_request_id, position_id, turn_id, bundle_id
        ):
            _identifier(value)
        if not isinstance(context_document, Mapping):
            raise TypeError("panorama bundle context invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_intelligence_bundle_reference_v86("
                    "%s,%s,%s,%s,%s,%s,%s,%s::jsonb)).*",
                    (
                        reference_id, owner_id, client_request_id, position_id,
                        turn_id, bundle_id, observed_at,
                        json.dumps(context_document, ensure_ascii=False),
                    ),
                ).fetchone()
            if row is None:
                raise PanoramaUnavailable("panorama bundle task reference unavailable")
            return row
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "bundle task reference")

    def bundle_by_manifest(
        self,
        owner_id: UUID,
        manifest_sha256: str,
    ) -> Mapping[str, object] | None:
        _identifier(owner_id)
        if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64:
            raise TypeError("panorama manifest hash invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.read_intelligence_bundle_by_manifest_v85(%s,%s)",
                    (owner_id, manifest_sha256),
                ).fetchone()
            return None if row is None else _bundle(row)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "bundle manifest")

    @staticmethod
    def canonical_payload(record: Mapping[str, object]) -> str:
        return json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )


__all__ = [
    "PanoramaConflict",
    "PanoramaNotFound",
    "PanoramaRepository",
    "PanoramaRepositoryError",
    "PanoramaUnavailable",
]
