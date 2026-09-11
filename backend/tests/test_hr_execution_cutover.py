from __future__ import annotations

import pytest

from app.hr_agent.cutover import CutoverRejected, lock_admission
from hr_agent_support import hr_agent_database


class Cursor:
    def __init__(self, phase):
        self.phase = phase
        self.calls = []

    def execute(self, query, parameters=()):
        self.calls.append((query, parameters))
        return self

    def fetchone(self):
        if "to_regclass" in self.calls[-1][0]:
            return {"relation": "platform_control.hr_execution_cutover"}
        if "hr_execution_cutover" in self.calls[-1][0]:
            return None if self.phase is None else {"phase": self.phase, "epoch": 7}
        return {"pg_advisory_xact_lock": None}


@pytest.mark.parametrize(
    ("phase", "lane"),
    [(None, "legacy"), (None, "cloud"), ("legacy", "legacy"),
     ("draining_legacy", "legacy"), ("cloud", "cloud"),
     ("draining_cloud", "cloud")],
)
def test_admission_locks_before_reading_phase_and_allows_owned_lane(phase, lane):
    cursor = Cursor(phase)
    state = lock_admission(cursor, lane, continuing=phase is not None and phase.startswith("draining"))
    assert "pg_advisory_xact_lock" in cursor.calls[0][0]
    assert "to_regclass" in cursor.calls[1][0]
    assert "hr_execution_cutover" in cursor.calls[2][0]
    assert state is None or state.epoch == 7


@pytest.mark.parametrize(
    ("phase", "lane", "continuing"),
    [("draining_legacy", "legacy", False), ("cloud", "legacy", True),
     ("draining_cloud", "cloud", False), ("legacy", "cloud", True)],
)
def test_admission_rejects_new_work_during_drain_and_wrong_lane(phase, lane, continuing):
    with pytest.raises(CutoverRejected):
        lock_admission(Cursor(phase), lane, continuing=continuing)


def test_cutover_rejection_maps_through_old_domain_value_error_boundaries():
    error = CutoverRejected()
    assert isinstance(error, ValueError)
    assert error.http_status == 503


def test_real_postgres_requires_drain_and_preserves_exact_cloud_replay():
    from uuid import uuid4
    from app.control_plane.crypto import IdentityKeyring
    from app.execution_relay.content_crypto import ContentCodec
    from app.hr_agent.repository import HrAgentRepository
    from app.control_plane.migrate import migrate_control_database
    from pathlib import Path

    with hr_agent_database() as database:
        migrate_control_database(
            database.migrator_dsn,
            Path(__file__).parents[1] / "control_migrations" / "hr_web",
            owner_role="platform_control_owner",
        )
        with database.admin_connection() as c:
            c.execute("select platform_control.initialize_hr_execution_cutover_v102(%s)", (uuid4(),))
            drain_request = uuid4()
            first_drain = c.execute("select * from platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (drain_request,)).fetchone()
            other_job, hr_job = uuid4(), uuid4()
            c.execute("insert into platform_control.execution_jobs(job_id,run_id,agent_id,payload_ciphertext,encryption_key_version,status) values(%s,%s,'other-bot',%s,1,'queued'),(%s,%s,'hr-bot',%s,1,'queued')", (other_job, uuid4(), b'x', hr_job, uuid4(), b'x'))
        with database.admin_connection() as c:
            with pytest.raises(Exception, match="drain is incomplete"):
                c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))
        with database.admin_connection() as c:
            c.execute("delete from platform_control.execution_jobs where job_id=%s", (hr_job,))
            c.execute("select platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),))
            assert c.execute("select status from platform_control.execution_jobs where job_id=%s", (other_job,)).fetchone()[0] == "queued"
            replayed_drain = c.execute("select * from platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (drain_request,)).fetchone()
            assert replayed_drain[1:] == first_drain[1:]
        with database.admin_connection() as c:
            with pytest.raises(Exception, match="replay mismatch"):
                c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_cloud',%s)", (drain_request,))
        repo = HrAgentRepository(
            database.connection,
            ContentCodec(IdentityKeyring(1, "platform-content-encryption", {1: b"k" * 32})),
            settings=type("S", (), {"budget_profile": {
                "id": "test", "limits": {"model_calls": 3, "total_tokens": 1000, "active_seconds": 60},
                "reserve": {"model_calls": 1, "total_tokens": 100, "active_seconds": 5},
                "service_limits": {"model_calls": 6, "total_tokens": 2000, "active_seconds": 120},
                "max_output_tokens": 100, "input_target_tokens": 100, "input_trigger_tokens": 200,
            }, "provider_profile": {"context_window_tokens": 1000}})(),
            scope_validator=lambda *args: None,
        )
        owner, key = uuid4(), uuid4()
        body = {"thread_id": None, "text": "test", "objects": [], "references": [], "budget_profile": "test"}
        accepted = repo.submit(owner, body, key)
        with database.admin_connection() as c:
            c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_cloud',%s)", (uuid4(),))
        assert repo.submit(owner, body, key) == accepted
        with database.admin_connection() as c:
            with pytest.raises(Exception, match="drain is incomplete"):
                c.execute("select platform_control.transition_hr_execution_cutover_v102('legacy',%s)", (uuid4(),))
