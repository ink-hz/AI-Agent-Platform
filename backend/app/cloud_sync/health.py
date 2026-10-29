"""Health derives from source observations; process liveness is insufficient."""
from __future__ import annotations

from datetime import datetime


def healthy_sources(states, profiles, *, now: datetime, max_age_seconds: int) -> bool:
    if not profiles:
        return False
    for profile in profiles:
        state = states.get(profile.agent_id, {})
        success = state.get("last_source_success_at")
        failure = state.get("last_failure_at")
        if (state.get("publisher") != "cloud" or state.get("mode") != "cloud_direct"
                or not isinstance(success, datetime) or success.tzinfo is None
                or not 0 <= (now - success).total_seconds() <= max_age_seconds
                or (failure is not None and failure >= success)):
            return False
    return True
