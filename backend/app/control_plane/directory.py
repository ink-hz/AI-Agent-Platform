from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import logging
import time
from typing import Any
from uuid import UUID, uuid4

from .crypto import ProtectedProviderId, ProviderIdentityCodec
from .dingtalk import DingTalkClient, DingTalkDepartment, DingTalkMember
from .models import DirectoryFreshness
from .identity import IdentityResolver


_LOG = logging.getLogger(__name__)


class DirectoryReconciliationError(RuntimeError):
    """Safe directory reconciliation failure with a bounded reason code."""


class DirectoryPromotionIndeterminate(RuntimeError):
    """Promotion may have committed but authoritative state cannot be read."""


class MemberAccessReason(StrEnum):
    ALLOWED = "allowed"
    DIRECTORY_HARD_STALE = "directory_hard_stale"
    NOT_IN_ACTIVE_DIRECTORY = "not_in_active_directory"
    LOCALLY_INVALIDATED = "locally_invalidated"


@dataclass(frozen=True)
class DirectoryFreshnessStatus:
    freshness: DirectoryFreshness
    database_now: datetime
    last_complete_at: datetime | None
    generation_id: UUID | str | None
    last_run_result: str | None
    warning: bool
    deny_member_access: bool


@dataclass(frozen=True)
class MemberAccessSignal:
    allowed: bool
    reason: str
    freshness: DirectoryFreshness


@dataclass(frozen=True)
class StagedDepartment:
    department_key: UUID
    parent_department_key: UUID | None
    protected: ProtectedProviderId
    display_name: str


@dataclass(frozen=True)
class StagedMember:
    member_key: UUID
    corporate: ProtectedProviderId
    union: ProtectedProviderId
    display_name: str
    status: str


@dataclass(frozen=True)
class DirectoryReconciliationResult:
    generation_id: UUID
    run_id: UUID
    member_count: int
    department_count: int
    membership_count: int
    duration_seconds: float


def evaluate_directory_freshness(
    last_complete_at: datetime | None,
    now: datetime,
) -> DirectoryFreshness:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("directory time invalid")
    if last_complete_at is None:
        return DirectoryFreshness.HARD_STALE
    if last_complete_at.tzinfo is None or last_complete_at.utcoffset() is None:
        raise ValueError("directory time invalid")
    age = now - last_complete_at
    if age < timedelta(0):
        raise ValueError("directory time invalid")
    if age >= timedelta(hours=24):
        return DirectoryFreshness.HARD_STALE
    if age >= timedelta(hours=8):
        return DirectoryFreshness.WARNING
    return DirectoryFreshness.FRESH


class DirectoryFreshnessService:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def evaluate(self) -> DirectoryFreshnessStatus:
        database_now, last_complete_at, generation_id, last_run_result = (
            self._repository.read_directory_clock()
        )
        freshness = evaluate_directory_freshness(last_complete_at, database_now)
        return DirectoryFreshnessStatus(
            freshness=freshness,
            database_now=database_now,
            last_complete_at=last_complete_at,
            generation_id=generation_id,
            last_run_result=last_run_result,
            warning=freshness is not DirectoryFreshness.FRESH,
            deny_member_access=freshness is DirectoryFreshness.HARD_STALE,
        )

    def member_access_signal(self, internal_user_id: Any) -> MemberAccessSignal:
        status = self.evaluate()
        active_member, locally_invalidated = (
            self._repository.member_directory_signal(internal_user_id)
        )
        if locally_invalidated:
            reason = MemberAccessReason.LOCALLY_INVALIDATED
        elif status.deny_member_access:
            reason = MemberAccessReason.DIRECTORY_HARD_STALE
        elif not active_member:
            reason = MemberAccessReason.NOT_IN_ACTIVE_DIRECTORY
        else:
            reason = MemberAccessReason.ALLOWED
        return MemberAccessSignal(
            allowed=reason is MemberAccessReason.ALLOWED,
            reason=reason.value,
            freshness=status.freshness,
        )


def build_department_closure(
    parents: dict[int, int | None],
) -> tuple[tuple[int, int, int], ...]:
    if any(not isinstance(key, int) or isinstance(key, bool) for key in parents):
        raise DirectoryReconciliationError("department_invalid")
    rows: set[tuple[int, int, int]] = set()
    for descendant in parents:
        visited: set[int] = set()
        current = descendant
        depth = 0
        while True:
            if current in visited:
                raise DirectoryReconciliationError("department_cycle")
            visited.add(current)
            if current not in parents:
                raise DirectoryReconciliationError("department_orphan")
            rows.add((current, descendant, depth))
            parent = parents[current]
            if parent is None:
                break
            current = parent
            depth += 1
    return tuple(sorted(rows))


def normalize_member_departments(
    department_ids: Iterable[int], valid_departments: set[int]
) -> tuple[int, ...]:
    try:
        normalized = tuple(sorted(set(department_ids)))
    except TypeError:
        raise ValueError("member department invalid") from None
    if (
        not normalized
        or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value not in valid_departments
            for value in normalized
        )
    ):
        raise ValueError("member department invalid")
    return normalized


def _stable_key(protected: ProtectedProviderId) -> UUID:
    raw = bytearray(protected.lookup_hmac[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(raw))


class DirectoryReconciler:
    DEFAULT_HARD_TIMEOUT_SECONDS = 900
    DEFAULT_FETCH_CONCURRENCY = 4
    BATCH_SIZE = 250

    def __init__(
        self,
        client: DingTalkClient | Any,
        repository: Any,
        identity_codec: ProviderIdentityCodec,
        *,
        corp_id: str,
        hard_timeout_seconds: float = DEFAULT_HARD_TIMEOUT_SECONDS,
        fetch_concurrency: int = DEFAULT_FETCH_CONCURRENCY,
    ) -> None:
        if not isinstance(identity_codec, ProviderIdentityCodec):
            raise ValueError("provider identity codec required")
        if not isinstance(corp_id, str) or not corp_id.strip() or "\0" in corp_id:
            raise ValueError("DingTalk corp ID invalid")
        if hard_timeout_seconds <= 0 or hard_timeout_seconds > 900:
            raise ValueError("directory timeout invalid")
        if fetch_concurrency < 1 or fetch_concurrency > 4:
            raise ValueError("directory concurrency invalid")
        self._client = client
        self._repository = repository
        self._codec = identity_codec
        self._corp_id = corp_id
        self._hard_timeout_seconds = hard_timeout_seconds
        self._fetch_concurrency = fetch_concurrency

    async def run_full(self, run_kind: str = "scheduled") -> DirectoryReconciliationResult:
        if run_kind not in {"startup", "scheduled", "targeted", "event"}:
            raise ValueError("directory run kind invalid")
        started = time.monotonic()
        try:
            return await asyncio.wait_for(
                self._run_full(
                    run_kind, started, started + self._hard_timeout_seconds
                ),
                timeout=self._hard_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise DirectoryReconciliationError("sync_timeout") from None
        except DirectoryReconciliationError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise DirectoryReconciliationError("provider_failed") from None

    async def _run_full(
        self, run_kind: str, started: float, deadline: float
    ) -> DirectoryReconciliationResult:
        # The complete provider snapshot is collected and validated before opening
        # the first database transaction. No network await occurs while staging.
        departments = [DingTalkDepartment(1, None, "Organization")]
        async for department in self._client.iter_departments():
            departments.append(department)
        by_id: dict[int, DingTalkDepartment] = {}
        for department in departments:
            previous = by_id.get(department.department_id)
            if previous is not None and previous != department:
                raise DirectoryReconciliationError("department_conflict")
            if previous is not None:
                raise DirectoryReconciliationError("department_duplicate")
            by_id[department.department_id] = department
        parents = {
            item.department_id: item.parent_department_id for item in departments
        }
        closure_ids = build_department_closure(parents)

        semaphore = asyncio.Semaphore(self._fetch_concurrency)

        async def collect(department_id: int) -> list[DingTalkMember]:
            async with semaphore:
                return [
                    member
                    async for member in self._client.iter_department_members(
                        department_id
                    )
                ]

        pages = await asyncio.gather(*(collect(key) for key in sorted(by_id)))
        members: dict[str, DingTalkMember] = {}
        union_owners: dict[str, str] = {}
        for page in pages:
            for member in page:
                departments_for_member = normalize_member_departments(
                    member.department_ids, set(by_id)
                )
                normalized = DingTalkMember(
                    member.userid,
                    member.unionid,
                    member.display_name,
                    member.active,
                    departments_for_member,
                )
                previous = members.get(member.userid)
                if previous is not None and previous != normalized:
                    raise DirectoryReconciliationError("member_conflict")
                other_userid = union_owners.get(member.unionid)
                if other_userid is not None and other_userid != member.userid:
                    raise DirectoryReconciliationError("member_conflict")
                members[member.userid] = normalized
                union_owners[member.unionid] = member.userid

        protected_departments: dict[int, StagedDepartment] = {}
        for department_id in sorted(by_id):
            item = by_id[department_id]
            protected = self._codec.seal("department", str(department_id))
            protected_departments[department_id] = StagedDepartment(
                department_key=_stable_key(protected),
                parent_department_key=None,
                protected=protected,
                display_name=item.display_name,
            )
        protected_departments = {
            department_id: StagedDepartment(
                row.department_key,
                (
                    protected_departments[by_id[department_id].parent_department_id].department_key
                    if by_id[department_id].parent_department_id is not None
                    else None
                ),
                row.protected,
                row.display_name,
            )
            for department_id, row in protected_departments.items()
        }
        staged_members: list[StagedMember] = []
        member_keys: dict[str, UUID] = {}
        for userid in sorted(members):
            member = members[userid]
            corporate = self._codec.seal(
                "employee",
                IdentityResolver.corporate_provider_id(self._corp_id, userid),
            )
            union = self._codec.seal("employee_union", member.unionid)
            key = _stable_key(corporate)
            member_keys[userid] = key
            staged_members.append(
                StagedMember(
                    key,
                    corporate,
                    union,
                    member.display_name,
                    "active" if member.active else "inactive",
                )
            )
        memberships = tuple(
            sorted(
                (
                    member_keys[userid],
                    protected_departments[department_id].department_key,
                )
                for userid, member in members.items()
                for department_id in member.department_ids
            )
        )
        closure = tuple(
            (
                protected_departments[ancestor].department_key,
                protected_departments[descendant].department_key,
                depth,
            )
            for ancestor, descendant, depth in closure_ids
        )
        generation_id = uuid4()
        run_id = uuid4()
        try:
            self._check_deadline(deadline)
            self._repository.create_staging_generation(
                generation_id,
                run_id,
                run_kind,
                len(staged_members),
                len(protected_departments),
                len(memberships),
            )
            self._check_deadline(deadline)
            self._stage_batches(
                "stage_departments",
                generation_id,
                tuple(protected_departments[key] for key in sorted(protected_departments)),
                deadline,
            )
            self._stage_batches("stage_members", generation_id, tuple(staged_members), deadline)
            self._stage_batches("stage_memberships", generation_id, memberships, deadline)
            self._stage_batches("stage_closure", generation_id, closure, deadline)
            self._check_deadline(deadline)
            self._repository.finalize_staging_generation(generation_id)
            self._check_deadline(deadline)
            self._repository.promote_generation(generation_id)
            self._check_deadline(deadline)
        except DirectoryReconciliationError as error:
            try:
                self._repository.mark_generation_failed(generation_id, str(error))
            except Exception:
                pass
            raise
        except DirectoryPromotionIndeterminate:
            # A promotion may have committed even when both its response and the
            # authoritative follow-up read were lost. Never mark that generation
            # failed until a later worker can reconcile directory_state.
            raise DirectoryReconciliationError(
                "promotion_indeterminate"
            ) from None
        except Exception:
            try:
                self._repository.mark_generation_failed(
                    generation_id, "staging_failed"
                )
            except Exception:
                pass
            raise DirectoryReconciliationError("staging_failed") from None
        duration = time.monotonic() - started
        _LOG.info(
            "directory reconciliation completed generation_id=%s run_id=%s "
            "duration_ms=%d member_count=%d department_count=%d membership_count=%d",
            generation_id,
            run_id,
            int(duration * 1000),
            len(staged_members),
            len(protected_departments),
            len(memberships),
        )
        return DirectoryReconciliationResult(
            generation_id,
            run_id,
            len(staged_members),
            len(protected_departments),
            len(memberships),
            duration,
        )

    def _stage_batches(
        self,
        method_name: str,
        generation_id: UUID,
        rows: tuple[Any, ...],
        deadline: float,
    ) -> None:
        method = getattr(self._repository, method_name)
        for offset in range(0, len(rows), self.BATCH_SIZE):
            self._check_deadline(deadline)
            method(generation_id, rows[offset : offset + self.BATCH_SIZE])
            self._check_deadline(deadline)

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise DirectoryReconciliationError("sync_timeout")
