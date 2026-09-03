from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_session_expiry_uses_calendar_year_not_fixed_day_count():
    store = (ROOT / "backend" / "app" / "cloud_replica" / "store.py").read_text(encoding="utf-8")

    assert "interval '1 year'" in store
    assert "timedelta(days=365)" not in store


def test_backup_and_restore_scripts_never_create_plaintext_dump_files():
    backup = (ROOT / "deploy" / "cloud" / "backup.sh").read_text(encoding="utf-8")
    restore = (ROOT / "deploy" / "cloud" / "restore-drill.sh").read_text(encoding="utf-8")

    assert "pg_dump" in backup
    assert "app.cloud_replica.cli backup" in backup
    assert "app.cloud_replica.cli restore-stream" in restore
    assert "pg_restore" in restore
    assert ".sql" not in backup
    assert ".dump" not in backup
    assert ".sql" not in restore
    assert ".dump" not in restore
    assert "mktemp -d" not in backup
    assert "CLOUD_BACKUP_OK" in backup
    assert "CLOUD_RESTORE_DRILL_OK" in restore
    assert "chmod 600 /target/recovery-public-key" in backup
    assert 'platform_image="$(/usr/bin/sed -n' in backup
    assert "/usr/bin/docker run --rm -i --user 10001:10001 --read-only" in backup
    assert "--security-opt no-new-privileges:true --network none" in backup
    assert "--network orbbec-agent-platform-internal" in backup
    assert '"${compose[@]}" run' not in backup


def test_daily_backup_timer_is_platform_scoped_and_persistent():
    service = (ROOT / "deploy" / "cloud" / "orbbec-agent-platform-backup.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "cloud" / "orbbec-agent-platform-backup.timer").read_text(encoding="utf-8")
    deploy = (ROOT / "deploy" / "cloud" / "remote-stage.sh").read_text(encoding="utf-8")

    assert "ExecStart=/opt/orbbec-agent-platform/current/deploy/cloud/backup.sh" in service
    assert "OnCalendar=" in timer
    assert "Persistent=true" in timer
    assert "orbbec-agent-platform-backup.timer" in deploy
    assert "enable --now" in deploy
    assert "ai-fae" not in service.lower()
