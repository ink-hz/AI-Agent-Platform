from pathlib import Path

from test_cloud_backup_runtime import fixture_runtime, container_command


ROOT = Path(__file__).parents[2]


def test_session_expiry_uses_calendar_year_not_fixed_day_count():
    store = (ROOT / "backend" / "app" / "cloud_replica" / "store.py").read_text(encoding="utf-8")

    assert "interval '1 year'" in store
    assert "timedelta(days=365)" not in store


def test_backup_and_restore_scripts_never_create_plaintext_dump_files(tmp_path):
    _, runtime, runner, _ = fixture_runtime(tmp_path)
    runtime.run()
    restore = (ROOT / "deploy" / "cloud" / "restore-drill.sh").read_text(encoding="utf-8")

    assert "app.cloud_replica.cli restore-stream" in restore
    assert "pg_restore" in restore
    assert ".sql" not in restore
    assert ".dump" not in restore
    assert "CLOUD_RESTORE_DRILL_OK" in restore
    for producer, encryptor, _ in runner.pipelines:
        assert "compose" not in producer
        assert not any(argument.endswith((".sql", ".dump")) for argument in producer)
        assert encryptor[-4:] == ("python", "-m", "app.cloud_replica.cli", "backup")
        assert "10001:10001" in encryptor and "--read-only" in encryptor
        assert "no-new-privileges:true" in encryptor and "none" in encryptor
        assert "TMPDIR=/backups" in encryptor
    assert any('exec pg_dump' in part for part in runner.pipelines[0][0])
    assert any('exec pg_dump' in part for part in runner.pipelines[1][0])
    retention = container_command(runner, 'retention')
    assert "orbbec-agent-platform-internal" in retention


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
