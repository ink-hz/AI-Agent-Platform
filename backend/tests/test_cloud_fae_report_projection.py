import json
from datetime import UTC, datetime
from uuid import uuid4

from app.cloud_replica.crypto import FieldCipher
from app.cloud_replica.management_repository import ReplicaFaeReportRepository
from app.cloud_replica.models import FaeReportProjection, ReviewIssueProjection
from app.cloud_replica.sanitize import (
    SanitizationPolicy,
    sanitize_management_projection,
)
from app.cloud_replica.store import ReplicaStore

NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
IDENTITY_KEY = b"i" * 32


def test_report_finding_projection_removes_canonical_evidence_keys():
    raw = FaeReportProjection(
        projection_kind="fae_report_finding_projection",
        report_id="fae-topic-production-through-20260831",
        report_version=1,
        item_id="finding-1",
        occurred_at=NOW,
        payload={
            "finding_id": "finding-1",
            "title": "客户现场诊断",
            "evidence_refs": [
                {"kind": "session", "canonical_key": "fae:session-1", "label": "Session 1"}
            ],
        },
    )

    record = sanitize_management_projection(
        raw, SanitizationPolicy(version="test-v1"), IDENTITY_KEY
    )
    serialized = json.dumps(record, ensure_ascii=False)

    assert record["kind"] == "fae_report_finding_projection"
    evidence = record["payload"]["evidence_refs"][0]
    assert evidence["replica_key"]
    assert "canonical_key" not in evidence
    assert "fae:session-1" not in serialized


def test_report_projection_is_accepted_and_encrypted_by_cloud_store():
    raw = FaeReportProjection(
        projection_kind="fae_report_metric_projection",
        report_id="fae-topic-production-through-20260831",
        report_version=1,
        item_id="metric-1",
        occurred_at=NOW,
        payload={"metric_id": "metric-1", "label": "累计服务", "value": 692},
    )
    record = sanitize_management_projection(
        raw, SanitizationPolicy(version="test-v1"), IDENTITY_KEY
    )

    prepared = ReplicaStore(
        "postgresql://replica", cipher=FieldCipher(b"e" * 32)
    ).prepare_management(record)

    assert prepared.projection_kind == "fae_report_metric_projection"
    assert "累计服务" not in json.dumps(prepared.encrypted, ensure_ascii=False)


class _ReportReader(ReplicaFaeReportRepository):
    def __init__(self, records):
        self.records = records

    def _records(self, kind, agent_id=None):
        assert agent_id == "ai-fae-agent"
        return list(self.records.get(kind, ()))


def _record(kind, item_id, payload):
    return {
        "kind": kind,
        "key": "a" * 52,
        "agent_id": "ai-fae-agent",
        "report_id": "fae-topic-production-through-20260831",
        "report_version": 1,
        "item_id": item_id,
        "payload": payload,
        "occurred_at": "2026-08-31T08:00:00Z",
        "sanitizer_policy_version": "test-v1",
    }


def test_cloud_reader_reassembles_complete_report_projection():
    header_payload = {
        "schema_name": "fae.analysis-report",
        "schema_version": "1.0.0",
        "report_id": "fae-topic-production-through-20260831",
        "report_version": 1,
        "report_type": "topic",
        "status": "ready",
        "title": "FAE 生产成果",
        "period": {"start_at": "2026-07-01T00:00:00Z", "end_at": "2026-08-31T00:00:00Z"},
        "data_cutoff_at": "2026-08-31T00:00:00Z",
        "generated_at": "2026-08-31T08:00:00Z",
        "analysis_version": "v5",
        "source": {"agent_id": "ai-fae-agent", "source_kind": "fae", "environment": "production", "source_snapshot_at": "2026-08-31T00:00:00Z", "session_count": 692, "turn_count": 1492, "feedback_event_count": 5, "reviewed_session_count": 654},
        "summary": {"headline": "成果", "overview": "真实生产分析", "top_finding_ids": ["finding-1"], "top_recommendation_ids": ["rec-1"]},
        "cases": [], "artifact_digests": {}, "failure": None,
        "counts": {"metrics": 1, "findings": 1, "recommendations": 1},
    }
    records = {
        "fae_report_header_projection": [_record("fae_report_header_projection", "header", header_payload)],
        "fae_report_metric_projection": [_record("fae_report_metric_projection", "metric-1", {"metric_id": "metric-1"})],
        "fae_report_finding_projection": [_record("fae_report_finding_projection", "finding-1", {"finding_id": "finding-1"})],
        "fae_report_recommendation_projection": [_record("fae_report_recommendation_projection", "rec-1", {"recommendation_id": "rec-1"})],
    }

    report = _ReportReader(records).get_report(
        "fae-topic-production-through-20260831", 1
    )

    assert report["title"] == "FAE 生产成果"
    assert report["metrics"][0]["metric_id"] == "metric-1"
    assert report["findings"][0]["finding_id"] == "finding-1"


def test_detailed_fae_issue_projection_keeps_repair_chain_and_pseudonymizes_session():
    issue_id, link_id = uuid4(), uuid4()
    raw = ReviewIssueProjection(
        issue_id=issue_id, agent_id="ai-fae-agent", status="actionable",
        priority="P1", title="资料缺口", failure_layer="coverage",
        owner_display="FAE", linked_turn_count=1, linked_turn_keys=("fae:turn-1",),
        created_at=NOW, updated_at=NOW, scope_valid=True,
        detail_schema_version=1, origin_turn_key="fae:turn-1",
        root_cause="资料未覆盖", impact_scope="现场排障", secondary_layers=(),
        links=({"id": str(link_id), "source_turn_key": "fae:turn-1", "source_session_key": "fae:session-1", "source_question": "怎么处理", "source_answer": "旧回答", "active": True, "link_role": "primary"},),
        evidence=({"id": str(uuid4()), "evidence_type": "merge", "reference": "abc123", "verification_status": "verified", "observed_at": NOW.isoformat()},),
        replays=({"id": str(uuid4()), "issue_link_id": str(link_id), "runtime_gate": "passed", "semantic_verdict": "passed", "answer": "修复后回答"},),
        events=({"event_type": "issue_closed", "actor": "fae:owner", "reason": "复审通过", "created_at": NOW.isoformat()},),
        progress={"issue_id": str(issue_id), "status": "closed", "missing_gates": [], "replay_passed_turns": 1, "replay_required_turns": 1, "reopened": False},
    )

    record = sanitize_management_projection(raw, SanitizationPolicy(version="test-v1"), IDENTITY_KEY)
    serialized = json.dumps(record, ensure_ascii=False, default=str)

    assert record["detail_schema_version"] == 1
    assert record["links"][0]["source_session_key"] != "fae:session-1"
    assert record["links"][0]["source_answer"] == "旧回答"
    assert record["progress"]["status"] == "closed"
    assert "fae:session-1" not in serialized
