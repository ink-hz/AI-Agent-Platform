from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from test_agent_brain_conversation_repository import _codec
from test_control_plane_migration import control_database  # noqa: F401

from app.agent_brain.conversation_repository import message_subject
from app.hr.position_package_projection import (
    ClaimedPositionPackage,
    PositionPackageProjectionRepository,
    PositionPackageProjectionUnavailable,
    PositionPackageProjector,
)
from app.hr.repository import HrPositionRepository, HrUnavailable
from app.hr.service import HrPositionService
from app.hr.structured_output import encode_hr_envelope

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "076_hr_position_packages.sql"
)
PACKAGE = {
    "title": "高级结构工程师",
    "modules": {
        "mission": {"text": "负责新产品结构落地"},
        "jd": {"text": "负责精密结构设计与量产"},
        "jr": {"text": "具备五年以上结构设计经验"},
    },
}


def _markdown() -> str:
    return "完整岗位方案。\n\n" + encode_hr_envelope("position_package", PACKAGE)


def _seed_owner(connection: psycopg.Connection) -> UUID:
    owner_id = uuid4()
    connection.execute(
        "insert into platform_control.internal_users "
        "(internal_user_id,display_name,status) values (%s,'Projection Owner','active')",
        (owner_id,),
    )
    return owner_id


def _seed_conversation(
    connection: psycopg.Connection,
    owner_id: UUID,
    markdown: str,
    *,
    created_at: datetime | None = None,
    direct_agent_id: str = "hr-bot",
    assistant_role: str = "assistant",
    delivery_status: str = "completed",
    turn_status: str = "completed",
) -> tuple[UUID, UUID, UUID]:
    conversation_id = uuid4()
    timestamp = created_at or datetime.now(timezone.utc)
    connection.execute(
        "insert into platform_control.conversations("
        "conversation_id,owner_internal_user_id,started_by_client_request_id,"
        "mode,direct_agent_id,title,status,created_at,updated_at) values "
        "(%s,%s,%s,'direct_agent',%s,'岗位草拟','active',%s,%s)",
        (conversation_id, owner_id, uuid4(), direct_agent_id, timestamp, timestamp),
    )
    turn_id, assistant_message_id = _seed_turn(
        connection,
        conversation_id,
        markdown,
        created_at=timestamp,
        assistant_role=assistant_role,
        delivery_status=delivery_status,
        turn_status=turn_status,
    )
    return conversation_id, turn_id, assistant_message_id


def _seed_turn(
    connection: psycopg.Connection,
    conversation_id: UUID,
    markdown: str,
    *,
    seq: int = 1,
    created_at: datetime | None = None,
    assistant_role: str = "assistant",
    delivery_status: str = "completed",
    turn_status: str = "completed",
) -> tuple[UUID, UUID]:
    codec = _codec()
    turn_id = uuid4()
    user_message_id, assistant_message_id = uuid4(), uuid4()
    user = codec.seal_json(
        message_subject(conversation_id, user_message_id), {"text": "请生成岗位方案"}
    )
    assistant = codec.seal_json(
        message_subject(conversation_id, assistant_message_id), {"text": markdown}
    )
    timestamp = created_at or datetime.now(timezone.utc)
    connection.execute("set constraints all deferred")
    connection.execute(
        "insert into platform_control.conversation_messages("
        "message_id,conversation_id,seq,role,content_ciphertext,"
        "encryption_key_version,turn_id,delivery_status,created_at,completed_at) "
        "values (%s,%s,%s,'user',%s,%s,%s,'completed',%s,%s),"
        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            user_message_id,
            conversation_id,
            seq,
            user.ciphertext,
            user.key_version,
            turn_id,
            timestamp,
            timestamp,
            assistant_message_id,
            conversation_id,
            seq + 1,
            assistant_role,
            assistant.ciphertext,
            assistant.key_version,
            turn_id,
            delivery_status,
            timestamp,
            timestamp if delivery_status in {"completed", "failed"} else None,
        ),
    )
    connection.execute(
        "insert into platform_control.conversation_turns("
        "turn_id,conversation_id,user_message_id,assistant_message_id,"
        "client_request_id,status,created_at,updated_at) "
        "values (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            turn_id,
            conversation_id,
            user_message_id,
            assistant_message_id,
            uuid4(),
            turn_status,
            timestamp,
            timestamp,
        ),
    )
    return turn_id, assistant_message_id


def _seed_draft(
    connection: psycopg.Connection, owner_id: UUID, conversation_id: UUID
) -> UUID:
    draft_id = uuid4()
    connection.execute(
        "insert into platform_hr.position_drafts("
        "draft_id,owner_internal_user_id,client_request_id,source_kind,"
        "source_key,source_conversation_id,title,proposal,evidence,"
        "discovery_rule_version) values ("
        "%s,%s,%s,'new_conversation',%s,%s,'显式岗位草稿','{}','{}','interactive-v1')",
        (
            draft_id,
            owner_id,
            uuid4(),
            f"explicit:{conversation_id}",
            conversation_id,
        ),
    )
    return draft_id


def _projector(environment, *, worker_id: str = "position-package.test"):
    database_url = environment["urls"]["platform_control_app"]
    repository = PositionPackageProjectionRepository(database_url)
    positions = HrPositionService(HrPositionRepository(database_url))
    return (
        PositionPackageProjector(
            repository,
            positions,
            _codec(),
            worker_id=worker_id,
            model_version="hr-runtime-v1",
        ),
        repository,
        positions,
    )


def test_migration_defines_owner_scoped_leased_projection_contract() -> None:
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())

    assert "create table platform_hr.position_package_projections" in sql
    for function in (
        "claim_position_package_projection_v76",
        "complete_position_package_projection_v76",
        "fail_position_package_projection_v76",
        "release_position_package_projection_v76",
    ):
        assert f"create function platform_hr.{function}" in sql
        assert f"revoke all on function platform_hr.{function}" in sql
        assert f"grant execute on function platform_hr.{function}" in sql
    assert "conversation.mode='direct_agent'" in sql
    assert "conversation.direct_agent_id='hr-bot'" in sql
    assert "message.role='assistant'" in sql
    assert "message.delivery_status='completed'" in sql
    assert "turn.status='completed'" in sql
    assert "for update of message skip locked" in sql
    assert "source_kind='new_conversation'" in sql
    assert "conversation:" in sql


@pytest.mark.postgres
def test_free_hr_conversation_projects_package_into_deterministic_draft(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _seed_owner(admin)
        conversation_id, _turn_id, assistant_message_id = _seed_conversation(
            admin, owner_id, _markdown()
        )
    projector, _repository, positions = _projector(environment)

    assert projector.reconcile_one() is True

    with psycopg.connect(environment["admin"]) as admin:
        draft = admin.execute(
            "select draft_id,source_kind,source_key,state from "
            "platform_hr.position_drafts where owner_internal_user_id=%s "
            "and source_conversation_id=%s",
            (owner_id, conversation_id),
        ).fetchone()
    assert draft[1:] == (
        "new_conversation",
        f"conversation:{conversation_id}",
        "proposed",
    )
    version = positions.latest_draft_version(owner_id, draft[0])
    assert set(version.modules) == {"mission", "jd", "jr"}
    assert version.source_assistant_message_id == assistant_message_id
    assert projector.reconcile_one() is False


@pytest.mark.postgres
def test_explicit_proposed_draft_receives_the_conversation_package(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _seed_owner(admin)
        conversation_id, _turn_id, assistant_message_id = _seed_conversation(
            admin, owner_id, _markdown()
        )
        draft_id = _seed_draft(admin, owner_id, conversation_id)
    projector, _repository, positions = _projector(environment)

    assert projector.reconcile_one() is True

    version = positions.latest_draft_version(owner_id, draft_id)
    assert version.title == PACKAGE["title"]
    assert version.source_assistant_message_id == assistant_message_id
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_hr.position_drafts "
            "where owner_internal_user_id=%s and source_conversation_id=%s",
            (owner_id, conversation_id),
        ).fetchone()[0] == 1


@pytest.mark.postgres
def test_no_envelope_is_skipped_and_malformed_envelope_does_not_block_next_message(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _seed_owner(admin)
        clarification = _seed_conversation(
            admin, owner_id, "请问岗位在哪个城市？", created_at=start
        )
    projector, _repository, _positions = _projector(environment)
    assert projector.reconcile_one() is True
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,error_code from platform_hr.position_package_projections "
            "where assistant_message_id=%s",
            (clarification[2],),
        ).fetchone() == ("skipped", None)
        malformed = _seed_conversation(
            admin,
            owner_id,
            "不完整包\n\n<!-- platform-hr-v1:not-valid! -->",
            created_at=start + timedelta(seconds=1),
        )
        valid = _seed_conversation(
            admin,
            owner_id,
            _markdown(),
            created_at=start + timedelta(seconds=2),
        )

    assert projector.reconcile_one() is True
    assert projector.reconcile_one() is True

    with psycopg.connect(environment["admin"]) as admin:
        states = dict(
            admin.execute(
                "select assistant_message_id,state from "
                "platform_hr.position_package_projections where "
                "assistant_message_id in (%s,%s)",
                (malformed[2], valid[2]),
            ).fetchall()
        )
        assert states == {malformed[2]: "failed", valid[2]: "completed"}
        assert admin.execute(
            "select count(*) from platform_hr.position_draft_versions "
            "where source_assistant_message_id=%s",
            (valid[2],),
        ).fetchone()[0] == 1


@pytest.mark.postgres
def test_resolved_deterministic_draft_is_terminal_for_one_message_and_does_not_starve_queue(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    start = datetime.now(timezone.utc)
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _seed_owner(admin)
        conversation_id, _turn_id, _message_id = _seed_conversation(
            admin, owner_id, _markdown(), created_at=start
        )
    projector, _repository, _positions = _projector(environment)
    assert projector.reconcile_one() is True
    with psycopg.connect(environment["admin"]) as admin:
        draft_id = admin.execute(
            "select draft_id from platform_hr.position_drafts "
            "where owner_internal_user_id=%s and source_conversation_id=%s",
            (owner_id, conversation_id),
        ).fetchone()[0]
        admin.execute(
            "update platform_hr.position_drafts set state='dismissed' "
            "where draft_id=%s",
            (draft_id,),
        )
        _later_turn_id, later_message_id = _seed_turn(
            admin,
            conversation_id,
            _markdown(),
            seq=3,
            created_at=start + timedelta(seconds=1),
        )
        next_conversation = _seed_conversation(
            admin,
            owner_id,
            _markdown(),
            created_at=start + timedelta(seconds=2),
        )

    assert projector.reconcile_one() is True
    assert projector.reconcile_one() is True

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,error_code from platform_hr.position_package_projections "
            "where assistant_message_id=%s",
            (later_message_id,),
        ).fetchone() == ("failed", "projection_scope_invalid")
        assert admin.execute(
            "select state from platform_hr.position_package_projections "
            "where assistant_message_id=%s",
            (next_conversation[2],),
        ).fetchone()[0] == "completed"


@pytest.mark.postgres
def test_valid_non_position_hr_envelope_is_skipped(control_database) -> None:
    environment = control_database["environments"]["production"]
    candidate = encode_hr_envelope(
        "candidate_match",
        {
            "summary": "候选人匹配结论",
            "dimensions": [],
            "evidence": [],
            "gaps": [],
            "risks": [],
            "unknowns": [],
            "verification_questions": [],
        },
    )
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _seed_owner(admin)
        _conversation_id, _turn_id, assistant_message_id = _seed_conversation(
            admin, owner_id, "候选人分析。\n\n" + candidate
        )
    projector, _repository, _positions = _projector(environment)

    assert projector.reconcile_one() is True

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,error_code from platform_hr.position_package_projections "
            "where assistant_message_id=%s",
            (assistant_message_id,),
        ).fetchone() == ("skipped", None)


class _ReplayLedger:
    def __init__(self, repository, claim: ClaimedPositionPackage) -> None:
        self._repository = repository
        self._claims = [claim, claim]

    def claim(self, _worker_id, _lease_seconds):
        return self._claims.pop(0) if self._claims else None

    def complete(self, claim, worker_id, draft_version_id):
        self._repository.complete(claim, worker_id, draft_version_id)

    def fail(self, claim, worker_id, error_code):
        self._repository.fail(claim, worker_id, error_code)

    def release(self, claim, worker_id, error_code):
        self._repository.release(claim, worker_id, error_code)


@pytest.mark.postgres
def test_duplicate_result_replay_creates_only_one_draft_version(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _seed_owner(admin)
        conversation_id, _turn_id, _message_id = _seed_conversation(
            admin, owner_id, _markdown()
        )
    _projector_instance, repository, positions = _projector(environment)
    claim = repository.claim("position-package.replay", 300)
    assert claim is not None
    projector = PositionPackageProjector(
        _ReplayLedger(repository, claim),
        positions,
        _codec(),
        worker_id="position-package.replay",
        model_version="hr-runtime-v1",
    )

    assert projector.reconcile_one() is True
    assert projector.reconcile_one() is True

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_hr.position_draft_versions "
            "where source_conversation_id=%s",
            (conversation_id,),
        ).fetchone()[0] == 1


@pytest.mark.postgres
def test_repository_release_requeues_the_same_leased_message(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _seed_owner(admin)
        _seed_conversation(admin, owner_id, _markdown())
    repository = PositionPackageProjectionRepository(
        environment["urls"]["platform_control_app"]
    )
    first = repository.claim("position-package.release-one", 300)
    assert first is not None

    repository.release(first, "position-package.release-one", "projection_unavailable")

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,error_code,attempt_count from "
            "platform_hr.position_package_projections where projection_id=%s",
            (first.projection_id,),
        ).fetchone() == ("pending", "projection_unavailable", 1)
        admin.execute(
            "update platform_hr.position_package_projections "
            "set available_at=now() where projection_id=%s",
            (first.projection_id,),
        )
    second = repository.claim("position-package.release-two", 300)
    assert second == first
    repository.fail(second, "position-package.release-two", "envelope_invalid")


@pytest.mark.postgres
def test_claim_rejects_wrong_agent_role_and_incomplete_turns(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _seed_owner(admin)
        _seed_conversation(admin, owner_id, _markdown(), direct_agent_id="fae-bot")
        _seed_conversation(admin, owner_id, _markdown(), assistant_role="system")
        _seed_conversation(
            admin,
            owner_id,
            _markdown(),
            delivery_status="streaming",
            turn_status="running",
        )
    repository = PositionPackageProjectionRepository(
        environment["urls"]["platform_control_app"]
    )

    assert repository.claim("position-package.ineligible", 300) is None


@pytest.mark.postgres
def test_concurrent_claims_are_distinct_and_expired_lease_rejects_stale_worker(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _seed_owner(admin)
        _seed_conversation(admin, owner_id, _markdown())
        _seed_conversation(admin, owner_id, _markdown())
    repository = PositionPackageProjectionRepository(
        environment["urls"]["platform_control_app"]
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = tuple(
            pool.map(
                lambda worker: repository.claim(worker, 300),
                ("position-package.concurrent-one", "position-package.concurrent-two"),
            )
        )
    assert all(claim is not None for claim in claims)
    assert len({claim.assistant_message_id for claim in claims if claim}) == 2
    first, second = claims
    assert first is not None and second is not None
    with pytest.raises(PositionPackageProjectionUnavailable):
        repository.fail(first, "position-package.stale", "envelope_invalid")
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "update platform_hr.position_package_projections "
            "set lease_expires_at=now()-interval '1 second' where projection_id=%s",
            (first.projection_id,),
        )
    replacement = repository.claim("position-package.takeover", 300)
    assert replacement == first
    with pytest.raises(PositionPackageProjectionUnavailable):
        repository.fail(first, "position-package.concurrent-one", "envelope_invalid")
    repository.fail(replacement, "position-package.takeover", "envelope_invalid")
    repository.fail(second, "position-package.concurrent-two", "envelope_invalid")


@pytest.mark.postgres
def test_non_app_role_cannot_claim_or_read_projection_ledger(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["urls"]["platform_brain_worker"]) as worker:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute(
                "select * from platform_hr.claim_position_package_projection_v76("
                "'position-package.denied',300)"
            )
        worker.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute("select * from platform_hr.position_package_projections")


class _Ledger:
    def __init__(self, claims) -> None:
        self.claims = list(claims)
        self.completed = []
        self.failed = []
        self.released = []

    def claim(self, _worker_id, _lease_seconds):
        return self.claims.pop(0) if self.claims else None

    def complete(self, claim, worker_id, draft_version_id):
        self.completed.append((claim, worker_id, draft_version_id))

    def fail(self, claim, worker_id, error_code):
        self.failed.append((claim, worker_id, error_code))

    def release(self, claim, worker_id, error_code):
        self.released.append((claim, worker_id, error_code))


@pytest.mark.postgres
def test_cross_owner_claim_is_terminal_without_writing_a_version(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner_id = _seed_owner(admin)
        other_owner_id = _seed_owner(admin)
        conversation_id, turn_id, assistant_message_id = _seed_conversation(
            admin, owner_id, _markdown()
        )
        draft_id = _seed_draft(admin, owner_id, conversation_id)
    real_repository = PositionPackageProjectionRepository(
        environment["urls"]["platform_control_app"]
    )
    claimed = real_repository.claim("position-package.owner", 300)
    assert claimed is not None
    cross_owner = ClaimedPositionPackage(
        projection_id=claimed.projection_id,
        projection_request_id=claimed.projection_request_id,
        owner_id=other_owner_id,
        draft_id=draft_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        assistant_message_id=assistant_message_id,
        agent_id="hr-bot",
        content_ciphertext=claimed.content_ciphertext,
        encryption_key_version=claimed.encryption_key_version,
    )
    ledger = _Ledger((cross_owner,))
    positions = HrPositionService(
        HrPositionRepository(environment["urls"]["platform_control_app"])
    )
    projector = PositionPackageProjector(
        ledger,
        positions,
        _codec(),
        worker_id="position-package.owner",
        model_version="hr-runtime-v1",
    )

    assert projector.reconcile_one() is True

    assert ledger.failed[0][2] == "projection_scope_invalid"
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_hr.position_draft_versions "
            "where draft_id=%s",
            (draft_id,),
        ).fetchone()[0] == 0


def test_temporary_position_failure_releases_only_the_claim() -> None:
    codec = _codec()
    conversation_id = UUID(int=5)
    assistant_message_id = UUID(int=7)
    sealed = codec.seal_json(
        message_subject(conversation_id, assistant_message_id),
        {"text": _markdown()},
    )
    claim = ClaimedPositionPackage(
        projection_id=UUID(int=1),
        projection_request_id=UUID(int=2),
        owner_id=UUID(int=3),
        draft_id=UUID(int=4),
        conversation_id=conversation_id,
        turn_id=UUID(int=6),
        assistant_message_id=assistant_message_id,
        agent_id="hr-bot",
        content_ciphertext=sealed.ciphertext,
        encryption_key_version=sealed.key_version,
    )
    ledger = _Ledger((claim,))

    class _UnavailablePositions:
        def create_draft_version(self, **_kwargs):
            raise HrUnavailable("temporary")

    projector = PositionPackageProjector(
        ledger,
        _UnavailablePositions(),
        codec,
        worker_id="position-package.release",
        model_version="hr-runtime-v1",
    )

    assert projector.reconcile_one() is True
    assert ledger.released[0][2] == "projection_unavailable"


def test_invalid_position_service_result_releases_the_claim() -> None:
    codec = _codec()
    conversation_id = UUID(int=15)
    assistant_message_id = UUID(int=17)
    sealed = codec.seal_json(
        message_subject(conversation_id, assistant_message_id),
        {"text": _markdown()},
    )
    claim = ClaimedPositionPackage(
        projection_id=UUID(int=11),
        projection_request_id=UUID(int=12),
        owner_id=UUID(int=13),
        draft_id=UUID(int=14),
        conversation_id=conversation_id,
        turn_id=UUID(int=16),
        assistant_message_id=assistant_message_id,
        agent_id="hr-bot",
        content_ciphertext=sealed.ciphertext,
        encryption_key_version=sealed.key_version,
    )
    ledger = _Ledger((claim,))

    class _InvalidPositions:
        def create_draft_version(self, **_kwargs):
            return SimpleNamespace(draft_version_id="not-a-uuid")

    projector = PositionPackageProjector(
        ledger,
        _InvalidPositions(),
        codec,
        worker_id="position-package.contract",
        model_version="hr-runtime-v1",
    )

    assert projector.reconcile_one() is True
    assert ledger.failed == []
    assert ledger.released[0][2] == "projection_unavailable"


def test_domain_invalid_oversized_package_is_terminal() -> None:
    codec = _codec()
    conversation_id = UUID(int=25)
    assistant_message_id = UUID(int=27)
    oversized = dict(PACKAGE)
    oversized["title"] = "岗" * 501
    markdown = encode_hr_envelope("position_package", oversized)
    sealed = codec.seal_json(
        message_subject(conversation_id, assistant_message_id),
        {"text": markdown},
    )
    claim = ClaimedPositionPackage(
        projection_id=UUID(int=21),
        projection_request_id=UUID(int=22),
        owner_id=UUID(int=23),
        draft_id=UUID(int=24),
        conversation_id=conversation_id,
        turn_id=UUID(int=26),
        assistant_message_id=assistant_message_id,
        agent_id="hr-bot",
        content_ciphertext=sealed.ciphertext,
        encryption_key_version=sealed.key_version,
    )
    ledger = _Ledger((claim,))

    class _DomainValidatingPositions:
        def create_draft_version(self, **_kwargs):
            raise ValueError("position title invalid")

    projector = PositionPackageProjector(
        ledger,
        _DomainValidatingPositions(),
        codec,
        worker_id="position-package.domain",
        model_version="hr-runtime-v1",
    )

    assert projector.reconcile_one() is True
    assert ledger.released == []
    assert ledger.failed[0][2] == "envelope_invalid"
