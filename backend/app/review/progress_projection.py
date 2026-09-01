from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from .models import IssueProgress, IssueRecord, LinkGate
from .state import calculate_progress


def progress_from_detail(detail: Mapping[str, Any]) -> IssueProgress:
    raw_issue = detail["issue"]
    evidence = detail["evidence"]
    verified_merges = [
        item
        for item in evidence
        if item["evidence_type"] == "merge"
        and item["verification_status"] == "verified"
    ]
    deployments = [
        item
        for item in evidence
        if item["evidence_type"] == "deployment"
        and item["verification_status"] == "verified"
        and bool((item.get("verification_details") or {}).get("contains_merge"))
    ]
    latest_deployment = deployments[-1] if deployments else None
    deployment_details = (
        latest_deployment.get("verification_details") or {}
        if latest_deployment
        else {}
    )
    deployment_sha = deployment_details.get("deployment_sha", "")
    deployment_at = latest_deployment.get("observed_at") if latest_deployment else None
    previous_status = next(
        (
            (event.get("after") or {}).get("status")
            for event in reversed(detail["events"])
            if (event.get("after") or {}).get("status")
        ),
        None,
    )
    issue = IssueRecord(
        id=raw_issue["id"],
        agent_id=raw_issue["agent_id"],
        title=raw_issue["title"],
        priority=raw_issue["priority"],
        failure_layer=raw_issue["failure_layer"],
        secondary_layers=tuple(raw_issue["secondary_layers"] or ()),
        root_cause=raw_issue["root_cause"],
        impact_scope=raw_issue["impact_scope"],
        owner=raw_issue["owner"],
        fix_ready=raw_issue["fix_ready_at"] is not None,
        verified_merge=bool(verified_merges),
        verified_deployment=bool(deployments),
        merge_ancestor=bool(deployments),
        disposition=raw_issue["disposition"],
        previous_status=previous_status,
        row_version=int(raw_issue["row_version"]),
    )
    latest_by_link: dict[UUID, dict] = {}
    for replay in detail["replays"]:
        latest_by_link[replay["issue_link_id"]] = replay
    links: list[LinkGate] = []
    for raw_link in detail["links"]:
        replay = latest_by_link.get(raw_link["id"])
        deployed_replay = bool(
            replay
            and deployment_sha
            and replay["actual_git_sha"] == deployment_sha
            and deployment_at is not None
            and replay["started_at"] >= deployment_at
        )
        echo = ((replay or {}).get("done") or {}).get("loop", {}).get(
            "provider_model_echo", {}
        )
        links.append(
            LinkGate(
                id=raw_link["id"],
                active=bool(raw_link["active"]),
                link_role=raw_link["link_role"],
                runtime_gate_passed=bool(
                    replay and replay["runtime_gate"] == "passed" and deployed_replay
                ),
                runtime_failure_reason=(replay or {}).get(
                    "runtime_failure_reason", ""
                ),
                build_identity_matches=(
                    None
                    if replay is None
                    else replay["actual_git_sha"] == deployment_sha
                ),
                model_echo_available=(
                    None
                    if replay is None
                    else bool(
                        echo.get("complete")
                        and echo.get("consistent")
                        and replay["actual_model"]
                    )
                ),
                actual_model_matches=(
                    None
                    if replay is None
                    else bool(
                        replay["actual_model"]
                        and replay["actual_model"] == replay["configured_model"]
                    )
                ),
                semantic_verdict=(replay or {}).get("semantic_verdict", "pending"),
                review_method=(replay or {}).get("review_method"),
                reviewer=(replay or {}).get("reviewer"),
                review_reason=(replay or {}).get("review_reason", ""),
            )
        )
    return calculate_progress(issue, links)
