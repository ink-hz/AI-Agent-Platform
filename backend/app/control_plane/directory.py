from __future__ import annotations

import asyncio
import hashlib
import logging
import struct
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from .crypto import (
    EncryptedDirectoryAttribute,
    ProtectedProviderId,
    ProviderIdentityCodec,
)
from .dingtalk import (
    DingTalkClient,
    DingTalkDepartment,
    DingTalkDirectorySnapshotError,
    DingTalkEmployeeProfile,
    DingTalkGender,
    DingTalkMember,
    hydrate_authoritative_members,
    member_identity_snapshot,
)
from .directory_limits import (
    DIRECTORY_FETCH_CONCURRENCY,
    DIRECTORY_SOURCE_SCHEMA_VERSION,
    DIRECTORY_STAGE_BATCH_SIZE,
    MAX_CLOSURE_ROWS,
    MAX_DEPARTMENT_DEPTH,
    MAX_DEPARTMENTS,
    MAX_DEPARTMENTS_PER_MEMBER,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_MEMBERS,
    MAX_MEMBERSHIPS,
    MAX_PROVIDER_CIPHERTEXT_BYTES,
    MIN_PROVIDER_CIPHERTEXT_BYTES,
)
from .identity import IdentityResolver
from .models import DirectoryFreshness

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
    reason: MemberAccessReason
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
    gender: DingTalkGender | None = field(repr=False)
    real_name: EncryptedDirectoryAttribute | None = field(default=None, repr=False)
    mobile: EncryptedDirectoryAttribute | None = field(default=None, repr=False)
    primary_department: EncryptedDirectoryAttribute | None = field(
        default=None,
        repr=False,
    )


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
            reason=reason,
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
            if depth > MAX_DEPARTMENT_DEPTH:
                raise DirectoryReconciliationError("department_depth_bound")
        if len(rows) > MAX_CLOSURE_ROWS:
            raise DirectoryReconciliationError("department_closure_bound")
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
    if len(normalized) > MAX_DEPARTMENTS_PER_MEMBER:
        raise ValueError("member department bound")
    return normalized


def _canonical_field(value: bytes | str | int | UUID | None) -> bytes:
    if value is None:
        return struct.pack(">i", -1)
    if isinstance(value, bytes):
        encoded = value
    elif isinstance(value, UUID):
        encoded = str(value).encode("ascii")
    elif isinstance(value, int) and not isinstance(value, bool):
        encoded = str(value).encode("ascii")
    elif isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        raise ValueError("directory canonical field invalid")
    return struct.pack(">i", len(encoded)) + encoded


def _canonical_record(tag: bytes, *fields: bytes | str | int | UUID | None) -> bytes:
    return tag + b"".join(_canonical_field(field) for field in fields)


def canonical_directory_digest(
    source_schema_version: int,
    departments: tuple[StagedDepartment, ...],
    members: tuple[StagedMember, ...],
    memberships: tuple[tuple[UUID, UUID], ...],
    closure: tuple[tuple[UUID, UUID, int], ...],
) -> str:
    if source_schema_version != DIRECTORY_SOURCE_SCHEMA_VERSION:
        raise ValueError("directory source schema invalid")
    profile_counts = tuple(
        sum(
            row.status == "active" and getattr(row, field_name) is not None
            for row in members
        )
        for field_name in ("real_name", "mobile", "primary_department")
    )
    records = [
        _canonical_record(
            b"H", source_schema_version, len(members), len(departments),
            len(memberships), len(closure), *profile_counts
        )
    ]
    for row in sorted(departments, key=lambda item: item.department_key.bytes):
        records.append(_canonical_record(
            b"D", row.department_key, row.parent_department_key,
            row.display_name, row.protected.lookup_key_version,
            row.protected.lookup_hmac, row.protected.encryption_key_version,
            row.protected.ciphertext,
        ))
    for row in sorted(members, key=lambda item: item.member_key.bytes):
        records.append(_canonical_record(
            b"M", row.member_key, row.display_name, row.status, row.gender,
            row.corporate.lookup_key_version, row.corporate.lookup_hmac,
            row.corporate.encryption_key_version, row.corporate.ciphertext,
            row.union.lookup_key_version, row.union.lookup_hmac,
            row.union.encryption_key_version, row.union.ciphertext,
            row.real_name.encryption_key_version if row.real_name else None,
            row.real_name.nonce if row.real_name else None,
            row.real_name.ciphertext if row.real_name else None,
            row.mobile.encryption_key_version if row.mobile else None,
            row.mobile.nonce if row.mobile else None,
            row.mobile.ciphertext if row.mobile else None,
            (
                row.primary_department.encryption_key_version
                if row.primary_department
                else None
            ),
            row.primary_department.nonce if row.primary_department else None,
            (
                row.primary_department.ciphertext
                if row.primary_department
                else None
            ),
        ))
    for member_key, department_key in sorted(
        memberships, key=lambda item: (item[0].bytes, item[1].bytes)
    ):
        records.append(_canonical_record(b"P", member_key, department_key))
    for ancestor, descendant, depth in sorted(
        closure, key=lambda item: (item[0].bytes, item[1].bytes, item[2])
    ):
        records.append(_canonical_record(b"C", ancestor, descendant, depth))
    return hashlib.sha256(b"".join(records)).hexdigest()


def _stable_key(protected: ProtectedProviderId) -> UUID:
    raw = bytearray(protected.lookup_hmac[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(raw))


class DirectoryReconciler:
    DEFAULT_HARD_TIMEOUT_SECONDS = 900
    DEFAULT_FETCH_CONCURRENCY = DIRECTORY_FETCH_CONCURRENCY
    BATCH_SIZE = DIRECTORY_STAGE_BATCH_SIZE

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
        hard_deadline = started + self._hard_timeout_seconds
        cleanup_reserve = min(1.0, self._hard_timeout_seconds * 0.2)
        try:
            return await asyncio.wait_for(
                self._run_full(
                    run_kind, started, hard_deadline - cleanup_reserve,
                    hard_deadline,
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
        self, run_kind: str, started: float, deadline: float,
        hard_deadline: float,
    ) -> DirectoryReconciliationResult:
        # The complete provider snapshot is collected and validated before opening
        # the first database transaction. No network await occurs while staging.
        departments = [DingTalkDepartment(1, None, "Organization")]
        async for department in self._client.iter_departments():
            departments.append(department)
            if len(departments) > MAX_DEPARTMENTS:
                raise DirectoryReconciliationError("department_count_bound")
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

        members: dict[str, DingTalkMember] = {}
        union_owners: dict[str, str] = {}
        department_keys = sorted(by_id)
        for offset in range(0, len(department_keys), self._fetch_concurrency):
            chunk = department_keys[offset : offset + self._fetch_concurrency]
            async for member in self._iter_member_chunk(chunk):
                departments_for_member = normalize_member_departments(
                    member.department_ids, set(by_id)
                )
                normalized = DingTalkMember(
                    member.userid, member.unionid, member.display_name,
                    member.active, departments_for_member, member.gender,
                    member.gender_attribute_status,
                )
                previous = members.get(member.userid)
                if (
                    previous is not None
                    and member_identity_snapshot(previous)
                    != member_identity_snapshot(normalized)
                ):
                    raise DirectoryReconciliationError("member_conflict")
                other_userid = union_owners.get(member.unionid)
                if other_userid is not None and other_userid != member.userid:
                    raise DirectoryReconciliationError("member_conflict")
                members[member.userid] = normalized
                union_owners[member.unionid] = member.userid
                if len(members) > MAX_MEMBERS:
                    raise DirectoryReconciliationError("member_count_bound")

        try:
            members = await hydrate_authoritative_members(self._client, members)
        except DingTalkDirectorySnapshotError:
            raise DirectoryReconciliationError("member_conflict") from None

        profiles = await self._client.get_employee_profiles(tuple(sorted(members)))
        if (
            not isinstance(profiles, dict)
            or set(profiles) != set(members)
            or any(
                not isinstance(profile, DingTalkEmployeeProfile)
                or profile.userid != userid
                for userid, profile in profiles.items()
            )
        ):
            raise DirectoryReconciliationError("member_profile_conflict")

        generation_id = uuid4()

        protected_departments: dict[int, StagedDepartment] = {}
        for department_id in sorted(by_id):
            item = by_id[department_id]
            protected = self._codec.seal("department", str(department_id))
            self._validate_protected(protected)
            self._validate_display_name(item.display_name)
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
            profile = profiles[userid]
            corporate = self._codec.seal(
                "employee",
                IdentityResolver.corporate_provider_id(self._corp_id, userid),
            )
            union = self._codec.seal("employee_union", member.unionid)
            self._validate_protected(corporate)
            self._validate_protected(union)
            self._validate_display_name(member.display_name)
            key = _stable_key(corporate)
            member_keys[userid] = key
            staged_members.append(
                StagedMember(
                    key,
                    corporate,
                    union,
                    member.display_name,
                    "active" if member.active else "inactive",
                    member.gender,
                    self._seal_optional_attribute(
                        generation_id,
                        key,
                        "real_name",
                        profile.real_name,
                    ),
                    self._seal_optional_attribute(
                        generation_id,
                        key,
                        "mobile",
                        profile.mobile,
                    ),
                    self._seal_optional_attribute(
                        generation_id,
                        key,
                        "primary_department",
                        profile.primary_department,
                    ),
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
        if len(memberships) > MAX_MEMBERSHIPS:
            raise DirectoryReconciliationError("membership_count_bound")
        closure = tuple(
            (
                protected_departments[ancestor].department_key,
                protected_departments[descendant].department_key,
                depth,
            )
            for ancestor, descendant, depth in closure_ids
        )
        expected_digest = canonical_directory_digest(
            DIRECTORY_SOURCE_SCHEMA_VERSION,
            tuple(protected_departments[key] for key in sorted(protected_departments)),
            tuple(staged_members), memberships, closure,
        )
        profile_present_counts = tuple(
            sum(
                row.status == "active" and getattr(row, field_name) is not None
                for row in staged_members
            )
            for field_name in ("real_name", "mobile", "primary_department")
        )
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
                len(closure),
                DIRECTORY_SOURCE_SCHEMA_VERSION,
                expected_digest,
                *profile_present_counts,
                timeout_seconds=self._remaining(deadline),
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
            self._repository.finalize_staging_generation(
                generation_id, timeout_seconds=self._remaining(deadline)
            )
            self._check_deadline(deadline)
            self._repository.promote_generation(
                generation_id, timeout_seconds=self._remaining(deadline)
            )
        except DirectoryReconciliationError as error:
            self._mark_failed_with_remaining_budget(
                generation_id, str(error), hard_deadline
            )
            raise
        except DirectoryPromotionIndeterminate:
            # A promotion may have committed even when both its response and the
            # authoritative follow-up read were lost. Never mark that generation
            # failed until a later worker can reconcile directory_state.
            raise DirectoryReconciliationError(
                "promotion_indeterminate"
            ) from None
        except Exception:
            self._mark_failed_with_remaining_budget(
                generation_id, "staging_failed", hard_deadline
            )
            raise DirectoryReconciliationError("staging_failed") from None
        duration = time.monotonic() - started
        gender_status_counts = {
            "valid": 0,
            "missing": 0,
            "invalid": 0,
        }
        for member in members.values():
            if member.active:
                gender_status_counts[member.gender_attribute_status] += 1
        _LOG.info(
            "directory reconciliation completed generation_id=%s run_id=%s "
            "duration_ms=%d member_count=%d department_count=%d membership_count=%d "
            "gender_valid_count=%d gender_missing_count=%d gender_invalid_count=%d "
            "real_name_present_count=%d mobile_present_count=%d "
            "primary_department_present_count=%d",
            generation_id,
            run_id,
            int(duration * 1000),
            len(staged_members),
            len(protected_departments),
            len(memberships),
            gender_status_counts["valid"],
            gender_status_counts["missing"],
            gender_status_counts["invalid"],
            *profile_present_counts,
        )
        return DirectoryReconciliationResult(
            generation_id,
            run_id,
            len(staged_members),
            len(protected_departments),
            len(memberships),
            duration,
        )

    def _seal_optional_attribute(
        self,
        generation_id: UUID,
        member_id: UUID,
        purpose: str,
        value: str | None,
    ) -> EncryptedDirectoryAttribute | None:
        if value is None:
            return None
        protected = self._codec.seal_attribute(
            self._corp_id,
            generation_id,
            member_id,
            purpose,
            value,
        )
        if (
            len(protected.nonce) != 12
            or not 16 <= len(protected.ciphertext) <= MAX_PROVIDER_CIPHERTEXT_BYTES
        ):
            raise DirectoryReconciliationError("profile_ciphertext_bound")
        return protected

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
            method(
                generation_id, rows[offset : offset + self.BATCH_SIZE],
                timeout_seconds=self._remaining(deadline),
            )
            self._check_deadline(deadline)

    async def _iter_member_chunk(
        self, department_ids: list[int]
    ):
        """Round-robin at most four provider iterators without page accumulation."""
        sentinel = object()
        iterators = [
            self._client.iter_department_members(department_id).__aiter__()
            for department_id in department_ids
        ]
        while iterators:
            results = await asyncio.gather(*(
                anext(iterator, sentinel) for iterator in iterators
            ))
            remaining = []
            for iterator, result in zip(iterators, results, strict=True):
                if result is sentinel:
                    continue
                remaining.append(iterator)
                yield result
            iterators = remaining

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise DirectoryReconciliationError("sync_timeout")

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DirectoryReconciliationError("sync_timeout")
        return remaining

    @staticmethod
    def _validate_display_name(value: str) -> None:
        if not isinstance(value, str) or len(value) > MAX_DISPLAY_NAME_LENGTH:
            raise DirectoryReconciliationError("display_name_bound")

    @staticmethod
    def _validate_protected(value: ProtectedProviderId) -> None:
        if not (
            MIN_PROVIDER_CIPHERTEXT_BYTES
            <= len(value.ciphertext)
            <= MAX_PROVIDER_CIPHERTEXT_BYTES
        ):
            raise DirectoryReconciliationError("provider_ciphertext_bound")

    def _mark_failed_with_remaining_budget(
        self, generation_id: UUID, error_code: str, deadline: float
    ) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            self._repository.mark_generation_failed(
                generation_id, error_code, timeout_seconds=remaining
            )
        except Exception:
            pass
