from pathlib import Path


ROOT = Path(__file__).parents[2]
ACCEPTANCE = ROOT / "deploy" / "cloud" / "acceptance.sh"
RUNBOOK = ROOT / "docs" / "runbooks" / "cloud-platform.md"


def test_acceptance_gate_names_all_eighteen_criteria_and_has_stable_success():
    script = ACCEPTANCE.read_text(encoding="utf-8")

    for number in range(1, 19):
        assert f"criterion {number:02d}" in script
    assert 'CLOUD_PLATFORM_ACCEPTANCE_OK release=$release_sha criteria=18' in script
    assert "set -x" not in script
    assert "BatchMode=yes" in script


def test_acceptance_stops_on_local_verification_and_requires_private_evidence():
    script = ACCEPTANCE.read_text(encoding="utf-8")

    assert "python -m pytest -q" in script
    assert "npm test" in script
    assert "npm run build" in script
    assert "bash -n" in script
    assert "git diff --check" in script
    assert "CLOUD_ACCEPTANCE_EVIDENCE_FILE" in script
    assert "mode_600_file" in script
    assert "CANARY_ABSENT" in script
    assert "BACKFILL_RECONCILED" in script
    assert "RESTORE_DRILL_OK" in script


def test_runbook_covers_tunnel_sync_backup_restore_rollback_and_later_domain():
    runbook = RUNBOOK.read_text(encoding="utf-8").lower()

    for required in (
        "ssh tunnel",
        "five-minute sync",
        "freshness",
        "encrypted backup",
        "restore drill",
        "rollback",
        "agent.orbbec.com.cn",
        "fae.orbbec.com.cn",
        "dingtalk",
        "private sanitizer dictionary",
    ):
        assert required in runbook
