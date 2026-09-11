"""Small transactional boundary for the HR execution cutover."""

from __future__ import annotations

from dataclasses import dataclass

from .types import HrAgentProblem


ADVISORY_LOCK_KEY = 0x485245584954  # "HREXIT"


class CutoverRejected(HrAgentProblem, ValueError):
    def __init__(self):
        super().__init__({
            "code": "hr_execution_admission_paused",
            "message": "HR execution admission is paused",
            "retryable": True,
            "details": {},
        }, 503)


@dataclass(frozen=True)
class CutoverState:
    phase: str
    epoch: int


def lock_state(cursor) -> CutoverState | None:
    """Take the shared cutover lock before any domain row lock."""
    cursor.execute("SELECT pg_advisory_xact_lock_shared(%s)", (ADVISORY_LOCK_KEY,))
    relation = cursor.execute(
        "SELECT to_regclass('platform_control.hr_execution_cutover') AS relation"
    ).fetchone()
    if relation is None or relation["relation"] is None:
        return None
    result = cursor.execute(
        "SELECT phase,epoch FROM platform_control.hr_execution_cutover WHERE singleton"
    )
    row = result.fetchone()
    return None if row is None else CutoverState(row["phase"], row["epoch"])


def require_lane(state: CutoverState | None, lane: str, *, continuing: bool = False):
    """Validate a state already locked by ``lock_state``."""
    if state is None:
        return
    allowed = state.phase == lane or (
        continuing and state.phase == f"draining_{lane}"
    )
    if not allowed:
        raise CutoverRejected()


def lock_admission(cursor, lane: str, *, continuing: bool = False) -> CutoverState | None:
    """Serialize an admission with activation/transition before domain locks.

    A missing singleton preserves the behavior predating migration 102. Once
    initialized, a drain accepts continuations owned by that lane, but no new
    root admission. Work from the other lane is never adopted.
    """
    if lane not in {"legacy", "cloud"} or type(continuing) is not bool:
        raise ValueError("HR cutover admission invalid")
    state = lock_state(cursor)
    require_lane(state, lane, continuing=continuing)
    return state


def verify_candidate_continuation(cursor, owner_id, proof) -> bool:
    """Verify an internal candidate child against its immutable generation."""
    if not isinstance(proof, tuple) or len(proof) != 3:
        return False
    item_id, generation, attachment_id = proof
    row = cursor.execute(
        "SELECT 1 FROM platform_hr_agent.candidate_intake_items "
        "WHERE owner_id=%s AND item_id=%s AND generation=%s AND attachment_id=%s "
        "AND state IN ('queued','parsing','profiling') FOR SHARE",
        (owner_id, item_id, generation, attachment_id),
    ).fetchone()
    return row is not None
