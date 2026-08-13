from __future__ import annotations

import re
from typing import Any

from app.config import Config, is_cloud_mode
from app.review.service import UnavailableReviewService


_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_RELEASE_NAME = "orbbec-agent-platform"


def build_public_platform_health() -> dict[str, str]:
    """The unauthenticated liveness contract intentionally reveals one fact."""
    return {"status": "ok"}


def build_deployment_status(app, config: Config) -> dict[str, Any]:
    if is_cloud_mode(config):
        if app.state.replica_repository is None:
            return {
                "mode": "cloud-replica",
                "read_only": True,
                "auth": config.cloud_auth_mode,
                "freshness": "unavailable",
                "last_success_at": None,
            }
        return app.state.replica_repository.deployment_status()
    return {
        "mode": "local",
        "read_only": False,
        "auth": "local",
        "freshness": "current",
        "last_success_at": None,
    }


def build_detailed_platform_health(
    app, config: Config, *, release_sha: str | None
) -> dict[str, Any]:
    """Build the owner-only health view from live application services."""
    selected_sha = (
        release_sha.lower()
        if isinstance(release_sha, str)
        and _FULL_GIT_SHA.fullmatch(release_sha.lower())
        else None
    )
    registered = [
        {
            "id": agent.id,
            "name": agent.name,
            "status": agent.status,
            "environment": agent.env,
            "version": agent.version,
        }
        for agent in app.state.repo.list_agents()
    ]
    local = app.state.cluster_monitor.snapshot().model_dump(mode="json")
    remote = app.state.remote_health_monitor.snapshot().model_dump(mode="json")
    runtime = [
        item.model_dump(mode="json") for item in app.state.health_cache.all()
    ]
    deployment = build_deployment_status(app, config)
    return {
        "status": "ok",
        "build": {
            "available": selected_sha is not None,
            "release_name": _RELEASE_NAME,
            "git_sha": selected_sha,
        },
        "release": {
            "name": _RELEASE_NAME,
            "version": app.version,
            "git_sha": selected_sha,
        },
        "deployment": deployment,
        "dependencies": {
            "registry": {"status": "ok", "agent_count": len(registered)},
            "cluster_source": local["source"],
            "remote_agents": {
                "healthy": remote["healthy"],
                "checked_at": remote["checked_at"],
                "error": remote["error"],
            },
            "replica": deployment,
            "services": {
                "review": not isinstance(
                    app.state.review_service, UnavailableReviewService
                ),
                "operations": app.state.operations_service is not None,
                "attachments": app.state.attachment_service is not None,
            },
        },
        "agents": {
            "registered": registered,
            "runtime": runtime,
            "local": local,
            "remote": remote,
        },
    }
