from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def test_freshness_boundaries_are_exact() -> None:
    from app.control_plane.directory import evaluate_directory_freshness
    from app.control_plane.models import DirectoryFreshness

    now = datetime(2026, 8, 13, 8, tzinfo=timezone.utc)
    assert evaluate_directory_freshness(None, now) is DirectoryFreshness.HARD_STALE
    assert evaluate_directory_freshness(now - timedelta(hours=7, minutes=59, seconds=59), now) is DirectoryFreshness.FRESH
    assert evaluate_directory_freshness(now - timedelta(hours=8), now) is DirectoryFreshness.WARNING
    assert evaluate_directory_freshness(now - timedelta(hours=23, minutes=59, seconds=59), now) is DirectoryFreshness.WARNING
    assert evaluate_directory_freshness(now - timedelta(hours=24), now) is DirectoryFreshness.HARD_STALE
    assert evaluate_directory_freshness(now - timedelta(hours=24, seconds=1), now) is DirectoryFreshness.HARD_STALE


def test_freshness_rejects_naive_or_reverse_time() -> None:
    from app.control_plane.directory import evaluate_directory_freshness

    aware = datetime(2026, 8, 13, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="directory time invalid"):
        evaluate_directory_freshness(aware.replace(tzinfo=None), aware)
    with pytest.raises(ValueError, match="directory time invalid"):
        evaluate_directory_freshness(aware + timedelta(seconds=1), aware)


def test_freshness_service_uses_database_time_and_local_departure_override() -> None:
    from app.control_plane.directory import DirectoryFreshnessService
    from app.control_plane.models import DirectoryFreshness

    now = datetime(2026, 8, 13, 8, tzinfo=timezone.utc)

    class Repository:
        def read_directory_clock(self):
            return now, now - timedelta(hours=9), "generation-safe-id", "failed"

        def member_directory_signal(self, internal_user_id):
            assert internal_user_id == "internal-safe-id"
            return True, True

    status = DirectoryFreshnessService(Repository()).evaluate()
    assert status.freshness is DirectoryFreshness.WARNING
    assert status.warning is True
    assert status.deny_member_access is False
    assert status.generation_id == "generation-safe-id"
    assert status.last_run_result == "failed"
    signal = DirectoryFreshnessService(Repository()).member_access_signal(
        "internal-safe-id"
    )
    assert signal.allowed is False
    assert signal.reason.value == "locally_invalidated"


def test_hard_stale_is_a_typed_member_denial_signal() -> None:
    from app.control_plane.directory import DirectoryFreshnessService
    from app.control_plane.models import DirectoryFreshness

    now = datetime(2026, 8, 13, 8, tzinfo=timezone.utc)

    class Repository:
        def read_directory_clock(self):
            return now, now - timedelta(hours=24), None, None

        def member_directory_signal(self, internal_user_id):
            return True, False

    service = DirectoryFreshnessService(Repository())
    assert service.evaluate().freshness is DirectoryFreshness.HARD_STALE
    signal = service.member_access_signal("member")
    assert signal.allowed is False
    assert signal.reason.value == "directory_hard_stale"
