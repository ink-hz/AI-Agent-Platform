from uuid import uuid4

from app.review.progress_projection import progress_from_detail


def test_progress_from_detail_preserves_closed_evidence_gates():
    issue_id, link_id = uuid4(), uuid4()
    detail = {
        "issue": {
            "id": issue_id, "agent_id": "ai-fae-agent", "title": "资料缺失",
            "priority": "P1", "failure_layer": "coverage", "secondary_layers": [],
            "root_cause": "知识缺口", "impact_scope": "FAE", "owner": "FAE",
            "fix_ready_at": "2026-08-20T00:00:00Z", "disposition": "actionable",
            "row_version": 3,
        },
        "links": [{"id": link_id, "active": True, "link_role": "primary"}],
        "evidence": [
            {"evidence_type": "merge", "verification_status": "verified", "verification_details": {}, "observed_at": "2026-08-21T00:00:00Z"},
            {"evidence_type": "deployment", "verification_status": "verified", "verification_details": {"contains_merge": True, "deployment_sha": "a" * 40}, "observed_at": "2026-08-22T00:00:00Z"},
        ],
        "replays": [{
            "issue_link_id": link_id, "actual_git_sha": "a" * 40,
            "started_at": "2026-08-23T00:00:00Z", "runtime_gate": "passed",
            "runtime_failure_reason": "", "done": {"loop": {"provider_model_echo": {"complete": True, "consistent": True}}},
            "actual_model": "claude-opus-5", "configured_model": "claude-opus-5",
            "semantic_verdict": "passed", "review_method": "human_fae",
            "reviewer": "fae:owner", "review_reason": "verified",
        }],
        "events": [],
    }

    progress = progress_from_detail(detail)

    assert progress.status == "closed"
    assert progress.missing_gates == []
    assert progress.replay_passed_turns == 1
