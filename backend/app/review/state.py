from __future__ import annotations

from .models import IssueProgress, IssueRecord, LinkGate


def _nonempty(value: str | None) -> bool:
    return bool(value and value.strip())


def calculate_progress(
    issue: IssueRecord,
    links: list[LinkGate],
) -> IssueProgress:
    """Derive issue status exclusively from stored evidence gates."""
    if issue.disposition != "actionable":
        return IssueProgress(issue_id=issue.id, status=issue.disposition)

    active_links = [link for link in links if link.active]
    required = len(active_links)
    passed = sum(link.runtime_gate_passed for link in active_links)

    triage_missing: list[str] = []
    if issue.failure_layer is None:
        triage_missing.append("failure_layer")
    if not _nonempty(issue.root_cause):
        triage_missing.append("root_cause")
    if not _nonempty(issue.owner):
        triage_missing.append("owner")
    if triage_missing:
        status = "pending_triage"
        missing = triage_missing
    elif not issue.verified_merge:
        if issue.fix_ready:
            status = "awaiting_merge"
            missing = ["verified_merge"]
        else:
            status = "fixing"
            missing = ["fix_ready"]
    elif not issue.verified_deployment:
        status = "awaiting_deploy"
        missing = ["verified_deployment"]
    elif not issue.merge_ancestor:
        status = "awaiting_deploy"
        missing = ["merge_ancestor"]
    else:
        runtime_missing: list[str] = []
        if not active_links:
            runtime_missing.append("linked_turn")
        for link in active_links:
            if link.build_identity_matches is False:
                runtime_missing.append("build_identity_mismatch")
            elif link.model_echo_available is False:
                runtime_missing.append("model_echo_unavailable")
            elif link.actual_model_matches is False:
                runtime_missing.append("actual_model_mismatch")
            elif not link.runtime_gate_passed:
                runtime_missing.append(
                    link.runtime_failure_reason or "qualified_replay"
                )
        if runtime_missing:
            status = "awaiting_replay"
            missing = list(dict.fromkeys(runtime_missing))
        else:
            review_missing: list[str] = []
            for link in active_links:
                if link.semantic_verdict != "passed":
                    review_missing.append("semantic_review")
                    continue
                if link.review_method not in {"codex", "human_fae"}:
                    review_missing.append("review_method")
                if not _nonempty(link.reviewer):
                    review_missing.append("reviewer")
                if not _nonempty(link.review_reason):
                    review_missing.append("review_reason")
            if review_missing:
                status = "awaiting_review"
                missing = list(dict.fromkeys(review_missing))
            else:
                status = "closed"
                missing = []

    return IssueProgress(
        issue_id=issue.id,
        status=status,
        missing_gates=missing,
        replay_passed_turns=passed,
        replay_required_turns=required,
        reopened=issue.previous_status == "closed" and status != "closed",
    )
