from __future__ import annotations

import json

from app.hr.import_cli import inspect_snapshot
from test_hr_position_importers import _job, _snapshot


def test_import_cli_inspection_emits_safe_snapshot_summary_only() -> None:
    summary = inspect_snapshot(_snapshot(_job(requirement="PRIVATE INTERNAL TEXT")))

    assert summary == {
        "version": "20260904T010000Z-a1b2c3",
        "last_successful_sync_at": "2026-09-04T01:00:00+00:00",
        "job_count": 1,
        "statuses": {"active": 1},
    }
    assert "PRIVATE INTERNAL TEXT" not in json.dumps(summary)
