from __future__ import annotations

# ruff: noqa: F811
import json
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest
from app.hr.panorama_models import (
    CreateProductionBatch,
    CreatePublicJobSnapshot,
    CreateSourceCollectionAttempt,
    CreateTalentInsightVersion,
    CreateTalentSource,
    PublishPanoramaReport,
)
from app.hr.panorama_repository import PanoramaRepository, PanoramaUnavailable
from test_control_plane_migration import control_database  # noqa: F401
from test_hr_panorama_database import _seed_owner_scope

NOW = datetime(2026, 9, 6, 8, tzinfo=timezone.utc)
TRANSITION_BATCH = (
    "select result.* from platform_hr.transition_panorama_production_batch_v80("
    "%s,%s,%s,%s,%s,%s::jsonb) result"
)


def _seed_source(repository: PanoramaRepository, owner_id):
    source_id = uuid4()
    repository.create_source(
        CreateTalentSource(
            source_id=source_id,
            owner_id=owner_id,
            client_request_id=uuid4(),
            company_key=f"company-{source_id.hex[:8]}",
            canonical_name="测试公司",
            aliases=(),
            approved_urls=("https://example.com/jobs",),
            active=True,
        )
    )
    return source_id


def _transition(app, owner_id, batch_id, version, state, failures=None):
    row = app.execute(
        TRANSITION_BATCH,
        (
            owner_id,
            batch_id,
            version,
            state,
            "production_failed" if state == "failed" else None,
            json.dumps(failures or {}),
        ),
    ).fetchone()
    return row[9]


def _seed_grounded_insight(admin, owner_id, source_id, batch_id):
    snapshot_id = uuid4()
    insight_id = uuid4()
    observation_id = uuid4()
    source_url = "https://example.com/jobs/structure"
    admin.execute(
        "insert into platform_hr.public_job_snapshots("
        "snapshot_id,owner_internal_user_id,origin_client_request_id,"
        "production_batch_id,source_id,public_job_key,title,location,"
        "duty_excerpt,requirement_excerpt,source_url,observed_at,"
        "content_sha256,status) values ("
        "%s,%s,%s,%s,%s,'structure-1','高级结构工程师','深圳',"
        "'负责精密结构研发','五年以上量产经验',%s,%s,%s,'open')",
        (
            snapshot_id,
            owner_id,
            uuid4(),
            batch_id,
            source_id,
            source_url,
            NOW,
            "a" * 64,
        ),
    )
    admin.execute(
        "insert into platform_hr.public_job_snapshot_requests("
        "owner_internal_user_id,client_request_id,observation_id,"
        "requested_snapshot_id,run_id,source_id,public_job_key,"
        "result_snapshot_id,source_url,observed_at,status,payload_sha256,"
        "production_batch_id) values ("
        "%s,%s,%s,%s,null,%s,'structure-1',%s,%s,%s,'open',%s,%s)",
        (
            owner_id,
            observation_id,
            observation_id,
            snapshot_id,
            source_id,
            snapshot_id,
            source_url,
            NOW,
            b"a" * 32,
            batch_id,
        ),
    )
    facts = [
        {
            "fact_id": "fact-1",
            "text": "测试公司公开招聘高级结构工程师",
            "snapshot_id": str(snapshot_id),
            "observation_id": str(observation_id),
            "source_url": source_url,
            "observed_at": NOW.isoformat(),
        }
    ]
    admin.execute(
        "insert into platform_hr.talent_insight_versions("
        "insight_version_id,owner_internal_user_id,client_request_id,"
        "production_batch_id,version_number,selected_source_ids,snapshot_ids,"
        "facts,inferences,unknowns,direction_clusters,summary,agent_id,"
        "model_version) values ("
        "%s,%s,%s,%s,1,%s::uuid[],%s::uuid[],%s::jsonb,%s::jsonb,%s::jsonb,"
        "%s::jsonb,'结构人才投入分析','hr-intelligence-producer',"
        "'configured-model-v1')",
        (
            insight_id,
            owner_id,
            uuid4(),
            batch_id,
            [source_id],
            [snapshot_id],
            json.dumps(facts, ensure_ascii=False),
            json.dumps(
                [{"text": "结构研发投入持续", "basis_fact_ids": ["fact-1"]}],
                ensure_ascii=False,
            ),
            json.dumps([{"text": "实际 HC 未公开"}], ensure_ascii=False),
            json.dumps({"结构": 1}, ensure_ascii=False),
        ),
    )
    admin.commit()
    return insight_id


@pytest.mark.postgres
def test_atomic_publication_is_shared_and_retains_last_known_good(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Panorama Producer Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    source_id = _seed_source(repository, scope["owner"])
    batch_id = uuid4()
    batch = repository.create_production_batch(
        CreateProductionBatch(
            batch_id=batch_id,
            owner_id=scope["owner"],
            client_request_id=uuid4(),
            selected_source_ids=(source_id,),
            trigger_kind="schedule",
            analyzer_version="configured-model-v1",
        )
    )
    with psycopg.connect(environment["admin"]) as admin:
        stored_row_version = admin.execute(
            "select row_version from platform_hr.panorama_production_batches "
            "where batch_id=%s",
            (batch_id,),
        ).fetchone()[0]
    assert stored_row_version == batch.row_version

    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        running = _transition(
            app, scope["owner"], batch_id, batch.row_version, "running"
        )
    repository.record_source_attempt(
        CreateSourceCollectionAttempt(
            attempt_id=uuid4(),
            batch_id=batch_id,
            owner_id=scope["owner"],
            source_id=source_id,
            source_url="https://example.com/jobs",
            attempt_number=1,
            state="succeeded",
            error_code=None,
            evidence_sha256="b" * 64,
            evidence_locator="sha256/bb/" + "b" * 64,
            evidence_mime="text/html",
            evidence_size_bytes=1024,
            normalized_job_count=1,
            observed_at=NOW,
        )
    )
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        _transition(app, scope["owner"], batch_id, running, "analyzing")
    with psycopg.connect(environment["admin"]) as admin:
        insight_id = _seed_grounded_insight(admin, scope["owner"], source_id, batch_id)

    coverage = (
        {
            "source_id": str(source_id),
            "state": "succeeded",
            "observed_at": NOW.isoformat(),
            "source_urls": ["https://example.com/jobs"],
            "job_count": 1,
        },
    )
    published = repository.publish_production_report(
        PublishPanoramaReport(
            publication_id=uuid4(),
            client_request_id=uuid4(),
            batch_id=batch_id,
            insight_version_id=insight_id,
            coverage_state="complete",
            source_coverage=coverage,
        )
    )

    assert repository.current_publication() == published

    failed_batch_id = uuid4()
    failed_batch = repository.create_production_batch(
        CreateProductionBatch(
            batch_id=failed_batch_id,
            owner_id=scope["owner"],
            client_request_id=uuid4(),
            selected_source_ids=(source_id,),
            trigger_kind="schedule",
            analyzer_version="configured-model-v1",
        )
    )
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        failed_running = _transition(
            app,
            scope["owner"],
            failed_batch_id,
            failed_batch.row_version,
            "running",
        )
        _transition(
            app,
            scope["owner"],
            failed_batch_id,
            failed_running,
            "analyzing",
        )
    with pytest.raises(PanoramaUnavailable):
        repository.publish_production_report(
            PublishPanoramaReport(
                publication_id=uuid4(),
                client_request_id=uuid4(),
                batch_id=failed_batch_id,
                insight_version_id=uuid4(),
                coverage_state="partial",
                source_coverage=(
                    {
                        "source_id": str(source_id),
                        "state": "failed",
                        "observed_at": NOW.isoformat(),
                        "source_urls": ["https://example.com/jobs"],
                        "job_count": 0,
                        "error_code": "source_timeout",
                    },
                ),
            )
        )

    assert repository.current_publication() == published


@pytest.mark.postgres
def test_repository_persists_raw_jobs_and_ai_analysis_as_separate_production_records(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        scope = _seed_owner_scope(admin, "Panorama Production Records Owner")
    repository = PanoramaRepository(environment["urls"]["platform_control_app"])
    source_id = _seed_source(repository, scope["owner"])
    batch = repository.create_production_batch(
        CreateProductionBatch(
            batch_id=uuid4(),
            owner_id=scope["owner"],
            client_request_id=uuid4(),
            selected_source_ids=(source_id,),
            trigger_kind="schedule",
            analyzer_version="configured-model-v1",
        )
    )
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        running_version = _transition(
            app, scope["owner"], batch.batch_id, batch.row_version, "running"
        )
    repository.record_source_attempt(
        CreateSourceCollectionAttempt(
            attempt_id=uuid4(),
            batch_id=batch.batch_id,
            owner_id=scope["owner"],
            source_id=source_id,
            source_url="https://example.com/jobs",
            attempt_number=1,
            state="succeeded",
            error_code=None,
            evidence_sha256="b" * 64,
            evidence_locator="sha256/bb/" + "b" * 64,
            evidence_mime="text/html",
            evidence_size_bytes=1024,
            normalized_job_count=1,
            observed_at=NOW,
        )
    )
    observation_id = uuid4()
    snapshot = repository.create_snapshot(
        CreatePublicJobSnapshot(
            snapshot_id=uuid4(),
            owner_id=scope["owner"],
            client_request_id=observation_id,
            run_id=None,
            source_id=source_id,
            public_job_key="structure-production-1",
            title="高级结构工程师",
            location="深圳",
            duty_excerpt="负责精密结构研发",
            requirement_excerpt="五年以上量产经验",
            source_url="https://example.com/jobs/structure-production-1",
            observed_at=NOW,
            content_sha256="c" * 64,
            status="open",
            production_batch_id=batch.batch_id,
        )
    )
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        _transition(app, scope["owner"], batch.batch_id, running_version, "analyzing")
    fact = {
        "fact_id": "fact-production-1",
        "text": "公开招聘高级结构工程师",
        "snapshot_id": str(snapshot.snapshot_id),
        "observation_id": str(observation_id),
        "source_url": snapshot.source_url,
        "observed_at": snapshot.observed_at.isoformat(),
    }
    insight = repository.create_insight(
        CreateTalentInsightVersion(
            insight_version_id=uuid4(),
            owner_id=scope["owner"],
            client_request_id=uuid4(),
            run_id=None,
            selected_source_ids=(source_id,),
            snapshot_ids=(snapshot.snapshot_id,),
            facts=(fact,),
            inferences=(
                {"text": "结构投入明确", "basis_fact_ids": ("fact-production-1",)},
            ),
            unknowns=({"text": "实际 HC 未公开"},),
            direction_clusters={"结构": 1},
            summary="结构投入分析",
            source_conversation_id=None,
            source_turn_id=None,
            agent_id="hr-intelligence-producer",
            model_version="configured-model-v1",
            production_batch_id=batch.batch_id,
        )
    )

    assert snapshot.production_batch_id == batch.batch_id
    assert insight.production_batch_id == batch.batch_id
    assert insight.facts[0]["snapshot_id"] == str(snapshot.snapshot_id)
