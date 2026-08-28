from __future__ import annotations

UPSTREAM_HTTP_CASE_IDS = (
    "health",
    "auth_missing",
    "auth_expired",
    "auth_wrong_audience",
    "auth_retired_kid",
    "auth_wrong_scope",
    "auth_wrong_task_binding",
    "create_idempotency_capability",
    "finite_event_pages_sequence_terminal",
    "follow_up",
    "cancel",
    "deadline",
    "action_proposal_execute",
)

BASE_UPSTREAM_HTTP_CASE_IDS = UPSTREAM_HTTP_CASE_IDS[:-1]
ACTION_CASE_ID = UPSTREAM_HTTP_CASE_IDS[-1]
