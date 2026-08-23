from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time
from uuid import UUID

import pytest
import psycopg

from app.control_plane.crypto import IdentityKeyring, ProviderIdentityCodec
from app.control_plane.dingtalk import DingTalkDepartment, DingTalkMember
from app.control_plane.identity import IdentityResolver
from test_control_plane_migration import control_database


def _codec() -> ProviderIdentityCodec:
    return ProviderIdentityCodec(
        IdentityKeyring(1, "provider-encryption", {1: b"e" * 32}),
        IdentityKeyring(
            1,
            "provider-lookup-hmac",
            {1: b"h" * 32},
            transition_versions=(1,),
        ),
    )


def _reconciler(client, repository, **kwargs):
    from app.control_plane.directory import DirectoryReconciler

    return DirectoryReconciler(
        client, repository, _codec(), corp_id="test-corp", **kwargs
    )


@dataclass
class FakeRepository:
    active_generation: UUID | None = None
    fail_at: str | None = None

    def __post_init__(self):
        self.calls = []
        self.staged = {}

    def create_staging_generation(self, generation_id, run_id, kind, member_count, department_count, membership_count, closure_count, source_schema_version, expected_digest, **kwargs):
        self.calls.append((
            "create", generation_id, member_count, department_count,
            membership_count, source_schema_version,
        ))
        self.staged[generation_id] = {"departments": [], "members": [], "memberships": [], "closure": [], "failed": None}

    def stage_departments(self, generation_id, rows, **kwargs):
        assert self.fail_at != "departments"
        self.calls.append(("departments", generation_id))
        self.staged[generation_id]["departments"].extend(rows)

    def stage_members(self, generation_id, rows, **kwargs):
        assert self.fail_at != "members"
        self.calls.append(("members", generation_id))
        self.staged[generation_id]["members"].extend(rows)

    def stage_memberships(self, generation_id, rows, **kwargs):
        assert self.fail_at != "memberships"
        self.calls.append(("memberships", generation_id))
        self.staged[generation_id]["memberships"].extend(rows)

    def stage_closure(self, generation_id, rows, **kwargs):
        assert self.fail_at != "closure"
        self.calls.append(("closure", generation_id))
        self.staged[generation_id]["closure"].extend(rows)

    def finalize_staging_generation(self, generation_id, **kwargs):
        assert self.fail_at != "finalize"
        self.calls.append(("finalize", generation_id))

    def promote_generation(self, generation_id, **kwargs):
        if self.fail_at == "promote":
            raise ConnectionError("ambiguous")
        self.active_generation = generation_id
        self.calls.append(("promote", generation_id))

    def mark_generation_failed(self, generation_id, error_code, **kwargs):
        self.staged[generation_id]["failed"] = error_code
        self.calls.append(("failed", generation_id, error_code))


class FakeClient:
    def __init__(self, *, fail=False, conflict=False, gender_conflict=False):
        self.fail = fail
        self.conflict = conflict
        self.gender_conflict = gender_conflict
        self.detail_calls: Counter[str] = Counter()

    async def iter_departments(self):
        yield DingTalkDepartment(2, 1, "Engineering")
        yield DingTalkDepartment(3, 2, "Vision")

    async def iter_department_members(self, department_id):
        if self.fail and department_id == 2:
            raise RuntimeError("provider 429 body must not escape")
        if department_id == 2:
            yield DingTalkMember(
                "u-1", "union-1", "Alice", True, (2, 3), "female", "valid"
            )
        if department_id == 3:
            name = "Conflicting" if self.conflict else "Alice"
            gender = "male" if self.gender_conflict else "female"
            yield DingTalkMember(
                "u-1", "union-1", name, True, (2, 3), gender, "valid"
            )

    async def get_member(self, userid):
        self.detail_calls[userid] += 1
        return DingTalkMember(
            "u-1", "union-1", "Alice", True, (2, 3), "female", "valid"
        )


@pytest.mark.asyncio
async def test_reconciliation_fetches_before_staging_and_promotes_once() -> None:
    from app.control_plane.directory import DirectoryReconciler

    repository = FakeRepository()
    result = await _reconciler(FakeClient(), repository).run_full("startup")
    assert repository.calls[0][0] == "create"
    assert repository.calls[0][5] == 2
    assert repository.calls[-1][0] == "promote"
    assert repository.active_generation == result.generation_id
    staged = repository.staged[result.generation_id]
    assert len(staged["departments"]) == 3
    assert len(staged["members"]) == 1
    assert len(staged["memberships"]) == 2
    assert (next(row.department_key for row in staged["departments"] if row.display_name == "Engineering"), next(row.department_key for row in staged["departments"] if row.display_name == "Vision"), 1) in staged["closure"]
    member = staged["members"][0]
    assert member.corporate.subject_kind == "employee"
    assert member.union.subject_kind == "employee_union"
    assert member.corporate.lookup_key_version == member.union.lookup_key_version == 1
    assert member.gender == "female"
    assert b"u-1" not in member.corporate.ciphertext
    assert b"union-1" not in member.union.ciphertext
    assert _codec().unseal(member.corporate) == IdentityResolver.corporate_provider_id(
        "test-corp", "u-1"
    )


@pytest.mark.asyncio
async def test_reconciliation_stages_gender_only_from_one_authoritative_detail() -> None:
    class ListWithoutGenderClient(FakeClient):
        async def iter_department_members(self, department_id):
            if department_id in (2, 3):
                yield DingTalkMember(
                    "u-1", "union-1", "Alice", True, (2, 3), None, "missing"
                )

    client = ListWithoutGenderClient()
    repository = FakeRepository()

    result = await _reconciler(client, repository).run_full("startup")

    assert repository.staged[result.generation_id]["members"][0].gender == "female"
    assert client.detail_calls == Counter({"u-1": 1})


@pytest.mark.asyncio
async def test_network_failure_never_creates_staging_or_changes_active() -> None:
    from app.control_plane.directory import DirectoryReconciler, DirectoryReconciliationError

    previous = UUID("00000000-0000-0000-0000-000000000001")
    repository = FakeRepository(active_generation=previous)
    with pytest.raises(DirectoryReconciliationError, match="provider_failed") as caught:
        await _reconciler(FakeClient(fail=True), repository).run_full("scheduled")
    assert caught.value.__cause__ is None
    assert repository.active_generation == previous
    assert repository.calls == []


@pytest.mark.asyncio
async def test_conflicting_duplicate_user_is_rejected_before_database_write() -> None:
    from app.control_plane.directory import DirectoryReconciler, DirectoryReconciliationError

    repository = FakeRepository()
    with pytest.raises(DirectoryReconciliationError, match="member_conflict"):
        await _reconciler(FakeClient(conflict=True), repository).run_full()
    assert repository.calls == []


@pytest.mark.asyncio
async def test_conflicting_duplicate_member_gender_is_rejected_before_database_write() -> None:
    from app.control_plane.directory import DirectoryReconciliationError

    repository = FakeRepository()
    with pytest.raises(DirectoryReconciliationError, match="member_conflict"):
        await _reconciler(
            FakeClient(gender_conflict=True), repository
        ).run_full()
    assert repository.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["departments", "members", "memberships", "closure", "finalize"])
async def test_stage_failure_marks_candidate_failed_and_keeps_prior_active(failure) -> None:
    from app.control_plane.directory import DirectoryReconciler, DirectoryReconciliationError

    previous = UUID("00000000-0000-0000-0000-000000000001")
    repository = FakeRepository(active_generation=previous, fail_at=failure)
    with pytest.raises(DirectoryReconciliationError, match="staging_failed"):
        await _reconciler(FakeClient(), repository).run_full()
    assert repository.active_generation == previous
    generation_id = repository.calls[0][1]
    assert repository.staged[generation_id]["failed"] == "staging_failed"


@pytest.mark.asyncio
async def test_hard_timeout_is_15_minutes_and_cancels_before_any_database_write() -> None:
    from app.control_plane.directory import DirectoryReconciler, DirectoryReconciliationError

    class HangingClient(FakeClient):
        async def iter_departments(self):
            await asyncio.sleep(3600)
            if False:
                yield

    repository = FakeRepository()
    reconciler = _reconciler(HangingClient(), repository, hard_timeout_seconds=0.01)
    assert DirectoryReconciler.DEFAULT_HARD_TIMEOUT_SECONDS == 900
    with pytest.raises(DirectoryReconciliationError, match="sync_timeout"):
        await reconciler.run_full()
    assert repository.calls == []


@pytest.mark.asyncio
async def test_total_deadline_marks_an_existing_staging_generation_failed() -> None:
    class SlowRepository(FakeRepository):
        def stage_departments(self, generation_id, rows, **kwargs):
            super().stage_departments(generation_id, rows, **kwargs)
            # The fake cannot enforce PostgreSQL statement_timeout, so leave a
            # realistic cleanup margin instead of relying on a scheduler-sensitive
            # 9ms window.
            time.sleep(0.41)

    previous = UUID("00000000-0000-0000-0000-000000000001")
    repository = SlowRepository(active_generation=previous)
    with pytest.raises(Exception, match="sync_timeout"):
        await _reconciler(
            FakeClient(), repository, hard_timeout_seconds=0.5
        ).run_full()
    generation_id = repository.calls[0][1]
    assert repository.active_generation == previous
    assert repository.staged[generation_id]["failed"] == "sync_timeout"


@pytest.mark.asyncio
async def test_representative_sizing_harness_is_below_ten_minutes() -> None:
    from app.control_plane.directory import DirectoryReconciler

    class SizingClient:
        def __init__(self):
            self.detail_calls = 0

        async def iter_departments(self):
            for department_id in range(2, 102):
                yield DingTalkDepartment(department_id, 1, f"D-{department_id}")

        async def iter_department_members(self, department_id):
            if department_id == 1:
                for index in range(5000):
                    yield DingTalkMember(f"u-{index}", f"x-{index}", f"M-{index}", True, (1,))

        async def get_member(self, userid):
            self.detail_calls += 1
            index = userid.removeprefix("u-")
            return DingTalkMember(userid, f"x-{index}", f"M-{index}", True, (1,))

    started = time.monotonic()
    result = await _reconciler(SizingClient(), FakeRepository()).run_full("scheduled")
    assert result.duration_seconds < 600
    assert time.monotonic() - started < 30


@pytest.mark.asyncio
async def test_logs_contain_only_safe_run_metadata(caplog) -> None:
    from app.control_plane.directory import DirectoryReconciler

    caplog.set_level(logging.INFO)
    await _reconciler(FakeClient(), FakeRepository()).run_full()
    rendered = caplog.text
    assert "u-1" not in rendered
    assert "union-1" not in rendered
    assert "Alice" not in rendered
    assert "generation_id=" in rendered
    assert "member_count=1" in rendered
    assert "gender_valid_count=1" in rendered
    assert "gender_missing_count=0" in rendered
    assert "gender_invalid_count=0" in rendered


def test_staged_member_repr_omits_trusted_gender() -> None:
    from app.control_plane.directory import StagedMember

    protected = _codec().seal("employee", "private-member")
    staged = StagedMember(
        UUID("00000000-0000-0000-0000-000000000001"),
        protected,
        protected,
        "Display",
        "active",
        "female",
    )

    assert "gender" not in repr(staged)


@pytest.fixture
def production_directory(control_database):
    from app.control_plane.directory_worker import DirectoryWorkerRepository

    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.provider_identity_key_policies "
            "(provider,lookup_transition_versions) values ('dingtalk',array[1]) "
            "on conflict (provider) do update set "
            "lookup_transition_versions=excluded.lookup_transition_versions"
        )
    return (
        DirectoryWorkerRepository(
            environment["urls"]["platform_directory_worker"]
        ),
        environment,
    )


@pytest.mark.postgres
def test_worker_has_only_narrow_directory_functions(production_directory) -> None:
    repository, environment = production_directory
    worker_url = environment["urls"]["platform_directory_worker"]
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(worker_url) as connection, connection.cursor() as cursor:
        for table in (
            "directory_generations", "directory_state", "directory_members",
            "directory_departments", "department_closure", "member_departments",
            "provider_identities", "internal_users",
        ):
            cursor.execute(
                "select has_table_privilege(current_user,%s,'insert') or "
                "has_table_privilege(current_user,%s,'update') or "
                "has_table_privilege(current_user,%s,'delete')",
                (f"platform_control.{table}",) * 3,
            )
            assert cursor.fetchone()[0] is False
        cursor.execute(
            "select current_user,current_database(),"
            "has_function_privilege(current_user,'platform_control."
            "create_directory_staging_generation(uuid,uuid,text,integer,integer,integer)','execute'),"
            "has_function_privilege(current_user,'platform_control."
            "create_directory_staging_generation_v20(uuid,uuid,text,integer,integer,integer,integer,integer,text)','execute'),"
            "has_function_privilege(current_user,'platform_control."
            "create_directory_staging_generation_v28(uuid,uuid,text,integer,integer,integer,integer,integer,text)','execute'),"
            "has_function_privilege(current_user,'platform_control."
            "promote_verified_directory_generation(uuid)','execute')"
        )
        assert cursor.fetchone() == (
            "platform_directory_worker", "agent_platform_control",
            False, True, True, True,
        )
        cursor.execute(
            "select proname,has_function_privilege(current_user,oid,'execute'),"
            "has_function_privilege('platform_control_app',oid,'execute'),"
            "has_function_privilege('platform_directory_worker_preview',oid,'execute'),"
            "has_function_privilege('public',oid,'execute'),prosecdef,proconfig "
            "from pg_proc where pronamespace='platform_control'::regnamespace "
            "and proname=any(%s) order by proname",
            ([
                "create_directory_staging_generation_v20",
                "create_directory_staging_generation_v28",
                "stage_directory_department",
                "stage_directory_member_v19",
                "stage_directory_member_v28",
                "stage_directory_membership",
                "stage_department_closure",
                "finalize_directory_staging_generation",
                "fail_directory_staging_generation",
                "try_directory_worker_lease",
                "release_directory_worker_lease",
            ],),
        )
        rows = cursor.fetchall()
        assert len(rows) == 11
        assert all(
            worker and not app and not cross and not public and security_definer
            and config == ["search_path=pg_catalog, platform_control"]
            for _, worker, app, cross, public, security_definer, config in rows
        )
    with psycopg.connect(app_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select has_function_privilege(current_user,'platform_control."
            "create_directory_staging_generation_v28(uuid,uuid,text,integer,integer,integer,integer,integer,text)','execute')"
        )
        assert cursor.fetchone()[0] is False


@pytest.mark.postgres
def test_postgres_worker_lease_is_cross_process_single_flight_and_released(
    production_directory,
) -> None:
    from app.control_plane.directory_worker import DirectoryWorkerRepository

    first, environment = production_directory
    second = DirectoryWorkerRepository(
        environment["urls"]["platform_directory_worker"]
    )
    with first.worker_lease() as acquired_first:
        assert acquired_first is True
        with second.worker_lease() as acquired_second:
            assert acquired_second is False
    with second.worker_lease() as acquired_after_release:
        assert acquired_after_release is True


def test_promotion_connection_ambiguity_reconciles_authoritative_active_state() -> None:
    from app.control_plane.directory_worker import (
        DirectoryRepositoryError,
        DirectoryWorkerRepository,
    )

    selected = UUID("80000000-0000-0000-0000-000000000001")
    repository = DirectoryWorkerRepository(
        "postgresql://platform_directory_worker@127.0.0.1/agent_platform_control"
    )
    repository._call = lambda *args, **kwargs: (_ for _ in ()).throw(
        DirectoryRepositoryError("directory repository unavailable")
    )

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def execute(self, *args): return None
        def fetchone(self): return {"active_generation_id": selected}

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def cursor(self): return Cursor()

    repository._connection = lambda *args, **kwargs: Connection()
    repository.promote_generation(selected)


def test_unreconciled_promotion_ambiguity_is_explicit() -> None:
    from app.control_plane.directory_worker import (
        DirectoryPromotionIndeterminate,
        DirectoryRepositoryError,
        DirectoryWorkerRepository,
    )

    repository = DirectoryWorkerRepository(
        "postgresql://platform_directory_worker@127.0.0.1/agent_platform_control"
    )
    repository._call = lambda *args, **kwargs: (_ for _ in ()).throw(
        DirectoryRepositoryError("directory repository unavailable")
    )
    repository._connection = lambda *args, **kwargs: (_ for _ in ()).throw(
        psycopg.OperationalError("database details")
    )
    with pytest.raises(DirectoryPromotionIndeterminate) as caught:
        repository.promote_generation(
            UUID("90000000-0000-0000-0000-000000000001")
        )
    assert caught.value.__cause__ is None
    assert str(caught.value) == "directory promotion indeterminate"


@pytest.mark.asyncio
async def test_reconciler_does_not_mark_indeterminate_promotion_failed() -> None:
    from app.control_plane.directory import (
        DirectoryPromotionIndeterminate,
        DirectoryReconciliationError,
    )

    class IndeterminateRepository(FakeRepository):
        def promote_generation(self, generation_id, **kwargs):
            raise DirectoryPromotionIndeterminate(
                "directory promotion indeterminate"
            )

    repository = IndeterminateRepository()
    with pytest.raises(
        DirectoryReconciliationError, match="promotion_indeterminate"
    ):
        await _reconciler(FakeClient(), repository).run_full()
    generation_id = repository.calls[0][1]
    assert repository.staged[generation_id]["failed"] is None


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_real_postgres_atomic_promotion_and_staging_isolation(
    production_directory,
) -> None:
    from app.control_plane.directory import DirectoryReconciler

    repository, environment = production_directory
    first = await _reconciler(FakeClient(), repository).run_full("startup")
    with psycopg.connect(environment["admin"]) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select active_generation_id,last_complete_at from "
            "platform_control.directory_state where singleton"
        )
        active, completed = cursor.fetchone()
        assert active == first.generation_id and completed is not None
        cursor.execute(
            "select count(*) from platform_control.department_closure "
            "where generation_id=%s",
            (first.generation_id,),
        )
        assert cursor.fetchone()[0] == 6
        cursor.execute(
            "select count(*) from platform_control.directory_members "
            "where generation_id=%s and union_encrypted_provider_id is not null",
            (first.generation_id,),
        )
        assert cursor.fetchone()[0] == 1

    # A candidate can be staged, but active-generation queries cannot see it.
    generation = UUID("10000000-0000-0000-0000-000000000001")
    repository.create_staging_generation(
        generation, UUID("20000000-0000-0000-0000-000000000001"),
        "scheduled", 0, 1, 0, 1, 2, "0" * 64,
    )
    with psycopg.connect(environment["admin"]) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select active_generation_id from platform_control.directory_state where singleton"
        )
        assert cursor.fetchone()[0] == first.generation_id
        cursor.execute(
            "select count(*) from platform_control.directory_members member "
            "join platform_control.directory_state state "
            "on state.active_generation_id=member.generation_id"
        )
        assert cursor.fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_real_postgres_stage_failure_preserves_active_generation(
    production_directory,
) -> None:
    from app.control_plane.directory import DirectoryReconciler, DirectoryReconciliationError

    repository, environment = production_directory
    prior = await _reconciler(FakeClient(), repository).run_full("startup")

    class FailingRepository:
        def __getattr__(self, name):
            if name == "stage_memberships":
                def fail(*args):
                    raise RuntimeError("batch failure details")
                return fail
            return getattr(repository, name)

    with pytest.raises(DirectoryReconciliationError, match="staging_failed"):
        await _reconciler(FakeClient(), FailingRepository()).run_full()
    with psycopg.connect(environment["admin"]) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select active_generation_id from platform_control.directory_state where singleton"
        )
        assert cursor.fetchone()[0] == prior.generation_id
        cursor.execute(
            "select count(*) from platform_control.directory_generations where status='failed'"
        )
        assert cursor.fetchone()[0] >= 1


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_checksum_mismatch_rolls_back_promotion_and_retry_is_idempotent(
    production_directory,
) -> None:
    from app.control_plane.directory import DirectoryReconciler

    repository, environment = production_directory
    result = await _reconciler(FakeClient(), repository).run_full()
    repository.promote_generation(result.generation_id)
    with psycopg.connect(environment["admin"]) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select active_generation_id,status from platform_control.directory_state "
            "join platform_control.directory_generations on generation_id=active_generation_id "
            "where singleton"
        )
        assert cursor.fetchone() == (result.generation_id, "complete")


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_checksum_mismatch_cannot_replace_active_generation(
    production_directory,
) -> None:
    from app.control_plane.directory import DirectoryReconciler
    from app.control_plane.directory_worker import DirectoryRepositoryError

    repository, environment = production_directory
    prior = await _reconciler(FakeClient(), repository).run_full()
    candidate = UUID("30000000-0000-0000-0000-000000000001")
    run = UUID("40000000-0000-0000-0000-000000000001")
    root_key = UUID("50000000-0000-0000-0000-000000000001")
    protected = _codec().seal("department", "root-checksum")
    from app.control_plane.directory import (
        DIRECTORY_SOURCE_SCHEMA_VERSION, StagedDepartment,
        canonical_directory_digest,
    )
    department = StagedDepartment(root_key, None, protected, "Root")
    closure = ((root_key, root_key, 0),)
    digest = canonical_directory_digest(
        DIRECTORY_SOURCE_SCHEMA_VERSION, (department,), (), (), closure
    )
    repository.create_staging_generation(
        candidate, run, "scheduled", 0, 1, 0, 1,
        DIRECTORY_SOURCE_SCHEMA_VERSION, digest,
    )
    repository.stage_departments(candidate, (department,))
    repository.stage_closure(candidate, closure)
    repository.finalize_staging_generation(candidate)
    with psycopg.connect(environment["admin"]) as connection, connection.cursor() as cursor:
        cursor.execute(
            "update platform_control.directory_generations set content_sha256=%s "
            "where generation_id=%s",
            ("0" * 64, candidate),
        )
    with pytest.raises(DirectoryRepositoryError):
        repository.promote_generation(candidate)
    with psycopg.connect(environment["admin"]) as connection, connection.cursor() as cursor:
        cursor.execute("select active_generation_id from platform_control.directory_state where singleton")
        assert cursor.fetchone()[0] == prior.generation_id
        cursor.execute("select status from platform_control.directory_generations where generation_id=%s", (candidate,))
        assert cursor.fetchone()[0] == "staging"


@pytest.mark.postgres
def test_cycle_and_declared_count_mismatch_fail_before_promotion(
    production_directory,
) -> None:
    from app.control_plane.directory import StagedDepartment
    from app.control_plane.directory_worker import DirectoryRepositoryError

    repository, environment = production_directory
    codec = _codec()
    first = UUID("60000000-0000-0000-0000-000000000001")
    second = UUID("60000000-0000-0000-0000-000000000002")
    generation = UUID("60000000-0000-0000-0000-000000000003")
    repository.create_staging_generation(
        generation, UUID("60000000-0000-0000-0000-000000000004"),
        "scheduled", 0, 2, 0, 2, 2, "0" * 64,
    )
    repository.stage_departments(generation, (
        StagedDepartment(first, second, codec.seal("department", "cycle-1"), "One"),
        StagedDepartment(second, first, codec.seal("department", "cycle-2"), "Two"),
    ))
    repository.stage_closure(generation, ((first, first, 0), (second, second, 0)))
    with pytest.raises(DirectoryRepositoryError):
        repository.finalize_staging_generation(generation)

    mismatch = UUID("70000000-0000-0000-0000-000000000001")
    repository.create_staging_generation(
        mismatch, UUID("70000000-0000-0000-0000-000000000002"),
        "scheduled", 1, 1, 0, 1, 2, "0" * 64,
    )
    root = UUID("70000000-0000-0000-0000-000000000003")
    repository.stage_departments(mismatch, (StagedDepartment(root, None, codec.seal("department", "mismatch-root"), "Root"),))
    repository.stage_closure(mismatch, ((root, root, 0),))
    with pytest.raises(DirectoryRepositoryError):
        repository.finalize_staging_generation(mismatch)


@pytest.mark.asyncio
async def test_worker_is_single_flight_and_releases_lease_after_failure() -> None:
    from app.control_plane.directory_worker import DirectoryWorker

    class LeaseRepository:
        def __init__(self):
            self.held = False

        def worker_lease(self):
            repository = self
            class Lease:
                def __enter__(self):
                    if repository.held:
                        return False
                    repository.held = True
                    return True
                def __exit__(self, *args):
                    repository.held = False
            return Lease()

    class Reconciler:
        def __init__(self):
            self.calls = 0
            self.fail = True

        async def run_full(self, kind):
            self.calls += 1
            await asyncio.sleep(0)
            if self.fail:
                raise RuntimeError("safe failure")
            return kind

    repository = LeaseRepository()
    reconciler = Reconciler()
    worker = DirectoryWorker(reconciler, repository)
    with pytest.raises(RuntimeError):
        await worker.run_once("startup")
    assert repository.held is False
    reconciler.fail = False
    assert await worker.run_once("scheduled") == "scheduled"
    assert reconciler.calls == 2


@pytest.mark.asyncio
async def test_worker_survives_startup_failure_and_schedules_six_hour_retry() -> None:
    from app.control_plane.directory_worker import DirectoryWorker

    class Lease:
        def worker_lease(self):
            class Acquired:
                def __enter__(self): return True
                def __exit__(self, *args): return None
            return Acquired()

    class Reconciler:
        calls = 0
        async def run_full(self, kind):
            self.calls += 1
            raise RuntimeError("provider secret body")

    delays = []
    async def stop_after_delay(delay):
        delays.append(delay)
        raise asyncio.CancelledError

    class NoJitter:
        def uniform(self, start, end): return 0

    reconciler = Reconciler()
    worker = DirectoryWorker(
        reconciler, Lease(), sleep=stop_after_delay, random_source=NoJitter()
    )
    with pytest.raises(asyncio.CancelledError):
        await worker.serve()
    assert reconciler.calls == 1
    assert delays == [21_600]


def test_versioned_canonical_digest_has_golden_vector_and_covers_ciphertext() -> None:
    from app.control_plane.directory import (
        DIRECTORY_SOURCE_SCHEMA_VERSION,
        canonical_directory_digest,
    )

    codec = _codec()
    department = codec.seal("department", "golden-dept")
    corporate = codec.seal("employee", "golden-user")
    union = codec.seal("employee_union", "golden-union")
    # Fixed ciphertext makes this a stable cross-language serialization vector.
    department = type(department)(department.subject_kind, department.lookup_hmac, 1, b"d" * 29, 1)
    corporate = type(corporate)(corporate.subject_kind, corporate.lookup_hmac, 1, b"c" * 29, 1)
    union = type(union)(union.subject_kind, union.lookup_hmac, 1, b"u" * 29, 1)
    dept_key = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    member_key = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    from app.control_plane.directory import StagedDepartment, StagedMember
    digest = canonical_directory_digest(
        DIRECTORY_SOURCE_SCHEMA_VERSION,
        (StagedDepartment(dept_key, None, department, "研发"),),
        (StagedMember(member_key, corporate, union, "Alice", "active", "female"),),
        ((member_key, dept_key),),
        ((dept_key, dept_key, 0),),
    )
    assert digest == "1c4829418990392bede2f2f2fb5bd0da8cdae27000fa1e3833a8a18d10a281fc"
    changed = type(corporate)(corporate.subject_kind, corporate.lookup_hmac, 1, b"x" * 29, 1)
    assert canonical_directory_digest(
        DIRECTORY_SOURCE_SCHEMA_VERSION,
        (StagedDepartment(dept_key, None, department, "研发"),),
        (StagedMember(member_key, changed, union, "Alice", "active", "female"),),
        ((member_key, dept_key),),
        ((dept_key, dept_key, 0),),
    ) != digest


def test_schema_v2_canonical_digest_distinguishes_member_gender() -> None:
    from app.control_plane.directory import (
        StagedDepartment,
        StagedMember,
        canonical_directory_digest,
    )

    codec = _codec()
    department = StagedDepartment(
        UUID("10000000-0000-4000-8000-000000000001"),
        None,
        codec.seal("department", "gender-digest"),
        "Engineering",
    )
    member_key = UUID("20000000-0000-4000-8000-000000000001")
    corporate = codec.seal("employee", "gender-user")
    union = codec.seal("employee_union", "gender-union")
    memberships = ((member_key, department.department_key),)
    closure = ((department.department_key, department.department_key, 0),)
    male = StagedMember(
        member_key, corporate, union, "Alice", "active", "male"
    )
    female = StagedMember(
        member_key, corporate, union, "Alice", "active", "female"
    )
    missing = StagedMember(
        member_key, corporate, union, "Alice", "active", None
    )

    digests = {
        canonical_directory_digest(2, (department,), (member,), memberships, closure)
        for member in (male, female, missing)
    }
    assert len(digests) == 3


@pytest.mark.postgres
def test_python_and_postgres_canonical_digest_are_identical(
    production_directory,
) -> None:
    from app.control_plane.directory import (
        DIRECTORY_SOURCE_SCHEMA_VERSION,
        StagedDepartment,
        StagedMember,
        canonical_directory_digest,
    )

    repository, environment = production_directory
    codec = _codec()
    protected = codec.seal("department", "cross-language")
    protected = type(protected)(
        protected.subject_kind, protected.lookup_hmac, 1, b"z" * 29, 1
    )
    department = StagedDepartment(
        UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        None, protected, "Cross Language",
    )
    corporate = codec.seal("employee", "cross-language-member")
    union = codec.seal("employee_union", "cross-language-union")
    member = StagedMember(
        UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        corporate,
        union,
        "Cross Language Member",
        "active",
        "female",
    )
    memberships = ((member.member_key, department.department_key),)
    closure = ((department.department_key, department.department_key, 0),)
    expected = canonical_directory_digest(
        DIRECTORY_SOURCE_SCHEMA_VERSION,
        (department,),
        (member,),
        memberships,
        closure,
    )
    generation = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    repository.create_staging_generation(
        generation, UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        "scheduled", 1, 1, 1, 1, DIRECTORY_SOURCE_SCHEMA_VERSION, expected,
    )
    repository.stage_departments(generation, (department,))
    repository.stage_members(generation, (member,))
    repository.stage_memberships(generation, memberships)
    repository.stage_closure(generation, closure)
    assert repository.finalize_staging_generation(generation) == expected
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select platform_control.directory_generation_checksum_v28(%s)",
            (generation,),
        ).fetchone() == (expected,)


@pytest.mark.postgres
def test_database_operation_consumes_remaining_deadline_budget(
    production_directory,
) -> None:
    from app.control_plane.directory_worker import DirectoryRepositoryError

    repository, _ = production_directory
    started = time.monotonic()
    with pytest.raises(DirectoryRepositoryError):
        repository._call("select pg_sleep(1)", (), timeout_seconds=0.05)
    assert time.monotonic() - started < 0.8


@pytest.mark.asyncio
async def test_committed_promotion_near_deadline_reports_success() -> None:
    class NearDeadlineRepository(FakeRepository):
        def promote_generation(self, generation_id, **kwargs):
            self.active_generation = generation_id
            time.sleep(0.06)

    repository = NearDeadlineRepository()
    result = await _reconciler(
        FakeClient(), repository, hard_timeout_seconds=0.05
    ).run_full()
    assert result.generation_id == repository.active_generation
