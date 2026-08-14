from pathlib import Path


RUNBOOK = Path(__file__).parents[2] / "docs/runbooks/platform-admin.md"


def test_rollback_gate_counts_platform_administrators_across_every_status() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    gate = runbook.split("## Mandatory gate before rollback", 1)[1].split(
        "## Evidence record", 1
    )[0]

    assert "where role = 'platform_admin'" in gate
    assert "status = 'active'" not in gate
    assert "zero active" not in gate.lower()
    assert "display_name" not in gate
    assert "internal_user_id" not in gate
    assert "ADMIN_ROLLBACK_ZERO_GATE_OK" in gate
