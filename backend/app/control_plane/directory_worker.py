from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime
import random
import logging
from typing import Any, Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .directory import (
    DirectoryPromotionIndeterminate,
    StagedDepartment,
    StagedMember,
)
from .dsn import validate_control_dsn


_LOG = logging.getLogger(__name__)


class DirectoryRepositoryError(RuntimeError):
    """Stable worker database failure without provider or connection material."""


class DirectoryWorkerRepository:
    def __init__(self, database_url: str, *, connect=psycopg.connect) -> None:
        validate_control_dsn(database_url, purpose="directory")
        self._database_url = database_url
        self._connect = connect

    def __repr__(self) -> str:
        return "DirectoryWorkerRepository(database_url=<redacted>)"

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=20000 -c lock_timeout=5000",
            row_factory=dict_row,
        )

    @contextmanager
    def worker_lease(self) -> Iterator[bool]:
        connection = self._connection()
        acquired = False
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select platform_control.try_directory_worker_lease() as acquired"
                )
                acquired = bool(cursor.fetchone()["acquired"])
                connection.commit()
            yield acquired
        except psycopg.Error:
            raise DirectoryRepositoryError("directory repository unavailable") from None
        finally:
            if acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "select platform_control.release_directory_worker_lease()"
                        )
                        connection.commit()
                except psycopg.Error:
                    pass
            connection.close()

    def _call(self, query: str, parameters: tuple[Any, ...]) -> Any:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(query, parameters)
                row = cursor.fetchone() if cursor.description else None
            return row
        except psycopg.Error:
            raise DirectoryRepositoryError("directory repository unavailable") from None

    def create_staging_generation(
        self,
        generation_id: UUID,
        run_id: UUID,
        run_kind: str,
        member_count: int,
        department_count: int,
        membership_count: int,
    ) -> None:
        self._call(
            "select platform_control.create_directory_staging_generation("
            "%s,%s,%s,%s,%s,%s)",
            (
                generation_id,
                run_id,
                run_kind,
                member_count,
                department_count,
                membership_count,
            ),
        )

    def stage_departments(
        self, generation_id: UUID, rows: tuple[StagedDepartment, ...]
    ) -> None:
        self._batch(
            "select platform_control.stage_directory_department("
            "%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                (
                    generation_id,
                    row.department_key,
                    row.parent_department_key,
                    row.protected.lookup_hmac,
                    row.protected.lookup_key_version,
                    row.protected.ciphertext,
                    row.protected.encryption_key_version,
                    row.display_name,
                )
                for row in rows
            ),
        )

    def stage_members(
        self, generation_id: UUID, rows: tuple[StagedMember, ...]
    ) -> None:
        self._batch(
            "select platform_control.stage_directory_member_v19("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                (
                    generation_id,
                    row.member_key,
                    row.corporate.lookup_hmac,
                    row.corporate.lookup_key_version,
                    row.corporate.ciphertext,
                    row.corporate.encryption_key_version,
                    row.union.lookup_hmac,
                    row.union.lookup_key_version,
                    row.union.ciphertext,
                    row.union.encryption_key_version,
                    row.display_name,
                    row.status,
                )
                for row in rows
            ),
        )

    def stage_memberships(
        self, generation_id: UUID, rows: tuple[tuple[UUID, UUID], ...]
    ) -> None:
        self._batch(
            "select platform_control.stage_directory_membership(%s,%s,%s)",
            ((generation_id, member, department) for member, department in rows),
        )

    def stage_closure(
        self,
        generation_id: UUID,
        rows: tuple[tuple[UUID, UUID, int], ...],
    ) -> None:
        self._batch(
            "select platform_control.stage_department_closure(%s,%s,%s,%s)",
            (
                (generation_id, ancestor, descendant, depth)
                for ancestor, descendant, depth in rows
            ),
        )

    def _batch(self, query: str, parameters) -> None:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                for values in parameters:
                    cursor.execute(query, values)
        except psycopg.Error:
            raise DirectoryRepositoryError("directory repository unavailable") from None

    def finalize_staging_generation(self, generation_id: UUID) -> str:
        row = self._call(
            "select platform_control.finalize_directory_staging_generation(%s) "
            "as checksum",
            (generation_id,),
        )
        return str(row["checksum"])

    def promote_generation(self, generation_id: UUID) -> None:
        try:
            self._call(
                "select platform_control.promote_verified_directory_generation(%s)",
                (generation_id,),
            )
            return
        except DirectoryRepositoryError:
            # A lost response after COMMIT is reconciled by authoritative state.
            try:
                with self._connection() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "select active_generation_id from "
                        "platform_control.directory_state where singleton"
                    )
                    row = cursor.fetchone()
                if row is not None and row["active_generation_id"] == generation_id:
                    return
            except psycopg.Error:
                raise DirectoryPromotionIndeterminate(
                    "directory promotion indeterminate"
                ) from None
            raise

    def mark_generation_failed(self, generation_id: UUID, error_code: str) -> None:
        self._call(
            "select platform_control.fail_directory_staging_generation(%s,%s)",
            (generation_id, error_code),
        )

    def read_directory_clock(self):
        row = self._call(
            "select clock_timestamp() as database_now,state.last_complete_at,"
            "state.active_generation_id,(select status from platform_control.sync_runs "
            "order by started_at desc,sync_run_id desc limit 1) as last_run_result "
            "from platform_control.directory_state state where state.singleton",
            (),
        )
        return (
            row["database_now"],
            row["last_complete_at"],
            row["active_generation_id"],
            row["last_run_result"],
        )

    def member_directory_signal(self, internal_user_id: UUID):
        row = self._call(
            "select exists(select 1 from platform_control.directory_state state "
            "join platform_control.directory_members member "
            "on member.generation_id=state.active_generation_id "
            "where state.singleton and member.internal_user_id=%s "
            "and member.status='active') as active_member,"
            "exists(select 1 from platform_control.internal_users users "
            "where users.internal_user_id=%s and "
            "(users.status<>'active' or users.locally_invalidated_at is not null)) "
            "as locally_invalidated",
            (internal_user_id, internal_user_id),
        )
        return bool(row["active_member"]), bool(row["locally_invalidated"])


class DirectoryWorker:
    """Single-flight startup and six-hour scheduler with bounded jitter."""

    def __init__(
        self,
        reconciler: Any,
        repository: DirectoryWorkerRepository,
        *,
        interval_seconds: int = 21_600,
        jitter_seconds: int = 300,
        sleep=asyncio.sleep,
        random_source=random.SystemRandom(),
    ) -> None:
        if interval_seconds != 21_600 or not 0 <= jitter_seconds <= 900:
            raise ValueError("directory schedule invalid")
        self._reconciler = reconciler
        self._repository = repository
        self._interval = interval_seconds
        self._jitter = jitter_seconds
        self._sleep = sleep
        self._random = random_source
        self._local_lock = asyncio.Lock()

    async def run_once(self, run_kind: str) -> Any | None:
        async with self._local_lock:
            with self._repository.worker_lease() as acquired:
                if not acquired:
                    return None
                return await self._reconciler.run_full(run_kind)

    async def serve(self) -> None:
        await self._attempt("startup")
        while True:
            delay = self._interval + self._random.uniform(
                -self._jitter, self._jitter
            )
            await self._sleep(delay)
            await self._attempt("scheduled")

    async def _attempt(self, run_kind: str) -> None:
        try:
            await self.run_once(run_kind)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.error("directory reconciliation failed error_code=sync_failed")
