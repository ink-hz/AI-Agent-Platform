from dataclasses import replace
from uuid import UUID

import pytest

from app.review.models import IssueRecord, LinkGate
from app.review.state import calculate_progress


ISSUE_ID = UUID("00000000-0000-0000-0000-000000000001")
LINK_ID = UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def closed_snapshot():
    issue = IssueRecord(
        id=ISSUE_ID,
        agent_id="ai-fae-agent",
        title="系列事实错误",
        priority="P1",
        failure_layer="capability_evidence",
        root_cause="结构化事实压过直接证据",
        impact_scope="Gemini 330 系列问答",
        owner="fae:alice",
        fix_ready=True,
        verified_merge=True,
        verified_deployment=True,
        merge_ancestor=True,
    )
    link = LinkGate(
        id=LINK_ID,
        active=True,
        runtime_gate_passed=True,
        build_identity_matches=True,
        model_echo_available=True,
        actual_model_matches=True,
        semantic_verdict="passed",
        review_method="codex",
        reviewer="codex",
        review_reason="事实、证据和表达均通过",
    )
    return issue, [link]


@pytest.mark.parametrize(
    ("mutator", "status", "missing"),
    [
        (lambda i, links: (replace(i, root_cause=""), links), "pending_triage", "root_cause"),
        (lambda i, links: (replace(i, owner=None), links), "pending_triage", "owner"),
        (
            lambda i, links: (replace(i, verified_merge=False), links),
            "awaiting_merge",
            "verified_merge",
        ),
        (
            lambda i, links: (replace(i, verified_deployment=False), links),
            "awaiting_deploy",
            "verified_deployment",
        ),
        (
            lambda i, links: (replace(i, merge_ancestor=False), links),
            "awaiting_deploy",
            "merge_ancestor",
        ),
        (
            lambda i, links: (
                i,
                [replace(links[0], runtime_gate_passed=False)],
            ),
            "awaiting_replay",
            "qualified_replay",
        ),
        (
            lambda i, links: (
                i,
                [replace(links[0], build_identity_matches=False)],
            ),
            "awaiting_replay",
            "build_identity_mismatch",
        ),
        (
            lambda i, links: (
                i,
                [replace(links[0], model_echo_available=False)],
            ),
            "awaiting_replay",
            "model_echo_unavailable",
        ),
        (
            lambda i, links: (
                i,
                [replace(links[0], actual_model_matches=False)],
            ),
            "awaiting_replay",
            "actual_model_mismatch",
        ),
        (
            lambda i, links: (
                i,
                [replace(links[0], semantic_verdict="pending")],
            ),
            "awaiting_review",
            "semantic_review",
        ),
        (
            lambda i, links: (i, [replace(links[0], reviewer=None)]),
            "awaiting_review",
            "reviewer",
        ),
    ],
)
def test_each_missing_gate_prevents_closed(
    closed_snapshot, mutator, status, missing
):
    issue, links = mutator(*closed_snapshot)

    progress = calculate_progress(issue, links)

    assert progress.status == status
    assert missing in progress.missing_gates


def test_new_active_link_reopens_closed_issue(closed_snapshot):
    issue, links = closed_snapshot
    issue = replace(issue, previous_status="closed")
    links.append(
        LinkGate(
            id=UUID("00000000-0000-0000-0000-000000000003"),
            active=True,
        )
    )

    progress = calculate_progress(issue, links)

    assert progress.status == "awaiting_replay"
    assert progress.reopened is True
    assert progress.replay_passed_turns == 1
    assert progress.replay_required_turns == 2


@pytest.mark.parametrize(
    "disposition",
    ["duplicate", "not_actionable", "wont_fix"],
)
def test_non_actionable_dispositions_are_never_closed(closed_snapshot, disposition):
    issue, links = closed_snapshot

    progress = calculate_progress(replace(issue, disposition=disposition), links)

    assert progress.status == disposition
    assert progress.replay_passed_turns == 0


def test_fixing_precedes_merge_when_fix_not_ready(closed_snapshot):
    issue, links = closed_snapshot
    issue = replace(issue, fix_ready=False, verified_merge=False)

    progress = calculate_progress(issue, links)

    assert progress.status == "fixing"
    assert progress.missing_gates == ["fix_ready"]
