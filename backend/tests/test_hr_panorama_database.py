# ruff: noqa: F811
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from test_control_plane_migration import control_database  # noqa: F401

from app.agent_brain.conversation_repository import (
    ConversationRepository,
    ConversationRepositoryConflict,
)
from app.agent_brain.conversation_service import ConversationCommandService
from app.agent_brain.repository import MissionRepository
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from app.hr.panorama_context import (
    PanoramaContextProvider,
    _postgres_jsonb_text_size,
)
from app.hr.panorama_models import (
    CreatePanoramaRun,
    CreatePublicJobSnapshot,
    CreateTalentInsightVersion,
    CreateTalentSource,
    TransitionPanoramaRun,
    canonical_panorama_url,
)
from app.hr.panorama_repository import PanoramaConflict, PanoramaRepository
from app.hr.panorama_runtime import PanoramaRunCoordinator
from app.hr.panorama_service import PanoramaService

CREATE_SOURCE = (
    "select (platform_hr.create_talent_source_v79("
    "%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)).*"
)
LIST_SOURCES = "select * from platform_hr.list_talent_sources_v79(%s,%s,%s)"
PAGE_SOURCES = "select * from platform_hr.list_talent_sources_page_v79(%s,%s,%s,%s,%s)"
CREATE_RUN = "select (platform_hr.create_panorama_run_v79(%s,%s,%s,%s::uuid[],%s)).*"
LIST_RUNS = "select * from platform_hr.list_panorama_runs_v79(%s,%s)"
TRANSITION_RUN = (
    "select (platform_hr.transition_panorama_run_v79(%s,%s,%s,%s,%s,%s,%s::jsonb)).*"
)
CREATE_SNAPSHOT = (
    "select (platform_hr.create_public_job_snapshot_v79("
    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)).*"
)
LIST_SNAPSHOTS = "select * from platform_hr.list_public_job_snapshots_v79(%s,%s,%s)"
CREATE_INSIGHT = (
    "select (platform_hr.create_talent_insight_version_v79("
    "%s,%s,%s,%s,%s::uuid[],%s::uuid[],%s::jsonb,%s::jsonb,%s::jsonb,"
    "%s::jsonb,%s,%s,%s,%s,%s)).*"
)
LIST_INSIGHTS = "select * from platform_hr.list_talent_insight_versions_v79(%s,%s)"
CREATE_RETRIEVAL = (
    "select (platform_hr.create_position_insight_retrieval_v79("
    "%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s::jsonb)).*"
)
LIST_RETRIEVALS = (
    "select * from platform_hr.list_position_insight_retrievals_v79(%s,%s,%s)"
)
READ_TURN_RETRIEVAL = (
    "select * from platform_hr.read_position_insight_retrieval_for_turn_v79(%s,%s,%s)"
)
READ_SOURCES = "select * from platform_hr.read_talent_sources_v79(%s,%s::uuid[])"
READ_RUN = "select (platform_hr.read_panorama_run_v79(%s,%s)).*"
READ_SNAPSHOTS = (
    "select * from platform_hr.read_public_job_snapshots_v79(%s,%s::uuid[])"
)
READ_INSIGHT = "select (platform_hr.read_talent_insight_version_v79(%s,%s)).*"
PAGE_INSIGHTS = (
    "select * from platform_hr.list_talent_insight_versions_page_v79(%s,%s,%s)"
)
READ_RUNTIME = "select * from platform_hr.read_panorama_run_runtime_v79(%s)"
CLAIM_RUNTIME = "select * from platform_hr.claim_next_panorama_run_v79(%s)"


def _seed_owner_scope(admin: psycopg.Connection, display_name: str) -> dict[str, UUID]:
    ids = {
        name: uuid4()
        for name in (
            "owner",
            "conversation",
            "turn",
            "user_message",
            "assistant_message",
            "position",
        )
    }
    admin.execute(
        "insert into platform_control.internal_users("
        "internal_user_id,display_name,status) values (%s,%s,'active')",
        (ids["owner"], display_name),
    )
    admin.execute(
        "insert into platform_control.conversations("
        "conversation_id,owner_internal_user_id,started_by_client_request_id,"
        "mode,direct_agent_id,title,status) values ("
        "%s,%s,%s,'direct_agent','hr-bot','全景分析','active')",
        (ids["conversation"], ids["owner"], uuid4()),
    )
    admin.execute(
        "insert into platform_control.conversation_messages("
        "message_id,conversation_id,seq,role,content_ciphertext,"
        "encryption_key_version,turn_id,delivery_status,completed_at) values ("
        "%s,%s,1,'user',%s,1,%s,'completed',now()),("
        "%s,%s,2,'assistant',%s,1,%s,'completed',now())",
        (
            ids["user_message"],
            ids["conversation"],
            b"u" * 29,
            ids["turn"],
            ids["assistant_message"],
            ids["conversation"],
            b"a" * 29,
            ids["turn"],
        ),
    )
    admin.execute(
        "insert into platform_control.conversation_turns("
        "turn_id,conversation_id,user_message_id,assistant_message_id,"
        "client_request_id,status) values (%s,%s,%s,%s,%s,'completed')",
        (
            ids["turn"],
            ids["conversation"],
            ids["user_message"],
            ids["assistant_message"],
            uuid4(),
        ),
    )
    admin.execute(
        "insert into platform_hr.positions("
        "position_id,owner_internal_user_id,client_request_id,source_kind,title) "
        "values (%s,%s,%s,'manual','高级结构工程师')",
        (ids["position"], ids["owner"], uuid4()),
    )
    admin.execute(
        "insert into platform_hr.position_conversations("
        "conversation_id,owner_internal_user_id,position_id,client_request_id,"
        "binding_kind) values (%s,%s,%s,%s,'created_in_position')",
        (
            ids["conversation"],
            ids["owner"],
            ids["position"],
            uuid4(),
        ),
    )
    admin.commit()
    return ids


def _source_values(
    scope: dict[str, UUID],
    *,
    request_id: UUID,
    source_id: UUID | None = None,
    company_key: str = "union-optech",
    canonical_name: str = "联合光电",
) -> tuple[object, ...]:
    return (
        source_id or uuid4(),
        scope["owner"],
        request_id,
        company_key,
        canonical_name,
        '["Union Optech"]',
        '["https://example.com/jobs"]',
        True,
    )


def _snapshot_values(
    scope: dict[str, UUID],
    source_id: UUID,
    run_id: UUID,
    *,
    request_id: UUID,
    snapshot_id: UUID | None = None,
    content_sha256: str = "a" * 64,
    title: str = "结构工程师",
    public_job_key: str = "job-001",
    source_url: str = "https://example.com/jobs/001",
    observed_at: str = "2026-09-05T08:00:00+00:00",
) -> tuple[object, ...]:
    return (
        snapshot_id or uuid4(),
        scope["owner"],
        request_id,
        run_id,
        source_id,
        public_job_key,
        title,
        "中山",
        "负责精密结构设计",
        "五年以上结构经验",
        source_url,
        observed_at,
        content_sha256,
        "open",
    )


def _insight_values(scope, run_id, source_id, snapshot_id, observation_id):
    return (
        uuid4(),
        scope["owner"],
        uuid4(),
        run_id,
        [source_id],
        [snapshot_id],
        json.dumps(
            [
                {
                    "fact_id": "f1",
                    "text": "公开招聘结构工程师",
                    "snapshot_id": str(snapshot_id),
                    "observation_id": str(observation_id),
                    "source_url": "https://example.com/jobs/001",
                    "observed_at": "2026-09-05T08:00:00Z",
                }
            ],
            ensure_ascii=False,
        ),
        '[{"text":"结构岗位增加","basis_fact_ids":["f1"]}]',
        '[{"text":"招聘人数未知"}]',
        '{"结构":4}',
        "结构人才需求上升",
        scope["conversation"],
        scope["turn"],
        "hr-bot",
        "gpt-5",
    )


def _create_running_run(app: psycopg.Connection, scope, source_id):
    selected_source_ids = [source_id] if isinstance(source_id, UUID) else source_id
    run_id = uuid4()
    created = app.execute(
        CREATE_RUN,
        (
            run_id,
            scope["owner"],
            uuid4(),
            selected_source_ids,
            scope["conversation"],
        ),
    ).fetchone()
    running = app.execute(
        TRANSITION_RUN,
        (scope["owner"], run_id, uuid4(), created[8], "running", None, "{}"),
    ).fetchone()
    return run_id, running


def _content_codec() -> ContentCodec:
    return ContentCodec(
        IdentityKeyring(
            active_version=1,
            purpose="platform-content-encryption",
            _keys={1: b"p" * 32},
        )
    )


def _publication_operation(scope, run, source, *, expected_row_version):
    snapshot_id, observation_id, insight_id, transition_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )

    def publish(writer):
        snapshot = writer.create_snapshot(
            CreatePublicJobSnapshot(
                snapshot_id,
                scope["owner"],
                observation_id,
                run.run_id,
                source.source_id,
                "atomic-job",
                "结构工程师",
                "中山",
                "负责结构设计",
                "五年以上经验",
                "https://example.com/jobs/atomic-job",
                datetime.fromisoformat("2026-09-05T08:00:00+00:00"),
                "d" * 64,
                "open",
            )
        )
        writer.create_insight(
            CreateTalentInsightVersion(
                insight_id,
                scope["owner"],
                insight_id,
                run.run_id,
                (source.source_id,),
                (snapshot.snapshot_id,),
                (
                    {
                        "fact_id": "atomic-fact",
                        "text": "公开招聘结构工程师",
                        "snapshot_id": str(snapshot.snapshot_id),
                        "observation_id": str(observation_id),
                        "source_url": "https://example.com/jobs/atomic-job",
                        "observed_at": "2026-09-05T08:00:00Z",
                    },
                ),
                (),
                ({"text": "招聘人数未知"},),
                {"结构": 1},
                "结构岗位公开招聘",
                scope["conversation"],
                scope["turn"],
                "hr-bot",
                "test-model",
            )
        )
        writer.transition_run(
            TransitionPanoramaRun(
                scope["owner"],
                run.run_id,
                transition_id,
                expected_row_version,
                "completed",
                None,
                {},
            )
        )

    return publish


@pytest.mark.postgres
@pytest.mark.parametrize("environment_name", ("production", "preview"))
def test_panorama_contract_migrates_and_is_app_only_in_each_environment(
    control_database,
    environment_name,
) -> None:
    environment = control_database["environments"][environment_name]
    app_role = environment["roles"][1]
    signatures = (
        "platform_hr.create_talent_source_v79(uuid,uuid,uuid,text,text,jsonb,jsonb,boolean)",
        "platform_hr.list_talent_sources_v79(uuid,boolean,integer)",
        "platform_hr.list_talent_sources_page_v79(uuid,boolean,timestamptz,uuid,integer)",
        "platform_hr.create_panorama_run_v79(uuid,uuid,uuid,uuid[],uuid)",
        "platform_hr.list_panorama_runs_v79(uuid,integer)",
        "platform_hr.transition_panorama_run_v79(uuid,uuid,uuid,bigint,text,text,jsonb)",
        "platform_hr.create_public_job_snapshot_v79(uuid,uuid,uuid,uuid,uuid,text,text,text,text,text,text,timestamptz,text,text)",
        "platform_hr.list_public_job_snapshots_v79(uuid,uuid,integer)",
        "platform_hr.create_talent_insight_version_v79(uuid,uuid,uuid,uuid,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,text,uuid,uuid,text,text)",
        "platform_hr.list_talent_insight_versions_v79(uuid,integer)",
        "platform_hr.create_position_insight_retrieval_v79(uuid,uuid,uuid,uuid,uuid,uuid,uuid[],text,jsonb)",
        "platform_hr.list_position_insight_retrievals_v79(uuid,uuid,integer)",
        "platform_hr.read_position_insight_retrieval_for_turn_v79(uuid,uuid,uuid)",
        "platform_hr.read_talent_sources_v79(uuid,uuid[])",
        "platform_hr.read_panorama_run_v79(uuid,uuid)",
        "platform_hr.read_public_job_snapshots_v79(uuid,uuid[])",
        "platform_hr.read_talent_insight_version_v79(uuid,uuid)",
        "platform_hr.list_talent_insight_versions_page_v79(uuid,bigint,integer)",
        "platform_hr.read_panorama_run_runtime_v79(uuid)",
        "platform_hr.claim_next_panorama_run_v79(integer)",
    )
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select version from platform_control.schema_migrations where version=79"
        ).fetchone() == (79,)
        for signature in signatures:
            assert admin.execute(
                "select has_function_privilege(%s,%s,'EXECUTE')",
                (app_role, signature),
            ).fetchone() == (True,)
            for role in ("public", environment["roles"][5], environment["roles"][6]):
                assert admin.execute(
                    "select has_function_privilege(%s,%s,'EXECUTE')",
                    (role, signature),
                ).fetchone() == (False,)
        for table in (
            "talent_sources",
            "panorama_runs",
            "panorama_run_sources",
            "panorama_run_transition_events",
            "public_job_snapshots",
            "public_job_snapshot_requests",
            "public_job_current_snapshots",
            "talent_insight_versions",
            "talent_insight_sources",
            "talent_insight_snapshots",
            "position_insight_retrievals",
            "position_insight_retrieval_versions",
        ):
            assert admin.execute(
                "select has_table_privilege(%s,%s,'SELECT'),"
                "has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE')",
                (app_role, f"platform_hr.{table}", app_role, f"platform_hr.{table}"),
            ).fetchone() == (False, False)
            for role in (
                "public",
                environment["roles"][5],
                environment["roles"][6],
            ):
                assert admin.execute(
                    "select has_table_privilege(%s,%s,'SELECT'),"
                    "has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE')",
                    (role, f"platform_hr.{table}", role, f"platform_hr.{table}"),
                ).fetchone() == (False, False)
    with psycopg.connect(environment["urls"][app_role]) as app:
        assert app.execute(LIST_SOURCES, (uuid4(), False, 10)).fetchall() == []
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute(
                "insert into platform_hr.talent_sources(source_id) values (%s)",
                (uuid4(),),
            )
    for role in (environment["roles"][5], environment["roles"][6]):
        with (
            psycopg.connect(environment["urls"][role]) as connection,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            connection.execute(LIST_SOURCES, (uuid4(), False, 10))


@pytest.mark.postgres
def test_runtime_claim_is_exclusive_rediscoverable_and_excludes_terminal_runs(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "update platform_hr.panorama_runs set state='failed',"
            "started_at=coalesce(started_at,now()),finished_at=now(),"
            "error_code='test_cleanup',updated_at=now() "
            "where state in ('queued','running')"
        )
        scope = _seed_owner_scope(admin, "Panorama Runtime Claim Owner")
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        source = app.execute(
            CREATE_SOURCE,
            _source_values(scope, request_id=uuid4()),
        ).fetchone()
        run_id, running = _create_running_run(app, scope, source[0])
        app.commit()

    gate = Barrier(2)

    def claim():
        with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
            gate.wait()
            return app.execute(CLAIM_RUNTIME, (1,)).fetchall()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _index: claim(), range(2)))

    assert sorted(bool(rows) for rows in results) == [False, True]
    claimed = next(rows for rows in results if rows)
    assert {row[0] for row in claimed} == {run_id}

    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        assert app.execute(READ_RUNTIME, (run_id,)).fetchall()
        assert app.execute(CLAIM_RUNTIME, (1,)).fetchall() == []
        time.sleep(1.1)
        rediscovered = app.execute(CLAIM_RUNTIME, (1,)).fetchall()
        assert {row[0] for row in rediscovered} == {run_id}
        app.execute(
            TRANSITION_RUN,
            (
                scope["owner"],
                run_id,
                uuid4(),
                running[8],
                "failed",
                "search_unavailable",
                "{}",
            ),
        )
        app.commit()
        time.sleep(1.1)
        assert app.execute(CLAIM_RUNTIME, (1,)).fetchall() == []


@pytest.mark.postgres
def test_coordinator_replay_creates_one_exact_conversation_turn(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Panorama Conversation Replay Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    source = repository.create_source(
        CreateTalentSource(
            uuid4(),
            scope["owner"],
            uuid4(),
            "panorama-replay-company",
            "全景回放公司",
            (),
            ("https://example.com/jobs",),
            True,
        )
    )
    run = repository.create_run(
        CreatePanoramaRun(
            uuid4(),
            scope["owner"],
            uuid4(),
            (source.source_id,),
            scope["conversation"],
        )
    )
    codec = _content_codec()
    conversations = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        mission_repository=MissionRepository(
            environment["urls"]["platform_control_app"],
            content_codec=codec,
        ),
    )
    coordinator = PanoramaRunCoordinator(
        repository,
        ConversationCommandService(conversations, v2_enabled=False),
        resolver=lambda _host, _port: ("8.8.8.8", "2001:4860:4860::8888"),
    )

    coordinator.submit(run.run_id)
    with psycopg.connect(environment["admin"]) as admin:
        first = admin.execute(
            "select turn_id,client_request_id from "
            "platform_control.conversation_turns where conversation_id=%s "
            "order by created_at desc,turn_id desc limit 1",
            (scope["conversation"],),
        ).fetchone()
    coordinator.submit(run.run_id)

    with psycopg.connect(environment["admin"]) as admin:
        turns = admin.execute(
            "select turn_id,client_request_id from "
            "platform_control.conversation_turns where conversation_id=%s "
            "order by created_at,turn_id",
            (scope["conversation"],),
        ).fetchall()
    assert len(turns) == 2
    assert turns[-1] == first


@pytest.mark.postgres
def test_archived_conversation_after_run_creation_durably_fails_run(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Panorama Archived Race Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    source = repository.create_source(
        CreateTalentSource(
            uuid4(),
            scope["owner"],
            uuid4(),
            f"archived-race-{uuid4().hex}",
            "归档竞态公司",
            (),
            ("https://example.com/jobs",),
            True,
        )
    )
    run = repository.create_run(
        CreatePanoramaRun(
            uuid4(),
            scope["owner"],
            uuid4(),
            (source.source_id,),
            scope["conversation"],
        )
    )
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "update platform_control.conversations set "
            "status='archived',archived_at=now(),updated_at=now() "
            "where conversation_id=%s",
            (scope["conversation"],),
        )
        admin.commit()
    codec = _content_codec()
    conversations = ConversationRepository(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        mission_repository=MissionRepository(
            environment["urls"]["platform_control_app"],
            content_codec=codec,
        ),
    )
    coordinator = PanoramaRunCoordinator(
        repository,
        ConversationCommandService(conversations, v2_enabled=False),
        resolver=lambda _host, _port: ("8.8.8.8", "2001:4860:4860::8888"),
    )

    with pytest.raises(ConversationRepositoryConflict):
        coordinator.submit(run.run_id)

    failed = repository.run(scope["owner"], run.run_id)
    assert failed.state == "failed"
    assert failed.error_code == "conversation_rejected"
    assert failed.started_at is not None
    assert failed.finished_at is not None


@pytest.mark.postgres
def test_report_publication_rolls_back_every_write_when_terminal_transition_fails(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Atomic Panorama Rollback Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    source = repository.create_source(
        CreateTalentSource(
            uuid4(),
            scope["owner"],
            uuid4(),
            f"atomic-{uuid4().hex}",
            "原子发布公司",
            (),
            ("https://example.com/jobs",),
            True,
        )
    )
    run = repository.create_run(
        CreatePanoramaRun(
            uuid4(),
            scope["owner"],
            uuid4(),
            (source.source_id,),
            scope["conversation"],
        )
    )
    running = repository.transition_run(
        TransitionPanoramaRun(
            scope["owner"], run.run_id, uuid4(), run.row_version, "running", None, {}
        )
    )

    with pytest.raises(PanoramaConflict):
        repository.publish_report(
            _publication_operation(
                scope,
                running,
                source,
                expected_row_version=running.row_version + 99,
            )
        )

    assert repository.list_snapshots(scope["owner"], source.source_id) == ()
    assert repository.list_insights(scope["owner"]) == ()
    assert repository.run(scope["owner"], run.run_id) == running


@pytest.mark.postgres
def test_runtime_unavailable_rejects_before_any_run_insert(control_database) -> None:
    from app.hr.panorama_repository import PanoramaUnavailable

    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Unavailable Panorama Runtime Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    source = repository.create_source(
        CreateTalentSource(
            uuid4(),
            scope["owner"],
            uuid4(),
            f"unavailable-{uuid4().hex}",
            "运行时不可用公司",
            (),
            ("https://example.com/jobs",),
            True,
        )
    )

    with pytest.raises(PanoramaUnavailable, match="runtime unavailable"):
        PanoramaService(repository).start_run(
            owner_id=scope["owner"],
            request_id=uuid4(),
            source_ids=(source.source_id,),
            conversation_id=scope["conversation"],
        )

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_hr.panorama_runs "
            "where owner_internal_user_id=%s",
            (scope["owner"],),
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_dns_rejection_after_insert_persists_a_failed_run(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Rejected Panorama Destination Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    source = repository.create_source(
        CreateTalentSource(
            uuid4(),
            scope["owner"],
            uuid4(),
            f"rejected-{uuid4().hex}",
            "目标拒绝公司",
            (),
            ("https://example.com/jobs",),
            True,
        )
    )
    run = repository.create_run(
        CreatePanoramaRun(
            uuid4(),
            scope["owner"],
            uuid4(),
            (source.source_id,),
            scope["conversation"],
        )
    )

    class Commands:
        def append_turn(self, *args):
            pytest.fail("rejected destination must not dispatch")

    coordinator = PanoramaRunCoordinator(
        repository,
        Commands(),
        resolver=lambda _host, _port: ("10.0.0.7",),
    )
    with pytest.raises(ValueError, match="destination invalid"):
        coordinator.submit(run.run_id)

    failed = repository.run(scope["owner"], run.run_id)
    assert failed.state == "failed"
    assert failed.error_code == "destination_invalid"
    assert failed.started_at is not None and failed.finished_at is not None


@pytest.mark.postgres
def test_dns_preflight_rejects_before_run_insert(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Panorama DNS Preflight Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    source = repository.create_source(
        CreateTalentSource(
            uuid4(),
            scope["owner"],
            uuid4(),
            f"preflight-{uuid4().hex}",
            "预检拒绝公司",
            (),
            ("https://example.com/jobs",),
            True,
        )
    )

    class Commands:
        def append_turn(self, *args):
            pytest.fail("preflight rejection must not dispatch")

    service = PanoramaService(
        repository,
        coordinator=PanoramaRunCoordinator(
            repository,
            Commands(),
            resolver=lambda _host, _port: ("10.0.0.7",),
        ),
    )
    with pytest.raises(ValueError, match="destination invalid"):
        service.start_run(
            owner_id=scope["owner"],
            request_id=uuid4(),
            source_ids=(source.source_id,),
            conversation_id=scope["conversation"],
        )

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_hr.panorama_runs "
            "where owner_internal_user_id=%s",
            (scope["owner"],),
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_concurrent_report_publication_replays_one_atomic_projection(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Atomic Panorama Concurrent Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    source = repository.create_source(
        CreateTalentSource(
            uuid4(),
            scope["owner"],
            uuid4(),
            f"atomic-{uuid4().hex}",
            "并发发布公司",
            (),
            ("https://example.com/jobs",),
            True,
        )
    )
    run = repository.create_run(
        CreatePanoramaRun(
            uuid4(),
            scope["owner"],
            uuid4(),
            (source.source_id,),
            scope["conversation"],
        )
    )
    running = repository.transition_run(
        TransitionPanoramaRun(
            scope["owner"], run.run_id, uuid4(), run.row_version, "running", None, {}
        )
    )
    operation = _publication_operation(
        scope,
        running,
        source,
        expected_row_version=running.row_version,
    )
    gate = Barrier(2)

    def publish(_index):
        gate.wait()
        return PanoramaRepository(
            environment["urls"]["platform_control_app"]
        ).publish_report(operation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        tuple(pool.map(publish, range(2)))

    assert len(repository.list_snapshots(scope["owner"], source.source_id)) == 1
    assert len(repository.list_insights(scope["owner"])) == 1
    assert repository.run(scope["owner"], run.run_id).state == "completed"


@pytest.mark.postgres
def test_point_reads_and_insight_keyset_cover_records_beyond_first_100(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Point Read Owner")
        other = _seed_owner_scope(admin, "Point Read Other Owner")
        source_id = uuid4()
        admin.execute(
            "insert into platform_hr.talent_sources("
            "source_id,owner_internal_user_id,client_request_id,company_key,"
            "canonical_name,approved_public_urls) values ("
            "%s,%s,%s,'point-read-company','点查公司','[\"https://example.com/jobs\"]')",
            (source_id, scope["owner"], uuid4()),
        )
        run_ids = [uuid4() for _ in range(101)]
        for run_id in run_ids:
            admin.execute(
                "insert into platform_hr.panorama_runs("
                "run_id,owner_internal_user_id,client_request_id,"
                "selected_source_ids,conversation_id) values (%s,%s,%s,%s,%s)",
                (
                    run_id,
                    scope["owner"],
                    uuid4(),
                    [source_id],
                    scope["conversation"],
                ),
            )
        snapshot_id, observation_id = uuid4(), uuid4()
        admin.execute(
            "insert into platform_hr.public_job_snapshots("
            "snapshot_id,owner_internal_user_id,origin_client_request_id,run_id,"
            "source_id,public_job_key,title,location,duty_excerpt,"
            "requirement_excerpt,source_url,observed_at,content_sha256,status) "
            "values (%s,%s,%s,%s,%s,'point-job','结构工程师','中山','结构设计',"
            "'五年经验','https://example.com/jobs/point',%s,%s,'open')",
            (
                snapshot_id,
                scope["owner"],
                observation_id,
                run_ids[0],
                source_id,
                datetime.fromisoformat("2026-09-05T08:00:00+00:00"),
                "a" * 64,
            ),
        )
        insight_ids = [uuid4() for _ in range(101)]
        facts = json.dumps(
            [
                {
                    "fact_id": "f1",
                    "text": "公开招聘结构工程师",
                    "snapshot_id": str(snapshot_id),
                    "observation_id": str(observation_id),
                    "source_url": "https://example.com/jobs/point",
                    "observed_at": "2026-09-05T08:00:00Z",
                }
            ],
            ensure_ascii=False,
        )
        for version_number, insight_id in enumerate(insight_ids, start=1):
            admin.execute(
                "insert into platform_hr.talent_insight_versions("
                "insight_version_id,owner_internal_user_id,client_request_id,run_id,"
                "version_number,selected_source_ids,snapshot_ids,facts,inferences,"
                "unknowns,direction_clusters,summary,source_conversation_id,"
                "source_turn_id,agent_id,model_version) values ("
                "%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'[]','[]','{}','点查报告',"
                "%s,%s,'hr-bot','gpt-5')",
                (
                    insight_id,
                    scope["owner"],
                    uuid4(),
                    run_ids[0],
                    version_number,
                    [source_id],
                    [snapshot_id],
                    facts,
                    scope["conversation"],
                    scope["turn"],
                ),
            )
        admin.commit()

    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        assert (
            app.execute(READ_RUN, (scope["owner"], run_ids[0])).fetchone()[0]
            == run_ids[0]
        )
        assert (
            app.execute(READ_INSIGHT, (scope["owner"], insight_ids[0])).fetchone()[0]
            == insight_ids[0]
        )
        first_page = app.execute(PAGE_INSIGHTS, (scope["owner"], None, 100)).fetchall()
        second_page = app.execute(
            PAGE_INSIGHTS, (scope["owner"], first_page[-1][4], 100)
        ).fetchall()
        assert [row[4] for row in first_page] == list(range(101, 1, -1))
        assert [row[4] for row in second_page] == [1]
        assert (
            app.execute(READ_SOURCES, (scope["owner"], [source_id])).fetchone()[0]
            == source_id
        )
        assert (
            app.execute(READ_SNAPSHOTS, (scope["owner"], [snapshot_id])).fetchone()[0]
            == snapshot_id
        )
        repository = PanoramaRepository(environment["urls"]["platform_control_app"])
        assert repository.run(scope["owner"], run_ids[0]).run_id == run_ids[0]
        assert (
            repository.insight(scope["owner"], insight_ids[0]).insight_version_id
            == insight_ids[0]
        )
        assert (
            repository.report(scope["owner"], insight_ids[0]).snapshots[0].snapshot_id
            == snapshot_id
        )
        assert len(repository._ranking_candidates(scope["owner"])) == 101
        for statement, parameters in (
            (READ_RUN, (other["owner"], run_ids[0])),
            (READ_INSIGHT, (other["owner"], insight_ids[0])),
            (READ_SOURCES, (other["owner"], [source_id])),
            (READ_SNAPSHOTS, (other["owner"], [snapshot_id])),
        ):
            with pytest.raises(psycopg.errors.NoDataFound):
                app.execute(statement, parameters).fetchall()
            app.rollback()
        for invalid_limit in (None, 0, 101):
            with pytest.raises(psycopg.errors.CheckViolation):
                app.execute(
                    PAGE_INSIGHTS,
                    (scope["owner"], None, invalid_limit),
                ).fetchall()
            app.rollback()


@pytest.mark.postgres
def test_panorama_lists_reject_null_limits(control_database) -> None:
    environment = control_database["environments"]["production"]
    calls = (
        (LIST_SOURCES, (uuid4(), False, None)),
        (LIST_RUNS, (uuid4(), None)),
        (LIST_SNAPSHOTS, (uuid4(), uuid4(), None)),
        (LIST_INSIGHTS, (uuid4(), None)),
        (LIST_RETRIEVALS, (uuid4(), uuid4(), None)),
    )
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        for statement, parameters in calls:
            with pytest.raises(psycopg.errors.CheckViolation):
                app.execute(statement, parameters)
            app.rollback()


@pytest.mark.postgres
def test_source_create_replays_rejects_mismatch_and_lists_only_one_owner(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner = _seed_owner_scope(admin, "Panorama Owner")
        other = _seed_owner_scope(admin, "Other Panorama Owner")
    request_id, source_id = uuid4(), uuid4()
    values = _source_values(owner, request_id=request_id, source_id=source_id)
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        first = app.execute(CREATE_SOURCE, values).fetchone()
        replay = app.execute(CREATE_SOURCE, values).fetchone()
        app.execute(CREATE_SOURCE, _source_values(other, request_id=uuid4()))
        assert replay == first
        assert [
            row[0]
            for row in app.execute(LIST_SOURCES, (owner["owner"], False, 10)).fetchall()
        ] == [source_id]
        with pytest.raises(
            psycopg.errors.UniqueViolation,
            match="talent source idempotency payload mismatch",
        ):
            app.execute(
                CREATE_SOURCE,
                _source_values(
                    owner,
                    request_id=request_id,
                    source_id=source_id,
                    canonical_name="被篡改名称",
                ),
            )
        app.rollback()
        for invalid_url in (
            "http://example.com/jobs",
            "https://user:password@example.com/jobs",
            "https:///jobs",
            "https://localhost/jobs",
            "https://jobs.local/jobs",
            "https://127.0.0.1/jobs",
            "https://127.1/jobs",
            "https://0x7f000001/jobs",
            "https://0x7f.0.0.1/jobs",
            "https://2130706433/jobs",
            "https://0177.0.0.1/jobs",
            "https://10.0.0.8/jobs",
            "https://100.64.0.1/jobs",
            "https://169.254.169.254/latest/meta-data",
            "https://[::1]/jobs",
            "https://[v1.test]/jobs",
            "https://Example.com/jobs",
            "HTTPS://example.com/jobs",
            "https://example.com:0443/jobs",
            "https://example.com:/jobs",
            "https://example.com:8443/jobs",
            "https://example.com:65536/jobs",
            "https://example.com/jobs/../admin",
            "https://example.com/jobs/%2e%2e/admin",
            "https://example.com/jobs%",
            "https://example.com/jobs%2",
            "https://example.com/jobs%GG",
            "https://example.com/jobs/%FF",
            "https://example.com/jobs/%2E%2E/admin",
            "https://example.com/jobs/%2fadmin",
            "https://example.com/jobs/%5Cadmin",
            "https://example.com/jobs/%25admin",
            "https://example.com/jobs/%EF%BC%8Fadmin",
            "https://example.com/jobs/%EF%BC%8E%EF%BC%8E/admin",
            "https://example.com/jobs／admin",
            "https://example.com/jobs#section",
            "https://example.com/jobs?%74oken=x",
            "https://example.com/jobs?to%6Ben=x",
            "https://example.com/jobs?%2574oken=x",
            "https://example.com/jobs?key=x",
            "https://example.com/jobs?api+key=x",
            "https://example.com/jobs?ｔｏｋｅｎ=x",
            "https://example.com/jobs?passwd=x",
            "https://example.com/jobs?sig=x",
            "https://example.com/jobs\\admin",
        ):
            invalid = list(_source_values(owner, request_id=uuid4()))
            invalid[6] = json.dumps([invalid_url])
            with pytest.raises(psycopg.errors.CheckViolation):
                app.execute(CREATE_SOURCE, invalid)
            app.rollback()


@pytest.mark.postgres
def test_approved_url_prefix_is_canonical_and_uses_literal_path_boundaries(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Literal URL Owner")
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        source_values = list(_source_values(scope, request_id=uuid4()))
        source_values[6] = '["https://example.com/jobs_100%20open"]'
        source = app.execute(CREATE_SOURCE, source_values).fetchone()
        run_id, _ = _create_running_run(app, scope, source[0])
        allowed = app.execute(
            CREATE_SNAPSHOT,
            _snapshot_values(
                scope,
                source[0],
                run_id,
                request_id=uuid4(),
                source_url="https://example.com/jobs_100%20open/001",
            ),
        ).fetchone()
        assert allowed[0]
        app.commit()
        for unapproved in (
            "https://example.com/jobsX100%20open/001",
            "https://example.com/jobs_100%20open-evil/001",
            "https://example.com/other/jobs_100%20open/001",
        ):
            values = list(
                _snapshot_values(
                    scope,
                    source[0],
                    run_id,
                    request_id=uuid4(),
                    source_url=unapproved,
                )
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                app.execute(CREATE_SNAPSHOT, values)
            app.rollback()


@pytest.mark.postgres
@pytest.mark.parametrize(
    "url",
    (
        "https://example.com",
        "https://example.com:443/jobs/open-role",
        "https://jobs.example.com/careers/%E6%8B%9B%E8%81%98",
        "https://jobs.example.com/careers?page=1&role=engineer",
    ),
)
def test_python_accepted_canonical_urls_are_also_accepted_by_sql(
    control_database,
    url: str,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, f"Canonical URL Owner {uuid4().hex}")
    assert canonical_panorama_url(url) == url
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        values = list(
            _source_values(
                scope,
                request_id=uuid4(),
                company_key=f"canonical-{uuid4().hex}",
                canonical_name="Canonical URL Company",
            )
        )
        values[6] = json.dumps([url])
        assert app.execute(CREATE_SOURCE, values).fetchone()[0] == values[0]


@pytest.mark.postgres
def test_python_accepted_utf8_evidence_url_publishes_without_sql_drift(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Canonical Publication Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    source = repository.create_source(
        CreateTalentSource(
            uuid4(),
            scope["owner"],
            uuid4(),
            f"canonical-publication-{uuid4().hex}",
            "Canonical Publication Company",
            (),
            ("https://jobs.example.com/careers",),
            True,
        )
    )
    run = repository.create_run(
        CreatePanoramaRun(
            uuid4(),
            scope["owner"],
            uuid4(),
            (source.source_id,),
            scope["conversation"],
        )
    )
    running = repository.transition_run(
        TransitionPanoramaRun(
            scope["owner"], run.run_id, uuid4(), run.row_version, "running", None, {}
        )
    )
    evidence_url = (
        "https://jobs.example.com/careers/%E6%8B%9B%E8%81%98?page=1&role=engineer"
    )
    assert canonical_panorama_url(evidence_url) == evidence_url

    snapshot = repository.create_snapshot(
        CreatePublicJobSnapshot(
            uuid4(),
            scope["owner"],
            uuid4(),
            running.run_id,
            source.source_id,
            "canonical-job",
            "结构工程师",
            "中山",
            "负责结构设计",
            "五年以上经验",
            evidence_url,
            datetime.fromisoformat("2026-09-05T08:00:00+00:00"),
            "e" * 64,
            "open",
        )
    )

    assert snapshot.source_url == evidence_url


@pytest.mark.postgres
def test_cross_owner_links_and_unapproved_or_unbounded_content_fail_closed(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        owner = _seed_owner_scope(admin, "Scope Owner")
        other = _seed_owner_scope(admin, "Wrong Scope Owner")
        wrong_agent = _seed_owner_scope(admin, "Wrong Agent Owner")
        admin.execute(
            "update platform_control.conversations set direct_agent_id='fae-bot' "
            "where conversation_id=%s",
            (wrong_agent["conversation"],),
        )
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as app:
        source = app.execute(
            CREATE_SOURCE, _source_values(owner, request_id=uuid4())
        ).fetchone()
        app.commit()
        with pytest.raises(psycopg.errors.NoDataFound):
            app.execute(
                CREATE_RUN,
                (
                    uuid4(),
                    other["owner"],
                    uuid4(),
                    [source[0]],
                    other["conversation"],
                ),
            )
        app.rollback()
        wrong_agent_source = app.execute(
            CREATE_SOURCE, _source_values(wrong_agent, request_id=uuid4())
        ).fetchone()
        app.commit()
        with pytest.raises(psycopg.errors.NoDataFound):
            app.execute(
                CREATE_RUN,
                (
                    uuid4(),
                    wrong_agent["owner"],
                    uuid4(),
                    [wrong_agent_source[0]],
                    wrong_agent["conversation"],
                ),
            )
        app.rollback()
        run_id, _ = _create_running_run(app, owner, source[0])
        app.commit()
        unapproved = list(
            _snapshot_values(owner, source[0], run_id, request_id=uuid4())
        )
        unapproved[10] = "https://unapproved.example/jobs/001"
        with pytest.raises(psycopg.errors.CheckViolation):
            app.execute(CREATE_SNAPSHOT, unapproved)
        app.rollback()
        oversized = list(_snapshot_values(owner, source[0], run_id, request_id=uuid4()))
        oversized[8] = "d" * 32769
        with pytest.raises(psycopg.errors.CheckViolation):
            app.execute(CREATE_SNAPSHOT, oversized)


@pytest.mark.postgres
def test_concurrent_snapshot_collection_deduplicates_and_supersedes_current(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Snapshot Owner")
    with psycopg.connect(app_url) as app:
        source = app.execute(
            CREATE_SOURCE, _source_values(scope, request_id=uuid4())
        ).fetchone()
        run_id, _ = _create_running_run(app, scope, source[0])
        app.commit()
    barrier = Barrier(2)

    def create_once() -> tuple[tuple[object, ...], UUID]:
        observation_id = uuid4()
        values = _snapshot_values(
            scope,
            source[0],
            run_id,
            request_id=observation_id,
            snapshot_id=uuid4(),
        )
        with psycopg.connect(app_url) as app:
            barrier.wait(timeout=3)
            return app.execute(CREATE_SNAPSHOT, values).fetchone(), observation_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result, second_result = tuple(pool.map(lambda _: create_once(), range(2)))
    first, first_observation_id = first_result
    second, _ = second_result
    assert first[0] == second[0]
    with psycopg.connect(environment["admin"]) as admin:
        evidence_before = admin.execute(
            "select snapshot_id,run_id,source_id,public_job_key,title,location,"
            "duty_excerpt,requirement_excerpt,source_url,observed_at,"
            "content_sha256,status from platform_hr.public_job_snapshots "
            "where snapshot_id=%s",
            (first[0],),
        ).fetchone()
    with psycopg.connect(app_url) as app:
        historical_insight = app.execute(
            CREATE_INSIGHT,
            _insight_values(
                scope,
                run_id,
                source[0],
                first[0],
                first_observation_id,
            ),
        ).fetchone()
        changed = app.execute(
            CREATE_SNAPSHOT,
            _snapshot_values(
                scope,
                source[0],
                run_id,
                request_id=uuid4(),
                content_sha256="b" * 64,
                title="高级结构工程师",
            ),
        ).fetchone()
        app.commit()
        reactivation_run_id, _ = _create_running_run(app, scope, source[0])
        reactivated_values = list(
            _snapshot_values(
                scope,
                source[0],
                reactivation_run_id,
                request_id=uuid4(),
                snapshot_id=uuid4(),
            )
        )
        reactivated_values[11] = "2026-09-06T08:00:00+00:00"
        reactivated = app.execute(CREATE_SNAPSHOT, reactivated_values).fetchone()
        rows = app.execute(LIST_SNAPSHOTS, (scope["owner"], source[0], 10)).fetchall()
    assert changed[0] != first[0]
    assert reactivated[0] == first[0]
    assert reactivated[3] == run_id
    assert reactivated[11] == datetime.fromisoformat("2026-09-05T08:00:00+00:00")
    assert len(rows) == 2
    with psycopg.connect(environment["admin"]) as admin:
        evidence_after = admin.execute(
            "select snapshot_id,run_id,source_id,public_job_key,title,location,"
            "duty_excerpt,requirement_excerpt,source_url,observed_at,"
            "content_sha256,status from platform_hr.public_job_snapshots "
            "where snapshot_id=%s",
            (first[0],),
        ).fetchone()
        assert evidence_after == evidence_before
        assert admin.execute(
            "select snapshot_id from platform_hr.talent_insight_snapshots "
            "where insight_version_id=%s",
            (historical_insight[0],),
        ).fetchone() == (first[0],)
        current = admin.execute(
            "select current_snapshot.snapshot_id,observation.run_id,"
            "observation.observed_at from platform_hr.public_job_current_snapshots "
            "current_snapshot join platform_hr.public_job_snapshot_requests observation "
            "on observation.owner_internal_user_id="
            "current_snapshot.owner_internal_user_id and "
            "observation.observation_id="
            "current_snapshot.latest_observation_id "
            "where current_snapshot.owner_internal_user_id=%s and "
            "current_snapshot.source_id=%s and current_snapshot.public_job_key=%s",
            (scope["owner"], source[0], "job-001"),
        ).fetchone()
        assert current == (
            first[0],
            reactivation_run_id,
            datetime.fromisoformat("2026-09-06T08:00:00+00:00"),
        )
        assert admin.execute(
            "select count(*) from platform_hr.public_job_snapshot_requests "
            "where owner_internal_user_id=%s and source_id=%s "
            "and public_job_key=%s",
            (scope["owner"], source[0], "job-001"),
        ).fetchone() == (4,)
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="public job observation is immutable",
        ):
            admin.execute(
                "delete from platform_hr.public_job_snapshot_requests "
                "where owner_internal_user_id=%s and client_request_id=%s",
                (scope["owner"], reactivated_values[2]),
            )
        admin.rollback()
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="public job snapshot is immutable",
        ):
            admin.execute(
                "update platform_hr.public_job_snapshots set title='篡改' "
                "where snapshot_id=%s",
                (first[0],),
            )


@pytest.mark.postgres
def test_snapshot_request_replay_conflicts_on_changed_payload(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Snapshot Replay Owner")
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        source = app.execute(
            CREATE_SOURCE, _source_values(scope, request_id=uuid4())
        ).fetchone()
        run_id, _ = _create_running_run(app, scope, source[0])
        request_id, snapshot_id = uuid4(), uuid4()
        values = _snapshot_values(
            scope,
            source[0],
            run_id,
            request_id=request_id,
            snapshot_id=snapshot_id,
        )
        first = app.execute(CREATE_SNAPSHOT, values).fetchone()
        assert app.execute(CREATE_SNAPSHOT, values).fetchone() == first
        changed = list(values)
        changed[6] = "同 request 的不同标题"
        with pytest.raises(
            psycopg.errors.UniqueViolation,
            match="public job snapshot idempotency payload mismatch",
        ):
            app.execute(CREATE_SNAPSHOT, changed)


@pytest.mark.postgres
def test_concurrent_distinct_job_keys_keep_independent_current_projections(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Independent Projection Owner")
    with psycopg.connect(app_url) as app:
        source = app.execute(
            CREATE_SOURCE, _source_values(scope, request_id=uuid4())
        ).fetchone()
        run_id, _ = _create_running_run(app, scope, source[0])
        app.commit()
    barrier = Barrier(2)

    def create_job(job_number: int):
        with psycopg.connect(app_url) as app:
            barrier.wait(timeout=3)
            return app.execute(
                CREATE_SNAPSHOT,
                _snapshot_values(
                    scope,
                    source[0],
                    run_id,
                    request_id=uuid4(),
                    public_job_key=f"job-00{job_number}",
                    source_url=f"https://example.com/jobs/00{job_number}",
                    content_sha256=f"{job_number}" * 64,
                ),
            ).fetchone()

    with ThreadPoolExecutor(max_workers=2) as pool:
        created = tuple(pool.map(create_job, (2, 3)))
    with psycopg.connect(environment["admin"]) as admin:
        projections = admin.execute(
            "select public_job_key,snapshot_id "
            "from platform_hr.public_job_current_snapshots "
            "where owner_internal_user_id=%s and source_id=%s "
            "order by public_job_key",
            (scope["owner"], source[0]),
        ).fetchall()
    assert projections == [("job-002", created[0][0]), ("job-003", created[1][0])]


@pytest.mark.postgres
def test_current_projection_tie_break_and_late_observation_are_deterministic(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Deterministic Projection Owner")
    observation_ids = [UUID(int=value) for value in range(1, 7)]
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        source = app.execute(
            CREATE_SOURCE, _source_values(scope, request_id=uuid4())
        ).fetchone()
        run_id, _ = _create_running_run(app, scope, source[0])
        app.commit()
        for job_key, observations in (
            (
                "tie-low-then-high",
                ((observation_ids[0], "a"), (observation_ids[1], "b")),
            ),
            (
                "tie-high-then-low",
                ((observation_ids[3], "b"), (observation_ids[2], "a")),
            ),
        ):
            for observation_id, hash_character in observations:
                app.execute(
                    CREATE_SNAPSHOT,
                    _snapshot_values(
                        scope,
                        source[0],
                        run_id,
                        request_id=observation_id,
                        public_job_key=job_key,
                        source_url=f"https://example.com/jobs/{job_key}",
                        content_sha256=hash_character * 64,
                    ),
                )
                app.commit()
        for observation_id, hash_character, observed_at in (
            (observation_ids[4], "c", "2026-09-06T08:00:00+00:00"),
            (observation_ids[5], "d", "2026-09-04T08:00:00+00:00"),
        ):
            app.execute(
                CREATE_SNAPSHOT,
                _snapshot_values(
                    scope,
                    source[0],
                    run_id,
                    request_id=observation_id,
                    public_job_key="older-arrives-late",
                    source_url="https://example.com/jobs/older-arrives-late",
                    content_sha256=hash_character * 64,
                    observed_at=observed_at,
                ),
            )
            app.commit()
    with psycopg.connect(environment["admin"]) as admin:
        current = admin.execute(
            "select projection.public_job_key,observation.observation_id,"
            "snapshot.content_sha256 "
            "from platform_hr.public_job_current_snapshots projection "
            "join platform_hr.public_job_snapshot_requests observation "
            "on observation.owner_internal_user_id="
            "projection.owner_internal_user_id and observation.observation_id="
            "projection.latest_observation_id "
            "join platform_hr.public_job_snapshots snapshot "
            "on snapshot.owner_internal_user_id=projection.owner_internal_user_id "
            "and snapshot.snapshot_id=projection.snapshot_id "
            "where projection.owner_internal_user_id=%s "
            "order by projection.public_job_key",
            (scope["owner"],),
        ).fetchall()
    assert current == [
        ("older-arrives-late", observation_ids[4], "c" * 64),
        ("tie-high-then-low", observation_ids[3], "b" * 64),
        ("tie-low-then-high", observation_ids[1], "b" * 64),
    ]


@pytest.mark.postgres
def test_run_create_and_transition_replay_or_reject_changed_payload(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Run Replay Owner")
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        source = app.execute(
            CREATE_SOURCE, _source_values(scope, request_id=uuid4())
        ).fetchone()
        run_id, run_request_id = uuid4(), uuid4()
        run_values = (
            run_id,
            scope["owner"],
            run_request_id,
            [source[0]],
            scope["conversation"],
        )
        created = app.execute(CREATE_RUN, run_values).fetchone()
        assert app.execute(CREATE_RUN, run_values).fetchone() == created
        transition_request_id = uuid4()
        transition_values = (
            scope["owner"],
            run_id,
            transition_request_id,
            created[8],
            "running",
            None,
            "{}",
        )
        running = app.execute(TRANSITION_RUN, transition_values).fetchone()
        assert app.execute(TRANSITION_RUN, transition_values).fetchone() == running
        app.commit()
        with pytest.raises(
            psycopg.errors.UniqueViolation,
            match="panorama run transition idempotency payload mismatch",
        ):
            app.execute(
                TRANSITION_RUN,
                (*transition_values[:4], "completed", None, "{}"),
            )
        app.rollback()
        with pytest.raises(
            psycopg.errors.UniqueViolation,
            match="panorama run idempotency payload mismatch",
        ):
            app.execute(
                CREATE_RUN,
                (
                    uuid4(),
                    scope["owner"],
                    run_request_id,
                    [source[0]],
                    scope["conversation"],
                ),
            )


@pytest.mark.postgres
def test_run_transitions_serialize_and_failed_run_preserves_snapshots(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Transition Owner")
    with psycopg.connect(app_url) as app:
        source = app.execute(
            CREATE_SOURCE, _source_values(scope, request_id=uuid4())
        ).fetchone()
        other_source = app.execute(
            CREATE_SOURCE,
            _source_values(
                scope,
                request_id=uuid4(),
                company_key="other-company",
                canonical_name="另一家公司",
            ),
        ).fetchone()
        run_id, running = _create_running_run(app, scope, [source[0], other_source[0]])
        snapshot = app.execute(
            CREATE_SNAPSHOT,
            _snapshot_values(scope, source[0], run_id, request_id=uuid4()),
        ).fetchone()
        app.commit()
    barrier = Barrier(2)

    def finish(state: str):
        error = "search_unavailable" if state == "failed" else None
        failures = (
            f'{{"{source[0]}":"search_unavailable"}}' if state == "failed" else "{}"
        )
        with psycopg.connect(app_url) as app:
            barrier.wait(timeout=3)
            return app.execute(
                TRANSITION_RUN,
                (
                    scope["owner"],
                    run_id,
                    uuid4(),
                    running[8],
                    state,
                    error,
                    failures,
                ),
            ).fetchone()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(finish, "failed") for _ in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except psycopg.errors.SerializationFailure:
                outcomes.append("conflict")
    assert sum(outcome == "conflict" for outcome in outcomes) == 1
    terminal = next(outcome for outcome in outcomes if outcome != "conflict")
    assert terminal[5] == "failed"
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_hr.public_job_snapshots "
            "where snapshot_id=%s",
            (snapshot[0],),
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_partial_completion_records_bounded_source_reason_without_deleting_history(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Partial Owner")
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        source = app.execute(
            CREATE_SOURCE, _source_values(scope, request_id=uuid4())
        ).fetchone()
        other_source = app.execute(
            CREATE_SOURCE,
            _source_values(
                scope,
                request_id=uuid4(),
                company_key="other-company",
                canonical_name="另一家公司",
            ),
        ).fetchone()
        run_id, running = _create_running_run(app, scope, [source[0], other_source[0]])
        snapshot = app.execute(
            CREATE_SNAPSHOT,
            _snapshot_values(scope, source[0], run_id, request_id=uuid4()),
        ).fetchone()
        app.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            app.execute(
                TRANSITION_RUN,
                (
                    scope["owner"],
                    run_id,
                    uuid4(),
                    running[8],
                    "partially_completed",
                    None,
                    (
                        f'{{"{source[0]}":"search_unavailable",'
                        f'"{other_source[0]}":"search_unavailable"}}'
                    ),
                ),
            )
        app.rollback()
        partial = app.execute(
            TRANSITION_RUN,
            (
                scope["owner"],
                run_id,
                uuid4(),
                running[8],
                "partially_completed",
                None,
                f'{{"{other_source[0]}":"search_unavailable"}}',
            ),
        ).fetchone()
        assert partial[5] == "partially_completed"
        assert partial[7] == {str(other_source[0]): "search_unavailable"}
        assert app.execute(LIST_RUNS, (scope["owner"], 10)).fetchone()[0] == run_id
        assert (
            app.execute(LIST_SNAPSHOTS, (scope["owner"], source[0], 10)).fetchone()[0]
            == snapshot[0]
        )


@pytest.mark.postgres
def test_insight_is_append_only_and_retrieval_is_position_owner_scoped(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Insight Owner")
        other = _seed_owner_scope(admin, "Other Insight Owner")
    with psycopg.connect(app_url) as app:
        source = app.execute(
            CREATE_SOURCE, _source_values(scope, request_id=uuid4())
        ).fetchone()
        run_id, _ = _create_running_run(app, scope, source[0])
        snapshot_observation_id = uuid4()
        snapshot = app.execute(
            CREATE_SNAPSHOT,
            _snapshot_values(
                scope,
                source[0],
                run_id,
                request_id=snapshot_observation_id,
            ),
        ).fetchone()
        app.commit()
        missing_fact_url = (
            uuid4(),
            scope["owner"],
            uuid4(),
            run_id,
            [source[0]],
            [snapshot[0]],
            json.dumps(
                [
                    {
                        "fact_id": "missing-url",
                        "text": "缺少来源",
                        "snapshot_id": str(snapshot[0]),
                        "observation_id": str(snapshot_observation_id),
                        "observed_at": "2026-09-05T08:00:00Z",
                    }
                ],
                ensure_ascii=False,
            ),
            "[]",
            "[]",
            "{}",
            "缺少来源的非法结论",
            scope["conversation"],
            scope["turn"],
            "hr-bot",
            "gpt-5",
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            app.execute(CREATE_INSIGHT, missing_fact_url)
        app.rollback()
        missing_basis = (
            uuid4(),
            scope["owner"],
            uuid4(),
            run_id,
            [source[0]],
            [snapshot[0]],
            _insight_values(
                scope,
                run_id,
                source[0],
                snapshot[0],
                snapshot_observation_id,
            )[6],
            '[{"text":"无证据推断","basis_fact_ids":["missing"]}]',
            "[]",
            "{}",
            "推断引用了不存在的事实",
            scope["conversation"],
            scope["turn"],
            "hr-bot",
            "gpt-5",
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            app.execute(CREATE_INSIGHT, missing_basis)
        app.rollback()
        empty_basis = list(missing_basis)
        empty_basis[0], empty_basis[2] = uuid4(), uuid4()
        empty_basis[7] = '[{"text":"无证据推断","basis_fact_ids":[]}]'
        with pytest.raises(psycopg.errors.CheckViolation):
            app.execute(CREATE_INSIGHT, empty_basis)
        app.rollback()
        insight_values = _insight_values(
            scope,
            run_id,
            source[0],
            snapshot[0],
            snapshot_observation_id,
        )
        insight_id = insight_values[0]
        insight = app.execute(CREATE_INSIGHT, insight_values).fetchone()
        assert app.execute(CREATE_INSIGHT, insight_values).fetchone() == insight
        assert (
            app.execute(LIST_INSIGHTS, (scope["owner"], 10)).fetchone()[0] == insight_id
        )
        retrieval_id, retrieval_request = uuid4(), uuid4()
        retrieval_values = (
            retrieval_id,
            scope["owner"],
            retrieval_request,
            scope["position"],
            scope["conversation"],
            scope["turn"],
            [insight_id],
            "c" * 64,
            f'[{{"insight_version_id":"{insight_id}","excerpt":"结构需求"}}]',
        )
        retrieval = app.execute(CREATE_RETRIEVAL, retrieval_values).fetchone()
        assert retrieval[0] == retrieval_id
        assert app.execute(CREATE_RETRIEVAL, retrieval_values).fetchone() == retrieval
        assert (
            app.execute(
                LIST_RETRIEVALS, (scope["owner"], scope["position"], 10)
            ).fetchone()[0]
            == retrieval_id
        )
        assert (
            app.execute(
                READ_TURN_RETRIEVAL,
                (scope["owner"], scope["position"], scope["turn"]),
            ).fetchone()
            == retrieval
        )
        assert (
            app.execute(
                READ_TURN_RETRIEVAL,
                (scope["owner"], other["position"], scope["turn"]),
            ).fetchone()
            is None
        )
        assert (
            app.execute(
                READ_TURN_RETRIEVAL,
                (other["owner"], scope["position"], scope["turn"]),
            ).fetchone()
            is None
        )
        app.commit()
        changed_insight = list(insight_values)
        changed_insight[10] = "同 request 的不同摘要"
        with pytest.raises(
            psycopg.errors.UniqueViolation,
            match="talent insight idempotency payload mismatch",
        ):
            app.execute(CREATE_INSIGHT, changed_insight)
        app.rollback()
        changed_retrieval = list(retrieval_values)
        changed_retrieval[7] = "d" * 64
        with pytest.raises(
            psycopg.errors.UniqueViolation,
            match="position insight retrieval idempotency payload mismatch",
        ):
            app.execute(CREATE_RETRIEVAL, changed_retrieval)
        app.rollback()
        cross_owner = list(retrieval_values)
        cross_owner[0], cross_owner[1], cross_owner[2] = (
            uuid4(),
            other["owner"],
            uuid4(),
        )
        cross_owner[3:6] = (other["position"], other["conversation"], other["turn"])
        with pytest.raises(psycopg.errors.NoDataFound):
            app.execute(CREATE_RETRIEVAL, cross_owner)
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select source_id,source_ordinal from platform_hr.talent_insight_sources "
            "where insight_version_id=%s order by source_ordinal",
            (insight_id,),
        ).fetchall() == [(source[0], 1)]
        assert admin.execute(
            "select snapshot_id,snapshot_ordinal "
            "from platform_hr.talent_insight_snapshots "
            "where insight_version_id=%s order by snapshot_ordinal",
            (insight_id,),
        ).fetchall() == [(snapshot[0], 1)]
        assert admin.execute(
            "select insight_version_id,insight_ordinal "
            "from platform_hr.position_insight_retrieval_versions "
            "where retrieval_id=%s order by insight_ordinal",
            (retrieval_id,),
        ).fetchall() == [(insight_id, 1)]
        for statement, parameters, message in (
            (
                (
                    "insert into platform_hr.talent_insight_sources("
                    "insight_version_id,owner_internal_user_id,source_id,"
                    "source_ordinal) values (%s,%s,%s,2)"
                ),
                (insight_id, scope["owner"], source[0]),
                "talent insight links are sealed",
            ),
            (
                (
                    "insert into platform_hr.talent_insight_snapshots("
                    "insight_version_id,owner_internal_user_id,snapshot_id,"
                    "snapshot_ordinal) values (%s,%s,%s,2)"
                ),
                (insight_id, scope["owner"], snapshot[0]),
                "talent insight links are sealed",
            ),
            (
                (
                    "update platform_hr.position_insight_retrieval_versions "
                    "set insight_ordinal=insight_ordinal where retrieval_id=%s"
                ),
                (retrieval_id,),
                "position insight retrieval links are sealed",
            ),
        ):
            with pytest.raises(psycopg.errors.CheckViolation, match=message):
                admin.execute(statement, parameters)
            admin.rollback()
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="position insight retrieval is immutable",
        ):
            admin.execute(
                "update platform_hr.position_insight_retrievals "
                "set insight_version_ids=insight_version_ids where retrieval_id=%s",
                (retrieval_id,),
            )
        admin.rollback()
        for table in ("talent_insight_sources", "talent_insight_snapshots"):
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="talent insight links are sealed",
            ):
                admin.execute(
                    f"delete from platform_hr.{table} where insight_version_id=%s",
                    (insight_id,),
                )
            admin.rollback()
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="talent insight version is immutable",
        ):
            admin.execute(
                "update platform_hr.talent_insight_versions set summary='篡改' "
                "where insight_version_id=%s",
                (insight_id,),
            )
        admin.rollback()
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="talent insight version is immutable",
        ):
            admin.execute(
                "delete from platform_hr.talent_insight_versions "
                "where insight_version_id=%s",
                (insight_id,),
            )


@pytest.mark.postgres
def test_talent_source_keyset_page_reaches_the_101st_followed_company(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Source Paging Owner")
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        for index in range(101):
            app.execute(
                CREATE_SOURCE,
                _source_values(
                    scope,
                    request_id=uuid4(),
                    source_id=UUID(int=index + 1),
                    company_key=f"paged-company-{index:03d}",
                    canonical_name=(
                        "第101家目标公司" if index == 100 else f"分页公司{index}"
                    ),
                ),
            )
        first = app.execute(
            PAGE_SOURCES, (scope["owner"], False, None, None, 100)
        ).fetchall()
        second = app.execute(
            PAGE_SOURCES,
            (scope["owner"], False, first[-1][9], first[-1][0], 100),
        ).fetchall()

    assert len(first) == 100
    assert len(second) == 1
    assert {row[0] for row in first}.isdisjoint({row[0] for row in second})
    assert second[0][5] == "第101家目标公司"


@pytest.mark.postgres
def test_context_budget_matches_postgres_jsonb_text_for_174_short_unknowns(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Context JSONB Budget Owner")
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        source = app.execute(
            CREATE_SOURCE, _source_values(scope, request_id=uuid4())
        ).fetchone()
        run_id, _ = _create_running_run(app, scope, source[0])
        observation_id = uuid4()
        snapshot = app.execute(
            CREATE_SNAPSHOT,
            _snapshot_values(
                scope,
                source[0],
                run_id,
                request_id=observation_id,
            ),
        ).fetchone()
        values = list(
            _insight_values(
                scope,
                run_id,
                source[0],
                snapshot[0],
                observation_id,
            )
        )
        values[8] = json.dumps(
            [{"text": f"短未知项-{index:03d}"} for index in range(174)],
            ensure_ascii=False,
        )
        app.execute(CREATE_INSIGHT, values)
        app.commit()

    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    provider = PanoramaContextProvider(
        repository,
        now=lambda: datetime.fromisoformat("2026-09-05T08:00:00+00:00"),
    )
    fragment = provider.for_turn(
        scope["owner"], scope["position"], "参考全景分析", scope["turn"]
    )

    assert fragment is not None
    assert (
        provider.for_turn(
            scope["owner"], scope["position"], "参考全景分析", scope["turn"]
        )
        == fragment
    )
    with psycopg.connect(environment["admin"]) as admin:
        persisted_size = admin.execute(
            "select octet_length(retrieved_excerpts::text) from "
            "platform_hr.position_insight_retrievals where "
            "owner_internal_user_id=%s and position_id=%s and turn_id=%s",
            (scope["owner"], scope["position"], scope["turn"]),
        ).fetchone()[0]
        estimated_size = _postgres_jsonb_text_size((fragment.as_prompt_document(),))
        actual_size = admin.execute(
            "select octet_length(%s::jsonb::text)",
            (json.dumps((fragment.as_prompt_document(),), ensure_ascii=False),),
        ).fetchone()[0]
    assert estimated_size == actual_size == persisted_size
    assert persisted_size <= 32768


@pytest.mark.postgres
@pytest.mark.parametrize(
    "invalid_case",
    (
        "extra_fact_key",
        "fact_not_object",
        "unselected_snapshot",
        "unknown_observation",
        "mismatched_source_url",
        "mismatched_observed_at",
        "invalid_observed_at",
        "extra_inference_key",
        "inference_not_object",
        "extra_unknown_key",
        "unknown_not_object",
    ),
)
def test_insight_payload_requires_exact_observation_bound_schemas(
    control_database,
    invalid_case,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, f"Exact Insight {invalid_case}")
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        source = app.execute(
            CREATE_SOURCE, _source_values(scope, request_id=uuid4())
        ).fetchone()
        run_id, _ = _create_running_run(app, scope, source[0])
        observation_id = uuid4()
        snapshot = app.execute(
            CREATE_SNAPSHOT,
            _snapshot_values(
                scope,
                source[0],
                run_id,
                request_id=observation_id,
            ),
        ).fetchone()
        app.commit()
        fact = {
            "fact_id": "f1",
            "text": "公开招聘结构工程师",
            "snapshot_id": str(snapshot[0]),
            "observation_id": str(observation_id),
            "source_url": "https://example.com/jobs/001",
            "observed_at": "2026-09-05T08:00:00Z",
        }
        inference = {"text": "结构岗位增加", "basis_fact_ids": ["f1"]}
        unknown = {"text": "招聘人数未知"}
        if invalid_case == "extra_fact_key":
            fact["unexpected"] = True
        elif invalid_case == "unselected_snapshot":
            fact["snapshot_id"] = str(uuid4())
        elif invalid_case == "unknown_observation":
            fact["observation_id"] = str(uuid4())
        elif invalid_case == "mismatched_source_url":
            fact["source_url"] = "https://example.com/jobs/other"
        elif invalid_case == "mismatched_observed_at":
            fact["observed_at"] = "2026-09-05T09:00:00Z"
        elif invalid_case == "invalid_observed_at":
            fact["observed_at"] = "2026-02-30T08:00:00Z"
        elif invalid_case == "extra_inference_key":
            inference["unexpected"] = True
        elif invalid_case == "extra_unknown_key":
            unknown["unexpected"] = True
        facts: list[object] = [fact]
        inferences: list[object] = [inference]
        unknowns: list[object] = [unknown]
        if invalid_case == "fact_not_object":
            facts = ["not-an-object"]
        elif invalid_case == "inference_not_object":
            inferences = ["not-an-object"]
        elif invalid_case == "unknown_not_object":
            unknowns = ["not-an-object"]
        values = list(
            _insight_values(
                scope,
                run_id,
                source[0],
                snapshot[0],
                observation_id,
            )
        )
        values[6] = json.dumps(facts, ensure_ascii=False)
        values[7] = json.dumps(inferences, ensure_ascii=False)
        values[8] = json.dumps(unknowns, ensure_ascii=False)
        with pytest.raises(psycopg.errors.CheckViolation):
            app.execute(CREATE_INSIGHT, values)


@pytest.mark.postgres
def test_normalized_links_exactly_mirror_parent_arrays(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Exact Normalized Links")
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        sources = (
            app.execute(
                CREATE_SOURCE,
                _source_values(
                    scope,
                    request_id=uuid4(),
                    company_key="first-company",
                    canonical_name="第一家公司",
                ),
            ).fetchone(),
            app.execute(
                CREATE_SOURCE,
                _source_values(
                    scope,
                    request_id=uuid4(),
                    company_key="second-company",
                    canonical_name="第二家公司",
                ),
            ).fetchone(),
        )
        run_id, _ = _create_running_run(app, scope, [sources[1][0], sources[0][0]])
        observation_ids = (uuid4(), uuid4())
        snapshots = tuple(
            app.execute(
                CREATE_SNAPSHOT,
                _snapshot_values(
                    scope,
                    source[0],
                    run_id,
                    request_id=observation_id,
                    public_job_key=f"job-{ordinal}",
                    source_url=f"https://example.com/jobs/{ordinal}",
                    content_sha256=str(ordinal) * 64,
                ),
            ).fetchone()
            for ordinal, (source, observation_id) in enumerate(
                zip(sources, observation_ids, strict=True), start=1
            )
        )
        facts = [
            {
                "fact_id": f"f{ordinal}",
                "text": f"公开岗位 {ordinal}",
                "snapshot_id": str(snapshot[0]),
                "observation_id": str(observation_id),
                "source_url": f"https://example.com/jobs/{ordinal}",
                "observed_at": "2026-09-05T08:00:00Z",
            }
            for ordinal, (snapshot, observation_id) in enumerate(
                zip(snapshots, observation_ids, strict=True), start=1
            )
        ]
        insight_values = list(
            _insight_values(
                scope,
                run_id,
                sources[0][0],
                snapshots[0][0],
                observation_ids[0],
            )
        )
        insight_values[4] = [sources[1][0], sources[0][0]]
        insight_values[5] = [snapshots[1][0], snapshots[0][0]]
        insight_values[6] = json.dumps(facts, ensure_ascii=False)
        insight_values[7] = '[{"text":"综合推断","basis_fact_ids":["f1","f2"]}]'
        first_insight = app.execute(CREATE_INSIGHT, insight_values).fetchone()
        insight_values[0], insight_values[2] = uuid4(), uuid4()
        second_insight = app.execute(CREATE_INSIGHT, insight_values).fetchone()
        retrieval_id = uuid4()
        app.execute(
            CREATE_RETRIEVAL,
            (
                retrieval_id,
                scope["owner"],
                uuid4(),
                scope["position"],
                scope["conversation"],
                scope["turn"],
                [second_insight[0], first_insight[0]],
                "e" * 64,
                "[]",
            ),
        )
        app.commit()
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select source_id from platform_hr.talent_insight_sources "
            "where insight_version_id=%s order by source_ordinal",
            (first_insight[0],),
        ).fetchall() == [(sources[1][0],), (sources[0][0],)]
        assert admin.execute(
            "select snapshot_id from platform_hr.talent_insight_snapshots "
            "where insight_version_id=%s order by snapshot_ordinal",
            (first_insight[0],),
        ).fetchall() == [(snapshots[1][0],), (snapshots[0][0],)]
        assert admin.execute(
            "select insight_version_id "
            "from platform_hr.position_insight_retrieval_versions "
            "where retrieval_id=%s order by insight_ordinal",
            (retrieval_id,),
        ).fetchall() == [(second_insight[0],), (first_insight[0],)]
