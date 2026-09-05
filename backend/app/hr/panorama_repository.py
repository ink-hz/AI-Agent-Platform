# ruff: noqa: TRY004
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .panorama_models import (
    CreatePanoramaRun,
    CreatePositionInsightRetrieval,
    CreatePublicJobSnapshot,
    CreateTalentInsightVersion,
    CreateTalentSource,
    PanoramaReport,
    PanoramaRun,
    PositionInsightRetrieval,
    PublicJobSnapshot,
    TalentInsightVersion,
    TalentSource,
    TransitionPanoramaRun,
    thaw_json,
)


class PanoramaRepositoryError(RuntimeError):
    pass


class PanoramaNotFound(PanoramaRepositoryError):
    pass


class PanoramaConflict(PanoramaRepositoryError):
    pass


class PanoramaUnavailable(PanoramaRepositoryError):
    pass


def _source(row: Mapping[str, Any]) -> TalentSource:
    return TalentSource(
        source_id=row["source_id"],
        owner_id=row["owner_internal_user_id"],
        client_request_id=row["client_request_id"],
        source_kind=row["source_kind"],
        company_key=row["company_key"],
        canonical_name=row["canonical_name"],
        aliases=tuple(row["aliases"]),
        approved_urls=tuple(row["approved_public_urls"]),
        active=row["active"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run(row: Mapping[str, Any]) -> PanoramaRun:
    return PanoramaRun(
        run_id=row["run_id"],
        owner_id=row["owner_internal_user_id"],
        client_request_id=row["client_request_id"],
        selected_source_ids=tuple(row["selected_source_ids"]),
        conversation_id=row["conversation_id"],
        state=row["state"],
        error_code=row["error_code"],
        source_failures=row["source_failures"],
        row_version=row["row_version"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _runtime_source(row: Mapping[str, Any]) -> TalentSource:
    return TalentSource(
        source_id=row["source_id"],
        owner_id=row["owner_internal_user_id"],
        client_request_id=row["source_client_request_id"],
        source_kind=row["source_kind"],
        company_key=row["company_key"],
        canonical_name=row["canonical_name"],
        aliases=tuple(row["aliases"]),
        approved_urls=tuple(row["approved_public_urls"]),
        active=row["active"],
        created_at=row["source_created_at"],
        updated_at=row["source_updated_at"],
    )


def _snapshot(row: Mapping[str, Any]) -> PublicJobSnapshot:
    return PublicJobSnapshot(
        snapshot_id=row["snapshot_id"],
        owner_id=row["owner_internal_user_id"],
        origin_request_id=row["origin_client_request_id"],
        run_id=row["run_id"],
        source_id=row["source_id"],
        public_job_key=row["public_job_key"],
        title=row["title"],
        location=row["location"],
        duty_excerpt=row["duty_excerpt"],
        requirement_excerpt=row["requirement_excerpt"],
        source_url=row["source_url"],
        observed_at=row["observed_at"],
        content_sha256=row["content_sha256"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _insight(row: Mapping[str, Any]) -> TalentInsightVersion:
    return TalentInsightVersion(
        insight_version_id=row["insight_version_id"],
        owner_id=row["owner_internal_user_id"],
        client_request_id=row["client_request_id"],
        run_id=row["run_id"],
        version_number=row["version_number"],
        selected_source_ids=tuple(row["selected_source_ids"]),
        snapshot_ids=tuple(row["snapshot_ids"]),
        facts=tuple(row["facts"]),
        inferences=tuple(row["inferences"]),
        unknowns=tuple(row["unknowns"]),
        direction_clusters=row["direction_clusters"],
        summary=row["summary"],
        source_conversation_id=row["source_conversation_id"],
        source_turn_id=row["source_turn_id"],
        agent_id=row["agent_id"],
        model_version=row["model_version"],
        created_at=row["created_at"],
    )


def _retrieval(row: Mapping[str, Any]) -> PositionInsightRetrieval:
    return PositionInsightRetrieval(
        retrieval_id=row["retrieval_id"],
        owner_id=row["owner_internal_user_id"],
        client_request_id=row["client_request_id"],
        position_id=row["position_id"],
        conversation_id=row["conversation_id"],
        turn_id=row["turn_id"],
        insight_version_ids=tuple(row["insight_version_ids"]),
        query_sha256=row["query_sha256"],
        retrieved_excerpts=tuple(row["retrieved_excerpts"]),
        created_at=row["created_at"],
    )


def _limit(value: int, maximum: int = 100) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ValueError("panorama limit invalid")
    return value


def _identifier(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("panorama identifiers invalid")
    return value


class _PanoramaPublication:
    """Write a report and its terminal state on one caller-owned transaction."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def create_snapshot(self, command: CreatePublicJobSnapshot) -> PublicJobSnapshot:
        if not isinstance(command, CreatePublicJobSnapshot):
            raise ValueError("public job snapshot command required")
        row = self._connection.execute(
            "select (platform_hr.create_public_job_snapshot_v79("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)).*",
            (
                command.snapshot_id,
                command.owner_id,
                command.client_request_id,
                command.run_id,
                command.source_id,
                command.public_job_key,
                command.title,
                command.location,
                command.duty_excerpt,
                command.requirement_excerpt,
                command.source_url,
                command.observed_at,
                command.content_sha256,
                command.status,
            ),
        ).fetchone()
        if row is None:
            raise PanoramaUnavailable("public job snapshot unavailable")
        return _snapshot(row)

    def create_insight(
        self, command: CreateTalentInsightVersion
    ) -> TalentInsightVersion:
        if not isinstance(command, CreateTalentInsightVersion):
            raise ValueError("talent insight command required")
        row = self._connection.execute(
            "select (platform_hr.create_talent_insight_version_v79("
            "%s,%s,%s,%s,%s::uuid[],%s::uuid[],%s::jsonb,%s::jsonb,"
            "%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)).*",
            (
                command.insight_version_id,
                command.owner_id,
                command.client_request_id,
                command.run_id,
                list(command.selected_source_ids),
                list(command.snapshot_ids),
                json.dumps(thaw_json(command.facts), ensure_ascii=False),
                json.dumps(thaw_json(command.inferences), ensure_ascii=False),
                json.dumps(thaw_json(command.unknowns), ensure_ascii=False),
                json.dumps(
                    thaw_json(command.direction_clusters),
                    ensure_ascii=False,
                ),
                command.summary,
                command.source_conversation_id,
                command.source_turn_id,
                command.agent_id,
                command.model_version,
            ),
        ).fetchone()
        if row is None:
            raise PanoramaUnavailable("talent insight unavailable")
        return _insight(row)

    def transition_run(self, command: TransitionPanoramaRun) -> PanoramaRun:
        if not isinstance(command, TransitionPanoramaRun):
            raise ValueError("panorama run transition required")
        row = self._connection.execute(
            "select (platform_hr.transition_panorama_run_v79("
            "%s,%s,%s,%s,%s,%s,%s::jsonb)).*",
            (
                command.owner_id,
                command.run_id,
                command.client_request_id,
                command.expected_row_version,
                command.state,
                command.error_code,
                json.dumps(thaw_json(command.source_failures)),
            ),
        ).fetchone()
        if row is None:
            raise PanoramaUnavailable("panorama run unavailable")
        return _run(row)


class PanoramaRepository:
    def __init__(
        self, database_url: str, *, connect: Callable[..., Any] = psycopg.connect
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("panorama database URL required")
        if not callable(connect):
            raise ValueError("panorama connection factory invalid")
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def publish_report(self, operation: Callable[[object], Any]) -> Any:
        if not callable(operation):
            raise ValueError("panorama publication operation required")
        try:
            with self._connection() as connection:
                return operation(_PanoramaPublication(connection))
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "report publication")

    @staticmethod
    def _raise(error: Exception, action: str) -> None:
        if isinstance(error, psycopg.errors.NoDataFound):
            raise PanoramaNotFound(f"panorama {action} not found") from None
        if isinstance(
            error,
            (
                psycopg.errors.CheckViolation,
                psycopg.errors.SerializationFailure,
                psycopg.errors.UniqueViolation,
            ),
        ):
            raise PanoramaConflict(f"panorama {action} conflict") from None
        raise PanoramaUnavailable(f"panorama {action} unavailable") from None

    def create_source(self, command: CreateTalentSource) -> TalentSource:
        if not isinstance(command, CreateTalentSource):
            raise ValueError("talent source command required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_talent_source_v79("
                    "%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)).*",
                    (
                        command.source_id,
                        command.owner_id,
                        command.client_request_id,
                        command.company_key,
                        command.canonical_name,
                        json.dumps(command.aliases, ensure_ascii=False),
                        json.dumps(command.approved_urls, ensure_ascii=False),
                        command.active,
                    ),
                ).fetchone()
            if row is None:
                raise PanoramaUnavailable("panorama source unavailable")
            return _source(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "source")

    def list_sources(
        self, owner_id: UUID, *, include_inactive: bool = False, limit: int = 100
    ) -> tuple[TalentSource, ...]:
        _identifier(owner_id)
        if type(include_inactive) is not bool:
            raise ValueError("include inactive flag invalid")
        _limit(limit)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.list_talent_sources_v79(%s,%s,%s)",
                    (owner_id, include_inactive, limit),
                ).fetchall()
            return tuple(_source(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "sources")

    def list_sources_page(
        self,
        owner_id: UUID,
        *,
        include_inactive: bool = False,
        before_created_at: datetime | None = None,
        before_source_id: UUID | None = None,
        limit: int = 100,
    ) -> tuple[TalentSource, ...]:
        _identifier(owner_id)
        if type(include_inactive) is not bool:
            raise ValueError("include inactive flag invalid")
        if (before_created_at is None) != (before_source_id is None):
            raise ValueError("talent source page cursor invalid")
        if before_created_at is not None and (
            not isinstance(before_created_at, datetime)
            or before_created_at.tzinfo is None
        ):
            raise ValueError("talent source page cursor invalid")
        if before_source_id is not None:
            _identifier(before_source_id)
        _limit(limit)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.list_talent_sources_page_v79("
                    "%s,%s,%s,%s,%s)",
                    (
                        owner_id,
                        include_inactive,
                        before_created_at,
                        before_source_id,
                        limit,
                    ),
                ).fetchall()
            return tuple(_source(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "source page")

    def create_run(self, command: CreatePanoramaRun) -> PanoramaRun:
        if not isinstance(command, CreatePanoramaRun):
            raise ValueError("panorama run command required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_panorama_run_v79("
                    "%s,%s,%s,%s::uuid[],%s)).*",
                    (
                        command.run_id,
                        command.owner_id,
                        command.client_request_id,
                        list(command.selected_source_ids),
                        command.conversation_id,
                    ),
                ).fetchone()
            if row is None:
                raise PanoramaUnavailable("panorama run unavailable")
            return _run(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "run")

    def list_runs(self, owner_id: UUID, *, limit: int = 100) -> tuple[PanoramaRun, ...]:
        _identifier(owner_id)
        _limit(limit)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.list_panorama_runs_v79(%s,%s)",
                    (owner_id, limit),
                ).fetchall()
            return tuple(_run(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "runs")

    def run(self, owner_id: UUID, run_id: UUID) -> PanoramaRun:
        _identifier(owner_id)
        _identifier(run_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.read_panorama_run_v79(%s,%s)).*",
                    (owner_id, run_id),
                ).fetchone()
            if row is None:
                raise PanoramaNotFound("panorama run not found")
            return _run(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "run")

    def runtime_context(self, run_id: UUID):
        from .panorama_runtime import PanoramaRunRuntime

        _identifier(run_id)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.read_panorama_run_runtime_v79(%s)",
                    (run_id,),
                ).fetchall()
            if not rows:
                return None
            run = _run(rows[0])
            if any(_run(row) != run for row in rows) or tuple(
                row["source_ordinal"] for row in rows
            ) != tuple(range(1, len(rows) + 1)):
                raise ValueError("panorama runtime rows invalid")
            return PanoramaRunRuntime(run, tuple(_runtime_source(row) for row in rows))
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "runtime")

    def claim_next_runtime(self, *, claim_seconds: int = 5):
        from .panorama_runtime import PanoramaRunRuntime

        if (
            isinstance(claim_seconds, bool)
            or not isinstance(claim_seconds, int)
            or not 1 <= claim_seconds <= 300
        ):
            raise ValueError("panorama claim duration invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.claim_next_panorama_run_v79(%s)",
                    (claim_seconds,),
                ).fetchall()
            if not rows:
                return None
            run = _run(rows[0])
            if any(_run(row) != run for row in rows) or tuple(
                row["source_ordinal"] for row in rows
            ) != tuple(range(1, len(rows) + 1)):
                raise ValueError("panorama runtime rows invalid")
            return PanoramaRunRuntime(run, tuple(_runtime_source(row) for row in rows))
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "runtime claim")

    def transition_run(self, command: TransitionPanoramaRun) -> PanoramaRun:
        if not isinstance(command, TransitionPanoramaRun):
            raise ValueError("panorama run transition required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.transition_panorama_run_v79("
                    "%s,%s,%s,%s,%s,%s,%s::jsonb)).*",
                    (
                        command.owner_id,
                        command.run_id,
                        command.client_request_id,
                        command.expected_row_version,
                        command.state,
                        command.error_code,
                        json.dumps(thaw_json(command.source_failures)),
                    ),
                ).fetchone()
            if row is None:
                raise PanoramaUnavailable("panorama run unavailable")
            return _run(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "run transition")

    def create_snapshot(self, command: CreatePublicJobSnapshot) -> PublicJobSnapshot:
        if not isinstance(command, CreatePublicJobSnapshot):
            raise ValueError("public job snapshot command required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_public_job_snapshot_v79("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)).*",
                    (
                        command.snapshot_id,
                        command.owner_id,
                        command.client_request_id,
                        command.run_id,
                        command.source_id,
                        command.public_job_key,
                        command.title,
                        command.location,
                        command.duty_excerpt,
                        command.requirement_excerpt,
                        command.source_url,
                        command.observed_at,
                        command.content_sha256,
                        command.status,
                    ),
                ).fetchone()
            if row is None:
                raise PanoramaUnavailable("public job snapshot unavailable")
            return _snapshot(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "snapshot")

    def list_snapshots(
        self, owner_id: UUID, source_id: UUID, *, limit: int = 100
    ) -> tuple[PublicJobSnapshot, ...]:
        _identifier(owner_id)
        _identifier(source_id)
        _limit(limit)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.list_public_job_snapshots_v79(%s,%s,%s)",
                    (owner_id, source_id, limit),
                ).fetchall()
            return tuple(_snapshot(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "snapshots")

    def create_insight(
        self, command: CreateTalentInsightVersion
    ) -> TalentInsightVersion:
        if not isinstance(command, CreateTalentInsightVersion):
            raise ValueError("talent insight command required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_talent_insight_version_v79("
                    "%s,%s,%s,%s,%s::uuid[],%s::uuid[],%s::jsonb,%s::jsonb,"
                    "%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)).*",
                    (
                        command.insight_version_id,
                        command.owner_id,
                        command.client_request_id,
                        command.run_id,
                        list(command.selected_source_ids),
                        list(command.snapshot_ids),
                        json.dumps(thaw_json(command.facts), ensure_ascii=False),
                        json.dumps(thaw_json(command.inferences), ensure_ascii=False),
                        json.dumps(thaw_json(command.unknowns), ensure_ascii=False),
                        json.dumps(
                            thaw_json(command.direction_clusters),
                            ensure_ascii=False,
                        ),
                        command.summary,
                        command.source_conversation_id,
                        command.source_turn_id,
                        command.agent_id,
                        command.model_version,
                    ),
                ).fetchone()
            if row is None:
                raise PanoramaUnavailable("talent insight unavailable")
            return _insight(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "insight")

    def list_insights(
        self, owner_id: UUID, *, limit: int = 100
    ) -> tuple[TalentInsightVersion, ...]:
        _identifier(owner_id)
        _limit(limit)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.list_talent_insight_versions_v79(%s,%s)",
                    (owner_id, limit),
                ).fetchall()
            return tuple(_insight(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "insights")

    def insight(self, owner_id: UUID, insight_version_id: UUID) -> TalentInsightVersion:
        _identifier(owner_id)
        _identifier(insight_version_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.read_talent_insight_version_v79(%s,%s)).*",
                    (owner_id, insight_version_id),
                ).fetchone()
            if row is None:
                raise PanoramaNotFound("panorama report not found")
            return _insight(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "report")

    def _read_sources(
        self, owner_id: UUID, source_ids: tuple[UUID, ...]
    ) -> tuple[TalentSource, ...]:
        try:
            rows: list[Mapping[str, Any]] = []
            for selected_ids in _chunks(source_ids, 100):
                with self._connection() as connection:
                    rows.extend(
                        connection.execute(
                            "select * from platform_hr.read_talent_sources_v79("
                            "%s,%s::uuid[])",
                            (owner_id, list(selected_ids)),
                        ).fetchall()
                    )
            return tuple(_source(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "sources")

    def sources_for_run(
        self, owner_id: UUID, source_ids: tuple[UUID, ...]
    ) -> tuple[TalentSource, ...]:
        _identifier(owner_id)
        if (
            not isinstance(source_ids, tuple)
            or not 1 <= len(source_ids) <= 100
            or any(not isinstance(source_id, UUID) for source_id in source_ids)
            or len(set(source_ids)) != len(source_ids)
        ):
            raise ValueError("panorama source selection invalid")
        sources = self._read_sources(owner_id, source_ids)
        if tuple(source.source_id for source in sources) != source_ids or any(
            not source.active for source in sources
        ):
            raise PanoramaNotFound("panorama sources not found")
        return sources

    def _read_snapshots(
        self, owner_id: UUID, snapshot_ids: tuple[UUID, ...]
    ) -> tuple[PublicJobSnapshot, ...]:
        try:
            rows: list[Mapping[str, Any]] = []
            for selected_ids in _chunks(snapshot_ids, 1000):
                with self._connection() as connection:
                    rows.extend(
                        connection.execute(
                            "select * from platform_hr.read_public_job_snapshots_v79("
                            "%s,%s::uuid[])",
                            (owner_id, list(selected_ids)),
                        ).fetchall()
                    )
            return tuple(_snapshot(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "snapshots")

    def report(self, owner_id: UUID, insight_version_id: UUID) -> PanoramaReport:
        insight = self.insight(owner_id, insight_version_id)
        sources = self._read_sources(owner_id, insight.selected_source_ids)
        snapshots = self._read_snapshots(owner_id, insight.snapshot_ids)
        return PanoramaReport(insight=insight, sources=sources, snapshots=snapshots)

    def create_retrieval(
        self, command: CreatePositionInsightRetrieval
    ) -> PositionInsightRetrieval:
        if not isinstance(command, CreatePositionInsightRetrieval):
            raise ValueError("position insight retrieval command required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_position_insight_retrieval_v79("
                    "%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s::jsonb)).*",
                    (
                        command.retrieval_id,
                        command.owner_id,
                        command.client_request_id,
                        command.position_id,
                        command.conversation_id,
                        command.turn_id,
                        list(command.insight_version_ids),
                        command.query_sha256,
                        json.dumps(
                            thaw_json(command.retrieved_excerpts),
                            ensure_ascii=False,
                        ),
                    ),
                ).fetchone()
            if row is None:
                raise PanoramaUnavailable("position insight retrieval unavailable")
            return _retrieval(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "retrieval")

    def retrieval_for_turn(
        self, owner_id: UUID, position_id: UUID, turn_id: UUID
    ) -> PositionInsightRetrieval | None:
        _identifier(owner_id)
        _identifier(position_id)
        _identifier(turn_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from "
                    "platform_hr.read_position_insight_retrieval_for_turn_v79("
                    "%s,%s,%s)",
                    (owner_id, position_id, turn_id),
                ).fetchone()
            return _retrieval(row) if row is not None else None
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "turn retrieval")

    def record_retrieval_for_turn(
        self,
        *,
        retrieval_id: UUID,
        owner_id: UUID,
        client_request_id: UUID,
        position_id: UUID,
        turn_id: UUID,
        insight_version_ids: tuple[UUID, ...],
        query_sha256: str,
        retrieved_excerpts: tuple[Mapping[str, object], ...],
    ) -> PositionInsightRetrieval:
        for value in (
            retrieval_id,
            owner_id,
            client_request_id,
            position_id,
            turn_id,
        ):
            _identifier(value)
        try:
            with self._connection() as connection:
                scope = connection.execute(
                    "select turn_record.conversation_id from "
                    "platform_control.conversation_turns turn_record join "
                    "platform_control.conversations conversation on "
                    "conversation.conversation_id=turn_record.conversation_id join "
                    "platform_hr.position_conversations binding on "
                    "binding.conversation_id=conversation.conversation_id and "
                    "binding.owner_internal_user_id="
                    "conversation.owner_internal_user_id where "
                    "turn_record.turn_id=%s and "
                    "conversation.owner_internal_user_id=%s and "
                    "binding.position_id=%s",
                    (turn_id, owner_id, position_id),
                ).fetchone()
                if scope is None:
                    raise PanoramaNotFound("panorama turn not found")
                command = CreatePositionInsightRetrieval(
                    retrieval_id,
                    owner_id,
                    client_request_id,
                    position_id,
                    scope["conversation_id"],
                    turn_id,
                    insight_version_ids,
                    query_sha256,
                    retrieved_excerpts,
                )
                row = connection.execute(
                    "select (platform_hr.create_position_insight_retrieval_v79("
                    "%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s::jsonb)).*",
                    (
                        command.retrieval_id,
                        command.owner_id,
                        command.client_request_id,
                        command.position_id,
                        command.conversation_id,
                        command.turn_id,
                        list(command.insight_version_ids),
                        command.query_sha256,
                        json.dumps(
                            thaw_json(command.retrieved_excerpts),
                            ensure_ascii=False,
                        ),
                    ),
                ).fetchone()
            if row is None:
                raise PanoramaUnavailable("position insight retrieval unavailable")
            return _retrieval(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "turn retrieval record")

    def list_retrievals(
        self, owner_id: UUID, position_id: UUID, *, limit: int = 100
    ) -> tuple[PositionInsightRetrieval, ...]:
        _identifier(owner_id)
        _identifier(position_id)
        _limit(limit)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.list_position_insight_retrievals_v79("
                    "%s,%s,%s)",
                    (owner_id, position_id, limit),
                ).fetchall()
            return tuple(_retrieval(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "retrievals")

    def _position_terms(self, owner_id: UUID, position_id: UUID) -> tuple[str, ...]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select position.title,official.category "
                    "from platform_hr.positions position "
                    "left join platform_hr.official_position_versions official "
                    "on official.official_position_version_id="
                    "position.current_official_version_id "
                    "and official.owner_internal_user_id="
                    "position.owner_internal_user_id "
                    "where position.owner_internal_user_id=%s "
                    "and position.position_id=%s",
                    (owner_id, position_id),
                ).fetchone()
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "position")
        if row is None:
            raise PanoramaNotFound("panorama position not found")
        return tuple(value for value in (row["title"], row["category"]) if value)

    def _ranking_candidates(self, owner_id: UUID) -> tuple[TalentInsightVersion, ...]:
        candidates: list[TalentInsightVersion] = []
        before_version_number: int | None = None
        try:
            while True:
                with self._connection() as connection:
                    rows = connection.execute(
                        "select * from "
                        "platform_hr.list_talent_insight_versions_page_v79("
                        "%s,%s,%s)",
                        (owner_id, before_version_number, 100),
                    ).fetchall()
                page = tuple(_insight(row) for row in rows)
                candidates.extend(page)
                if len(page) < 100:
                    return tuple(candidates)
                before_version_number = page[-1].version_number
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "insights")

    def _sources_for_ranking(
        self, owner_id: UUID, source_ids: tuple[UUID, ...]
    ) -> dict[UUID, TalentSource]:
        sources: dict[UUID, TalentSource] = {}
        before_created_at = None
        before_source_id = None
        while True:
            page = self.list_sources_page(
                owner_id,
                include_inactive=False,
                before_created_at=before_created_at,
                before_source_id=before_source_id,
                limit=100,
            )
            sources.update((source.source_id, source) for source in page)
            if len(page) < 100:
                return sources
            before_created_at = page[-1].created_at
            before_source_id = page[-1].source_id

    def _evidenced_sources_for_ranking(
        self, owner_id: UUID, insights: tuple[TalentInsightVersion, ...]
    ) -> dict[UUID, frozenset[UUID]]:
        snapshots = {
            snapshot.snapshot_id: snapshot.source_id
            for snapshot in self._read_snapshots(
                owner_id,
                tuple(
                    dict.fromkeys(
                        snapshot_id
                        for insight in insights
                        for snapshot_id in insight.snapshot_ids
                    )
                ),
            )
        }
        return {
            insight.insight_version_id: frozenset(
                source_id
                for snapshot_id in insight.snapshot_ids
                if (source_id := snapshots.get(snapshot_id)) is not None
            )
            for insight in insights
        }

    def relevant_insights(
        self, owner_id: UUID, query: str, position_id: UUID, *, limit: int = 5
    ) -> tuple[TalentInsightVersion, ...]:
        _identifier(owner_id)
        _identifier(position_id)
        _limit(limit, maximum=5)
        if not isinstance(query, str) or not query.strip() or len(query) > 32768:
            raise ValueError("panorama query invalid")
        position_terms = self._position_terms(owner_id, position_id)
        ranked_candidates = self._ranking_candidates(owner_id)
        latest_by_source_scope: dict[tuple[str, ...], TalentInsightVersion] = {}
        for insight in ranked_candidates:
            source_scope = tuple(
                sorted(str(source_id) for source_id in insight.selected_source_ids)
            )
            previous = latest_by_source_scope.get(source_scope)
            if previous is None or (
                insight.created_at,
                insight.version_number,
                str(insight.insight_version_id),
            ) > (
                previous.created_at,
                previous.version_number,
                str(previous.insight_version_id),
            ):
                latest_by_source_scope[source_scope] = insight
        candidates = tuple(latest_by_source_scope.values())
        source_ids = tuple(
            dict.fromkeys(
                source_id
                for insight in candidates
                for source_id in insight.selected_source_ids
            )
        )
        sources = self._sources_for_ranking(owner_id, source_ids) if source_ids else {}
        normalized_query = _normalize(query)
        mentioned_source_ids = tuple(
            source_id
            for source_id, source in sources.items()
            if any(
                (name_key := _normalize(name)) and name_key in normalized_query
                for name in (source.canonical_name, *source.aliases)
            )
        )
        if mentioned_source_ids:
            evidenced_sources = self._evidenced_sources_for_ranking(
                owner_id, candidates
            )
            candidates = tuple(
                insight
                for insight in candidates
                if all(
                    source_id in insight.selected_source_ids
                    and source_id in evidenced_sources[insight.insight_version_id]
                    for source_id in mentioned_source_ids
                )
            )

        def rank(insight: TalentInsightVersion):
            source_names = tuple(
                name
                for source_id in insight.selected_source_ids
                if (source := sources.get(source_id)) is not None
                for name in (source.canonical_name, *source.aliases)
            )
            company_score = sum(
                bool((name_key := _normalize(name)) and name_key in normalized_query)
                for name in source_names
            )
            direction_score = sum(
                bool(
                    (key_text := _normalize(str(key))) and key_text in normalized_query
                )
                for key in insight.direction_clusters
            )
            corpus = _normalize(
                " ".join(
                    (
                        insight.summary,
                        *(str(key) for key in insight.direction_clusters),
                        *(str(fact.get("text", "")) for fact in insight.facts),
                        *(str(item.get("text", "")) for item in insight.inferences),
                    )
                )
            )
            position_score = sum(
                bool((term_key := _normalize(term)) and term_key in corpus)
                for term in position_terms
            )
            return (
                -company_score,
                -direction_score,
                -position_score,
                -insight.created_at.timestamp(),
                str(insight.insight_version_id),
            )

        return tuple(sorted(candidates, key=rank)[:limit])


def _normalize(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def _chunks(values: tuple[UUID, ...], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


__all__ = [
    "PanoramaConflict",
    "PanoramaNotFound",
    "PanoramaRepository",
    "PanoramaRepositoryError",
    "PanoramaUnavailable",
]
